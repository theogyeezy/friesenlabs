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
        self.sessions = 0

    def create_customer(self, email, idempotency_key):
        return {"id": "cus_1"}

    def create_checkout_session(self, **kw):
        self.sessions += 1
        self.event["data"]["object"]["client_reference_id"] = kw["client_reference_id"]
        return {"id": "cs_1", "url": "https://checkout.stripe.com/c/pay/cs_1"}

    def construct_event(self, payload, sig, secret):
        if sig != "good":
            raise ValueError("bad signature")
        return self.event


def _client(verify_redirect_url="", internal_bypass_domains=frozenset()):
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
        verify_redirect_url=verify_redirect_url,
        internal_bypass_domains=internal_bypass_domains,
    )
    deps = ApiDeps(verifier=object(), greenlight=Greenlight(), saved_views=SavedViews(),
                   conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
                   executor=lambda a: None, signup=signup)
    return TestClient(create_app(deps)), provisioned, stripe


@pytest.mark.integration
def test_cannot_checkout_before_verified():
    client, _, _stripe = _client()
    client.post("/signup", json={"email": "u@x.com", "phone": "+1555"})
    r = client.post("/signup/acct1/checkout", json={"plan": "pro"})
    assert r.status_code == 400  # verify before pay


@pytest.mark.integration
def test_get_verify_email_click_through_json_fallback():
    # No SPA base configured -> the safe fallback answers JSON (no redirect, no 500).
    client, _, _stripe = _client()
    client.post("/signup", json={"email": "u@x.com", "phone": "+1555"})
    r = client.get("/signup/acct1/verify-email", params={"token": "valid-email-token"})
    assert r.status_code == 200
    assert r.json()["email_verified"] is True

    # A bad token flips nothing — same 200 shape, no oracle.
    client2, _, _stripe2 = _client()
    client2.post("/signup", json={"email": "u@x.com", "phone": "+1555"})
    r2 = client2.get("/signup/acct1/verify-email", params={"token": "wrong"})
    assert r2.status_code == 200 and r2.json()["email_verified"] is False


@pytest.mark.integration
def test_get_verify_email_303_redirects_to_spa():
    client, _, _stripe = _client(verify_redirect_url="https://app.example/verify")
    client.post("/signup", json={"email": "u@x.com", "phone": "+1555"})
    r = client.get("/signup/acct1/verify-email", params={"token": "valid-email-token"},
                   follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "https://app.example/verify?account_id=acct1&email_verified=1"
    # The outcome flag is honest: a bad token redirects with email_verified=0.
    client2, _, _stripe2 = _client(verify_redirect_url="https://app.example/verify")
    client2.post("/signup", json={"email": "u@x.com", "phone": "+1555"})
    r2 = client2.get("/signup/acct1/verify-email", params={"token": "wrong"},
                     follow_redirects=False)
    assert r2.status_code == 303 and r2.headers["location"].endswith("email_verified=0")
    # Unknown account stays a 404, never a redirect.
    assert client.get("/signup/nope/verify-email", params={"token": "x"},
                      follow_redirects=False).status_code == 404


@pytest.mark.integration
def test_full_signup_to_checkout_then_webhook_provisions_once():
    client, provisioned, stripe = _client()
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


def _verified(client):
    client.post("/signup", json={"email": "u@friesenlabs.com", "phone": "+1555"})
    client.post("/signup/acct1/verify-email", json={"token": "valid-email-token"})
    client.post("/signup/acct1/verify-phone", json={"code": "123456"})


@pytest.mark.integration
def test_checkout_returns_the_hosted_checkout_url():
    # The revenue-path fix: the Stripe-hosted URL is RETURNED to the client (it used to be
    # discarded, leaving the SPA to fake payment success).
    client, provisioned, _stripe = _client()
    _verified(client)
    body = client.post("/signup/acct1/checkout", json={"plan": "pro"}).json()
    assert body["checkout_url"] == "https://checkout.stripe.com/c/pay/cs_1"
    assert body["checkout_id"] == "cs_1"
    assert body["stripe_customer_id"] == "cus_1"
    assert provisioned == []   # returning the URL changes nothing about the trigger


# ---------------- the env-gated internal bypass (SIGNUP_INTERNAL_BYPASS_DOMAINS) ----------------
@pytest.mark.integration
def test_bypass_off_by_default_even_for_friesenlabs_emails():
    # Default = empty set = the feature does not exist: a friesenlabs.com signup goes through
    # the normal Stripe checkout like anyone else.
    client, provisioned, stripe = _client()   # no internal_bypass_domains
    _verified(client)
    body = client.post("/signup/acct1/checkout", json={"plan": "pro"}).json()
    assert body["checkout_url"] == "https://checkout.stripe.com/c/pay/cs_1"
    assert "bypass" not in body
    assert stripe.sessions == 1 and provisioned == []


@pytest.mark.integration
def test_bypass_on_with_domain_settles_without_stripe_and_provisions():
    client, provisioned, stripe = _client(internal_bypass_domains=frozenset({"friesenlabs.com"}))
    _verified(client)
    body = client.post("/signup/acct1/checkout", json={"plan": "pro"}).json()
    assert body["bypass"] == "internal_comp" and body["checkout_url"] is None
    assert body["internal_comp"] is True
    assert stripe.sessions == 0               # NO Stripe call was made
    assert provisioned == ["acct1"]           # settled through the SAME on_paid path
    assert client.get("/signup/acct1").json()["state"] == "active"


@pytest.mark.integration
def test_bypass_double_fire_is_idempotent():
    client, provisioned, stripe = _client(internal_bypass_domains=frozenset({"friesenlabs.com"}))
    _verified(client)
    first = client.post("/signup/acct1/checkout", json={"plan": "pro"}).json()
    second = client.post("/signup/acct1/checkout", json={"plan": "pro"}).json()
    assert first["handled"] and second["idempotent"] is True
    assert provisioned == ["acct1"]           # exactly one provision
    assert stripe.sessions == 0


@pytest.mark.integration
def test_bypass_domain_must_match_exactly():
    # An enabled bypass for friesenlabs.com must not catch other domains (or tricks like
    # evil-friesenlabs.com / friesenlabs.com.evil.io — the stored domain is an exact-match key).
    client, provisioned, stripe = _client(internal_bypass_domains=frozenset({"friesenlabs.com"}))
    client.post("/signup", json={"email": "u@evil-friesenlabs.com", "phone": "+1555"})
    client.post("/signup/acct1/verify-email", json={"token": "valid-email-token"})
    client.post("/signup/acct1/verify-phone", json={"code": "123456"})
    body = client.post("/signup/acct1/checkout", json={"plan": "pro"}).json()
    assert "bypass" not in body and stripe.sessions == 1 and provisioned == []


@pytest.mark.integration
def test_bypass_still_requires_verification():
    # VERIFY BEFORE PAY holds on the bypass path too.
    client, provisioned, stripe = _client(internal_bypass_domains=frozenset({"friesenlabs.com"}))
    client.post("/signup", json={"email": "u@friesenlabs.com", "phone": "+1555"})
    r = client.post("/signup/acct1/checkout", json={"plan": "pro"})
    assert r.status_code == 400
    assert provisioned == [] and stripe.sessions == 0
