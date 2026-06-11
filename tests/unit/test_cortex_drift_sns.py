"""Unit: ml/retrain.py SNS drift publish — fake SNS client, no boto3, no network.

Covers:
- `_publish_drift` sends a correctly shaped SNS message when CORTEX_DRIFT_TOPIC_ARN is set.
- `_publish_drift` is a no-op (no publish) when CORTEX_DRIFT_TOPIC_ARN is unset or blank.
- `run_scheduled_retrain` calls `_publish_drift` (and therefore SNS) when live drift fires.
- `run_scheduled_retrain` does NOT publish when there is no drift.
- A boto3 failure inside `_publish_drift` is swallowed (non-fatal).
"""
from __future__ import annotations

import json
import random

import pytest

from ml.data_loader import StaticTrainingDataLoader
from ml.predictions import MIN_LIVE_SAMPLES, InMemoryPredictionLog
from ml.registry import InMemoryRegistry
from ml.retrain import _publish_drift, run_scheduled_retrain


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeSns:
    """A minimal SNS fake that records publish() calls."""

    def __init__(self, *, raise_on_publish: bool = False):
        self.published: list[dict] = []
        self._raise = raise_on_publish

    def publish(self, **kw: object) -> dict:
        if self._raise:
            raise RuntimeError("SNS unavailable")
        self.published.append(kw)
        return {"MessageId": "fake-msg-id"}


def _synthetic(n: int = 300, seed: int = 2) -> list[dict]:
    """Return `n` labeled synthetic CRM records (separable)."""
    rng = random.Random(seed)
    recs = []
    for i in range(n):
        amount = rng.uniform(0, 10_000)
        acts = rng.randint(0, 20)
        has_email = rng.random() < 0.7
        signal = amount / 10_000 + acts / 20 + (0.3 if has_email else 0)
        booked = 1 if signal + rng.uniform(-0.3, 0.3) > 1.0 else 0
        recs.append({
            "deal_id": f"d{i}",
            "amount": amount,
            "n_activities": acts,
            "days_since_created": rng.randint(0, 90),
            "email": "x@y.com" if has_email else None,
            "phone": None,
            "booked": booked,
        })
    return recs


DRIFT_VERDICT = {
    "drift": True,
    "registered_auc": 0.81,
    "recent_auc": 0.62,
    "n_outcomes": 40,
    "reason": "degraded beyond tolerance",
}

NO_DRIFT_VERDICT = {
    "drift": False,
    "registered_auc": 0.81,
    "recent_auc": 0.80,
    "n_outcomes": 40,
    "reason": "ok",
}


# ---------------------------------------------------------------------------
# _publish_drift unit tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_publish_drift_sends_correctly_shaped_message(monkeypatch):
    """When CORTEX_DRIFT_TOPIC_ARN is set, publish() is called with the right payload."""
    monkeypatch.setenv("CORTEX_DRIFT_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789:uplift-cortex-drift")
    sns = FakeSns()

    _publish_drift("tenant-42", DRIFT_VERDICT, sns_client=sns)

    assert len(sns.published) == 1
    call = sns.published[0]
    # TopicArn forwarded verbatim
    assert call["TopicArn"] == "arn:aws:sns:us-east-1:123456789:uplift-cortex-drift"
    # Subject contains the tenant id and is within the SNS 100-char limit
    assert "tenant-42" in call["Subject"]
    assert len(call["Subject"]) <= 100
    # Message is valid JSON with required fields
    body = json.loads(call["Message"])
    assert body["tenant_id"] == "tenant-42"
    assert body["metric"] == "live_auc"
    assert body["registered_auc"] == pytest.approx(0.81)
    assert body["recent_auc"] == pytest.approx(0.62)
    assert body["drift_magnitude"] == pytest.approx(0.19, abs=1e-4)
    assert "timestamp" in body
    assert body["n_outcomes"] == 40
    assert body["reason"] == "degraded beyond tolerance"


@pytest.mark.unit
def test_publish_drift_skipped_when_env_unset(monkeypatch):
    """No CORTEX_DRIFT_TOPIC_ARN → publish() must never be called."""
    monkeypatch.delenv("CORTEX_DRIFT_TOPIC_ARN", raising=False)
    sns = FakeSns()

    _publish_drift("tenant-42", DRIFT_VERDICT, sns_client=sns)

    assert sns.published == []


@pytest.mark.unit
def test_publish_drift_skipped_when_env_blank(monkeypatch):
    """Blank / whitespace CORTEX_DRIFT_TOPIC_ARN also skips — not just missing."""
    monkeypatch.setenv("CORTEX_DRIFT_TOPIC_ARN", "   ")
    sns = FakeSns()

    _publish_drift("tenant-42", DRIFT_VERDICT, sns_client=sns)

    assert sns.published == []


