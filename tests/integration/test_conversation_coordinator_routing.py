"""Integration: coordinator-driven routing on real runtimes — the conv layer EXECUTES NOTHING.

On any NON-fake runtime, `Conversation.send` handles the `send_message` event digest only
(docs/decisions/custom-tool-execution-path.md: ONE executor owns tool execution — the deployed
EnvironmentWorker on Managed Agents, the runtime's own loop on the self-hosted HIPAA fallback):

- digest `tool_results` (executor-served calls) are recorded to analytics, never re-run;
- already-routed `tool_name` entries (a gated call's Greenlight proposal, built IN the executor)
  pass through untouched — the proposal is never enqueued twice; approvals hit analytics;
- pending `tool` entries that reached the digest UN-served (worker down, unknown name) surface
  untouched — the conv layer never resolves a name through the registry, never invokes,
  never default-allows;
- the offline `_ACTION_TOOLS` regex stays explicitly gated to FakeRuntime and never runs;
- `action_kwargs` are facade-only: with a server-side executor there is no in-process
  invocation to top up.

The MA-shaped send_message digest is exactly what agents.runtime.ManagedAgentsRuntime returns
({session_id, tenant_id, delegations, answer, pending_approvals, tool_results}); a stub runtime
replays it offline. FakeRuntime regex tests live in test_conversation_turn.py, unchanged.
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
def test_unserved_side_effecting_tool_surfaces_untouched_never_invoked():
    # The executor (worker) didn't serve the gated call — it reaches the digest as a pending
    # `tool` entry. The conv layer must NOT resolve it through the registry or invoke it:
    # nothing may land in Greenlight from this process (ONE executor owns Greenlight routing).
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

    turn = convo.send("what should we do about the Acme lead?")

    assert turn.pending_approvals == rt.response["pending_approvals"]  # surfaced verbatim
    assert gl.list_pending("tenant-A") == []  # NEVER enqueued by the conv layer
    assert analytics.list("tenant-A", type=EventType.TOOL_CALL) == []
    assert analytics.list("tenant-A", type=EventType.APPROVAL) == []
    assert turn.delegations == ["nadia"]
    # Unserved call = in-flight turn under the async contract (settled=False, the client
    # continues) — the old "Prepared an action" copy claimed a draft that had not landed.
    assert turn.settled is False
    assert turn.answer == ""


@pytest.mark.integration
def test_already_routed_entry_passes_through_and_approval_hits_analytics():
    # The EXECUTOR routed the gated call to Greenlight (Tool.invoke, draft-only) and the digest
    # carries the already-routed `tool_name` entry + the served call in tool_results. The conv
    # layer records the trace and passes the entry through untouched.
    routed = {
        "status": "pending_approval", "tool_name": "send_email",
        "input": {"to": "lead@acme.com", "body": "hi"},
        "custom_tool_use_id": "ctu_1",
        "proposal": {"action": "send_email", "to": "lead@acme.com"},
        "approval": {"id": 42, "status": "pending"},
    }
    rt = StubManagedRuntime({
        "answer": "Queued for your approval.", "delegations": [],
        "pending_approvals": [dict(routed)],
        "tool_results": [
            {"tool": "send_email", "custom_tool_use_id": "ctu_1",
             "status": "queued_for_approval"},
        ],
    })
    gl = Greenlight()
    analytics = Analytics()
    convo = _convo(rt, greenlight=gl, analytics=analytics)

    turn = convo.send("email the Acme lead")

    assert turn.pending_approvals == [routed]  # untouched — never re-invoked/enqueued twice
    assert gl.list_pending("tenant-A") == []
    tool_calls = analytics.list("tenant-A", type=EventType.TOOL_CALL)
    assert [(e["payload"]["tool"], e["payload"]["status"]) for e in tool_calls] == [
        ("send_email", "queued_for_approval")
    ]
    approvals = analytics.list("tenant-A", type=EventType.APPROVAL)
    assert [(e["payload"]["action"], e["payload"]["approval_id"]) for e in approvals] == [
        ("send_email", 42)
    ]
    assert turn.answer == "Queued for your approval."


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
        # Read-only reaching the digest un-served (worker down) — the facade must NOT run it.
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
def test_action_kwargs_are_facade_only_nothing_invoked_on_real_runtimes():
    # With one server-side executor there is no in-process invocation to top up: explicit
    # caller kwargs change nothing on the real path — the entry surfaces verbatim and nothing
    # lands in Greenlight from this process.
    entry = {
        "status": "pending", "tool": "send_email",
        "input": {"to": "lead@acme.com", "subject": "old subject", "body": "hi"},
        "custom_tool_use_id": "ctu_1",
    }
    rt = StubManagedRuntime({"answer": "", "delegations": [],
                             "pending_approvals": [dict(entry)]})
    gl = Greenlight()
    convo = _convo(rt, greenlight=gl)

    turn = convo.send("follow up", subject="new subject")

    assert turn.pending_approvals == [entry]  # untouched — no top-up, no invocation
    assert gl.list_pending("tenant-A") == []


@pytest.mark.integration
def test_executor_served_tool_results_hit_analytics_never_rerun():
    rt = StubManagedRuntime({
        "answer": "You have 2 open deals.", "delegations": ["scout"],
        "pending_approvals": [],
        "tool_results": [
            {"tool": "read_crm", "custom_tool_use_id": "ctu_1", "status": "ok"},
        ],
    })
    analytics = Analytics()

    class _ExplodingCrm:
        def read(self, **kw):  # pragma: no cover — served results are never re-run
            raise AssertionError("tool_results must never be re-executed")

    convo = _convo(rt, analytics=analytics, crm=_ExplodingCrm())
    turn = convo.send("how many open deals?")

    assert turn.answer == "You have 2 open deals."
    tool_calls = analytics.list("tenant-A", type=EventType.TOOL_CALL)
    assert [(e["payload"]["tool"], e["payload"]["status"]) for e in tool_calls] == [
        ("read_crm", "ok")
    ]
