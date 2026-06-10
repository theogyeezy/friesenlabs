"""Unit: SnsSmsOtpSender — DRAFT-GATE default, publish shape, SmsSendError, lazy client."""
import pytest

from signup.sms_sender import SmsSendError, SnsSmsOtpSender


class FakeSns:
    def __init__(self, fail=False):
        self.published = []
        self.fail = fail

    def publish(self, **kwargs):
        if self.fail:
            raise RuntimeError("sns down")
        self.published.append(kwargs)
        return {"MessageId": "m1"}


def _poison_factory(region):
    raise AssertionError("client factory must not be called")


# ---------------- DRAFT-GATE blocks by default ----------------
@pytest.mark.unit
def test_gate_blocks_by_default_no_client_no_publish():
    sns = FakeSns()
    sender = SnsSmsOtpSender(client=sns, client_factory=_poison_factory)
    assert sender.allow_real_sends is False  # explicit flag, default False
    assert sender.send_otp("+15555550100", "123456") is False
    assert sns.published == []  # gate refused before any AWS call


@pytest.mark.unit
def test_gate_never_constructs_a_client():
    sender = SnsSmsOtpSender(client_factory=_poison_factory)  # no injected client either
    assert sender.send_otp("+15555550100", "123456") is False
    assert sender._client is None  # lazy: nothing built while gated


# ---------------- publish payload shape (transport mocked) ----------------
@pytest.mark.unit
def test_publish_shape_phone_code_and_transactional_route():
    sns = FakeSns()
    sender = SnsSmsOtpSender(allow_real_sends=True, client=sns)
    assert sender.send_otp("+15555550100", "424242") is True
    (kwargs,) = sns.published
    assert kwargs["PhoneNumber"] == "+15555550100"
    assert "424242" in kwargs["Message"]
    assert "expires" in kwargs["Message"]
    attr = kwargs["MessageAttributes"]["AWS.SNS.SMS.SMSType"]
    assert attr == {"DataType": "String", "StringValue": "Transactional"}


# ---------------- failure contract: SmsSendError ----------------
@pytest.mark.unit
def test_publish_failure_raises_sms_send_error():
    sender = SnsSmsOtpSender(allow_real_sends=True, client=FakeSns(fail=True))
    with pytest.raises(SmsSendError) as e:
        sender.send_otp("+15555550100", "123456")
    assert "sns down" in str(e.value)


@pytest.mark.unit
def test_unconfigured_client_raises_clean_error():
    def no_boto3(region):
        raise ImportError("No module named 'boto3'")

    sender = SnsSmsOtpSender(allow_real_sends=True, client_factory=no_boto3)
    with pytest.raises(SmsSendError) as e:
        sender.send_otp("+15555550100", "123456")
    assert "SNS client unavailable" in str(e.value)


# ---------------- lazy client construction + reuse ----------------
@pytest.mark.unit
def test_factory_called_once_then_reused():
    sns = FakeSns()
    calls = []

    def factory(region):
        calls.append(region)
        return sns

    sender = SnsSmsOtpSender("us-east-1", allow_real_sends=True, client_factory=factory)
    assert sender._client is None  # nothing imported/built at construction
    sender.send_otp("+15555550100", "111111")
    sender.send_otp("+15555550100", "222222")
    assert calls == ["us-east-1"]  # built lazily exactly once
    assert len(sns.published) == 2


# ---------------- import safety ----------------
@pytest.mark.unit
def test_import_does_not_import_boto3():
    import importlib
    import sys

    import signup.sms_sender as mod

    before = "boto3" in sys.modules
    importlib.reload(mod)
    # reload must not pull boto3 in as a side effect (lazy import contract)
    assert ("boto3" in sys.modules) == before
