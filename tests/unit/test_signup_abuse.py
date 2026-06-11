"""Unit tests for the acquisition-funnel abuse controls (signup/abuse.py).

Covers the three building blocks directly (no HTTP): the disposable-email blocklist + its
overrides, the per-IP velocity limiter window/keying, and the captcha seam's default-open /
fail-closed contract — including the real siteverify validator path (provider selected by env,
fake HTTP transport injected so tests never hit the network).
"""
import pytest

from signup.abuse import (
    ACTION_RESEND,
    ACTION_SIGNUP,
    CaptchaRequiredError,
    CaptchaVerifier,
    DisposableEmailBlocklist,
    DisposableEmailError,
    ENV_HCAPTCHA_SECRET,
    ENV_TURNSTILE_SECRET,
    SignupVelocityLimiter,
    VelocityLimitError,
    _make_hcaptcha_validator,
    _make_turnstile_validator,
)


# --- disposable email blocklist ---------------------------------------------------------------

def test_shipped_blocklist_loads_and_flags_known_disposable():
    bl = DisposableEmailBlocklist.from_env()
    assert len(bl) > 50  # the shipped static list is non-trivial
    assert bl.is_disposable("burner@mailinator.com") is True
    assert bl.is_disposable("Throwaway@GuerrillaMail.com") is True  # case-insensitive
    # A real provider is never flagged.
    assert bl.is_disposable("nick@gmail.com") is False
    assert bl.is_disposable("ceo@friesenlabs.com") is False


def test_check_raises_with_honest_copy():
    bl = DisposableEmailBlocklist({"mailinator.com"})
    with pytest.raises(DisposableEmailError) as ei:
        bl.check("x@mailinator.com")
    msg = str(ei.value)
    assert "permanent email" in msg.lower()
    assert "mailinator.com" in msg
    assert ei.value.domain == "mailinator.com"
    # A clean address passes (returns None, no raise).
    assert bl.check("x@gmail.com") is None


def test_malformed_or_empty_email_is_not_flagged_disposable():
    # The email-SHAPE validator owns rejecting these; the blocklist only fires on a real domain.
    bl = DisposableEmailBlocklist({"mailinator.com"})
    assert bl.is_disposable("") is False
    assert bl.is_disposable("not-an-email") is False
    assert bl.is_disposable(None) is False  # type: ignore[arg-type]


def test_blocklist_is_overridable_via_extra_and_alternate_file(tmp_path):
    f = tmp_path / "custom.txt"
    f.write_text("# header\nevil.test\nspam.test\n\n", encoding="utf-8")
    bl = DisposableEmailBlocklist.from_file(f, extra={"more.test"})
    assert bl.is_disposable("a@evil.test") is True
    assert bl.is_disposable("a@spam.test") is True
    assert bl.is_disposable("a@more.test") is True
    assert bl.is_disposable("a@gmail.com") is False


def test_from_env_honors_override_knobs(monkeypatch, tmp_path):
    from signup.abuse import ENV_DISPOSABLE_DOMAINS_EXTRA, ENV_DISPOSABLE_DOMAINS_FILE

    f = tmp_path / "alt.txt"
    f.write_text("alt-only.test\n", encoding="utf-8")
    monkeypatch.setenv(ENV_DISPOSABLE_DOMAINS_FILE, str(f))
    monkeypatch.setenv(ENV_DISPOSABLE_DOMAINS_EXTRA, "x.test, y.test")
    bl = DisposableEmailBlocklist.from_env()
    assert bl.is_disposable("a@alt-only.test") is True
    assert bl.is_disposable("a@x.test") is True
    assert bl.is_disposable("a@y.test") is True
    # The shipped mailinator entry is NOT present because we pointed at the alternate file.
    assert bl.is_disposable("a@mailinator.com") is False


def test_missing_override_file_degrades_to_extra_only():
    # A misconfigured alternate path must never crash signup — it degrades to the extra set.
    bl = DisposableEmailBlocklist.from_file("/no/such/file.txt", extra={"only.test"})
    assert bl.is_disposable("a@only.test") is True
    assert bl.is_disposable("a@mailinator.com") is False


# --- per-IP velocity limiter ------------------------------------------------------------------

def test_velocity_limit_blocks_after_budget():
    clock = [0.0]
    v = SignupVelocityLimiter(limit=3, window_seconds=60, now=lambda: clock[0])
    assert [v.allow(ACTION_SIGNUP, "1.1.1.1") for _ in range(3)] == [True, True, True]
    assert v.allow(ACTION_SIGNUP, "1.1.1.1") is False  # 4th in-window -> blocked


def test_velocity_check_raises_velocity_error():
    v = SignupVelocityLimiter(limit=1, window_seconds=60)
    v.check(ACTION_SIGNUP, "9.9.9.9")  # first passes
    with pytest.raises(VelocityLimitError) as ei:
        v.check(ACTION_SIGNUP, "9.9.9.9")
    assert ei.value.action == ACTION_SIGNUP
    assert "9.9.9.9" not in str(ei.value)  # copy doesn't leak the IP back to the caller


