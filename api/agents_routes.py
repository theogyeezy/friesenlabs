"""Authed per-tenant agent-crew endpoint — the api half of the real Agents tab
(the third honest-stub tab converted to REAL, after Pipeline + Contacts; the web half is
web/src/api/AgentsRoster.tsx).

One endpoint, READ-ONLY, bound to the VERIFIED JWT claims (THE TRUST RULE — tenant never from
a header or the request body):

  GET /agents    the tenant's crew: the 7 specialists + coordinator exactly as the eager-ensure
                 provisioning creates them. The roster comes from the OWNED definitions
                 (agents/roster + agents/coordinator — names, specialties, duty descriptions,
                 tool lists), and each tool carries its TRUSTED policy from the server-side
                 registry (auto = runs on its own, always_ask = routes through Greenlight) —
                 the autonomy story, made visible. The tenant's provisioned Managed Agents ids
                 ride along from THEIR tenant_workspaces row (RLS-scoped read), TRUNCATED to a
                 short display tail — the full ids never leave the API.

NO live Managed Agents API calls happen here: the tenant_workspaces row is the truth about
provisioning (live agent status arrives with the worker, a later cycle). An unprovisioned
tenant (no row / incomplete row / offline 'stub-' placeholder ids) gets the SAME roster with
`provisioned: false` so the UI can say "your crew assembles at signup" honestly.

Reads ride the same PgWorkspaceStore instance the /chat conversation factory + signup
provisioning already ride — RLS via the per-op `SET LOCAL app.current_tenant` transaction
(agents/workspace_store.py). Unconfigured (no DSN -> no store injected) the endpoint answers
an honest 503, never an invented crew state.

IMPORT SAFETY: importing this module touches no AWS/boto3/DB and never imports ingest/ (the
production API image does not bundle it — see api/integrations_routes.py HOTFIX note; the
image-fileset regression test imports api.app, which mounts this module). The roster +
registry are imported lazily inside the route, mirroring deals_routes' move-stage pattern.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from api.auth import TenantClaims

_UNCONFIGURED_DETAIL = (
    "agents plane not configured — no crm_app DSN on this task "
    "(DB_*/UPLIFT_DB_URL unset); the agent crew is unavailable"
)

# How much of a provisioned Managed Agents id may leave the API: the LAST 6 chars only —
# enough for a human to eyeball "which environment is mine" against the Console, useless for
# addressing the resource. The full id is operator material and never reaches the browser.
ID_TAIL_LEN = 6

# Display specialties for the OWNED roster (agents/roster.py + agents/coordinator.py). Keyed by
# agent name; an unknown future agent falls back to a humanized name so a roster addition can
# never crash this tab (it just ships with a plain label until this map learns it).
SPECIALTIES: dict[str, str] = {
    "scout": "Lead research",
    "nadia": "Outreach drafting",
    "margo": "Quoting",
    "ledger": "CRM ops",
    "echo": "Follow-ups",
    "pip": "Support",
    "critic": "Review & risk",
    "uplift-orchestrator": "Coordinator",
}


# --------------------------------------------------------------------------- #
# Injected deps — the DealsDeps/ContactsDeps pattern, with the same
# DELIBERATELY inert default: ApiDeps' default_factory builds the all-None
# stub, so a bare create_app(ApiDeps(...)) — every test, any non-asgi
# constructor — mounts the route answering the honest 503 and NEVER opens a DB
# pool as a side effect of constructing deps. The ONLY real wiring is
# api/asgi.py passing the SAME PgWorkspaceStore instance the /chat
# conversation factory + signup provisioning already use (one pool, the exact
# dsn_from_env guard the live siblings ride).
# --------------------------------------------------------------------------- #
@dataclass
class AgentsDeps:
    # A WorkspaceStore-shaped reader (get(tenant_id) -> row | None). None = data plane
    # unconfigured -> the endpoint answers the honest 503, never an invented crew state.
    workspace_store: Any | None = None


def _require_store(deps: AgentsDeps) -> Any:
    if deps.workspace_store is None:
        raise HTTPException(status_code=503, detail=_UNCONFIGURED_DETAIL)
    return deps.workspace_store


def _is_stub(value: Any) -> bool:
    """The offline placeholder ids written by the api/prod_deps._Noop agent plane (the same
    'stub-' contract signup/agent_plane.py and the asgi conversation factory honor)."""
    return isinstance(value, str) and value.startswith("stub-")


def _id_tail(value: Any) -> str | None:
    """The display-safe tail of a provisioned Managed Agents id. NEVER the full id."""
    if not isinstance(value, str) or not value:
        return None
    return value[-ID_TAIL_LEN:]


def _specialty(name: str) -> str:
    return SPECIALTIES.get(name, name.replace("-", " ").replace("_", " ").capitalize())


def _tool_entry(tool_name: str) -> dict:
    """One roster tool with its TRUSTED policy, derived from the server-side registry — the
    same source of truth the action gate uses (never a hand-written flag). A roster tool
    missing from the registry (a definitions drift that tests catch) is reported with the
    CONSERVATIVE policy: never claim autonomy the registry doesn't grant."""
    from agents.tools.base import Policy  # noqa: PLC0415 — lazy, mirrors deals_routes
    from agents.tools.registry import get_tool  # noqa: PLC0415

    cls = get_tool(tool_name)
    policy = cls.policy.value if cls is not None else Policy.ALWAYS_ASK.value
    return {"name": tool_name, "policy": policy}


