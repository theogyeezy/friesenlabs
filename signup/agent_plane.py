"""Eager per-tenant agent-plane provisioning glue — the real `agent_plane.ensure()`.

RATIFIED (issue #123): docs/decisions/agent-plane-ensure-eager-vs-lazy.md — Option A, EAGER.
The roster is created at signup (provisioning step 3, `Provisioner._step_agent_plane`), never in
the request path: MA agents/coordinators are free persisted config (tokens + session-hours are the
only billing dimensions), so an eagerly provisioned roster that nobody chats with costs $0.00 —
while lazy would move Anthropic-API failure into the user-facing request path, need distributed
locking around the last-write-wins workspace upsert, and contradict the conversation-factory
contract ("provisioning happens at signup, never in the request path", api/asgi.py).

WHAT ensure() CREATES — mirroring the proven sequence in scripts/verify_agent_plane.py step [1]:
the 7 roster specialists + 1 coordinator IN THE EXISTING Managed Agents environment (the
UPLIFT_ENV_ID the live API task carries). It NEVER calls `create_environment`: the runtime is
constructed already bound to that environment id, and `ManagedAgentsRuntime.create_environment`
refuses to overwrite a bound id anyway (belt and suspenders). Per-tenant environments + the
workspace-scoped key from Secrets Manager are the brief's noted follow-up (the self-hosted
environment key is Console-generated today — an ops item under eager AND lazy, not a tiebreaker);
until then every tenant's roster lives in the shared environment, keyed by the org key.

CONTRACT (signup/provisioning.py step 3):
    ensure(tenant_id=..., workspace_id=...) -> {workspace_id, environment_id, coordinator_id}
The caller persists the returned ids (workspace_store.upsert) — the conversation factory and the
worker read them back; nothing here is ever invoked per request.

IDEMPOTENT: the tenant's workspace-store row is checked FIRST — a complete, non-stub row makes the
second call a no-op that returns the stored ids (the brief's done-when criterion), so a
re-delivered webhook / duplicate SFN execution never builds a second roster. A row holding the
offline 'stub-' placeholder ids (written by the api/prod_deps._Noop fallback) does NOT count as
provisioned — it is re-provisioned for real, and the caller's upsert overwrites the stubs.

RETRY-SAFE: a partial failure (e.g. specialist #4's create raises) RAISES out of ensure() — the
provisioning machinery owns the policy (SFN Retry absorbs transients; a terminal failure parks the
account in `provisioning_failed` with the operator/tenant `retry` available). Because the caller
only upserts AFTER ensure() returns, the retry finds no row and rebuilds the full roster; the
handful of orphaned part-rosters cost nothing (no per-agent/at-rest charge — the brief's pricing
fact) and are name-matched cleanup fodder for a sweeper.

Import-safe: importing this module touches no env, no network, no agents.* heavyweights — the
runtime/roster imports happen lazily inside the first ensure() (the Lambda cold-start posture).
THE TRUST RULE: tenant_id arrives from the caller (the provisioning pipeline, which got it from
the verified payment webhook's account) — never from env, headers, or request payloads here.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _is_stub(value) -> bool:
    return isinstance(value, str) and value.startswith("stub-")


class AgentPlaneEnsure:
    """The real agent plane behind `Provisioner._step_agent_plane` (eager, ratified #123).

    Construction is pure wiring (no network): `api/prod_deps._build_agent_plane` selects this
    class ONLY under the SIGNUP_REAL_DEPS master switch with the AI-plane gate on
    (ANTHROPIC_API_KEY + UPLIFT_ENV_ID present) AND a workspace store to check/persist ids
    against — everywhere else the _Noop stub-id fallback stays, and the conversation factory's
    stub-id guard keeps protecting /chat.

    `runtime_factory` is the test seam: () -> AgentRuntime. The default builds a FRESH
    `ManagedAgentsRuntime` bound to the existing environment per ensure() call (never a shared
    instance serving every tenant's provisioning, and never one that could create_environment).
    """

    def __init__(self, *, api_key: str, environment_id: str, workspace_store,
                 runtime_factory=None):
        if not api_key or not environment_id:
            raise ValueError("AgentPlaneEnsure needs both an api_key and an environment_id")
        if workspace_store is None:
            # Never create live Anthropic resources whose ids cannot be persisted/checked —
            # an ensure() without a store would rebuild (and orphan) a roster on every call.
            raise ValueError("AgentPlaneEnsure needs a workspace_store (idempotency check)")
        self._api_key = api_key
        self._environment_id = environment_id
        self._store = workspace_store
        self._runtime_factory = runtime_factory or self._default_runtime

    def _default_runtime(self):
        from agents.runtime import get_runtime  # noqa: PLC0415 — lazy (import-safety)

        return get_runtime({
            "runtime": "managed",
            "api_key": self._api_key,
            "environment_id": self._environment_id,  # the EXISTING env — never create one
        })

    def ensure(self, *, tenant_id: str, workspace_id: str | None = None) -> dict:
        """Idempotent check-then-create: the tenant's roster ids, building them if absent.

        Returns {workspace_id, environment_id, coordinator_id} — the provisioning step persists
        them via workspace_store.upsert (and re-persisting the same values on the no-op path is
        a harmless same-value overwrite).

        PINNING CONSTRAINT (#161, bit live 2026-06-10): the coordinator's multiagent config pins
        each specialist at the VERSION current at create time — later `agents.update` calls on a
        specialist do NOT propagate to existing coordinators (delegations keep running the old
        version). This method is create-once and never upgrades; rolling out agent-spec changes
        to EXISTING tenants needs a deliberate upgrade pass (update specialists, then repin the
        coordinator's multiagent.agents[].version, as done by hand during the #147 remediation).
        """
        row = self._store.get(tenant_id) or {}
        ids = (row.get("workspace_id"), row.get("environment_id"), row.get("coordinator_id"))
        if row.get("environment_id") and row.get("coordinator_id") \
                and not any(_is_stub(v) for v in ids):
            # Store hit: already provisioned for real — the second call is a no-op (the brief's
            # done-when criterion). Stub rows fall through and get re-provisioned for real.
            log.info("agent_plane.ensure: tenant %s already provisioned (coordinator=%s) — no-op",
                     tenant_id, row["coordinator_id"])
            return {
                "workspace_id": row.get("workspace_id") or workspace_id,
                "environment_id": row["environment_id"],
                "coordinator_id": row["coordinator_id"],
            }

        # EAGER create — the verify_agent_plane step-[1] sequence: 7 specialists + coordinator in
        # the existing environment. Any failure RAISES (partial roster -> park, retryable); the
        # caller upserts only after success, so a retry rebuilds cleanly from the top.
        from agents.coordinator import COORDINATOR  # noqa: PLC0415 — lazy (import-safety)
        from agents.roster import roster  # noqa: PLC0415

        runtime = self._runtime_factory()
        agent_ids = [runtime.create_agent(spec) for spec in roster()]
        coordinator_id = runtime.create_coordinator(COORDINATOR, agent_ids)
        log.info("agent_plane.ensure: tenant %s provisioned %d specialists + coordinator %s "
                 "in environment %s", tenant_id, len(agent_ids), coordinator_id,
                 self._environment_id)
        return {
            "workspace_id": workspace_id,
            "environment_id": self._environment_id,
            "coordinator_id": coordinator_id,
        }
