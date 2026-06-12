"""Integration: Greenlight route hardening (customer-readiness audit P0/P1).

GET /approvals pages with limit+cursor and reports total_pending; deciding an expired approval
is an honest 400 with no write; /chat refuses while the kill switch is engaged (the pause gates
chat-driven agent activity on EVERY runtime, at the API boundary); record-only approvals are
logged so a draft never silently reads as a real send.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.control.killswitch import KillSwitch
from api.views import SavedViews

H_A = {"Authorization": "Bearer tokA"}
DEAL_ID = "11111111-1111-1111-1111-111111111111"


class FakeVerifier:
    def verify(self, token):
        return {
            "tokA": {"sub": "uA", "custom:tenant_id": "A", "email": "a@x.com"},
        }[token]


class SpyCrm:
    def __init__(self):
        self.calls: list[tuple] = []

    def update_deal_fields(self, *, tenant_id, deal_id, changes):
        self.calls.append(("update_deal_fields", tenant_id, deal_id, dict(changes)))
        return {"id": deal_id, "updated": dict(changes)}


def _client(crm=None, *, killswitch=None, ttl_hours=None):
    gl = Greenlight(ttl_hours=ttl_hours) if ttl_hours is not None else Greenlight()
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


def _propose(gl, *, action="update_deal", body="draft"):
    return gl.propose(
        tenant_id="A", action=action, agent="ledger", reasoning="queued by test",
        value_at_stake=100,
        payload={"deal_id": DEAL_ID, "changes": {"stage": "closed_won"}, "body": body,
                               "has_unsubscribe": True},  # compliance floor: email needs opt-out
    )


# ------------------------------------------------------------------ pagination

@pytest.mark.integration
def test_list_approvals_pages_with_limit_cursor_and_total():
    client, gl = _client()
    ids = [str(_propose(gl, body=f"d{i}")["id"]) for i in range(5)]

    seen: list[str] = []
    r = client.get("/approvals", params={"limit": 2}, headers=H_A).json()
    assert r["total_pending"] == 5
    seen += [str(a["id"]) for a in r["approvals"]]
    while r["cursor"]:
        r = client.get("/approvals", params={"limit": 2, "cursor": r["cursor"]}, headers=H_A).json()
        seen += [str(a["id"]) for a in r["approvals"]]
    assert seen == ids


@pytest.mark.integration
def test_list_approvals_default_shape_keeps_approvals_key():
    client, gl = _client()
    _propose(gl)
    r = client.get("/approvals", headers=H_A)
    assert r.status_code == 200
    body = r.json()
    assert len(body["approvals"]) == 1
    assert body["cursor"] is None
    assert body["total_pending"] == 1


@pytest.mark.integration
def test_list_approvals_invalid_cursor_is_422():
    client, gl = _client()
    _propose(gl)
    r = client.get("/approvals", params={"cursor": "junk"}, headers=H_A)
    assert r.status_code == 422


# ------------------------------------------------------------------ expiry at the route

@pytest.mark.integration
def test_decide_expired_approval_is_400_and_never_writes():
    crm = SpyCrm()
    client, gl = _client(crm, ttl_hours=1)
    rec = _propose(gl)
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert gl.store.update("A", rec["id"], {"expires_at": past}) == 1

    r = client.post(f"/approvals/{rec['id']}/decide", json={"decision": "approve"}, headers=H_A)

    assert r.status_code == 400
    assert "expired" in r.json()["detail"]
    assert crm.calls == []
    assert gl.store.get("A", rec["id"])["status"] == "expired"


# ------------------------------------------------------------------ kill switch gates /chat

@pytest.mark.integration
def test_chat_refuses_409_while_killswitch_engaged():
    ks = KillSwitch()
    ks.pause_tenant("A")
    client, _ = _client(killswitch=ks)
    r = client.post("/chat", json={"message": "hi"}, headers=H_A)
    assert r.status_code == 409
    assert "kill switch" in r.json()["detail"]


@pytest.mark.integration
def test_chat_passes_the_killswitch_gate_when_released():
    # Factory returns None here, so a released switch falls through to the honest 503 —
    # proving the 409 above came from the pause, not from the unconfigured backend.
    client, _ = _client(killswitch=KillSwitch())
    r = client.post("/chat", json={"message": "hi"}, headers=H_A)
    assert r.status_code == 503


# ------------------------------------------------------------------ record-only honesty

@pytest.mark.integration
def test_record_only_approval_is_logged_as_draft_only(caplog):
    client, gl = _client()
    rec = _propose(gl, action="send_email")
    with caplog.at_level("INFO", logger="api.app"):
        r = client.post(f"/approvals/{rec['id']}/decide", json={"decision": "approve"}, headers=H_A)
    assert r.status_code == 200
    assert r.json()["apply_result"]["performed"] is False
    assert any("draft-only" in m for m in caplog.messages)


# ------------------------------------------------------------------ applier audit seam

@pytest.mark.integration
def test_apply_result_carries_approval_id_and_decided_by():
    crm = SpyCrm()
    client, gl = _client(crm)
    rec = _propose(gl)
    r = client.post(f"/approvals/{rec['id']}/decide", json={"decision": "approve"}, headers=H_A)
    assert r.status_code == 200
    ar = r.json()["apply_result"]
    assert ar["approval_id"] == str(rec["id"])
    assert ar["decided_by"] == "uA"
