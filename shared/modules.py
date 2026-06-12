"""Module catalog — the single source of truth for the suite's à-la-carte modules.

A tenant's instance shows ONLY the modules it has enabled (per-tenant entitlements, managed in
Settings). Each module gates a set of app route-ids and carries a monthly price (the "selection
sets the price" billing model — Phase 2 wires each enabled module to a Stripe subscription item;
the per-module Stripe Price ids are owner-created and injected by env, never built here).

The web app does NOT mirror this catalog: it consumes it at runtime via GET /account/modules
(catalog_payload below — api/modules_routes.py), so the SPA's nav/route gate can never drift
from this file. Prices are the RATIFIED launch prices (also shown on the marketing
suite-builder, which hardcodes its display copy); change prices HERE.

Route gating contract:
  * A route-id listed in a module's `routes` is shown ONLY when that module is enabled.
  * ALWAYS_ON_ROUTES (account + governance) are never gated — every tenant can reach them.
  * `required=True` modules cannot be disabled (Command Center is the spine).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Module:
    id: str
    name: str
    monthly_cents: int
    required: bool
    routes: tuple[str, ...]  # the app route-ids this module gates (empty = no dedicated route yet)
    # Env-var NAME holding the module's Stripe Price id (Phase 2 billing). Resolved at runtime
    # from the task env; absent/empty = the module isn't billable yet (owner hasn't minted a Price).
    price_env: str = ""


# The catalog. `id`s match the marketing suite-builder + the app route mapping below.
MODULES: tuple[Module, ...] = (
    Module("command", "Command Center", 4900, True, ("dashboard", "reports", "dashboards"),
           "STRIPE_PRICE_ID_MODULE_COMMAND"),
    Module("uplift", "Uplift CRM", 4900, False, ("crm", "contacts"),
           "STRIPE_PRICE_ID_MODULE_UPLIFT"),
    Module("agents", "Agents", 3900, False, ("agents", "studio", "marketplace"),
           "STRIPE_PRICE_ID_MODULE_AGENTS"),
    Module("workflows", "Workflows", 3900, False, ("workflows",),
           "STRIPE_PRICE_ID_MODULE_WORKFLOWS"),
    Module("greenlight", "Greenlight", 2500, False, ("approvals",),
           "STRIPE_PRICE_ID_MODULE_GREENLIGHT"),
    Module("frontline", "Frontline", 3900, False, ("frontline",),
           "STRIPE_PRICE_ID_MODULE_FRONTLINE"),
    Module("knowledge", "Knowledge", 2500, False, ("knowledge",),
           "STRIPE_PRICE_ID_MODULE_KNOWLEDGE"),
    Module("cortex", "Cortex", 4500, False, ("cortex",),
           "STRIPE_PRICE_ID_MODULE_CORTEX"),
    Module("integration", "Switchboard", 2900, False, ("integrations",),
           "STRIPE_PRICE_ID_MODULE_INTEGRATION"),
    Module("sidecar", "Sidecar", 3500, False, ("sidecar",),  # the agentic layer over your CRM
           "STRIPE_PRICE_ID_MODULE_SIDECAR"),
    Module("sell", "Sell", 2500, False, ("sell",),  # gamified selling: levels, streaks, quests, board
           "STRIPE_PRICE_ID_MODULE_SELL"),
)

# Account + governance surfaces every tenant can always reach (never gated by a module).
ALWAYS_ON_ROUTES: frozenset[str] = frozenset({"settings", "security"})

_BY_ID = {m.id: m for m in MODULES}
MODULE_IDS: frozenset[str] = frozenset(_BY_ID)
REQUIRED_IDS: frozenset[str] = frozenset(m.id for m in MODULES if m.required)


def get_module(module_id: str) -> Module | None:
    return _BY_ID.get(module_id)


def valid_module_ids(ids) -> set[str]:
    """The subset of `ids` that are real module ids (drops unknowns — never trust raw input)."""
    return {i for i in ids if i in MODULE_IDS}


def default_enabled() -> set[str]:
    """Modules a tenant sees when they haven't tailored their suite yet (no entitlements row).

    The default is the FULL suite — the post-signup model is opt-OUT: every tenant starts with
    everything visible and turns OFF what they don't want in Settings. This is also the fail-safe
    for the pre-migrate / store-error fallback, so no existing tenant ever loses a surface on deploy.
    (Phase 2 billing seeds the row from the purchased plan at provision time; trimming the suite then
    drives the à-la-carte total — "selection sets the price".)"""
    return set(MODULE_IDS)


def normalize_enabled(ids) -> set[str]:
    """Coerce an entitlement set: keep only real ids, and always force the required modules on."""
    return valid_module_ids(ids) | set(REQUIRED_IDS)


def enabled_routes(enabled_ids) -> set[str]:
    """The app route-ids visible for an entitlement set = every enabled module's routes + the
    always-on routes. The app shows a nav item / mounts a route only if its id is in here."""
    ids = normalize_enabled(enabled_ids)
    routes: set[str] = set(ALWAYS_ON_ROUTES)
    for mid in ids:
        m = _BY_ID.get(mid)
        if m:
            routes.update(m.routes)
    return routes


def monthly_total_cents(enabled_ids) -> int:
    """Sum of the monthly prices of the enabled modules (the à-la-carte total — Phase 2 billing)."""
    return sum(_BY_ID[m].monthly_cents for m in normalize_enabled(enabled_ids))


def price_env_names() -> set[str]:
    """The env-var NAMEs that hold per-module Stripe Price ids (for config wiring/diagnostics)."""
    return {m.price_env for m in MODULES if m.price_env}


def configured_module_prices(env) -> dict[str, str]:
    """{module_id -> Stripe Price id} for every module whose ``price_env`` resolves to a NON-empty
    value in ``env`` (an os.environ-like mapping). A module with no configured Price isn't billable
    yet — it toggles visibility but never touches the invoice. This is the ONLY place env → Price
    resolution happens; Phase-2 billing sync reads it, and an empty result means "no module billing
    configured" (the whole feature stays inert — exactly the unconfigured-stub posture)."""
    out: dict[str, str] = {}
    for m in MODULES:
        if not m.price_env:
            continue
        pid = (env.get(m.price_env) or "").strip()
        if pid:
            out[m.id] = pid
    return out


def desired_module_prices(enabled_ids, env) -> dict[str, str]:
    """The subset of :func:`configured_module_prices` whose module is in the ENABLED set (forced-
    required included). These are the Price ids the tenant's subscription SHOULD carry."""
    enabled = normalize_enabled(enabled_ids)
    return {mid: pid for mid, pid in configured_module_prices(env).items() if mid in enabled}


def catalog_payload(enabled_ids) -> dict:
    """The GET /account/modules shape: the full catalog + this tenant's enabled set + the total +
    the enabled route-ids (so the web gate filters its nav/routes without mirroring the mapping)."""
    enabled = normalize_enabled(enabled_ids)
    return {
        "modules": [
            {
                "id": m.id,
                "name": m.name,
                "monthly_cents": m.monthly_cents,
                "required": m.required,
                "enabled": m.id in enabled,
            }
            for m in MODULES
        ],
        "monthly_total_cents": monthly_total_cents(enabled),
        "enabled_routes": sorted(enabled_routes(enabled)),
    }
