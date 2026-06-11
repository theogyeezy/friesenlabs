"""Unit: agents/playbooks/runner.py — the trigger->run seam over the EXISTING agent plane.

These pin the runner's own guarantees: an activated playbook runs on a trigger through the
runtime; a side-effecting action surfaces as a Greenlight DRAFT (proposed, never sent/approved);
the draft-only + execute-nothing invariants hold; autonomy is carried; only ACTIVE playbooks run;
and ANY failure is contained (the trigger source is never crashed).
"""
import pytest

from agents.playbooks import PlaybookRunner, RunRecord, TriggerEvent, run
from agents.playbooks.runner import _trigger_prompt
from agents.playbooks.store import InMemoryPlaybookStore
from agents.runtime import FakeRuntime, Session


def _defn(autonomy="L1"):
    return {
        "name": "Welcome new leads",
        "description": "Greet and qualify a freshly-created lead.",
        "trigger": {"kind": "event", "event": "lead.created"},
        "roster": [
            {"agent": "scout", "tools": ["read_crm"]},
            {"agent": "nadia", "tools": ["draft_email"]},
        ],
        "autonomy": autonomy,
        "greenlight": {"side_effects": "always_ask"},
    }


def _active_playbook(store, tenant="tenant-a", autonomy="L1"):
    row = store.create(tenant, _defn(autonomy))
    store.set_status(tenant, row["id"], "active")
    return row["id"]


class StubRuntime(FakeRuntime):
    """A FakeRuntime whose send_message returns a caller-supplied digest — so a test can shape
    exactly the worker round-trip the runner must OBSERVE (a gated draft, a served read-only call,
    an unserved call) without a live worker. Everything else (agent/coordinator/session creation)
    is the real FakeRuntime bookkeeping, so the execute-nothing assertions are meaningful."""

    def __init__(self, response):
        super().__init__()
        self._response = response

    def send_message(self, session, message):
        self.sent.append((session.id, message))
        return {"session_id": session.id, "tenant_id": session.tenant_id, **self._response}


# A side-effecting tool's reply AFTER the single executor routed it to Greenlight via Tool.invoke
# (draft-only): the proposal exists, the side effect did NOT run, and approval is still pending.
_ROUTED_SEND = {
    "status": "pending_approval",
    "tool_name": "send_email",
    "input": {"to": "lead@acme.com", "subject": "Welcome", "body": "(draft) Re: welcome"},
    "custom_tool_use_id": "ctu_1",
    "proposal": {"action": "send_email", "to": "lead@acme.com", "reasoning": "Send email to lead@acme.com"},
    "approval": {"id": 7, "status": "pending"},
}


@pytest.mark.unit
def test_draft_email_action_is_proposed_through_greenlight_never_sent():
    store = InMemoryPlaybookStore()
    pid = _active_playbook(store)
    rt = StubRuntime({
        "answer": "Drafted a welcome email for your approval.",
        "delegations": ["scout", "nadia"],
        "pending_approvals": [dict(_ROUTED_SEND)],
        "tool_results": [
            {"tool": "draft_email", "custom_tool_use_id": "ctu_0", "status": "ok"},
            {"tool": "send_email", "custom_tool_use_id": "ctu_1", "status": "queued_for_approval"},
        ],
    })

    rec = run(rt, store, "tenant-a", pid, TriggerEvent(kind="event", name="lead.created"))

    # The side-effecting send is PROPOSED (draft-only), never executed and never approved.
    assert rec.status == "pending"
    assert rec.actions_proposed == [_ROUTED_SEND]  # surfaced verbatim — never re-invoked
    assert rec.actions_approved == [], "a trigger never auto-approves a side effect"
    # The runner executed NO tool itself — exactly one send_message, the digest's results recorded.
    assert rt.sent and len(rt.sent) == 1
    assert {tr["tool"] for tr in rec.tool_results} == {"draft_email", "send_email"}
    assert rec.delegations == ["scout", "nadia"]
    assert rec.autonomy == "L1"
    # The trace is an ordered, append-only audit log of the run.
    kinds = [e["event"] for e in rec.trace]
    assert kinds[:4] == ["triggered", "registered", "session", "tool_result"]
    assert {"event": "action_proposed", "tool": "send_email", "status": "pending_approval"} in rec.trace


@pytest.mark.unit
def test_runner_executes_no_tool_and_opens_exactly_one_session():
    store = InMemoryPlaybookStore()
    pid = _active_playbook(store)
    rt = StubRuntime({"answer": "All caught up.", "delegations": ["scout"]})

    rec = run(rt, store, "tenant-a", pid, {"kind": "schedule", "schedule": "0 13 * * 1"})

    assert rec.status == "ok"  # nothing pending -> ok
    assert rec.actions_proposed == [] and rec.actions_approved == []
    assert len(rt.sessions) == 1, "exactly one session per run"
    # The runner REGISTERED the playbook's narrowed roster (scout + nadia) + a flat coordinator.
    assert rt.coordinators, "a coordinator was registered for the playbook"
    [(_, agent_ids)] = rt.coordinators.items()
    assert {rt.agents[a].name for a in agent_ids} == {"scout", "nadia"}


