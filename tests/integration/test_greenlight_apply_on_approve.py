"""Integration: approving Greenlight proposals applies CRM actions exactly once.

The propose path remains draft-only. These tests start at an already-queued approval and prove the
decide endpoint applies only approved CRM proposals, under the approval row's tenant, and records
honest apply_result state for success, record-only actions, and failures.
"""
import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight, InMemoryApprovalStore
from api.control.killswitch import KillSwitch
from api.views import SavedViews

H_A = {"Authorization": "Bearer tokA"}
H_B = {"Authorization": "Bearer tokB"}
DEAL_ID = "11111111-1111-1111-1111-111111111111"
CONTACT_ID = "22222222-2222-2222-2222-222222222222"


class FakeVerifier:
    def verify(self, token):
        return {
            "tokA": {"sub": "uA", "custom:tenant_id": "A", "email": "a@x.com"},
            "tokB": {"sub": "uB", "custom:tenant_id": "B", "email": "b@x.com"},
        }[token]


class SpyCrm:
    def __init__(self, *, fail: Exception | None = None):
        self.fail = fail
        self.calls: list[tuple] = []

    def update_deal_fields(self, *, tenant_id, deal_id, changes):
        if self.fail is not None:
            self.calls.append(("update_deal_fields", tenant_id, deal_id, dict(changes)))
            raise self.fail
        bad = [k for k in changes if k not in {"stage", "amount", "name"}]
        if bad:
            raise ValueError(f"change field {bad[0]!r} is not allow-listed")
        self.calls.append(("update_deal_fields", tenant_id, deal_id, dict(changes)))
        return {"id": deal_id, "updated": dict(changes)}

    def update_contact_fields(self, *, tenant_id, contact_id, changes):
        self.calls.append(("update_contact_fields", tenant_id, contact_id, dict(changes)))
        return {"id": contact_id, "updated": dict(changes), "skipped": {}}


class ExplodingCrm:
    def __getattr__(self, name):
        raise AssertionError(f"CRM should not be touched for record-only action {name}")


def _client(crm=None, *, killswitch=None, store=None):
    gl = Greenlight(store=store)
    deps = ApiDeps(
        verifier=FakeVerifier(),
        greenlight=gl,
        saved_views=SavedViews(),
        conversation_factory=lambda t: None,
        autonomy_config=AutonomyConfig(),
        executor=lambda a: None,
        crm=crm,
        killswitch=killswitch or KillSwitch(),
    )
    return TestClient(create_app(deps)), gl


def _propose(gl, *, tenant="A", action="update_deal", payload=None):
    return gl.propose(
        tenant_id=tenant,
        action=action,
        agent="ledger",
        reasoning="queued by test",
        value_at_stake=100,
        payload=payload or {"deal_id": DEAL_ID, "changes": {"stage": "closed_won"}},
    )


@pytest.mark.integration
def test_approve_update_deal_applies_once_with_claims_tenant():
    crm = SpyCrm()
    client, gl = _client(crm)
    rec = _propose(gl, payload={"deal_id": DEAL_ID, "changes": {"stage": "closed_won"}})

    r = client.post(f"/approvals/{rec['id']}/decide", json={"decision": "approve"}, headers=H_A)

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "approved"
    assert body["applied_at"] is not None
    assert body["apply_result"]["performed"] is True
    assert crm.calls == [
        ("update_deal_fields", "A", DEAL_ID, {"stage": "closed_won"}),
    ]


@pytest.mark.integration
def test_deny_never_runs_applier():
    crm = SpyCrm()
    client, gl = _client(crm)
    rec = _propose(gl)

    r = client.post(
        f"/approvals/{rec['id']}/decide",
        json={"decision": "deny", "deny_message": "not now"},
        headers=H_A,
    )

    assert r.status_code == 200
    assert r.json()["status"] == "denied"
    assert crm.calls == []
    assert gl.store.get("A", rec["id"])["apply_result"] is None


@pytest.mark.integration
def test_cross_tenant_approval_id_is_404_and_no_write():
    crm = SpyCrm()
    client, gl = _client(crm)
    rec = _propose(gl, tenant="A")

    r = client.post(f"/approvals/{rec['id']}/decide", json={"decision": "approve"}, headers=H_B)

    assert r.status_code == 404
    assert crm.calls == []
    assert gl.store.get("A", rec["id"])["status"] == "pending"


@pytest.mark.integration
def test_applier_exception_is_recorded_without_500_or_retry_write():
    crm = SpyCrm(fail=RuntimeError("database detail must not leak"))
    client, gl = _client(crm)
    rec = _propose(gl)

    r = client.post(f"/approvals/{rec['id']}/decide", json={"decision": "approve"}, headers=H_A)

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "approved"
    assert body["applied_at"] is None
    assert body["apply_result"] == {"performed": False, "error": "RuntimeError"}
    assert "database detail" not in r.text
    assert crm.calls == [("update_deal_fields", "A", DEAL_ID, {"stage": "closed_won"})]

    again = client.post(f"/approvals/{rec['id']}/decide", json={"decision": "approve"}, headers=H_A)
    assert again.status_code == 400
    assert crm.calls == [("update_deal_fields", "A", DEAL_ID, {"stage": "closed_won"})]


