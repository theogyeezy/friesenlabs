"""Unit: agents/playbooks/activation.py — registration semantics over the runtime seam.

The api-level wiring is proven in tests/integration/test_api_studio.py; these pin the module's
own guarantees: owned-spec resolution with narrowed (never widened) tools, the flat-topology
coordinator, re-validation as defense in depth, and the execute-nothing invariant.
"""
import pytest

from agents.playbooks import PlaybookValidationError
from agents.playbooks.activation import activate_playbook
from agents.roster import SCOUT
from agents.runtime import FakeRuntime


def defn():
    return {
        "name": "Unit playbook",
        "trigger": {"kind": "schedule", "schedule": "0 13 * * 1"},
        "roster": [
            {"agent": "scout", "tools": ["read_crm"]},
            {"agent": "pip"},  # omitted tools -> the agent's full owned grant
        ],
        "autonomy": "L2",
        "greenlight": {"side_effects": "always_ask"},
    }


@pytest.mark.unit
def test_activation_registers_narrowed_owned_specs():
    rt = FakeRuntime()
    result = activate_playbook(rt, "tenant-a", defn())

    assert result["tenant_id"] == "tenant-a"
    assert result["agents"] == ["scout", "pip"]
    assert len(result["agent_ids"]) == 2

    by_name = {s.name: s for s in rt.agents.values()}
    assert by_name["scout"].tools == ["read_crm"], "tools must narrow to the playbook subset"
    assert by_name["scout"].model == SCOUT.model, "everything else stays the OWNED spec"
    assert by_name["pip"].tools == ["search_rag", "read_crm"], "omitted tools = full owned grant"

    # One coordinator over exactly the playbook's agents (flat topology).
    assert rt.coordinators == {result["coordinator_id"]: result["agent_ids"]}
    coordinator = rt.agents[result["coordinator_id"]]
    assert coordinator.name.startswith("playbook-")
    assert coordinator.tools == []  # the built-in toolset is never granted (#147)


@pytest.mark.unit
def test_activation_executes_nothing():
    rt = FakeRuntime()
    activate_playbook(rt, "tenant-a", defn())
    assert rt.sessions == {}, "activation must never open a session"
    assert rt.sent == [], "activation must never send a message"
    assert rt.vaults == [] and rt.environments == [], "activation creates agents only"


@pytest.mark.unit
def test_activation_revalidates_defense_in_depth():
    """A stored definition that no longer validates (e.g. edited out-of-band, or a schema
    tightening since it persisted) must fail loud before anything registers."""
    rt = FakeRuntime()
    bad = defn()
    bad["roster"][0]["tools"] = ["send_email"]  # escalation: not in scout's owned grant
    with pytest.raises(PlaybookValidationError):
        activate_playbook(rt, "tenant-a", bad)
    assert rt.agents == {}, "nothing may register from an invalid definition"
