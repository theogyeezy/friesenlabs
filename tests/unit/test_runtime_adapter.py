"""Unit: the runtime adapter is swappable and the real MA impl never touches the network — tests
exercise ManagedAgentsRuntime against a mocked anthropic client only (live shapes stay VERIFY-flagged).
"""
import itertools
from types import SimpleNamespace
from unittest import mock

import pytest

from agents import runtime as rt
from agents.coordinator import COORDINATOR
from agents.roster import SCOUT
from agents.runtime import (
    AGENT_TOOLSET,
    AgentRuntime,
    FakeRuntime,
    ManagedAgentsRuntime,
    get_runtime,
)
from shared.config import MA_BETA_HEADER


@pytest.mark.unit
def test_factory_defaults_to_fake():
    r = get_runtime()
    assert isinstance(r, FakeRuntime)
    assert isinstance(r, AgentRuntime)


@pytest.mark.unit
def test_factory_managed_builds_without_network():
    # Constructing the real runtime must NOT touch Anthropic or require creds.
    r = get_runtime({"runtime": "managed", "api_key": "unused", "environment_id": "env_persisted"})
    assert isinstance(r, ManagedAgentsRuntime)
    assert r._client is None  # client is lazy
    assert r._environment_id == "env_persisted"  # persisted per-tenant env id flows through


@pytest.mark.unit
def test_unknown_runtime_raises():
    with pytest.raises(ValueError):
        get_runtime({"runtime": "nope"})


@pytest.mark.unit
def test_hard_limits_constants():
    assert rt.DELEGATION_DEPTH == 1
    assert rt.MAX_AGENTS_PER_ROSTER == 20
    assert rt.MAX_CONCURRENT_THREADS == 25


# ---------------------------------------------------------------- mocked MA client helpers
def _ev(**kw):
    return SimpleNamespace(**kw)


class _FakeStream:
    """Stands in for client.beta.sessions.events.stream(...) — a context manager over events."""

    def __init__(self, events):
        self._events = list(events)

    def __enter__(self):
        return iter(self._events)

    def __exit__(self, *exc):
        return False


def _mock_client(stream_events=()):
    client = mock.MagicMock(name="anthropic_client")
    counters = {"agent": itertools.count(1), "sess": itertools.count(1)}
    client.beta.environments.create.return_value = SimpleNamespace(id="env_live_1")
    client.beta.agents.create.side_effect = lambda **kw: SimpleNamespace(
        id=f"agent_live_{next(counters['agent'])}", version=1
    )
    client.beta.vaults.create.return_value = SimpleNamespace(id="vault_live_1")
    client.beta.sessions.create.side_effect = lambda **kw: SimpleNamespace(
        id=f"sess_live_{next(counters['sess'])}", status="idle"
    )
    client.beta.sessions.events.stream.return_value = _FakeStream(stream_events)
    client.beta.sessions.events.send.return_value = None
    return client


def _managed(stream_events=()) -> ManagedAgentsRuntime:
    r = ManagedAgentsRuntime(api_key="test-key")
    r._client = _mock_client(stream_events)  # injected — no anthropic import, no network
    return r


_TURN_EVENTS = [
    _ev(type="session.thread_created", agent_name="scout", session_thread_id="th_1"),
    _ev(type="agent.message", content=[_ev(type="text", text="Here are your leads.")]),
    _ev(type="session.status_idle", stop_reason=_ev(type="end_turn")),
]


# ---------------------------------------------------------------- create_* return ids
@pytest.mark.unit
def test_managed_creates_return_ids():
    r = _managed()
    assert r.create_environment("uplift-vpc") == "env_live_1"
    assert r.create_agent(SCOUT) == "agent_live_1"
    coord_id = r.create_coordinator(COORDINATOR, ["agent_live_1"])
    assert coord_id == "agent_live_2"
    assert r.create_vault("Tenant A", external_user_id="user-1") == "vault_live_1"

    # The self-hosted env config + coordinator multiagent shape went over the wire.
    env_kwargs = r._client.beta.environments.create.call_args.kwargs
    assert env_kwargs["config"] == {"type": "self_hosted"}
    coord_kwargs = r._client.beta.agents.create.call_args.kwargs
    assert coord_kwargs["multiagent"] == {"type": "coordinator", "agents": ["agent_live_1"]}


