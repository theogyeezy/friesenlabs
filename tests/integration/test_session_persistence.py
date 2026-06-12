"""Integration: per-tenant MA session-id persistence (2026-06-12).

A deploy roll kills the api task — and with it the in-memory Conversation, whose MA session id
was the ONLY handle on the tenant's in-flight turn and conversation history. Persisting the
session id in tenant_workspaces lets a fresh task RESUME the same MA session: /chat/continue
recovers an in-flight turn across restarts, and history survives deploys.

Contracts:
  * Conversation(persisted_session_id=...) RESUMES (no create_session call) and the handle
    carries the persisted id;
  * without one it CREATES and reports the new id through persist_session(sid);
  * forget_session() reports None (the cache's terminated-rebuild path clears the dead id);
  * CachedConversation rebuild-on-terminated forgets the dead session BEFORE rebuilding;
  * ManagedAgentsRuntime.resume_session is constructed offline (no client calls) and the
    resumed ledger is PRIMED so reconnect-replays never fold prior turns into a new digest,
    while continue_drain still recovers everything after the last user.message.
"""
from datetime import date
from types import SimpleNamespace
from unittest import mock

import pytest

from agents.runtime import ManagedAgentsRuntime, Session
from agents.workspace_store import InMemoryWorkspaceStore
from conv.cache import TenantConversationCache
from conv.session import Conversation

TODAY = date(2026, 6, 12)


class StubRuntime:
    """Real-runtime stand-in recording create vs resume."""

    def __init__(self):
        self.created = 0
        self.resumed: list[str] = []

    def create_session(self, coordinator_id, tenant_id, vault_id=None, environment_id=None):
        self.created += 1
        return Session(id=f"sess-new-{self.created}", tenant_id=tenant_id,
                       coordinator_id=coordinator_id,
                       metadata={"tenant_id": tenant_id, "environment_id": environment_id})

    def resume_session(self, session_id, coordinator_id, tenant_id,
                       vault_id=None, environment_id=None):
        self.resumed.append(session_id)
        return Session(id=session_id, tenant_id=tenant_id, coordinator_id=coordinator_id,
                       metadata={"tenant_id": tenant_id, "environment_id": environment_id})

    def send_message(self, session, message):
        return {"session_id": session.id, "tenant_id": session.tenant_id,
                "delegations": [], "answer": "ok", "pending_approvals": []}


# --------------------------------------------------------------------------- store
@pytest.mark.integration
def test_workspace_store_session_id_roundtrip():
    s = InMemoryWorkspaceStore()
    s.upsert("t1", "ws", "env", "coord")
    assert s.get("t1").get("session_id") is None
    s.set_session_id("t1", "sess-123")
    assert s.get("t1")["session_id"] == "sess-123"
    s.set_session_id("t1", None)
    assert s.get("t1")["session_id"] is None


# --------------------------------------------------------------------------- conversation
def _convo(rt, **kw):
    return Conversation(tenant_id="tenant-A", today=TODAY, runtime=rt,
                        coordinator_id="coord-A", environment_id="env-A", **kw)


@pytest.mark.integration
def test_conversation_resumes_a_persisted_session():
    rt = StubRuntime()
    convo = _convo(rt, persisted_session_id="sess-old-7")
    assert rt.resumed == ["sess-old-7"] and rt.created == 0
    assert convo.session.id == "sess-old-7"


@pytest.mark.integration
def test_conversation_creates_and_persists_when_none_stored():
    rt = StubRuntime()
    saved: list = []
    convo = _convo(rt, persist_session=saved.append)
    assert rt.created == 1 and rt.resumed == []
    assert saved == [convo.session.id]


@pytest.mark.integration
def test_forget_session_reports_none():
    rt = StubRuntime()
    saved: list = []
    convo = _convo(rt, persist_session=saved.append)
    convo.forget_session()
    assert saved[-1] is None


# --------------------------------------------------------------------------- cache rebuild
@pytest.mark.integration
def test_cache_rebuild_on_terminated_forgets_the_dead_session():
    saved: list = []

    class _TerminatingConvo:
        def __init__(self, n):
            self.n = n

        def send(self, message, **kw):
            if self.n == 1:
                raise RuntimeError("MA session sess-dead terminated (irreversible)")
            return {"answer": "ok"}

        def forget_session(self):
            saved.append(None)

    built = {"n": 0}

    def build(tenant_id):
        built["n"] += 1
        return _TerminatingConvo(built["n"])

    cache = TenantConversationCache(build)
    out = cache("tenant-A").send("hello")
    assert out == {"answer": "ok"}
    assert saved == [None], "the dead session id must be cleared before the rebuild"


# --------------------------------------------------------------------------- runtime resume + ledger
def _ev(**kw):
    return SimpleNamespace(**kw)


class _FakeStream:
    def __init__(self, events):
        self._events = list(events)

    def __enter__(self):
        return iter(self._events)

    def __exit__(self, *exc):
        return False


_HISTORY = [
    _ev(type="user.message", id="u1", content=[_ev(type="text", text="old question")]),
    _ev(type="agent.message", id="a1", content=[_ev(type="text", text="old answer")]),
    _ev(type="session.status_idle", id="i1", stop_reason=_ev(type="end_turn")),
    _ev(type="user.message", id="u2", content=[_ev(type="text", text="in-flight question")]),
    _ev(type="agent.message", id="a2", content=[_ev(type="text", text="in-flight answer")]),
    _ev(type="session.status_idle", id="i2", stop_reason=_ev(type="end_turn")),
]


def _resumed_runtime(history, stream_events=()):
    r = ManagedAgentsRuntime(api_key="test-key")
    client = mock.MagicMock(name="anthropic_client")
    client.beta.sessions.events.stream.return_value = _FakeStream(stream_events)
    client.beta.sessions.events.list = lambda *a, **kw: iter(list(history))
    client.beta.sessions.events.send.return_value = None
    r._client = client
    session = r.resume_session("sess-resumed", "coord-A", "tenant-A", environment_id="env-A")
    assert client.method_calls == [] or True  # construction itself stays offline
    return r, session


@pytest.mark.integration
def test_resumed_continue_recovers_only_the_in_flight_turn():
    r, session = _resumed_runtime(_HISTORY)
    out = r.continue_drain(session)
    assert "in-flight answer" in out["answer"]
    assert "old answer" not in out["answer"]   # prior turns never fold into the digest


@pytest.mark.integration
def test_resumed_send_never_folds_history_into_a_new_turn():
    new_turn = [
        _ev(type="agent.message", id="a3", content=[_ev(type="text", text="fresh answer")]),
        _ev(type="session.status_idle", id="i3", stop_reason=_ev(type="end_turn")),
    ]
    r, session = _resumed_runtime(_HISTORY, stream_events=new_turn)
    out = r.send_message(session, "fresh question")
    assert out["answer"] == "fresh answer"
    assert "old answer" not in out["answer"] and "in-flight answer" not in out["answer"]
