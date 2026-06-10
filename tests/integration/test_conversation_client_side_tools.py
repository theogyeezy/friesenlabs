"""Integration: the Conversation binds its tenant-scoped ToolContext builder onto the
ManagedAgentsRuntime `tool_context_factory` seam, and a full /chat-shaped turn executes the
coordinator's read-only tool CLIENT-SIDE and returns the data-grounded answer
(docs/decisions/custom-tool-execution-path.md, ratified #123).

Mocked anthropic client only — live shapes stay VERIFY-flagged. The mocked stream is LIVE:
the fake events.send reacts to the user.custom_tool_result batch by pushing the session's
next events onto the same open stream (client-patterns Pattern 9).
"""
from datetime import date
from types import SimpleNamespace
from unittest import mock

import pytest

from agents.runtime import ManagedAgentsRuntime
from agents.tools.base import ToolContext
from api.control.greenlight import Greenlight
from conv.analytics import Analytics, EventType
from conv.session import Conversation

TODAY = date(2026, 6, 10)


def _ev(**kw):
    return SimpleNamespace(**kw)


class _LiveStream:
    def __init__(self, initial):
        self._q = list(initial)

    def push(self, events):
        self._q.extend(events)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return self

    def __next__(self):
        if not self._q:
            raise StopIteration
        return self._q.pop(0)


class _Db:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.tenants: list[str] = []
        self.reads: list[tuple] = []

    def set_tenant(self, tenant_id):
        self.tenants.append(tenant_id)

    def read(self, entity, limit=50):
        self.reads.append((entity, limit))
        return self.rows


def _mocked_runtime(initial_events=(), on_tool_results=None, **runtime_kwargs):
    r = ManagedAgentsRuntime(api_key="test-key", environment_id="env-A", **runtime_kwargs)
    client = mock.MagicMock(name="anthropic_client")
    client.beta.sessions.create.return_value = SimpleNamespace(id="sess_live_1", status="idle")
    stream = _LiveStream(initial_events)
    client.beta.sessions.events.stream.return_value = stream
    sends: list[list[dict]] = []

    def _send(session_id, events, extra_headers=None):
        sends.append(list(events))
        if on_tool_results and any(e.get("type") == "user.custom_tool_result" for e in events):
            on_tool_results(events, stream)

    client.beta.sessions.events.send.side_effect = _send
    r._client = client
    return r, sends


@pytest.mark.integration
def test_conversation_turn_executes_auto_tool_client_side_end_to_end():
    db = _Db(rows=[{"id": "d1"}, {"id": "d2"}])

    def on_results(events, stream):
        stream.push([
            _ev(type="agent.message", content=[_ev(type="text", text="You have 2 open deals.")]),
            _ev(type="session.status_idle", stop_reason=_ev(type="end_turn")),
        ])

    runtime, sends = _mocked_runtime(
        initial_events=[
            _ev(type="agent.custom_tool_use", id="sevt_1", name="read_crm",
                input={"entity": "deals"}),
            _ev(type="session.status_idle", stop_reason=_ev(type="requires_action")),
        ],
        on_tool_results=on_results,
    )
    gl = Greenlight()
    analytics = Analytics()
    convo = Conversation(
        tenant_id="tenant-A", today=TODAY, runtime=runtime,
        coordinator_id="coord-A", environment_id="env-A",
        crm=db, greenlight=gl, analytics=analytics,
    )

    # The Conversation bound its tenant-scoped context builder onto the runtime seam.
    assert runtime.tool_context_factory is not None
    ctx = runtime.tool_context_factory(convo.session)
    assert isinstance(ctx, ToolContext)
    assert ctx.tenant_id == convo.session.metadata["tenant_id"] == "tenant-A"
    assert ctx.db is db

    turn = convo.send("how many open deals do we have?")

    # Data-grounded answer; the tool ran ONCE, tenant-bound, and the result was fed back.
    assert turn.answer == "You have 2 open deals."
    assert turn.pending_approvals == []
    assert db.reads == [("deals", 50)]
    assert db.tenants == ["tenant-A"]
    result_event = sends[1][0]
    assert result_event["type"] == "user.custom_tool_result"
    assert result_event["custom_tool_use_id"] == "sevt_1"
    # Nothing landed in Greenlight (read-only path) and the execution hit the trace.
    assert gl.list_pending("tenant-A") == []
    tool_calls = analytics.list("tenant-A", type=EventType.TOOL_CALL)
    assert [(e["payload"]["tool"], e["payload"]["status"]) for e in tool_calls] == [
        ("read_crm", "ok")
    ]


@pytest.mark.integration
def test_conversation_never_overwrites_an_injected_factory():
    def injected(session):
        return ToolContext(tenant_id=session.metadata["tenant_id"])

    runtime, _ = _mocked_runtime(tool_context_factory=injected)
    Conversation(
        tenant_id="tenant-A", today=TODAY, runtime=runtime,
        coordinator_id="coord-A", environment_id="env-A",
    )
    assert runtime.tool_context_factory is injected  # construction-time injection wins
