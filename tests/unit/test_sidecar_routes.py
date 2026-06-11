"""Unit: /sidecar/suggestions + /sidecar/act (api/sidecar_routes.py) over fakes (no DB/network)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.sidecar_routes import SidecarDeps, mount_sidecar
from api.auth import make_current_tenant

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)
OLD = NOW - timedelta(days=400)  # far in the past so rows look aging/stale regardless of "now"


class FakeVerifier:
    def verify(self, token):
        t = token.split("-")[1] if token.startswith("t-") else "A"
        return {"sub": f"sub-{t}", "custom:tenant_id": t, "email": f"{t}@x.com"}


class _Reject:
    def verify(self, token):
        raise ValueError("bad token")


class FakeCrm:
    """Returns rows tagged with the tenant so the route's isolation check passes."""
    def __init__(self, tenant="A", deals=None, contacts=None):
        self.t = tenant
        self._deals = deals if deals is not None else [
            {"id": "d1", "tenant_id": tenant, "title": "Acme", "stage": "new",
             "amount": 5000, "contact_id": None, "created_at": OLD},
        ]
        self._contacts = contacts if contacts is not None else [
            {"id": "c1", "tenant_id": tenant, "name": "Dana", "email": None,
             "phone": None, "last_activity_at": None},
        ]

    def list_deals_board(self, *, tenant_id):
        return [dict(r) for r in self._deals]

    def list_contacts_directory(self, *, tenant_id):
        return [dict(r) for r in self._contacts]


class FakeGreenlight:
    def __init__(self):
        self.calls = []

    def propose(self, *, tenant_id, action, agent, reasoning, value_at_stake, payload):
        self.calls.append({"tenant_id": tenant_id, "action": action, "agent": agent,
                           "payload": payload, "value_at_stake": value_at_stake})
        return {"id": "appr-1"}


H_A = {"Authorization": "Bearer t-A"}


def _client(*, crm=None, verifier=None, greenlight=None):
    app = FastAPI()
    gate = SimpleNamespace(greenlight=greenlight or FakeGreenlight())
    mount_sidecar(app, SidecarDeps(crm=crm), make_current_tenant(verifier or FakeVerifier()),
                  gate_deps=gate)
    return TestClient(app, raise_server_exceptions=False), gate


@pytest.mark.unit
def test_suggestions_503_when_unconfigured():
    c, _ = _client(crm=None)
    assert c.get("/sidecar/suggestions", headers=H_A).status_code == 503


@pytest.mark.unit
def test_suggestions_401_unauth():
    c, _ = _client(crm=FakeCrm(), verifier=_Reject())
    assert c.get("/sidecar/suggestions").status_code == 401


@pytest.mark.unit
def test_suggestions_returns_grounded_items():
    c, _ = _client(crm=FakeCrm())
    r = c.get("/sidecar/suggestions", headers=H_A)
    assert r.status_code == 200
    body = r.json()
    kinds = {s["kind"] for s in body["suggestions"]}
    assert "unlinked_deal" in kinds and "missing_contact_info" in kinds
    assert body["total"] >= 2


@pytest.mark.unit
def test_tenant_isolation_violation_is_500():
    # A row tagged with a different tenant than the claim must never leak.
    bad = FakeCrm(deals=[{"id": "d1", "tenant_id": "EVIL", "title": "x", "stage": "new",
                          "amount": 1, "contact_id": None, "created_at": OLD}], contacts=[])
    c, _ = _client(crm=bad)
    assert c.get("/sidecar/suggestions", headers=H_A).status_code == 500


@pytest.mark.unit
def test_act_enqueues_greenlight_draft():
    c, gate = _client(crm=FakeCrm())
    # First read to get a real id.
    sug = c.get("/sidecar/suggestions", headers=H_A).json()["suggestions"]
    sid = next(s["id"] for s in sug if s["kind"] == "unlinked_deal")
    r = c.post("/sidecar/act", headers=H_A, json={"id": sid})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued" and body["approval_id"] == "appr-1"
    call = gate.greenlight.calls[-1]
    assert call["tenant_id"] == "A" and call["action"] == "create_activity"
    assert call["agent"] == "sidecar"
    assert "action" not in call["payload"]  # the action name is lifted out, not duplicated in payload


@pytest.mark.unit
def test_act_tenant_from_claim_not_body():
    c, gate = _client(crm=FakeCrm())
    sid = c.get("/sidecar/suggestions", headers=H_A).json()["suggestions"][0]["id"]
    c.post("/sidecar/act", headers=H_A, json={"id": sid, "tenant_id": "EVIL"})
    assert gate.greenlight.calls[-1]["tenant_id"] == "A"


@pytest.mark.unit
def test_act_unknown_id_is_409():
    c, _ = _client(crm=FakeCrm())
    r = c.post("/sidecar/act", headers=H_A, json={"id": "nope:123"})
    assert r.status_code == 409


@pytest.mark.unit
def test_act_503_when_unconfigured():
    c, _ = _client(crm=None)
    assert c.post("/sidecar/act", headers=H_A, json={"id": "x"}).status_code == 503
