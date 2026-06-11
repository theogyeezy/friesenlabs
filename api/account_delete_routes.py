"""Authed per-tenant account TEARDOWN endpoint — offboarding / GDPR erasure (right to be forgotten).

The destructive sibling of GET /account/export (api/account_routes.py):

  POST /account/delete   hard-deletes the calling tenant's MUTABLE data and reports what was
                         deleted, what was retained (append-only audit tables), and what failed.

Privileged + destructive, so the safety rails are layered:

  * TENANT FROM CLAIM ONLY (THE TRUST RULE). The tenant is the verified Cognito `custom:tenant_id`
    claim threaded by `current_tenant` — NEVER from the request body. The body's `confirm` token is
    a *typed acknowledgement*, not an identity input: it must EQUAL the claim tenant or the request
    is refused 422. It can only ever scope the teardown to the caller's own tenant.
  * RLS-SCOPED. The deleter (api/pg_account_delete.PgAccountDeleter) runs every DELETE as the
    non-owner `crm_app` role inside ONE `SET LOCAL app.current_tenant` transaction — RLS is the only
    tenant filter (no hand-written `WHERE tenant_id`). Cross-tenant data is unreachable by construction.
  * APPEND-ONLY SAFE. db/roles.sql REVOKEs DELETE on the audit-trail tables; the deleter SKIPS them
    and reports them as retained-with-reason rather than erroring on a forbidden DELETE.
  * IDEMPOTENT + ROLLBACK-SAFE. A SAVEPOINT per table — one table's failure never leaves a
    half-teardown; a re-run finds nothing and reports 0s.

The 503 contract mirrors the export sibling EXACTLY: `AccountDeleteDeps()` with an all-None default
is inert (constructing it opens no pool); when no deleter is configured the route returns 503, never
500. IMPORT SAFETY: importing this module touches no AWS/boto3/DB — the deleter is injected.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException

from api.auth import TenantClaims

log = logging.getLogger("api.account_delete")

_UNCONFIGURED = (
    "account teardown is not available — no data plane configured on this task "
    "(DB_*/UPLIFT_DB_URL unset); nothing to delete"
)
_CONFIRM_MISMATCH = (
    "confirmation token does not match the authenticated tenant — to delete your account "
    "the request body must be {\"confirm\": \"<your tenant id>\"}"
)


@dataclass
class AccountDeleteDeps:
    """Injected deps for the POST /account/delete route.

    The all-None default is deliberately inert (the AccountDeps export pattern): constructing
    AccountDeleteDeps() never opens a DB pool. The ONLY real wiring is mount_account_delete callers
    (api/app.py + api/asgi.py) passing the same PgAccountDeleter the rest of the data plane shares.
    Pass None to get the honest 503 — never a 500.
    """
    # A PgAccountDeleter-shaped object exposing
    # `delete_tenant_data(tenant_id=...) -> {deleted, retained, failed}`. None = data plane
    # unconfigured -> 503 (nothing can be deleted).
    deleter: Any | None = None


def mount_account_delete(app: FastAPI, deps: AccountDeleteDeps, current_tenant) -> None:
    """Mount POST /account/delete on `app`, authed via `current_tenant` (the same verified-claims
    dependency every other authed route uses).

    Destructive, but draft-/audit-safe: it deletes only the tenant's own mutable data and leaves the
    append-only audit trail intact (the deleter enforces that, not this route).
    """

    @app.post("/account/delete")
    def account_delete(
        claims: TenantClaims = Depends(current_tenant),
        payload: dict = Body(default=None),
    ):
        """Tear down the calling tenant's mutable data and return a structured report.

        Body MUST be `{"confirm": "<tenant_id>"}` where `<tenant_id>` equals the verified claim
        tenant (an accidental-deletion guard) — else 422. Tenant identity itself comes ONLY from the
        verified claim (THE TRUST RULE); the body is never an identity source.

        503 when no deleter is configured (nothing to delete). Returns:
            {"tenant_id", "deleted": {table: count}, "retained": {table: reason},
             "failed": {table: error}}
        """
        if deps.deleter is None:
            raise HTTPException(status_code=503, detail=_UNCONFIGURED)

        tid = claims.tenant_id

        # Confirmation guard. The body must carry confirm == the verified tenant. Anything else —
        # missing body, missing/blank confirm, or a confirm that names a DIFFERENT tenant — is a
        # 422. Because the comparison is against the CLAIM (never the body), a caller cannot widen
        # the teardown to another tenant: at best they confirm their own.
        confirm = None
        if isinstance(payload, dict):
            confirm = payload.get("confirm")
        if not isinstance(confirm, str) or confirm != str(tid):
            raise HTTPException(status_code=422, detail=_CONFIRM_MISMATCH)

        try:
            report = deps.deleter.delete_tenant_data(tenant_id=tid)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — surface a clean 500, never leak DSN/value detail
            log.error("account_delete: teardown failed (%s)", type(exc).__name__)
            raise HTTPException(status_code=500, detail="account teardown failed") from exc

        return {
            "tenant_id": tid,
            "deleted": report.get("deleted", {}),
            "retained": report.get("retained", {}),
            "failed": report.get("failed", {}),
        }
