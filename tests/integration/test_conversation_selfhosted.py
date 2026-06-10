"""Integration: `conv.session.Conversation` over the HIPAA fallback runtime (AI/P3).

The self-hosted runtime is a REAL (non-fake) runtime, so the conversation takes the
coordinator-driven path — and because the runtime has ALREADY routed ALWAYS_ASK calls through
Greenlight inside its tool-use loop, its pending entries carry `tool_name` (not `tool`) and the
facade must pass them through untouched. The load-bearing assertion: exactly ONE proposal lands
in the queue for one side-effecting tool call (no double-invoke at the Conversation layer).
"""
from datetime import date
from types import SimpleNamespace

import pytest

from agents.runtime_selfhosted import SelfHostedToolUseRuntime
from api.control.greenlight import Greenlight
from conv.session import Conversation

TODAY = date(2026, 6, 9)


def _text(t):
    return SimpleNamespace(type="text", text=t)


def _tool_use(name, input, id="tu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=input, id=id)


def _resp(*blocks):
    return SimpleNamespace(content=list(blocks))


class _SeqClient:
    def __init__(self, responses):
        self.calls = []
        self._responses = list(responses)
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        return self._responses.pop(0)


def _convo(responses, greenlight):
    rt = SelfHostedToolUseRuntime(api_key="unused", greenlight=greenlight)
    rt._client = _SeqClient(responses)  # injected — offline
    return Conversation(
        tenant_id="tenant-A", today=TODAY, runtime=rt,
        coordinator_id="selfhosted-coord-A", environment_id="selfhosted-env-A",
        greenlight=greenlight,
    )


@pytest.mark.integration
def test_side_effecting_call_lands_exactly_one_greenlight_proposal():
    gl = Greenlight()
    convo = _convo(
        [
            _resp(_tool_use("send_email",
                            {"to": "lead@acme.com", "subject": "Hi", "body": "following up"},
                            id="tu_1")),
            _resp(_text("Drafted the email; it is waiting for your approval.")),
        ],
        gl,
    )

    # No regex action verbs needed — the MODEL picked the tool inside the runtime's loop.
    turn = convo.send("what should we do about the Acme lead?")

    # Exactly ONE proposal: the runtime routed it; the Conversation did NOT re-invoke.
    pending = gl.list_pending("tenant-A")
    assert len(pending) == 1
    assert pending[0]["proposed_action"]["action"] == "send_email"
    assert pending[0]["proposed_action"]["to"] == "lead@acme.com"
    assert gl.list_pending("tenant-B") == []  # tenant-scoped

    # The already-routed entry surfaced through the turn untouched (tool_name, never 'tool').
    assert len(turn.pending_approvals) == 1
    entry = turn.pending_approvals[0]
    assert entry["status"] == "pending_approval"
    assert entry["tool_name"] == "send_email"
    assert "tool" not in entry
    assert turn.answer == "Drafted the email; it is waiting for your approval."
    assert turn.delegations == []  # no subagent threads on the self-hosted loop
    assert turn.tenant_id == "tenant-A"


@pytest.mark.integration
def test_plain_answer_round_trip_no_proposals():
    gl = Greenlight()
    convo = _convo([_resp(_text("Acme is your largest open deal."))], gl)

    turn = convo.send("send me an update on the deal")  # regex verbs present — must be inert

    assert turn.answer == "Acme is your largest open deal."
    assert turn.pending_approvals == []
    assert gl.list_pending("tenant-A") == []  # the FakeRuntime regex never ran (real runtime)
