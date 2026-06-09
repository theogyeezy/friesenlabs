"""Estimators behind one interface (Build Guide Phase 8, Step 45).

Production runs a LightGBM/XGBoost bake-off on SageMaker/Modal; offline we ship a small, real
pure-Python logistic-regression so the whole Cortex pipeline (train → registry → gate → serve →
retrain) is testable with no heavy deps or GPUs. LightGBM/XGBoost implement the same `Estimator`
protocol and drop in unchanged.
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
