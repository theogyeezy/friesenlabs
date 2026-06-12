"""Integration: a full /chat-shaped turn on ManagedAgentsRuntime with the EnvironmentWorker as
the SINGLE tool executor (docs/decisions/custom-tool-execution-path.md — the client-side
execution loop is gone). The mocked stream carries the worker's round-trip
(`agent.custom_tool_use` -> the worker's `user.custom_tool_result`); the Conversation observes,
records analytics, and surfaces — it executes nothing and binds no execution seam onto the
managed runtime.

Mocked anthropic client only — live shapes stay VERIFY-flagged.
"""
import json
from datetime import date
from types import SimpleNamespace
from unittest import mock

import pytest

from agents.runtime import ManagedAgentsRuntime
from api.control.greenlight import Greenlight
from conv.analytics import Analytics, EventType
from conv.session import Conversation

TODAY = date(2026, 6, 10)
_SENTINEL = object()


def _ev(**kw):
    return SimpleNamespace(**kw)


class _Stream:
    def __init__(self, events):
        self._q = list(events)

    def __enter__(self):
        return iter(self._q)

    def __exit__(self, *exc):
        return False


def _worker_result(tu_id, payload, eid):
    return _ev(
        type="user.custom_tool_result", id=eid, custom_tool_use_id=tu_id,
        content=[_ev(type="text", text=json.dumps(payload))],
    )


def _mocked_runtime(stream_events=()):
    r = ManagedAgentsRuntime(api_key="test-key", environment_id="env-A")
    client = mock.MagicMock(name="anthropic_client")
    client.beta.sessions.create.return_value = SimpleNamespace(id="sess_live_1", status="idle")
    client.beta.sessions.events.stream.return_value = _Stream(stream_events)
    sends: list[list[dict]] = []
    client.beta.sessions.events.send.side_effect = (
        lambda session_id, events, extra_headers=None: sends.append(list(events))
    )
    r._client = client
    return r, sends


class _ExplodingCrm:
    """Injected tool-side client that must never run — the conv layer executes nothing."""

    def set_tenant(self, tenant_id):  # pragma: no cover — must never be called
        raise AssertionError("the conv layer must never bind a tool execution context")

    def read(self, **kw):  # pragma: no cover — must never be called
        raise AssertionError("registry tools must never execute in the conv layer")


@pytest.mark.integration
def test_worker_served_read_tool_turn_end_to_end():
    runtime, sends = _mocked_runtime([
        _ev(type="agent.custom_tool_use", id="sevt_1", name="read_crm",
            input={"entity": "deals"}),
        _worker_result("sevt_1", {"status": "ok", "result": {"rows": [{"id": "d1"}]}}, "uevt_1"),
        _ev(type="agent.message", content=[_ev(type="text", text="You have 1 open deal.")],
            id="sevt_2"),
        _ev(type="session.status_idle", stop_reason=_ev(type="end_turn"), id="sevt_3"),
    ])
    gl = Greenlight()
    analytics = Analytics()
    convo = Conversation(
        tenant_id="tenant-A", today=TODAY, runtime=runtime,
        coordinator_id="coord-A", environment_id="env-A",
        crm=_ExplodingCrm(), greenlight=gl, analytics=analytics,
    )

    # NO execution seam was bound onto the managed runtime — the worker is the only executor.
    assert getattr(runtime, "tool_context_factory", _SENTINEL) is _SENTINEL

    turn = convo.send("how many open deals do we have?")

    assert turn.answer == "You have 1 open deal."
    assert turn.pending_approvals == []
    # This process sent only the user.message; the worker's result was OBSERVED, not produced.
    assert len(sends) == 1
    assert sends[0][0]["type"] == "user.message"
    # The served call hit the analytics trace; Greenlight untouched (read-only path).
    tool_calls = analytics.list("tenant-A", type=EventType.TOOL_CALL)
    assert [(e["payload"]["tool"], e["payload"]["status"]) for e in tool_calls] == [
        ("read_crm", "ok")
    ]
    assert gl.list_pending("tenant-A") == []


