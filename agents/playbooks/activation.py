"""Playbook activation — register a validated playbook with the EXISTING roster mechanism.

Activation is pure orchestration over the swappable ``AgentRuntime`` adapter (agents/runtime.py)
— the exact mechanism signup provisioning uses (agents/coordinator.build): resolve each roster
entry to its OWNED ``AgentSpec``, narrow tools to the playbook's (already-validated) subset,
``create_agent`` each one, then ``create_coordinator`` over them.

WHAT ACTIVATION NEVER DOES:
  * execute anything — no session is opened, no message sent, no tool invoked;
  * widen a grant — agent/tool subsets were validated upstream (agents/playbooks.validate)
    and are re-validated here as defense in depth;
  * bypass the gates — the registered agents carry only trusted-registry tools, whose
    side-effecting members are ``Policy.ALWAYS_ASK`` at the Tool base class: when the worker
    eventually serves a call, the side effect lands as a Greenlight DRAFT, never a real send.
    Autonomy stays governed by the EXISTING per-tenant dial (api/control) at execution time.

Works against ``FakeRuntime`` offline; against ``ManagedAgentsRuntime`` it is a live-resource
sequence and therefore only runs where the agent plane is deliberately configured.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from agents.playbooks import validate
from agents.roster import OPUS, AgentSpec, roster
from agents.runtime import MAX_AGENTS_PER_ROSTER


def _playbook_specs(definition: dict) -> list[AgentSpec]:
    """Resolve the playbook roster to OWNED AgentSpecs, tools narrowed to the playbook subset.
    An omitted ``tools`` list means the agent's full owned grant (never more)."""
    owned = {spec.name: spec for spec in roster()}
    specs: list[AgentSpec] = []
    for entry in definition["roster"]:
        base = owned[entry["agent"]]  # validate() upstream guarantees membership
        tools = entry.get("tools")
        specs.append(base if tools is None else replace(base, tools=list(tools)))
    return specs


def _coordinator_spec(definition: dict) -> AgentSpec:
    """The playbook's own coordinator (flat topology, depth 1 — the MA hard limit)."""
    members = ", ".join(e["agent"] for e in definition["roster"])
    return AgentSpec(
        name=f"playbook-{definition['name']}"[:64].strip(),
        model=OPUS,
        system=(
            f"You coordinate the '{definition['name']}' playbook. Delegate to your specialists "
            f"({members}) per their duties. Side-effecting proposals always route to Greenlight "
            "for human approval — you never send or mutate anything directly."
        ),
        tools=[],  # custom tools only ride on specialists; the built-in toolset is NOT granted
    )


def activate_playbook(runtime: Any, tenant_id: str, definition: dict) -> dict:
    """Register the playbook's agents + coordinator on ``runtime`` for ``tenant_id``.

    ``tenant_id`` must come from the VERIFIED claim upstream (THE TRUST RULE); it is threaded
    here only for audit metadata in the result. Re-validates the definition (defense in depth —
    a stored row edited out-of-band must not register unvalidated). Returns the registration
    digest: ``{"agent_ids": [...], "coordinator_id": ..., "agents": [names]}``. The FULL ids
    are operator material — API callers must truncate before serializing (api/routes_studio.py).
    """
    validate(definition)
    specs = _playbook_specs(definition)
    if len(specs) > MAX_AGENTS_PER_ROSTER:
        raise ValueError(f"playbook roster exceeds the {MAX_AGENTS_PER_ROSTER}-agent limit")
    agent_ids = [runtime.create_agent(s) for s in specs]
    coordinator_id = runtime.create_coordinator(_coordinator_spec(definition), agent_ids)
    return {
        "tenant_id": str(tenant_id),
        "agents": [s.name for s in specs],
        "agent_ids": agent_ids,
        "coordinator_id": coordinator_id,
    }
