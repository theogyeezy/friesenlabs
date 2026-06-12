"""Unit: the compliance floor inside Greenlight + the decide-time re-validation + 422 hygiene.

THE GAP THIS GUARDS (security audit, 2026-06): the deterministic compliance checks used to run from
exactly ONE call site — ActionGate.run — while side-effecting proposals are ALSO created by the
worker (agents/tools/base.py calls greenlight.propose directly), Sidecar accept, and the playbook
runner; AND the approve/apply path never re-validated after a human `edit` mutated the payload.
Today that is masked by record-only send appliers — the day a real sender lands in APPLIERS,
compliance would have been silently absent on the dominant path. These tests pin the fix:

  * propose() — every path — runs the deterministic floor with channel classification from the
    TRUSTED tool registry (never the payload); a violation is stored DENIED with the reason and
    can never be decided/applied.
  * decide() re-validates the post-edit snapshot BEFORE the atomic status flip: a violating edit
    is rejected (422 at the route) and the approval stays PENDING.
  * The gate path is unchanged for compliant actions and never double-reports (a gate block
    happens BEFORE propose, so no denied row is stored).
  * The four /views//dashboards 422 paths answer fixed messages for unexpected errors (curated
    view_spec.ValidationError reasons still flow); internal exception text is never echoed.
"""
import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.compliance import TCPA_QUIET_START
from api.control.gate import ActionGate, GateContext
from api.control.greenlight import ComplianceViolation, Greenlight
from api.control.killswitch import KillSwitch
from api.control.traces import InMemoryTraceStore
from api.control.types import Action, Level
from api.views import SavedViews

COMPLIANT_EMAIL = {"to": "x@y.com", "subject": "Hi", "body": "hello — unsubscribe link below"}
SPAMMY_EMAIL = {"to": "x@y.com", "subject": "Hi", "body": "BUY NOW"}


def _propose(gl, *, tenant="t1", action="send_email", payload, agent="nadia"):
    return gl.propose(tenant_id=tenant, action=action, agent=agent,
                      reasoning="r", value_at_stake=None, payload=dict(payload))


def _sms_resolver(name: str) -> dict:
    """Worker-style injected classifier for a (future) SMS tool. The trusted registry carries no
    SMS sender yet, so the constructor seam is exercised exactly the way a runtime would wire it:
    classification keyed by the TRUSTED action name, never read from the payload."""
    return {"send_sms": {"side_effecting": True, "channel": "sms"}}[name]


# ---------------------------------------------------------------------------
# propose(): the deterministic floor on the worker-style DIRECT path
# (no gate in front — exactly how agents/tools/base.py, sidecar, playbooks call it).
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_direct_propose_of_unsubscribeless_email_is_denied():
    gl = Greenlight()  # default resolver = the trusted tool registry
    rec = _propose(gl, payload=SPAMMY_EMAIL)
    assert rec["status"] == "denied"
    assert "CAN-SPAM" in rec["deny_message"]
    # Not executable: never pending, and decide() refuses it like any decided row.
    assert gl.list_pending("t1") == []
    with pytest.raises(ValueError, match="already denied"):
        gl.decide("t1", rec["id"], "approve")


@pytest.mark.unit
def test_direct_propose_of_quiet_hours_sms_is_denied():
    gl = Greenlight(channel_resolver=_sms_resolver)
    rec = _propose(gl, action="send_sms",
                   payload={"to": "+15125550100", "body": "hi", "consent": True,
                            "local_hour": TCPA_QUIET_START})
    assert rec["status"] == "denied"
    assert "quiet hours" in rec["deny_message"]
    assert gl.list_pending("t1") == []


@pytest.mark.unit
def test_direct_propose_of_sms_without_consent_is_denied():
    gl = Greenlight(channel_resolver=_sms_resolver)
    rec = _propose(gl, action="send_sms",
                   payload={"to": "+15125550100", "body": "hi", "local_hour": 12})
    assert rec["status"] == "denied"
    assert "consent" in rec["deny_message"]


@pytest.mark.unit
def test_compliant_direct_propose_is_unaffected():
    gl = Greenlight()
    rec = _propose(gl, payload=COMPLIANT_EMAIL)
    assert rec["status"] == "pending"
    assert not rec.get("deny_message")
    assert len(gl.list_pending("t1")) == 1
    # Channel-free side-effecting actions (no email/sms rules apply) stay pending too.
    deal = _propose(gl, action="update_deal",
                    payload={"deal_id": "d1", "changes": {"stage": "proposal"}})
    assert deal["status"] == "pending"


@pytest.mark.unit
def test_unknown_action_fails_closed():
    # tool_meta's contract: reject unknown tools, never default-allow. An action the trusted
    # registry can't classify must not become an executable draft.
    gl = Greenlight()
    rec = _propose(gl, action="exfiltrate_db", payload={"target": "everything"})
    assert rec["status"] == "denied"
    assert "trusted tool registry" in rec["deny_message"]
    assert gl.list_pending("t1") == []