@pytest.mark.unit
@pytest.mark.parametrize("autonomy", ["L0", "L1", "L2", "L3"])
def test_autonomy_level_is_honored_and_side_effects_stay_draft_only(autonomy):
    """At EVERY autonomy level a side-effecting action stays a Greenlight draft (the schema
    constant greenlight.side_effects='always_ask' makes this true by construction). The level is
    carried into the run record + the trigger prompt; it never auto-approves a send."""
    store = InMemoryPlaybookStore()
    pid = _active_playbook(store, autonomy=autonomy)
    rt = StubRuntime({"answer": "", "delegations": [], "pending_approvals": [dict(_ROUTED_SEND)]})

    rec = run(rt, store, "tenant-a", pid, TriggerEvent(name="lead.created"))

    assert rec.autonomy == autonomy
    assert rec.status == "pending"
    assert rec.actions_approved == []  # never auto-approved, regardless of level
    # The level rides into the coordinator instruction so the model's posture matches the dial.
    prompt = _trigger_prompt(_defn(autonomy), TriggerEvent(name="lead.created"))
    assert f"Autonomy level: {autonomy}" in prompt
    assert "route to Greenlight" in prompt


@pytest.mark.unit
def test_only_active_playbooks_run():
    store = InMemoryPlaybookStore()
    row = store.create("tenant-a", _defn())  # left in DRAFT — never activated
    rt = StubRuntime({"answer": "should not run"})

    rec = run(rt, store, "tenant-a", row["id"], TriggerEvent(name="lead.created"))

    assert rec.status == "not_active"
    assert rt.sent == [] and rt.sessions == {}, "a non-active playbook must never run"
    assert rt.coordinators == {}


@pytest.mark.unit
def test_missing_playbook_is_not_found_not_a_crash():
    store = InMemoryPlaybookStore()
    rt = StubRuntime({"answer": "x"})

    rec = run(rt, store, "tenant-a", "00000000-0000-0000-0000-000000000000", {})

    assert rec.status == "not_found"
    assert rt.sent == [] and rt.sessions == {}


@pytest.mark.unit
def test_tenant_scoped_another_tenants_playbook_is_invisible():
    """RLS contract (the store is keyed by tenant): tenant-b cannot run tenant-a's playbook."""
    store = InMemoryPlaybookStore()
    pid = _active_playbook(store, tenant="tenant-a")
    rt = StubRuntime({"answer": "x"})

    rec = run(rt, store, "tenant-b", pid, {})

    assert rec.status == "not_found"
    assert rt.sent == []


@pytest.mark.unit
def test_runner_failure_is_contained():
    """A runtime that blows up mid-turn must NOT crash the trigger source — the failure is caught
    and returned as a contained error record (side effects already could not have run)."""
    store = InMemoryPlaybookStore()
    pid = _active_playbook(store)

    class BoomRuntime(FakeRuntime):
        def send_message(self, session, message):
            raise RuntimeError("agent plane unreachable")

    rt = BoomRuntime()
    rec = run(rt, store, "tenant-a", pid, TriggerEvent(name="lead.created"))

    assert isinstance(rec, RunRecord)
    assert rec.status == "error"
    assert "agent plane unreachable" in (rec.error or "")
    assert rec.actions_approved == [], "nothing approved/executed on a contained failure"
    assert rec.trace and rec.trace[-1]["event"] == "error"


@pytest.mark.unit
def test_out_of_band_invalid_definition_fails_before_running():
    """An ACTIVE row whose stored definition was edited to escalate a grant must fail validation
    (defense in depth) before any session opens — contained as an error, nothing runs."""
    store = InMemoryPlaybookStore()
    row = store.create("tenant-a", _defn())
    store.set_status("tenant-a", row["id"], "active")
    # Mutate the stored definition out-of-band to a privilege escalation (not in scout's grant).
    store.rows[row["id"]]["definition"]["roster"][0]["tools"] = ["send_email"]
    rt = StubRuntime({"answer": "should not run"})

    rec = run(rt, store, "tenant-a", row["id"], {})

    assert rec.status == "error"
    assert "invalid playbook" in (rec.error or "")
    assert rt.sent == [] and rt.sessions == {}, "an invalid definition must never run"


@pytest.mark.unit
def test_dict_event_coercion_and_manual_default():
    assert TriggerEvent.coerce(None).kind == "manual"
    ev = TriggerEvent.coerce({"kind": "event", "event": "deal.won", "payload": {"id": 5}})
    assert ev.kind == "event" and ev.name == "deal.won" and ev.payload == {"id": 5}
    sched = TriggerEvent.coerce({"kind": "schedule", "schedule": "0 9 * * *"})
    assert sched.name == "0 9 * * *"


@pytest.mark.unit
def test_runner_class_entry_matches_module_run():
    store = InMemoryPlaybookStore()
    pid = _active_playbook(store)
    rt = StubRuntime({"answer": "ok", "delegations": []})

    rec = PlaybookRunner(rt, store).run("tenant-a", pid, TriggerEvent(name="lead.created"))
    assert rec.status == "ok"
    assert rec.playbook_id == pid
