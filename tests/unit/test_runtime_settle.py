"""Unit: the turn SETTLE loop — a /chat turn finishes the delegation round-trips.

Live finding (2026-06-12, demo-tenant browser test): the drain returned at the FIRST
`requires_action` idle while the worker was still seconds away from serving the
coordinator's read-only `search_rag` calls. The customer got "I've asked Scout — I'll
report back" as the final answer and had to NUDGE the chat ("did you hear back") to
harvest the result; grounding/citations were skipped because the unserved reads sat in
`pending_approvals`. Not agentic.

The settle contract tested here:
  * `requires_action` with OPEN calls and settle budget remaining -> KEEP DRAINING (the
    worker's `user.custom_tool_result` resumes the session) — the turn returns the FINAL
    answer with the reads served and nothing bogus in pending;
  * budget exhausted -> exactly today's fail-closed surface (open calls -> pending);
  * a stream that ENDS with calls still open (worker down) -> fail-closed surface, no hang;
  * a routed Greenlight proposal (queued_for_approval) is a LEGITIMATE stop — settle never
    waits on it;
  * multi-part coordinator narration folds into readable paragraphs, not jammed text.
"""
from types import SimpleNamespace
from unittest import mock

import pytest

from agents.runtime import ManagedAgentsRuntime


def _ev(**kw):
    return SimpleNamespace(**kw)


class _FakeStream:
    def __init__(self, events):
        self._events = list(events)

    def __enter__(self):
        return iter(self._events)

    def __exit__(self, *exc):
        return False


def _mock_client(stream_events=()):
    client = mock.MagicMock(name="anthropic_client")
    client.beta.environments.create.return_value = SimpleNamespace(id="env_live_1")
    client.beta.sessions.create.return_value = SimpleNamespace(id="sess_live_1", status="idle")
    client.beta.sessions.events.stream.return_value = _FakeStream(stream_events)
    client.beta.sessions.events.send.return_value = None
    return client


def _managed(stream_events=(), *, clock=None, settle_budget_s=None) -> ManagedAgentsRuntime:
    kw = {}
    if clock is not None:
        kw["clock"] = clock
    if settle_budget_s is not None:
        kw["settle_budget_s"] = settle_budget_s
    r = ManagedAgentsRuntime(api_key="test-key", **kw)
    r._client = _mock_client(stream_events)
    return r


def _session(r):
    r.create_environment("uplift-vpc")
    return r.create_session("coord_1", tenant_id="tenant-a")


# The live bug's exact shape: coordinator narrates the delegation, the reads go up,
# requires_action fires BEFORE the worker has answered — then the worker serves them and
# the coordinator produces the real answer.
_DELEGATED_TURN = [
    _ev(type="session.thread_created", agent_name="scout", session_thread_id="th_1"),
    _ev(type="agent.message",
        content=[_ev(type="text", text="I've asked Scout to search our internal docs.")]),
    _ev(type="agent.custom_tool_use", name="search_rag", input={"q": "discount policy"},
        id="ctu_1"),
    _ev(type="session.status_idle", stop_reason=_ev(type="requires_action")),
    _ev(type="user.custom_tool_result", custom_tool_use_id="ctu_1",
        content=[_ev(type="text", text='{"status": "ok", "hits": []}')]),
    _ev(type="agent.message",
        content=[_ev(type="text", text="Discounts cap at 15% without VP approval.")]),
    _ev(type="session.status_idle", stop_reason=_ev(type="end_turn")),
]


@pytest.mark.unit
def test_settle_drains_through_requires_action_to_the_final_answer():
    r = _managed(_DELEGATED_TURN)
    out = r.send_message(_session(r), "What is our discount policy?")
    # The worker-served read is a tool_result, NOT a pending entry.
    assert out["pending_approvals"] == []
    assert [(t["tool"], t["status"]) for t in out["tool_results"]] == [("search_rag", "ok")]
    # The FINAL answer arrived in the same turn — no human nudge required.
    assert "Discounts cap at 15%" in out["answer"]
    assert out["delegations"] == ["scout"]


@pytest.mark.unit
def test_settle_folds_narration_into_paragraphs():
    r = _managed(_DELEGATED_TURN)
    out = r.send_message(_session(r), "What is our discount policy?")
    # Multi-part narration is readable — never the jammed "docs.Discounts" concatenation.
    assert "docs.Discounts" not in out["answer"]
    assert out["answer"] == ("I've asked Scout to search our internal docs.\n\n"
                             "Discounts cap at 15% without VP approval.")


