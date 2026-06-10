"""Unit: CLIENT-SIDE custom-tool execution in `ManagedAgentsRuntime.send_message`
(docs/decisions/custom-tool-execution-path.md, ratified #123 — v1 = client-side, the
orchestrator drives tool execution).

The documented round-trip under test (mocked anthropic client only — live shapes stay
VERIFY-flagged): `agent.custom_tool_use` -> session idles `requires_action` -> the runtime
executes the read-only (Policy.AUTO) tool through the TRUSTED registry with the session's
tenant-bound ToolContext -> `user.custom_tool_result` is sent back on the SAME open stream ->
the drain continues to a data-grounded answer. Invariants:

- ALWAYS_ASK tools are NEVER executed; with a greenlight client in the bound context they are
  ROUTED — the proposal lands in Greenlight and the session gets an IMMEDIATE
  `queued_for_approval` reply (ratified brief: the session never dangles on a gated call);
  without one they surface as pending exactly as before (nothing to truthfully queue into);
- unknown tools are never default-allowed (surfaced, never executed);
- the resolve-and-resume loop is bounded (`max_tool_rounds`) and fails CLOSED;
- a tool error becomes an `is_error` result fed back — never a crash;
- with no factory / no tool clients, behavior is byte-identical to the surface-only adapter;
- an SSE drop mid-turn reconnects ONCE (replay via events.list, deduped by event id — an
  already-answered tool call is never collected or answered twice); a second drop fails loud.
"""
import json
from types import SimpleNamespace
from unittest import mock

import pytest

from agents.runtime import DEFAULT_MAX_TOOL_ROUNDS, ManagedAgentsRuntime
from agents.tools.base import InMemoryGreenlight, ToolContext


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


def _factory(db=None, rag=None, greenlight=None):
    """A tenant-bound ToolContext factory — tenant from SESSION metadata only (trust rule)."""

    def build(session):
        return ToolContext(
            tenant_id=session.metadata["tenant_id"], agent="uplift-orchestrator", db=db, rag=rag,
            greenlight=greenlight,
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
def test_always_ask_without_greenlight_surfaces_never_executes():
    # HONEST FALLBACK: no greenlight client in the bound context -> nothing to truthfully queue
    # into -> the gated call surfaces as a `tool` entry for the conv layer's own routing,
    # exactly the pre-brief behavior (the session stays blocked; no reply is invented).
    db = _Db()
    r, sends, _ = _runtime(
        [_tool_use("send_email", {"to": "x@y.co", "body": "hi"}, "sevt_1"),
         _idle("requires_action")],
        factory=_factory(db=db),
    )
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "email the lead")

    assert out["pending_approvals"] == [{
        "status": "pending", "tool": "send_email",
        "input": {"to": "x@y.co", "body": "hi"}, "custom_tool_use_id": "sevt_1",
    }]
    assert out["tool_results"] == []
    assert len(sends) == 1  # only the user.message — no custom_tool_result was sent
    assert db.reads == [] and db.tenants == []


@pytest.mark.unit
def test_always_ask_with_greenlight_gets_immediate_queued_reply_and_proposal_lands():
    # THE RATIFIED-BRIEF REPLY (custom-tool-execution-path, critic-corrected section): an
    # ALWAYS_ASK call is routed to Greenlight and the session receives an IMMEDIATE
    # user.custom_tool_result — {"status": "queued_for_approval", approval_id, performed: false}
    # — through the SAME result-submission path AUTO tools use, so the coordinator is never
    # left dangling and can acknowledge the queue in its answer. The side effect never runs.
    gl = InMemoryGreenlight()
    db = _Db()

    def on_results(events, stream):
        # The coordinator consumed the queued reply and acknowledged instead of retrying.
        stream.push([
            _ev(type="agent.message",
                content=[_ev(type="text", text="Queued the email for your approval.")]),
            _idle("end_turn"),
        ])

    r, sends, _ = _runtime(
        [_tool_use("send_email", {"to": "x@y.co", "body": "hi"}, "sevt_1"),
         _idle("requires_action")],
        factory=_factory(db=db, greenlight=gl), on_tool_results=on_results,
    )
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "email the lead")

    # Exactly the prescribed reply, fed back like an AUTO result (2nd send, matching id).
    assert len(sends) == 2
    reply = sends[1][0]
    assert reply["type"] == "user.custom_tool_result"
    assert reply["custom_tool_use_id"] == "sevt_1"
    assert "is_error" not in reply
    payload = json.loads(reply["content"][0]["text"])
    assert payload["status"] == "queued_for_approval"
    assert payload["performed"] is False
    assert payload["approval_id"] == 1  # the Greenlight record it points at

    # The Greenlight proposal landed exactly once — draft-only, nothing performed.
    assert len(gl.queue) == 1
    assert gl.queue[0]["action"] == "send_email"
    assert gl.queue[0]["tenant_id"] == "tenant-a"  # trust rule: tenant from session metadata
    assert gl.queue[0]["status"] == "pending"
    assert db.reads == []  # nothing executed

    # Digest: already-routed entry (`tool_name`, NOT `tool` — conv.session must not re-invoke),
    # the queue hit the trace, and the coordinator settled with the acknowledgment.
    assert out["tool_results"] == [
        {"tool": "send_email", "custom_tool_use_id": "sevt_1", "status": "queued_for_approval"}
    ]
    assert len(out["pending_approvals"]) == 1
    entry = out["pending_approvals"][0]
    assert entry["status"] == "pending_approval"
    assert entry["tool_name"] == "send_email" and "tool" not in entry
    assert entry["custom_tool_use_id"] == "sevt_1"
    assert entry["approval"]["id"] == 1
    assert entry["proposal"]["to"] == "x@y.co"
    assert out["answer"] == "Queued the email for your approval."


