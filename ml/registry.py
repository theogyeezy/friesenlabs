"""Per-tenant model registry + champion/challenger gate (Build Guide Phase 8, Step 46).

Each tenant has its own registry of versioned models with their held-out metrics. A new model only
promotes to champion if it beats the incumbent on held-out data (by a margin) — champion/challenger.
The injected store is the real per-tenant registry (e.g. SageMaker Model Registry / a table); offline
we use an in-memory fake.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# A challenger must beat the champion's AUC by at least this margin to promote (avoid churn on noise).
PROMOTION_MARGIN = 0.01


@dataclass
class ModelRecord:
    tenant_id: str
    version: int
    estimator_name: str
    metrics: dict
    model: Any                     # the fitted estimator (or a ref to its artifact in prod)
    is_champion: bool = False


@dataclass
class InMemoryRegistry:
    _by_tenant: dict[str, list[ModelRecord]] = field(default_factory=dict)

    def _versions(self, tenant_id: str) -> list[ModelRecord]:
        return self._by_tenant.setdefault(tenant_id, [])

    def register(self, tenant_id: str, estimator_name: str, metrics: dict, model: Any) -> ModelRecord:
        versions = self._versions(tenant_id)
        rec = ModelRecord(tenant_id, len(versions) + 1, estimator_name, metrics, model)
        versions.append(rec)
        return rec

    def champion(self, tenant_id: str) -> ModelRecord | None:
        return next((r for r in self._versions(tenant_id) if r.is_champion), None)

    def versions(self, tenant_id: str) -> list[ModelRecord]:
        return list(self._versions(tenant_id))


def evaluate_and_gate(registry: InMemoryRegistry, tenant_id: str, challenger: ModelRecord,
                      metric: str = "auc", margin: float = PROMOTION_MARGIN) -> bool:
    """Promote `challenger` to champion iff it beats the incumbent by `margin`. Returns True if promoted.

    With no incumbent, the first model that beats random (auc > 0.5) becomes champion.
    """
    champ = registry.champion(tenant_id)
    if champ is None:
        if challenger.metrics.get(metric, 0.0) > 0.5:
            challenger.is_champion = True
            return True
        return False
    if challenger.metrics.get(metric, 0.0) >= champ.metrics.get(metric, 0.0) + margin:
        champ.is_champion = False
        challenger.is_champion = True
        return True
    return False