@pytest.mark.unit
def test_settle_budget_exhausted_fails_closed_like_today():
    # A clock that burns the whole budget before the requires_action idle is examined.
    t = {"now": 0.0}

    def clock():
        t["now"] += 100.0
        return t["now"]

    r = _managed(_DELEGATED_TURN, clock=clock, settle_budget_s=45.0)
    out = r.send_message(_session(r), "What is our discount policy?")
    # Exactly today's fail-closed shape: the open call surfaces, the turn returns.
    assert [p.get("tool") for p in out["pending_approvals"]] == ["search_rag"]
    assert "Discounts cap at 15%" not in out["answer"]


@pytest.mark.unit
def test_stream_end_with_open_calls_surfaces_them_no_hang():
    # Worker down: the stream simply ends after requires_action — fail closed, never a hang.
    events = _DELEGATED_TURN[:4]
    r = _managed(events)
    out = r.send_message(_session(r), "anything")
    assert [p.get("tool") for p in out["pending_approvals"]] == ["search_rag"]


@pytest.mark.unit
def test_settle_never_waits_on_a_routed_greenlight_proposal():
    # A gated tool's draft is queued for approval — that is a LEGITIMATE stop; the turn
    # returns at the following requires_action idle with the routed entry in pending.
    events = [
        _ev(type="agent.custom_tool_use", name="send_email",
            input={"to": "a@b.c"}, id="ctu_9"),
        _ev(type="user.custom_tool_result", custom_tool_use_id="ctu_9",
            content=[_ev(type="text",
                         text='{"status": "pending_approval", "proposal": {"x": 1}}')]),
        _ev(type="session.status_idle", stop_reason=_ev(type="requires_action")),
    ]
    r = _managed(events)
    out = r.send_message(_session(r), "email the lead")
    assert [p.get("tool_name") for p in out["pending_approvals"]] == ["send_email"]
    assert [(t["tool"], t["status"]) for t in out["tool_results"]] == [
        ("send_email", "queued_for_approval")]


# Live finding ROUND 2 (post-deploy re-test, 2026-06-12): `requires_action` can fire for a
# DELEGATED THREAD's upcoming work BEFORE any custom_tool_use reaches the stream — zero open
# calls at the idle, so the v1 settle (which keyed on open calls) still returned early with
# pending=[{reason: requires_action}]. Settle must wait through requires_action regardless of
# open calls — UNLESS a routed Greenlight proposal is already pending (a legitimate stop).
_THREAD_RACE_TURN = [
    _ev(type="session.thread_created", agent_name="scout", session_thread_id="th_1"),
    _ev(type="agent.message",
        content=[_ev(type="text", text="Routing this to Scout.")]),
    _ev(type="session.status_idle", stop_reason=_ev(type="requires_action")),  # NO open calls
    _ev(type="agent.custom_tool_use", name="search_rag", input={"q": "policy"}, id="ctu_2"),
    _ev(type="session.status_idle", stop_reason=_ev(type="requires_action")),  # call now open
    _ev(type="user.custom_tool_result", custom_tool_use_id="ctu_2",
        content=[_ev(type="text", text='{"status": "ok", "hits": []}')]),
    _ev(type="agent.message",
        content=[_ev(type="text", text="AEs can approve up to 10% on their own.")]),
    _ev(type="session.status_idle", stop_reason=_ev(type="end_turn")),
]


@pytest.mark.unit
def test_settle_waits_through_requires_action_with_no_open_calls_yet():
    r = _managed(_THREAD_RACE_TURN)
    out = r.send_message(_session(r), "What can an AE approve?")
    assert out["pending_approvals"] == []
    assert "AEs can approve up to 10%" in out["answer"]
    assert [(t["tool"], t["status"]) for t in out["tool_results"]] == [("search_rag", "ok")]


@pytest.mark.unit
def test_budget_exhausted_no_open_calls_surfaces_requires_action_reason():
    t = {"now": 0.0}

    def clock():
        t["now"] += 100.0
        return t["now"]

    r = _managed(_THREAD_RACE_TURN, clock=clock, settle_budget_s=45.0)
    out = r.send_message(_session(r), "What can an AE approve?")
    # Today's fail-closed shape for the no-open-calls case.
    assert out["pending_approvals"] == [{"status": "pending", "reason": "requires_action"}]
    assert "AEs can approve up to 10%" not in out["answer"]


# Live finding ROUND 3 (2026-06-12): holding ONE request can't clear the 60s CloudFront/ALB
# ceiling — a delegation-heavy turn 504'd at the edge mid-settle. The async turn contract:
# send_message returns when the per-REQUEST budget is spent (unsettled), and continue_drain
# picks the SAME session back up — replaying missed events via events.list (deduped by the
# per-session ledger) then streaming on — so the client can settle a turn across several
# short requests with zero human intervention.

