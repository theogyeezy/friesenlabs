"""Per-tenant Cortex health — the payload for a future GET /cortex/health route.

No /cortex API seam exists yet (api/ is another lane's territory this cycle), so the JSON shape
lives here, fully testable: the route only has to bind the tenant from the verified JWT claim
(THE TRUST RULE) and return `cortex_health(registry, tenant_id, prediction_log)`.

Deliberately metadata-only: it reads the manifest listing (`registry.versions`), NEVER loads a
model artifact — a health probe must not deserialize blobs or get slower as models grow.
"""
from __future__ import annotations

from typing import Any

from .predictions import live_auc
from .retrain import DRIFT_TOLERANCE


def cortex_health(registry: Any, tenant_id: str, prediction_log: Any = None, *,
                  tolerance: float = DRIFT_TOLERANCE) -> dict:
    """One tenant's model health: champion + version count + live-AUC drift when evidence exists."""
    out: dict = {"tenant_id": str(tenant_id), "status": "no_registry", "champion": None,
                 "model_count": 0, "drift": None}
    if registry is None:
        return out

    versions = registry.versions(tenant_id)
    out["model_count"] = len(versions)
    champ = next((r for r in versions if r.is_champion), None)
    if champ is None:
        out["status"] = "no_champion"
        return out

    out["status"] = "serving"
    out["champion"] = {"version": champ.version, "estimator": champ.estimator_name,
                       "metrics": dict(champ.metrics)}

    if prediction_log is not None:
        live = live_auc(prediction_log, tenant_id)
        registered = champ.metrics.get("auc", 0.5)
        if live["auc"] is None:
            out["drift"] = {"drift": False, "recent_auc": None, "n_outcomes": live["n"],
                            "registered_auc": registered,
                            "reason": f"insufficient live evidence: {live['reason']}"}
        else:
            drifted = live["auc"] < registered - tolerance
            out["drift"] = {"drift": drifted, "recent_auc": live["auc"],
                            "n_outcomes": live["n"], "registered_auc": registered,
                            "reason": "degraded beyond tolerance" if drifted else "ok"}
            if drifted:
                out["status"] = "drifting"
    return out
