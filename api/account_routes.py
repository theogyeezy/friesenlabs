"""Authed per-tenant account data-export endpoint — GDPR/portability egress.

Single endpoint, READ-ONLY and bound to the VERIFIED JWT claims (THE TRUST RULE — tenant
never from a header or the request body):

  GET /account/export   returns the calling tenant's full data bundle:
                        - contacts (directory rows)
                        - companies (directory rows)
                        - deals (pipeline board rows)
                        - saved_views (all view + dashboard specs)
                        - knowledge_docs (document metadata — no raw content, just inventory)

All sections are RLS-scoped via the SET LOCAL app.current_tenant transaction pattern (the same
pattern every live surface rides). Each section degrades independently: if the crm store is
unconfigured, contacts/companies/deals are omitted with a note; if the rag store is unconfigured,
knowledge_docs is omitted with a note; if saved_views is fully in-memory (always available), it
always returns. A 503 is returned when ALL stores are unconfigured and the export would be empty.

Read-only egress: NO deletes, NO mutations. Account DELETION is a separate, more destructive task
— it is explicitly NOT implemented here.

IMPORT SAFETY: importing this module touches no AWS/boto3/DB. The stores are injected so this
module never opens a pool itself.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from api.auth import TenantClaims

log = logging.getLogger("api.account_export")

_UNCONFIGURED_CRM = (
    "crm data plane not configured — no crm_app DSN on this task "
    "(DB_*/UPLIFT_DB_URL unset); contacts/companies/deals are unavailable"
)
_UNCONFIGURED_RAG = (
    "knowledge data plane not configured — no crm_app DSN on this task "
    "(DB_*/UPLIFT_DB_URL unset); knowledge docs are unavailable"
)
_UNCONFIGURED_ALL = (
    "no data stores configured — export unavailable "
    "(DB_*/UPLIFT_DB_URL unset on this task)"
)


@dataclass
class AccountDeps:
    """Injected deps for the /account/export route.

    The all-None default is deliberately inert: constructing AccountDeps() never opens a DB
    pool. The ONLY real wiring is mount_account callers (api/app.py + api/asgi.py) passing
    the same PgCrmClient, PgRagClient, and SavedViews instances every other route uses.
    Pass None for any store to get the honest 503/omission for that section.
    """
    # A PgCrmClient-shaped reader (list_contacts_directory / list_companies_directory /
    # list_deals_board). None = data plane unconfigured -> contacts/companies/deals omitted.
    crm: Any | None = None
    # A PgRagClient-shaped reader (list_document_inventory). None = knowledge plane unconfigured
    # -> knowledge_docs section omitted.
    rag: Any | None = None
    # A SavedViews facade (list_views / list_dashboards). None = views omitted.
    saved_views: Any | None = None


def _checked_rows(rows: list[dict], tenant_id: str, section: str) -> list[dict]:
    """Defense in depth (the /views + /deals + /contacts pattern): re-check every row's
    tenant_id matches the verified request tenant. RLS already scopes the read; this makes
    a silent leak fail loud rather than propagate. Strip tenant_id from the outbound payload."""
    out = []
    for r in rows:
        if str(r.get("tenant_id")) != str(tenant_id):
            log.error(
                "export: tenant isolation violation in section=%s row_tenant=%s request_tenant=%s",
                section, r.get("tenant_id"), tenant_id,
            )
            raise HTTPException(status_code=500, detail="tenant isolation violation")
        out.append({k: v for k, v in r.items() if k != "tenant_id"})
    return out


def _collect_contacts(crm: Any, tenant_id: str) -> list[dict]:
    """Collect all contacts for the tenant (paginated internally, no API-layer cursor needed
    for an export — we use a generous limit and make repeated calls to drain the set)."""
    all_rows: list[dict] = []
    offset = 0
    page = 500
    while True:
        rows = crm.list_contacts_directory(tenant_id=tenant_id, q=None,
                                           limit=page + 1, offset=offset)
        has_more = len(rows) > page
        all_rows.extend(rows[:page])
        if not has_more:
            break
        offset += page
    return _checked_rows(all_rows, tenant_id, "contacts")


def _collect_companies(crm: Any, tenant_id: str) -> list[dict]:
    all_rows: list[dict] = []
    offset = 0
    page = 500
    while True:
        rows = crm.list_companies_directory(tenant_id=tenant_id, q=None,
                                            limit=page + 1, offset=offset)
        has_more = len(rows) > page
        all_rows.extend(rows[:page])
        if not has_more:
            break
        offset += page
    return _checked_rows(all_rows, tenant_id, "companies")


def _collect_deals(crm: Any, tenant_id: str) -> list[dict]:
    rows = crm.list_deals_board(tenant_id=tenant_id)
    return _checked_rows(rows, tenant_id, "deals")


def _collect_views(saved_views: Any, tenant_id: str) -> list[dict]:
    """All saved view + dashboard specs for the tenant. Uses SavedViews.list_views +
    list_dashboards (both already tenant-scoped; defense-in-depth check follows)."""
    views = saved_views.list_views(tenant_id)
    dashboards = saved_views.list_dashboards(tenant_id)
    all_items = list(views) + list(dashboards)
    out = []
    for item in all_items:
        if str(item.get("tenant_id")) != str(tenant_id):
            log.error(
                "export: tenant isolation violation in section=views row_tenant=%s request_tenant=%s",
                item.get("tenant_id"), tenant_id,
            )
            raise HTTPException(status_code=500, detail="tenant isolation violation")
        out.append({k: v for k, v in item.items() if k != "tenant_id"})
    return out


def _collect_knowledge_docs(rag: Any, tenant_id: str) -> list[dict]:
    """Metadata-only inventory from the knowledge store (source + doc count + last updated).
    No raw content is included in the export — privacy-consistent with the /knowledge endpoint."""
    rows = rag.list_document_inventory(tenant_id=tenant_id)
    # list_document_inventory returns aggregate rows per source, not individual RLS rows —
    # no per-row tenant_id to verify here (same as the existing /knowledge endpoint).
    return [
        {
            "source": r.get("source"),
            "document_count": int(r.get("document_count") or 0),
            "last_updated": (
                r["last_updated"].isoformat()
                if hasattr(r.get("last_updated"), "isoformat")
                else str(r["last_updated"]) if r.get("last_updated") is not None else None
            ),
        }
        for r in rows
    ]


def mount_account(app: FastAPI, deps: AccountDeps, current_tenant) -> None:
    """Mount GET /account/export on `app`, authed via `current_tenant` (the same
    verified-claims dependency every other authed route uses).

    Read-only: no gate deps — nothing here mutates. Account deletion is deliberately absent.
    """

    @app.get("/account/export")
    def account_export(claims: TenantClaims = Depends(current_tenant)):
        """Return the tenant's full data bundle for GDPR/portability export.

        Tenant identity comes ONLY from the verified JWT claim (THE TRUST RULE). Every section
        is RLS-scoped; the defense-in-depth cross-tenant check runs on every outbound row.

        503 when ALL stores are unconfigured (nothing to export). Partial configuration is
        tolerated: configured sections are included, unconfigured sections are omitted with a
        reason note under `sections_unavailable`.
        """
        all_unconfigured = deps.crm is None and deps.rag is None and deps.saved_views is None
        if all_unconfigured:
            raise HTTPException(status_code=503, detail=_UNCONFIGURED_ALL)

        tid = claims.tenant_id
        bundle: dict = {
            "tenant_id": tid,
            "sections_unavailable": [],
        }

        # --- contacts ---
        if deps.crm is not None:
            try:
                bundle["contacts"] = _collect_contacts(deps.crm, tid)
            except HTTPException:
                raise
            except Exception as exc:  # noqa: BLE001 — degrade section, don't kill the export
                log.error("export: contacts collection failed (%s)", type(exc).__name__)
                bundle.setdefault("sections_unavailable", []).append(
                    {"section": "contacts", "reason": type(exc).__name__}
                )
                bundle["contacts"] = []
        else:
            bundle["sections_unavailable"].append(
                {"section": "contacts", "reason": _UNCONFIGURED_CRM}
            )
            bundle["contacts"] = []

        # --- companies ---
        if deps.crm is not None:
            try:
                bundle["companies"] = _collect_companies(deps.crm, tid)
            except HTTPException:
                raise
            except Exception as exc:  # noqa: BLE001
                log.error("export: companies collection failed (%s)", type(exc).__name__)
                bundle.setdefault("sections_unavailable", []).append(
                    {"section": "companies", "reason": type(exc).__name__}
                )
                bundle["companies"] = []
        else:
            bundle["companies"] = []  # already noted under contacts above

        # --- deals ---
        if deps.crm is not None:
            try:
                bundle["deals"] = _collect_deals(deps.crm, tid)
            except HTTPException:
                raise
            except Exception as exc:  # noqa: BLE001
                log.error("export: deals collection failed (%s)", type(exc).__name__)
                bundle.setdefault("sections_unavailable", []).append(
                    {"section": "deals", "reason": type(exc).__name__}
                )
                bundle["deals"] = []
        else:
            bundle["deals"] = []  # already noted under contacts above

        # --- saved views ---
        if deps.saved_views is not None:
            try:
                bundle["saved_views"] = _collect_views(deps.saved_views, tid)
            except HTTPException:
                raise
            except Exception as exc:  # noqa: BLE001
                log.error("export: views collection failed (%s)", type(exc).__name__)
                bundle.setdefault("sections_unavailable", []).append(
                    {"section": "saved_views", "reason": type(exc).__name__}
                )
                bundle["saved_views"] = []
        else:
            bundle["saved_views"] = []

        # --- knowledge docs ---
        if deps.rag is not None:
            try:
                bundle["knowledge_docs"] = _collect_knowledge_docs(deps.rag, tid)
            except HTTPException:
                raise
            except Exception as exc:  # noqa: BLE001
                log.error("export: knowledge_docs collection failed (%s)", type(exc).__name__)
                bundle.setdefault("sections_unavailable", []).append(
                    {"section": "knowledge_docs", "reason": type(exc).__name__}
                )
                bundle["knowledge_docs"] = []
        else:
            bundle["sections_unavailable"].append(
                {"section": "knowledge_docs", "reason": _UNCONFIGURED_RAG}
            )
            bundle["knowledge_docs"] = []

        return bundle
