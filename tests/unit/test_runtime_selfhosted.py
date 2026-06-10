"""Unit: the HIPAA fallback runtime (agents/runtime_selfhosted.py) — a mocked-client Messages
tool-use loop. NOTHING here touches the network: tests inject a canned `client` whose
`messages.create` replays scripted responses (the live Bedrock-vs-1P endpoint choice stays
VERIFY-flagged in the module).
"""
from types import SimpleNamespace

import pytest

from agents import coordinator as coord
from agents.runtime import AgentRuntime, FakeRuntime, get_runtime
from agents.runtime_selfhosted import DEFAULT_MAX_TURNS, SelfHostedToolUseRuntime
from agents.tools.base import InMemoryGreenlight
from agents.tools.registry import TOOL_REGISTRY
from agents.workspace_store import InMemoryWorkspaceStore


# ---------------------------------------------------------------- mocked Messages client
def _text(t):
    return SimpleNamespace(type="text", text=t)


def _tool_use(name, input, id="tu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=input, id=id)


def _resp(*blocks, stop_reason="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason)


class _SeqClient:
    """Replays canned Messages responses in order; `repeat_last=True` loops the final one
    forever (for the turn-bound test). Records every create() kwargs."""

    def __init__(self, responses, repeat_last=False):
        self.calls: list[dict] = []
        self._responses = list(responses)
        self._repeat_last = repeat_last
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        if len(self._responses) > 1 or not self._repeat_last:
            return self._responses.pop(0)
        return self._responses[0]


def _runtime(responses, repeat_last=False, **kw) -> tuple[SelfHostedToolUseRuntime, _SeqClient]:
    client = _SeqClient(responses, repeat_last=repeat_last)
    rt = SelfHostedToolUseRuntime(api_key="unused", **kw)
    rt._client = client  # injected — no anthropic import, no network
    return rt, client


# ---------------------------------------------------------------- factory + construction
@pytest.mark.unit
def test_factory_self_hosted_builds_without_network():
    rt = get_runtime({"runtime": "self_hosted", "api_key": "unused"})
    assert isinstance(rt, SelfHostedToolUseRuntime)
    assert isinstance(rt, AgentRuntime)
    assert rt._client is None  # client is lazy
    assert rt.max_turns == DEFAULT_MAX_TURNS


@pytest.mark.unit
def test_factory_existing_kinds_unchanged_and_unknown_still_errors():
    assert isinstance(get_runtime(), FakeRuntime)
    assert isinstance(get_runtime({"runtime": "fake"}), FakeRuntime)
    from agents.runtime import ManagedAgentsRuntime
    assert isinstance(get_runtime({"runtime": "managed"}), ManagedAgentsRuntime)
    # An unknown key must still fail loud — a HIPAA tenant must never silently land on a
    # default runtime because of a typo'd kind.
    with pytest.raises(ValueError, match="unknown runtime"):
        get_runtime({"runtime": "bedrock"})
    with pytest.raises(ValueError, match="unknown runtime"):
        get_runtime({"runtime": "selfhosted"})  # the key is 'self_hosted', exactly


@pytest.mark.unit
def test_max_turns_must_be_positive():
    with pytest.raises(ValueError, match="max_turns"):
        SelfHostedToolUseRuntime(max_turns=0)


# ---------------------------------------------------------------- synthetic ids + persistence
@pytest.mark.unit
def test_create_ids_are_synthetic_and_persisted_via_workspace_store():
    store = InMemoryWorkspaceStore()
    rt = SelfHostedToolUseRuntime(workspace_store=store, tenant_id="tenant-a")

    # The standard provisioning sequence works unchanged over the fallback runtime.
    coordinator_id = coord.build(rt)

    row = store.get("tenant-a")
    assert row is not None
    assert row["coordinator_id"] == coordinator_id
    assert row["environment_id"].startswith("selfhosted-env-")
    assert row["coordinator_id"].startswith("selfhosted-coord-")
    # NOT the offline placeholder prefix: the asgi factory refuses 'stub-' ids on real runtimes.
    for v in (row["environment_id"], row["coordinator_id"]):
        assert not v.startswith("stub-")
    # Tenant-scoped: nothing leaked onto another tenant's key.
    assert store.get("tenant-b") is None


@pytest.mark.unit
def test_create_without_store_returns_local_ids_only():
    rt = SelfHostedToolUseRuntime()
    eid = rt.create_environment("uplift-hipaa")
    vid = rt.create_vault("Tenant A", external_user_id="user-1")
    assert eid.startswith("selfhosted-env-")
    assert vid.startswith("selfhosted-vault-")


@pytest.mark.unit
def test_hard_limits_match_the_managed_plane():
    rt = SelfHostedToolUseRuntime()
    too_many = [f"agent_{i}" for i in range(21)]
    with pytest.raises(ValueError, match="exceeds the limit of 20"):
        rt.create_coordinator(coord.COORDINATOR, too_many)
    cid = rt.create_coordinator(coord.COORDINATOR, ["agent_x"])
    with pytest.raises(ValueError, match="depth is 1"):
        rt.create_coordinator(coord.COORDINATOR, [cid])


@pytest.mark.unit
def test_session_carries_tenant_and_vault_metadata():
    rt = SelfHostedToolUseRuntime()
    s = rt.create_session("selfhosted-coord-x", tenant_id="tenant-a", vault_id="selfhosted-vault-1")
    # THE TRUST RULE: the caller-supplied (verified-claim) tenant rides the session metadata.
    assert s.tenant_id == "tenant-a"
    assert s.metadata == {"tenant_id": "tenant-a", "vault_id": "selfhosted-vault-1"}


# ---------------------------------------------------------------- the tool-use loop
class _FakeCrm:
    def __init__(self, rows):
        self.rows = rows
        self.tenants: list[str] = []

    def set_tenant(self, tenant_id):
        self.tenants.append(tenant_id)

    def read(self, *, entity, limit=50):
        return self.rows


def _session(rt, tenant="tenant-a"):
    return rt.create_session("selfhosted-coord-x", tenant_id=tenant)


@pytest.mark.unit
def test_tool_use_loop_round_trip_with_mocked_client():
    rows = [{"id": "d1", "name": "Acme expansion"}]
    crm = _FakeCrm(rows)

    def ctx_factory(session):
        from agents.tools.base import ToolContext
        return ToolContext(tenant_id=session.metadata["tenant_id"], db=crm)

    rt, client = _runtime(
        [
            _resp(_tool_use("read_crm", {"entity": "deals"}, id="tu_1"), stop_reason="tool_use"),
            _resp(_text("You have 1 open deal: Acme expansion.")),
        ],
        tool_context_factory=ctx_factory,
    )
    session = _session(rt)
    out = rt.send_message(session, "what deals are open?")

    # ManagedAgentsRuntime-compatible digest shape (FakeRuntime keys + pending_approvals).
    fake = FakeRuntime()
    fake_keys = set(fake.send_message(fake.create_session("c", "t"), "x"))
    assert set(out) == fake_keys | {"pending_approvals"}
    assert out["session_id"] == session.id
    assert out["tenant_id"] == "tenant-a"
    assert out["delegations"] == []  # single-model loop: never any subagent threads
    assert out["answer"] == "You have 1 open deal: Acme expansion."
    assert out["pending_approvals"] == []

    # Two model calls; the second carried the tool_result with the real rows back to the model.
    assert len(client.calls) == 2
    second = client.calls[1]["messages"]
    tool_results = [b for b in second[-1]["content"] if b["type"] == "tool_result"]
    assert tool_results[0]["tool_use_id"] == "tu_1"
    assert "Acme expansion" in tool_results[0]["content"]
    assert "is_error" not in tool_results[0]
    # RLS: the tool bound the session's tenant before reading.
    assert crm.tenants == ["tenant-a"]


@pytest.mark.unit
def test_loop_uses_plain_messages_tool_shape_and_no_ma_header():
    rt, client = _runtime([_resp(_text("hi"))])
    rt.send_message(_session(rt), "hello")

    kw = client.calls[0]
    # Plain /v1/messages surface: every registry tool in the client-side shape, no MA wrapper,
    # and no MA beta header (this is not a client.beta.* namespace).
    assert {t["name"] for t in kw["tools"]} == set(TOOL_REGISTRY)
    for t in kw["tools"]:
        assert set(t) == {"name", "description", "input_schema"}
        assert t["input_schema"]["type"] == "object"
    assert "extra_headers" not in kw
    # The coordinator persona + the self-hosted runtime note drive the loop.
    assert "RUNTIME NOTE" in kw["system"]
    assert kw["model"]  # roster default (bare 1P id; Bedrock prefixing is the client_factory seam)


@pytest.mark.unit
def test_always_ask_routes_to_greenlight_proposal_not_execution():
    gl = InMemoryGreenlight()
    rt, client = _runtime(
        [
            _resp(_tool_use("send_email", {"to": "x@y.co", "body": "hi"}, id="tu_9"),
                  stop_reason="tool_use"),
            _resp(_text("Queued the email for your approval.")),
        ],
        greenlight=gl,
    )
    out = rt.send_message(_session(rt), "email the lead")

    # The SAME Greenlight ALWAYS_ASK routing: exactly one PROPOSAL, no side effect performed.
    assert len(gl.queue) == 1
    rec = gl.queue[0]
    assert rec["status"] == "pending"
    assert rec["action"] == "send_email"
    assert rec["tenant_id"] == "tenant-a"

    # Surfaced as an ALREADY-ROUTED pending entry: carries tool_name (NOT 'tool'), so
    # conv.session.Conversation passes it through untouched instead of re-invoking the tool
    # (which would enqueue the proposal twice).
    assert len(out["pending_approvals"]) == 1
    entry = out["pending_approvals"][0]
    assert entry["status"] == "pending_approval"
    assert entry["tool_name"] == "send_email"
    assert "tool" not in entry
    assert entry["custom_tool_use_id"] == "tu_9"
    assert entry["approval"]["id"] == rec["id"]
    assert entry["proposal"]["action"] == "send_email"

    # The model was told the action is queued, NOT that it executed.
    tool_results = [b for b in client.calls[1]["messages"][-1]["content"]
                    if b["type"] == "tool_result"]
    assert "pending_approval" in tool_results[0]["content"]
    assert "NOT performed" in tool_results[0]["content"]
    assert out["answer"] == "Queued the email for your approval."


@pytest.mark.unit
def test_always_ask_without_greenlight_still_only_proposes():
    rt, _ = _runtime(
        [
            _resp(_tool_use("update_deal", {"deal_id": "d1", "changes": {"stage": "won"}}),
                  stop_reason="tool_use"),
            _resp(_text("Awaiting approval.")),
        ],
    )  # no greenlight injected
    out = rt.send_message(_session(rt), "move the deal")
    entry = out["pending_approvals"][0]
    assert entry["status"] == "pending_approval"
    assert entry["greenlight"] == "unconfigured"
    assert entry["proposal"]["action"] == "update_deal"  # surfaced; nothing silently executed


@pytest.mark.unit
def test_turn_bound_enforced():
    rt, client = _runtime(
        [_resp(_tool_use("read_crm", {"entity": "deals"}), stop_reason="tool_use")],
        repeat_last=True,
        max_turns=3,
    )
    with pytest.raises(RuntimeError, match="max_turns=3"):
        rt.send_message(_session(rt), "loop forever")
    assert len(client.calls) == 3  # exactly the bound — never a 4th model call


@pytest.mark.unit
def test_unknown_tool_never_default_allowed():
    rt, client = _runtime(
        [
            _resp(_tool_use("rm_rf", {"path": "/"}, id="tu_evil"), stop_reason="tool_use"),
            _resp(_text("I can't do that.")),
        ],
    )
    out = rt.send_message(_session(rt), "wipe everything")

    assert out["pending_approvals"] == [
        {"status": "unknown_tool", "tool_name": "rm_rf", "custom_tool_use_id": "tu_evil"}
    ]
    # The refusal was fed back as an error result — nothing resolved, nothing executed.
    tool_results = [b for b in client.calls[1]["messages"][-1]["content"]
                    if b["type"] == "tool_result"]
    assert tool_results[0]["is_error"] is True
    assert "refusing to execute" in tool_results[0]["content"]
    assert out["answer"] == "I can't do that."


@pytest.mark.unit
def test_tool_exception_degrades_to_error_result_not_crash():
    class _ExplodingCrm:
        def set_tenant(self, tenant_id): ...
        def read(self, **kw):
            raise ConnectionError("db unreachable")

    def ctx_factory(session):
        from agents.tools.base import ToolContext
        return ToolContext(tenant_id=session.metadata["tenant_id"], db=_ExplodingCrm())

    rt, client = _runtime(
        [
            _resp(_tool_use("read_crm", {"entity": "deals"}), stop_reason="tool_use"),
            _resp(_text("The CRM is unreachable right now.")),
        ],
        tool_context_factory=ctx_factory,
    )
    out = rt.send_message(_session(rt), "read deals")
    tool_results = [b for b in client.calls[1]["messages"][-1]["content"]
                    if b["type"] == "tool_result"]
    assert tool_results[0]["is_error"] is True
    assert "db unreachable" in tool_results[0]["content"]
    assert out["answer"] == "The CRM is unreachable right now."


@pytest.mark.unit
def test_multi_turn_history_persists_per_session():
    rt, client = _runtime([_resp(_text("first")), _resp(_text("second"))])
    session = _session(rt)
    rt.send_message(session, "turn one")
    rt.send_message(session, "turn two")

    msgs = client.calls[1]["messages"]
    texts = [b["text"] for m in msgs for b in m["content"] if b.get("type") == "text"]
    assert texts == ["turn one", "first", "turn two"]  # full history replayed (API is stateless)
