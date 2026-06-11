"""Decision traces (Build Guide Phase 5, Step 31).

Per-step records that power the customer-facing "why I did this" UI and deal-card narration.
Capture: agent, tool, minimized inputs, summarized outputs, reasoning, timestamps, tokens.

The real store is `PgTraceStore` over the EXISTING `traces` table in Aurora (db/schema.sql —
tenant-scoped, FORCE'd RLS): the gate's per-run trace write lands in Pg in prod (wired in
api/asgi.py build_app), using the exact per-op `SET LOCAL app.current_tenant` transaction
pattern of PgApprovalStore (via api/pg_clients._PgTenantClient — one shared, reviewed plumbing).
Offline we use an in-memory list.

Wire shape: the `traces` table has (id, tenant_id, session_id, step, agent, kind, payload,
created_at); the gate's flat row (tool/inputs/outputs/reasoning/tokens) rides in `payload` jsonb.
Both stores' `list(...)` returns NORMALIZED flat rows (id/tenant_id/ts/agent/kind/tool/
inputs/outputs/reasoning/tokens) plus an opaque next-page cursor, so /control/traces
(api/routes_control.py) serves either store identically.
"""
from __future__ import annotations

import json
import threading
import uuid as _uuid
from datetime import datetime, timezone
from typing import Protocol

# How many trace rows may leave a single list() call (the route clamps to this too).
DEFAULT_TRACE_LIMIT = 50
MAX_TRACE_LIMIT = 200

# The flat per-run fields the gate writes (append_trace) that ride in the Pg `payload` jsonb.
_PAYLOAD_FIELDS = ("tool", "inputs", "outputs", "reasoning", "tokens")


def _clamp(limit, default: int = DEFAULT_TRACE_LIMIT) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return default
    return max(1, min(n, MAX_TRACE_LIMIT))


class TraceStore(Protocol):
    def append(self, row: dict) -> object: ...

    def list(self, *, tenant_id: str, limit: int = DEFAULT_TRACE_LIMIT,
             cursor: str | None = None) -> tuple[list[dict], str | None]: ...


class InMemoryTraceStore:
    def __init__(self):
        self.rows: list[dict] = []
        self._lock = threading.Lock()

    def append(self, row: dict) -> int:
        with self._lock:
            tid = len(self.rows) + 1
            self.rows.append({"id": tid,
                              "ts": datetime.now(timezone.utc).isoformat(), **row})
            return tid

    def list(self, *, tenant_id: str, limit: int = DEFAULT_TRACE_LIMIT,
             cursor: str | None = None) -> tuple[list[dict], str | None]:
        """Tenant-scoped, newest-first page. The cursor is the last-seen row id (opaque to
        callers); a malformed cursor raises ValueError (the route maps it to 422)."""
        n = _clamp(limit)
        before: int | None = None
        if cursor is not None:
            if not str(cursor).isdigit():
                raise ValueError("invalid cursor")
            before = int(cursor)
        with self._lock:
            rows = [dict(r) for r in reversed(self.rows)
                    if str(r.get("tenant_id")) == str(tenant_id)
                    and (before is None or r["id"] < before)]
        page = rows[:n]
        next_cursor = str(page[-1]["id"]) if len(page) == n and page else None
        return page, next_cursor


class PgTraceStore:
    """Aurora-backed trace store over the `traces` table (FORCE'd RLS; non-owner crm_app role).

    EXACTLY the PgApprovalStore connection pattern, via the shared `_PgTenantClient` plumbing:
    every operation checks a connection out of a thread-safe pool (or a per-op conn_factory) and
    runs in ONE transaction that begins with `SET LOCAL app.current_tenant = %s` — RLS scopes
    every read/write and the GUC auto-resets at txn end, never leaking across the pool. The
    payload is serialized to JSON text and bound as a `%s::jsonb` param (no adapter dependency,
    works with offline fakes). Import-safe: psycopg2 only on the DSN construction path.
    """

    def __init__(self, dsn: str | None = None, *, conn_factory=None):
        from api.pg_clients import _PgTenantClient  # noqa: PLC0415 — shared pool plumbing
        self._client = _PgTenantClient(dsn, conn_factory=conn_factory)

    def append(self, row: dict) -> str:
        from api.pg_clients import _dict_one  # noqa: PLC0415
        payload = {k: row.get(k) for k in _PAYLOAD_FIELDS}
        with self._client._tx(row["tenant_id"]) as cur:
            cur.execute(
                "INSERT INTO traces (tenant_id, agent, kind, payload) "
                "VALUES (%s,%s,%s,%s::jsonb) RETURNING id",
                (str(row["tenant_id"]), row.get("agent"), row.get("kind"),
                 json.dumps(payload)),
            )
            got = _dict_one(cur)
        if got is None:
            raise RuntimeError("trace insert returned no row")
        return str(got["id"])

    @staticmethod
    def _parse_cursor(cursor: str) -> tuple[str, str]:
        """Validate + split the opaque keyset cursor ('<created_at iso>|<uuid>').
        Malformed input raises ValueError BEFORE any SQL is issued (route maps to 422)."""
        ts, sep, rid = str(cursor).partition("|")
        if not sep:
            raise ValueError("invalid cursor")
        datetime.fromisoformat(ts)   # raises ValueError on junk
        _uuid.UUID(rid)              # raises ValueError on junk
        return ts, rid

    def list(self, *, tenant_id: str, limit: int = DEFAULT_TRACE_LIMIT,
             cursor: str | None = None) -> tuple[list[dict], str | None]:
        """Tenant-scoped (RLS via SET LOCAL), newest-first keyset page over (created_at, id)."""
        from api.pg_clients import _as_iso, _as_str, _dict_rows  # noqa: PLC0415
        n = _clamp(limit)
        where = ""
        params: list = []
        if cursor is not None:
            ts, rid = self._parse_cursor(cursor)
            where = "WHERE (created_at, id) < (%s::timestamptz, %s::uuid) "
            params += [ts, rid]
        with self._client._tx(tenant_id) as cur:
            cur.execute(
                "SELECT id, tenant_id, created_at, agent, kind, payload FROM traces "
                + where + "ORDER BY created_at DESC, id DESC LIMIT %s",
                (*params, n),
            )
            raw = _dict_rows(cur)
        rows = []
        for r in raw:
            payload = r.get("payload") or {}
            if isinstance(payload, str):  # tolerate drivers/fakes returning jsonb as text
                payload = json.loads(payload)
            rows.append({
                "id": _as_str(r.get("id")),
                "tenant_id": _as_str(r.get("tenant_id")),
                "ts": _as_iso(r.get("created_at")),
                "agent": r.get("agent"),
                "kind": r.get("kind"),
                **{k: payload.get(k) for k in _PAYLOAD_FIELDS},
            })
        next_cursor = None
        if len(raw) == n and raw:
            last = raw[-1]
            next_cursor = f"{_as_iso(last['created_at'])}|{_as_str(last['id'])}"
        return rows, next_cursor


def _minimize(value, limit: int = 200):
    """Minimize inputs / summarize outputs so traces never store full payloads/PII verbatim."""
    s = str(value)
    return s if len(s) <= limit else s[:limit] + "…"


def append_trace(store: TraceStore, *, tenant_id: str, agent: str | None, tool: str,
                 kind: str, inputs=None, outputs=None, reasoning: str = "", tokens: int | None = None):
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
