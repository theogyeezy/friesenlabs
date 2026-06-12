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
