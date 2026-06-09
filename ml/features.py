"""Feature build (Build Guide Phase 8, Step 45).

Turn a tenant's lead/deal records (read tenant-scoped from Aurora/S3 — injected here) into a numeric
feature matrix + the outcome label (lead -> booked job). Keep it deterministic and explicit so the
same features serve training and run_model inference.
"""
from __future__ import annotations

# The feature order is the contract shared by training and inference.
FEATURE_NAMES = [
    "amount",            # deal value
    "n_activities",      # engagement count
    "days_since_created",
    "has_email",
    "has_phone",
]


def featurize_one(record: dict) -> list[float]:
    return [
        float(record.get("amount") or 0.0),
        float(record.get("n_activities") or 0),
        float(record.get("days_since_created") or 0),
        1.0 if record.get("email") else 0.0,
        1.0 if record.get("phone") else 0.0,
    ]


def featurize(records: list[dict]) -> list[list[float]]:
    return [featurize_one(r) for r in records]


def labels(records: list[dict], target: str = "booked") -> list[int]:
    """Outcome label; default target is whether the lead became a booked job."""
    return [1 if r.get(target) else 0 for r in records]
