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
    def insert(self, row: dict) -> int: ...
    def get(self, approval_id: int) -> dict | None: ...
    def list_pending(self, tenant_id: str) -> list[dict]: ...
    def update(self, approval_id: int, changes: dict) -> None: ...


class InMemoryApprovalStore:
    """Offline approval store (the real one is `approvals` in Aurora with RLS)."""

    def __init__(self):
        self._rows: dict[int, dict] = {}
        self._n = 0

    def insert(self, row: dict) -> int:
        self._n += 1
        row = {"id": self._n, **row}
        self._rows[self._n] = row
        return self._n

    def get(self, approval_id: int) -> dict | None:
        return self._rows.get(approval_id)

    def list_pending(self, tenant_id: str) -> list[dict]:
        return [r for r in self._rows.values() if r["tenant_id"] == tenant_id and r["status"] == "pending"]

    def update(self, approval_id: int, changes: dict) -> None:
        self._rows[approval_id].update(changes)


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
