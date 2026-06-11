"""Connector sync-run history over the `integration_sync_runs` table (FORCE'd RLS; crm_app).

The persistence half of the async "Sync now" flow (api/integrations_routes.py):

  * `start()` opens a run — INSERTs a `running` row. The table's partial UNIQUE index
    (`integration_sync_runs_one_running`: at most one `running` row per tenant+source) is the
    CONCURRENCY GUARD — a second concurrent start hits the index and `start()` returns None,
    which the route maps to 409. A `running` row older than ``STALE_RUNNING_MINUTES`` is a
    crashed/redeployed runner's orphan: `start()` flips it to `aborted` first and proceeds, so
    one dead task never wedges a tenant's connector forever.
  * `finish()` closes a run with its terminal status + SyncResult metrics. `error` carries an
    exception CLASS NAME only, never a message (a provider error message could embed
    credential material).
  * `latest()` answers the per-connector "last synced" surface in GET /integrations;
    `list_runs()` backs GET /integrations/{name}/syncs (newest-first, capped).

EXACTLY the PgTraceStore connection pattern via the shared `_PgTenantClient` plumbing: every
operation is ONE transaction beginning with `SET LOCAL app.current_tenant = %s`, so RLS scopes
every read/write and the GUC resets at txn end. THE TRUST RULE — `tenant_id` is the verified
claim threaded in by the route, never read from env/headers/body here. Import-safe: psycopg2
only on the DSN construction path; tests inject a `conn_factory` or use InMemorySyncRunStore.
"""
from __future__ import annotations

import threading
from typing import Any, Protocol, runtime_checkable

#: A `running` row older than this is an orphan (the API task died/redeployed mid-sync) —
#: `start()` flips it to `aborted` and lets the new run proceed.
STALE_RUNNING_MINUTES = 30

#: Cap for list_runs — the history surface is a recent-activity readout, not an export.
MAX_RUN_LIST = 50

_METRIC_FIELDS = ("pulled", "landed_rows", "chunks", "embedded", "skipped")


@runtime_checkable
class SyncRunStore(Protocol):
    """Seam the routes depend on (PgSyncRunStore in prod, InMemorySyncRunStore in tests)."""

    def start(self, tenant_id: str, source: str, *, triggered_by: str = "api") -> dict | None: ...

    def finish(self, tenant_id: str, run_id: str, *, status: str,
               metrics: dict | None = None, error: str | None = None) -> None: ...

    def latest(self, tenant_id: str) -> dict[str, dict]: ...

    def list_runs(self, tenant_id: str, source: str, *, limit: int = 20) -> list[dict]: ...


def _row_payload(r: dict) -> dict:
    """Serialize a runs row for API responses (iso timestamps, str id — JSON-safe)."""
    from api.pg_clients import _as_iso, _as_str  # noqa: PLC0415

    return {
        "id": _as_str(r.get("id")),
        "source": r.get("source"),
        "triggered_by": r.get("triggered_by"),
        "status": r.get("status"),
        "started_at": _as_iso(r.get("started_at")),
        "finished_at": _as_iso(r.get("finished_at")),
        **{f: r.get(f) for f in _METRIC_FIELDS},
        "error": r.get("error"),
    }


