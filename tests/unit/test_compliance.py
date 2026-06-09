"""Unit: the compliance validator blocks non-compliant actions before Greenlight."""
import pytest

from api.control.compliance import ComplianceResult, validate
from api.control.types import Action


@pytest.mark.unit
def test_sms_requires_consent():
    a = Action(name="send_sms", side_effecting=True, channel="sms", payload={"local_hour": 12})
    assert validate(a).ok is False  # no consent


@pytest.mark.unit
def test_sms_quiet_hours():
    base = {"consent": True}
    ok = Action(name="send_sms", side_effecting=True, channel="sms", payload={**base, "local_hour": 12})
    late = Action(name="send_sms", side_effecting=True, channel="sms", payload={**base, "local_hour": 22})
    early = Action(name="send_sms", side_effecting=True, channel="sms", payload={**base, "local_hour": 6})
    assert validate(ok).ok is True
    assert validate(late).ok is False
    assert validate(early).ok is False


@pytest.mark.unit
def test_email_requires_unsubscribe():
    bad = Action(name="send_email", side_effecting=True, channel="email", payload={"body": "buy now"})
    good = Action(name="send_email", side_effecting=True, channel="email", payload={"body": "hi, unsubscribe here"})
    flagged = Action(name="send_email", side_effecting=True, channel="email",
                     payload={"body": "hi", "has_unsubscribe": True})
    assert validate(bad).ok is False
    assert validate(good).ok is True
    assert validate(flagged).ok is True


@pytest.mark.unit
def test_readonly_passes():
    assert validate(Action(name="read_crm", side_effecting=False)).ok is True


@pytest.mark.unit
def test_critic_pass_for_regulated_verticals():
    a = Action(name="send_email", side_effecting=True, channel="email", payload={"body": "x unsubscribe"})

    def critic(action):
        return ComplianceResult(False, "UPL: contains legal advice")

    assert validate(a, critic=critic).ok is False
