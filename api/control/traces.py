"""Decision traces (Build Guide Phase 5, Step 31).

Per-step records that power the customer-facing "why I did this" UI and deal-card narration.
Capture: agent, tool, minimized inputs, summarized outputs, reasoning, timestamps, tokens. The real
store is the `traces` table in Aurora (tenant-scoped via RLS); offline we use an in-memory list.
"""
from __future__ import annotations

from typing import Protocol


class TraceStore(Protocol):
    def append(self, row: dict) -> int: ...


class InMemoryTraceStore:
    def __init__(self):
        self.rows: list[dict] = []

    def append(self, row: dict) -> int:
        tid = len(self.rows) + 1
        self.rows.append({"id": tid, **row})
        return tid


def _minimize(value, limit: int = 200):
    """Minimize inputs / summarize outputs so traces never store full payloads/PII verbatim."""
    s = str(value)
    return s if len(s) <= limit else s[:limit] + "…"


def append_trace(store: TraceStore, *, tenant_id: str, agent: str | None, tool: str,
                 kind: str, inputs=None, outputs=None, reasoning: str = "", tokens: int | None = None) -> int:
    return store.append({
        "tenant_id": tenant_id,
        "agent": agent,
        "tool": tool,
        "kind": kind,  # executed | pending_approval | blocked
        "inputs": _minimize(inputs) if inputs is not None else None,
        "outputs": _minimize(outputs) if outputs is not None else None,
        "reasoning": reasoning,
        "tokens": tokens,
    })
