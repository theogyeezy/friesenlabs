"""Unit: CLIENT-SIDE custom-tool execution in `ManagedAgentsRuntime.send_message`
(docs/decisions/custom-tool-execution-path.md, ratified #123 — v1 = client-side, the
orchestrator drives tool execution).

The documented round-trip under test (mocked anthropic client only — live shapes stay
VERIFY-flagged): `agent.custom_tool_use` -> session idles `requires_action` -> the runtime
executes the read-only (Policy.AUTO) tool through the TRUSTED registry with the session's
tenant-bound ToolContext -> `user.custom_tool_result` is sent back on the SAME open stream ->
the drain continues to a data-grounded answer. Invariants:

- ALWAYS_ASK tools keep the EXISTING behavior exactly: surfaced as pending, never executed;
- unknown tools are never default-allowed (surfaced, never executed);
- the execute-and-resume loop is bounded (`max_tool_rounds`) and fails CLOSED;
- a tool error becomes an `is_error` result fed back — never a crash;
- with no factory / no tool clients, behavior is byte-identical to the surface-only adapter.
"""
import json
from types import SimpleNamespace
from unittest import mock

import pytest

from agents.runtime import DEFAULT_MAX_TOOL_ROUNDS, ManagedAgentsRuntime
from agents.tools.base import ToolContext


def _ev(**kw):
    return SimpleNamespace(**kw)


def _idle(stop: str):
    return _ev(type="session.status_idle", stop_reason=_ev(type=stop))


def _tool_use(name: str, input: dict, tu_id: str, thread: str | None = None):
    kw = dict(type="agent.custom_tool_use", id=tu_id, name=name, input=input)
    if thread is not None:
        kw["session_thread_id"] = thread
    return _ev(**kw)


class _LiveStream:
    """One open SSE stream: yields scripted events; the fake `events.send` can PUSH the
    session's next events onto the same open stream — exactly the client-patterns Pattern 9
    shape (results submitted while the stream stays open, drain continues)."""

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
            raise StopIteration  # stream closed by the server
        return self._q.pop(0)


class _Db:
    """Fake tool-side CRM client: records tenant binding + reads; can be told to fail."""

    def __init__(self, rows=None, fail=False):
        self.rows = rows or []
        self.fail = fail
        self.tenants: list[str] = []
        self.reads: list[tuple] = []

    def set_tenant(self, tenant_id):
        self.tenants.append(tenant_id)

    def read(self, entity, limit=50):
        self.reads.append((entity, limit))
        if self.fail:
            raise RuntimeError("aurora down")
        return self.rows


def _factory(db=None, rag=None):
    """A tenant-bound ToolContext factory — tenant from SESSION metadata only (trust rule)."""

    def build(session):
        return ToolContext(
            tenant_id=session.metadata["tenant_id"], agent="uplift-orchestrator", db=db, rag=rag
        )

    return build


def _runtime(initial_events, factory=None, on_tool_results=None,
             max_tool_rounds=DEFAULT_MAX_TOOL_ROUNDS):
    """ManagedAgentsRuntime over a mocked client whose stream is live: `on_tool_results`
    scripts what the session emits after a user.custom_tool_result batch arrives."""
    r = ManagedAgentsRuntime(
        api_key="test-key", environment_id="env_t",
        tool_context_factory=factory, max_tool_rounds=max_tool_rounds,
    )
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
    r._client = client  # injected — no anthropic import, no network
    return r, sends, stream


