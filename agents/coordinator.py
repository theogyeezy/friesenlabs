"""The coordinator (Build Guide Phase 4, Step 24). Opus; flat topology; runs the critic before
responding. The roster is snapshotted at create/update — bumping a specialist does not auto-update
the coordinator, so re-register it after any roster change.
"""
from __future__ import annotations

from .roster import OPUS, AgentSpec, roster
from .runtime import MAX_AGENTS_PER_ROSTER

COORDINATOR = AgentSpec(
    name="uplift-orchestrator",
    model=OPUS,
    system=(
        "You coordinate the Uplift team. Delegate research to scout, outreach drafting to nadia, "
        "quoting to margo, follow-ups to echo, support to pip, ops to ledger, and always run the "
        "critic before responding."
    ),
    tools=[],  # custom tools only — the built-in agent_toolset is NOT granted (#147); specialists carry the custom tools
)


def build(runtime, environment_name: str = "uplift-vpc"):
    """Create the environment, every specialist, and the coordinator on the given runtime.

    Pure orchestration over the AgentRuntime adapter — works against FakeRuntime offline; against
    ManagedAgentsRuntime it is the per-tenant provisioning sequence (BLOCKED on live Anthropic).
    """
    specs = roster()
    assert len(specs) <= MAX_AGENTS_PER_ROSTER, "roster exceeds the 20-agent limit"
    runtime.create_environment(environment_name)
    agent_ids = [runtime.create_agent(s) for s in specs]
    coordinator_id = runtime.create_coordinator(COORDINATOR, agent_ids)
    return coordinator_id
