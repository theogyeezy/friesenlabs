"""Estimators behind one interface (Build Guide Phase 8, Step 45).

Two REAL learners, both pure-Python (no heavy deps / GPUs — the whole Cortex pipeline stays testable
offline): a logistic regression and a gradient-boosted decision-tree classifier. The training
bake-off (ml/train.py) fits both plus a majority baseline and keeps whichever wins on held-out AUC —
so a tenant whose signal is linear gets logreg and one with feature interactions gets the GBT, picked
on evidence, never assumed. All three implement the same `Estimator` protocol; a future
LightGBM/XGBoost candidate drops in behind the identical interface.
"""
from __future__ import annotations

import math
import random
from typing import Protocol


class Estimator(Protocol):
    def fit(self, X: list[list[float]], y: list[int]) -> "Estimator": ...
    def predict_proba(self, X: list[list[float]]) -> list[float]: ...


def _sigmoid(z: float) -> float:
    if z < -60:
        return 0.0
    if z > 60:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


class LogisticRegression:
    """Batch gradient descent with L2. Deterministic given `seed`. A real (if simple) learner."""

    name = "logreg"

    def __init__(self, lr: float = 0.1, epochs: int = 400, l2: float = 0.0, seed: int = 0):
        self.lr = lr
        self.epochs = epochs
        self.l2 = l2
        self.seed = seed
        self.w: list[float] = []
        self.b: float = 0.0
        self._mean: list[float] = []
        self._std: list[float] = []

    def _standardize_fit(self, X):
        n, d = len(X), len(X[0])
        self._mean = [sum(row[j] for row in X) / n for j in range(d)]
        self._std = []
        for j in range(d):
            var = sum((row[j] - self._mean[j]) ** 2 for row in X) / n
            self._std.append(math.sqrt(var) or 1.0)

    def _standardize(self, X):
        return [[(row[j] - self._mean[j]) / self._std[j] for j in range(len(row))] for row in X]

    def fit(self, X, y):
        rng = random.Random(self.seed)
        self._standardize_fit(X)
        Xs = self._standardize(X)
        n, d = len(Xs), len(Xs[0])
        self.w = [rng.uniform(-0.01, 0.01) for _ in range(d)]
        self.b = 0.0
        for _ in range(self.epochs):
            gw = [0.0] * d
            gb = 0.0
            for i in range(n):
                z = self.b + sum(self.w[j] * Xs[i][j] for j in range(d))
                err = _sigmoid(z) - y[i]
                for j in range(d):
                    gw[j] += err * Xs[i][j]
                gb += err
            for j in range(d):
                self.w[j] -= self.lr * (gw[j] / n + self.l2 * self.w[j])
            self.b -= self.lr * (gb / n)
        return self

    def predict_proba(self, X):
        Xs = self._standardize(X)
        return [_sigmoid(self.b + sum(self.w[j] * row[j] for j in range(len(row)))) for row in Xs]


