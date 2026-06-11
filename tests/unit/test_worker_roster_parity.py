"""Unit: granted == served, EXACTLY.

Every tool an agent on the roster (or the coordinator) is granted MUST have a server behind it
in the self-hosted worker — a granted-but-unserved tool wedges live sessions at requires_action
forever (the model calls a tool nothing will ever answer; live finding behind scout's
run_model/build_view grants). And the worker must not serve tools nothing grants — a
served-but-ungranted tool is unreachable dead weight that still ships with creds-adjacent code.

This is the contract that keeps agents/roster (the grants) and worker/worker.py TOOLS (the
servers) from drifting apart.
"""
import pytest

from agents.coordinator import COORDINATOR
from agents.roster import ROSTER
from agents.tools.registry import TOOL_REGISTRY
from worker import worker


def _granted() -> set[str]:
    granted: set[str] = set()
    for spec in [*ROSTER, COORDINATOR]:
        granted.update(spec.tools)
    return granted


def _served() -> set[str]:
    return {t.name for t in worker.TOOLS}


@pytest.mark.unit
def test_every_granted_tool_is_served_by_the_worker():
    missing = _granted() - _served()
    assert not missing, (
        f"granted but UNSERVED (sessions will wedge at requires_action): {sorted(missing)} — "
        "register the tool in worker.TOOLS or remove the grant from agents/roster"
    )


@pytest.mark.unit
def test_worker_serves_no_tool_nothing_grants():
    extra = _served() - _granted()
    assert not extra, (
        f"served but granted to NO agent (unreachable dead weight): {sorted(extra)} — "
        "grant it on the roster or drop it from worker.TOOLS"
    )


@pytest.mark.unit
def test_granted_equals_served_exactly():
    assert _granted() == _served()


@pytest.mark.unit
def test_every_served_tool_resolves_in_the_trusted_registry():
    # The worker only ever serves registry tools (the action gate derives side-effecting truth
    # from the tool class — an off-registry server would bypass that contract).
    served = _served()
    assert served <= set(TOOL_REGISTRY), sorted(served - set(TOOL_REGISTRY))


@pytest.mark.unit
def test_worker_tool_names_are_unique():
    names = [t.name for t in worker.TOOLS]
    assert len(names) == len(set(names))