@pytest.mark.integration
def test_send_email_approval_is_record_only_and_touches_no_sender_or_crm():
    client, gl = _client(ExplodingCrm())
    rec = _propose(
        gl,
        action="send_email",
        payload={"to": "x@y.com", "subject": "s", "body": "hi unsubscribe"},
    )

    r = client.post(f"/approvals/{rec['id']}/decide", json={"decision": "approve"}, headers=H_A)

    assert r.status_code == 200
    assert r.json()["apply_result"] == {
        "performed": False,
        "reason": "draft-only until provider go-live",
        # The audit linkage stamped by apply_approved_action (approval row + approving human).
        "approval_id": str(rec["id"]),
        "decided_by": "uA",
    }
    # A record-only (draft-only) send performed NOTHING, so it carries no applied_at — it must
    # never read as "sent at <time>" (same honest shape as the applier-error path).
    assert r.json()["applied_at"] is None


@pytest.mark.integration
def test_forbidden_update_field_records_error_and_no_partial_write():
    crm = SpyCrm()
    client, gl = _client(crm)
    rec = _propose(
        gl,
        payload={"deal_id": DEAL_ID, "changes": {"stage": "closed_won", "owner_id": "evil"}},
    )

    r = client.post(f"/approvals/{rec['id']}/decide", json={"decision": "approve"}, headers=H_A)

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "approved"
    assert body["apply_result"] == {"performed": False, "error": "ValueError"}
    assert body["applied_at"] is None
    assert crm.calls == []


@pytest.mark.integration
def test_approve_update_contact_dispatches_registered_applier():
    crm = SpyCrm()
    client, gl = _client(crm)
    rec = _propose(
        gl,
        action="update_contact",
        payload={"contact_id": CONTACT_ID, "changes": {"email": "new@example.com"}},
    )

    r = client.post(f"/approvals/{rec['id']}/decide", json={"decision": "approve"}, headers=H_A)

    assert r.status_code == 200
    assert r.json()["apply_result"]["performed"] is True
    assert crm.calls == [
        ("update_contact_fields", "A", CONTACT_ID, {"email": "new@example.com"}),
    ]


# ---------------------------------------------------------------------------
# M1 — TOCTOU double-apply: the pending->decided transition is atomic.
# ---------------------------------------------------------------------------

class StaleReadStore(InMemoryApprovalStore):
    """Simulates the TOCTOU window: the loser's reads see a stale 'pending' snapshot while the
    conditional check-and-set (the real arbiter) returns 0 — exactly the concurrent-decider race."""

    def __init__(self):
        super().__init__()
        self.stale_pending_reads = 0

    def get(self, tenant_id, approval_id):
        row = super().get(tenant_id, approval_id)
        if row is not None and self.stale_pending_reads > 0:
            self.stale_pending_reads -= 1
            return {**row, "status": "pending"}
        return row


@pytest.mark.integration
def test_concurrent_decides_run_the_applier_exactly_once():
    crm = SpyCrm()
    store = StaleReadStore()
    client, gl = _client(crm, store=store)
    rec = _propose(gl, payload={"deal_id": DEAL_ID, "changes": {"stage": "closed_won"}})

    winner = client.post(f"/approvals/{rec['id']}/decide", json={"decision": "approve"}, headers=H_A)
    assert winner.status_code == 200
    assert winner.json()["apply_result"]["performed"] is True

    # The "concurrent" second decider: both of its pre-write reads still see pending (the stale
    # snapshot), so it reaches the conditional update — which returns 0. It must lose cleanly:
    # 400, and the applier NEVER runs for the loser.
    store.stale_pending_reads = 2  # the route-level read + decide()'s read
    loser = client.post(f"/approvals/{rec['id']}/decide", json={"decision": "approve"}, headers=H_A)

    assert loser.status_code == 400
    assert crm.calls == [("update_deal_fields", "A", DEAL_ID, {"stage": "closed_won"})]  # exactly once
    row = gl.store.get("A", rec["id"])
    assert row["status"] == "approved"
    assert row["apply_result"]["performed"] is True


# ---------------------------------------------------------------------------
# M2 — kill switch: consulted BEFORE the status flip; an engaged pause never
# consumes the approval and the applier never runs.
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_tenant_killswitch_blocks_approve_409_and_keeps_it_pending():
    crm = SpyCrm()
    ks = KillSwitch()
    ks.pause_tenant("A")
    client, gl = _client(crm, killswitch=ks)
    rec = _propose(gl)

    r = client.post(f"/approvals/{rec['id']}/decide", json={"decision": "approve"}, headers=H_A)

    assert r.status_code == 409
    assert "kill switch" in r.json()["detail"]
    assert crm.calls == []
    row = gl.store.get("A", rec["id"])
    assert row["status"] == "pending"  # NOT consumed — re-approvable after the pause lifts
    assert row["applied_at"] is None and row["apply_result"] is None

    # Disengage -> the SAME approval approves and applies normally.
    ks.resume_tenant("A")
    r2 = client.post(f"/approvals/{rec['id']}/decide", json={"decision": "approve"}, headers=H_A)
    assert r2.status_code == 200
    assert r2.json()["status"] == "approved"
    assert r2.json()["apply_result"]["performed"] is True
    assert crm.calls == [("update_deal_fields", "A", DEAL_ID, {"stage": "closed_won"})]


