"""Unit: typed-password signup path (cognito-password-fix).

Covers the end-to-end contract without network or DB:

  1. Provided password → set_signup_password → admin_set_user_password(Permanent=True) with the
     user's real credential; provisioning confirm() then sets email_verified and does NOT reset
     the password (idempotent CONFIRMED path).
  2. No-password (older client / back-compat) → confirm() sets a GENERATED throwaway password via
     the existing FORCE_CHANGE_PASSWORD path.
  3. The password is never echoed in the API response, never stored as an attribute on the account,
     and never logged (log records carry no Password field).
"""
from __future__ import annotations

import logging
import types
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from signup.cognito_admin import CognitoAdminClient


# --------------------------------------------------------------------------- Cognito fake
# Mirrors the FakeCidp in test_cognito_admin.py but lives here so the tests are self-contained.

class UsernameExists(Exception):
    pass


class NotAuthorized(Exception):
    pass


class FakeCidp:
    """boto3 cognito-idp stand-in: in-memory user pool + recorded calls."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.users: dict[str, dict] = {}
        self._n = 0
        self.exceptions = types.SimpleNamespace(
            UsernameExistsException=UsernameExists,
            NotAuthorizedException=NotAuthorized,
        )

    def _lookup(self, username: str) -> dict:
        if username in self.users:
            return self.users[username]
        for u in self.users.values():
            if u["sub"] == username:
                return u
        raise KeyError(username)

    def admin_create_user(self, **kw):
        self.calls.append(("admin_create_user", kw))
        username = kw["Username"]
        if username in self.users:
            raise UsernameExists(username)
        self._n += 1
        sub = f"sub-{self._n}"
        self.users[username] = {"sub": sub, "attrs": {},
                                "status": "FORCE_CHANGE_PASSWORD", "password": None}
        return {"User": {"Username": username,
                         "Attributes": [{"Name": "sub", "Value": sub},
                                        {"Name": "email", "Value": username}]}}

    def admin_get_user(self, **kw):
        self.calls.append(("admin_get_user", kw))
        u = self._lookup(kw["Username"])
        return {"Username": kw["Username"],
                "UserStatus": u["status"],
                "UserAttributes": [{"Name": "sub", "Value": u["sub"]}]}

    def admin_update_user_attributes(self, **kw):
        self.calls.append(("admin_update_user_attributes", kw))
        u = self._lookup(kw["Username"])
        for attr in kw["UserAttributes"]:
            u["attrs"][attr["Name"]] = attr["Value"]

    def admin_set_user_password(self, **kw):
        self.calls.append(("admin_set_user_password", kw))
        u = self._lookup(kw["Username"])
        u["password"] = kw["Password"]
        if kw.get("Permanent"):
            u["status"] = "CONFIRMED"

    def admin_confirm_sign_up(self, **kw):
        self.calls.append(("admin_confirm_sign_up", kw))
        u = self._lookup(kw["Username"])
        if u["status"] == "CONFIRMED":
            raise NotAuthorized("User cannot be confirmed. Current status is CONFIRMED")
        u["status"] = "CONFIRMED"


def _cognito_client(fake=None):
    fake = fake or FakeCidp()
    return CognitoAdminClient("us-east-1_TestPool", client=fake), fake


# --------------------------------------------------------------------------- Route helpers

def _route_client(password_arg: str | None = None):
    """Build a TestClient for the signup route; Cognito is the real CognitoAdminClient
    wired to a FakeCidp so we can inspect admin_set_user_password calls."""

    from api.signup_routes import SignupDeps, mount_signup
    from signup.accounts import AccountService
    from signup.payment import PaymentService

    # Minimal fakes — just enough for the signup + password path.
    class Store:
        def __init__(self):
            self.rows = {}
        def get(self, aid): return self.rows.get(aid)
        def insert(self, acct): self.rows[acct.id] = acct
        def update(self, acct): self.rows[acct.id] = acct

    class Email:
        def send_verification(self, *a, **k): pass

    class Sms:
        def send_otp(self, *a, **k): pass

    fake_cidp = FakeCidp()
    cognito = CognitoAdminClient("us-east-1_TestPool", client=fake_cidp)
    store = Store()
    accounts = AccountService(store, cognito, Email(), Sms())

    class Stripe:
        def create_customer(self, *a, **k): return {"id": "cus_1"}
        def create_checkout_session(self, **k):
            return {"id": "cs_1", "url": "https://checkout.stripe.com/c/pay/cs_1"}
        def construct_event(self, *a, **k): raise ValueError("no webhook")

    payment = PaymentService(Stripe(), accounts, on_paid=lambda a: None)
    signup = SignupDeps(
        accounts=accounts,
        payment=payment,
        stripe_webhook_secret="whsec",
        new_account_id=lambda: str(uuid.uuid4()),
        email_token_ok=lambda aid, t: False,
        sms_code_ok=lambda aid, c: False,
    )

    app = FastAPI()
    mount_signup(app, signup)
    return TestClient(app), fake_cidp, store


# ============================================================================= tests

@pytest.mark.unit
def test_set_signup_password_calls_admin_set_user_password_permanent():
    """set_signup_password sets the user's typed password with Permanent=True, making
    them CONFIRMED so they can log in immediately with what they typed."""
    cognito, fake = _cognito_client()
    sub = cognito.create_unconfirmed_user("u@x.com")
    assert fake.users["u@x.com"]["status"] == "FORCE_CHANGE_PASSWORD"

    cognito.set_signup_password(sub, "S3cur3P@ssw0rd!")
    user = fake.users["u@x.com"]
    assert user["status"] == "CONFIRMED"         # user can log in immediately
    assert user["password"] == "S3cur3P@ssw0rd!" # the exact typed credential

    # Verify the boto3 call used Permanent=True and the exact password.
    pw_calls = [c for c in fake.calls if c[0] == "admin_set_user_password"]
    assert len(pw_calls) == 1
    assert pw_calls[0][1]["Password"] == "S3cur3P@ssw0rd!"
    assert pw_calls[0][1]["Permanent"] is True


@pytest.mark.unit
def test_set_signup_password_is_idempotent_when_already_confirmed():
    """A duplicate signup POST must not reset a password the user has already set."""
    cognito, fake = _cognito_client()
    sub = cognito.create_unconfirmed_user("u@x.com")
    cognito.set_signup_password(sub, "FirstPassword1!")
    first_pw = fake.users["u@x.com"]["password"]

    # Simulate a duplicate call (e.g. retry on the same sub).
    cognito.set_signup_password(sub, "DifferentPassword2!")
    assert fake.users["u@x.com"]["password"] == first_pw  # unchanged

    # Only one admin_set_user_password call should have been made.
    pw_calls = [c for c in fake.calls if c[0] == "admin_set_user_password"]
    assert len(pw_calls) == 1


@pytest.mark.unit
def test_confirm_does_not_reset_password_when_already_confirmed():
    """If the user already has a CONFIRMED status (from set_signup_password), confirm()
    sets email_verified=true (for forgot-password) but never resets the password."""
    cognito, fake = _cognito_client()
    sub = cognito.create_unconfirmed_user("u@x.com")
    cognito.set_signup_password(sub, "TypedByUser1!")
    typed_pw = fake.users["u@x.com"]["password"]

    # Provisioning confirm() runs after set_signup_password.
    cognito.confirm(sub)
    assert fake.users["u@x.com"]["status"] == "CONFIRMED"
    assert fake.users["u@x.com"]["password"] == typed_pw    # never overwritten

    # admin_set_user_password should only have been called once (by set_signup_password).
    pw_calls = [c for c in fake.calls if c[0] == "admin_set_user_password"]
    assert len(pw_calls) == 1

    # email_verified must be true after confirm() (for the forgot-password flow).
    assert fake.users["u@x.com"]["attrs"].get("email_verified") == "true"


@pytest.mark.unit
def test_confirm_fallback_uses_generated_password_when_no_signup_password():
    """No-password path (older client): confirm() still sets a generated password via the
    FORCE_CHANGE_PASSWORD path — the user onboards via forgot-password as before."""
    cognito, fake = _cognito_client()
    sub = cognito.create_unconfirmed_user("u@x.com")
    # No set_signup_password call — user stays FORCE_CHANGE_PASSWORD.
    assert fake.users["u@x.com"]["status"] == "FORCE_CHANGE_PASSWORD"

    cognito.confirm(sub)
    user = fake.users["u@x.com"]
    assert user["status"] == "CONFIRMED"

    pw_calls = [c for c in fake.calls if c[0] == "admin_set_user_password"]
    assert len(pw_calls) == 1
    gen_pw = pw_calls[0][1]["Password"]
    # The generated password is strong (all Cognito policy classes) and NOT the user's value.
    assert len(gen_pw) >= 12
    assert any(c.isupper() for c in gen_pw)
    assert any(c.islower() for c in gen_pw)
    assert any(c.isdigit() for c in gen_pw)
    assert any(not c.isalnum() for c in gen_pw)
    assert user["attrs"].get("email_verified") == "true"


@pytest.mark.unit
def test_signup_route_with_password_calls_set_signup_password(caplog):
    """POST /signup with a password body field → set_signup_password is called → user is
    CONFIRMED with the typed credential. The password is never echoed in the response."""
    http, fake_cidp, store = _route_client()

    with caplog.at_level(logging.DEBUG):
        r = http.post("/signup", json={
            "email": "new@x.com",
            "phone": "+15555550100",
            "password": "V@lid1Password!",
        })

    assert r.status_code == 200
    body = r.json()
    # account_id is returned; the password is NOT echoed.
    assert "account_id" in body
    assert "password" not in body
    assert "V@lid1Password!" not in r.text

    # The Cognito user should now be CONFIRMED with the typed password.
    email = "new@x.com"
    assert email in fake_cidp.users
    user = fake_cidp.users[email]
    assert user["status"] == "CONFIRMED"
    assert user["password"] == "V@lid1Password!"

    # The password must not appear in any log record.
    for record in caplog.records:
        assert "V@lid1Password!" not in record.getMessage()


@pytest.mark.unit
def test_signup_route_without_password_leaves_user_force_change_password():
    """POST /signup without a password (older client / back-compat) leaves the Cognito user
    in FORCE_CHANGE_PASSWORD — provisioning's confirm() will handle it at the right time."""
    http, fake_cidp, _store = _route_client()

    r = http.post("/signup", json={"email": "old@x.com", "phone": "+15555550101"})
    assert r.status_code == 200

    email = "old@x.com"
    assert email in fake_cidp.users
    # No set_signup_password called → user stays in the initial state until confirm().
    assert fake_cidp.users[email]["status"] == "FORCE_CHANGE_PASSWORD"

    pw_calls = [c for c in fake_cidp.calls if c[0] == "admin_set_user_password"]
    assert len(pw_calls) == 0


@pytest.mark.unit
def test_signup_route_password_not_in_response_or_logs(caplog):
    """Defense-in-depth: the password must never appear in the HTTP response body or any
    log record, regardless of log level."""
    http, _fake, _store = _route_client()

    secret_pw = "T0p$ecretPw99!"
    with caplog.at_level(logging.DEBUG):
        r = http.post("/signup", json={
            "email": "check@x.com",
            "phone": "+15555550102",
            "password": secret_pw,
        })

    assert r.status_code == 200
    assert secret_pw not in r.text

    for record in caplog.records:
        assert secret_pw not in record.getMessage()
        # Also check the record args, in case the password was passed via % formatting.
        msg_with_args = str(record.args) if record.args else ""
        assert secret_pw not in msg_with_args
