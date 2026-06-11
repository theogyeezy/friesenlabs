"""Per-tenant usage quotas + Anthropic cost attribution (the data layer).

Three pieces, all tenant-scoped via the verified-claim tenant_id (THE TRUST RULE — tenant_id is
threaded in by the API from the verified JWT claim, never read from a header/body here):

  * UsageStore — a per-tenant MONTHLY counter over `usage_counters` (messages + agent_actions).
    `bump(tenant_id, metric)` atomically increments the current UTC-month bucket and returns the
    new running total for the period (messages + agent_actions); `current(tenant_id)` reads it.
    The plan-quota gate (api/limits.py) calls these; GET /usage (api/usage_routes.py) reports them.
  * CostRecorder — append-only token-usage attribution over `cost_events`. `record(...)` logs
    {tenant_id, ts, model, in_tok, out_tok, est_cost} where est_cost is computed AT WRITE TIME from
    shared/cost.py TIER_PRICES (stored so a later price change never rewrites history). `summary(
    tenant_id)` sums tokens + cost for the current month. Cost is MEASUREMENT — it NEVER blocks.
  * Pg* implementations over the EXISTING `_PgTenantClient` plumbing (pool + per-op
    `SET LOCAL app.current_tenant` txn) — the identical RLS pattern as PgTraceStore/PgApprovalStore,
    so RLS scopes every read/write and the GUC auto-resets at txn end. In-memory fakes mirror the
    same surface for offline tests.

Import-safe: psycopg2 is imported lazily on construction (DSN path only); nothing here touches the
network at import.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Protocol

# The metrics counted against the monthly quota (both roll into one running total per period).
QUOTA_METRICS = ("messages", "agent_actions")


def current_period(now: datetime | None = None) -> str:
    """The UTC month bucket 'YYYY-MM' a timestamp falls in (the usage_counters.period key)."""
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m")


# --------------------------------------------------------------------------- #
# Usage counters (monthly quota)
# --------------------------------------------------------------------------- #
class UsageStore(Protocol):
    def bump(self, tenant_id: str, metric: str, *, amount: int = 1) -> int: ...
    def current(self, tenant_id: str) -> dict: ...


def _norm_metric(metric: str) -> str:
    if metric not in QUOTA_METRICS:
        raise ValueError(f"unknown usage metric: {metric!r} (expected one of {QUOTA_METRICS})")
    return metric


class InMemoryUsageStore:
    """Offline counter store. Per (tenant, period, metric) integer counts; thread-safe."""

    def __init__(self):
        self._counts: dict[tuple[str, str, str], int] = defaultdict(int)
        self._lock = threading.Lock()

    def bump(self, tenant_id: str, metric: str, *, amount: int = 1) -> int:
        metric = _norm_metric(metric)
        period = current_period()
        with self._lock:
            self._counts[(str(tenant_id), period, metric)] += int(amount)
            return sum(self._counts[(str(tenant_id), period, m)] for m in QUOTA_METRICS)

    def current(self, tenant_id: str) -> dict:
        period = current_period()
        with self._lock:
            by_metric = {m: self._counts[(str(tenant_id), period, m)] for m in QUOTA_METRICS}
        return {"period": period, "by_metric": by_metric, "total": sum(by_metric.values())}


class PgUsageStore:
    """Aurora-backed monthly counter over `usage_counters` (FORCE'd RLS; non-owner crm_app role).

    EXACTLY the PgTraceStore connection pattern via the shared `_PgTenantClient` plumbing: every
    op runs in ONE transaction beginning with `SET LOCAL app.current_tenant = %s`, so RLS scopes
    the row and the GUC resets at txn end. `bump` is an atomic upsert
    (INSERT .. ON CONFLICT (tenant_id, period, metric) DO UPDATE SET count = count + EXCLUDED.count)
    so a concurrent bump never loses an increment.
    """

    def __init__(self, dsn: str | None = None, *, conn_factory=None):
        from api.pg_clients import _PgTenantClient  # noqa: PLC0415 — shared pool plumbing
        self._client = _PgTenantClient(dsn, conn_factory=conn_factory)

    def bump(self, tenant_id: str, metric: str, *, amount: int = 1) -> int:
        from api.pg_clients import _dict_rows  # noqa: PLC0415
        metric = _norm_metric(metric)
        period = current_period()
        with self._client._tx(tenant_id) as cur:
            cur.execute(
                "INSERT INTO usage_counters (tenant_id, period, metric, count, updated_at) "
                "VALUES (%s,%s,%s,%s, now()) "
                "ON CONFLICT (tenant_id, period, metric) DO UPDATE "
                "SET count = usage_counters.count + EXCLUDED.count, updated_at = now()",
                (str(tenant_id), period, metric, int(amount)),
            )
            cur.execute(
                "SELECT metric, count FROM usage_counters "
                "WHERE period = %s AND metric = ANY(%s)",
                (period, list(QUOTA_METRICS)),
            )
            rows = _dict_rows(cur)
        return sum(int(r["count"]) for r in rows)

    def current(self, tenant_id: str) -> dict:
        from api.pg_clients import _dict_rows  # noqa: PLC0415
        period = current_period()
        with self._client._tx(tenant_id) as cur:
            cur.execute(
                "SELECT metric, count FROM usage_counters "
                "WHERE period = %s AND metric = ANY(%s)",
                (period, list(QUOTA_METRICS)),
            )
            rows = _dict_rows(cur)
        by_metric = {m: 0 for m in QUOTA_METRICS}
        for r in rows:
            if r["metric"] in by_metric:
                by_metric[r["metric"]] = int(r["count"])
        return {"period": period, "by_metric": by_metric, "total": sum(by_metric.values())}


# --------------------------------------------------------------------------- #
# Cost attribution (Anthropic token usage)
# --------------------------------------------------------------------------- #
def estimate_cost(model: str | None, in_tok: int, out_tok: int) -> float:
    """USD estimate for one turn from shared/cost.py TIER_PRICES, mapping the model name to a
    pricing tier (haiku/sonnet/opus substring; default sonnet — the mid tier — for an unknown
    model name so a new model never silently logs $0). Computed at write time and stored."""
    from shared.cost import TIER_PRICES  # noqa: PLC0415 — keep import-safe + the rates in one place
    name = (model or "").lower()
    tier = "sonnet"
    for t in ("haiku", "opus", "sonnet"):
        if t in name:
            tier = t
            break
    pin, pout = TIER_PRICES[tier]
    return round(max(0, int(in_tok)) / 1_000_000 * pin + max(0, int(out_tok)) / 1_000_000 * pout, 6)


class CostRecorder(Protocol):
    def record(self, tenant_id: str, *, model: str | None, in_tok: int, out_tok: int) -> None: ...
    def summary(self, tenant_id: str) -> dict: ...


class InMemoryCostRecorder:
    """Offline cost recorder — an in-memory list of events; thread-safe."""

    def __init__(self):
        self.events: list[dict] = []
        self._lock = threading.Lock()

    def record(self, tenant_id: str, *, model: str | None, in_tok: int, out_tok: int) -> None:
        est = estimate_cost(model, in_tok, out_tok)
        with self._lock:
            self.events.append({
                "tenant_id": str(tenant_id),
                "ts": datetime.now(timezone.utc),
                "model": model,
                "in_tok": max(0, int(in_tok)),
                "out_tok": max(0, int(out_tok)),
                "est_cost": est,
            })

    def summary(self, tenant_id: str) -> dict:
        period = current_period()
        with self._lock:
            rows = [e for e in self.events
                    if str(e["tenant_id"]) == str(tenant_id) and current_period(e["ts"]) == period]
        return {
            "period": period,
            "events": len(rows),
            "in_tok": sum(r["in_tok"] for r in rows),
            "out_tok": sum(r["out_tok"] for r in rows),
            "est_cost": round(sum(r["est_cost"] for r in rows), 6),
        }


class PgPlanLookup:
    """Resolve a tenant's plan label from the `accounts` row (signup plane). `accounts` is
    RLS-EXEMPT (pre-tenant — tenant_id is nullable, set at provisioning), so this is a plain
    GRANT-gated read keyed on the VERIFIED tenant_id (the caller passes the verified claim only —
    never a header/body). Returns the plan string, or None when no provisioned account row carries
    that tenant_id (-> the limiter's most-generous fallback). Reuses the shared `_PgTenantClient`
    pool for connection management but issues a PLAIN query (no SET LOCAL — accounts has no
    tenant policy)."""

    def __init__(self, dsn: str | None = None, *, conn_factory=None):
        from api.pg_clients import _PgTenantClient  # noqa: PLC0415 — shared pool plumbing
        self._client = _PgTenantClient(dsn, conn_factory=conn_factory)

    def plan(self, tenant_id: str) -> str | None:
        from api.pg_clients import _dict_one  # noqa: PLC0415
        conn = self._client._getconn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT plan FROM accounts WHERE tenant_id = %s "
                "ORDER BY updated_at DESC LIMIT 1",
                (str(tenant_id),),
            )
            row = _dict_one(cur)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._client._putconn(conn)
        return (row or {}).get("plan")


class PgCostRecorder:
    """Aurora-backed cost recorder over `cost_events` (FORCE'd RLS; non-owner crm_app role).

    Same `_PgTenantClient` per-op SET LOCAL txn pattern. record() is a plain append (the table is
    INSERT/SELECT only for crm_app — append-only audit, like traces). summary() sums the current
    month's tokens + est_cost; the period filter is computed in Python and bound, never trusting
    the DB clock to agree with the app's notion of "this month".
    """

    def __init__(self, dsn: str | None = None, *, conn_factory=None):
        from api.pg_clients import _PgTenantClient  # noqa: PLC0415 — shared pool plumbing
        self._client = _PgTenantClient(dsn, conn_factory=conn_factory)

    def record(self, tenant_id: str, *, model: str | None, in_tok: int, out_tok: int) -> None:
        in_tok, out_tok = max(0, int(in_tok)), max(0, int(out_tok))
        est = estimate_cost(model, in_tok, out_tok)
        with self._client._tx(tenant_id) as cur:
            cur.execute(
                "INSERT INTO cost_events (tenant_id, model, in_tok, out_tok, est_cost) "
                "VALUES (%s,%s,%s,%s,%s)",
                (str(tenant_id), model, in_tok, out_tok, est),
            )

    def summary(self, tenant_id: str) -> dict:
        from api.pg_clients import _as_float, _dict_one  # noqa: PLC0415
        period = current_period()
        # Month window [first-of-month, first-of-next-month) bound as params (no DB-clock trust).
        start = datetime.strptime(period + "-01", "%Y-%m-%d").replace(tzinfo=timezone.utc)
        nyear, nmonth = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
        end = start.replace(year=nyear, month=nmonth)
        with self._client._tx(tenant_id) as cur:
            cur.execute(
                "SELECT count(*) AS events, "
                "COALESCE(sum(in_tok),0) AS in_tok, COALESCE(sum(out_tok),0) AS out_tok, "
                "COALESCE(sum(est_cost),0) AS est_cost FROM cost_events "
                "WHERE ts >= %s AND ts < %s",
                (start, end),
            )
            row = _dict_one(cur) or {}
        return {
            "period": period,
            "events": int(row.get("events") or 0),
            "in_tok": int(row.get("in_tok") or 0),
            "out_tok": int(row.get("out_tok") or 0),
            "est_cost": round(_as_float(row.get("est_cost")) or 0.0, 6),
        }