@pytest.mark.unit
def test_payload_cannot_smuggle_its_own_classification():
    # Forged channel/side_effecting keys in the PAYLOAD must not route around the floor —
    # classification comes only from the registry keyed by the trusted action name.
    gl = Greenlight()
    rec = _propose(gl, payload={**SPAMMY_EMAIL, "channel": None, "side_effecting": False})
    assert rec["status"] == "denied"
    assert "CAN-SPAM" in rec["deny_message"]


@pytest.mark.unit
def test_denied_proposal_maps_to_ma_deny_with_the_reason():
    # The worker surfaces the record via to_ma_confirmation: a compliance denial becomes a
    # tool_confirmation deny carrying the curated reason — the agent learns WHY, nothing sends.
    gl = Greenlight()
    rec = _propose(gl, payload=SPAMMY_EMAIL)
    ev = gl.to_ma_confirmation(rec, tool_use_id="tu_1")
    assert ev["result"] == "deny"
    assert "CAN-SPAM" in ev["deny_message"]


# ---------------------------------------------------------------------------
# The gate path: identical behavior for compliant actions, no double-report.
# ---------------------------------------------------------------------------

class SpyExecutor:
    def __init__(self):
        self.calls = []

    def __call__(self, action):
        self.calls.append(action)
        return {"sent": True}


def _ctx(gl, ex):
    return GateContext(
        tenant_id="t1",
        autonomy_config=AutonomyConfig(default_level=Level.L1),
        executor=ex,
        greenlight=gl,
        killswitch=KillSwitch(),
        trace_store=InMemoryTraceStore(),
    )


@pytest.mark.unit
def test_gate_block_happens_before_propose_so_nothing_is_double_reported():
    gl = Greenlight()
    ex = SpyExecutor()
    bad = Action(name="send_email", side_effecting=True, channel="email",
                 payload=dict(SPAMMY_EMAIL))
    res = ActionGate().run(bad, _ctx(gl, ex))
    assert res.status == "blocked"
    assert ex.calls == []
    # No row AT ALL — not pending, not denied: the gate blocked before propose, so the queue
    # never sees a second copy of the same compliance verdict.
    assert gl.store._rows == {}


@pytest.mark.unit
def test_gate_path_still_pends_compliant_actions_end_to_end():
    gl = Greenlight()
    ex = SpyExecutor()
    good = Action(name="send_email", side_effecting=True, channel="email",
                  payload=dict(COMPLIANT_EMAIL))
    res = ActionGate().run(good, _ctx(gl, ex))
    assert res.status == "pending_approval"
    assert res.approval["status"] == "pending"
    assert ex.calls == []
    assert len(gl.store._rows) == 1  # exactly one row, exactly once


# ---------------------------------------------------------------------------
# decide(): post-edit re-validation — the snapshot that would EXECUTE is what
# must pass, and a violating edit leaves the approval pending (CAS intact).
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_edit_that_strips_the_unsubscribe_link_is_rejected_and_stays_pending():
    gl = Greenlight()
    rec = _propose(gl, payload=COMPLIANT_EMAIL)
    with pytest.raises(ComplianceViolation, match="CAN-SPAM"):
        gl.decide("t1", rec["id"], "edit", edits={"body": "BUY NOW"})
    row = gl.store.get("t1", rec["id"])
    assert row["status"] == "pending"  # raised BEFORE the status flip — still decidable
    assert row["proposed_action"]["body"] == COMPLIANT_EMAIL["body"]  # snapshot untouched
    # A compliant edit on the SAME approval still works (the CAS semantics are intact).
    out = gl.decide("t1", rec["id"], "edit", edits={"body": "trimmed — unsubscribe below"})
    assert out["status"] == "approved"
    assert out["proposed_action"]["body"] == "trimmed — unsubscribe below"


# ---------------------------------------------------------------------------
# The API decide->apply flow: the 422 contract, pending-on-rejection, and
# "the applier runs exactly the approved snapshot".
# ---------------------------------------------------------------------------

H = {"Authorization": "Bearer tokA"}


class FakeVerifier:
    def verify(self, token):
        return {"tokA": {"sub": "uA", "custom:tenant_id": "A", "email": "a@x.com"}}[token]


class SpyCrm:
    def __init__(self):
        self.calls = []

    def update_deal_fields(self, *, tenant_id, deal_id, changes):
        self.calls.append((tenant_id, deal_id, dict(changes)))
        return {"id": deal_id, "updated": dict(changes)}


def _app(crm=None, *, saved_views=None, view_patcher=None, view_synthesizer=None):
    gl = Greenlight()
    deps = ApiDeps(
        verifier=FakeVerifier(),
        greenlight=gl,
        saved_views=saved_views or SavedViews(),
        conversation_factory=lambda t: None,
        autonomy_config=AutonomyConfig(),
        executor=lambda a: None,
        crm=crm,
        view_patcher=view_patcher,
        view_synthesizer=view_synthesizer,
    )
    return TestClient(create_app(deps)), gl


