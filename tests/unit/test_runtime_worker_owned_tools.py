"""Unit: the EnvironmentWorker is the SINGLE tool executor — `ManagedAgentsRuntime.send_message`
executes NOTHING and only OBSERVES the worker's round-trip on the session event stream
(docs/decisions/custom-tool-execution-path.md; the client-side execution loop was removed so a
tool call can never be answered by two executors).

Mocked anthropic client only — live shapes stay VERIFY-flagged. Invariants:

- the adapter sends exactly ONE event batch per turn (the user.message) — it never posts a
  `user.custom_tool_result` (that is the worker's job) and never invokes a registry tool;
- a worker-answered call (an observed `user.custom_tool_result`) closes into the digest's
  `tool_results`: ok | error | queued_for_approval (a gated call's pending_approval payload also
  surfaces as the already-routed `tool_name` entry — conv.session must never re-invoke it);
- a `requires_action` idle with calls still OPEN fails CLOSED: the open calls surface as pending
  `tool` entries and the turn returns (worker down / unknown tool — never default-allowed);
- reconnect-with-consolidation still dedupes by server event id, so a worker-answered call is
  never double-closed and a replayed result is processed exactly once;
- the runtime carries NO `tool_context_factory` seam — nothing can bind an in-process executor.
"""
import json
from types import SimpleNamespace
from unittest import mock

import pytest

from agents.runtime import ManagedAgentsRuntime

_SENTINEL = object()


def _ev(**kw):
    return SimpleNamespace(**kw)


def _idle(stop: str, eid: str | None = None):
    kw = dict(type="session.status_idle", stop_reason=_ev(type=stop))
    if eid is not None:
        kw["id"] = eid
    return _ev(**kw)


def _tool_use(name: str, input: dict, tu_id: str):
    return _ev(type="agent.custom_tool_use", id=tu_id, name=name, input=input)


def _worker_result(tu_id: str, payload, *, eid: str, is_error: bool = False):
    """A `user.custom_tool_result` the worker posted (SessionBoundTool.call json.dumps shape)."""
    content = payload if isinstance(payload, str) else json.dumps(payload)
    kw = dict(
        type="user.custom_tool_result",
        id=eid,
        custom_tool_use_id=tu_id,
        content=[_ev(type="text", text=content)],
    )
    if is_error:
        kw["is_error"] = True
    return _ev(**kw)


def _msg(text: str, eid: str | None = None):
    kw = dict(type="agent.message", content=[_ev(type="text", text=text)])
    if eid is not None:
        kw["id"] = eid
    return _ev(**kw)


class _Stream:
    def __init__(self, events):
        self._q = list(events)

    def __enter__(self):
        return iter(self._q)

    def __exit__(self, *exc):
        return False


def _runtime(stream_events=()):
    r = ManagedAgentsRuntime(api_key="test-key", environment_id="env_t")
    client = mock.MagicMock(name="anthropic_client")
    client.beta.sessions.create.return_value = SimpleNamespace(id="sess_live_1", status="idle")
    client.beta.sessions.events.stream.return_value = _Stream(stream_events)
    sends: list[list[dict]] = []
    client.beta.sessions.events.send.side_effect = (
        lambda session_id, events, extra_headers=None: sends.append(list(events))
    )
    r._client = client  # injected — no anthropic import, no network
    return r, sends


# ---------------------------------------------------------------- single-executor invariant
@pytest.mark.unit
def test_runtime_carries_no_client_side_execution_seam():
    r = ManagedAgentsRuntime(api_key="k")
    # No seam = nothing (conv.session included) can bind an in-process tool executor.
    assert getattr(r, "tool_context_factory", _SENTINEL) is _SENTINEL
    assert getattr(r, "max_tool_rounds", _SENTINEL) is _SENTINEL


