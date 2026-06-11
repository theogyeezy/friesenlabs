"""Integration: the /control surface — kill switch, autonomy dial, decision traces.

Drives the REAL app factory (create_app) with the fake verifier the sibling API tests use.
Proves the web-lane wire contract EXACTLY:

  GET/PUT /control/killswitch -> {"engaged": bool, "scope": str}
  GET/PUT /control/autonomy   -> {"level": int}
  GET     /control/traces     -> {"traces": [{id, ts, tool, decision, status, summary}], "cursor"}

…and the semantics behind it: a flipped switch blocks the gate (/actions) AND the
approval-decide path; the dial changes what the gate auto-executes; traces are claims-bound,
newest-first, and paginate. THE TRUST RULE throughout: tenant only from the verified claim.
"""
import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig, Thresholds
from api.control.greenlight import Greenlight
from api.control.settings import (
    InMemoryControlSettings,
    PersistedAutonomyDial,
    PersistedKillSwitch,
)
from api.control.types import Level
from api.routes_control import ENV_CONTROL_GLOBAL_OPERATORS
from api.views import SavedViews


class FakeVerifier:
    def __init__(self, tenant="A", sub="uA"):
        self.tenant, self.sub = tenant, sub

    def verify(self, token):
        return {"sub": self.sub, "custom:tenant_id": self.tenant, "email": "a@x.com"}


H = {"Authorization": "Bearer t"}


def _deps(*, tenant="A", store=None, default_level=Level.L1):
    """ApiDeps wired the way api/asgi.py wires prod: persisted killswitch + dial over ONE
    shared settings store (in-memory here; Aurora live), provider plugged into the config."""
    store = store if store is not None else InMemoryControlSettings()
    dial = PersistedAutonomyDial(store, ttl_seconds=0.0)
    executed = []
    deps = ApiDeps(
        verifier=FakeVerifier(tenant=tenant),
        greenlight=Greenlight(),
        saved_views=SavedViews(),
        conversation_factory=lambda tenant_id: None,
        autonomy_config=AutonomyConfig(default_level=default_level,
                                       thresholds=Thresholds(max_auto_value=1000),
                                       level_provider=dial.provider),
        executor=lambda a: executed.append(a) or {"ran": True},
        killswitch=PersistedKillSwitch(store, ttl_seconds=0.0),
        autonomy_dial=dial,
    )
    return deps, executed


def _client(**kw):
    deps, executed = _deps(**kw)
    return TestClient(create_app(deps)), executed, deps


# --------------------------------------------------------------------------- kill switch
@pytest.mark.integration
def test_killswitch_contract_and_roundtrip():
    client, _, _ = _client()
    assert client.get("/control/killswitch", headers=H).json() == \
        {"engaged": False, "scope": "tenant"}
    r = client.put("/control/killswitch", json={"engaged": True}, headers=H)
    assert r.status_code == 200
    assert r.json() == {"engaged": True, "scope": "tenant"}
    assert client.get("/control/killswitch", headers=H).json() == \
        {"engaged": True, "scope": "tenant"}
    r = client.put("/control/killswitch", json={"engaged": False}, headers=H)
    assert r.json() == {"engaged": False, "scope": "tenant"}


@pytest.mark.integration
def test_engaged_killswitch_blocks_gate_and_approval_decide():
    client, executed, _ = _client(default_level=Level.L3)
    client.put("/control/killswitch", json={"engaged": True}, headers=H)

    # 1) The gate: even a read-only action is blocked while paused.
    r = client.post("/actions", json={"name": "read_crm"}, headers=H).json()
    assert r["status"] == "blocked"
    assert "kill switch" in r["detail"]
    assert executed == []

    # 2) Release, queue a side effect, re-engage, then try to approve -> 409 and still pending.
    client.put("/control/killswitch", json={"engaged": False}, headers=H)
    body = {"name": "send_email", "payload": {"body": "hi unsubscribe"}}
    client_l1, _, deps_l1 = _client()  # L1 default: send_email pends
    client_l1.post("/actions", json=body, headers=H)
    approvals = client_l1.get("/approvals", headers=H).json()["approvals"]
    assert len(approvals) == 1
    aid = approvals[0]["id"]
    client_l1.put("/control/killswitch", json={"engaged": True}, headers=H)
    r = client_l1.post(f"/approvals/{aid}/decide", json={"decision": "approve"}, headers=H)
    assert r.status_code == 409  # the engaged switch blocks the apply path
    # The approval was NOT consumed — re-approve works after release.
    client_l1.put("/control/killswitch", json={"engaged": False}, headers=H)
    r = client_l1.post(f"/approvals/{aid}/decide", json={"decision": "approve"}, headers=H)
    assert r.status_code == 200


