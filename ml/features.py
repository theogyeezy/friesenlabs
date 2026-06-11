"""Feature build (Build Guide Phase 8, Step 45).

Turn a tenant's lead/deal records (read tenant-scoped from Aurora/S3 — injected here) into a numeric
feature matrix + the outcome label (lead -> booked job). Keep it deterministic and explicit so the
same features serve training and run_model inference.

The feature vector is built ENTIRELY from the base record fields (amount, n_activities,
days_since_created, email, phone) that both the training loader (ml/data_loader.py) and the
run_model inference path (agents/tools/run_model.py) already produce — so train/serve parity is
guaranteed by construction. Beyond the raw fields we add cheap DERIVED features (log-scaled amount,
engagement velocity, recency flag, contact-completeness) that give the gradient-boosted learner real
non-linear signal without needing any new data plumbing. (Deeper joins — company/source/stage
history — are a follow-up that touches the loader SQL and the inference record shape together.)
"""
from __future__ import annotations

import math

# The feature order is the contract shared by training and inference. APPEND-ONLY: never reorder or
# remove an entry (the registered estimator is dimensioned to this vector; a mismatch silently
# corrupts scoring). The first five are the raw fields; the rest are deterministic transforms.
FEATURE_NAMES = [
    "amount",             # deal value
    "n_activities",       # engagement count
    "days_since_created",
    "has_email",
    "has_phone",
    "log_amount",         # log1p(amount) — tames the heavy right tail of deal values
    "activities_per_day", # engagement VELOCITY (n_activities normalised by deal age)
    "is_recent",          # 1 if created within the last week (fresh leads convert differently)
    "has_both_contacts",  # email AND phone present — a fuller, more reachable contact
]


def featurize_one(record: dict) -> list[float]:
    amount = float(record.get("amount") or 0.0)
    n_activities = float(record.get("n_activities") or 0)
    days_since_created = float(record.get("days_since_created") or 0)
    has_email = 1.0 if record.get("email") else 0.0
    has_phone = 1.0 if record.get("phone") else 0.0
    return [
        amount,
        n_activities,
        days_since_created,
        has_email,
        has_phone,
        math.log1p(max(amount, 0.0)),
        n_activities / (days_since_created + 1.0),
        1.0 if days_since_created < 7 else 0.0,
        1.0 if (has_email and has_phone) else 0.0,
    ]


def featurize(records: list[dict]) -> list[list[float]]:
    return [featurize_one(r) for r in records]


def labels(records: list[dict], target: str = "booked") -> list[int]:
    """Outcome label; default target is whether the lead became a booked job."""
    return [1 if r.get(target) else 0 for r in records]
