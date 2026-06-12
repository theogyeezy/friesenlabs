"""Unit: PII masking in the outbound senders' logs (signup/__init__.py helpers).

The senders logged the FULL recipient email / phone on every gated, accepted, and failed send —
turning CloudWatch into an unguarded PII store. These tests pin: the helpers' masked forms
(j***@domain.com; last-4-only phone), their junk-tolerance (a logging helper must never raise),
and that every sender log line / raised-error message carries the MASKED identifier, never the
raw one — while the actual delivery payload keeps the real address (masking is logs-only).
"""
import json
import logging

import pytest

from signup import mask_email, mask_phone
from signup.resend_sender import ResendEmailSender
from signup.sms_sender import SmsSendError, SnsSmsOtpSender

EMAIL = "john.doe@acme-corp.com"
PHONE = "+15125550100"


# ---------------------------------------------------------------------------
# The helpers
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_mask_email_first_char_plus_domain():
    assert mask_email("john@acme.com") == "j***@acme.com"
    assert mask_email(EMAIL) == "j***@acme-corp.com"
    assert mask_email("a@b.co") == "a***@b.co"


@pytest.mark.unit
def test_mask_email_junk_never_raises():
    assert mask_email("") == "***"
    assert mask_email(None) == "***"
    assert mask_email("not-an-email") == "n***"
    assert mask_email("@nodomain-local") == "***@nodomain-local"
    assert mask_email(42) == "4***"


@pytest.mark.unit
def test_mask_phone_last_four_only():
    assert mask_phone(PHONE) == "***0100"
    assert mask_phone("+1 (512) 555-0100") == "***0100"


@pytest.mark.unit
def test_mask_phone_short_or_junk_masks_entirely():
    # Anything <= 4 chars would be FULLY revealed by a last-4 suffix — mask it all instead.
    assert mask_phone("0100") == "***"
    assert mask_phone("") == "***"
    assert mask_phone(None) == "***"


# ---------------------------------------------------------------------------
# ResendEmailSender — every log path masks; the payload keeps the real address
# ---------------------------------------------------------------------------

class CaptureOpener:
    def __init__(self, fail=False):
        self.requests = []
        self.fail = fail

    def __call__(self, request, timeout):
        if self.fail:
            raise OSError("connection refused")
        self.requests.append(request)

        class _Resp:
            def read(self_inner):
                return b'{"id": "email_123"}'

        return _Resp()


def _no_raw_email(caplog):
    assert all(EMAIL not in r.getMessage() for r in caplog.records)
    assert any("j***@acme-corp.com" in r.getMessage() for r in caplog.records)


@pytest.mark.unit
def test_resend_draft_gate_log_masks_recipient(caplog):
    sender = ResendEmailSender("re_key", "noreply@uplift.test", opener=CaptureOpener())
    with caplog.at_level(logging.INFO):
        assert sender.send_verification(EMAIL, "tok") is False  # gated
    _no_raw_email(caplog)


@pytest.mark.unit
def test_resend_unconfigured_log_masks_recipient(caplog):
    sender = ResendEmailSender("", "", allow_real_sends=True, opener=CaptureOpener())
    with caplog.at_level(logging.WARNING):
        assert sender.send_welcome(EMAIL) is False
    _no_raw_email(caplog)


@pytest.mark.unit
def test_resend_accepted_log_masks_but_payload_keeps_real_address(caplog):
    cap = CaptureOpener()
    sender = ResendEmailSender("re_key", "noreply@uplift.test",
                               allow_real_sends=True, opener=cap)
    with caplog.at_level(logging.INFO):
        assert sender.send_welcome(EMAIL, "tenant-1") is True
    _no_raw_email(caplog)
    # Masking is LOGS-ONLY: the API payload must still address the real recipient.
    (request,) = cap.requests
    assert json.loads(request.data.decode())["to"] == [EMAIL]


@pytest.mark.unit
def test_resend_failure_log_masks_recipient(caplog):
    sender = ResendEmailSender("re_key", "noreply@uplift.test",
                               allow_real_sends=True, opener=CaptureOpener(fail=True))
    with caplog.at_level(logging.WARNING):
        assert sender.send_verification(EMAIL, "tok") is False
    assert any("Resend send failed" in r.getMessage() for r in caplog.records)
    _no_raw_email(caplog)


# ---------------------------------------------------------------------------
# SnsSmsOtpSender — gate log + raised-error message mask; publish keeps the real number
# ---------------------------------------------------------------------------

class FakeSns:
    def __init__(self, fail=False):
        self.published = []
        self.fail = fail

    def publish(self, **kwargs):
        if self.fail:
            raise RuntimeError("sns down")
        self.published.append(kwargs)
        return {"MessageId": "m1"}


@pytest.mark.unit
def test_sms_draft_gate_log_masks_phone(caplog):
    sender = SnsSmsOtpSender(client=FakeSns())
    with caplog.at_level(logging.INFO):
        assert sender.send_otp(PHONE, "123456") is False  # gated
    assert all(PHONE not in r.getMessage() for r in caplog.records)
    assert any("***0100" in r.getMessage() for r in caplog.records)


@pytest.mark.unit
def test_sms_send_error_message_masks_phone():
    """SmsSendError messages get logged upstream — they must carry the masked number only."""
    sender = SnsSmsOtpSender(allow_real_sends=True, client=FakeSns(fail=True))
    with pytest.raises(SmsSendError) as e:
        sender.send_otp(PHONE, "123456")
    assert PHONE not in str(e.value)
    assert "***0100" in str(e.value)
    assert "sns down" in str(e.value)  # the transport reason survives


@pytest.mark.unit
def test_sms_publish_keeps_the_real_number():
    """Masking is LOGS-ONLY: SNS must still be handed the real E.164 number."""
    sns = FakeSns()
    sender = SnsSmsOtpSender(allow_real_sends=True, client=sns)
    assert sender.send_otp(PHONE, "424242") is True
    (kwargs,) = sns.published
    assert kwargs["PhoneNumber"] == PHONE