@pytest.mark.integration
def test_global_scope_is_operator_only(monkeypatch):
    client, _, _ = _client()
    # Not allowlisted -> 403, and nothing flipped.
    r = client.put("/control/killswitch", json={"engaged": True, "scope": "global"}, headers=H)
    assert r.status_code == 403
    assert client.get("/control/killswitch", headers=H).json()["engaged"] is False

    # Allowlist the caller's USER (sub "uA" from the verified claim — the allowlist is
    # user-granular now, never tenant-granular) -> the flip lands, and pauses OTHER tenants too.
    monkeypatch.setenv(ENV_CONTROL_GLOBAL_OPERATORS, "uA, other-op")
    store = InMemoryControlSettings()
    op_client, _, _ = _client(store=store)
    r = op_client.put("/control/killswitch", json={"engaged": True, "scope": "global"}, headers=H)
    assert r.status_code == 200
    assert r.json() == {"engaged": True, "scope": "global"}
    tenant_b, executed_b, _ = _client(tenant="B", store=store)  # same shared persistence
    assert tenant_b.get("/control/killswitch", headers=H).json() == \
        {"engaged": True, "scope": "global"}
    assert tenant_b.post("/actions", json={"name": "read_crm"}, headers=H).json()["status"] \
        == "blocked"
    assert executed_b == []


@pytest.mark.integration
def test_killswitch_validation():
    client, _, _ = _client()
    assert client.put("/control/killswitch", json={"engaged": True, "scope": "everything"},
                      headers=H).status_code == 422
    assert client.put("/control/killswitch", json={}, headers=H).status_code == 422
    assert client.get("/control/killswitch").status_code == 401  # unauthed


@pytest.mark.integration
def test_two_app_instances_share_the_flip():
    """Multi-instance semantics offline: two separate apps (two 'API tasks') over ONE shared
    settings store — instance A flips, instance B's gate blocks. The real-Pg twin lives in
    tests/integration/test_control_rls.py."""
    store = InMemoryControlSettings()
    app_a, _, _ = _client(store=store)
    app_b, executed_b, _ = _client(store=store)
    app_a.put("/control/killswitch", json={"engaged": True}, headers=H)
    assert app_b.get("/control/killswitch", headers=H).json()["engaged"] is True
    assert app_b.post("/actions", json={"name": "read_crm"}, headers=H).json()["status"] \
        == "blocked"
    assert executed_b == []


# --------------------------------------------------------------------------- autonomy dial
@pytest.mark.integration
def test_autonomy_contract_roundtrip_and_gate_effect():
    client, executed, _ = _client()
    assert client.get("/control/autonomy", headers=H).json() == {"level": 1}  # the L1 default

    body = {"name": "send_email", "payload": {"body": "hi unsubscribe"}}
    assert client.post("/actions", json=body, headers=H).json()["status"] == "pending_approval"
    assert executed == []

    # Dial to L3 -> the SAME side effect now auto-executes through the gate.
    assert client.put("/control/autonomy", json={"level": 3}, headers=H).json() == {"level": 3}
    assert client.get("/control/autonomy", headers=H).json() == {"level": 3}
    assert client.post("/actions", json=body, headers=H).json()["status"] == "ok"
    assert len(executed) == 1

    # Dial to L0 -> everything side-effecting pends again.
    client.put("/control/autonomy", json={"level": 0}, headers=H)
    assert client.post("/actions", json=body, headers=H).json()["status"] == "pending_approval"
    assert len(executed) == 1


