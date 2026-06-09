"""Integration: the production ASGI app boots and mounts the signup/webhook routes (H6)."""
import pytest
from fastapi.testclient import TestClient

import api.asgi as asgi


@pytest.fixture(scope="module")
def client():
    return TestClient(asgi.app)


@pytest.mark.integration
def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


@pytest.mark.integration
def test_signup_and_webhook_routes_are_mounted(client):
    paths = {r.path for r in asgi.app.routes}
    assert "/webhooks/stripe" in paths
    assert "/signup" in paths
    assert "/signup/{account_id}/checkout" in paths


@pytest.mark.integration
def test_signup_create_works_in_memory(client):
    # Account creation works with the in-memory store + stubbed Cognito/email (no live creds needed).
    r = client.post("/signup", json={"email": "founder@acme.com", "phone": "+15555550100"})
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "created" and body["account_id"]


@pytest.mark.integration
def test_authed_routes_reject_without_token(client):
    # The default (no Cognito configured) verifier rejects everything → 401, not a crash.
    assert client.get("/approvals").status_code == 401