def test_velocity_window_rolls_off():
    clock = [0.0]
    v = SignupVelocityLimiter(limit=2, window_seconds=60, now=lambda: clock[0])
    assert v.allow(ACTION_SIGNUP, "ip") and v.allow(ACTION_SIGNUP, "ip")
    assert v.allow(ACTION_SIGNUP, "ip") is False
    clock[0] = 61.0  # past the window -> the budget refreshes
    assert v.allow(ACTION_SIGNUP, "ip") is True


def test_velocity_keys_independently_per_ip_and_action():
    v = SignupVelocityLimiter(limit=1, window_seconds=60)
    assert v.allow(ACTION_SIGNUP, "a") is True
    assert v.allow(ACTION_SIGNUP, "a") is False
    # A different IP has its own budget.
    assert v.allow(ACTION_SIGNUP, "b") is True
    # And a different action on the SAME ip has its own budget.
    assert v.allow(ACTION_RESEND, "a") is True


# --- captcha seam -----------------------------------------------------------------------------

def test_captcha_seam_defaults_open():
    # The default seam never requires a token -> verify is a no-op (returns None for any input).
    c = CaptchaVerifier()
    assert c.required is False
    assert c.verify(None) is None
    assert c.verify("anything") is None


def test_captcha_from_env_default_open(monkeypatch):
    from signup.abuse import ENV_CAPTCHA_REQUIRED

    monkeypatch.delenv(ENV_CAPTCHA_REQUIRED, raising=False)
    assert CaptchaVerifier.from_env().required is False
    monkeypatch.setenv(ENV_CAPTCHA_REQUIRED, "true")
    assert CaptchaVerifier.from_env().required is True


def test_captcha_required_fails_closed_without_validator():
    # 'required' with no validator wired must REFUSE (never silently wave everyone through).
    c = CaptchaVerifier(required=True)
    with pytest.raises(CaptchaRequiredError):
        c.verify("some-token")
    with pytest.raises(CaptchaRequiredError):
        c.verify(None)  # missing token also refused


def test_captcha_required_with_validator():
    seen = {}

    def validate(token, ip):
        seen["token"], seen["ip"] = token, ip
        return token == "good"

    c = CaptchaVerifier(required=True, token_validator=validate)
    assert c.verify("good", "1.2.3.4") is None
    assert seen == {"token": "good", "ip": "1.2.3.4"}
    with pytest.raises(CaptchaRequiredError):
        c.verify("bad", "1.2.3.4")


# --- real siteverify validator path (fake HTTP transport, no network) -------------------------

def _fake_http_post(response: dict):
    """Return a fake http_post that records calls and returns the given response dict."""
    calls = []

    def _post(url, data, headers):
        calls.append({"url": url, "data": data, "headers": headers})
        return response

    _post.calls = calls
    return _post


def test_turnstile_validator_passes_valid_token():
    """A Turnstile token that the provider marks success=True should pass (return True)."""
    http = _fake_http_post({"success": True})
    validate = _make_turnstile_validator("secret-key", http_post=http)
    assert validate("valid-token", "1.2.3.4") is True
    assert len(http.calls) == 1
    call = http.calls[0]
    assert "challenges.cloudflare.com" in call["url"]
    assert b"secret=secret-key" in call["data"]
    assert b"response=valid-token" in call["data"]
    assert b"remoteip=1.2.3.4" in call["data"]


def test_turnstile_validator_fails_invalid_token():
    """A Turnstile token that the provider marks success=False should fail (return False)."""
    http = _fake_http_post({"success": False, "error-codes": ["invalid-input-response"]})
    validate = _make_turnstile_validator("secret-key", http_post=http)
    assert validate("bad-token", "1.2.3.4") is False


def test_hcaptcha_validator_passes_valid_token():
    """An hCaptcha token that the provider marks success=True should pass."""
    http = _fake_http_post({"success": True})
    validate = _make_hcaptcha_validator("hcaptcha-secret", http_post=http)
    assert validate("valid-token", "2.2.2.2") is True
    call = http.calls[0]
    assert "hcaptcha.com" in call["url"]
    assert b"secret=hcaptcha-secret" in call["data"]
    assert b"response=valid-token" in call["data"]
    assert b"remoteip=2.2.2.2" in call["data"]


def test_hcaptcha_validator_fails_invalid_token():
    """An hCaptcha token that the provider marks success=False should fail."""
    http = _fake_http_post({"success": False})
    validate = _make_hcaptcha_validator("hcaptcha-secret", http_post=http)
    assert validate("bad-token", None) is False