@pytest.mark.unit
def test_gated_routing_error_feeds_is_error_reply_and_queues_nothing():
    # Fail CLOSED like the AUTO path: a routing error (bad model input) becomes an is_error
    # reply fed back — never a crash, and nothing lands in the queue.
    gl = InMemoryGreenlight()

    def on_results(events, stream):
        stream.push([_idle("end_turn")])

    r, sends, _ = _runtime(
        [_tool_use("send_email", {"to": "x@y.co"}, "sevt_1"),  # missing required `body`
         _idle("requires_action")],
        factory=_factory(greenlight=gl), on_tool_results=on_results,
    )
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "email the lead")

    reply = sends[1][0]
    assert reply["is_error"] is True
    assert "send_email failed" in reply["content"][0]["text"]
    assert gl.queue == []  # nothing queued on a failed routing
    assert out["pending_approvals"] == []  # no routed entry — there is no approval to track
    assert out["tool_results"] == [
        {"tool": "send_email", "custom_tool_use_id": "sevt_1", "status": "error"}
    ]


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
def test_mixed_round_without_greenlight_surfaces_everything_executes_nothing():
    # A round holding an AUTO call AND a gated call, with NO greenlight in the context: the
    # gated call is unresolvable, and partial result submission to a session still blocked on
    # it is deliberately avoided — ALL calls surface, in arrival order, and nothing executes.
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


@pytest.mark.unit
def test_mixed_round_with_greenlight_resolves_fully_in_one_batch():
    # With greenlight bound, a mixed AUTO+gated round is FULLY resolvable: the AUTO call
    # executes, the gated call gets the immediate queued_for_approval reply, both results go
    # back in ONE events.send batch (no partial submission), and the drain continues.
    gl = InMemoryGreenlight()
    db = _Db(rows=[{"id": "d1"}])

    def on_results(events, stream):
        stream.push([
            _ev(type="agent.message",
                content=[_ev(type="text", text="1 deal; email queued for approval.")]),
            _idle("end_turn"),
        ])

    r, sends, _ = _runtime(
        [
            _tool_use("read_crm", {"entity": "deals"}, "sevt_a"),
            _tool_use("send_email", {"to": "x@y.co", "body": "hi"}, "sevt_b"),
            _idle("requires_action"),
        ],
        factory=_factory(db=db, greenlight=gl), on_tool_results=on_results,
    )
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "summarize the deals then email the lead")

    assert len(sends) == 2 and len(sends[1]) == 2  # one batch, both results, arrival order
    assert [e["custom_tool_use_id"] for e in sends[1]] == ["sevt_a", "sevt_b"]
    assert json.loads(sends[1][1]["content"][0]["text"])["status"] == "queued_for_approval"
    assert db.reads == [("deals", 50)]              # AUTO executed once
    assert len(gl.queue) == 1                       # gated routed once, never performed
    assert [t["status"] for t in out["tool_results"]] == ["ok", "queued_for_approval"]
    assert [e["tool_name"] for e in out["pending_approvals"]] == ["send_email"]
    assert out["answer"] == "1 deal; email queued for approval."


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


# ---------------------------------------------------------------- reconnect-with-consolidation
# The ratified brief's named deadlock: an SSE drop while a custom_tool_use round is in flight.
# The adapter re-opens the stream ONCE, replays the gap via events.list (deduped by server
# event id — for agent.custom_tool_use the event id IS the custom_tool_use id), and resumes.
class _DroppingStream(_LiveStream):
    """Yields its scripted events, then the SSE transport dies (connection-shaped failure)."""

    def __next__(self):
        if not self._q:
            raise ConnectionError("SSE transport reset")
        return self._q.pop(0)


def _idle_ev(stop: str, eid: str):
    return _ev(type="session.status_idle", stop_reason=_ev(type=stop), id=eid)


def _msg_ev(text: str, eid: str):
    return _ev(type="agent.message", content=[_ev(type="text", text=text)], id=eid)


