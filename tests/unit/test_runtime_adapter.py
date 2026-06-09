"""Unit: the runtime adapter is swappable and the real MA impl never touches the network on build."""
import pytest

from agents import runtime as rt
from agents.runtime import AgentRuntime, FakeRuntime, ManagedAgentsRuntime, get_runtime


@pytest.mark.unit
def test_factory_defaults_to_fake():
    r = get_runtime()
    assert isinstance(r, FakeRuntime)
    assert isinstance(r, AgentRuntime)


@pytest.mark.unit
def test_factory_managed_builds_without_network():
    # Constructing the real runtime must NOT touch Anthropic or require creds.
    r = get_runtime({"runtime": "managed", "api_key": "unused"})
    assert isinstance(r, ManagedAgentsRuntime)
    assert r._client is None  # client is lazy


@pytest.mark.unit
def test_managed_methods_are_blocked_until_verified():
    r = ManagedAgentsRuntime(api_key="unused")
    # Every live endpoint refuses to run (no accidental live Anthropic calls).
    with pytest.raises(NotImplementedError):
        r.create_environment("uplift-vpc")
    with pytest.raises(NotImplementedError):
        r.create_session("coord", "tenant")


@pytest.mark.unit
def test_unknown_runtime_raises():
    with pytest.raises(ValueError):
        get_runtime({"runtime": "nope"})


@pytest.mark.unit
def test_hard_limits_constants():
    assert rt.DELEGATION_DEPTH == 1
    assert rt.MAX_AGENTS_PER_ROSTER == 20
    assert rt.MAX_CONCURRENT_THREADS == 25
