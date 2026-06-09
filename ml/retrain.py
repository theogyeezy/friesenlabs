"""Scheduled retrain + drift (Build Guide Phase 8, Step 47).

The flywheel: more usage -> more labeled outcomes -> better per-tenant models -> stickier product.
EventBridge triggers periodic retrains as new outcomes accumulate; a drift check flags when the live
champion degrades. The EventBridge schedule itself is authored in infra/modules/cortex (not run);
this module is the orchestration + drift logic, testable offline.
"""
from __future__ import annotations

from . import features, train
from .registry import InMemoryRegistry, evaluate_and_gate

# Flag drift when the champion's recent live AUC falls this far below its registered (training) AUC.
DRIFT_TOLERANCE = 0.10


def retrain_tenant(registry: InMemoryRegistry, tenant_id: str, records: list[dict], *,
                   target: str = "booked", seed: int = 0) -> dict:
    """Train a challenger on the tenant's latest data and gate it against the champion."""
    X = features.featurize(records)
    y = features.labels(records, target=target)
    model = train.train(X, y, seed=seed)
    challenger = registry.register(tenant_id, model.estimator_name, model.metrics, model.estimator)
    promoted = evaluate_and_gate(registry, tenant_id, challenger)
    return {"version": challenger.version, "metrics": model.metrics, "promoted": promoted}


def drift_check(registry: InMemoryRegistry, tenant_id: str, recent_auc: float,
                tolerance: float = DRIFT_TOLERANCE) -> dict:
    """Compare the champion's recent live AUC to its registered AUC; flag if it degraded too far."""
    champ = registry.champion(tenant_id)
    if champ is None:
        return {"drift": False, "reason": "no champion"}
    registered = champ.metrics.get("auc", 0.5)
    drifted = recent_auc < registered - tolerance
    return {"drift": drifted, "registered_auc": registered, "recent_auc": recent_auc,
            "reason": "degraded beyond tolerance" if drifted else "ok"}