@pytest.mark.integration
def test_global_killswitch_blocks_edit_approve_too():
    crm = SpyCrm()
    ks = KillSwitch()
    ks.pause_global()
    client, gl = _client(crm, killswitch=ks)
    rec = _propose(gl)

    r = client.post(
        f"/approvals/{rec['id']}/decide",
        json={"decision": "edit", "edits": {"changes": {"stage": "closed_lost"}}},
        headers=H_A,
    )

    assert r.status_code == 409
    assert crm.calls == []
    assert gl.store.get("A", rec["id"])["status"] == "pending"


@pytest.mark.integration
def test_killswitch_does_not_block_deny():
    # Denying performs no side effect — a paused tenant can still stop a queued proposal.
    crm = SpyCrm()
    ks = KillSwitch()
    ks.pause_tenant("A")
    client, gl = _client(crm, killswitch=ks)
    rec = _propose(gl)

    r = client.post(
        f"/approvals/{rec['id']}/decide",
        json={"decision": "deny", "deny_message": "paused anyway"},
        headers=H_A,
    )

    assert r.status_code == 200
    assert r.json()["status"] == "denied"
    assert crm.calls == []


# ---------------------------------------------------------------------------
# M3 — audit honesty: an audit-write failure AFTER a successful apply must not
# rewrite the outcome to performed: false — the CRM write happened.
# ---------------------------------------------------------------------------

class AuditFailStore(InMemoryApprovalStore):
    """The post-apply audit write (the update carrying applied_at) raises on its first attempt."""

    def __init__(self):
        super().__init__()
        self.audit_failures = 1

    def update(self, tenant_id, approval_id, changes, *, expected_status=None):
        if "applied_at" in changes and self.audit_failures > 0:
            self.audit_failures -= 1
            raise RuntimeError("audit db blip")
        return super().update(tenant_id, approval_id, changes, expected_status=expected_status)


@pytest.mark.integration
def test_audit_write_failure_after_successful_apply_still_reports_the_applied_write():
    crm = SpyCrm()
    store = AuditFailStore()
    client, gl = _client(crm, store=store)
    rec = _propose(gl, payload={"deal_id": DEAL_ID, "changes": {"stage": "closed_won"}})

    r = client.post(f"/approvals/{rec['id']}/decide", json={"decision": "approve"}, headers=H_A)

    assert r.status_code == 200
    body = r.json()
    # The response reflects the write that ACTUALLY happened — never performed: false.
    assert body["status"] == "approved"
    assert body["apply_result"]["performed"] is True
    assert body["applied_at"] is not None
    assert "warning" in body and "audit" in body["warning"]
    assert crm.calls == [("update_deal_fields", "A", DEAL_ID, {"stage": "closed_won"})]
    # And the store row was never rewritten with a dishonest failure record.
    row = gl.store.get("A", rec["id"])
    assert row["status"] == "approved"
    assert row["apply_result"] is None  # the audit write failed — absent, not falsified


# ---------------------------------------------------------------------------
# L1 — edit guard: edits may touch payload fields only; the 'action' key (or any
# novel key) is rejected with 422 and nothing is applied.
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_edit_changing_action_key_is_422_and_nothing_applied():
    client, gl = _client(ExplodingCrm())
    rec = _propose(
        gl,
        action="send_email",
        payload={"to": "x@y.com", "subject": "s", "body": "hi unsubscribe"},
    )

    r = client.post(
        f"/approvals/{rec['id']}/decide",
        json={"decision": "edit", "edits": {"action": "create_deal"}},
        headers=H_A,
    )

    assert r.status_code == 422
    assert "action" in r.json()["detail"]
    row = gl.store.get("A", rec["id"])
    assert row["status"] == "pending"  # guard fires BEFORE the status flip
    assert row["proposed_action"]["action"] == "send_email"
    assert row["applied_at"] is None and row["apply_result"] is None


@pytest.mark.integration
def test_edit_with_novel_key_is_422_but_payload_edit_still_works():
    crm = SpyCrm()
    client, gl = _client(crm)
    rec = _propose(gl, payload={"deal_id": DEAL_ID, "changes": {"stage": "proposal"}})

    bad = client.post(
        f"/approvals/{rec['id']}/decide",
        json={"decision": "edit", "edits": {"owner_override": "evil"}},
        headers=H_A,
    )
    assert bad.status_code == 422
    assert crm.calls == []
    assert gl.store.get("A", rec["id"])["status"] == "pending"

    good = client.post(
        f"/approvals/{rec['id']}/decide",
        json={"decision": "edit", "edits": {"changes": {"stage": "closed_won"}}},
        headers=H_A,
    )
    assert good.status_code == 200
    assert good.json()["apply_result"]["performed"] is True
    assert crm.calls == [("update_deal_fields", "A", DEAL_ID, {"stage": "closed_won"})]
