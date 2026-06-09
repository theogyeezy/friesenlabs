"""Evaluation metrics (pure Python)."""
from __future__ import annotations


def accuracy(y_true: list[int], proba: list[float], threshold: float = 0.5) -> float:
    correct = sum(1 for yt, p in zip(y_true, proba) if int(p >= threshold) == yt)
    return correct / len(y_true) if y_true else 0.0


def auc(y_true: list[int], proba: list[float]) -> float:
    """ROC AUC via the rank/Mann-Whitney-U formula. Ties get averaged ranks."""
    pos = [p for p, y in zip(proba, y_true) if y == 1]
    neg = [p for p, y in zip(proba, y_true) if y == 0]
    if not pos or not neg:
        return 0.5
    order = sorted(range(len(proba)), key=lambda i: proba[i])
    ranks = [0.0] * len(proba)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and proba[order[j + 1]] == proba[order[i]]:
            j += 1
        avg_rank = (i + j) / 2 + 1  # 1-based average rank for the tie group
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    sum_pos_ranks = sum(ranks[i] for i in range(len(proba)) if y_true[i] == 1)
    n_pos, n_neg = len(pos), len(neg)
    return (sum_pos_ranks - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)
