"""Saved views — save & edit (Build Guide Phase 7, Step 43).

Persist the validated spec as the source of truth (in `saved_views`), the prompt as metadata, with a
version bump on every change. Two edit paths: NL refine (the model patches the existing spec) and
direct edit (spec tweaks). Because the spec binds to governed Cube metrics (not frozen SQL), saved
views stay correct as metric definitions evolve.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Callable, Protocol

from shared import view_spec


class SavedViewStore(Protocol):
    def insert(self, row: dict) -> None: ...
    def latest(self, tenant_id: str, view_id: str) -> dict | None: ...
    def list(self, tenant_id: str) -> list[dict]: ...


class InMemorySavedViewStore:
    """Offline store (the real one is `PgSavedViewStore` over Aurora, tenant-scoped via RLS)."""

    def __init__(self):
        self.rows: list[dict] = []

    def insert(self, row: dict) -> None:
        self.rows.append(dict(row))

    def latest(self, tenant_id: str, view_id: str) -> dict | None:
        versions = [r for r in self.rows
                    if str(r["tenant_id"]) == str(tenant_id) and r["view_id"] == view_id]
        return max(versions, key=lambda r: r["version"]) if versions else None

    def list(self, tenant_id: str) -> list[dict]:
        # latest version per view_id
        latest: dict[str, dict] = {}
        for r in self.rows:
            if str(r["tenant_id"]) != str(tenant_id):
                continue
            if r["view_id"] not in latest or r["version"] > latest[r["view_id"]]["version"]:
                latest[r["view_id"]] = r
        return list(latest.values())


class PgSavedViewStore:
    """Aurora-backed saved-views store over `saved_views`. Connects as crm_app.

    Each operation checks out a connection from a thread-safe pool and runs in ONE transaction that
    begins with `SET LOCAL app.current_tenant = %s` (the tenant for THIS operation) — so RLS scopes
    every read/write and the GUC auto-resets at txn end, never leaking across the pooled connection.
    Import-safe (lazy psycopg2)."""

    def __init__(self, dsn: str):
        import psycopg2  # noqa: PLC0415 — guarded
        import psycopg2.pool  # noqa: PLC0415
        from psycopg2.extras import Json, RealDictCursor  # noqa: PLC0415
        self._psycopg2 = psycopg2
        self._Json = Json
        self._cursor_factory = RealDictCursor
        pool_max = int(os.environ.get("UPLIFT_DB_POOL_MAX", "10"))
        # min == max: fixed-size pool retains returned connections (avoids TCP/auth churn under load).
        self._pool = psycopg2.pool.ThreadedConnectionPool(1, pool_max, dsn)

    def _getconn(self):
        """Check out a pooled connection, waiting briefly if exhausted (see PgApprovalStore._getconn)."""
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
        """Yield a RealDict cursor inside a single tenant-scoped transaction (see PgApprovalStore._tx)."""
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

    def insert(self, row: dict) -> None:
        with self._tx(row["tenant_id"]) as cur:
            cur.execute(
                "INSERT INTO saved_views (tenant_id, view_id, version, spec_json, semantic_refs, "
                "source_prompt, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (row["tenant_id"], row["view_id"], row["version"], self._Json(row["spec_json"]),
                 self._Json(row.get("semantic_refs") or []), row.get("source_prompt"), row.get("created_by")),
            )

    def latest(self, tenant_id: str, view_id: str) -> dict | None:
        with self._tx(tenant_id) as cur:
            cur.execute(
                "SELECT * FROM saved_views WHERE view_id = %s ORDER BY version DESC LIMIT 1", (view_id,)
            )
            row = cur.fetchone()
        return dict(row) if row else None

    def list(self, tenant_id: str) -> list[dict]:
        with self._tx(tenant_id) as cur:
            cur.execute(
                "SELECT DISTINCT ON (view_id) * FROM saved_views ORDER BY view_id, version DESC"
            )
            return [dict(r) for r in cur.fetchall()]


class SavedViews:
    def __init__(self, store: SavedViewStore | None = None, allowed_members: set[str] | None = None,
                 members_provider=None):
        self.store = store or InMemorySavedViewStore()
        self.allowed_members = allowed_members
        # Optional per-tenant resolver: tenant_id -> set[str] of real Cube members (live catalog).
        # In production wire this to the Cube catalog so specs are validated against THAT tenant's
        # members; without it the static allowed_members is used (tests) — but never silently skip
        # when a provider is configured.
        self.members_provider = members_provider

    def _members_for(self, tenant_id: str) -> set[str] | None:
        if self.members_provider is not None:
            return set(self.members_provider(tenant_id))
        return self.allowed_members

    def _validate_dashboard_items(self, tenant_id: str, spec: dict) -> None:
        """A kind=dashboard spec composes SAVED views. Never persist one whose references are
        broken: every item must resolve to an existing view of THIS tenant, must not be a
        dashboard itself (no nesting — keeps render depth and cycle-freedom by construction),
        and must not be the dashboard's own id (self-reference)."""
        for item in spec.get("items", []):
            ref_id = item["view_id"]
            if ref_id == spec["view_id"]:
                raise view_spec.ValidationError("dashboard references itself", ref_id)
            ref = self.store.latest(tenant_id, ref_id)
            if ref is None:
                raise view_spec.ValidationError("dashboard references unknown view", ref_id)
            if view_spec.is_dashboard(ref.get("spec_json") or {}):
                raise view_spec.ValidationError(
                    "dashboard cannot embed another dashboard", ref_id
                )

    def _persist(self, tenant_id: str, spec: dict, source_prompt: str, created_by: str, version: int) -> dict:
        # Validate against THIS tenant's real Cube members (never persist an invalid spec).
        view_spec.validate(spec, allowed_members=self._members_for(tenant_id))
        if view_spec.is_dashboard(spec):
            self._validate_dashboard_items(tenant_id, spec)
        row = {
            "tenant_id": tenant_id,
            "view_id": spec["view_id"],
            "version": version,
            "spec_json": spec,
            "semantic_refs": spec.get("semantic_refs", []),
            "source_prompt": source_prompt,
            "created_by": created_by,
        }
        self.store.insert(row)
        return row

    def save(self, tenant_id: str, spec: dict, *, source_prompt: str = "", created_by: str = "") -> dict:
        existing = self.store.latest(tenant_id, spec["view_id"])
        version = (existing["version"] + 1) if existing else 1
        spec = {**spec, "version": version}
        return self._persist(tenant_id, spec, source_prompt, created_by, version)

    def refine_nl(self, tenant_id: str, view_id: str, instruction: str,
                  patcher: Callable[[dict, str], dict], *, created_by: str = "") -> dict:
        """NL refine: the agent patches the existing spec ('make it a line chart, last 90 days')."""
        current = self.store.latest(tenant_id, view_id)
        if current is None:
            raise ValueError(f"no such view {view_id}")
        patched = patcher(current["spec_json"], instruction)  # injected model patch; fake in tests
        return self.save(tenant_id, patched, source_prompt=instruction, created_by=created_by)

    def edit_direct(self, tenant_id: str, view_id: str, new_spec: dict, *, created_by: str = "") -> dict:
        """Direct edit: control/spec tweaks. Validated + versioned like any other save."""
        return self.save(tenant_id, new_spec, source_prompt="(direct edit)", created_by=created_by)

    def get(self, tenant_id: str, view_id: str) -> dict | None:
        return self.store.latest(tenant_id, view_id)

    # --- kind=dashboard composition (spec_version 2) -------------------------------------
    # Dashboards REUSE this store: a dashboard is a saved_views row whose spec_json carries
    # the kind="dashboard" discriminator (no new table). The helpers below split the two
    # kinds so view consumers (the reports gallery, view pickers) keep seeing only renderable
    # view specs and dashboard consumers see only dashboards.

    @staticmethod
    def _row_is_dashboard(row: dict) -> bool:
        return view_spec.is_dashboard(row.get("spec_json") or {})

    def list_views(self, tenant_id: str) -> list[dict]:
        """Latest version per view_id, EXCLUDING kind=dashboard rows."""
        return [r for r in self.store.list(tenant_id) if not self._row_is_dashboard(r)]

    def list_dashboards(self, tenant_id: str) -> list[dict]:
        """Latest version per view_id, ONLY kind=dashboard rows."""
        return [r for r in self.store.list(tenant_id) if self._row_is_dashboard(r)]

    def resolve_dashboard(self, tenant_id: str, view_id: str) -> tuple[dict, dict[str, dict]] | None:
        """The latest dashboard row + the latest row of every view it references, in one shot
        (what the dashboard screen renders). None when the id is missing or not a dashboard.
        A referenced view that has since vanished is simply absent from the map — the renderer
        shows an honest per-panel placeholder rather than the whole dashboard failing."""
        dash = self.store.latest(tenant_id, view_id)
        if dash is None or not self._row_is_dashboard(dash):
            return None
        resolved: dict[str, dict] = {}
        for item in (dash.get("spec_json") or {}).get("items", []):
            ref_id = item.get("view_id")
            if not ref_id or ref_id in resolved:
                continue
            ref = self.store.latest(tenant_id, ref_id)
            if ref is not None and not self._row_is_dashboard(ref):
                resolved[ref_id] = ref
        return dash, resolved
