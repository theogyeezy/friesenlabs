"""Unit: run_model logs predictions to the prediction_log after a successful score.

Covers:
  - A successful score logs exactly one prediction via prediction_log.log().
  - A failure inside prediction_log.log() does NOT propagate — the score is still returned.
  - The no-model path (score=None) logs nothing.
  - No prediction_log injected (ctx.extra empty) is a clean no-op.
"""
from __future__ import annotations

import pytest

from agents.tools.base import ToolContext
from agents.tools.run_model import RunModel
from ml.registry import InMemoryRegistry
from ml.retrain import retrain_tenant

import random


# --------------------------------------------------------------------------- helpers


def _synthetic(n=300, seed=2):
    rng = random.Random(seed)
    recs = []
    for _ in range(n):
        amount = rng.uniform(0, 10000)
        acts = rng.randint(0, 20)
        has_email = rng.random() < 0.7
        score = amount / 10000 + acts / 20 + (0.3 if has_email else 0)
        recs.append({
            "amount": amount,
            "n_activities": acts,
            "days_since_created": rng.randint(0, 90),
            "email": "x@y.com" if has_email else None,
            "phone": None,
            "booked": 1 if score + rng.uniform(-0.3, 0.3) > 1.0 else 0,
        })
    return recs


class FakePredictionLog:
    """Minimal fake that records every call to log()."""

    def __init__(self):
        self.calls: list[dict] = []

    def log(self, tenant_id: str, *, deal_id, model_version: int,
            score: float, features=None) -> None:
        self.calls.append({
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "model_version": model_version,
            "score": score,
            "features": features,
        })


class BrokenPredictionLog:
    """Fake whose log() always raises — used to verify best-effort wrapping."""

    def log(self, *args, **kwargs) -> None:
        raise RuntimeError("simulated log failure")


# --------------------------------------------------------------------------- tests


@pytest.mark.unit
def test_successful_score_logs_exactly_one_prediction():
    reg = InMemoryRegistry()
    retrain_tenant(reg, "t1", _synthetic(), seed=0)

    pred_log = FakePredictionLog()
    ctx = ToolContext(
        tenant_id="t1",
        cortex=reg,
        extra={"prediction_log": pred_log},
    )
    record = {"amount": 9000, "n_activities": 18, "email": "a@b.com", "deal_id": "deal-42"}
    out = RunModel().invoke(ctx, record=record)

    # Score must be a valid probability.
    assert out["result"]["score"] is not None
    assert 0.0 <= out["result"]["score"] <= 1.0

    # Exactly one prediction logged.
    assert len(pred_log.calls) == 1
    logged = pred_log.calls[0]
    assert logged["tenant_id"] == "t1"
    assert logged["model_version"] == reg.champion("t1").version
    assert logged["score"] == out["result"]["score"]
    assert logged["deal_id"] == "deal-42"


@pytest.mark.unit
def test_log_failure_does_not_break_scoring():
    reg = InMemoryRegistry()
    retrain_tenant(reg, "t1", _synthetic(), seed=0)

    broken_log = BrokenPredictionLog()
    ctx = ToolContext(
        tenant_id="t1",
        cortex=reg,
        extra={"prediction_log": broken_log},
    )
    record = {"amount": 5000, "n_activities": 10, "email": "b@c.com"}
    # Must NOT raise despite the broken log.
    out = RunModel().invoke(ctx, record=record)
    assert out["result"]["score"] is not None
    assert 0.0 <= out["result"]["score"] <= 1.0


@pytest.mark.unit
def test_no_model_logs_nothing():
    reg = InMemoryRegistry()
    # No model trained for tenant — champion returns None.

    pred_log = FakePredictionLog()
    ctx = ToolContext(
        tenant_id="t1",
        cortex=reg,
        extra={"prediction_log": pred_log},
    )
    out = RunModel().invoke(ctx, record={"amount": 1000})
    assert out["result"]["score"] is None
    assert pred_log.calls == []


@pytest.mark.unit
def test_no_prediction_log_injected_is_noop():
    reg = InMemoryRegistry()
    retrain_tenant(reg, "t1", _synthetic(), seed=0)

    # ctx.extra has no 'prediction_log' key — must score cleanly without error.
    ctx = ToolContext(tenant_id="t1", cortex=reg)
    out = RunModel().invoke(ctx, record={"amount": 9000, "n_activities": 18, "email": "a@b.com"})
    assert out["result"]["score"] is not None
    assert 0.0 <= out["result"]["score"] <= 1.0
