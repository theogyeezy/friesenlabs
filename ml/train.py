"""Per-tenant training + the bake-off (Build Guide Phase 8, Step 45).

feature build -> train candidates -> evaluate on a held-out split -> pick the best by held-out AUC.
Deterministic given `seed`. Tabular training is light (no GPUs); production swaps in LightGBM/XGBoost
candidates behind the same Estimator protocol.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from . import metrics
from .estimator import Estimator, GradientBoostedTrees, LogisticRegression, MajorityBaseline


@dataclass
class TrainedModel:
    estimator: Estimator
    estimator_name: str
    metrics: dict          # held-out metrics: {"auc":..., "accuracy":...}
    n_train: int
    n_holdout: int

    def score(self, records_features: list[list[float]]) -> list[float]:
        return self.estimator.predict_proba(records_features)


def _split(X, y, holdout: float, seed: int):
    idx = list(range(len(X)))
    random.Random(seed).shuffle(idx)
    cut = int(len(idx) * (1 - holdout))
    tr, ho = idx[:cut], idx[cut:]
    return ([X[i] for i in tr], [y[i] for i in tr], [X[i] for i in ho], [y[i] for i in ho])


def _candidates(seed: int) -> list[Estimator]:
    # The real bake-off: a linear model + a gradient-boosted tree ensemble, floored by the majority
    # baseline. The held-out AUC picks the winner per tenant (a future LightGBM/XGBoost candidate
    # slots in here behind the same protocol).
    return [LogisticRegression(seed=seed), GradientBoostedTrees(seed=seed), MajorityBaseline()]


def train(X: list[list[float]], y: list[int], *, holdout: float = 0.25, seed: int = 0) -> TrainedModel:
    Xtr, ytr, Xho, yho = _split(X, y, holdout, seed)
    best: TrainedModel | None = None
    for est in _candidates(seed):
        est.fit(Xtr, ytr)
        proba = est.predict_proba(Xho) if Xho else est.predict_proba(Xtr)
        ev = {"auc": metrics.auc(yho or ytr, proba), "accuracy": metrics.accuracy(yho or ytr, proba)}
        cand = TrainedModel(est, est.name, ev, len(Xtr), len(Xho))
        if best is None or cand.metrics["auc"] > best.metrics["auc"]:
            best = cand
    return best