@pytest.mark.unit
def test_worker_served_auto_tool_round_trip_observed_not_executed():
    r, sends = _runtime([
        _tool_use("read_crm", {"entity": "deals"}, "sevt_1"),
        _worker_result("sevt_1", {"status": "ok", "result": {"rows": [{"id": "d1"}]}},
                       eid="uevt_1"),
        _msg("You have 1 open deal.", "sevt_2"),
        _idle("end_turn", "sevt_3"),
    ])
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "how many open deals?")

    assert out["answer"] == "You have 1 open deal."
    assert out["pending_approvals"] == []
    assert out["tool_results"] == [
        {"tool": "read_crm", "custom_tool_use_id": "sevt_1", "status": "ok"}
    ]
    # SINGLE EXECUTOR: this process sent only the user.message — never a tool result.
    assert len(sends) == 1
    assert sends[0] == [
        {"type": "user.message", "content": [{"type": "text", "text": "how many open deals?"}]}
    ]


@pytest.mark.unit
def test_worker_routed_gated_tool_surfaces_already_routed_entry():
    # The WORKER ran Tool.invoke (Phase 4 base class): the proposal landed in Greenlight there,
    # draft-only, and its result payload is what SessionBoundTool.call json.dumps'd. The adapter
    # surfaces it as the already-routed entry (`tool_name`, NOT `tool`) so conv.session passes
    # it through untouched and never enqueues the proposal twice.
    payload = {
        "status": "pending_approval",
        "proposal": {"action": "send_email", "to": "x@y.co", "body": "hi"},
        "approval": {"id": 7, "status": "pending"},
    }
    r, sends = _runtime([
        _tool_use("send_email", {"to": "x@y.co", "body": "hi"}, "sevt_1"),
        _worker_result("sevt_1", payload, eid="uevt_1"),
        _msg("Queued the email for your approval.", "sevt_2"),
        _idle("end_turn", "sevt_3"),
    ])
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "email the lead")

    assert out["tool_results"] == [
        {"tool": "send_email", "custom_tool_use_id": "sevt_1", "status": "queued_for_approval"}
    ]
    assert len(out["pending_approvals"]) == 1
    entry = out["pending_approvals"][0]
    assert entry["status"] == "pending_approval"
    assert entry["tool_name"] == "send_email" and "tool" not in entry
    assert entry["custom_tool_use_id"] == "sevt_1"
    assert entry["proposal"]["to"] == "x@y.co"
    assert entry["approval"]["id"] == 7
    assert out["answer"] == "Queued the email for your approval."
    assert len(sends) == 1  # nothing executed or answered by this process


@pytest.mark.unit
def test_worker_error_result_closes_call_as_error():
    r, sends = _runtime([
        _tool_use("read_crm", {"entity": "deals"}, "sevt_1"),
        _worker_result("sevt_1", "read_crm failed: aurora down", eid="uevt_1", is_error=True),
        _msg("The data source is down.", "sevt_2"),
        _idle("end_turn", "sevt_3"),
    ])
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "how many deals?")
    assert out["tool_results"] == [
        {"tool": "read_crm", "custom_tool_use_id": "sevt_1", "status": "error"}
    ]
    assert out["pending_approvals"] == []
    assert out["answer"] == "The data source is down."
    assert len(sends) == 1


@pytest.mark.unit
def test_unparseable_worker_result_still_closes_the_call():
    # A served call is a served call: junk content never crashes the drain mid-turn.
    r, _ = _runtime([
        _tool_use("query_cube", {"measure": "deals.count"}, "sevt_1"),
        _worker_result("sevt_1", "not json at all", eid="uevt_1"),
        _idle("end_turn", "sevt_2"),
    ])
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "count deals")
    assert out["tool_results"] == [
        {"tool": "query_cube", "custom_tool_use_id": "sevt_1", "status": "ok"}
    ]


