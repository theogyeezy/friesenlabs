"""Greenlight — the human-in-the-loop approval queue (Build Guide Phase 5, Step 30).

When a side-effecting action needs confirmation, persist it to `approvals` and surface it: every item
carries the agent's reasoning, an editable draft, and the value at stake. A human approves / edits /
denies. Maps to the Managed Agents tool-confirmation reply (user.tool_confirmation allow/deny) — that
mapping is authored + flagged "verify"; it is never called live here.

Conforms to the `Greenlight` protocol in agents/tools/base.py (so Phase 4 tools route through it).
"""
import os
from contextlib import contextmanager
from typing import Protocol


class ApprovalStore(Protocol):
    def insert(self, row: dict) -> object: ...
    def get(self, tenant_id: str, approval_id: object) -> dict | None: ...
    def list_pending(self, tenant_id: str) -> list[dict]: ...
    def update(self, tenant_id: str, approval_id: object, changes: dict) -> None: ...


class InMemoryApprovalStore:
    """Offline approval store (the real one is `PgApprovalStore` over Aurora with RLS)."""

    def __init__(self):
        self._rows: dict[int, dict] = {}
        self._n = 0

    @staticmethod
    def _key(approval_id):
        # tolerate numeric string ids (FastAPI path params arrive as strings).
        s = str(approval_id)
        return int(s) if s.isdigit() else s

    def insert(self, row: dict) -> int:
        self._n += 1
        row = {"id": self._n, "applied_at": None, "apply_result": None, **row}
        self._rows[self._n] = row
        return self._n

    def get(self, tenant_id: str, approval_id) -> dict | None:
        row = self._rows.get(self._key(approval_id))
        # Tenant-scope the read (mirrors the Pg RLS boundary): never return another tenant's row.
        if row is None or str(row["tenant_id"]) != str(tenant_id):
            return None
        return row

    def list_pending(self, tenant_id: str) -> list[dict]:
        return [r for r in self._rows.values()
                if str(r["tenant_id"]) == str(tenant_id) and r["status"] == "pending"]

    def update(self, tenant_id: str, approval_id, changes: dict) -> None:
        row = self._rows.get(self._key(approval_id))
        if row is None or str(row["tenant_id"]) != str(tenant_id):
            return  # tenant-scoped: silently ignore a cross-tenant write
        row.update(changes)