class _FakeEventsList:
    def __init__(self, events):
        self._events = list(events)

    def __call__(self, *a, **kw):
        return iter(self._events)


@pytest.mark.unit
def test_continue_drain_replays_missed_events_then_finishes_the_turn():
    first_leg = [
        _ev(type="session.thread_created", agent_name="scout", session_thread_id="th_1", id="e1"),
        _ev(type="agent.message", id="e2",
            content=[_ev(type="text", text="Routing this to Scout.")]),
        _ev(type="session.status_idle", id="e3",
            stop_reason=_ev(type="requires_action")),
    ]
    # Emitted while no request was attached (between /chat and /chat/continue):
    missed = [
        _ev(type="agent.custom_tool_use", name="search_rag", input={"q": "policy"}, id="ctu_5"),
        _ev(type="user.custom_tool_result", custom_tool_use_id="ctu_5", id="e5",
            content=[_ev(type="text", text='{"status": "ok", "hits": []}')]),
        _ev(type="agent.message", id="e6",
            content=[_ev(type="text", text="AEs can approve up to 10% on their own.")]),
        _ev(type="session.status_idle", id="e7", stop_reason=_ev(type="end_turn")),
    ]

    # Per-request budget burns out during leg 1 (clock jumps), so send_message returns
    # unsettled; the continue call gets a fresh budget (anchor resets per request).
    t = {"now": 0.0, "step": 100.0}

    def clock():
        t["now"] += t["step"]
        return t["now"]

    r = _managed(first_leg, clock=clock, settle_budget_s=45.0)
    session = _session(r)
    out1 = r.send_message(session, "What can an AE approve?")
    assert out1["pending_approvals"] == [{"status": "pending", "reason": "requires_action"}]

    # The continue request: list-replay carries the FULL session history (already-seen leg-1
    # events MUST dedupe) + everything missed; no new stream events needed.
    t["step"] = 0.1  # plenty of budget this request
    r._client.beta.sessions.events.list = _FakeEventsList(first_leg + missed)
    r._client.beta.sessions.events.stream.return_value = _FakeStream([])
    out2 = r.continue_drain(session)

    assert "AEs can approve up to 10%" in out2["answer"]
    assert out2["pending_approvals"] == []
    assert [(x["tool"], x["status"]) for x in out2["tool_results"]] == [("search_rag", "ok")]
    # Dedupe held: leg-1's delegation is not re-reported on the continue.
    assert out2["delegations"] == []
    # And NO user.message was sent by the continue (observe-only).
    r._client.beta.sessions.events.send.assert_called_once()


# Live finding ROUND 4 (2026-06-12): the budget is only checkable when EVENTS arrive — a long
# inference round emits nothing for 40+s, the stream wait blocks silently, and the request
# sails past the 60s edge ceiling into a 504. Two guarantees:
#   * the SDK client is built with a BOUNDED stream read timeout, so a silent wait wakes up
#     in time to surface the turn unsettled (the continue leg picks it back up);
#   * a reconnect-exhausted stream drop SURFACES unsettled instead of raising — under the
#     async contract a recoverable in-flight turn must never become a customer-facing 500.

class _DroppingStream:
    """A stream whose iteration raises a connection-shaped failure (read timeout)."""

    def __init__(self, events, exc):
        self._events = list(events)
        self._exc = exc

    def __enter__(self):
        def gen():
            yield from self._events
            raise self._exc
        return gen()

    def __exit__(self, *exc):
        return False


@pytest.mark.unit
def test_reconnect_exhausted_drop_surfaces_unsettled_never_raises():
    events = [
        _ev(type="agent.message", id="m1",
            content=[_ev(type="text", text="Routing this to Scout.")]),
    ]
    client_streams = [
        _DroppingStream(events, TimeoutError("read timeout")),
        _DroppingStream([], TimeoutError("read timeout")),  # reconnect drops too
    ]
    r = _managed([])
    r._client.beta.sessions.events.stream.side_effect = client_streams
    r._client.beta.sessions.events.list = lambda *a, **kw: iter([])
    out = r.send_message(_session(r), "What can an AE approve?")
    # Surfaced unsettled — recoverable via /chat/continue — never a RuntimeError/500.
    assert out["answer"] == "Routing this to Scout."
    assert out["pending_approvals"] == [{"status": "pending", "reason": "stream_interrupted"}]


@pytest.mark.unit
def test_client_is_built_with_a_bounded_stream_read_timeout():
    import httpx

    r = ManagedAgentsRuntime(api_key="test-key")
    client = r._c()
    t = client.timeout
    assert isinstance(t, httpx.Timeout)
    assert t.read is not None and t.read <= 30.0, t  # must wake up under the 60s edge ceiling
