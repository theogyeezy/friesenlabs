"""Unit: autonomy levels L0-L3 decide auto vs approve correctly."""
import pytest

from api.control.autonomy import AutonomyConfig, Thresholds, decide, resolve
from api.control.types import Action, Decision, Level


def _cfg(level, **th):
    return AutonomyConfig(default_level=level, thresholds=Thresholds(**th) if th else None)


READONLY = Action(name="read_crm", side_effecting=False)
SEND = Action(name="send_email", side_effecting=True, channel="email", value_at_stake=None)


@pytest.mark.unit
def test_readonly_always_auto():
    for lvl in Level:
        assert decide(lvl, READONLY, _cfg(lvl)) is Decision.AUTO


@pytest.mark.unit
def test_l0_suggest_only():
    assert decide(Level.L0, SEND, _cfg(Level.L0)) is Decision.APPROVE


@pytest.mark.unit
def test_l1_ask_first():
    assert decide(Level.L1, SEND, _cfg(Level.L1)) is Decision.APPROVE


@pytest.mark.unit
def test_l2_acts_under_threshold_approves_above():
    cfg = _cfg(Level.L2, max_auto_value=1000.0, max_discount=0.10)
    under = Action(name="issue_quote", side_effecting=True, value_at_stake=500.0)
    over = Action(name="issue_quote", side_effecting=True, value_at_stake=5000.0)
    discounted = Action(name="issue_quote", side_effecting=True, value_at_stake=100.0, discount=0.25)
    assert decide(Level.L2, under, cfg) is Decision.AUTO
    assert decide(Level.L2, over, cfg) is Decision.APPROVE
    assert decide(Level.L2, discounted, cfg) is Decision.APPROVE  # discount over limit


@pytest.mark.unit
def test_l2_value_less_side_effect_requires_approval():
    # A side-effecting action with no declared value-at-stake must NOT auto-execute under L2.
    cfg = _cfg(Level.L2, max_auto_value=1000.0)
    no_value = Action(name="send_email", side_effecting=True, value_at_stake=None)
    assert decide(Level.L2, no_value, cfg) is Decision.APPROVE


@pytest.mark.unit
def test_l3_autos_except_flagged():
    cfg = _cfg(Level.L3)
    assert decide(Level.L3, SEND, cfg) is Decision.AUTO
    flagged = Action(name="send_email", side_effecting=True, flagged=True)
    assert decide(Level.L3, flagged, cfg) is Decision.APPROVE


@pytest.mark.unit
def test_resolve_override_precedence():
    cfg = AutonomyConfig(default_level=Level.L1, overrides={("nadia", "t1"): Level.L3, "t1": Level.L0})
    assert resolve(cfg, "nadia", "t1") is Level.L3   # agent+tenant most specific
    assert resolve(cfg, "scout", "t1") is Level.L0   # tenant-level
    assert resolve(cfg, "scout", "t2") is Level.L1   # default
