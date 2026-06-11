"""Integration: acquisition-funnel abuse controls at the HTTP layer (api/signup_routes.py).

Proves, end-to-end through the mounted routes:
  * a disposable email is rejected at signup-start with honest copy (422),
  * the per-IP signup velocity cap answers 429 on exceed (and a different IP is independent),
  * the verification-resend velocity guard answers 429 on exceed,
  * the captcha seam is OPEN by default (signup unaffected) and ENFORCES once required.

The velocity limiter keys on the TRUST-BOUNDARY viewer IP parsed from X-Forwarded-For (reused
from api/public_routes), so the tests drive distinct IPs via the XFF header — never the shared
ALB socket peer.
"""
import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.signup_routes import SignupDeps
from api.views import SavedViews
from signup.abuse import (
    CaptchaVerifier,
    DisposableEmailBlocklist,
    SignupVelocityLimiter,
)
from signup.accounts import AccountService
from signup.payment import PaymentService

# Reuse the provisioning unit-test fakes (same as test_api_signup.py).
from tests.unit.test_signup_provisioning import (
    AnthropicAdmin, Cognito, DB, Email, Recorder, Secrets, Store,
)
from signup.provisioning import Provisioner


class _Stripe:
    def create_customer(self, email, idempotency_key):
        return {"id": "cus_1"}

    def create_checkout_session(self, **kw):
        return {"id": "cs_1", "url": "https://checkout.stripe.com/c/pay/cs_1"}

    def construct_event(self, payload, sig, secret):
        return {"type": "checkout.session.completed",
                "data": {"object": {"client_reference_id": None}}}


def _client(*, disposable=None, velocity=None, captcha=None, new_id=None):
    store = Store()
    accounts = AccountService(store, Cognito(), Email(), Recorder())
    prov = Provisioner(store=store, mint_tenant_id=lambda aid: f"tenant-{aid}", db=DB(),
                       anthropic_admin=AnthropicAdmin(), secrets=Secrets(), cognito=Cognito(),
                       cube=Recorder(), resend=Recorder(), agent_plane=Recorder())
    payment = PaymentService(_Stripe(), accounts, on_paid=prov.provision)
    # A counter so each /signup mints a UNIQUE account_id (idempotency keys off account_id, not
    # email — these abuse tests use distinct emails so uniqueness-by-email never collapses them).
    counter = {"n": 0}

    def _new_id():
        counter["n"] += 1
        return f"acct{counter['n']}"

    signup = SignupDeps(
        accounts=accounts, payment=payment, stripe_webhook_secret="whsec",
        new_account_id=new_id or _new_id,
        email_token_ok=lambda aid, t: t == "valid-email-token",
        sms_code_ok=lambda aid, c: c == "123456",
        disposable=disposable, velocity=velocity, captcha=captcha,
    )
    deps = ApiDeps(verifier=object(), greenlight=Greenlight(), saved_views=SavedViews(),
                   conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
                   executor=lambda a: None, signup=signup)
    return TestClient(create_app(deps))


def _xff(ip):
    # XFF with the viewer IP two hops from the right (default trusted_hops=2): <viewer>, <edge>.
    return {"x-forwarded-for": f"{ip}, 70.132.0.1"}


# --- disposable email ------------------------------------------------------------------------

@pytest.mark.integration
def test_disposable_email_rejected_with_honest_copy():
    client = _client(disposable=DisposableEmailBlocklist({"mailinator.com"}))
    r = client.post("/signup", json={"email": "burner@mailinator.com", "phone": "+15551230000"})
    assert r.status_code == 422
    assert "permanent email" in r.json()["detail"].lower()


@pytest.mark.integration
def test_real_email_passes_disposable_check():
    client = _client(disposable=DisposableEmailBlocklist({"mailinator.com"}))
    r = client.post("/signup", json={"email": "nick@gmail.com", "phone": "+15551230000"})
    assert r.status_code == 200
    assert r.json()["state"] == "created"


@pytest.mark.integration
def test_no_disposable_dep_is_byte_identical_passthrough():
    # disposable=None -> the route's NEW disposable check is absent. A domain that WOULD be on an
    # injected blocklist (but isn't in signup.accounts' tiny built-in set) proceeds unblocked,
    # proving the inert default changes nothing.
    client = _client()
    r = client.post("/signup", json={"email": "u@evil.test", "phone": "+15551230000"})
    assert r.status_code == 200
    # And with the dep wired, that same domain is rejected — the gate is what changes behavior.
    client2 = _client(disposable=DisposableEmailBlocklist({"evil.test"}))
    r2 = client2.post("/signup", json={"email": "u@evil.test", "phone": "+15551230000"})
    assert r2.status_code == 422