def test_validator_omits_remoteip_when_none():
    """remote_ip=None should not send a remoteip field to the provider."""
    http = _fake_http_post({"success": True})
    validate = _make_turnstile_validator("secret-key", http_post=http)
    validate("token", None)
    assert b"remoteip" not in http.calls[0]["data"]


def test_captcha_required_valid_passes_via_real_validator():
    """required=True + wired validator + success response → verify passes (returns None)."""
    http = _fake_http_post({"success": True})
    validate = _make_turnstile_validator("ts-secret", http_post=http)
    c = CaptchaVerifier(required=True, token_validator=validate)
    assert c.verify("good-token", "1.2.3.4") is None


def test_captcha_required_invalid_fails_closed_via_real_validator():
    """required=True + wired validator + failure response → CaptchaRequiredError."""
    http = _fake_http_post({"success": False})
    validate = _make_turnstile_validator("ts-secret", http_post=http)
    c = CaptchaVerifier(required=True, token_validator=validate)
    with pytest.raises(CaptchaRequiredError):
        c.verify("bad-token", "1.2.3.4")


def test_captcha_not_required_no_op_even_with_validator():
    """required=False → verify is always a no-op regardless of the validator result."""
    http = _fake_http_post({"success": False})  # would fail if called
    validate = _make_turnstile_validator("ts-secret", http_post=http)
    c = CaptchaVerifier(required=False, token_validator=validate)
    assert c.verify("any-token", "1.2.3.4") is None
    # The validator must never be called when not required.
    assert len(http.calls) == 0


def test_from_env_wires_turnstile_when_secret_present(monkeypatch):
    """TURNSTILE_SECRET in env → from_env() wires the Turnstile validator."""
    monkeypatch.setenv("SIGNUP_CAPTCHA_REQUIRED", "true")
    monkeypatch.setenv(ENV_TURNSTILE_SECRET, "ts-secret-xyz")
    monkeypatch.delenv(ENV_HCAPTCHA_SECRET, raising=False)
    http = _fake_http_post({"success": True})
    c = CaptchaVerifier.from_env(http_post=http)
    assert c.required is True
    assert c._validate is not None
    # The validator actually calls the Turnstile endpoint (via the injected fake transport).
    assert c.verify("token", "1.1.1.1") is None
    assert "challenges.cloudflare.com" in http.calls[0]["url"]


def test_from_env_wires_hcaptcha_when_only_hcaptcha_secret_present(monkeypatch):
    """HCAPTCHA_SECRET in env (no TURNSTILE_SECRET) → from_env() wires the hCaptcha validator."""
    monkeypatch.setenv("SIGNUP_CAPTCHA_REQUIRED", "true")
    monkeypatch.delenv(ENV_TURNSTILE_SECRET, raising=False)
    monkeypatch.setenv(ENV_HCAPTCHA_SECRET, "hc-secret-xyz")
    http = _fake_http_post({"success": True})
    c = CaptchaVerifier.from_env(http_post=http)
    assert c.required is True
    assert c._validate is not None
    assert c.verify("token", "2.2.2.2") is None
    assert "hcaptcha.com" in http.calls[0]["url"]


def test_from_env_turnstile_takes_precedence_over_hcaptcha(monkeypatch):
    """When BOTH secrets are present, Turnstile wins."""
    monkeypatch.setenv("SIGNUP_CAPTCHA_REQUIRED", "true")
    monkeypatch.setenv(ENV_TURNSTILE_SECRET, "ts-wins")
    monkeypatch.setenv(ENV_HCAPTCHA_SECRET, "hc-loses")
    http = _fake_http_post({"success": True})
    c = CaptchaVerifier.from_env(http_post=http)
    c.verify("token", None)
    assert "challenges.cloudflare.com" in http.calls[0]["url"]


def test_from_env_no_secret_no_validator_required_fails_closed(monkeypatch):
    """required=True but no provider secret → from_env() has no validator → fails closed."""
    monkeypatch.setenv("SIGNUP_CAPTCHA_REQUIRED", "true")
    monkeypatch.delenv(ENV_TURNSTILE_SECRET, raising=False)
    monkeypatch.delenv(ENV_HCAPTCHA_SECRET, raising=False)
    c = CaptchaVerifier.from_env()
    assert c.required is True
    assert c._validate is None
    with pytest.raises(CaptchaRequiredError):
        c.verify("some-token", None)


def test_from_env_no_secret_not_required_stays_no_op(monkeypatch):
    """No provider secret + required=False → from_env() is a plain no-op (open seam)."""
    monkeypatch.delenv("SIGNUP_CAPTCHA_REQUIRED", raising=False)
    monkeypatch.delenv(ENV_TURNSTILE_SECRET, raising=False)
    monkeypatch.delenv(ENV_HCAPTCHA_SECRET, raising=False)
    c = CaptchaVerifier.from_env()
    assert c.required is False
    assert c._validate is None
    assert c.verify(None) is None
    assert c.verify("any-token") is None
