"""Unit: run_model tool serves the tenant champion; retrain + drift orchestration."""
import random

import pytest

from agents.tools.base import ToolContext
from agents.tools.run_model import RunModel
from ml.registry import InMemoryRegistry
from ml.retrain import drift_check, retrain_tenant


def _synthetic(n=300, seed=2):
    rng = random.Random(seed)
    recs = []
    for _ in range(n):
        amount = rng.uniform(0, 10000)
        acts = rng.randint(0, 20)
        has_email = rng.random() < 0.7
        score = amount / 10000 + acts / 20 + (0.3 if has_email else 0)
        recs.append({"amount": amount, "n_activities": acts, "days_since_created": rng.randint(0, 90),
                     "email": "x@y.com" if has_email else None, "phone": None,
                     "booked": 1 if score + rng.uniform(-0.3, 0.3) > 1.0 else 0})
    return recs


@pytest.mark.unit
def test_retrain_registers_and_promotes_first_model():
    reg = InMemoryRegistry()
    out = retrain_tenant(reg, "t1", _synthetic(), seed=0)
    assert out["promoted"] is True
    assert reg.champion("t1").version == out["version"]


@pytest.mark.unit
def test_run_model_scores_via_champion_tenant_scoped():
    reg = InMemoryRegistry()
    retrain_tenant(reg, "t1", _synthetic(), seed=0)
    ctx = ToolContext(tenant_id="t1", cortex=reg)
    out = RunModel().invoke(ctx, record={"amount": 9000, "n_activities": 18, "email": "a@b.com"})
    assert out["result"]["score"] is not None
    assert 0.0 <= out["result"]["score"] <= 1.0
    # A tenant with no model gets a clear no-model response (never another tenant's model).
    out2 = RunModel().invoke(ToolContext(tenant_id="t2", cortex=reg), record={"amount": 1})
    assert out2["result"]["score"] is None


@pytest.mark.unit
def test_run_model_is_auto_policy():
    from agents.tools.base import Policy
    assert RunModel().policy is Policy.AUTO


@pytest.mark.unit
def test_drift_check_flags_degradation():
    reg = InMemoryRegistry()
    retrain_tenant(reg, "t1", _synthetic(), seed=0)
    registered = reg.champion("t1").metrics["auc"]
    assert drift_check(reg, "t1", recent_auc=registered)["drift"] is False
    assert drift_check(reg, "t1", recent_auc=registered - 0.25)["drift"] is True
