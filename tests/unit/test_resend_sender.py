"""Unit: ResendEmailSender — payload shape, the DRAFT-GATE default, logs-not-raises."""
import json
import urllib.error
from dataclasses import dataclass

import pytest

from shared import config
from signup.resend_sender import RESEND_API_URL, ResendEmailSender


class PoisonOpener:
    """Fails the test if any network attempt happens."""

    def __init__(self):
        self.calls = 0

    def __call__(self, request, timeout):
        self.calls += 1
        raise AssertionError("network opener must not be called")


class CaptureOpener:
    def __init__(self):
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))

        class _Resp:
            def read(self_inner):
                return b'{"id": "email_123"}'

        return _Resp()


@dataclass
class FakeAccount:
    email: str


def _payload(request):
    return json.loads(request.data.decode("utf-8"))


# ---------------- DRAFT-GATE blocks by default ----------------
@pytest.mark.unit
def test_gate_blocks_by_default_no_network():
    poison = PoisonOpener()
    sender = ResendEmailSender("re_key", "Uplift <noreply@uplift.test>", opener=poison)
    assert sender.allow_real_sends is False  # explicit flag, default False
    assert sender.send_verification("u@x.com", "tok123") is False
    assert sender.send_welcome("u@x.com", "tenant-1") is False
    assert poison.calls == 0  # gate refused before any transport


@pytest.mark.unit
def test_config_gate_default_is_false(monkeypatch):
    monkeypatch.delenv("ALLOW_REAL_SENDS", raising=False)
    assert config.Config().allow_real_sends is False  # safe default at the config seam too


# ---------------- payload shapes (transport mocked) ----------------
@pytest.mark.unit
def test_send_verification_payload_shape_and_link():
    cap = CaptureOpener()
    sender = ResendEmailSender(
        "re_key", "Uplift <noreply@uplift.test>",
        allow_real_sends=True,
        verify_url_base="https://app.uplift.test/verify-email",
        opener=cap,
    )
    assert sender.send_verification("user@example.com", "tok123") is True
    (request, _timeout), = cap.requests
    assert request.full_url == RESEND_API_URL
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer re_key"
    assert request.get_header("Content-type") == "application/json"
    body = _payload(request)
    assert body["from"] == "Uplift <noreply@uplift.test>"
    assert body["to"] == ["user@example.com"]
    assert "Verify" in body["subject"]
    # bare token composed onto the click-through base
    assert "https://app.uplift.test/verify-email/tok123" in body["html"]
    # AND the bare token is shown as copy-pasteable code (the SPA flow is typed-code)
    assert "<code>tok123</code>" in body["html"]


@pytest.mark.unit
def test_full_signed_url_passes_through_unchanged():
    cap = CaptureOpener()
    sender = ResendEmailSender("re_key", "noreply@uplift.test",
                               allow_real_sends=True, opener=cap)
    link = "https://app.uplift.test/verify-email?token=abc&sig=def"
    assert sender.send_verification("u@x.com", link) is True
    body = _payload(cap.requests[0][0])
    assert "https://app.uplift.test/verify-email?token=abc&amp;sig=def" in body["html"]


@pytest.mark.unit
def test_send_welcome_accepts_account_object_and_tenant():
    cap = CaptureOpener()
    sender = ResendEmailSender("re_key", "noreply@uplift.test",
                               allow_real_sends=True, opener=cap)
    assert sender.send_welcome(FakeAccount(email="owner@x.com"), "tenant-a1") is True
    body = _payload(cap.requests[0][0])
    assert body["to"] == ["owner@x.com"]  # Account-like object resolved via .email
    assert "tenant-a1" in body["html"]
    assert "ready" in body["subject"]


# ---------------- failure contract: logs, never raises ----------------
@pytest.mark.unit
def test_transport_failure_logs_not_raises(caplog):
    def boom(request, timeout):
        raise urllib.error.URLError("connection refused")

    sender = ResendEmailSender("re_key", "noreply@uplift.test",
                               allow_real_sends=True, opener=boom)
    with caplog.at_level("WARNING"):
        assert sender.send_verification("u@x.com", "tok") is False  # no exception escapes
    assert any("Resend send failed" in r.message for r in caplog.records)


@pytest.mark.unit
def test_unconfigured_key_drops_cleanly_even_when_ungated():
    poison = PoisonOpener()
    sender = ResendEmailSender("", "noreply@uplift.test",
                               allow_real_sends=True, opener=poison)
    assert sender.send_welcome("u@x.com") is False
    assert poison.calls == 0  # unconfigured -> stub cleanly, no network


# ---------------- import safety ----------------
@pytest.mark.unit
def test_import_is_side_effect_free():
    import importlib

    import signup.resend_sender as mod

    importlib.reload(mod)  # re-import performs no I/O (would explode loudly if it did)