class PgApprovalStore:
    """Aurora-backed approval store over the `approvals` table.

    Connects as the non-owner crm_app role. Each operation checks out a connection from a thread-safe
    pool and runs in ONE transaction that begins with `SET LOCAL app.current_tenant = %s` (the tenant
    for THIS operation) — so Postgres RLS scopes every read/write and the GUC auto-resets at txn end,
    never leaking past the unit of work across the pooled connection. Import-safe (psycopg2 imported
    lazily on construction). Ids are the table's uuids (as strings).
    """

    def __init__(self, dsn: str):
        import psycopg2  # noqa: PLC0415 — guarded
        import psycopg2.pool  # noqa: PLC0415
        from psycopg2.extras import Json, RealDictCursor  # noqa: PLC0415
        self._psycopg2 = psycopg2
        self._Json = Json
        self._cursor_factory = RealDictCursor
        pool_max = int(os.environ.get("UPLIFT_DB_POOL_MAX", "10"))
        # min == max: a fixed-size pool RETAINS returned connections (psycopg2 closes any
        # connection beyond minconn on putconn), avoiding TCP/auth churn under concurrent load.
        self._pool = psycopg2.pool.ThreadedConnectionPool(pool_max, pool_max, dsn)

    @staticmethod
    def _row(row) -> dict | None:
        if row is None:
            return None
        out = dict(row)
        out.setdefault("applied_at", None)
        out.setdefault("apply_result", None)
        return out

    def _getconn(self):
        """Check out a pooled connection, waiting briefly if the pool is momentarily exhausted.

        psycopg2's pool raises rather than blocks when all connections are out; under a burst wider
        than the pool (the anyio threadpool can exceed pool_max) we'd otherwise 500. Wait up to a few
        seconds for a peer's short tenant-scoped txn to release one, then give up.
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

    def insert(self, row: dict) -> str:
        with self._tx(row["tenant_id"]) as cur:
            cur.execute(
                "INSERT INTO approvals (tenant_id, proposed_action, agent, reasoning, value_at_stake, status) "
                "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (row["tenant_id"], self._Json(row["proposed_action"]), row.get("agent"),
                 row.get("reasoning"), row.get("value_at_stake"), row.get("status", "pending")),
            )
            return str(cur.fetchone()["id"])

    def get(self, tenant_id: str, approval_id) -> dict | None:
        with self._tx(tenant_id) as cur:
            cur.execute("SELECT * FROM approvals WHERE id = %s", (str(approval_id),))
            row = cur.fetchone()
        return self._row(row)

    def list_pending(self, tenant_id: str) -> list[dict]:
        with self._tx(tenant_id) as cur:
            cur.execute("SELECT * FROM approvals WHERE status = 'pending' ORDER BY created_at")
            return [self._row(r) for r in cur.fetchall()]

    def update(self, tenant_id: str, approval_id, changes: dict) -> None:
        if not changes:
            return
        cols = ", ".join(f"{k} = %s" for k in changes)
        # jsonb columns (e.g. proposed_action) need the Json adapter.
        vals = [self._Json(v) if isinstance(v, dict) else v for v in changes.values()]
        vals.append(str(approval_id))
        with self._tx(tenant_id) as cur:
            cur.execute(f"UPDATE approvals SET {cols} WHERE id = %s", vals)


class Greenlight:
    def __init__(self, store: ApprovalStore | None = None):
        self.store = store or InMemoryApprovalStore()

    # --- matches agents.tools.base.Greenlight.propose(...) ---
    def propose(self, *, tenant_id: str, action: str, agent: str | None,
                reasoning: str, value_at_stake: float | None, payload: dict) -> dict:
        approval_id = self.store.insert({
            "tenant_id": tenant_id,
            "proposed_action": {"action": action, **payload},
            "agent": agent,
            "reasoning": reasoning,
            "value_at_stake": value_at_stake,
            "status": "pending",
        })
        return self.store.get(tenant_id, approval_id)

    def list_pending(self, tenant_id: str) -> list[dict]:
        return self.store.list_pending(tenant_id)

    def decide(self, tenant_id: str, approval_id: int, decision: str, *, edits: dict | None = None,
               deny_message: str = "", decided_by: str | None = None) -> dict:
        """Apply a human decision. 'approve' | 'edit' (approve with edits) | 'deny'.

        tenant_id is the verified per-request tenant (THE TRUST RULE) — threaded into every store call
        so RLS scopes the read/write; the store never relies on shared connection state.
        """
        rec = self.store.get(tenant_id, approval_id)
        if rec is None or rec["status"] != "pending":
            raise ValueError(f"approval {approval_id} not pending")
        if decision == "deny":
            changes = {"status": "denied", "deny_message": deny_message, "decided_by": decided_by}
        elif decision in ("approve", "edit"):
            action = dict(rec["proposed_action"])
            if decision == "edit" and edits:
                action.update(edits)
            changes = {"status": "approved", "proposed_action": action, "decided_by": decided_by}
        else:
            raise ValueError(f"unknown decision {decision!r}")
        self.store.update(tenant_id, approval_id, changes)
        return self.store.get(tenant_id, approval_id)

    def to_ma_confirmation(self, rec: dict, tool_use_id: str) -> dict:
        """The Managed Agents reply event for this decision (VERIFY against live SDK; not sent here)."""
        if rec["status"] == "approved":
            return {"type": "user.tool_confirmation", "tool_use_id": tool_use_id, "result": "allow",
                    "edited_input": rec["proposed_action"]}
        return {"type": "user.tool_confirmation", "tool_use_id": tool_use_id, "result": "deny",
                "deny_message": rec.get("deny_message", "")}