# ---------------------------------------------------------------- the happy round-trip
@pytest.mark.unit
def test_auto_tool_executes_result_feeds_back_and_answer_reflects_it():
    db = _Db(rows=[{"id": "d1"}, {"id": "d2"}])

    def on_results(events, stream):
        # The session consumed the tool result and settled with a data-grounded answer.
        stream.push([
            _ev(type="agent.message", content=[_ev(type="text", text="You have 2 open deals.")]),
            _idle("end_turn"),
        ])

    r, sends, _ = _runtime(
        [_tool_use("read_crm", {"entity": "deals"}, "sevt_1"), _idle("requires_action")],
        factory=_factory(db=db), on_tool_results=on_results,
    )
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "how many open deals?")

    # Final answer is the post-execution, data-grounded one; nothing left pending.
    assert out["answer"] == "You have 2 open deals."
    assert out["pending_approvals"] == []
    assert out["tool_results"] == [
        {"tool": "read_crm", "custom_tool_use_id": "sevt_1", "status": "ok"}
    ]
    # Tenant bound from SESSION metadata before execution (RLS during tool exec).
    assert db.tenants == ["tenant-a"]
    assert db.reads == [("deals", 50)]
    # The documented round-trip: 2nd send = user.custom_tool_result with the matching id.
    assert len(sends) == 2
    result_event = sends[1][0]
    assert result_event["type"] == "user.custom_tool_result"
    assert result_event["custom_tool_use_id"] == "sevt_1"
    payload = json.loads(result_event["content"][0]["text"])
    assert payload["rows"] == [{"id": "d1"}, {"id": "d2"}]
    assert "is_error" not in result_event


@pytest.mark.unit
def test_subagent_thread_id_is_echoed_on_the_result():
    # Multiagent contract: a custom tool call cross-posted from a subagent thread carries
    # session_thread_id — the result must echo it.
    def on_results(events, stream):
        stream.push([_idle("end_turn")])

    r, sends, _ = _runtime(
        [_tool_use("read_crm", {"entity": "deals"}, "sevt_1", thread="th_9"),
         _idle("requires_action")],
        factory=_factory(db=_Db()), on_tool_results=on_results,
    )
    session = r.create_session("coord_1", tenant_id="tenant-a")
    r.send_message(session, "go")
    assert sends[1][0]["session_thread_id"] == "th_9"


# ---------------------------------------------------------------- gated tools never execute
@pytest.mark.unit
def test_always_ask_tool_still_surfaces_never_executes():
    db = _Db()
    r, sends, _ = _runtime(
        [_tool_use("send_email", {"to": "x@y.co", "body": "hi"}, "sevt_1"),
         _idle("requires_action")],
        factory=_factory(db=db),
    )
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "email the lead")

    # EXISTING behavior exactly: surfaced for the conv layer's Greenlight routing.
    assert out["pending_approvals"] == [{
        "status": "pending", "tool": "send_email",
        "input": {"to": "x@y.co", "body": "hi"}, "custom_tool_use_id": "sevt_1",
    }]
    assert out["tool_results"] == []
    assert len(sends) == 1  # only the user.message — no custom_tool_result was sent
    assert db.reads == [] and db.tenants == []


@pytest.mark.unit
def test_unknown_tool_is_never_default_allowed():
    db = _Db()
    r, sends, _ = _runtime(
        [_tool_use("evil_tool", {"x": 1}, "sevt_1"), _idle("requires_action")],
        factory=_factory(db=db),
    )
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "do the thing")
    assert out["pending_approvals"] == [{
        "status": "pending", "tool": "evil_tool", "input": {"x": 1},
        "custom_tool_use_id": "sevt_1",
    }]
    assert out["tool_results"] == []
    assert len(sends) == 1 and db.reads == []


@pytest.mark.unit
def test_mixed_round_with_gated_tool_surfaces_everything_executes_nothing():
    # A round holding an AUTO call AND a gated call: partial result submission to a session
    # still blocked on the gated call is deliberately avoided — ALL calls surface, in
    # arrival order, and nothing executes (the gated path stays exactly as before).
    db = _Db(rows=[{"id": "d1"}])
    r, sends, _ = _runtime(
        [
            _tool_use("read_crm", {"entity": "deals"}, "sevt_a"),
            _tool_use("send_email", {"to": "x@y.co", "body": "hi"}, "sevt_b"),
            _idle("requires_action"),
        ],
        factory=_factory(db=db),
    )
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "summarize the deals then email the lead")
    assert [e["tool"] for e in out["pending_approvals"]] == ["read_crm", "send_email"]
    assert out["tool_results"] == []
    assert db.reads == []  # the AUTO call was NOT partially executed
    assert len(sends) == 1


