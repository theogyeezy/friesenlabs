"""Playbook persistence — per-tenant, RLS-scoped rows over the `playbooks` table.

Mirrors the saved-views store pair (api/views.py): an in-memory store for offline tests and a
psycopg2-pooled Postgres store that runs EVERY operation inside one transaction beginning with
``SET LOCAL app.current_tenant = %s`` — the tenant for THAT operation, from the verified JWT
claim upstream (THE TRUST RULE) — so FORCE'd RLS scopes every read/write and the GUC auto-resets
at txn end, never leaking across the pooled connection.

Versioning is in-place (one row per playbook): every definition update bumps ``version`` and
``updated_at``. Status is ``draft`` | ``active`` (agents/playbooks VALID_STATUSES).
"""
from __future__ import annotations

import copy
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Protocol

from agents.playbooks import STATUS_DRAFT, VALID_STATUSES


class PlaybookStore(Protocol):
    def create(self, tenant_id: str, definition: dict, *, template_id: str | None = None,
               created_by: str | None = None) -> dict: ...
    def get(self, tenant_id: str, playbook_id: str) -> dict | None: ...
    def list(self, tenant_id: str) -> list[dict]: ...
    def update_definition(self, tenant_id: str, playbook_id: str, definition: dict) -> dict | None: ...
    def set_status(self, tenant_id: str, playbook_id: str, status: str) -> dict | None: ...
    def delete(self, tenant_id: str, playbook_id: str) -> bool: ...


def _check_status(status: str) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid playbook status {status!r} (valid: {sorted(VALID_STATUSES)})")


