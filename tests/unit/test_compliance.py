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


# --------------------------------------------------------- timezone-aware quiet hours (audit P1)

def _sms(payload):
    return Action(name="send_sms", side_effecting=True, channel="sms", payload=payload)


@pytest.mark.unit
def test_sms_quiet_hours_computed_from_payload_timezone():
    from datetime import datetime, timezone
    # 03:30 UTC == 22:30 the previous evening in Chicago (CDT) — quiet hours there.
    at = datetime(2026, 6, 11, 3, 30, tzinfo=timezone.utc)
    blocked = validate(_sms({"consent": True, "timezone": "America/Chicago"}), now=at)
    assert blocked.ok is False and "quiet hours" in blocked.reason
    # The same instant is 13:30 in Tokyo — allowed.
    assert validate(_sms({"consent": True, "timezone": "Asia/Tokyo"}), now=at).ok is True


@pytest.mark.unit
def test_sms_timezone_beats_a_client_claimed_local_hour():
    from datetime import datetime, timezone
    at = datetime(2026, 6, 11, 3, 30, tzinfo=timezone.utc)  # 22:30 in Chicago
    lying = _sms({"consent": True, "timezone": "America/Chicago", "local_hour": 12})
    assert validate(lying, now=at).ok is False


@pytest.mark.unit
def test_sms_unknown_timezone_fails_closed():
    r = validate(_sms({"consent": True, "timezone": "Not/AZone"}))
    assert r.ok is False and "timezone" in r.reason


@pytest.mark.unit
def test_compliance_block_is_logged(caplog):
    a = Action(name="send_email", tenant_id="t1", side_effecting=True, channel="email",
               payload={"body": "no opt out"})
    with caplog.at_level("WARNING", logger="api.control.compliance"):
        assert validate(a).ok is False
    assert any("CAN-SPAM" in m and "send_email" in m for m in caplog.messages)
