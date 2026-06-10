"""Integration: approving Greenlight proposals applies CRM actions exactly once.

The propose path remains draft-only. These tests start at an already-queued approval and prove the
decide endpoint applies only approved CRM proposals, under the approval row's tenant, and records
honest apply_result state for success, record-only actions, and failures.
"""
import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
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


def _client(crm=None):
    gl = Greenlight()
    deps = ApiDeps(
        verifier=FakeVerifier(),
        greenlight=gl,
        saved_views=SavedViews(),
        conversation_factory=lambda t: None,
        autonomy_config=AutonomyConfig(),
        executor=lambda a: None,
        crm=crm,
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
    }
    assert r.json()["applied_at"] is not None


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