class PgSyncRunStore:
    """Aurora-backed SyncRunStore (see the module docstring for the flow + guard semantics)."""

    def __init__(self, dsn: str | None = None, *, conn_factory=None):
        from api.pg_clients import _PgTenantClient  # noqa: PLC0415 — shared pool plumbing
        self._client = _PgTenantClient(dsn, conn_factory=conn_factory)

    def start(self, tenant_id: str, source: str, *, triggered_by: str = "api") -> dict | None:
        from api.pg_clients import _dict_one  # noqa: PLC0415
        try:
            with self._client._tx(tenant_id) as cur:
                # Reap an orphaned runner first (crashed task / redeploy mid-sync): flip a
                # too-old `running` row to `aborted` inside the SAME txn, so the partial
                # unique index is free for the INSERT below. A FRESH running row is left
                # alone — the INSERT then hits the index and this start() answers None (409).
                cur.execute(
                    "UPDATE integration_sync_runs SET status = 'aborted', "
                    "finished_at = now(), error = 'stale_runner_reaped' "
                    "WHERE source = %s AND status = 'running' "
                    "AND started_at < now() - make_interval(mins => %s)",
                    (source, STALE_RUNNING_MINUTES),
                )
                cur.execute(
                    "INSERT INTO integration_sync_runs (tenant_id, source, triggered_by) "
                    "VALUES (%s, %s, %s) "
                    "RETURNING id, source, triggered_by, status, started_at, finished_at, "
                    "pulled, landed_rows, chunks, embedded, skipped, error",
                    (str(tenant_id), source, triggered_by),
                )
                got = _dict_one(cur)
        except Exception as exc:  # noqa: BLE001 — narrowed immediately below
            if _is_unique_violation(exc):
                return None  # a fresh run is already in flight — the route answers 409
            raise
        return _row_payload(got) if got else None

    def finish(self, tenant_id: str, run_id: str, *, status: str,
               metrics: dict | None = None, error: str | None = None) -> None:
        m = metrics or {}
        with self._client._tx(tenant_id) as cur:
            cur.execute(
                "UPDATE integration_sync_runs SET status = %s, finished_at = now(), "
                "pulled = %s, landed_rows = %s, chunks = %s, embedded = %s, skipped = %s, "
                "error = %s "
                "WHERE id = %s AND status = 'running'",
                (status, *(m.get(f) for f in _METRIC_FIELDS), error, str(run_id)),
            )

    def latest(self, tenant_id: str) -> dict[str, dict]:
        from api.pg_clients import _dict_rows  # noqa: PLC0415
        with self._client._tx(tenant_id) as cur:
            cur.execute(
                "SELECT DISTINCT ON (source) id, source, triggered_by, status, started_at, "
                "finished_at, pulled, landed_rows, chunks, embedded, skipped, error "
                "FROM integration_sync_runs ORDER BY source, started_at DESC",
            )
            rows = _dict_rows(cur)
        return {r["source"]: _row_payload(r) for r in rows}

    def list_runs(self, tenant_id: str, source: str, *, limit: int = 20) -> list[dict]:
        from api.pg_clients import _dict_rows  # noqa: PLC0415
        n = max(1, min(int(limit), MAX_RUN_LIST))
        with self._client._tx(tenant_id) as cur:
            cur.execute(
                "SELECT id, source, triggered_by, status, started_at, finished_at, "
                "pulled, landed_rows, chunks, embedded, skipped, error "
                "FROM integration_sync_runs WHERE source = %s "
                "ORDER BY started_at DESC LIMIT %s",
                (source, n),
            )
            rows = _dict_rows(cur)
        return [_row_payload(r) for r in rows]


def _is_unique_violation(exc: Exception) -> bool:
    """psycopg2 UniqueViolation (SQLSTATE 23505) OR a fake exception class named after it —
    the same duck-typed detection style as the AWS not-found helpers."""
    if getattr(exc, "pgcode", None) == "23505":
        return True
    return exc.__class__.__name__ == "UniqueViolation"


class InMemorySyncRunStore:
    """Offline/test SyncRunStore — same guard semantics (one running row per tenant+source),
    no staleness reaping (tests drive transitions explicitly). Thread-safe: the background
    sync task finishes runs from another thread."""

    def __init__(self):
        self._runs: list[dict] = []
        self._next = 1
        self._lock = threading.Lock()

    def start(self, tenant_id: str, source: str, *, triggered_by: str = "api") -> dict | None:
        with self._lock:
            for r in self._runs:
                if (r["tenant_id"] == tenant_id and r["source"] == source
                        and r["status"] == "running"):
                    return None
            row = {
                "id": str(self._next), "tenant_id": tenant_id, "source": source,
                "triggered_by": triggered_by, "status": "running",
                "started_at": f"t{self._next}", "finished_at": None,
                **{f: None for f in _METRIC_FIELDS}, "error": None,
            }
            self._next += 1
            self._runs.append(row)
            return {k: v for k, v in row.items() if k != "tenant_id"}

    def finish(self, tenant_id: str, run_id: str, *, status: str,
               metrics: dict | None = None, error: str | None = None) -> None:
        m = metrics or {}
        with self._lock:
            for r in self._runs:
                if r["tenant_id"] == tenant_id and r["id"] == run_id and r["status"] == "running":
                    r.update(status=status, finished_at=f"t{self._next}", error=error,
                             **{f: m.get(f) for f in _METRIC_FIELDS})
                    self._next += 1
                    return

    def latest(self, tenant_id: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        with self._lock:
            for r in self._runs:  # insertion order == started order
                if r["tenant_id"] == tenant_id:
                    out[r["source"]] = {k: v for k, v in r.items() if k != "tenant_id"}
        return out

    def list_runs(self, tenant_id: str, source: str, *, limit: int = 20) -> list[dict]:
        n = max(1, min(int(limit), MAX_RUN_LIST))
        with self._lock:
            rows = [
                {k: v for k, v in r.items() if k != "tenant_id"}
                for r in reversed(self._runs)
                if r["tenant_id"] == tenant_id and r["source"] == source
            ]
        return rows[:n]


# Quiet "imported but unused" for the Protocol's structural use in type checks.
_ = Any
