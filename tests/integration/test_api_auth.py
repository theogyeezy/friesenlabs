"""Integration: THE TRUST RULE at the HTTP layer — tenant only from the verified JWT claim."""
import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.views import SavedViews


class FakeVerifier:
    """Maps an opaque token string to its claims (stands in for Cognito JWKS verification)."""

    def __init__(self, tokens: dict):
        self.tokens = tokens

    def verify(self, token: str) -> dict:
        if token not in self.tokens:
            raise ValueError("bad token")
        return self.tokens[token]


def _client():
    verifier = FakeVerifier({
        "tokA": {"sub": "uA", "custom:tenant_id": "A", "email": "a@x.com"},
        "tokB": {"sub": "uB", "custom:tenant_id": "B", "email": "b@x.com"},
        "tokNoTenant": {"sub": "uC"},  # signature ok but no tenant claim
    })
    gl = Greenlight()
    deps = ApiDeps(
        verifier=verifier, greenlight=gl, saved_views=SavedViews(),
        conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
        executor=lambda a: {"ran": True},
    )
    return TestClient(create_app(deps)), gl


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.mark.integration
def test_no_token_401():
    client, _ = _client()
    assert client.get("/approvals").status_code == 401


@pytest.mark.integration
def test_invalid_token_401():
    client, _ = _client()
    assert client.get("/approvals", headers=_auth("garbage")).status_code == 401


@pytest.mark.integration
def test_token_without_tenant_claim_401():
    client, _ = _client()
    assert client.get("/approvals", headers=_auth("tokNoTenant")).status_code == 401


@pytest.mark.integration
def test_healthz_open():
    client, _ = _client()
    assert client.get("/healthz").status_code == 200


@pytest.mark.integration
def test_tenant_comes_from_claim_not_body():
    client, gl = _client()
    # Tenant A saves a view; the body has NO tenant_id (and any attempt to add one is ignored).
    spec = {"view_id": "v1", "title": "A view", "semantic_refs": ["Deals.count"],
            "layout": [{"type": "kpi", "metric": "Deals.count"}]}
    r = client.post("/views", json={"spec": spec, "tenant_id": "B"}, headers=_auth("tokA"))
    assert r.status_code == 200
    # It was saved under A (from the claim), not B (from the body).
    assert client.get("/views/v1", headers=_auth("tokA")).status_code == 200
    assert client.get("/views/v1", headers=_auth("tokB")).status_code == 404


@pytest.mark.integration
def test_two_tenant_approval_isolation():
    client, gl = _client()
    # Seed a pending approval for each tenant directly via the queue.
    gl.propose(tenant_id="A", action="send_email", agent="nadia", reasoning="r", value_at_stake=1, payload={})
    gl.propose(tenant_id="B", action="send_email", agent="nadia", reasoning="r", value_at_stake=1, payload={})
    a = client.get("/approvals", headers=_auth("tokA")).json()["approvals"]
    b = client.get("/approvals", headers=_auth("tokB")).json()["approvals"]
    assert len(a) == 1 and a[0]["tenant_id"] == "A"
    assert len(b) == 1 and b[0]["tenant_id"] == "B"
    # Tenant B cannot decide tenant A's approval (404, tenant-scoped).
    a_id = a[0]["id"]
    assert client.post(f"/approvals/{a_id}/decide", json={"decision": "approve"}, headers=_auth("tokB")).status_code == 404