@pytest.mark.unit
def test_route_violating_edit_is_422_with_the_fixed_message_and_approval_stays_pending():
    client, gl = _app()
    rec = _propose(gl, tenant="A", payload=COMPLIANT_EMAIL)
    r = client.post(f"/approvals/{rec['id']}/decide",
                    json={"decision": "edit", "edits": {"body": "BUY NOW"}}, headers=H)
    assert r.status_code == 422
    # Fixed prefix + the CURATED policy reason — never internal exception text.
    assert r.json()["detail"] == (
        "decision rejected by compliance: CAN-SPAM: missing unsubscribe mechanism"
    )
    row = gl.store.get("A", rec["id"])
    assert row["status"] == "pending"  # NOT consumed — re-decidable with a compliant edit
    assert row["applied_at"] is None and row["apply_result"] is None  # applier never ran


@pytest.mark.unit
def test_apply_runs_exactly_the_approved_snapshot():
    crm = SpyCrm()
    client, gl = _app(crm=crm)
    rec = _propose(gl, tenant="A", action="update_deal",
                   payload={"deal_id": "d-1", "changes": {"stage": "proposal"}})
    r = client.post(f"/approvals/{rec['id']}/decide",
                    json={"decision": "edit", "edits": {"changes": {"stage": "closed_won"}}},
                    headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "approved"
    assert body["apply_result"]["performed"] is True
    # The CRM saw the POST-EDIT approved snapshot — exactly once, exactly as approved.
    assert crm.calls == [("A", "d-1", {"stage": "closed_won"})]
    assert gl.store.get("A", rec["id"])["proposed_action"]["changes"] == {"stage": "closed_won"}


# ---------------------------------------------------------------------------
# 422-detail hygiene: the four /views//dashboards paths answer FIXED messages
# for unexpected errors; curated view_spec reasons still flow; internals never echo.
# ---------------------------------------------------------------------------

VALID_VIEW_SPEC = {"view_id": "v1", "title": "V1", "semantic_refs": ["Deals.count"],
                   "layout": [{"type": "kpi", "metric": "Deals.count"}], "version": 1}


@pytest.mark.unit
def test_save_view_unexpected_error_answers_the_fixed_422_message():
    client, _ = _app()
    # No view_id -> KeyError inside SavedViews.save (NOT a curated validation error).
    r = client.post("/views", json={"spec": {"title": "no id"}}, headers=H)
    assert r.status_code == 422
    assert r.json()["detail"] == "view spec failed validation"
    assert "view_id" not in r.text  # the KeyError repr is logged, never echoed


@pytest.mark.unit
def test_save_view_curated_validation_reason_still_flows():
    client, _ = _app()
    r = client.post("/views", json={"spec": {"view_id": "v1", "bogus": True}}, headers=H)
    assert r.status_code == 422
    assert r.json()["detail"].startswith("schema invalid")  # authored in shared/view_spec.py


@pytest.mark.unit
def test_refine_view_unexpected_error_answers_the_fixed_422_message():
    def exploding_patcher(spec, instruction):
        raise RuntimeError("dsn=postgresql://user:secretpw@db/crm")  # internal — must not echo

    client, _ = _app(view_patcher=exploding_patcher)
    assert client.post("/views", json={"spec": VALID_VIEW_SPEC}, headers=H).status_code == 200
    r = client.post("/views/v1/refine", json={"instruction": "make it a line chart"}, headers=H)
    assert r.status_code == 422
    assert r.json()["detail"] == "refined view spec failed validation"
    assert "secretpw" not in r.text


@pytest.mark.unit
def test_save_view_draft_unexpected_error_answers_the_fixed_422_message():
    class BoomSynth:
        def save_draft(self, tenant_id, draft_id, created_by=""):
            raise RuntimeError("dsn=postgresql://user:secretpw@db/crm")

    client, _ = _app(view_synthesizer=BoomSynth())
    r = client.post("/views/drafts/dr-1/save", headers=H)
    assert r.status_code == 422
    assert r.json()["detail"] == "draft view spec failed validation"
    assert "secretpw" not in r.text


@pytest.mark.unit
def test_save_dashboard_unexpected_error_answers_the_fixed_422_message():
    client, _ = _app()
    # kind passes the explicit discriminator check; the missing view_id then KeyErrors in save.
    r = client.post("/dashboards", json={"spec": {"kind": "dashboard"}}, headers=H)
    assert r.status_code == 422
    assert r.json()["detail"] == "dashboard spec failed validation"
    assert "view_id" not in r.text


@pytest.mark.unit
def test_save_dashboard_curated_validation_reason_still_flows():
    client, _ = _app()
    dash = {"kind": "dashboard", "view_id": "d1", "title": "D", "spec_version": 2,
            "items": [{"view_id": "missing-view"}]}
    r = client.post("/dashboards", json={"spec": dash}, headers=H)
    assert r.status_code == 422
    assert "unknown view" in r.json()["detail"]  # authored in api/views.py — client-actionable
