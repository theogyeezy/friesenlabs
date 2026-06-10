"""Per-tenant defaults seeded at provisioning step 5 — the `tenant_settings` table (TODO INT/P2).

Implements the real half of `Provisioner._step_tenant_context`'s `db.set_tenant_defaults` seam:
INSERT the new tenant's default autonomy level + cost tag so the Greenlight gate and cost
attribution have a row to read from day one. (The Cube half of step 5 is a documented NO-OP —
see the comment in `signup/provisioning.py`: Cube's security context is derived per REQUEST from
the verified JWT, there is nothing to provision per tenant.)

`tenant_settings` is a TENANT-scoped table (unlike the pre-tenant `accounts`/`stripe_events`),
so this store uses the full PgApprovalStore/PgWorkspaceStore pattern: connect as the non-owner
crm_app role; each operation checks a connection out of a thread-safe pool and runs in ONE
transaction that begins with `SET LOCAL app.current_tenant = %s` — Postgres RLS scopes the write
and the GUC auto-resets at txn end (never a shared connection, never session-level state).

THE TRUST RULE: the tenant_id written here is the one the Provisioner minted on the Account
AFTER the signed Stripe webhook — it never arrives from env, a header, or a request body.

IDEMPOTENT BY DESIGN: `ON CONFLICT (tenant_id) DO NOTHING` — the SFN Retry policy (and a whole
duplicate execution) re-runs the step safely, and a retry can never clobber an autonomy level an
operator has since tuned.

Import-safe: psycopg2 is imported lazily on construction; importing this module needs no driver
and no network.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager

# The seeded defaults. L1 (propose + approve) matches the runtime default the Greenlight gate
# already falls back to (api/control/autonomy.py AutonomyConfig.default_level) — the seeded row
# makes it explicit + per-tenant tunable instead of implicit.
DEFAULT_AUTONOMY_LEVEL = "L1"


def cost_tag_for(tenant_id) -> str:
    """The tenant's cost-allocation tag value (the lean-pool tenancy model attributes spend by
    tag, not by AWS account)."""
    return f"tenant:{tenant_id}"


class PgTenantDefaults:
    """Aurora-backed tenant-defaults seeder over `tenant_settings` (as crm_app, RLS-scoped)."""

    def __init__(self, dsn: str):
        import psycopg2  # noqa: PLC0415 — guarded (import-safe module)
        import psycopg2.pool  # noqa: PLC0415
        from psycopg2.extras import RealDictCursor  # noqa: PLC0415
        self._psycopg2 = psycopg2
        self._cursor_factory = RealDictCursor
        pool_max = int(os.environ.get("UPLIFT_DB_POOL_MAX", "10"))
        # min == max: a fixed-size pool RETAINS returned connections (psycopg2 closes any
        # connection beyond minconn on putconn), avoiding TCP/auth churn under concurrent load.
        self._pool = psycopg2.pool.ThreadedConnectionPool(pool_max, pool_max, dsn)

    def _getconn(self):
        """Check out a pooled connection, waiting briefly if the pool is momentarily exhausted."""
        deadline = time.monotonic() + 10.0
        while True:
            try:
                return self._pool.getconn()
            except self._psycopg2.pool.PoolError as exc:
                if "exhausted" not in str(exc) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.005)

    @contextmanager
    def _tx(self, tenant_id):
        """Yield a RealDict cursor inside a single tenant-scoped transaction.

        Begins with `SET LOCAL app.current_tenant` (auto-resets at COMMIT/ROLLBACK), commits on
        success / rolls back on error, and always returns the connection to the pool. The per-op
        connection is never shared across threads (checked out for the duration of the txn).
        """
        conn = self._getconn()
        try:
            cur = conn.cursor(cursor_factory=self._cursor_factory)
            cur.execute("SET LOCAL app.current_tenant = %s", (str(tenant_id),))
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.putconn(conn)

    def set_tenant_defaults(self, tenant_id) -> None:
        # RLS WITH CHECK enforces tenant_id == app.current_tenant on the INSERT; DO NOTHING
        # keeps the step idempotent (and operator tuning wins over a late retry — docstring).
        with self._tx(tenant_id) as cur:
            cur.execute(
                "INSERT INTO tenant_settings (tenant_id, autonomy_level, cost_tag) "
                "VALUES (%s,%s,%s) "
                "ON CONFLICT (tenant_id) DO NOTHING",
                (str(tenant_id), DEFAULT_AUTONOMY_LEVEL, cost_tag_for(tenant_id)),
            )

    def get(self, tenant_id) -> dict | None:
        """Read back the tenant's settings row (RLS-scoped; None when not yet seeded)."""
        with self._tx(tenant_id) as cur:
            cur.execute("SELECT * FROM tenant_settings WHERE tenant_id = %s", (str(tenant_id),))
            row = cur.fetchone()
        return dict(row) if row else None
