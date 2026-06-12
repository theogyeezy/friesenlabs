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
               environment_id: str | None, coordinator_id: str | None) -> None: ...
    def get(self, tenant_id: str) -> dict | None: ...

    def set_session_id(self, tenant_id: str, session_id: str | None) -> None: ...


class InMemoryWorkspaceStore:
    """Offline workspace store (for FakeRuntime/tests; the real one is `PgWorkspaceStore`)."""

    def __init__(self):
        self._rows: dict[str, dict] = {}

    def upsert(self, tenant_id: str, workspace_id: str | None,
               environment_id: str | None, coordinator_id: str | None) -> None:
        # Same semantics as the Pg ON CONFLICT (tenant_id) DO UPDATE: one row per tenant.
        prior = self._rows.get(str(tenant_id)) or {}
        self._rows[str(tenant_id)] = {
            "tenant_id": str(tenant_id),
            "workspace_id": workspace_id,
            "environment_id": environment_id,
            "coordinator_id": coordinator_id,
            "session_id": prior.get("session_id"),
        }

    def get(self, tenant_id: str) -> dict | None:
        # Tenant-scope the read (mirrors the Pg RLS boundary): keyed by the caller's tenant only.
        row = self._rows.get(str(tenant_id))
        return dict(row) if row else None


    def set_session_id(self, tenant_id: str, session_id: str | None) -> None:
        row = self._rows.get(str(tenant_id))
        if row is not None:
            row["session_id"] = session_id


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
               environment_id: str | None, coordinator_id: str | None) -> None:
        # RLS WITH CHECK enforces tenant_id == app.current_tenant on both the INSERT and UPDATE arm.
        with self._tx(tenant_id) as cur:
            cur.execute(
                "INSERT INTO tenant_workspaces (tenant_id, workspace_id, environment_id, coordinator_id) "
                "VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (tenant_id) DO UPDATE SET "
                "workspace_id = EXCLUDED.workspace_id, "
                "environment_id = EXCLUDED.environment_id, "
                "coordinator_id = EXCLUDED.coordinator_id",
                (str(tenant_id), workspace_id, environment_id, coordinator_id),
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
