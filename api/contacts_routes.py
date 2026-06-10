"""Authed per-tenant contacts/companies endpoints — the api half of the real Contacts directory
(the second honest-stub tab converted to REAL, after the Pipeline board; the web half is
web/src/api/ContactsDirectory.tsx).

Four endpoints, all READ-ONLY this cycle (CRM writes arrive with a later update_contact tool
through the ActionGate — never a direct write here) and all bound to the VERIFIED JWT claims
(THE TRUST RULE — tenant never from a header or the request body):

  GET /contacts          paginated directory: contact rows + the joined company name + the
                         newest activity timestamp; ?q= searches name/email (allow-listed
                         columns, ILIKE bind params with metacharacter escaping)
  GET /contacts/{id}     one contact + recent activities + their company's OPEN deals
                         (ties the directory into the Pipeline board)
  GET /companies         paginated directory with contact + open-deal counts; ?q= over
                         name/domain
  GET /companies/{id}    one company + its contacts + its open deals

Reads ride the same crm_app DSN every live surface (/approvals, /views, /deals) already rides —
RLS via the per-op `SET LOCAL app.current_tenant` transaction (api/pg_clients.py), allow-listed
hand-written column lists, no hand-written tenant filter anywhere. Free-text params are
length-capped (q > 200 chars -> 422) so a hostile query can never become an unbounded scan
term. Unconfigured (no DSN -> no reader injected) every endpoint answers an honest 503, never
invented rows.

IMPORT SAFETY: importing this module touches no AWS/boto3/DB and never imports ingest/ (the
production API image does not bundle it — see api/integrations_routes.py HOTFIX note; the
image-fileset regression test imports api.app, which mounts this module).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from api.auth import TenantClaims

_UNCONFIGURED_DETAIL = (
    "contacts data plane not configured — no crm_app DSN on this task "
    "(DB_*/UPLIFT_DB_URL unset); the contacts directory is unavailable"
)

# Free-text search cap (the Pipeline review's hardening note): a q longer than this is a 422,
# never a scan term. Generous for names/emails/domains; hostile for payload smuggling.
MAX_Q_LEN = 200

# Page-size/offset clamps applied at the ROUTE (the reader clamps again — belt and suspenders).
DEFAULT_PAGE = 50
MAX_PAGE = 200
MAX_OFFSET = 100_000


# --------------------------------------------------------------------------- #
# Injected deps — the DealsDeps pattern, with the same DELIBERATELY inert
# default: ApiDeps' default_factory builds the all-None stub, so a bare
# create_app(ApiDeps(...)) — every test, any non-asgi constructor — mounts the
# routes answering the honest 503 and NEVER opens a DB pool as a side effect of
# constructing deps. The ONLY real wiring is api/asgi.py passing the SAME
# PgCrmClient instance the executor/chat tools and /deals already use (one
# pool, the exact dsn_from_env guard the live siblings ride).
# --------------------------------------------------------------------------- #
@dataclass
class ContactsDeps:
    # A PgCrmClient-shaped reader (list_contacts_directory / get_contact_directory /
    # list_contact_activities / list_company_open_deals / list_companies_directory /
    # get_company_directory / list_company_contacts). None = data plane unconfigured ->
    # every endpoint answers the honest 503, never invented rows.
    crm: Any | None = None


def _require_reader(deps: ContactsDeps) -> Any:
    if deps.crm is None:
        raise HTTPException(status_code=503, detail=_UNCONFIGURED_DETAIL)
    return deps.crm


def _valid_id_or_404(value: str, *, kind: str) -> str:
    """Path ids must be uuids (the schema's PK type). A malformed id is indistinguishable
    from a missing row to the caller — 404, tenant-scoped semantics, never a 500."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail=f"no such {kind}")


def _clean_q(q: str | None) -> str | None:
    """Normalize the free-text search param: None/blank -> None (no filter); anything longer
    than MAX_Q_LEN is refused loudly (422), never truncated into a different query."""
    if q is None:
        return None
    if len(q) > MAX_Q_LEN:
        raise HTTPException(status_code=422,
                            detail=f"q must be at most {MAX_Q_LEN} characters")
    q = q.strip()
    return q or None


def _clamp_page(limit: int, offset: int) -> tuple[int, int]:
    """Route-level pagination clamps (junk ints are already 422'd by FastAPI's typing)."""
    return max(1, min(int(limit), MAX_PAGE)), max(0, min(int(offset), MAX_OFFSET))


def _checked_rows(rows: list[dict], tenant_id: str) -> list[dict]:
    """Defense in depth (the /views pattern): never let a row whose tenant_id isn't the
    verified request tenant leave the API — RLS already scopes the read; this makes a
    silent leak fail loud. The internal tenant_id is then stripped from the payload."""
    out = []
    for r in rows:
        if str(r.get("tenant_id")) != str(tenant_id):
            raise HTTPException(status_code=500, detail="tenant isolation violation")
        out.append({k: v for k, v in r.items() if k != "tenant_id"})
    return out


def mount_contacts(app: FastAPI, deps: ContactsDeps, current_tenant) -> None:
    """Mount the /contacts + /companies routes on `app`, authed via `current_tenant` (the same
    verified-claims dependency every other authed route uses). Read-only: no gate deps —
    there is nothing here for Greenlight to gate yet."""

    @app.get("/contacts")
    def list_contacts(q: str | None = None, limit: int = DEFAULT_PAGE, offset: int = 0,
                      claims: TenantClaims = Depends(current_tenant)):
        crm = _require_reader(deps)
        term = _clean_q(q)
        n, off = _clamp_page(limit, offset)
        # Ask for one row beyond the page so has_more is honest without a count query.
        rows = crm.list_contacts_directory(tenant_id=claims.tenant_id, q=term,
                                           limit=n + 1, offset=off)
        contacts = _checked_rows(rows, claims.tenant_id)
        has_more = len(contacts) > n
        return {
            "contacts": contacts[:n],
            "count": min(len(contacts), n),
            "has_more": has_more,
            "limit": n,
            "offset": off,
            "q": term,
        }

    @app.get("/contacts/{contact_id}")
    def get_contact(contact_id: str, claims: TenantClaims = Depends(current_tenant)):
        crm = _require_reader(deps)
        cid = _valid_id_or_404(contact_id, kind="contact")
        row = crm.get_contact_directory(tenant_id=claims.tenant_id, contact_id=cid)
        if row is None:  # missing OR another tenant's — indistinguishable by design
            raise HTTPException(status_code=404, detail="no such contact")
        contact = _checked_rows([row], claims.tenant_id)[0]
        activities = crm.list_contact_activities(tenant_id=claims.tenant_id, contact_id=cid)
        # The contact's company's OPEN deals — the seam into the Pipeline board. No company
        # -> honestly empty, no extra read.
        company_deals: list[dict] = []
        if contact.get("company_id"):
            company_deals = _checked_rows(
                crm.list_company_open_deals(tenant_id=claims.tenant_id,
                                            company_id=contact["company_id"]),
                claims.tenant_id,
            )
        return {"contact": contact, "activities": activities, "company_deals": company_deals}

    @app.get("/companies")
    def list_companies(q: str | None = None, limit: int = DEFAULT_PAGE, offset: int = 0,
                       claims: TenantClaims = Depends(current_tenant)):
        crm = _require_reader(deps)
        term = _clean_q(q)
        n, off = _clamp_page(limit, offset)
        rows = crm.list_companies_directory(tenant_id=claims.tenant_id, q=term,
                                            limit=n + 1, offset=off)
        companies = _checked_rows(rows, claims.tenant_id)
        has_more = len(companies) > n
        return {
            "companies": companies[:n],
            "count": min(len(companies), n),
            "has_more": has_more,
            "limit": n,
            "offset": off,
            "q": term,
        }

    @app.get("/companies/{company_id}")
    def get_company(company_id: str, claims: TenantClaims = Depends(current_tenant)):
        crm = _require_reader(deps)
        coid = _valid_id_or_404(company_id, kind="company")
        row = crm.get_company_directory(tenant_id=claims.tenant_id, company_id=coid)
        if row is None:
            raise HTTPException(status_code=404, detail="no such company")
        company = _checked_rows([row], claims.tenant_id)[0]
        contacts = _checked_rows(
            crm.list_company_contacts(tenant_id=claims.tenant_id, company_id=coid),
            claims.tenant_id,
        )
        deals = _checked_rows(
            crm.list_company_open_deals(tenant_id=claims.tenant_id, company_id=coid),
            claims.tenant_id,
        )
        return {"company": company, "contacts": contacts, "deals": deals}
