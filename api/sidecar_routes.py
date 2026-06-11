"""Sidecar routes — GET /sidecar/suggestions + POST /sidecar/act (the agentic layer).

Sidecar reads the tenant's CRM (deals board + contacts directory, RLS-scoped via the SAME PgCrmClient
every other authed route rides) and returns grounded next-action suggestions (api/sidecar.py). When
the user accepts one, this enqueues a DRAFT action in Greenlight — Sidecar never writes to the CRM
itself; the existing gate + appliers do, after the user signs off (the draft-only constraint).

THE TRUST RULE: tenant from the verified Cognito claim only. SECURITY on accept: the client sends a
suggestion id, NOT an action — the server RECOMPUTES the suggestions and proposes the matching one's
own predefined action, so a client can never inject an arbitrary Greenlight action through this route.

Inert default contract: an all-None SidecarDeps mounts the routes answering an honest 503 and opens
no DB pool; api/asgi.py wires the real PgCrmClient. The web Sidecar surface degrades on 503.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from api.auth import TenantClaims
from api.sidecar import build_suggestions

_UNCONFIGURED_DETAIL = (
    "sidecar data plane not configured — no crm_app DSN on this task "
    "(DB_*/UPLIFT_DB_URL unset); Sidecar suggestions are unavailable"
)


@dataclass
class SidecarDeps:
    """A PgCrmClient-shaped reader (list_deals_board / list_contacts_directory). None = data plane
    unconfigured -> the routes answer the honest 503, never invented suggestions."""
    crm: Any | None = None


class ActBody(BaseModel):
    """Accept body: the suggestion id ONLY. The server resolves the action server-side (the client
    never dictates a Greenlight action). No tenant_id field (THE TRUST RULE)."""
    id: str


def _require_reader(deps: SidecarDeps) -> Any:
    if deps.crm is None:
        raise HTTPException(status_code=503, detail=_UNCONFIGURED_DETAIL)
    return deps.crm


def _checked(rows: list[dict], tenant_id: str) -> list[dict]:
    """Defense in depth (the /deals pattern): a row whose tenant_id isn't the verified request tenant
    must never leave the API (RLS already scopes the read; this makes a silent leak fail loud). The
    internal tenant_id is stripped from what the builder sees."""
    out = []
    for r in rows:
        if str(r.get("tenant_id")) != str(tenant_id):
            raise HTTPException(status_code=500, detail="tenant isolation violation")
        out.append({k: v for k, v in r.items() if k != "tenant_id"})
    return out


def _read_rows(crm: Any, tenant_id: str) -> tuple[list[dict], list[dict]]:
    deals = _checked(crm.list_deals_board(tenant_id=tenant_id), tenant_id)
    contacts = _checked(crm.list_contacts_directory(tenant_id=tenant_id), tenant_id)
    return deals, contacts


def mount_sidecar(app: FastAPI, deps: SidecarDeps, current_tenant, *, gate_deps: Any) -> None:
    """Mount the /sidecar routes, authed via `current_tenant`. `gate_deps` is the ApiDeps bag
    (duck-typed to avoid an api.app import cycle) — accept reuses ITS Greenlight so a Sidecar action
    lands in the SAME approval queue, autonomy policy, and kill switch as every other gated action."""

    @app.get("/sidecar/suggestions")
    def sidecar_suggestions(claims: TenantClaims = Depends(current_tenant)):
        crm = _require_reader(deps)
        deals, contacts = _read_rows(crm, claims.tenant_id)
        return build_suggestions(deals, contacts)

    @app.post("/sidecar/act")
    def sidecar_act(body: ActBody, claims: TenantClaims = Depends(current_tenant)):
        crm = _require_reader(deps)
        deals, contacts = _read_rows(crm, claims.tenant_id)
        # Recompute with no display cap so any VALID id resolves (the GET may have trimmed it).
        built = build_suggestions(deals, contacts, limit=10_000)
        match = next((s for s in built["suggestions"] if s["id"] == body.id), None)
        if match is None:
            # The id is unknown OR the underlying row changed (deal closed, contact enriched) so the
            # suggestion no longer applies — an honest 409, never a fabricated approval.
            raise HTTPException(status_code=409, detail="suggestion no longer applies")

        action = dict(match["action"])
        action_name = action.pop("action")
        approval = gate_deps.greenlight.propose(
            tenant_id=claims.tenant_id,
            action=action_name,
            agent="sidecar",
            reasoning=f"Sidecar: {match['title']} — {match['detail']}",
            value_at_stake=match.get("value_at_stake"),
            payload=action,
        )
        # Return just enough for the UI to confirm + link to Greenlight (never the raw internal row).
        return {
            "status": "queued",
            "approval_id": str(approval.get("id")) if approval else None,
            "suggestion_id": match["id"],
            "action": action_name,
        }


__all__ = ["SidecarDeps", "mount_sidecar"]