# ---------------------------------------------------------------- fail closed when unserved
@pytest.mark.unit
def test_requires_action_with_open_calls_fails_closed_surfaces_pending():
    # Worker down (or not claiming): the call reaches the requires_action idle unanswered.
    # NOTHING executes in this process — the open call surfaces verbatim and the turn returns.
    r, sends = _runtime([
        _tool_use("read_crm", {"entity": "deals"}, "sevt_1"),
        _idle("requires_action", "sevt_2"),
    ])
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "how many deals?")
    assert out["pending_approvals"] == [{
        "status": "pending", "tool": "read_crm", "input": {"entity": "deals"},
        "custom_tool_use_id": "sevt_1",
    }]
    assert out["tool_results"] == []
    assert len(sends) == 1  # only the user.message — nothing answered, nothing executed


@pytest.mark.unit
def test_unknown_tool_is_never_default_allowed():
    r, sends = _runtime([
        _tool_use("evil_tool", {"x": 1}, "sevt_1"),
        _idle("requires_action", "sevt_2"),
    ])
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "do the thing")
    assert out["pending_approvals"] == [{
        "status": "pending", "tool": "evil_tool", "input": {"x": 1},
        "custom_tool_use_id": "sevt_1",
    }]
    assert out["tool_results"] == []
    assert len(sends) == 1


@pytest.mark.unit
def test_mixed_served_and_unserved_calls_split_correctly():
    # The worker answered one call; the other reached requires_action open. Order-independent:
    # the served call closes into tool_results, the unserved one surfaces as pending.
    r, _ = _runtime([
        _tool_use("read_crm", {"entity": "deals"}, "sevt_a"),
        _tool_use("evil_tool", {"x": 1}, "sevt_b"),
        _worker_result("sevt_a", {"status": "ok", "result": {"rows": []}}, eid="uevt_1"),
        _idle("requires_action", "sevt_c"),
    ])
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "deals, then the thing")
    assert out["tool_results"] == [
        {"tool": "read_crm", "custom_tool_use_id": "sevt_a", "status": "ok"}
    ]
    assert out["pending_approvals"] == [{
        "status": "pending", "tool": "evil_tool", "input": {"x": 1},
        "custom_tool_use_id": "sevt_b",
    }]


@pytest.mark.unit
def test_open_calls_at_stream_end_surface_never_silently_dropped():
    r, _ = _runtime([_tool_use("read_crm", {"entity": "deals"}, "sevt_1")])  # stream just ends
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "deals?")
    assert out["pending_approvals"] == [{
        "status": "pending", "tool": "read_crm", "input": {"entity": "deals"},
        "custom_tool_use_id": "sevt_1",
    }]


@pytest.mark.unit
def test_requires_action_without_calls_keeps_placeholder():
    r, _ = _runtime([_idle("requires_action", "sevt_1")])
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "hello")
    assert out["pending_approvals"] == [{"status": "pending", "reason": "requires_action"}]


# ---------------------------------------------------------------- dict-shaped replay events
@pytest.mark.unit
def test_worker_result_text_and_error_flags_tolerate_dict_shape():
    # events.list replay may hand dict-shaped events/blocks; the digest helpers accept both.
    entry = {"tool": "send_email", "input": {"to": "x"}, "custom_tool_use_id": "sevt_1"}
    r = ManagedAgentsRuntime(api_key="k")
    status, routed = r._digest_tool_result(entry, {
        "type": "user.custom_tool_result",
        "custom_tool_use_id": "sevt_1",
        "content": [{"type": "text",
                     "text": json.dumps({"status": "pending_approval", "proposal": {"to": "x"}})}],
    })
    assert status == "queued_for_approval"
    assert routed["tool_name"] == "send_email"
    status, routed = r._digest_tool_result(entry, {
        "type": "user.custom_tool_result", "custom_tool_use_id": "sevt_1",
        "is_error": True, "content": [{"type": "text", "text": "boom"}],
    })
    assert status == "error" and routed is None


# ---------------------------------------------------------------- reconnect-with-consolidation
class _DroppingStream:
    """Yields its scripted events, then the SSE transport dies (connection-shaped failure)."""

    def __init__(self, events):
        self._q = list(events)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return self

    def __next__(self):
        if not self._q:
            raise ConnectionError("SSE transport reset")
        return self._q.pop(0)


