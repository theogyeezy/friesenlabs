"""Module entitlements — GET/PUT /account/modules (the "your suite" surface).

A tenant's instance shows ONLY the modules it has enabled. This route reads + writes the per-tenant
enabled-module set (tenant_settings.enabled_modules, via PgSettingsStore.get_modules/set_modules)
over the canonical catalog in shared.modules. The web app reads GET /account/modules to gate its
nav + routes; the Settings "your suite" manager PUTs toggles here.

THE TRUST RULE: tenant from the verified Cognito claim only — never a body/header. Required modules
(Command Center) cannot be disabled (the catalog forces them on regardless of the request).

Inert default contract (the deals/settings pattern): the all-None deps mount the routes answering an
honest 503 and never open a DB pool; api/asgi.py wires the real PgSettingsStore. The web gate
degrades to "show everything" on a 503/404 so a missing entitlement store never hides the app.

BILLING (Phase 2, "selection sets the price"): each enabled module maps to a Stripe Price (the
catalog's price_env) → a subscription item. On PUT this route persists the entitlement set, then —
when a ModuleBillingSync is wired (api/module_billing.from_env) — reconciles the tenant's
subscription items to match the selection. The sync is best-effort + idempotent: the saved set is
the source of truth, so a transient Stripe error surfaces in the response (``billing.error``)
without ever blocking the toggle. With no per-module Prices configured the sync dep is None and
billing is skipped entirely (Phase-1 behavior, fully inert).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from api.auth import TenantClaims
from shared.modules import MODULE_IDS, catalog_payload, default_enabled, normalize_enabled

_UNCONFIGURED_DETAIL = (
    "module store not configured — no crm_app DSN on this task "
    "(DB_*/UPLIFT_DB_URL unset); module entitlements are unavailable"
)


@dataclass
class ModulesDeps:
    """Injected deps for /account/modules. all-None default is inert (honest 503, no DB pool);
    api/asgi.py wires the real PgSettingsStore (get_modules / set_modules). ``billing`` is the
    optional Phase-2 sync (ModuleBillingSync) — None means billing isn't configured (no per-module
    Prices) and the PUT just persists the entitlement set."""
    store: Any | None = None
    billing: Any | None = None  # duck type: sync(tenant_id, enabled_ids) -> {"status": ...}


class ModulesBody(BaseModel):
    """PUT body: the full desired enabled-module id list. Unknown ids are dropped and the required
    modules are forced on (catalog-normalized) — the stored set is always valid."""
    enabled: list[str]


def _require_store(deps: ModulesDeps) -> Any:
    if deps.store is None:
        raise HTTPException(status_code=503, detail=_UNCONFIGURED_DETAIL)
    return deps.store


def _enabled_for(store: Any, tenant_id: str) -> set[str]:
    """The tenant's enabled set: the stored ids (normalized), or the provisioning default when the
    tenant has never toggled (no row yet)."""
    stored = store.get_modules(tenant_id)
    if stored is None:
        return default_enabled()
    return normalize_enabled(stored)


def mount_modules(app: FastAPI, deps: ModulesDeps, current_tenant) -> None:
    """Mount GET/PUT /account/modules, authed via the verified-claims dependency."""

    @app.get("/account/modules")
    def get_modules(claims: TenantClaims = Depends(current_tenant)):
        store = _require_store(deps)
        try:
            return catalog_payload(_enabled_for(store, claims.tenant_id))
        except Exception:  # noqa: BLE001 — e.g. the column predates the live migrate
            # Resilient read: if the entitlement store can't answer (pre-migrate column, transient),
            # return the DEFAULT catalog rather than 500 — the web gate then shows the default set,
            # never a broken app. The error is logged server-side.
            import logging  # noqa: PLC0415
            logging.getLogger("api.modules").warning(
                "modules read failed for a tenant; returning default catalog", exc_info=True)
            return catalog_payload(default_enabled())

    @app.put("/account/modules")
    def put_modules(body: ModulesBody, claims: TenantClaims = Depends(current_tenant)):
        store = _require_store(deps)
        # Reject only when the client sends ids that are ALL unknown (likely a client bug); a mix
        # silently drops the unknowns. Always force the required spine on.
        requested = list(body.enabled)
        if requested and not (set(requested) & MODULE_IDS):
            raise HTTPException(status_code=422, detail="no known module ids in `enabled`")
        saved = store.set_modules(claims.tenant_id, sorted(normalize_enabled(requested)))
        payload = catalog_payload(saved)
        # Phase 2 — "selection sets the price": reconcile the tenant's Stripe subscription items to
        # the saved set. Best-effort: the entitlement row is already persisted (source of truth), so
        # a Stripe failure is reported, not fatal. Skipped entirely when billing isn't wired.
        if deps.billing is not None:
            try:
                payload["billing"] = deps.billing.sync(claims.tenant_id, saved)
            except Exception as exc:  # noqa: BLE001 — never let a billing hiccup undo the toggle
                import logging  # noqa: PLC0415
                logging.getLogger("api.modules").warning(
                    "module billing sync failed (entitlements saved; re-syncable)", exc_info=True)
                payload["billing"] = {"status": "error", "error": type(exc).__name__}
        return payload


__all__ = ["ModulesDeps", "mount_modules"]
