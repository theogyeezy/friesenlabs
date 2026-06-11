"""Scheduled retrain + drift (Build Guide Phase 8, Step 47).

The flywheel: more usage -> more labeled outcomes -> better per-tenant models -> stickier product.
EventBridge triggers periodic retrains as new outcomes accumulate; a drift check flags when the live
champion degrades. The EventBridge schedule itself is authored in infra/modules/cortex (not run);
this module is the orchestration + drift logic, testable offline.

The actually-invokable entrypoint is `run_scheduled_retrain` (CLI: scripts/ml/retrain_tenant.py):
load the tenant's labeled records (ml/data_loader.py) -> train champion/challenger with a held-out
AUC bake-off -> promote only on improvement (registry gate) -> sync closed-deal outcomes into the
prediction log -> compute LIVE drift from real (score, outcome) pairs.
"""
from __future__ import annotations

from typing import Any

from . import features, train
from .predictions import live_auc
from .registry import Registry, evaluate_and_gate

# Flag drift when the champion's recent live AUC falls this far below its registered (training) AUC.
DRIFT_TOLERANCE = 0.10

# Below this many labeled (closed) deals, a per-tenant model is noise — the scheduled retrain
# skips honestly instead of registering junk versions.
MIN_TRAINING_RECORDS = 20


def retrain_tenant(registry: Registry, tenant_id: str, records: list[dict], *,
                   target: str = "booked", seed: int = 0) -> dict:
    """Train a challenger on the tenant's latest data and gate it against the champion.

    `registry` is the injection seam: the scheduled retrain job passes the PERSISTENT registry
    (`ml.registry.registry_from_env()`), so a gate-approved promotion here is durable and the new
    champion loads in any other process (`run_model` in the worker). Offline tests pass
    `InMemoryRegistry` — both implement the same register/champion/set_champion protocol.
    """
    X = features.featurize(records)
    y = features.labels(records, target=target)
    model = train.train(X, y, seed=seed)
    challenger = registry.register(tenant_id, model.estimator_name, model.metrics, model.estimator)
    promoted = evaluate_and_gate(registry, tenant_id, challenger)
    return {"version": challenger.version, "metrics": model.metrics, "promoted": promoted}


def drift_check(registry: Registry, tenant_id: str, recent_auc: float,
                tolerance: float = DRIFT_TOLERANCE) -> dict:
    """Compare the champion's recent live AUC to its registered AUC; flag if it degraded too far."""
    champ = registry.champion(tenant_id)
    if champ is None:
        return {"drift": False, "reason": "no champion"}
    registered = champ.metrics.get("auc", 0.5)
    drifted = recent_auc < registered - tolerance
    return {"drift": drifted, "registered_auc": registered, "recent_auc": recent_auc,
            "reason": "degraded beyond tolerance" if drifted else "ok"}


def live_drift_check(registry: Registry, tenant_id: str, prediction_log: Any,
                     tolerance: float = DRIFT_TOLERANCE) -> dict:
    """Drift with REAL inputs: recent live AUC computed from the tenant's logged predictions +
    resolved outcomes (ml/predictions.py), then compared to the champion's registered AUC.

    Too few resolved outcomes / a single outcome class = "no evidence" (drift=False with the
    reason surfaced), never a fabricated number.
    """
    live = live_auc(prediction_log, tenant_id)
    if live["auc"] is None:
        return {"drift": False, "recent_auc": None, "n_outcomes": live["n"],
                "reason": f"insufficient live evidence: {live['reason']}"}
    out = drift_check(registry, tenant_id, recent_auc=live["auc"], tolerance=tolerance)
    out["n_outcomes"] = live["n"]
    return out


def sync_outcomes(prediction_log: Any, tenant_id: str, records: list[dict], *,
                  target: str = "booked") -> int:
    """Backfill resolved outcomes onto logged predictions from the loader's CLOSED-deal records.

    Every record carries `deal_id` + the label; predictions logged for that deal while it was
    open get their outcome resolved — this is what makes the live AUC honest. Returns the number
    of prediction rows resolved.
    """
    resolved = 0
    for rec in records:
        deal_id = rec.get("deal_id")
        if not deal_id:
            continue
        resolved += prediction_log.record_outcome(tenant_id, deal_id, 1 if rec.get(target) else 0)
    return resolved


def run_scheduled_retrain(registry: Registry, loader: Any, tenant_id: str, *,
                          prediction_log: Any = None, target: str = "booked", seed: int = 0,
                          min_records: int = MIN_TRAINING_RECORDS) -> dict:
    """THE retrain entrypoint (scripts/ml/retrain_tenant.py; EventBridge target when scheduled).

    loader.load(tenant_id) -> labeled records -> retrain_tenant (bake-off + held-out AUC +
    promote-only-on-improvement gate, metrics written to the registry) -> outcome sync + live
    drift when a prediction log is wired. Skips honestly (no junk versions) on thin or
    single-class data.
    """
    records = loader.load(tenant_id)
    result: dict = {"tenant_id": str(tenant_id), "n_records": len(records)}

    if len(records) < min_records:
        result.update(status="skipped",
                      reason=f"only {len(records)} labeled records (< {min_records})")
        return result
    classes = set(features.labels(records, target=target))
    if len(classes) < 2:
        result.update(status="skipped",
                      reason="single-class labels — nothing separable to learn")
        return result

    result.update(status="trained", **retrain_tenant(registry, tenant_id, records,
                                                     target=target, seed=seed))
    if prediction_log is not None:
        result["outcomes_synced"] = sync_outcomes(prediction_log, tenant_id, records,
                                                  target=target)
        result["drift"] = live_drift_check(registry, tenant_id, prediction_log)
    return result