# --- signup velocity -------------------------------------------------------------------------

@pytest.mark.integration
def test_signup_velocity_429_after_budget_same_ip():
    client = _client(velocity=SignupVelocityLimiter(limit=2, window_seconds=3600))
    for i in range(2):
        r = client.post("/signup", json={"email": f"u{i}@x.com", "phone": "+15551230000"},
                        headers=_xff("203.0.113.7"))
        assert r.status_code == 200
    # 3rd signup from the SAME viewer IP within the window -> 429.
    r = client.post("/signup", json={"email": "u9@x.com", "phone": "+15551230000"},
                    headers=_xff("203.0.113.7"))
    assert r.status_code == 429
    assert "too many" in r.json()["detail"].lower()


@pytest.mark.integration
def test_signup_velocity_independent_per_viewer_ip():
    client = _client(velocity=SignupVelocityLimiter(limit=1, window_seconds=3600))
    a = client.post("/signup", json={"email": "a@x.com", "phone": "+15551230000"},
                    headers=_xff("203.0.113.7"))
    assert a.status_code == 200
    # Same IP exceeds, but a DIFFERENT viewer IP behind the same proxy has its own budget.
    assert client.post("/signup", json={"email": "a2@x.com", "phone": "+15551230000"},
                       headers=_xff("203.0.113.7")).status_code == 429
    assert client.post("/signup", json={"email": "b@x.com", "phone": "+15551230000"},
                       headers=_xff("198.51.100.4")).status_code == 200


# --- verification-resend velocity ------------------------------------------------------------

@pytest.mark.integration
def test_verify_email_resend_throttled_per_ip():
    client = _client(velocity=SignupVelocityLimiter(limit=2, window_seconds=3600))
    client.post("/signup", json={"email": "u@x.com", "phone": "+15551230000"},
                headers=_xff("203.0.113.7"))
    # Two verify-email attempts pass, the third (same IP, same window) -> 429. (Bad token, so no
    # state flips — the velocity guard runs regardless of token validity.)
    assert client.post("/signup/acct1/verify-email", json={"token": "wrong"},
                       headers=_xff("203.0.113.7")).status_code == 200
    assert client.post("/signup/acct1/verify-email", json={"token": "wrong"},
                       headers=_xff("203.0.113.7")).status_code == 200
    r = client.post("/signup/acct1/verify-email", json={"token": "wrong"},
                    headers=_xff("203.0.113.7"))
    assert r.status_code == 429


@pytest.mark.integration
def test_verify_phone_resend_throttled_per_ip():
    client = _client(velocity=SignupVelocityLimiter(limit=1, window_seconds=3600))
    client.post("/signup", json={"email": "u@x.com", "phone": "+15551230000"},
                headers=_xff("203.0.113.7"))
    assert client.post("/signup/acct1/verify-phone", json={"code": "000000"},
                       headers=_xff("203.0.113.7")).status_code == 200
    assert client.post("/signup/acct1/verify-phone", json={"code": "000000"},
                       headers=_xff("203.0.113.7")).status_code == 429


@pytest.mark.integration
def test_signup_and_resend_have_separate_budgets():
    # The signup action and the resend action key independently — exhausting signups does not
    # block verify attempts (different limiter key).
    client = _client(velocity=SignupVelocityLimiter(limit=1, window_seconds=3600))
    assert client.post("/signup", json={"email": "u@x.com", "phone": "+15551230000"},
                       headers=_xff("203.0.113.7")).status_code == 200
    # signup budget for this IP is now spent, but verify-email (resend action) still has its own.
    assert client.post("/signup/acct1/verify-email", json={"token": "wrong"},
                       headers=_xff("203.0.113.7")).status_code == 200


# --- captcha seam ----------------------------------------------------------------------------

@pytest.mark.integration
def test_captcha_seam_open_by_default():
    # The default seam never demands a token -> signup is byte-identical to no captcha.
    client = _client(captcha=CaptchaVerifier())  # required=False
    r = client.post("/signup", json={"email": "u@x.com", "phone": "+15551230000"})
    assert r.status_code == 200


@pytest.mark.integration
def test_captcha_seam_enforces_when_required():
    # When flipped required (and no validator wired) the seam fails closed -> 400, even with a
    # token. This proves the wiring point exists without integrating a real provider.
    client = _client(captcha=CaptchaVerifier(required=True))
    r = client.post("/signup", json={"email": "u@x.com", "phone": "+15551230000"},
                    headers={"x-captcha-token": "anything"})
    assert r.status_code == 400
    assert "captcha" in r.json()["detail"].lower()