@pytest.mark.integration
def test_worker_routed_gated_tool_surfaces_untouched_never_reinvoked():
    """The worker ran Tool.invoke (draft-only) and posted the pending_approval payload; the
    Conversation surfaces the already-routed entry and must NOT enqueue a second proposal into
    its own Greenlight client (the worker's Greenlight — the same Aurora queue in prod — already
    holds it)."""
    payload = {
        "status": "pending_approval",
        "proposal": {"action": "send_email", "to": "lead@x.co", "body": "hi"},
        "approval": {"id": 7, "status": "pending"},
    }
    runtime, sends = _mocked_runtime([
        _ev(type="agent.custom_tool_use", id="sevt_1", name="send_email",
            input={"to": "lead@x.co", "body": "hi"}),
        _worker_result("sevt_1", payload, "uevt_1"),
        _ev(type="agent.message",
            content=[_ev(type="text", text="Queued the email for your approval.")], id="sevt_2"),
        _ev(type="session.status_idle", stop_reason=_ev(type="end_turn"), id="sevt_3"),
    ])
    gl = Greenlight()
    analytics = Analytics()
    convo = Conversation(
        tenant_id="tenant-A", today=TODAY, runtime=runtime,
        coordinator_id="coord-A", environment_id="env-A",
        greenlight=gl, analytics=analytics,
    )
    turn = convo.send("email the lead")

    assert turn.answer == "Queued the email for your approval."
    assert len(turn.pending_approvals) == 1
    entry = turn.pending_approvals[0]
    assert entry["tool_name"] == "send_email" and "tool" not in entry
    assert entry["approval"]["id"] == 7
    # NEVER enqueued twice: the conv-side Greenlight client saw nothing.
    assert gl.list_pending("tenant-A") == []
    assert len(sends) == 1  # user.message only
    # Both the served call and the approval hit the analytics trace.
    tool_calls = analytics.list("tenant-A", type=EventType.TOOL_CALL)
    assert [(e["payload"]["tool"], e["payload"]["status"]) for e in tool_calls] == [
        ("send_email", "queued_for_approval")
    ]
    approvals = analytics.list("tenant-A", type=EventType.APPROVAL)
    assert [(e["payload"]["action"], e["payload"]["approval_id"]) for e in approvals] == [
        ("send_email", 7)
    ]


@pytest.mark.integration
def test_unserved_call_fails_closed_through_the_conversation():
    # Worker down: the call reaches requires_action unanswered. The turn surfaces it untouched —
    # the conv layer resolves nothing through the registry and invokes nothing.
    runtime, sends = _mocked_runtime([
        _ev(type="agent.custom_tool_use", id="sevt_1", name="send_email",
            input={"to": "x@y.co", "body": "hi"}),
        _ev(type="session.status_idle", stop_reason=_ev(type="requires_action"), id="sevt_2"),
    ])
    gl = Greenlight()
    convo = Conversation(
        tenant_id="tenant-A", today=TODAY, runtime=runtime,
        coordinator_id="coord-A", environment_id="env-A",
        crm=_ExplodingCrm(), greenlight=gl,
    )
    turn = convo.send("email the lead")

    assert turn.pending_approvals == [{
        "status": "pending", "tool": "send_email",
        "input": {"to": "x@y.co", "body": "hi"}, "custom_tool_use_id": "sevt_1",
    }]
    assert gl.list_pending("tenant-A") == []  # nothing enqueued by this process
    assert len(sends) == 1                    # nothing answered by this process
    # Unserved call = in-flight turn under the async contract (settled=False, the client
    # continues) — the old "Prepared an action" copy claimed a draft that had not landed.
    assert turn.settled is False
    assert turn.answer == ""