@pytest.mark.unit
def test_managed_serializes_tools_via_to_spec():
    r = _managed()
    r.create_agent(SCOUT)
    tools = r._client.beta.agents.create.call_args.kwargs["tools"]
    # CUSTOM TOOLS ONLY — the built-in toolset is deliberately absent (#147: nothing serves
    # native calls; a granted toolset wedges sessions the first time the model reaches for bash).
    assert {"type": AGENT_TOOLSET} not in tools
    customs = tools
    assert [t["name"] for t in customs] == SCOUT.tools
    for t in customs:
        assert t["type"] == "custom"
        assert t["description"]
        assert t["input_schema"]["type"] == "object"


# ---------------------------------------------------------------- hard limits enforced live
@pytest.mark.unit
def test_managed_roster_cap_enforced():
    r = _managed()
    too_many = [f"agent_{i}" for i in range(rt.MAX_AGENTS_PER_ROSTER + 1)]
    with pytest.raises(ValueError, match="exceeds the MA limit of 20"):
        r.create_coordinator(COORDINATOR, too_many)
    r._client.beta.agents.create.assert_not_called()  # rejected before any live call


@pytest.mark.unit
def test_managed_delegation_depth_enforced():
    r = _managed()
    coord_id = r.create_coordinator(COORDINATOR, ["agent_x"])
    with pytest.raises(ValueError, match="depth is 1"):
        r.create_coordinator(COORDINATOR, [coord_id])  # a coordinator on a roster = depth 2


@pytest.mark.unit
def test_managed_concurrent_session_cap_enforced():
    r = _managed()
    r.create_environment("uplift-vpc")
    for i in range(rt.MAX_CONCURRENT_THREADS):
        r.create_session("coord_1", tenant_id=f"tenant-{i}")
    with pytest.raises(RuntimeError, match="concurrent-thread limit is 25"):
        r.create_session("coord_1", tenant_id="tenant-overflow")


# ---------------------------------------------------------------- session metadata + env gating
@pytest.mark.unit
def test_managed_session_requires_environment():
    r = _managed()
    with pytest.raises(RuntimeError, match="environment_id"):
        r.create_session("coord_1", tenant_id="tenant-a")
    r._client.beta.sessions.create.assert_not_called()


@pytest.mark.unit
def test_managed_session_binds_per_tenant_environment():
    # The caller resolves THIS tenant's persisted env id (WorkspaceStore row) and passes it per
    # session — it must win over any instance-level id (no instance-global serving every tenant).
    r = ManagedAgentsRuntime(api_key="test-key", environment_id="env_global")
    r._client = _mock_client()
    r.create_session("coord_1", tenant_id="tenant-b", environment_id="env_tenant_b")
    assert r._client.beta.sessions.create.call_args.kwargs["environment_id"] == "env_tenant_b"

    # And with NO instance-level id at all, the per-tenant id alone is sufficient.
    r2 = _managed()
    r2.create_session("coord_1", tenant_id="tenant-c", environment_id="env_tenant_c")
    assert r2._client.beta.sessions.create.call_args.kwargs["environment_id"] == "env_tenant_c"


@pytest.mark.unit
def test_managed_create_environment_refuses_to_overwrite_configured_id():
    # A runtime bound to a persisted (per-tenant) environment id must not be silently repointed.
    r = ManagedAgentsRuntime(api_key="test-key", environment_id="env_persisted")
    r._client = _mock_client()
    with pytest.raises(RuntimeError, match="already bound to environment"):
        r.create_environment("uplift-vpc")
    r._client.beta.environments.create.assert_not_called()  # refused before any live call


@pytest.mark.unit
def test_managed_session_carries_tenant_and_vault_metadata():
    r = _managed()
    r.create_environment("uplift-vpc")
    s = r.create_session("coord_1", tenant_id="tenant-a", vault_id="vault_live_1")
    # The Session object matches FakeRuntime's metadata contract (worker RLS binding reads it).
    assert s.tenant_id == "tenant-a"
    assert s.metadata == {"tenant_id": "tenant-a", "vault_id": "vault_live_1"}
    # And the wire call carried tenant + vault into MA session metadata / vault_ids.
    kwargs = r._client.beta.sessions.create.call_args.kwargs
    assert kwargs["metadata"] == {"tenant_id": "tenant-a", "vault_id": "vault_live_1"}
    assert kwargs["vault_ids"] == ["vault_live_1"]
    assert kwargs["environment_id"] == "env_live_1"
    assert kwargs["agent"] == "coord_1"


