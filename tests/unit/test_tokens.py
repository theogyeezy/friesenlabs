"""Unit: signup verification credentials — signed email tokens + SMS OTP (signup/tokens.py).

Fake clock throughout (no sleeps). Proves the four security properties the signup flow leans on:
expiry (15-min TTL), tamper-rejection (HMAC), replay-rejection (single-use), and that every
credential comparison goes through the constant-time path (hmac.compare_digest) — plus the OTP
attempt-lockout and issue rate-limit counters.
"""
import pytest

import signup.tokens as tokens
from signup.tokens import (
    EmailTokenService,
    InMemoryOtpStore,
    OtpRateLimitError,
    OtpService,
)

SECRET = "test-signing-secret"


class Clock:
    """Injectable fake clock."""

    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


# ---------------------------------------------------------------------------
# Email tokens
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_email_token_round_trip():
    clock = Clock()
    svc = EmailTokenService(SECRET, now=clock)
    token = svc.issue("acct-1")
    assert svc.verify("acct-1", token) is True


@pytest.mark.unit
def test_email_token_expires_after_ttl():
    clock = Clock()
    svc = EmailTokenService(SECRET, ttl_seconds=900, now=clock)
    token = svc.issue("acct-1")
    clock.advance(901)  # one second past the 15-minute TTL
    assert svc.verify("acct-1", token) is False


@pytest.mark.unit
def test_email_token_valid_just_inside_ttl():
    clock = Clock()
    svc = EmailTokenService(SECRET, ttl_seconds=900, now=clock)
    token = svc.issue("acct-1")
    clock.advance(899)
    assert svc.verify("acct-1", token) is True


@pytest.mark.unit
def test_email_token_tamper_rejected():
    clock = Clock()
    svc = EmailTokenService(SECRET, now=clock)
    token = svc.issue("acct-1")
    body, _, sig = token.rpartition(".")
    # Tamper the signature.
    flipped = ("0" if sig[-1] != "0" else "1")
    assert svc.verify("acct-1", body + "." + sig[:-1] + flipped) is False
    # Tamper the body (signature no longer matches).
    assert svc.verify("acct-1", "A" + token[1:]) is False
    # A token signed with a DIFFERENT secret never verifies.
    other = EmailTokenService("other-secret", now=clock).issue("acct-1")
    assert svc.verify("acct-1", other) is False


@pytest.mark.unit
def test_email_token_replay_rejected_single_use():
    clock = Clock()
    svc = EmailTokenService(SECRET, now=clock)
    token = svc.issue("acct-1")
    assert svc.verify("acct-1", token) is True
    assert svc.verify("acct-1", token) is False  # second presentation = replay


@pytest.mark.unit
def test_email_token_bound_to_account():
    clock = Clock()
    svc = EmailTokenService(SECRET, now=clock)
    token = svc.issue("acct-1")
    assert svc.verify("acct-2", token) is False  # signed, unexpired — but for someone else
    assert svc.verify("acct-1", token) is True   # the miss did NOT consume it


@pytest.mark.unit
def test_email_token_malformed_inputs_return_false_never_raise():
    svc = EmailTokenService(SECRET, now=Clock())
    for junk in ["", "no-dot", "bad.sig", "!!!.deadbeef", None, 42, "a.b.c"]:
        assert svc.verify("acct-1", junk) is False


@pytest.mark.unit
def test_email_token_constant_time_path(monkeypatch):
    """Every signature/account comparison goes through the constant-time hook — even on a
    tampered token — never a bare `==` (no timing oracle)."""
    calls = []
    real = tokens._consteq

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(tokens, "_consteq", spy)
    clock = Clock()
    svc = EmailTokenService(SECRET, now=clock)
    token = svc.issue("acct-1")

    calls.clear()
    assert svc.verify("acct-1", token[:-1] + ("0" if token[-1] != "0" else "1")) is False
    assert len(calls) >= 1  # the tampered signature still went through compare_digest

    calls.clear()
    assert svc.verify("acct-1", token) is True
    assert len(calls) >= 2  # signature AND account binding both compared constant-time


@pytest.mark.unit
def test_email_token_empty_secret_refused():
    with pytest.raises(ValueError):
        EmailTokenService("")


# ---------------------------------------------------------------------------
# SMS OTP
# ---------------------------------------------------------------------------

def _otp(clock, **kw):
    return OtpService(SECRET, InMemoryOtpStore(), now=clock, **kw)


@pytest.mark.unit
def test_otp_is_six_digits_and_round_trips():
    clock = Clock()
    svc = _otp(clock)
    code = svc.issue("acct-1")
    assert len(code) == 6 and code.isdigit()
    assert svc.verify("acct-1", code) is True


@pytest.mark.unit
def test_otp_single_use():
    clock = Clock()
    svc = _otp(clock)
    code = svc.issue("acct-1")
    assert svc.verify("acct-1", code) is True
    assert svc.verify("acct-1", code) is False  # consumed on success


@pytest.mark.unit
def test_otp_expires():
    clock = Clock()
    svc = _otp(clock, ttl_seconds=600)
    code = svc.issue("acct-1")
    clock.advance(601)
    assert svc.verify("acct-1", code) is False


@pytest.mark.unit
def test_otp_wrong_code_and_no_code_issued():
    clock = Clock()
    svc = _otp(clock)
    assert svc.verify("acct-1", "123456") is False  # nothing issued — False, no raise
    code = svc.issue("acct-1")
    wrong = "000000" if code != "000000" else "000001"
    assert svc.verify("acct-1", wrong) is False
    assert svc.verify("acct-1", code) is True       # the right code still works after one miss


@pytest.mark.unit
def test_otp_attempt_lockout_then_reissue_recovers():
    clock = Clock()
    svc = _otp(clock, max_attempts=3)
    code = svc.issue("acct-1")
    wrong = "000000" if code != "000000" else "000001"
    for _ in range(3):
        assert svc.verify("acct-1", wrong) is False
    assert svc.verify("acct-1", code) is False      # locked out — even the RIGHT code fails
    code2 = svc.issue("acct-1")                     # re-issue mints a fresh record
    assert svc.verify("acct-1", code2) is True


@pytest.mark.unit
def test_otp_issue_rate_limited_then_window_rolls_over():
    clock = Clock()
    svc = _otp(clock, max_sends=3, send_window_seconds=3600)
    for _ in range(3):
        svc.issue("acct-1")
    with pytest.raises(OtpRateLimitError):
        svc.issue("acct-1")
    clock.advance(3601)                             # window rolled over — budget resets
    code = svc.issue("acct-1")
    assert svc.verify("acct-1", code) is True


@pytest.mark.unit
def test_otp_constant_time_compare(monkeypatch):
    calls = []
    real = tokens._consteq

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(tokens, "_consteq", spy)
    clock = Clock()
    svc = _otp(clock)
    code = svc.issue("acct-1")

    calls.clear()
    svc.verify("acct-1", "999999" if code != "999999" else "999998")
    assert len(calls) == 1                          # the wrong code went through compare_digest

    calls.clear()
    svc.verify("no-such-account", "123456")         # even with NO record, the compare happens
    assert len(calls) == 1


@pytest.mark.unit
def test_otp_store_never_holds_the_plain_code():
    clock = Clock()
    store = InMemoryOtpStore()
    svc = OtpService(SECRET, store, now=clock)
    code = svc.issue("acct-1")
    rec = store.get_otp("acct-1")
    assert code not in str(rec)                     # only the HMAC is persisted


@pytest.mark.unit
def test_otp_empty_secret_refused():
    with pytest.raises(ValueError):
        OtpService("")
