"""Unit: the SIGNUP_REQUIRE_PHONE feature flag — email-only verification when phone is gated off.

Default (flag unset / "true"): phone required (email AND phone → ready to pay). With
SIGNUP_REQUIRE_PHONE="false": an email-verified account is fully verified + ready to pay, no OTP
is needed, and the account advances straight to the ready-to-pay state on email verification."""
import pytest

from signup.accounts import Account, AccountService, State


# --------------------------------------------------------------------------- minimal stubs
class Store:
    def __init__(self):
        self.rows = {}

    def get(self, aid):
        return self.rows.get(aid)

    def get_by_email(self, email):
        return next((a for a in self.rows.values() if a.email == email), None)

    def insert(self, acct):
        self.rows[acct.id] = acct

    def update(self, acct):
        self.rows[acct.id] = acct


class Cognito:
    def create_unconfirmed_user(self, email):
        return f"sub-{email}"


class Email:
    def __init__(self):
        self.sent = []

    def send_verification(self, email, token):
        self.sent.append((email, token))
        return True


class Sms:
    def __init__(self):
        self.sent = []

    def send_otp(self, phone, code):
        self.sent.append((phone, code))
        return True


def _svc():
    return AccountService(Store(), Cognito(), Email(), Sms())


# --------------------------------------------------------------------------- default: phone required
@pytest.mark.unit
def test_default_requires_phone(monkeypatch):
    monkeypatch.delenv("SIGNUP_REQUIRE_PHONE", raising=False)
    svc = _svc()
    svc.create("a1", "u@x.com", "+15555550100")
    acct = svc.verify_email("a1", True)
    # Email alone is NOT ready to pay when phone is required.
    assert acct.email_verified is True
    assert acct.fully_verified is False
    assert acct.may_pay is False
    assert acct.state is State.EMAIL_VERIFIED
    # Verifying phone completes it.
    acct = svc.verify_phone("a1", True)
    assert acct.fully_verified is True and acct.may_pay is True
    assert acct.state is State.PHONE_VERIFIED


@pytest.mark.unit
@pytest.mark.parametrize("val", ["true", "TRUE", "1", "yes", "anything-not-false"])
def test_non_false_values_keep_phone_required(monkeypatch, val):
    # Only the literal "false" disables it — fail-safe (a typo never silently drops phone verify).
    monkeypatch.setenv("SIGNUP_REQUIRE_PHONE", val)
    acct = Account(id="a", email="e", phone="p", cognito_sub="s", email_verified=True)
    assert acct.fully_verified is False  # phone still required


# --------------------------------------------------------------------------- flag off: email-only
@pytest.mark.unit
def test_flag_off_email_only_is_ready_to_pay(monkeypatch):
    monkeypatch.setenv("SIGNUP_REQUIRE_PHONE", "false")
    svc = _svc()
    svc.create("a1", "u@x.com", "+15555550100")
    acct = svc.verify_email("a1", True)
    # Email alone now satisfies verification and advances straight to ready-to-pay.
    assert acct.email_verified is True
    assert acct.phone_verified is False         # phone never verified
    assert acct.fully_verified is True
    assert acct.may_pay is True
    assert acct.state is State.PHONE_VERIFIED    # the ready-to-pay state


@pytest.mark.unit
def test_flag_off_case_insensitive(monkeypatch):
    monkeypatch.setenv("SIGNUP_REQUIRE_PHONE", "  False  ")
    acct = Account(id="a", email="e", phone="p", cognito_sub="s", email_verified=True)
    assert acct.fully_verified is True  # trimmed + lowercased


# --------------------------------------------------------------------------- the create response
@pytest.mark.unit
def test_signup_response_carries_require_phone(monkeypatch):
    from signup.accounts import _require_phone_verification
    monkeypatch.setenv("SIGNUP_REQUIRE_PHONE", "false")
    assert _require_phone_verification() is False
    monkeypatch.setenv("SIGNUP_REQUIRE_PHONE", "true")
    assert _require_phone_verification() is True
