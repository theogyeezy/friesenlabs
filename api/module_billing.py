"""Module billing sync — the "selection sets the price" plane (Phase 2).

When a tenant changes their enabled modules (PUT /account/modules), their Stripe subscription is
reconciled so it carries exactly the MODULE price items their selection implies: enabling Cortex
adds the Cortex subscription item, disabling it removes that item. The plan-tier line item and
anything else on the subscription are never touched (the adapter's ``managed_price_ids`` boundary).

Inert by construction (the unconfigured-stub posture used everywhere in this codebase):
  * The per-module Stripe Price ids come from env (``configured_module_prices``). With none set —
    the owner hasn't minted per-module Prices yet — :func:`from_env` returns ``None`` and the
    PUT route skips billing entirely. The toggle still persists + re-gates the UI (Phase 1);
    only the *charge* is deferred until the Prices exist.
  * Resolution is tenant -> account -> ``stripe_customer_id`` (the SAME mapping start_checkout
    wrote). A tenant with no customer / no active subscription is a clean no-op, never an error.
  * Tenant identity is the verified-claim tenant passed by the route — never anything client-sent.

Best-effort: the entitlement row is the source of truth and the sync is idempotent + re-runnable,
so a transient Stripe failure surfaces to the caller but never blocks the toggle from saving.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from shared import modules as M


class ModuleBillingSync:
    """Resolves a tenant to its Stripe customer and reconciles its subscription's module items."""

    def __init__(self, *, accounts_store: Any, stripe: Any, env: Mapping[str, str]):
        self._accounts = accounts_store          # duck type: get_by_tenant_id(tenant_id) -> Account|None
        self._stripe = stripe                    # StripeAdapter (sync_subscription_modules)
        self._env = dict(env or {})
        # Snapshot the configured module prices once: {module_id -> price_id}.
        self._configured = M.configured_module_prices(self._env)

    @property
    def configured(self) -> dict[str, str]:
        return dict(self._configured)

    def sync(self, tenant_id: str, enabled_ids) -> dict:
        """Reconcile the tenant's subscription to its enabled modules.

        Returns a small status dict (never raises for the expected no-op cases):
          * ``{"status": "no_customer"}`` — the tenant has no Stripe customer mapping yet.
          * ``{"status": "no_subscription"}`` — customer exists but has no active subscription.
          * ``{"status": "synced", "added": [...], "removed": [...]}`` — items reconciled.
        A live Stripe/transport error propagates so the caller can report it (the toggle is already
        saved; the owner can re-sync)."""
        managed = list(self._configured.values())
        desired = list(M.desired_module_prices(enabled_ids, self._env).values())

        getter: Callable | None = getattr(self._accounts, "get_by_tenant_id", None)
        acct = getter(str(tenant_id)) if callable(getter) else None
        customer = getattr(acct, "stripe_customer_id", None) if acct else None
        if not customer:
            return {"status": "no_customer"}

        result = self._stripe.sync_subscription_modules(
            customer=customer,
            desired_price_ids=desired,
            managed_price_ids=managed,
            idempotency_key=f"modsync:{tenant_id}",
        )
        if result.get("subscription") is None:
            return {"status": "no_subscription"}
        return {"status": "synced", "added": result.get("added", []), "removed": result.get("removed", [])}


def from_env(*, accounts_store: Any, stripe: Any, env: Mapping[str, str]) -> ModuleBillingSync | None:
    """Build the sync ONLY when it can actually bill: a Stripe adapter is present AND at least one
    per-module Price is configured in env. Otherwise return None — the PUT route skips billing and
    Phase-1 entitlements work unchanged (fully inert until the owner mints the Prices)."""
    if stripe is None or accounts_store is None:
        return None
    if not M.configured_module_prices(env or {}):
        return None
    return ModuleBillingSync(accounts_store=accounts_store, stripe=stripe, env=dict(env))
