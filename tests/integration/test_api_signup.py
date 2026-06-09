"""Integration: signup + Stripe webhook routes at the HTTP layer.

Proves: verify-before-pay (checkout 400 until verified), the webhook is the ONLY provisioning trigger,
a bad signature is rejected, and a re-delivered webhook does not double-provision.
"""
import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.signup_routes import SignupDeps
from api.views import SavedViews
from signup.accounts import AccountService
from signup.payment import PaymentService

# Reuse the fakes from the provisioning unit test.
from tests.unit.test_signup_provisioning import (
    AnthropicAdmin, Cognito, DB, Email, Recorder, Secrets, Store,
)
from signup.provisioning import Provisioner


class Stripe:
    def __init__(self):
        self.event = {"type": "checkout.session.completed",
                      "data": {"object": {"client_reference_id": None}}}

    def create_customer(self, email, idempotency_key):
        return {"id": "cus_1"}

    def create_checkout_session(self, **kw):
        self.event["data"]["object"]["client_reference_id"] = kw["client_reference_id"]
        return {"id": "cs_1"}

    def construct_event(self, payload, sig, secret):
        if sig != "good":
            raise ValueError("bad signature")
        return self.event


def _client():
    store = Store()
    accounts = AccountService(store, Cognito(), Email(), Recorder())
    provisioned = []
    prov = Provisioner(store=store, mint_tenant_id=lambda aid: f"tenant-{aid}", db=DB(),
                       anthropic_admin=AnthropicAdmin(), secrets=Secrets(), cognito=Cognito(),
                       cube=Recorder(), resend=Recorder(), agent_plane=Recorder())

    def on_paid(acct):
        provisioned.append(acct.id)
        prov.provision(acct)

    stripe = Stripe()
    payment = PaymentService(stripe, accounts, on_paid=on_paid)
    signup = SignupDeps(
        accounts=accounts, payment=payment, stripe_webhook_secret="whsec",
        new_account_id=lambda: "acct1",
        email_token_ok=lambda aid, t: t == "valid-email-token",
        sms_code_ok=lambda aid, c: c == "123456",
    )
    deps = ApiDeps(verifier=object(), greenlight=Greenlight(), saved_views=SavedViews(),
                   conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
                   executor=lambda a: None, signup=signup)
    return TestClient(create_app(deps)), provisioned


@pytest.mark.integration
def test_cannot_checkout_before_verified():
    client, _ = _client()
    client.post("/signup", json={"email": "u@x.com", "phone": "+1555"})
    r = client.post("/signup/acct1/checkout", json={"plan": "pro"})
    assert r.status_code == 400  # verify before pay


@pytest.mark.integration
def test_full_signup_to_checkout_then_webhook_provisions_once():
    client, provisioned = _client()
    assert client.post("/signup", json={"email": "u@x.com", "phone": "+1555"}).json()["state"] == "created"
    client.post("/signup/acct1/verify-email", json={"token": "valid-email-token"})
    client.post("/signup/acct1/verify-phone", json={"code": "123456"})
    assert client.post("/signup/acct1/checkout", json={"plan": "pro"}).json()["checkout_id"] == "cs_1"
    # Provisioning has NOT happened yet (only the webhook triggers it).
    assert provisioned == []

    # Bad signature is rejected and provisions nothing.
    assert client.post("/webhooks/stripe", content=b"{}", headers={"stripe-signature": "bad"}).status_code == 400
    assert provisioned == []

    # The signed webhook provisions exactly once; re-delivery is idempotent.
    assert client.post("/webhooks/stripe", content=b"{}", headers={"stripe-signature": "good"}).status_code == 200
    client.post("/webhooks/stripe", content=b"{}", headers={"stripe-signature": "good"})
    assert provisioned == ["acct1"]
    assert client.get("/signup/acct1").json()["state"] == "active"
