"""Unit: the gradient-boosted-trees learner + the enriched feature contract.

Proves the GBT is a REAL learner (fits, beats the baseline, captures an interaction logreg can't,
deterministic) and that the feature vector stays a stable, train/serve-parity contract.
"""
import math
import random

import pytest

from ml import features
from ml.estimator import GradientBoostedTrees, LogisticRegression, MajorityBaseline
from ml.metrics import auc


# ------------------------------------------------------------------ feature contract
@pytest.mark.unit
def test_feature_vector_matches_names_length():
    rec = {"amount": 5000, "n_activities": 4, "days_since_created": 3, "email": "a@b.com", "phone": "555"}
    vec = features.featurize_one(rec)
    assert len(vec) == len(features.FEATURE_NAMES)


@pytest.mark.unit
def test_derived_features_are_correct():
    rec = {"amount": 100.0, "n_activities": 6, "days_since_created": 2, "email": "a@b.com", "phone": "555"}
    by_name = dict(zip(features.FEATURE_NAMES, features.featurize_one(rec)))
    assert by_name["amount"] == 100.0
    assert by_name["log_amount"] == pytest.approx(math.log1p(100.0))
    assert by_name["activities_per_day"] == pytest.approx(6 / 3.0)       # 6 acts / (2 days + 1)
    assert by_name["is_recent"] == 1.0                                   # 2 < 7
    assert by_name["has_both_contacts"] == 1.0                           # email AND phone


@pytest.mark.unit
def test_recency_and_contact_flags_flip():
    old_partial = {"amount": 0, "n_activities": 0, "days_since_created": 30, "email": "a@b.com", "phone": None}
    by_name = dict(zip(features.FEATURE_NAMES, features.featurize_one(old_partial)))
    assert by_name["is_recent"] == 0.0
    assert by_name["has_both_contacts"] == 0.0
    assert by_name["activities_per_day"] == 0.0


# ------------------------------------------------------------------ GBT learner
def _xor_ish(n=500, seed=3):
    """An INTERACTION pattern: booked iff exactly one of (high amount, high engagement) holds — the
    classic case a single linear boundary can't separate but trees can."""
    rng = random.Random(seed)
    recs = []
    for _ in range(n):
        hi_amt = rng.random() < 0.5
        hi_eng = rng.random() < 0.5
        booked = 1 if (hi_amt != hi_eng) else 0
        if rng.random() < 0.05:
            booked = 1 - booked  # a little label noise
        recs.append({
            "amount": rng.uniform(8000, 10000) if hi_amt else rng.uniform(0, 2000),
            "n_activities": rng.randint(15, 20) if hi_eng else rng.randint(0, 4),
            "days_since_created": rng.randint(0, 90),
            "email": "x@y.com" if rng.random() < 0.5 else None,
            "phone": "555" if rng.random() < 0.5 else None,
            "booked": booked,
        })
    return recs


def _split(X, y, frac=0.75):
    cut = int(len(X) * frac)
    return X[:cut], y[:cut], X[cut:], y[cut:]


@pytest.mark.unit
def test_gbt_beats_baseline_on_holdout():
    recs = _xor_ish()
    X, y = features.featurize(recs), features.labels(recs)
    Xtr, ytr, Xho, yho = _split(X, y)
    gbt = GradientBoostedTrees(seed=0).fit(Xtr, ytr)
    base = MajorityBaseline().fit(Xtr, ytr)
    gbt_auc = auc(yho, gbt.predict_proba(Xho))
    base_auc = auc(yho, base.predict_proba(Xho))
    assert gbt_auc > 0.75
    assert gbt_auc > base_auc + 0.1


@pytest.mark.unit
def test_gbt_captures_interaction_logreg_misses():
    # On the pure interaction pattern the GBT should clearly out-separate a linear model.
    recs = _xor_ish()
    X, y = features.featurize(recs), features.labels(recs)
    Xtr, ytr, Xho, yho = _split(X, y)
    gbt_auc = auc(yho, GradientBoostedTrees(seed=0).fit(Xtr, ytr).predict_proba(Xho))
    lr_auc = auc(yho, LogisticRegression(seed=0).fit(Xtr, ytr).predict_proba(Xho))
    assert gbt_auc > lr_auc


@pytest.mark.unit
def test_gbt_is_deterministic():
    recs = _xor_ish()
    X, y = features.featurize(recs), features.labels(recs)
    a = GradientBoostedTrees(seed=0).fit(X, y).predict_proba(X)
    b = GradientBoostedTrees(seed=0).fit(X, y).predict_proba(X)
    assert a == b


@pytest.mark.unit
def test_gbt_proba_in_range():
    recs = _xor_ish(n=120)
    X, y = features.featurize(recs), features.labels(recs)
    proba = GradientBoostedTrees(n_estimators=20, seed=0).fit(X, y).predict_proba(X)
    assert all(0.0 <= p <= 1.0 for p in proba)
