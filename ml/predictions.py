"""Prediction log — drift honesty for Cortex (predictions + outcomes -> live AUC).

`drift_check` compares the champion's REGISTERED (training-time) AUC against its RECENT LIVE
AUC — but nothing ever produced that live number. This module is the producer: score-time
predictions land in the RLS-FORCEd `predictions` table (db/schema.sql), outcomes are backfilled
when the deal closes (the retrain entrypoint syncs them from the training loader's closed-deal
records), and `live_auc` turns (score, outcome) pairs into the real input the drift check needs.

Two implementations share one protocol (log / record_outcome / scored_outcomes):
  * InMemoryPredictionLog — offline/test fake (process-local).
  * PgPredictionLog — durable, tenant-scoped via the pooled per-op
    `SET LOCAL app.current_tenant` transaction (ml/pg.py) as the non-owner crm_app role; RLS
    scopes every row (the WITH CHECK policy also forces the inserted tenant_id to match the
    GUC, which is set from the verified claim — THE TRUST RULE).
"""
from __future__ import annotations

import json
from typing import Any

from . import metrics
from .pg import PgTenantOps, dict_rows

# Below this many resolved (score, outcome) pairs, a "live AUC" is statistical noise — drift
# checks report insufficient data instead of guessing.
MIN_LIVE_SAMPLES = 20

DEFAULT_RECENT_LIMIT = 1000


class InMemoryPredictionLog:
    """Process-local prediction log — the offline/test fake. Same protocol as PgPredictionLog."""

    def __init__(self):
        self._by_tenant: dict[str, list[dict]] = {}

    def _rows(self, tenant_id: str) -> list[dict]:
        return self._by_tenant.setdefault(str(tenant_id), [])

    def log(self, tenant_id: str, *, deal_id: str | None, model_version: int,
            score: float, features: dict | None = None) -> None:
        self._rows(tenant_id).append({
            "deal_id": str(deal_id) if deal_id is not None else None,
            "model_version": int(model_version),
            "score": float(score),
            "features": dict(features or {}),
            "outcome": None,
        })

    def record_outcome(self, tenant_id: str, deal_id: str, outcome: int) -> int:
        """Resolve outcome for every still-open prediction on `deal_id`; returns rows updated."""
        updated = 0
        for row in self._rows(tenant_id):
            if row["deal_id"] == str(deal_id) and row["outcome"] is None:
                row["outcome"] = int(outcome)
                updated += 1
        return updated

    def scored_outcomes(self, tenant_id: str,
                        limit: int = DEFAULT_RECENT_LIMIT) -> list[tuple[float, int]]:
        """Most-recent (score, outcome) pairs whose outcome has resolved."""
        resolved = [(r["score"], r["outcome"]) for r in self._rows(tenant_id)
                    if r["outcome"] is not None]
        return resolved[-int(limit):]


class PgPredictionLog(PgTenantOps):
    """Durable prediction log over the `predictions` table (RLS-FORCEd; see db/schema.sql)."""

    def log(self, tenant_id: str, *, deal_id: str | None, model_version: int,
            score: float, features: dict | None = None) -> None:
        with self._tx(tenant_id) as cur:
            cur.execute(
                "INSERT INTO predictions (tenant_id, deal_id, model_version, score, features) "
                "VALUES (%s, %s, %s, %s, %s)",
                (str(tenant_id), deal_id, int(model_version), float(score),
                 json.dumps(features or {})),
            )

    def record_outcome(self, tenant_id: str, deal_id: str, outcome: int) -> int:
        """Resolve outcome on the deal's open predictions (RLS scopes to the tenant)."""
        with self._tx(tenant_id) as cur:
            cur.execute(
                "UPDATE predictions SET outcome = %s, outcome_at = now() "
                "WHERE deal_id = %s AND outcome IS NULL",
                (int(outcome), str(deal_id)),
            )
            return cur.rowcount if cur.rowcount is not None else 0

    def scored_outcomes(self, tenant_id: str,
                        limit: int = DEFAULT_RECENT_LIMIT) -> list[tuple[float, int]]:
        with self._tx(tenant_id) as cur:
            cur.execute(
                "SELECT score, outcome FROM predictions WHERE outcome IS NOT NULL "
                "ORDER BY predicted_at DESC LIMIT %s",
                (int(limit),),
            )
            rows = dict_rows(cur)
        # Chronological order (oldest first) to match the in-memory log's append order.
        return [(float(r["score"]), int(r["outcome"])) for r in reversed(rows)]


def live_auc(prediction_log: Any, tenant_id: str, *,
             min_samples: int = MIN_LIVE_SAMPLES) -> dict:
    """The champion's REAL recent AUC from logged predictions + resolved outcomes.

    Returns {"auc": float|None, "n": int, "reason": ...}: auc is None when there are too few
    resolved pairs or only one outcome class (AUC undefined) — callers must treat that as
    "no evidence", never as "no drift" certainty.
    """
    pairs = prediction_log.scored_outcomes(tenant_id)
    n = len(pairs)
    if n < min_samples:
        return {"auc": None, "n": n, "reason": f"only {n} resolved outcomes (< {min_samples})"}
    outcomes = [int(o) for _, o in pairs]
    if len(set(outcomes)) < 2:
        return {"auc": None, "n": n, "reason": "single-class outcomes — AUC undefined"}
    scores = [float(s) for s, _ in pairs]
    return {"auc": metrics.auc(outcomes, scores), "n": n, "reason": "ok"}