# ---------------------------------------------------------------- send_message stream flow
@pytest.mark.unit
def test_managed_send_message_returns_fake_compatible_shape():
    r = _managed(stream_events=_TURN_EVENTS)
    r.create_environment("uplift-vpc")
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "find me leads")

    # FakeRuntime's shape ({session_id, tenant_id, delegations, answer}) + pending approvals
    # + tool_results (client-side AUTO executions — ratified #123; empty on a no-tool turn)
    # + usage (additive observed token usage for cost attribution; {0,0} on a no-usage stream).
    fake = FakeRuntime()
    fake_keys = set(fake.send_message(fake.create_session("c", "t"), "x"))
    assert set(out) == fake_keys | {"pending_approvals", "tool_results", "usage"}
    assert out["tool_results"] == []
    # usage defaults to zero tokens when the stream emits no usage block (the cost recorder skips).
    assert out["usage"] == {"input_tokens": 0, "output_tokens": 0, "model": None}
    assert out["session_id"] == session.id
    assert out["tenant_id"] == "tenant-a"
    assert out["delegations"] == ["scout"]
    assert out["answer"] == "Here are your leads."
    assert out["pending_approvals"] == []

    # The real event-stream flow: stream opened FIRST, then the user.message sent.
    send_kwargs = r._client.beta.sessions.events.send.call_args.kwargs
    assert send_kwargs["events"] == [
        {"type": "user.message", "content": [{"type": "text", "text": "find me leads"}]}
    ]
    assert send_kwargs["session_id"] == session.id


@pytest.mark.unit
def test_managed_send_message_surfaces_custom_tool_calls_as_pending():
    events = [
        _ev(
            type="agent.custom_tool_use",
            id="sevt_1",
            name="send_email",
            input={"to": "x@y.co"},
        ),
        _ev(type="session.status_idle", stop_reason=_ev(type="requires_action")),
    ]
    r = _managed(stream_events=events)
    r.create_environment("uplift-vpc")
    session = r.create_session("coord_1", tenant_id="tenant-a")
    out = r.send_message(session, "email the lead")
    assert out["pending_approvals"] == [
        {
            "status": "pending",
            "tool": "send_email",
            "input": {"to": "x@y.co"},
            "custom_tool_use_id": "sevt_1",
        }
    ]


@pytest.mark.unit
def test_managed_send_message_raises_on_terminated():
    events = [
        _ev(type="session.error", error="boom", message=None),
        _ev(type="session.status_terminated"),
    ]
    r = _managed(stream_events=events)
    r.create_environment("uplift-vpc")
    session = r.create_session("coord_1", tenant_id="tenant-a")
    with pytest.raises(RuntimeError, match="terminated"):
        r.send_message(session, "hello")


# ---------------------------------------------------------------- beta header on every call
@pytest.mark.unit
def test_managed_beta_header_present_on_every_call():
    r = _managed(stream_events=_TURN_EVENTS)
    r.create_environment("uplift-vpc")
    agent_id = r.create_agent(SCOUT)
    coord_id = r.create_coordinator(COORDINATOR, [agent_id])
    r.create_vault("Tenant A", external_user_id="user-1")
    session = r.create_session(coord_id, tenant_id="tenant-a", vault_id="vault_live_1")
    r.send_message(session, "go")

    c = r._client
    surfaces = [
        c.beta.environments.create,
        c.beta.agents.create,
        c.beta.vaults.create,
        c.beta.sessions.create,
        c.beta.sessions.events.stream,
        c.beta.sessions.events.send,
    ]
    calls = [call for m in surfaces for call in m.call_args_list]
    assert len(calls) >= 7  # env + 2 agents + vault + session + stream + send
    for call in calls:
        assert call.kwargs["extra_headers"]["anthropic-beta"] == MA_BETA_HEADER


# ---------------------------------------------------------------- list/delete agents (GC seam)
# The orphan-roster reaper (agents/retirement.py) needs to enumerate and delete Managed-Agents
# agents created by superseded rosters. These pin the seam on both impls.
@pytest.mark.unit
def test_fake_runtime_lists_and_deletes_agents():
    r = FakeRuntime()
    a1 = r.create_agent(SCOUT)
    a2 = r.create_agent(SCOUT)
    coord = r.create_coordinator(COORDINATOR, [a1, a2])

    listed = {a["id"]: a for a in r.list_agents()}
    assert set(listed) == {a1, a2, coord}
    assert listed[coord]["is_coordinator"] is True
    assert listed[coord]["agents"] == [a1, a2]
    assert listed[a1]["is_coordinator"] is False

    r.delete_agent(a1)
    assert a1 not in {a["id"] for a in r.list_agents()}
    r.delete_agent(coord)                 # deleting a coordinator drops it from the roster map too
    assert coord not in r.coordinators
    assert {a["id"] for a in r.list_agents()} == {a2}


