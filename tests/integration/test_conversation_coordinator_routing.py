"""Integration: coordinator-driven tool routing on real runtimes (TODO AI/P1 resolved).

On any NON-fake runtime, `Conversation.send` routes from the coordinator's send_message event
digest (agent.custom_tool_use / delegations) — the offline `_ACTION_TOOLS` regex is explicitly
gated to FakeRuntime and never runs:

- a side-effecting tool the coordinator names resolves through the TRUSTED registry and lands a
  Greenlight proposal (draft-only — the Phase 4 base class never performs the side effect);
- read-only tool events that reach the digest UN-EXECUTED pass through untouched (AUTO tools
  normally execute client-side inside the runtime's send_message loop — ratified #123; the
  facade never re-runs them); an unknown tool name is never default-allowed — surfaced as-is,
  nothing executes;
- an action-verb utterance with NO coordinator tool event produces NO proposal (regex is off).

The MA-shaped send_message digest is exactly what agents.runtime.ManagedAgentsRuntime returns
({session_id, tenant_id, delegations, answer, pending_approvals}); a stub runtime replays it
offline. FakeRuntime regex tests live in test_conversation_turn.py, unchanged.
"""
from datetime import date

import pytest

from agents.runtime import Session
from api.control.greenlight import Greenlight
from conv.analytics import Analytics, EventType
from conv.session import Conversation

TODAY = date(2026, 6, 9)


class StubManagedRuntime:
    """Replays a canned ManagedAgentsRuntime.send_message digest — NOT a FakeRuntime, so the
    conversation takes the coordinator-driven path (and requires persisted ids, as in prod)."""

    def __init__(self, response: dict):
        self.response = dict(response)
        self.sent: list[tuple[str, str]] = []

    def create_session(self, coordinator_id, tenant_id, vault_id=None, environment_id=None):
        return Session(
            id="sess-1", tenant_id=tenant_id, coordinator_id=coordinator_id,
            metadata={"tenant_id": tenant_id, "environment_id": environment_id},
        )

    def send_message(self, session, message):
        self.sent.append((session.id, message))
        return {"session_id": session.id, "tenant_id": session.tenant_id, **self.response}


def _convo(runtime, **kw):
    return Conversation(
        tenant_id="tenant-A", today=TODAY, runtime=runtime,
        coordinator_id="coord-A", environment_id="env-A", **kw,
    )


@pytest.mark.integration
def test_coordinator_named_side_effecting_tool_routes_to_greenlight():
    rt = StubManagedRuntime({
        "answer": "",
        "delegations": ["nadia"],
        "pending_approvals": [{
            "status": "pending", "tool": "send_email",
            "input": {"to": "lead@acme.com", "subject": "Following up", "body": "hi there"},
            "custom_tool_use_id": "ctu_1",
        }],
    })
    gl = Greenlight()
    analytics = Analytics()
    convo = _convo(rt, greenlight=gl, analytics=analytics)

    # NO action verbs in the utterance — the COORDINATOR picked the tool, not a regex.
    turn = convo.send("what should we do about the Acme lead?")

    assert turn.pending_approvals, "the coordinator's tool choice should surface an approval"
    approval = turn.pending_approvals[0]
    assert approval["status"] == "pending"
    assert approval["proposed_action"]["action"] == "send_email"
    # It landed in the real control-plane queue, tenant-scoped — and nothing was sent.
    pending = gl.list_pending("tenant-A")
    assert len(pending) == 1
    assert pending[0]["proposed_action"]["to"] == "lead@acme.com"
    assert gl.list_pending("tenant-B") == []
    # Delegations + the default action answer flow through; analytics recorded the routing.
    assert turn.delegations == ["nadia"]
    assert turn.answer == "Prepared an action for your approval."
    assert analytics.list("tenant-A", type=EventType.TOOL_CALL)
    assert analytics.list("tenant-A", type=EventType.APPROVAL)


@pytest.mark.integration
def test_regex_routing_is_gated_off_on_real_runtimes():
    # An utterance FULL of regex action verbs, but the coordinator named no tool: no proposal.
    rt = StubManagedRuntime({"answer": "Here's the summary.", "delegations": [],
                             "pending_approvals": []})
    gl = Greenlight()
    convo = _convo(rt, greenlight=gl)

    turn = convo.send("send an email to update the deal and issue a quote")

    assert turn.pending_approvals == []
    assert gl.list_pending("tenant-A") == []  # the regex never invoked a tool
    assert turn.answer == "Here's the summary."
    assert rt.sent == [("sess-1", "send an email to update the deal and issue a quote")]


@pytest.mark.integration
def test_readonly_and_unknown_tool_events_surface_untouched():
    events = [
        # Read-only reaching the digest un-executed (no clients / gated round in the runtime
        # loop) — the facade must NOT re-run it.
        {"status": "pending", "tool": "read_crm", "input": {"entity": "deals"},
         "custom_tool_use_id": "ctu_r"},
        # Unknown: never default-allowed into the registry — surfaced as-is.
        {"status": "pending", "tool": "evil_tool", "input": {"x": 1},
         "custom_tool_use_id": "ctu_e"},
        # The adapter's requires_action placeholder (no tool name) passes through too.
        {"status": "pending", "reason": "requires_action"},
    ]

    class _ExplodingCrm:
        def read(self, **kw):  # pragma: no cover — must never be called
            raise AssertionError("read-only tool events must not execute in the facade")

    rt = StubManagedRuntime({"answer": "ok", "delegations": [], "pending_approvals": list(events)})
    gl = Greenlight()
    convo = _convo(rt, greenlight=gl, crm=_ExplodingCrm())

    turn = convo.send("show me the deals")

    assert turn.pending_approvals == events  # surfaced verbatim, nothing resolved or executed
    assert gl.list_pending("tenant-A") == []


@pytest.mark.integration
def test_explicit_action_kwargs_top_up_the_coordinator_input():
    rt = StubManagedRuntime({
        "answer": "", "delegations": [],
        "pending_approvals": [{
            "status": "pending", "tool": "send_email",
            "input": {"to": "lead@acme.com", "subject": "old subject", "body": "hi"},
            "custom_tool_use_id": "ctu_1",
        }],
    })
    gl = Greenlight()
    convo = _convo(rt, greenlight=gl)

    convo.send("follow up", subject="new subject")  # caller-supplied args win

    pending = gl.list_pending("tenant-A")
    assert len(pending) == 1
    assert pending[0]["proposed_action"]["subject"] == "new subject"
    assert pending[0]["proposed_action"]["to"] == "lead@acme.com"


@pytest.mark.integration
def test_greenlight_unconfigured_still_surfaces_the_proposal():
    rt = StubManagedRuntime({
        "answer": "", "delegations": [],
        "pending_approvals": [{
            "status": "pending", "tool": "send_email",
            "input": {"to": "x@y.com", "subject": "s", "body": "b"},
            "custom_tool_use_id": "ctu_1",
        }],
    })
    convo = _convo(rt)  # no greenlight injected

    turn = convo.send("anything")

    assert len(turn.pending_approvals) == 1
    entry = turn.pending_approvals[0]
    assert entry["status"] == "pending"
    assert entry["proposal"]["action"] == "send_email"  # surfaced — nothing silently executed
