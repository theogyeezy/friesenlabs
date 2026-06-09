"""Saved views — save & edit (Build Guide Phase 7, Step 43).

Persist the validated spec as the source of truth (in `saved_views`), the prompt as metadata, with a
version bump on every change. Two edit paths: NL refine (the model patches the existing spec) and
direct edit (spec tweaks). Because the spec binds to governed Cube metrics (not frozen SQL), saved
views stay correct as metric definitions evolve.
"""
from __future__ import annotations

from typing import Callable, Protocol

from shared import view_spec


class SavedViewStore(Protocol):
    def insert(self, row: dict) -> None: ...
    def latest(self, tenant_id: str, view_id: str) -> dict | None: ...
    def list(self, tenant_id: str) -> list[dict]: ...
    def bind_tenant(self, tenant_id: str) -> None: ...


class InMemorySavedViewStore:
    """Offline store (the real one is `PgSavedViewStore` over Aurora, tenant-scoped via RLS)."""

    def __init__(self):
        self.rows: list[dict] = []

    def bind_tenant(self, tenant_id: str) -> None:
        pass

    def insert(self, row: dict) -> None:
        self.rows.append(dict(row))

    def latest(self, tenant_id: str, view_id: str) -> dict | None:
        versions = [r for r in self.rows if r["tenant_id"] == tenant_id and r["view_id"] == view_id]
        return max(versions, key=lambda r: r["version"]) if versions else None

    def list(self, tenant_id: str) -> list[dict]:
        # latest version per view_id
        latest: dict[str, dict] = {}
        for r in self.rows:
            if r["tenant_id"] != tenant_id:
                continue
            if r["view_id"] not in latest or r["version"] > latest[r["view_id"]]["version"]:
                latest[r["view_id"]] = r
        return list(latest.values())


class PgSavedViewStore:
    """Aurora-backed saved-views store over `saved_views`. Connects as crm_app and SETs
    app.current_tenant so RLS scopes every read/write. Import-safe (lazy psycopg2)."""

    def __init__(self, dsn: str):
        import psycopg2  # noqa: PLC0415 — guarded
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

    def insert(self, row: dict) -> None:
        self.bind_tenant(row["tenant_id"])
        with self._cur() as cur:
            cur.execute(
                "INSERT INTO saved_views (tenant_id, view_id, version, spec_json, semantic_refs, "
                "source_prompt, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (row["tenant_id"], row["view_id"], row["version"], self._Json(row["spec_json"]),
                 self._Json(row.get("semantic_refs") or []), row.get("source_prompt"), row.get("created_by")),
            )
        self._conn.commit()

    def latest(self, tenant_id: str, view_id: str) -> dict | None:
        self.bind_tenant(tenant_id)
        with self._cur() as cur:
            cur.execute(
                "SELECT * FROM saved_views WHERE view_id = %s ORDER BY version DESC LIMIT 1", (view_id,)
            )
            row = cur.fetchone()
        return dict(row) if row else None

    def list(self, tenant_id: str) -> list[dict]:
        self.bind_tenant(tenant_id)
        with self._cur() as cur:
            cur.execute(
                "SELECT DISTINCT ON (view_id) * FROM saved_views ORDER BY view_id, version DESC"
            )
            return [dict(r) for r in cur.fetchall()]


class SavedViews:
    def __init__(self, store: SavedViewStore | None = None, allowed_members: set[str] | None = None):
        self.store = store or InMemorySavedViewStore()
        self.allowed_members = allowed_members

    def _persist(self, tenant_id: str, spec: dict, source_prompt: str, created_by: str, version: int) -> dict:
        view_spec.validate(spec, allowed_members=self.allowed_members)  # never persist an invalid spec
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