def _reconnect_runtime(streams, replay):
    r = ManagedAgentsRuntime(api_key="test-key", environment_id="env_t")
    client = mock.MagicMock(name="anthropic_client")
    client.beta.sessions.create.return_value = SimpleNamespace(id="sess_live_1", status="idle")
    client.beta.sessions.events.stream.side_effect = list(streams)
    client.beta.sessions.events.list.return_value = list(replay)
    sends: list[list[dict]] = []
    client.beta.sessions.events.send.side_effect = (
        lambda session_id, events, extra_headers=None: sends.append(list(events))
    )
    r._client = client
    return r, sends, client


@pytest.mark.unit
def test_stream_drop_replays_worker_result_and_never_double_closes():
    # Drop after the call surfaced but before the worker's answer arrived on OUR stream. The
    # replay re-delivers the call (deduped by id — never re-collected) plus the worker's result
    # and the final answer emitted while we were dark; the call closes EXACTLY once.
    tool_call = _tool_use("read_crm", {"entity": "deals"}, "sevt_1")
    r, sends, client = _reconnect_runtime(
        streams=[_DroppingStream([tool_call]), _Stream([])],
        replay=[
            tool_call,  # already collected — deduped by event id
            _worker_result("sevt_1", {"status": "ok", "result": {"rows": []}}, eid="uevt_1"),
            _msg("No open deals.", "sevt_2"),
            _idle("end_turn", "sevt_3"),
        ],
    )
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "deals?")

    assert out["answer"] == "No open deals."
    assert out["pending_approvals"] == []
    assert out["tool_results"] == [
        {"tool": "read_crm", "custom_tool_use_id": "sevt_1", "status": "ok"}
    ]
    assert len(sends) == 1  # the user.message only — never resubmitted, nothing answered here
    assert client.beta.sessions.events.stream.call_count == 2  # bounded: one reconnect
    client.beta.sessions.events.list.assert_called_once()


@pytest.mark.unit
def test_replayed_duplicate_worker_result_is_processed_once():
    # The result arrived on the live stream AND in the replay (overlap) — the event-id ledger
    # dedupes, so the digest closes the call exactly once.
    result = _worker_result("sevt_1", {"status": "ok", "result": {"rows": []}}, eid="uevt_1")
    r, _sends, _client = _reconnect_runtime(
        streams=[
            _DroppingStream([_tool_use("read_crm", {"entity": "deals"}, "sevt_1"), result]),
            _Stream([]),
        ],
        replay=[
            _tool_use("read_crm", {"entity": "deals"}, "sevt_1"),
            result,  # duplicate — deduped
            _idle("end_turn", "sevt_2"),
        ],
    )
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "deals?")
    assert out["tool_results"] == [
        {"tool": "read_crm", "custom_tool_use_id": "sevt_1", "status": "ok"}
    ]


@pytest.mark.unit
def test_second_drop_fails_loud_bounded_retry():
    r, _sends, client = _reconnect_runtime(
        streams=[_DroppingStream([]), _DroppingStream([])], replay=[],
    )
    session = r.create_session("coord_1", tenant_id="tenant-a")
    with pytest.raises(RuntimeError, match="dropped again .* giving up"):
        r.send_message(session, "hello")
    assert client.beta.sessions.events.stream.call_count == 2  # exactly one reconnect attempt


@pytest.mark.unit
def test_non_connection_errors_never_reconnect():
    class _BrokenStream(_Stream):
        def __enter__(self):
            return self

        def __iter__(self):
            return self

        def __next__(self):
            raise ValueError("not a transport failure")

    r, _sends, client = _reconnect_runtime(streams=[_BrokenStream([]), _Stream([])], replay=[])
    session = r.create_session("coord_1", tenant_id="tenant-a")
    with pytest.raises(ValueError, match="not a transport failure"):
        r.send_message(session, "hello")
    assert client.beta.sessions.events.stream.call_count == 1
    client.beta.sessions.events.list.assert_not_called()
