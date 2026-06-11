"""Authed per-tenant view-data resolution — the missing data-loader endpoint.

The web dashboards render "No data yet" because nothing resolves a SAVED view-spec's
CubeQuery into rows. This module is that seam:

  POST /views/{id}/data    load the tenant's saved view, extract the CubeQuery from every
                           data-bearing panel, run each through the Cube client carrying the
                           tenant security context, and return `{rows: [...]}` (the primary
                           panel's rows) plus a per-panel `panels` array for multi-panel render.

THE TRUST RULE (CLAUDE.md hard constraint #6) is the whole point of this file: the tenant the
query runs as comes ONLY from the verified Cognito `custom:tenant_id` claim (the `current_tenant`
dependency every authed route rides) — NEVER a header, query param, or request body. The Cube
client mints a fresh per-request HS256 JWT embedding exactly that tenant; Cube's `queryRewrite`
force-filters every cube to it server-side. There is no other tenant input to this route.

Honest degradation, never a 500:
  * cube client not wired, or wired-but-unconfigured                  -> 503 (never fake rows)
  * unknown view id (or another tenant's view — RLS makes it unknown) -> 404
  * a Cube load that errors                                            -> 502 (upstream failure)

IMPORT SAFETY: importing this module touches no AWS/boto3/DB/network. The view spec is read from
the SAME SavedViews facade the rest of the app uses (RLS-scoped reads); the Cube client is the
SAME per-request-token client the executor/chat tools ride.
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from api.auth import TenantClaims

# Panel types whose CubeQuery lives under `query` (chart/table/funnel/leaderboard/cohort-grid).
_QUERY_PANELS = ("chart", "table", "funnel", "leaderboard", "cohort-grid")


def _panel_query(block: dict) -> dict | None:
    """The CubeQuery a single layout panel renders, or None when it carries no data query.

    - chart/table/funnel/leaderboard/cohort-grid -> the panel's `query`
    - kpi                                         -> the metric as a measure (+ its `filter`)
    - stat-with-sparkline                         -> its `trend` query (the time series it draws);
                                                     `metric`/`filter` is the headline number, the
                                                     trend is the row-bearing series.
    - markdown-note                               -> None (no data)
    """
    if not isinstance(block, dict):
        return None
    btype = block.get("type")
    if btype in _QUERY_PANELS:
        q = block.get("query")
        return q if isinstance(q, dict) else None
    if btype == "kpi":
        metric = block.get("metric")
        if not metric:
            return None
        # A kpi is a single measure; an optional `filter` CubeQuery narrows it.
        query: dict[str, Any] = {"measures": [metric]}
        flt = block.get("filter")
        if isinstance(flt, dict):
            for key in ("filters", "timeDimensions", "dimensions"):
                if flt.get(key):
                    query[key] = flt[key]
        return query
    if btype == "stat-with-sparkline":
        trend = block.get("trend")
        return trend if isinstance(trend, dict) else None
    return None


def _spec_queries(spec: dict) -> list[tuple[int, dict]]:
    """Every (panel_index, CubeQuery) pair in the view's layout, in layout order.

    Dashboards compose saved views by reference (they carry no CubeQuery of their own), so a
    dashboard spec yields nothing here — the data endpoint is for renderable views.
    """
    out: list[tuple[int, dict]] = []
    for i, block in enumerate(spec.get("layout", []) or []):
        q = _panel_query(block)
        if q is not None:
            out.append((i, q))
    return out


def mount_cube_data(app: FastAPI, deps, current_tenant) -> None:
    """Mount POST /views/{id}/data on `app`, claims-bound via the SAME current_tenant dependency
    every authed route rides (unauth/invalid tokens 401 before any work).

    `deps` is the app's ApiDeps: `deps.saved_views` is the RLS-scoped saved-view store and
    `deps.cube` is the per-request-token Cube client (None / unconfigured -> honest 503).
    """

    @app.post("/views/{view_id}/data")
    def view_data(view_id: str, claims: TenantClaims = Depends(current_tenant)):
        # Cube must be wired AND configured before we can resolve any rows — answer 503 (never
        # 500, never invented rows) so the web data-loader degrades to an honest "not configured".
        cube = getattr(deps, "cube", None)
        if cube is None or not getattr(cube, "configured", False):
            raise HTTPException(status_code=503, detail="cube not configured")

        # Load the saved view tenant-scoped — the tenant is the VERIFIED claim only (RLS scopes
        # the read; another tenant's view id simply does not resolve here -> 404).
        view = deps.saved_views.get(claims.tenant_id, view_id)
        if view is None:
            raise HTTPException(status_code=404, detail="no such view")

        spec = view.get("spec_json") or {}
        queries = _spec_queries(spec)
        panels: list[dict] = []
        primary_rows: list[dict] = []
        for panel_index, query in queries:
            # Run each panel's query as the verified tenant. The Cube client mints a fresh
            # per-request token embedding EXACTLY claims.tenant_id; Cube's queryRewrite enforces
            # the tenant filter server-side (THE TRUST RULE's Cube leg).
            result = cube.load(tenant_id=claims.tenant_id, query=query)
            status = result.get("status")
            if status == "unconfigured":
                # The client degraded mid-flight (e.g. endpoint-without-secret) — honest 503.
                raise HTTPException(status_code=503, detail="cube not configured")
            if status != "ok":
                # Upstream Cube failure (warming/timeout/error) — surface as a bad-gateway, never
                # a 500 and never a partial-success masquerading as data.
                raise HTTPException(
                    status_code=502, detail=result.get("error") or "cube query failed"
                )
            rows = result.get("rows") or []
            panels.append({"panel": panel_index, "rows": rows})
            if not primary_rows:
                primary_rows = rows

        # `rows` is the primary (first data-bearing) panel — the clean contract the web
        # data-loader consumes; `panels` rides additively for multi-panel views.
        return {"rows": primary_rows, "panels": panels}
