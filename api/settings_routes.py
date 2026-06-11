"""Authed per-tenant workspace-settings surface — make the web Settings "Save" actually persist.

Two endpoints, both bound to the VERIFIED JWT claims (THE TRUST RULE — tenant identity comes ONLY
from `custom:tenant_id`, never a header, query, or the request body):

  GET  /account/settings   -> {"workspace_name": str|null, "notification_prefs": {flat bool/str map}}
  PUT  /account/settings    body {workspace_name?: str, notification_prefs?: {flat bool/str map}}
                            -> validates, upserts, returns the saved row (same shape as GET)

CONTRACT (the web Settings page builds against exactly these shapes):
  * GET returns the persisted settings; a tenant that has never saved gets the empty/default shape
    ({"workspace_name": null, "notification_prefs": {}}) — never a 404.
  * PUT persists only the fields present in the body (a partial PUT with just workspace_name leaves
    notification_prefs untouched, and vice versa), and returns the full saved row.

VALIDATION (422 on violation):
  * workspace_name, when provided, is non-empty after trim and <= MAX_WORKSPACE_NAME chars.
  * notification_prefs, when provided, is a FLAT dict whose values are bool or string — nested
    dicts/lists are rejected, oversized maps (too many keys, or string values too long) are rejected.

INERT BY DEFAULT: SettingsDeps() with store=None is the honest 503 — constructing it never opens a
DB pool. The real wiring (api/asgi.py, Lane Nick) passes the PgSettingsStore instance.

IMPORT SAFETY: importing this module touches no AWS/boto3/DB — the store is injected.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from api.auth import TenantClaims

log = logging.getLogger("api.settings")

_UNCONFIGURED = (
    "settings store not configured — no crm_app DSN on this task "
    "(DB_*/UPLIFT_DB_URL unset); workspace settings are unavailable"
)

# Validation bounds (flat, conservative — settings are tiny by design).
MAX_WORKSPACE_NAME = 200
MAX_PREF_KEYS = 50
MAX_PREF_KEY_LEN = 100
MAX_PREF_VALUE_LEN = 500


@dataclass
class SettingsDeps:
    """Injected deps for the /account/settings routes.

    The all-None default is deliberately inert: constructing SettingsDeps() never opens a DB pool.
    `store` is a PgSettingsStore-shaped object (get(tenant_id) / upsert(tenant_id, *, ...)); None
    yields the honest 503.
    """
    store: Any | None = None


class SettingsBody(BaseModel):
    """PUT body. Both fields optional — a partial PUT updates only what it carries. Unknown fields
    are ignored (Pydantic default). Empty/whole-field validation happens in the route so the error
    detail is precise (422 with a reason)."""
    workspace_name: str | None = None
    notification_prefs: dict | None = None


def _validate_workspace_name(name: str) -> str:
    """Non-empty (after trim) and length-capped. Returns the trimmed value to persist."""
    if not isinstance(name, str):
        raise HTTPException(status_code=422, detail="workspace_name must be a string")
    trimmed = name.strip()
    if not trimmed:
        raise HTTPException(status_code=422, detail="workspace_name must not be empty")
    if len(trimmed) > MAX_WORKSPACE_NAME:
        raise HTTPException(
            status_code=422,
            detail=f"workspace_name must be <= {MAX_WORKSPACE_NAME} characters",
        )
    return trimmed


def _validate_prefs(prefs: dict) -> dict:
    """A FLAT map of bool/string values. Reject nested structures and oversized maps/values."""
    if not isinstance(prefs, dict):
        raise HTTPException(status_code=422, detail="notification_prefs must be an object")
    if len(prefs) > MAX_PREF_KEYS:
        raise HTTPException(
            status_code=422,
            detail=f"notification_prefs may have at most {MAX_PREF_KEYS} keys",
        )
    out: dict = {}
    for key, value in prefs.items():
        if not isinstance(key, str) or not key:
            raise HTTPException(
                status_code=422, detail="notification_prefs keys must be non-empty strings")
        if len(key) > MAX_PREF_KEY_LEN:
            raise HTTPException(
                status_code=422,
                detail=f"notification_prefs key too long (max {MAX_PREF_KEY_LEN})",
            )
        # bool is a subclass of int — accept bool explicitly, reject bare int/float, and reject any
        # nested dict/list (the "flat only" rule). Strings are length-capped.
        if isinstance(value, bool):
            out[key] = value
        elif isinstance(value, str):
            if len(value) > MAX_PREF_VALUE_LEN:
                raise HTTPException(
                    status_code=422,
                    detail=f"notification_prefs value too long (max {MAX_PREF_VALUE_LEN})",
                )
            out[key] = value
        else:
            raise HTTPException(
                status_code=422,
                detail="notification_prefs values must be booleans or strings (flat map only)",
            )
    return out


def _empty_settings() -> dict:
    """The default shape for a tenant that has never saved settings."""
    return {"workspace_name": None, "notification_prefs": {}}


def mount_settings(app: FastAPI, deps: SettingsDeps, current_tenant) -> None:
    """Mount GET/PUT /account/settings on `app`, authed via `current_tenant` (the same
    verified-claims dependency every other authed route uses)."""

    @app.get("/account/settings")
    def get_settings(claims: TenantClaims = Depends(current_tenant)):
        """Return the tenant's persisted workspace settings (THE TRUST RULE — tenant from the claim
        only). A tenant that has never saved gets the empty/default shape, never a 404. 503 when the
        store is unconfigured."""
        if deps.store is None:
            raise HTTPException(status_code=503, detail=_UNCONFIGURED)
        row = deps.store.get(claims.tenant_id)
        return row if row is not None else _empty_settings()

    @app.put("/account/settings")
    def put_settings(body: SettingsBody, claims: TenantClaims = Depends(current_tenant)):
        """Validate + persist the provided settings fields, returning the saved row.

        Only the fields present in the body are updated (partial PUT). Tenant identity comes ONLY
        from the verified claim — a tenant_id in the body is ignored (it is not a model field, so
        Pydantic drops it). 503 when the store is unconfigured; 422 on validation failure."""
        if deps.store is None:
            raise HTTPException(status_code=503, detail=_UNCONFIGURED)

        if body.workspace_name is None and body.notification_prefs is None:
            raise HTTPException(
                status_code=422,
                detail="provide at least one of workspace_name / notification_prefs",
            )

        workspace_name = (
            None if body.workspace_name is None else _validate_workspace_name(body.workspace_name)
        )
        notification_prefs = (
            None if body.notification_prefs is None else _validate_prefs(body.notification_prefs)
        )

        saved = deps.store.upsert(
            claims.tenant_id,
            workspace_name=workspace_name,
            notification_prefs=notification_prefs,
        )
        log.info("settings saved tenant=%s by=%s fields=%s",
                 claims.tenant_id, claims.sub,
                 [f for f, v in (("workspace_name", workspace_name),
                                 ("notification_prefs", notification_prefs)) if v is not None])
        return saved
