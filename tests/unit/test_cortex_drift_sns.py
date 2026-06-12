"""Unit: the Cortex drift SNS publish is single-sourced (the dedup-fix contract).

After the double-publish fix, `ml.retrain.run_scheduled_retrain` ONLY computes the drift verdict
(returned in `result["drift"]`) — it never pages. The one and only drift-publish surface is
`ml.drift_alert.DriftNotifier.notify`, driven by the retrain fan-out (scripts/ml/retrain_all.py).
These tests pin that contract so the orchestrator can't grow a second publish again:

- `ml.retrain` carries no SNS-publish helpers (`_publish_drift` / `_sns_client` are gone).
- `run_scheduled_retrain` touches no SNS by itself.
- The integrated fan-out path (run_scheduled_retrain -> DriftNotifier.notify) publishes EXACTLY
  ONCE per drifting tenant.
- A no-drift verdict publishes nothing.
"""
from __future__ import annotations

import importlib
import json
import random

import pytest

from ml.data_loader import StaticTrainingDataLoader
from ml.drift_alert import DriftNotifier
from ml.predictions import MIN_LIVE_SAMPLES, InMemoryPredictionLog
from ml.registry import InMemoryRegistry
from ml.retrain import run_scheduled_retrain

retrain_all = importlib.import_module("scripts.ml.retrain_all")

DRIFT_TOPIC_ARN = "arn:aws:sns:us-east-1:123456789:uplift-cortex-drift"


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


def _train_then_drift(reg: InMemoryRegistry, log: InMemoryPredictionLog, tenant: str,
                      loader: StaticTrainingDataLoader) -> None:
    """Train a champion, then feed anti-correlated live evidence so live drift fires."""
    run_scheduled_retrain(reg, loader, tenant, seed=0)
    for i in range(MIN_LIVE_SAMPLES * 2):
        outcome = i % 2
        log.log(tenant, deal_id=f"live-{i}", model_version=1, score=0.9 - 0.8 * outcome)
        log.record_outcome(tenant, f"live-{i}", outcome)


# ---------------------------------------------------------------------------
# The orchestrator no longer carries an SNS-publish surface
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_retrain_module_has_no_sns_publish_helpers():
    """The drift double-publish is removed: ml.retrain owns no SNS publish helpers anymore."""
    import ml.retrain as retrain_mod

    assert not hasattr(retrain_mod, "_publish_drift")
    assert not hasattr(retrain_mod, "_sns_client")


@pytest.mark.unit
def test_run_scheduled_retrain_computes_drift_but_never_publishes(monkeypatch):
    """run_scheduled_retrain returns the drift verdict; it imports/uses no SNS client."""
    # Even with a topic ARN configured, the orchestrator must not page on its own.
    monkeypatch.setenv("CORTEX_DRIFT_TOPIC_ARN", DRIFT_TOPIC_ARN)
    reg = InMemoryRegistry()
    log = InMemoryPredictionLog()
    loader = StaticTrainingDataLoader(_synthetic(n=300))
    _train_then_drift(reg, log, "t-orch", loader)

    result = run_scheduled_retrain(reg, loader, "t-orch", prediction_log=log, seed=0)

    # Verdict is computed and surfaced — but nothing was published (no notifier in this path).
    assert result["drift"]["drift"] is True


# ---------------------------------------------------------------------------
# The single publish surface: exactly one page per drifting tenant
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_exactly_one_publish_per_drifting_tenant():
    """The integrated fan-out path (retrain -> DriftNotifier.notify) publishes ONCE per drift."""
    reg = InMemoryRegistry()
    log = InMemoryPredictionLog()
    loader = StaticTrainingDataLoader(_synthetic(n=300))
    _train_then_drift(reg, log, "t-drift", loader)

    sns = FakeSns()
    notifier = DriftNotifier(DRIFT_TOPIC_ARN, sns=sns)
    out = retrain_all.retrain_one(
        reg, loader, "t-drift", prediction_log=log, seed=0, drift_notifier=notifier
    )

    assert out["ok"] is True
    assert out.get("drift_alerted") is True
    assert out["result"]["drift"]["drift"] is True
    # The crux of the dedup fix: ONE SNS publish for this drifting tenant, not two.
    assert len(sns.published) == 1
    body = json.loads(sns.published[0]["Message"])
    assert body["tenant_id"] == "t-drift"
    assert "t-drift" in sns.published[0]["Subject"]


@pytest.mark.unit
def test_multiple_drifting_tenants_publish_once_each():
    """Two drifting tenants -> exactly two publishes total (one apiece), never doubled."""
    sns = FakeSns()
    notifier = DriftNotifier(DRIFT_TOPIC_ARN, sns=sns)
    for tenant in ("t-a", "t-b"):
        reg = InMemoryRegistry()
        log = InMemoryPredictionLog()
        loader = StaticTrainingDataLoader(_synthetic(n=300))
        _train_then_drift(reg, log, tenant, loader)
        retrain_all.retrain_one(
            reg, loader, tenant, prediction_log=log, seed=0, drift_notifier=notifier
        )

    assert len(sns.published) == 2
    assert {json.loads(c["Message"])["tenant_id"] for c in sns.published} == {"t-a", "t-b"}


@pytest.mark.unit
def test_no_publish_when_no_drift():
    """A healthy (no-drift / insufficient-evidence) verdict pages no one."""
    reg = InMemoryRegistry()
    loader = StaticTrainingDataLoader(_synthetic(n=300))
    run_scheduled_retrain(reg, loader, "t-ok", seed=0)

    sns = FakeSns()
    notifier = DriftNotifier(DRIFT_TOPIC_ARN, sns=sns)
    out = retrain_all.retrain_one(
        reg, loader, "t-ok", prediction_log=InMemoryPredictionLog(), seed=0, drift_notifier=notifier
    )

    assert out["ok"] is True
    assert "drift_alerted" not in out
    assert sns.published == []
