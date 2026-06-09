"""Conversation analytics (Build Guide Step 38).

`Analytics.record(event)` persists interaction events — utterance, tool_call, approval, click — to an
injected analytics store (in-memory fake offline; the real store is an Aurora table with RLS). These
are sourced from the same event stream as agent traces, so the product analytics and the audit trail
agree.

Tenant-scoped: every event carries its `tenant_id`, and the store lists/queries strictly within a
tenant. We never accept or return cross-tenant events.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class EventType(str, Enum):
    UTTERANCE = "utterance"     # a user message into the conversation
    TOOL_CALL = "tool_call"     # a tool was invoked during a turn
    APPROVAL = "approval"       # a Greenlight proposal was raised / decided
    CLICK = "click"             # a UI interaction (citation click, button, etc.)


@dataclass
class Event:
    tenant_id: str
    type: EventType
    session_id: str | None = None
    payload: dict = field(default_factory=dict)
    ts: float | None = None     # injectable for determinism; defaults to wall clock at record time

    def as_row(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "type": EventType(self.type).value,
            "session_id": self.session_id,
            "payload": dict(self.payload),
            "ts": self.ts,
        }


class AnalyticsStore(Protocol):
    """Tenant-scoped persistence for interaction events."""

    def insert(self, row: dict) -> int: ...
    def list(self, tenant_id: str, *, type: str | None = None) -> list[dict]: ...


class InMemoryAnalyticsStore:
    """Offline analytics store (the real one is an Aurora table with RLS). Tenant isolation is
    enforced on read: `list` only ever returns rows for the requested tenant."""

    def __init__(self) -> None:
        self._rows: list[dict] = []

    def insert(self, row: dict) -> int:
        rid = len(self._rows) + 1
        self._rows.append({"id": rid, **row})
        return rid

    def list(self, tenant_id: str, *, type: str | None = None) -> list[dict]:
        rows = [r for r in self._rows if r["tenant_id"] == tenant_id]
        if type is not None:
            rows = [r for r in rows if r["type"] == type]
        return rows


class Analytics:
    """Thin recorder over an injected store. Stamps a timestamp if the event lacks one."""

    def __init__(self, store: AnalyticsStore | None = None, *, clock: Any = None) -> None:
        self.store = store or InMemoryAnalyticsStore()
        # `clock` is injectable (a no-arg callable) for deterministic tests; defaults to time.time.
        self._clock = clock or time.time

    def record(self, event: Event) -> dict:
        if event.ts is None:
            event.ts = self._clock()
        row = event.as_row()
        rid = self.store.insert(row)
        return {"id": rid, **row}

    def list(self, tenant_id: str, *, type: EventType | str | None = None) -> list[dict]:
        t = EventType(type).value if isinstance(type, EventType) else type
        return self.store.list(tenant_id, type=t)