class _RegTree:
    """A tiny CART regression tree: greedy mean-squared-error splits to a fixed depth.

    Used as the weak learner inside the gradient booster (it fits the negative gradient / residual,
    a continuous target). Splits scan a bounded set of candidate thresholds per feature (quantile-ish
    via sorted unique values, capped) so fit stays O(depth · features · n·log n) — fine for the
    tabular per-tenant sizes Cortex trains on, with no numpy.
    """

    __slots__ = ("max_depth", "min_samples", "feat", "thresh", "left", "right", "value")

    def __init__(self, max_depth: int = 3, min_samples: int = 8):
        self.max_depth = max_depth
        self.min_samples = min_samples
        self.feat: int | None = None
        self.thresh: float = 0.0
        self.left: _RegTree | None = None
        self.right: _RegTree | None = None
        self.value: float = 0.0

    def fit(self, X: list[list[float]], g: list[float], depth: int = 0) -> "_RegTree":
        n = len(g)
        self.value = sum(g) / n if n else 0.0
        if depth >= self.max_depth or n < 2 * self.min_samples:
            return self
        best = self._best_split(X, g)
        if best is None:
            return self
        self.feat, self.thresh = best
        li = [i for i in range(n) if X[i][self.feat] <= self.thresh]
        ri = [i for i in range(n) if X[i][self.feat] > self.thresh]
        self.left = _RegTree(self.max_depth, self.min_samples).fit([X[i] for i in li], [g[i] for i in li], depth + 1)
        self.right = _RegTree(self.max_depth, self.min_samples).fit([X[i] for i in ri], [g[i] for i in ri], depth + 1)
        return self

    def _best_split(self, X, g):
        n, d = len(X), len(X[0])
        parent_sse = _sse(g)
        best_gain, best = 0.0, None
        for j in range(d):
            vals = sorted(set(row[j] for row in X))
            if len(vals) < 2:
                continue
            # Candidate thresholds = midpoints between consecutive unique values (cap at 32 scans).
            mids = [(vals[k] + vals[k + 1]) / 2.0 for k in range(len(vals) - 1)]
            if len(mids) > 32:
                step = len(mids) / 32.0
                mids = [mids[int(k * step)] for k in range(32)]
            for t in mids:
                lg = [g[i] for i in range(n) if X[i][j] <= t]
                rg = [g[i] for i in range(n) if X[i][j] > t]
                if len(lg) < self.min_samples or len(rg) < self.min_samples:
                    continue
                gain = parent_sse - _sse(lg) - _sse(rg)
                if gain > best_gain:
                    best_gain, best = gain, (j, t)
        return best

    def predict_one(self, row: list[float]) -> float:
        node = self
        while node.feat is not None and node.left is not None:
            node = node.left if row[node.feat] <= node.thresh else node.right
        return node.value


def _sse(values: list[float]) -> float:
    """Sum of squared error about the mean — the regression-tree split criterion."""
    n = len(values)
    if n == 0:
        return 0.0
    mean = sum(values) / n
    return sum((v - mean) ** 2 for v in values)


class GradientBoostedTrees:
    """Gradient boosting for binary classification with logistic loss (a real GBDT, pure-Python).

    Stagewise: start from the log-odds of the base rate, then for each round fit a shallow regression
    tree to the negative gradient of the logloss (y - p) and add `learning_rate · tree` to the raw
    margins. Deterministic given the data (no subsampling RNG). Captures the feature interactions a
    single logistic regression can't — e.g. "high amount ONLY converts when paired with high
    engagement" — which is exactly the structure of the derived feature set.
    """

    name = "gbt"

    def __init__(self, n_estimators: int = 60, learning_rate: float = 0.2,
                 max_depth: int = 3, min_samples: int = 8, seed: int = 0):
        self.n_estimators = n_estimators
        self.learning_rate = learning_rate
        self.max_depth = max_depth
        self.min_samples = min_samples
        self.seed = seed  # accepted for protocol parity; this learner is already deterministic
        self.init_margin: float = 0.0
        self.trees: list[_RegTree] = []

    def fit(self, X, y):
        n = len(y)
        rate = (sum(y) / n) if n else 0.5
        rate = min(max(rate, 1e-6), 1 - 1e-6)
        self.init_margin = math.log(rate / (1.0 - rate))
        margins = [self.init_margin] * n
        self.trees = []
        for _ in range(self.n_estimators):
            residual = [y[i] - _sigmoid(margins[i]) for i in range(n)]  # negative gradient of logloss
            tree = _RegTree(self.max_depth, self.min_samples).fit(X, residual)
            self.trees.append(tree)
            for i in range(n):
                margins[i] += self.learning_rate * tree.predict_one(X[i])
        return self

    def _margin(self, row: list[float]) -> float:
        return self.init_margin + self.learning_rate * sum(t.predict_one(row) for t in self.trees)

    def predict_proba(self, X):
        return [_sigmoid(self._margin(row)) for row in X]


class MajorityBaseline:
    """Predicts the base rate for everything — the floor a real model must beat."""

    name = "baseline"

    def __init__(self):
        self.rate = 0.5

    def fit(self, X, y):
        self.rate = (sum(y) / len(y)) if y else 0.5
        return self

    def predict_proba(self, X):
        return [self.rate for _ in X]