def _agent_entry(spec: Any, *, is_coordinator: bool = False) -> dict:
    """Serialize one OWNED AgentSpec for display: name, specialty, the duty description (the
    spec's own system prompt — what the agent is actually instructed to do, not marketing
    copy), and its tools with trusted policies. No model ids, no internal plumbing."""
    return {
        "name": spec.name,
        "role": _specialty(spec.name),
        "description": spec.system,
        "is_coordinator": is_coordinator,
        "tools": [_tool_entry(t) for t in spec.tools],
    }


def mount_agents(app: FastAPI, deps: AgentsDeps, current_tenant) -> None:
    """Mount the /agents route on `app`, authed via `current_tenant` (the same verified-claims
    dependency every other authed route uses). Read-only: no gate deps — nothing here mutates,
    proposes, or talks to live Managed Agents."""

    @app.get("/agents")
    def get_agent_crew(claims: TenantClaims = Depends(current_tenant)):
        store = _require_store(deps)

        # The roster is OWNED code (portable by design — CLAUDE.md hard constraint #4 keeps
        # all agent-plane shape behind our own definitions), so the crew the tenant sees is
        # exactly the crew provisioning creates. Imported lazily (import-safety note above).
        from agents.coordinator import COORDINATOR  # noqa: PLC0415
        from agents.roster import roster  # noqa: PLC0415

        # RLS-scoped read of THIS tenant's row (tenant from the VERIFIED claim only). The row
        # is the truth about provisioning; no live MA call happens in the request path.
        row = store.get(claims.tenant_id) or {}
        environment_id = row.get("environment_id")
        coordinator_id = row.get("coordinator_id")
        has_ids = bool(environment_id) and bool(coordinator_id)
        # Offline 'stub-' placeholder ids (the _Noop agent plane) are NOT a provisioned crew —
        # the same contract the /chat factory enforces. Honest false, never a fake tail.
        provisioned = has_ids and not any(
            _is_stub(v) for v in (row.get("workspace_id"), environment_id, coordinator_id)
        )

        coordinator = _agent_entry(COORDINATOR, is_coordinator=True)
        coordinator["id_tail"] = _id_tail(coordinator_id) if provisioned else None

        specialists = [_agent_entry(s) for s in roster()]
        return {
            "provisioned": provisioned,
            # TRUNCATED for display (last ID_TAIL_LEN chars) — full ids never leave the API.
            "environment_id_tail": _id_tail(environment_id) if provisioned else None,
            "coordinator": coordinator,
            "roster": specialists,
            "count": len(specialists),
        }