# ---------------------------------------------------------------- bound + error paths
@pytest.mark.unit
def test_round_bound_enforced_fails_closed():
    n = {"round": 0}

    def on_results(events, stream):
        # A runaway coordinator: every result triggers ANOTHER tool call, forever.
        n["round"] += 1
        stream.push([
            _tool_use("read_crm", {"entity": "deals"}, f"sevt_{n['round'] + 1}"),
            _idle("requires_action"),
        ])

    db = _Db(rows=[])
    r, sends, _ = _runtime(
        [_tool_use("read_crm", {"entity": "deals"}, "sevt_1"), _idle("requires_action")],
        factory=_factory(db=db), on_tool_results=on_results, max_tool_rounds=2,
    )
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "loop forever")  # returns — never drains unbounded

    assert len(out["tool_results"]) == 2          # exactly the bound, then stop
    assert db.reads == [("deals", 50)] * 2
    assert len(sends) == 3                         # user.message + two result rounds
    assert out["pending_approvals"] == [{
        "status": "pending", "tool": "read_crm", "input": {"entity": "deals"},
        "custom_tool_use_id": "sevt_3", "reason": "max_tool_rounds_exhausted",
    }]


@pytest.mark.unit
def test_tool_error_feeds_is_error_result_and_never_crashes():
    db = _Db(fail=True)

    def on_results(events, stream):
        stream.push([
            _ev(type="agent.message", content=[_ev(type="text", text="The data source is down.")]),
            _idle("end_turn"),
        ])

    r, sends, _ = _runtime(
        [_tool_use("read_crm", {"entity": "deals"}, "sevt_1"), _idle("requires_action")],
        factory=_factory(db=db), on_tool_results=on_results,
    )
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "how many deals?")  # no exception — fail closed

    result_event = sends[1][0]
    assert result_event["is_error"] is True
    assert "read_crm failed" in result_event["content"][0]["text"]
    assert out["tool_results"] == [
        {"tool": "read_crm", "custom_tool_use_id": "sevt_1", "status": "error"}
    ]
    assert out["answer"] == "The data source is down."


@pytest.mark.unit
def test_max_tool_rounds_must_be_positive():
    with pytest.raises(ValueError, match="max_tool_rounds"):
        ManagedAgentsRuntime(api_key="k", max_tool_rounds=0)


# ---------------------------------------------------------------- honest fallback invariance
_SURFACED = [{
    "status": "pending", "tool": "read_crm", "input": {"entity": "deals"},
    "custom_tool_use_id": "sevt_1",
}]


@pytest.mark.unit
def test_no_factory_is_byte_identical_to_surface_only():
    r, sends, _ = _runtime(
        [_tool_use("read_crm", {"entity": "deals"}, "sevt_1"), _idle("requires_action")],
        factory=None,
    )
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "how many deals?")
    assert out["pending_approvals"] == _SURFACED
    assert out["tool_results"] == []
    assert len(sends) == 1  # nothing fed back; the event surfaced exactly as before


@pytest.mark.unit
def test_factory_without_tool_clients_is_byte_identical():
    # Honest fallback: a context with NO clients must not "execute" tools into empty results
    # the coordinator would present as data-grounded — surface instead, exactly as before.
    def empty_ctx(session):
        return ToolContext(tenant_id=session.metadata["tenant_id"])

    r, sends, _ = _runtime(
        [_tool_use("read_crm", {"entity": "deals"}, "sevt_1"), _idle("requires_action")],
        factory=empty_ctx,
    )
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "how many deals?")
    assert out["pending_approvals"] == _SURFACED
    assert out["tool_results"] == []
    assert len(sends) == 1