@pytest.mark.integration
def test_autonomy_validation():
    client, _, _ = _client()
    for bad in (-1, 4, 99):
        assert client.put("/control/autonomy", json={"level": bad}, headers=H).status_code == 422
    assert client.put("/control/autonomy", json={"level": "high"}, headers=H).status_code == 422
    assert client.get("/control/autonomy").status_code == 401  # unauthed


@pytest.mark.integration
def test_autonomy_dial_fallback_without_wired_dial():
    """No autonomy_dial wired (offline default) -> the routes ride AutonomyDial over the gate's
    own AutonomyConfig.overrides, so flips are still gate-visible."""
    executed = []
    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda tenant_id: None,
        autonomy_config=AutonomyConfig(),
        executor=lambda a: executed.append(a) or {"ran": True},
    )
    client = TestClient(create_app(deps))
    assert client.get("/control/autonomy", headers=H).json() == {"level": 1}
    client.put("/control/autonomy", json={"level": 3}, headers=H)
    body = {"name": "send_email", "payload": {"body": "hi unsubscribe"}}
    assert client.post("/actions", json=body, headers=H).json()["status"] == "ok"
    assert len(executed) == 1


# --------------------------------------------------------------------------- decision traces
@pytest.mark.integration
def test_traces_contract_pagination_and_tenant_binding():
    client, _, deps = _client(default_level=Level.L3)
    # Three gate runs -> three traces: executed (read), pending (L1 would pend; here use L0 dial).
    client.post("/actions", json={"name": "read_crm"}, headers=H)
    client.put("/control/autonomy", json={"level": 0}, headers=H)
    client.post("/actions", json={"name": "send_email",
                                  "payload": {"body": "hi unsubscribe"},
                                  "reasoning": "warm intro"}, headers=H)
    client.post("/actions", json={"name": "send_email",
                                  "payload": {"body": "no unsub"}}, headers=H)  # blocked

    r = client.get("/control/traces", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"traces", "cursor"}
    traces = body["traces"]
    assert len(traces) == 3
    # EXACT wire shape per row.
    for t in traces:
        assert set(t) == {"id", "ts", "tool", "decision", "status", "summary"}
    # Newest first: blocked, pending, executed.
    assert [t["status"] for t in traces] == ["blocked", "pending_approval", "executed"]
    assert [t["decision"] for t in traces] == ["block", "approve", "auto"]
    assert traces[1]["tool"] == "send_email" and traces[1]["summary"] == "warm intro"
    assert traces[2]["tool"] == "read_crm"
    assert body["cursor"] is None  # short page

    # Pagination: limit=1 pages walk the same set with no overlap.
    seen, cursor = [], None
    for _ in range(4):
        q = "/control/traces?limit=1" + (f"&cursor={cursor}" if cursor else "")
        page = client.get(q, headers=H).json()
        seen += [t["id"] for t in page["traces"]]
        cursor = page["cursor"]
        if cursor is None:
            break
    assert len(seen) == 3 and len(set(seen)) == 3

    # Claims-bound: another tenant sees an empty ledger, never tenant A's rows.
    deps_b = ApiDeps(
        verifier=FakeVerifier(tenant="B"), greenlight=deps.greenlight,
        saved_views=deps.saved_views, conversation_factory=lambda t: None,
        autonomy_config=deps.autonomy_config, executor=deps.executor,
        killswitch=deps.killswitch, trace_store=deps.trace_store,
        autonomy_dial=deps.autonomy_dial,
    )
    client_b = TestClient(create_app(deps_b))
    assert client_b.get("/control/traces", headers=H).json() == {"traces": [], "cursor": None}


@pytest.mark.integration
def test_traces_validation():
    client, _, _ = _client()
    assert client.get("/control/traces?cursor=garbage", headers=H).status_code == 422
    assert client.get("/control/traces").status_code == 401  # unauthed
