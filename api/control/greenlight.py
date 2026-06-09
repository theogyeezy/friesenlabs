"""Greenlight — the human-in-the-loop approval queue (Build Guide Phase 5, Step 30).

When a side-effecting action needs confirmation, persist it to `approvals` and surface it: every item
carries the agent's reasoning, an editable draft, and the value at stake. A human approves / edits /
denies. Maps to the Managed Agents tool-confirmation reply (user.tool_confirmation allow/deny) — that
mapping is authored + flagged "verify"; it is never called live here.

Conforms to the `Greenlight` protocol in agents/tools/base.py (so Phase 4 tools route through it).
"""
from __future__ import annotations

from typing import Any, Protocol


class ApprovalStore(Protocol):
    def insert(self, row: dict) -> object: ...
    def get(self, approval_id: object) -> dict | None: ...
    def list_pending(self, tenant_id: str) -> list[dict]: ...
    def update(self, approval_id: object, changes: dict) -> None: ...
    def bind_tenant(self, tenant_id: str) -> None: ...


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

    def bind_tenant(self, tenant_id: str) -> None:
        pass  # in-memory has no RLS; isolation is enforced by explicit tenant filtering

    def insert(self, row: dict) -> int:
        self._n += 1
        row = {"id": self._n, **row}
        self._rows[self._n] = row
        return self._n

    def get(self, approval_id) -> dict | None:
        return self._rows.get(self._key(approval_id))

    def list_pending(self, tenant_id: str) -> list[dict]:
        return [r for r in self._rows.values() if r["tenant_id"] == tenant_id and r["status"] == "pending"]

    def update(self, approval_id, changes: dict) -> None:
        self._rows[self._key(approval_id)].update(changes)


class PgApprovalStore:
    """Aurora-backed approval store over the `approvals` table.

    Connects as the non-owner crm_app role and SETs app.current_tenant before every access so Postgres
    RLS scopes all reads/writes to the tenant. Import-safe (psycopg2 imported lazily on construction).
    Ids are the table's uuids (as strings).
    """

    def __init__(self, dsn: str):
        import psycopg2  # noqa: PLC0415 — guarded
        self._psycopg2 = psycopg2
        from psycopg2.extras import Json, RealDictCursor  # noqa: PLC0415
        self._Json = Json
        self._cursor_factory = RealDictCursor
        self._conn = psycopg2.connect(dsn)
        self._tenant: str | None = None

    def bind_tenant(self, tenant_id: str) -> None:
        self._tenant = str(tenant_id)

    def _cur(self):
        cur = self._conn.cursor(cursor_factory=self._cursor_factory)
        if self._tenant is not None:
            cur.execute("SET app.current_tenant = %s", (self._tenant,))
        return cur

    def insert(self, row: dict) -> str:
        self.bind_tenant(row["tenant_id"])
        with self._cur() as cur:
            cur.execute(
                "INSERT INTO approvals (tenant_id, proposed_action, agent, reasoning, value_at_stake, status) "
                "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (row["tenant_id"], self._Json(row["proposed_action"]), row.get("agent"),
                 row.get("reasoning"), row.get("value_at_stake"), row.get("status", "pending")),
            )
            rid = cur.fetchone()["id"]
        self._conn.commit()
        return str(rid)

    def get(self, approval_id) -> dict | None:
        with self._cur() as cur:
            cur.execute("SELECT * FROM approvals WHERE id = %s", (str(approval_id),))
            row = cur.fetchone()
        return dict(row) if row else None

    def list_pending(self, tenant_id: str) -> list[dict]:
        self.bind_tenant(tenant_id)
        with self._cur() as cur:
            cur.execute("SELECT * FROM approvals WHERE status = 'pending' ORDER BY created_at")
            return [dict(r) for r in cur.fetchall()]

    def update(self, approval_id, changes: dict) -> None:
        if not changes:
            return
        cols = ", ".join(f"{k} = %s" for k in changes)
        # jsonb columns (e.g. proposed_action) need the Json adapter.
        vals = [self._Json(v) if isinstance(v, dict) else v for v in changes.values()]
        vals.append(str(approval_id))
        with self._cur() as cur:
            cur.execute(f"UPDATE approvals SET {cols} WHERE id = %s", vals)
        self._conn.commit()


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
        return self.store.get(approval_id)

    def list_pending(self, tenant_id: str) -> list[dict]:
        return self.store.list_pending(tenant_id)

    def decide(self, approval_id: int, decision: str, *, edits: dict | None = None,
               deny_message: str = "", decided_by: str | None = None) -> dict:
        """Apply a human decision. 'approve' | 'edit' (approve with edits) | 'deny'."""
        rec = self.store.get(approval_id)
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
        self.store.update(approval_id, changes)
        return self.store.get(approval_id)

    def to_ma_confirmation(self, rec: dict, tool_use_id: str) -> dict:
        """The Managed Agents reply event for this decision (VERIFY against live SDK; not sent here)."""
        if rec["status"] == "approved":
            return {"type": "user.tool_confirmation", "tool_use_id": tool_use_id, "result": "allow",
                    "edited_input": rec["proposed_action"]}
        return {"type": "user.tool_confirmation", "tool_use_id": tool_use_id, "result": "deny",
                "deny_message": rec.get("deny_message", "")}
