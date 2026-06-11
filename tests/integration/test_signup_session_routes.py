"""Integration: signup routes under the signed session-token contract (security fix).

Proves, at the HTTP layer:
  * the pre-auth GET /signup/{id} state endpoint NO LONGER returns tenant_id;
  * when session tokens are wired, POST /signup returns a `session_token`;
  * state + checkout accept the session token in the path (not just the raw account_id);
  * a FORGED / WRONG-SCOPE token in the path is rejected (404), never treated as a raw id;
  * the emailed verify-email click redirect carries a SCOPED session_token, not the raw
    account_id (the Referer/log-leak fix) — and a state-scoped token cannot start checkout.
"""
import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.signup_routes import SignupDeps
from api.views import SavedViews
from shared.signup_session import SignupSessionTokens
from signup.accounts import AccountService
from signup.payment import PaymentService

from tests.unit.test_signup_provisioning import (
    AnthropicAdmin, Cognito, DB, Email, Recorder, Secrets, Store,
)
from signup.provisioning import Provisioner

SECRET = "session-signing-secret-for-tests"


class Stripe:
    def __init__(self):
        self.event = {"type": "checkout.session.completed",
                      "data": {"object": {"client_reference_id": None}}}

    def create_customer(self, email, idempotency_key):
        return {"id": "cus_1"}

    def create_checkout_session(self, **kw):
        self.event["data"]["object"]["client_reference_id"] = kw["client_reference_id"]
        return {"id": "cs_1", "url": "https://checkout.stripe.com/c/pay/cs_1"}

    def construct_event(self, payload, sig, secret):
        if sig != "good":
            raise ValueError("bad signature")
        return self.event


def _client(*, verify_redirect_url="", with_tokens=True, account_id="acct1"):
    store = Store()
    accounts = AccountService(store, Cognito(), Email(), Recorder())
    prov = Provisioner(store=store, mint_tenant_id=lambda aid: f"tenant-{aid}", db=DB(),
                       anthropic_admin=AnthropicAdmin(), secrets=Secrets(), cognito=Cognito(),
                       cube=Recorder(), resend=Recorder(), agent_plane=Recorder())
    payment = PaymentService(Stripe(), accounts, on_paid=prov.provision)
    tokens = SignupSessionTokens(SECRET) if with_tokens else None
    signup = SignupDeps(
        accounts=accounts, payment=payment, stripe_webhook_secret="whsec",
        new_account_id=lambda: account_id,
        email_token_ok=lambda aid, t: t == "valid-email-token",
        sms_code_ok=lambda aid, c: c == "123456",
        verify_redirect_url=verify_redirect_url,
        session_tokens=tokens,
    )
    deps = ApiDeps(verifier=object(), greenlight=Greenlight(), saved_views=SavedViews(),
                   conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
                   executor=lambda a: None, signup=signup)
    return TestClient(create_app(deps)), tokens, store


def _verified(client, aid="acct1"):
    client.post("/signup", json={"email": "u@x.com", "phone": "+1555"})
    client.post(f"/signup/{aid}/verify-email", json={"token": "valid-email-token"})
    client.post(f"/signup/{aid}/verify-phone", json={"code": "123456"})


@pytest.mark.integration
def test_state_endpoint_no_longer_leaks_tenant_id():
    client, _t, _s = _client()
    client.post("/signup", json={"email": "u@x.com", "phone": "+1555"})
    body = client.get("/signup/acct1").json()
    assert body["account_id"] == "acct1"
    assert "tenant_id" not in body          # the pre-auth leak is closed


@pytest.mark.integration
def test_signup_returns_session_token_when_wired():
    client, tokens, _s = _client()
    body = client.post("/signup", json={"email": "u@x.com", "phone": "+1555"}).json()
    assert "session_token" in body
    # The minted token is checkout-scoped and bound to the new account.
    assert tokens.verify(body["session_token"], "checkout") == "acct1"


@pytest.mark.integration
def test_no_session_token_when_feature_off():
    client, _t, _s = _client(with_tokens=False)
    body = client.post("/signup", json={"email": "u@x.com", "phone": "+1555"}).json()
    assert "session_token" not in body      # byte-identical pre-rollout posture


@pytest.mark.integration
def test_state_and_checkout_accept_session_token_in_path():
    client, tokens, _s = _client()
    _verified(client)
    state_tok = tokens.mint("acct1", "state")
    # State read works through a state token.
    assert client.get(f"/signup/{state_tok}").json()["account_id"] == "acct1"
    # Checkout works through a checkout token.
    checkout_tok = tokens.mint("acct1", "checkout")
    r = client.post(f"/signup/{checkout_tok}/checkout", json={"plan": "pro"})
    assert r.status_code == 200 and r.json()["checkout_id"] == "cs_1"


@pytest.mark.integration
def test_checkout_rejects_state_scoped_token():
    client, tokens, _s = _client()
    _verified(client)
    state_tok = tokens.mint("acct1", "state")
    # A read-only state token must NOT be usable to start checkout (scope binding).
    r = client.post(f"/signup/{state_tok}/checkout", json={"plan": "pro"})
    assert r.status_code == 404


@pytest.mark.integration
def test_forged_token_in_path_is_rejected_not_treated_as_raw():
    client, _tokens, _s = _client()
    _verified(client)
    attacker = SignupSessionTokens("attacker-key")
    forged = attacker.mint("acct1", "checkout")
    assert client.post(f"/signup/{forged}/checkout", json={"plan": "pro"}).status_code == 404
    assert client.get(f"/signup/{forged}").status_code == 404


@pytest.mark.integration
def test_raw_account_id_still_works_during_rollout():
    # Backward-compatible: the legacy raw account_id path still resolves while web updates.
    client, _tokens, _s = _client()
    _verified(client)
    assert client.get("/signup/acct1").json()["account_id"] == "acct1"
    assert client.post("/signup/acct1/checkout", json={"plan": "pro"}).status_code == 200


@pytest.mark.integration
def test_verify_email_redirect_carries_session_token_not_account_id():
    client, tokens, _s = _client(verify_redirect_url="https://app.example.com/welcome")
    client.post("/signup", json={"email": "u@x.com", "phone": "+1555"})
    r = client.get("/signup/acct1/verify-email", params={"token": "valid-email-token"},
                   follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    # The raw account_id must NOT be in the redirect (the Referer/log leak); a scoped token is.
    assert "account_id=" not in loc
    assert "session_token=" in loc
    # And the carried token is STATE-scoped (read-only) — useless for checkout if it leaks.
    from urllib.parse import parse_qs, urlparse, unquote
    carried = parse_qs(urlparse(loc).query)["session_token"][0]
    assert tokens.verify(unquote(carried), "state") == "acct1"
    assert tokens.verify(unquote(carried), "checkout") is None


@pytest.mark.integration
def test_verify_email_redirect_falls_back_to_account_id_when_tokens_off():
    client, _t, _s = _client(verify_redirect_url="https://app.example.com/welcome",
                             with_tokens=False)
    client.post("/signup", json={"email": "u@x.com", "phone": "+1555"})
    r = client.get("/signup/acct1/verify-email", params={"token": "valid-email-token"},
                   follow_redirects=False)
    assert r.status_code == 303
    assert "account_id=acct1" in r.headers["location"]   # legacy behavior preserved when off
