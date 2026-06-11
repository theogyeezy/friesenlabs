"""Training-data loader — the producer of the feature fields the serving code already expects.

`ml.features.FEATURE_NAMES` is the contract shared by training and `run_model` inference:
amount, n_activities, days_since_created, has_email, has_phone. Until now NOTHING produced
training records in that shape from real tenant data (the audit's "no producer" gap). This
loader closes it: it reads the tenant's CLOSED deals from the CRM core (deals + contacts +
activities) and emits one record per deal —

    {"deal_id", "amount", "n_activities", "days_since_created", "email", "phone", "booked"}

* label: `booked` = 1 for a won stage, 0 for a lost stage. Only CLOSED deals are loaded —
  an open deal has no outcome yet and would poison the labels.
* features: deal amount; engagement = count of the deal's activities (stage history proxy —
  every stage flip lands an activity row via the Greenlight appliers); recency = age in days
  at `as_of`; contact email/phone presence.

Tenant-scoped the only allowed way: every query runs through the pooled per-op
`SET LOCAL app.current_tenant` transaction (ml/pg.py) as the non-owner crm_app role, so RLS
does the tenant filtering — no hand-written `WHERE tenant_id = ...` anywhere. The tenant id
flows in from the caller (THE TRUST RULE: the verified claim / scheduler arg, never env or
payloads here).

Determinism: same rows + same `as_of` -> the identical record list (stable deal-id order,
explicit day arithmetic) — proven in tests/unit/test_ml_data_loader.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .pg import PgTenantOps, dict_rows

# Stage vocabulary observed across the demo/load packs and connectors ('closed_won'/'closed_lost'
# canonical; bare 'won'/'lost' accepted defensively). Matching is case-insensitive.
WON_STAGES = ("closed_won", "won")
LOST_STAGES = ("closed_lost", "lost")

_SQL = """
SELECT d.id AS deal_id,
       d.amount,
       lower(d.stage) AS stage,
       d.created_at,
       ct.email,
       ct.phone,
       (SELECT count(*) FROM activities a WHERE a.deal_id = d.id) AS n_activities
FROM deals d
LEFT JOIN contacts ct ON ct.id = d.contact_id
WHERE lower(d.stage) = ANY(%s)
ORDER BY d.created_at, d.id
"""


def _days_since(created_at: Any, as_of: datetime) -> float:
    if created_at is None:
        return 0.0
    if isinstance(created_at, str):  # fakes/fixtures may hand back ISO strings
        created_at = datetime.fromisoformat(created_at)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return max((as_of - created_at).total_seconds() / 86400.0, 0.0)


def record_from_row(row: dict, as_of: datetime) -> dict:
    """One closed-deal row -> one training record in the features.py contract shape."""
    return {
        "deal_id": str(row["deal_id"]) if row.get("deal_id") is not None else None,
        "amount": float(row["amount"]) if row.get("amount") is not None else 0.0,
        "n_activities": int(row.get("n_activities") or 0),
        "days_since_created": _days_since(row.get("created_at"), as_of),
        "email": row.get("email"),
        "phone": row.get("phone"),
        "booked": 1 if (row.get("stage") or "").lower() in WON_STAGES else 0,
    }


class PgTrainingDataLoader(PgTenantOps):
    """Loads a tenant's labeled training records from Aurora/Postgres (RLS-scoped)."""

    def load(self, tenant_id: str, *, as_of: datetime | None = None) -> list[dict]:
        """All CLOSED deals for `tenant_id` as training records (won=1 / lost=0)."""
        as_of = as_of or datetime.now(timezone.utc)
        stages = list(WON_STAGES) + list(LOST_STAGES)
        with self._tx(tenant_id) as cur:
            cur.execute(_SQL, (stages,))
            rows = dict_rows(cur)
        return [record_from_row(r, as_of) for r in rows]


class StaticTrainingDataLoader:
    """Offline loader over pre-built records (tests / the CLI's --records-json path).

    Same `load(tenant_id)` protocol as PgTrainingDataLoader; records must already carry the
    contract fields. Tenant-scoped by construction: it only ever holds one tenant's records,
    handed to it by the caller.
    """

    def __init__(self, records: list[dict]):
        self._records = list(records)

    def load(self, tenant_id: str, *, as_of: datetime | None = None) -> list[dict]:  # noqa: ARG002
        return [dict(r) for r in self._records]