@pytest.mark.unit
def test_fake_runtime_delete_unknown_agent_is_idempotent():
    r = FakeRuntime()
    r.delete_agent("agent_does_not_exist")   # a double-reap / partial-state delete must not raise


@pytest.mark.unit
def test_managed_list_agents_normalizes_coordinator_topology():
    r = _managed()
    r._client.beta.agents.list.return_value = [
        SimpleNamespace(id="agent_1", name="scout", created_at="2026-06-10T00:00:00Z",
                        multiagent=None),
        SimpleNamespace(id="coord_1", name="coordinator", created_at="2026-06-10T00:00:00Z",
                        multiagent=SimpleNamespace(type="coordinator", agents=["agent_1"])),
    ]
    listed = {a["id"]: a for a in r.list_agents()}
    assert listed["agent_1"]["is_coordinator"] is False
    assert listed["coord_1"]["is_coordinator"] is True
    assert listed["coord_1"]["agents"] == ["agent_1"]
    assert listed["coord_1"]["created_at"] == "2026-06-10T00:00:00Z"
    assert r._client.beta.agents.list.call_args.kwargs["extra_headers"]["anthropic-beta"] \
        == MA_BETA_HEADER


@pytest.mark.unit
def test_managed_delete_agent_archives_via_sdk_with_beta_header():
    # MA has no hard delete — the reaper "removes" an agent by ARCHIVING it (confirmed live
    # 2026-06-15: client.beta.agents exposes archive, not delete).
    r = _managed()
    r.delete_agent("agent_xyz")
    call = r._client.beta.agents.archive.call_args
    assert call.args == ("agent_xyz",)
    assert call.kwargs["extra_headers"]["anthropic-beta"] == MA_BETA_HEADER


@pytest.mark.unit
def test_managed_delete_agent_treats_404_as_already_reaped():
    # A re-run after a partial failure must be idempotent: archiving an already-gone agent 404s,
    # which is success (already reaped), not a failure the reaper should retry forever.
    import anthropic
    r = _managed()
    r._client.beta.agents.archive.side_effect = anthropic.NotFoundError(
        "missing", response=mock.MagicMock(status_code=404), body=None)
    r.delete_agent("agent_gone")          # must NOT raise


@pytest.mark.unit
def test_managed_delete_agent_reraises_non_404_errors():
    import anthropic
    r = _managed()
    r._client.beta.agents.archive.side_effect = anthropic.APIError(
        "boom", request=mock.MagicMock(), body=None)
    with pytest.raises(anthropic.APIError):
        r.delete_agent("agent_x")


@pytest.mark.unit
def test_base_runtime_agent_management_is_unsupported_by_default():
    # SelfHosted/other runtimes don't manage MA agents — the seam must default to a clear error,
    # not silently no-op (which would let the reaper believe it deleted something).
    class _Bare(AgentRuntime):
        def create_environment(self, name): return "e"
        def create_agent(self, spec): return "a"
        def create_coordinator(self, spec, agent_ids): return "c"
        def create_vault(self, display_name, external_user_id): return "v"
        def create_session(self, coordinator_id, tenant_id, vault_id=None, environment_id=None): ...
        def send_message(self, session, message): return {}
    b = _Bare()
    with pytest.raises(NotImplementedError):
        b.list_agents()
    with pytest.raises(NotImplementedError):
        b.delete_agent("a")


@pytest.mark.unit
def test_managed_list_agents_extracts_ids_from_agent_reference_objects():
    # Live MA returns multiagent.agents as reference OBJECTS (BetaManagedAgentsAgentReference with an
    # `.id`), NOT bare strings (caught live 2026-06-15). The normalizer must extract the ids so the
    # reaper targets real id strings, not repr'd objects.
    r = _managed()
    ref1 = SimpleNamespace(id="agent_pinned_1", type="agent", version=1)
    ref2 = SimpleNamespace(id="agent_pinned_2", type="agent", version=2)
    r._client.beta.agents.list.return_value = [
        SimpleNamespace(id="coord_1", name="orchestrator", created_at=None,
                        multiagent=SimpleNamespace(type="coordinator", agents=[ref1, ref2])),
    ]
    listed = {a["id"]: a for a in r.list_agents()}
    assert listed["coord_1"]["agents"] == ["agent_pinned_1", "agent_pinned_2"]   # ids, not objects
    assert all(isinstance(x, str) for x in listed["coord_1"]["agents"])
