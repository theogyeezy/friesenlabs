"""Unit: the learner actually learns a separable pattern and metrics are sane + deterministic."""
import random

import pytest

from ml import features, metrics, train


def _synthetic(n=400, seed=1):
    """Leads where high amount + many activities + contactable => more likely booked."""
    rng = random.Random(seed)
    records = []
    for _ in range(n):
        amount = rng.uniform(0, 10000)
        acts = rng.randint(0, 20)
        has_email = rng.random() < 0.7
        score = amount / 10000 + acts / 20 + (0.3 if has_email else 0)
        booked = 1 if score + rng.uniform(-0.3, 0.3) > 1.0 else 0
        records.append({"amount": amount, "n_activities": acts, "days_since_created": rng.randint(0, 90),
                        "email": "x@y.com" if has_email else None, "phone": None, "booked": booked})
    return records


@pytest.mark.unit
def test_model_beats_random_on_holdout():
    recs = _synthetic()
    X, y = features.featurize(recs), features.labels(recs)
    model = train.train(X, y, seed=0)
    assert model.metrics["auc"] > 0.7      # genuinely learned signal
    assert model.estimator_name == "logreg"  # bake-off picked the real model over the baseline


@pytest.mark.unit
def test_training_is_deterministic():
    recs = _synthetic()
    X, y = features.featurize(recs), features.labels(recs)
    a = train.train(X, y, seed=0).metrics["auc"]
    b = train.train(X, y, seed=0).metrics["auc"]
    assert a == b


@pytest.mark.unit
def test_auc_helper_basic():
    # Perfectly separable scores -> AUC 1.0; reversed -> 0.0.
    assert metrics.auc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == 1.0
    assert metrics.auc([1, 1, 0, 0], [0.1, 0.2, 0.8, 0.9]) == 0.0
