"""Per-tenant Managed Agents id persistence — the `tenant_workspaces` table (AI plane P0).

Provisioning creates one Anthropic workspace / environment / coordinator per tenant; this store
persists those ids so the conversation factory + worker read them back instead of rebuilding the
roster per request (TODO: "Persist per-tenant coordinator_id + environment_id").

THE TRUST RULE: tenant_id flows in from the caller (the verified Cognito JWT claim, bound per
request upstream) — never from env, header, or payload here. PgWorkspaceStore mirrors
api/control/greenlight.py PgApprovalStore exactly: a pooled per-request connection running each
operation in ONE transaction that begins with `SET LOCAL app.current_tenant`, so Postgres RLS
scopes every read/write and the GUC auto-resets at txn end — never a shared connection or a
session-level SET (the critical cross-tenant leak pattern).

Import-safe: psycopg2 is imported lazily on PgWorkspaceStore construction; importing this module
needs no network and no driver.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Protocol


class WorkspaceStore(Protocol):
    """What the rest of the system needs from per-tenant workspace persistence."""

    def upsert(self, tenant_id: str, workspace_id: str | None,
               environment_id: str | None, coordinator_id: str | None,
               roster_version: str | None = None) -> None: ...
    def get(self, tenant_id: str) -> dict | None: ...

    def set_session_id(self, tenant_id: str, session_id: str | None) -> None: ...

    def upsert_coordinator_if_version(self, tenant_id: str, coordinator_id: str | None, *,
                                      new_version: str, expected_version: str | None) -> bool: ...

    def record_retirement(self, tenant_id: str, coordinator_id: str | None,
                          agent_ids: list[str]) -> None: ...


class InMemoryWorkspaceStore:
    """Offline workspace store (for FakeRuntime/tests; the real one is `PgWorkspaceStore`)."""

    def __init__(self):
        self._rows: dict[str, dict] = {}
        self.retirements: list[dict] = []   # superseded rosters recorded for the reaper (test-visible)

    def upsert(self, tenant_id: str, workspace_id: str | None,
               environment_id: str | None, coordinator_id: str | None,
               roster_version: str | None = None) -> None:
        # Same semantics as the Pg ON CONFLICT (tenant_id) DO UPDATE: one row per tenant.
        # roster_version PRESERVES the prior value when not passed (mirrors the Pg COALESCE), so a
        # bare upsert never clobbers the stamp — only a provisioning call (which passes it) updates.
        prior = self._rows.get(str(tenant_id)) or {}
        self._rows[str(tenant_id)] = {
            "tenant_id": str(tenant_id),
            "workspace_id": workspace_id,
            "environment_id": environment_id,
            "coordinator_id": coordinator_id,
            "session_id": prior.get("session_id"),
            "roster_version": roster_version if roster_version is not None
            else prior.get("roster_version"),
        }

    def get(self, tenant_id: str) -> dict | None:
        # Tenant-scope the read (mirrors the Pg RLS boundary): keyed by the caller's tenant only.
        row = self._rows.get(str(tenant_id))
        return dict(row) if row else None


    def set_session_id(self, tenant_id: str, session_id: str | None) -> None:
        row = self._rows.get(str(tenant_id))
        if row is not None:
            row["session_id"] = session_id

    def upsert_coordinator_if_version(self, tenant_id: str, coordinator_id: str | None, *,
                                      new_version: str, expected_version: str | None) -> bool:
        # Same arbiter semantics as the Pg conditional UPDATE: swap the coordinator + stamp ONLY if
        # the roster_version is still what the caller read (NULL-safe). Returns True iff THIS caller
        # made the transition (cross-process exactly-once). Clears the session on a win — the old
        # session belongs to the old coordinator.
        row = self._rows.get(str(tenant_id))
        if row is None or row.get("roster_version") != expected_version:
            return False
        row["coordinator_id"] = coordinator_id
        row["roster_version"] = new_version
        row["session_id"] = None
        return True

    def record_retirement(self, tenant_id: str, coordinator_id: str | None,
                          agent_ids: list[str]) -> None:
        self.retirements.append({
            "tenant_id": str(tenant_id),
            "coordinator_id": coordinator_id,
            "agent_ids": list(agent_ids),
        })


class PgWorkspaceStore:
    """Aurora-backed workspace store over the `tenant_workspaces` table.

    Connects as the non-owner crm_app role. Each operation checks out a connection from a
    thread-safe pool and runs in ONE transaction that begins with
    `SET LOCAL app.current_tenant = %s` (the tenant for THIS operation) — so Postgres RLS scopes
    every read/write and the GUC auto-resets at txn end, never leaking past the unit of work across
    the pooled connection. Import-safe (psycopg2 imported lazily on construction).
    """

    def __init__(self, dsn: str):
        import psycopg2  # noqa: PLC0415 — guarded
        import psycopg2.pool  # noqa: PLC0415
        from psycopg2.extras import RealDictCursor  # noqa: PLC0415
        self._psycopg2 = psycopg2
        self._cursor_factory = RealDictCursor
        pool_max = int(os.environ.get("UPLIFT_DB_POOL_MAX", "10"))
        # min == max: a fixed-size pool RETAINS returned connections (psycopg2 closes any
        # connection beyond minconn on putconn), avoiding TCP/auth churn under concurrent load.
        self._pool = psycopg2.pool.ThreadedConnectionPool(1, pool_max, dsn)

    def _getconn(self):
        """Check out a pooled connection, waiting briefly if the pool is momentarily exhausted.

        psycopg2's pool raises rather than blocks when all connections are out; under a burst wider
        than the pool (the anyio threadpool can exceed pool_max) we'd otherwise 500. Wait up to a
        few seconds for a peer's short tenant-scoped txn to release one, then give up.
        """
        import time  # noqa: PLC0415
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

    def upsert(self, tenant_id: str, workspace_id: str | None,
               environment_id: str | None, coordinator_id: str | None,
               roster_version: str | None = None) -> None:
        # RLS WITH CHECK enforces tenant_id == app.current_tenant on both the INSERT and UPDATE arm.
        # roster_version uses COALESCE(EXCLUDED, existing) so a bare upsert (no version) PRESERVES the
        # stamp — only a provisioning call (which passes it) updates the version alongside the
        # coordinator_id it is paired with.
        with self._tx(tenant_id) as cur:
            cur.execute(
                "INSERT INTO tenant_workspaces "
                "(tenant_id, workspace_id, environment_id, coordinator_id, roster_version) "
                "VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (tenant_id) DO UPDATE SET "
                "workspace_id = EXCLUDED.workspace_id, "
                "environment_id = EXCLUDED.environment_id, "
                "coordinator_id = EXCLUDED.coordinator_id, "
                "roster_version = COALESCE(EXCLUDED.roster_version, tenant_workspaces.roster_version)",
                (str(tenant_id), workspace_id, environment_id, coordinator_id, roster_version),
            )

    def get(self, tenant_id: str) -> dict | None:
        # The explicit WHERE is belt-and-suspenders; RLS already scopes the read to the bound tenant.
        with self._tx(tenant_id) as cur:
            cur.execute("SELECT * FROM tenant_workspaces WHERE tenant_id = %s", (str(tenant_id),))
            row = cur.fetchone()
        return dict(row) if row else None

    def set_session_id(self, tenant_id: str, session_id: str | None) -> None:
        """Persist (or clear, with None) the tenant's CURRENT MA session id — the handle a
        fresh api task resumes after a deploy roll. RLS scopes the UPDATE to the bound tenant."""
        with self._tx(tenant_id) as cur:
            cur.execute(
                "UPDATE tenant_workspaces SET session_id = %s WHERE tenant_id = %s",
                (session_id, str(tenant_id)),
            )

    def upsert_coordinator_if_version(self, tenant_id: str, coordinator_id: str | None, *,
                                      new_version: str, expected_version: str | None) -> bool:
        """Compare-and-set the coordinator + roster stamp: land the swap ONLY if roster_version is
        still the value the caller read (the cross-process upgrade claim). Two api tasks that both
        detect a stale roster and both re-provision call this with the SAME expected_version — the
        first commits and moves the row off it; the second's WHERE no longer matches (rowcount 0)
        and it serves the winner, so the row never flip-flops between coordinators. `IS NOT DISTINCT
        FROM` makes the guard NULL-safe (an unstamped legacy row claims with expected=NULL). Clears
        session_id in the SAME statement — the old session is pinned to the old coordinator. Returns
        True iff THIS call won the claim. RLS scopes the UPDATE to the bound tenant."""
        with self._tx(tenant_id) as cur:
            cur.execute(
                "UPDATE tenant_workspaces SET coordinator_id = %s, roster_version = %s, "
                "session_id = NULL "
                "WHERE tenant_id = %s AND roster_version IS NOT DISTINCT FROM %s",
                (coordinator_id, new_version, str(tenant_id), expected_version),
            )
            return cur.rowcount == 1

    def record_retirement(self, tenant_id: str, coordinator_id: str | None,
                          agent_ids: list[str]) -> None:
        """Append a superseded roster (its coordinator + every specialist id) to retired_rosters for
        the orphan reaper. APPEND-ONLY ops ledger (crm_app has no DELETE; the reaper marks reaped_at).
        retired_rosters is RLS-EXEMPT so the cross-tenant reaper can read it, but the WRITE still
        rides the tenant-scoped txn (the SET LOCAL is harmless on a non-RLS table) and stamps the
        owning tenant_id for the audit trail."""
        with self._tx(tenant_id) as cur:
            cur.execute(
                "INSERT INTO retired_rosters (tenant_id, coordinator_id, agent_ids) "
                "VALUES (%s, %s, %s)",
                (str(tenant_id), coordinator_id, list(agent_ids)),
            )
