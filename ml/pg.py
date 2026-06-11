"""Tenant-scoped Postgres plumbing for Cortex (the PgApprovalStore / api.pg_clients pattern).

Every operation checks a connection out of a thread-safe pool (or builds one via a per-op
`conn_factory`) and runs in ONE transaction that begins with `SET LOCAL app.current_tenant = %s`
— Postgres RLS scopes every read/write and the GUC auto-resets at txn end, never leaking across
the pooled connection. NEVER a shared connection or a session-level SET (the historical
cross-tenant-leak shape). Connects as the non-owner crm_app role; RLS does the tenant filtering.

THE TRUST RULE: `tenant_id` flows in from the caller (the verified Cognito JWT claim threaded
through the scheduler / tool context) — never from env, headers, or payloads here.

Import-safe: psycopg2 is imported lazily on construction (DSN path only); a conn_factory needs
no psycopg2 at all (tests inject fakes).
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Callable


class PgTenantOps:
    """Pool/conn-factory + per-op `SET LOCAL` transaction — subclass and use `_tx(tenant_id)`."""

    def __init__(self, dsn: str | None = None, *,
                 conn_factory: Callable[[], Any] | None = None):
        if (dsn is None) == (conn_factory is None):
            raise ValueError("provide exactly one of dsn or conn_factory")
        self._conn_factory = conn_factory
        self._pool = None
        self._psycopg2 = None
        if dsn is not None:
            import psycopg2  # noqa: PLC0415 — guarded (lazy; import-safe module)
            import psycopg2.pool  # noqa: PLC0415
            self._psycopg2 = psycopg2
            pool_max = int(os.environ.get("UPLIFT_DB_POOL_MAX", "10"))
            # min == max: a fixed-size pool RETAINS returned connections (psycopg2 closes any
            # connection beyond minconn on putconn), avoiding TCP/auth churn under load.
            self._pool = psycopg2.pool.ThreadedConnectionPool(1, pool_max, dsn)

    def _getconn(self):
        if self._pool is None:
            return self._conn_factory()
        import time  # noqa: PLC0415
        deadline = time.monotonic() + 10.0
        while True:
            try:
                return self._pool.getconn()
            except self._psycopg2.pool.PoolError as exc:
                if "exhausted" not in str(exc) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.005)

    def _putconn(self, conn) -> None:
        if self._pool is None:
            close = getattr(conn, "close", None)
            if close is not None:
                close()
        else:
            self._pool.putconn(conn)

    @contextmanager
    def _tx(self, tenant_id):
        """Yield a cursor inside ONE tenant-scoped transaction (SET LOCAL first, commit on
        success / rollback on error, connection always returned)."""
        conn = self._getconn()
        try:
            cur = conn.cursor()
            cur.execute("SET LOCAL app.current_tenant = %s", (str(tenant_id),))
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._putconn(conn)


def dict_rows(cur) -> list[dict]:
    """Normalize fetched rows to dicts via cursor.description (plain cursors + fakes)."""
    rows = cur.fetchall() or []
    if rows and isinstance(rows[0], dict):
        return [dict(r) for r in rows]
    columns = [d[0] for d in (cur.description or [])]
    return [dict(zip(columns, r)) for r in rows]