class InMemoryPlaybookStore:
    """Offline store (the real one is PgPlaybookStore over Aurora, tenant-scoped via RLS).
    Honors the same contract: every method is keyed by tenant_id first — a read or write for
    tenant A can never touch tenant B's rows."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def create(self, tenant_id, definition, *, template_id=None, created_by=None) -> dict:
        row = {
            "id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "name": definition.get("name", ""),
            "version": 1,
            "status": STATUS_DRAFT,
            "definition": dict(definition),
            "template_id": template_id,
            "created_by": created_by,
            "created_at": self._now(),
            "updated_at": self._now(),
        }
        self.rows[row["id"]] = row
        return copy.deepcopy(row)

    def _own(self, tenant_id: str, playbook_id: str) -> dict | None:
        row = self.rows.get(str(playbook_id))
        if row is None or row["tenant_id"] != str(tenant_id):
            return None  # the RLS contract: another tenant's row is indistinguishable from absent
        return row

    def get(self, tenant_id, playbook_id) -> dict | None:
        row = self._own(tenant_id, playbook_id)
        return copy.deepcopy(row) if row else None

    def list(self, tenant_id) -> list[dict]:
        rows = [copy.deepcopy(r) for r in self.rows.values() if r["tenant_id"] == str(tenant_id)]
        return sorted(rows, key=lambda r: r["created_at"])

    def update_definition(self, tenant_id, playbook_id, definition) -> dict | None:
        row = self._own(tenant_id, playbook_id)
        if row is None:
            return None
        row["definition"] = dict(definition)
        row["name"] = definition.get("name", row["name"])
        row["version"] += 1
        row["updated_at"] = self._now()
        return copy.deepcopy(row)

    def set_status(self, tenant_id, playbook_id, status) -> dict | None:
        _check_status(status)
        row = self._own(tenant_id, playbook_id)
        if row is None:
            return None
        row["status"] = status
        row["updated_at"] = self._now()
        return copy.deepcopy(row)

    def delete(self, tenant_id, playbook_id) -> bool:
        row = self._own(tenant_id, playbook_id)
        if row is None:
            return False
        del self.rows[row["id"]]
        return True


class PgPlaybookStore:
    """Aurora-backed playbook store over `playbooks`. Connects as crm_app (non-owner, NOBYPASSRLS).

    Pool construction is LAZY (first operation), so building the store — e.g. from the env-built
    ApiDeps default — never opens a DB connection as a side effect. Import-safe (lazy psycopg2).
    """

    def __init__(self, dsn: str):
        self._dsn = dsn
        self._pool = None
        self._psycopg2 = None
        self._Json = None
        self._cursor_factory = None

    def _ensure_pool(self):
        if self._pool is None:
            import psycopg2  # noqa: PLC0415 — guarded
            import psycopg2.pool  # noqa: PLC0415
            from psycopg2.extras import Json, RealDictCursor  # noqa: PLC0415

            self._psycopg2 = psycopg2
            self._Json = Json
            self._cursor_factory = RealDictCursor
            pool_max = int(os.environ.get("UPLIFT_DB_POOL_MAX", "10"))
            # min == max: fixed-size pool retains returned connections (see PgSavedViewStore).
            self._pool = psycopg2.pool.ThreadedConnectionPool(1, pool_max, self._dsn)
        return self._pool

    def _getconn(self):
        """Check out a pooled connection, waiting briefly if exhausted (see PgApprovalStore)."""
        import time  # noqa: PLC0415

        pool = self._ensure_pool()
        deadline = time.monotonic() + 10.0
        while True:
            try:
                return pool.getconn()
            except self._psycopg2.pool.PoolError as exc:
                if "exhausted" not in str(exc) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.005)

    @contextmanager
    def _tx(self, tenant_id):
        """One tenant-scoped transaction: SET LOCAL app.current_tenant -> RLS scopes everything."""
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

    @staticmethod
    def _row(rec: Any) -> dict | None:
        if rec is None:
            return None
        row = dict(rec)
        row["id"] = str(row["id"])
        row["tenant_id"] = str(row["tenant_id"])
        return row

    @staticmethod
    def _uuid_or_none(playbook_id: Any) -> str | None:
        """Postgres `id = %s` raises on a non-uuid string; a malformed id must behave like an
        absent row (the same no-existence-oracle contract), never a 500."""
        try:
            return str(uuid.UUID(str(playbook_id)))
        except (ValueError, AttributeError, TypeError):
            return None

    def create(self, tenant_id, definition, *, template_id=None, created_by=None) -> dict:
        with self._tx(tenant_id) as cur:
            cur.execute(
                "INSERT INTO playbooks (tenant_id, name, definition, template_id, created_by) "
                "VALUES (%s,%s,%s,%s,%s) RETURNING *",
                (str(tenant_id), definition.get("name", ""), self._Json(definition),
                 template_id, created_by),
            )
            return self._row(cur.fetchone())

    def get(self, tenant_id, playbook_id) -> dict | None:
        pid = self._uuid_or_none(playbook_id)
        if pid is None:
            return None
        with self._tx(tenant_id) as cur:
            cur.execute("SELECT * FROM playbooks WHERE id = %s", (pid,))
            return self._row(cur.fetchone())

    def list(self, tenant_id) -> list[dict]:
        with self._tx(tenant_id) as cur:
            cur.execute("SELECT * FROM playbooks ORDER BY created_at")
            return [self._row(r) for r in cur.fetchall()]

    def update_definition(self, tenant_id, playbook_id, definition) -> dict | None:
        pid = self._uuid_or_none(playbook_id)
        if pid is None:
            return None
        with self._tx(tenant_id) as cur:
            cur.execute(
                "UPDATE playbooks SET definition = %s, name = %s, version = version + 1, "
                "updated_at = now() WHERE id = %s RETURNING *",
                (self._Json(definition), definition.get("name", ""), pid),
            )
            return self._row(cur.fetchone())

    def set_status(self, tenant_id, playbook_id, status) -> dict | None:
        _check_status(status)
        pid = self._uuid_or_none(playbook_id)
        if pid is None:
            return None
        with self._tx(tenant_id) as cur:
            cur.execute(
                "UPDATE playbooks SET status = %s, updated_at = now() WHERE id = %s RETURNING *",
                (status, pid),
            )
            return self._row(cur.fetchone())

    def delete(self, tenant_id, playbook_id) -> bool:
        pid = self._uuid_or_none(playbook_id)
        if pid is None:
            return False
        with self._tx(tenant_id) as cur:
            cur.execute("DELETE FROM playbooks WHERE id = %s", (pid,))
            return cur.rowcount > 0