@pytest.mark.unit
def test_publish_drift_swallows_boto3_exception(monkeypatch):
    """A boto3/SNS failure must not propagate — the retrain job continues."""
    monkeypatch.setenv("CORTEX_DRIFT_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789:uplift-cortex-drift")
    sns = FakeSns(raise_on_publish=True)

    # Must not raise
    _publish_drift("tenant-boom", DRIFT_VERDICT, sns_client=sns)


@pytest.mark.unit
def test_publish_drift_long_tenant_id_subject_truncated(monkeypatch):
    """Subject line must never exceed 100 chars even with a very long tenant id."""
    monkeypatch.setenv("CORTEX_DRIFT_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789:uplift-cortex-drift")
    sns = FakeSns()

    _publish_drift("x" * 200, DRIFT_VERDICT, sns_client=sns)

    assert len(sns.published[0]["Subject"]) <= 100


# ---------------------------------------------------------------------------
# Integration: run_scheduled_retrain calls SNS when live drift fires
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_run_scheduled_retrain_publishes_when_drift_detected(monkeypatch):
    """Full retrain path: anti-correlated live scores produce drift → SNS publish fires."""
    monkeypatch.setenv(
        "CORTEX_DRIFT_TOPIC_ARN",
        "arn:aws:sns:us-east-1:123456789:uplift-cortex-drift",
    )
    sns = FakeSns()
    # Patch _publish_drift to inject our fake SNS client
    import ml.retrain as retrain_mod

    _orig = retrain_mod._publish_drift

    def _patched(tenant_id: str, drift: dict, *, sns_client=None) -> None:
        return _orig(tenant_id, drift, sns_client=sns)

    monkeypatch.setattr(retrain_mod, "_publish_drift", _patched)

    reg = InMemoryRegistry()
    log = InMemoryPredictionLog()
    records = _synthetic(n=300)
    # First, train a champion
    from ml.retrain import run_scheduled_retrain
    run_scheduled_retrain(reg, StaticTrainingDataLoader(records), "t-sns", seed=0)

    # Now run retrain with anti-correlated live evidence (scores predict opposite of outcome)
    for i in range(MIN_LIVE_SAMPLES * 2):
        outcome = i % 2
        log.log("t-sns", deal_id=f"live-{i}", model_version=1, score=0.9 - 0.8 * outcome)
        log.record_outcome("t-sns", f"live-{i}", outcome)

    result = run_scheduled_retrain(
        reg,
        StaticTrainingDataLoader(records),
        "t-sns",
        prediction_log=log,
        seed=0,
    )

    assert result["drift"]["drift"] is True
    assert len(sns.published) == 1
    body = json.loads(sns.published[0]["Message"])
    assert body["tenant_id"] == "t-sns"
    assert body["metric"] == "live_auc"
    assert "timestamp" in body


@pytest.mark.unit
def test_run_scheduled_retrain_does_not_publish_when_no_drift(monkeypatch):
    """When the live AUC is healthy, no SNS message must be sent."""
    monkeypatch.setenv(
        "CORTEX_DRIFT_TOPIC_ARN",
        "arn:aws:sns:us-east-1:123456789:uplift-cortex-drift",
    )
    sns = FakeSns()
    import ml.retrain as retrain_mod

    _orig = retrain_mod._publish_drift

    def _patched(tenant_id: str, drift: dict, *, sns_client=None) -> None:
        return _orig(tenant_id, drift, sns_client=sns)

    monkeypatch.setattr(retrain_mod, "_publish_drift", _patched)

    reg = InMemoryRegistry()
    # No prediction log → live_drift_check returns "insufficient evidence" (drift=False)
    result = run_scheduled_retrain(
        reg,
        StaticTrainingDataLoader(_synthetic()),
        "t-nodrift",
        prediction_log=InMemoryPredictionLog(),
        seed=0,
    )

    assert result["drift"]["drift"] is False
    assert sns.published == []


@pytest.mark.unit
def test_run_scheduled_retrain_publishes_without_env_var_set_is_noop(monkeypatch):
    """Without CORTEX_DRIFT_TOPIC_ARN even a real drift verdict must not cause an error."""
    monkeypatch.delenv("CORTEX_DRIFT_TOPIC_ARN", raising=False)

    reg = InMemoryRegistry()
    log = InMemoryPredictionLog()
    records = _synthetic(n=300)
    run_scheduled_retrain(reg, StaticTrainingDataLoader(records), "t-noenv", seed=0)

    for i in range(MIN_LIVE_SAMPLES * 2):
        outcome = i % 2
        log.log("t-noenv", deal_id=f"live-{i}", model_version=1, score=0.9 - 0.8 * outcome)
        log.record_outcome("t-noenv", f"live-{i}", outcome)

    # Must not raise even though drift fires and no topic ARN is configured
    result = run_scheduled_retrain(
        reg,
        StaticTrainingDataLoader(records),
        "t-noenv",
        prediction_log=log,
        seed=0,
    )
    assert result["drift"]["drift"] is True  # drift fired, but silently skipped SNS