def _reconnect_runtime(streams, replay, factory=None, on_tool_results=None):
    """Runtime over a mocked client whose stream DROPS: `streams` are handed out per open (the
    reconnect re-opens), `replay` is what events.list returns (the FULL session log so far)."""
    r = ManagedAgentsRuntime(
        api_key="test-key", environment_id="env_t", tool_context_factory=factory,
    )
    client = mock.MagicMock(name="anthropic_client")
    client.beta.sessions.create.return_value = SimpleNamespace(id="sess_live_1", status="idle")
    streams = list(streams)
    client.beta.sessions.events.stream.side_effect = streams
    client.beta.sessions.events.list.return_value = list(replay)
    sends: list[list[dict]] = []

    def _send(session_id, events, extra_headers=None):
        sends.append(list(events))
        if on_tool_results and any(e.get("type") == "user.custom_tool_result" for e in events):
            on_tool_results(events, streams)

    client.beta.sessions.events.send.side_effect = _send
    r._client = client
    return r, sends, client


@pytest.mark.unit
def test_stream_drop_before_the_round_reconnects_replays_and_resolves():
    # Drop while a tool round is in flight: the call arrived, then the stream died BEFORE the
    # requires_action gate. Reconnect replays the gap (the already-collected call is deduped by
    # id, the missed idle fires the gate), the tool executes EXACTLY ONCE, and the drain
    # resumes on the fresh stream to the data-grounded answer.
    db = _Db(rows=[{"id": "d1"}, {"id": "d2"}])

    def on_results(events, streams):
        streams[1].push([_msg_ev("You have 2 open deals.", "sevt_3"),
                         _idle_ev("end_turn", "sevt_4")])

    tool_call = _tool_use("read_crm", {"entity": "deals"}, "sevt_1")
    r, sends, client = _reconnect_runtime(
        streams=[_DroppingStream([tool_call]), _LiveStream([])],
        replay=[tool_call, _idle_ev("requires_action", "sevt_2")],
        factory=_factory(db=db), on_tool_results=on_results,
    )
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "how many open deals?")

    assert out["answer"] == "You have 2 open deals."
    assert out["pending_approvals"] == []
    assert db.reads == [("deals", 50)]  # executed exactly once despite the replayed call event
    assert len(sends) == 2              # user.message + ONE result batch
    assert sends[1][0]["custom_tool_use_id"] == "sevt_1"
    assert client.beta.sessions.events.stream.call_count == 2  # bounded: one reconnect
    client.beta.sessions.events.list.assert_called_once_with(
        session_id=session.id, extra_headers={"anthropic-beta": mock.ANY}
    )


@pytest.mark.unit
def test_stream_drop_after_results_dedupes_already_answered_calls():
    # Drop AFTER the round resolved (results already submitted): the replay re-delivers the
    # answered tool call + the consumed idle — both deduped by id, so nothing re-executes and
    # no result is double-submitted; the gap's final answer is consolidated from the replay.
    db = _Db(rows=[{"id": "d1"}])
    r, sends, client = _reconnect_runtime(
        streams=[
            _DroppingStream([_tool_use("read_crm", {"entity": "deals"}, "sevt_1"),
                             _idle_ev("requires_action", "sevt_2")]),
            _LiveStream([]),
        ],
        replay=[
            _tool_use("read_crm", {"entity": "deals"}, "sevt_1"),   # answered — must dedupe
            _idle_ev("requires_action", "sevt_2"),                  # consumed — must dedupe
            _msg_ev("You have 1 open deal.", "sevt_3"),             # emitted while dark
            _idle_ev("end_turn", "sevt_4"),
        ],
        factory=_factory(db=db),
    )
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "how many open deals?")

    assert out["answer"] == "You have 1 open deal."
    assert db.reads == [("deals", 50)]  # once — never re-run on replay
    assert len(sends) == 2              # the result batch was never resubmitted
    assert out["tool_results"] == [
        {"tool": "read_crm", "custom_tool_use_id": "sevt_1", "status": "ok"}
    ]
    assert out["pending_approvals"] == []


@pytest.mark.unit
def test_second_drop_fails_loud_bounded_retry():
    r, _sends, client = _reconnect_runtime(
        streams=[_DroppingStream([]), _DroppingStream([])],  # drops again after the reconnect
        replay=[],
        factory=_factory(db=_Db()),
    )
    session = r.create_session("coord_1", tenant_id="tenant-a")
    with pytest.raises(RuntimeError, match="dropped again .* giving up"):
        r.send_message(session, "hello")
    assert client.beta.sessions.events.stream.call_count == 2  # exactly one reconnect attempt


@pytest.mark.unit
def test_non_connection_errors_never_reconnect():
    # Only connection-shaped failures are reconnectable; anything else propagates unchanged
    # (no replay, no second stream — a logic error must never be masked by a retry).
    class _BrokenStream(_LiveStream):
        def __next__(self):
            raise ValueError("not a transport failure")

    r, _sends, client = _reconnect_runtime(
        streams=[_BrokenStream([]), _LiveStream([])], replay=[], factory=_factory(db=_Db()),
    )
    session = r.create_session("coord_1", tenant_id="tenant-a")
    with pytest.raises(ValueError, match="not a transport failure"):
        r.send_message(session, "hello")
    assert client.beta.sessions.events.stream.call_count == 1
    client.beta.sessions.events.list.assert_not_called()
