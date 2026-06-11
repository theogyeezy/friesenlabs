"""Self-service billing via the Stripe Customer Portal (authed, claims-bound).

The Stripe-hosted Customer Portal is free: the tenant changes their card, cancels, or views past
invoices on Stripe's pages, then Stripe returns them to our ``return_url``. We only mint a portal
SESSION for the customer WE resolved server-side from the verified Cognito ``custom:tenant_id``
claim -> account -> ``stripe_customer_id`` mapping (the one ``start_checkout`` persisted). Nothing
the client sends names the customer or the tenant — THE TRUST RULE holds.

Routes (mounted on the control-plane API, behind the same verified-claims dependency as every other
authed route):
  * POST /billing/portal-session -> {"url": ...}. 403 (honest copy) when the tenant has no Stripe
    customer mapping yet (e.g. an internal-comp / not-yet-paid tenant): there is nothing to manage.
  * GET  /billing -> {"customer": bool, "plan": str|None, "status": str}. A small read the settings
    screen uses to show the current plan + whether the "Manage billing" button can do anything.

Why this is a separate, optional dep (not folded into the signup deps): billing portal is an AUTHED
post-provisioning surface (it needs the JWT verifier), whereas signup is pre-tenant + unauthenticated.
Keeping ``BillingDeps`` distinct means an unconfigured deploy simply doesn't mount the routes, and the
one-line include in the app factory stays trivially isolated for the integrator.

The Stripe call is gated behind the injected ``stripe`` adapter's
``create_billing_portal_session`` (signup/stripe_adapter.py): it raises ``StripeNotConfiguredError``
with no api key (clean stub, no network), which this route maps to an honest 503 — never a 500.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request


@dataclass
class BillingDeps:
    # The injected Stripe client (duck type: ``create_billing_portal_session(customer=, return_url=)``
    # -> {"id", "url"}). In prod this is the SAME StripeAdapter instance the payment plane uses; the
    # _StubStripe (no api key) raises StripeNotConfiguredError, surfaced as a 503.
    stripe: Any
    # The account store (duck type: ``get_by_tenant_id(tenant_id) -> Account | None`` and the
    # optional ``get_billing_status(account_id) -> dict | None``). The SAME PgAccountStore the
    # signup/payment plane uses, so the stripe_customer_id mapping is the one start_checkout wrote.
    accounts_store: Any
    # Where Stripe sends the tenant back after the portal (operator-configured; empty = Stripe's
    # default / no explicit return). LANE NICK injects STRIPE_PORTAL_RETURN_URL (see PR body).
    return_url: str = ""


def mount_billing(app: FastAPI, deps: BillingDeps, current_tenant: Callable) -> None:
    """Mount the billing-portal routes. ``current_tenant`` is the app's verified-claims FastAPI
    dependency (api.auth.make_current_tenant) — tenant identity comes ONLY from the verified JWT."""

    def _resolve_account(tenant_id: str):
        """Resolve the verified tenant to its signup account (carries stripe_customer_id)."""
        getter = getattr(deps.accounts_store, "get_by_tenant_id", None)
        if not callable(getter):
            return None
        return getter(str(tenant_id))

    @app.post("/billing/portal-session")
    @app.post("/api/billing/portal-session")
    def portal_session(request: Request):
        # Tenant identity from the VERIFIED claim only (THE TRUST RULE) — current_tenant raises
        # 401 on a missing/invalid token. Called as a plain function (not a FastAPI Depends) so
        # the route stays a single mount-time closure over the injected verifier.
        from api.auth import TenantClaims  # noqa: PLC0415 — typing only; avoids an import cycle
        claims: TenantClaims = current_tenant(request)

        acct = _resolve_account(claims.tenant_id)
        customer_id = getattr(acct, "stripe_customer_id", None) if acct else None
        if not customer_id:
            # No Stripe customer for this tenant yet (internal-comp tenant, or never reached
            # checkout): there is nothing to manage. Honest 403 copy, never a fake portal url.
            raise HTTPException(
                status_code=403,
                detail="No billing account is set up for this workspace yet. "
                       "This usually means there's no active paid subscription to manage.",
            )
        try:
            session = deps.stripe.create_billing_portal_session(
                customer=str(customer_id), return_url=deps.return_url,
            )
        except Exception as e:  # noqa: BLE001 — StripeNotConfiguredError / transient Stripe error
            # Unconfigured (no api key) or a transient Stripe failure -> honest 503, never a 500
            # leaking internals. The class name keeps the cause greppable in logs without exposing
            # it to the client.
            raise HTTPException(
                status_code=503,
                detail="Billing isn't available right now. Please try again shortly.",
            ) from e
        url = session.get("url") if isinstance(session, dict) else None
        if not url:
            raise HTTPException(
                status_code=503,
                detail="Billing isn't available right now. Please try again shortly.",
            )
        return {"url": url}

    @app.get("/billing")
    @app.get("/api/billing")
    def billing_state(request: Request):
        # The settings screen's bootstrap read: does this tenant have a Stripe customer (so the
        # "Manage billing" button can do something), what plan are they on, and what's the billing
        # status (active / past_due / canceled). Everything from the verified claim + the account
        # row; nothing from the client.
        from api.auth import TenantClaims  # noqa: PLC0415
        claims: TenantClaims = current_tenant(request)
        acct = _resolve_account(claims.tenant_id)
        customer_id = getattr(acct, "stripe_customer_id", None) if acct else None
        plan = (getattr(acct, "meta", {}) or {}).get("plan") if acct else None
        status = "active"
        if acct is not None:
            getter = getattr(deps.accounts_store, "get_billing_status", None)
            if callable(getter):
                rec = getter(acct.id)
                if rec and rec.get("status"):
                    status = str(rec["status"])
        return {"customer": bool(customer_id), "plan": plan, "status": status}
