"""Integration: GET /agents — the real Agents tab (the tenant's crew, read-only).

Proves the api half of the agents vertical slice (the test shapes mirror test_api_contacts.py):
  * 401 unauth (the shared current_tenant dependency)
  * tenant ALWAYS from the verified claims — a smuggled tenant (query param) is ignored
  * provisioned tenant (non-stub tenant_workspaces row): the OWNED roster (7 specialists +
    the coordinator, distinguished) with per-tool policies from the TRUSTED registry, plus
    the row's ids TRUNCATED to a display tail — the FULL ids never appear in the body
  * unprovisioned tenant (no row / incomplete row): the SAME roster with provisioned=false
    and no id tails (the "your crew assembles at signup" state)
  * stub row (the offline _Noop placeholder ids): provisioned=false, 'stub-' never leaves
  * internal tenant_id never leaves the API
  * unconfigured deps (no DSN -> no store) answer the honest 503
  * the default ApiDeps mounts the route with the honest stub (never 404 / fake success)
  * READ-ONLY: only GET is mounted (POST/PUT/PATCH/DELETE -> 405) and the store sees only
    get() calls — no upsert ever
  * NO live Managed Agents call: the store row is the only collaborator the route touches
"""
import pytest
from fastapi.testclient import TestClient

from agents.roster import ROSTER
from agents.tools.base import Policy
from agents.tools.registry import TOOL_REGISTRY
from api.agents_routes import ID_TAIL_LEN, AgentsDeps
from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.views import SavedViews

H = {"Authorization": "Bearer t"}

# Realistically-shaped Managed Agents ids (the live env id really looks like this).
WORKSPACE_ID = "wrkspc_01HZk9d3XBVxqJpTr88AAAAA"
ENVIRONMENT_ID = "env_012JvqRKUZzUDeH3Gse6TBgZ"
COORDINATOR_ID = "agent_01Y4mPGcVVuVxQq2hcZZZZZZ"


class FakeVerifier:
    def verify(self, token):
        return {"sub": "uA", "custom:tenant_id": "A", "email": "a@x.com"}


class FakeWorkspaceStore:
    """In-memory WorkspaceStore-shaped reader. Rows are keyed by tenant — the fake honors the
    RLS contract (a read for tenant A can never surface tenant B's row) and records every call
    so tests can assert the claims tenant steered each one. get() is the ONLY read the route
    may use; any attempted upsert would be recorded and failed by the read-only test."""

    def __init__(self, rows=None):
        self.rows = dict(rows or {})
        self.calls: list[tuple] = []

    def get(self, tenant_id):
        self.calls.append(("get", tenant_id))
        row = self.rows.get(str(tenant_id))
        return dict(row) if row else None


def _row(tenant="A", workspace=WORKSPACE_ID, env=ENVIRONMENT_ID, coord=COORDINATOR_ID):
    return {"tenant_id": tenant, "workspace_id": workspace,
            "environment_id": env, "coordinator_id": coord}


def _client(agents=None):
    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
        executor=lambda a: None,
        agents=agents if agents is not None else AgentsDeps(),
    )
    return TestClient(create_app(deps))


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_unauth_401():
    client = _client(AgentsDeps(workspace_store=FakeWorkspaceStore({"A": _row()})))
    assert client.get("/agents").status_code == 401


# --------------------------------------------------------------------------- #
# honest unconfigured stub (no DSN -> no store)
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_unconfigured_503_never_fake_crew():
    client = _client(AgentsDeps(workspace_store=None))
    r = client.get("/agents", headers=H)
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"]


@pytest.mark.integration
def test_default_apideps_mounts_route_with_honest_stub():
    # ApiDeps without an explicit `agents` builds the INERT default stub — the route must
    # mount and answer the honest 503 (not a 404, not an invented crew), and constructing
    # the deps must never open a DB pool regardless of env (CI carries UPLIFT_DB_URL for the
    # RLS proofs; the real store is wired ONLY by api/asgi.py).
    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
        executor=lambda a: None,
    )
    client = TestClient(create_app(deps))
    assert client.get("/agents", headers=H).status_code == 503


# --------------------------------------------------------------------------- #
# provisioned tenant — the crew with truncated ids
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_provisioned_tenant_gets_roster_with_truncated_ids():
    store = FakeWorkspaceStore({"A": _row()})
    client = _client(AgentsDeps(workspace_store=store))
    r = client.get("/agents", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["provisioned"] is True
    # Ids are TRUNCATED for display: the last ID_TAIL_LEN chars only.
    assert body["environment_id_tail"] == ENVIRONMENT_ID[-ID_TAIL_LEN:]
    assert body["coordinator"]["id_tail"] == COORDINATOR_ID[-ID_TAIL_LEN:]
    # The crew mirrors the OWNED definitions exactly: all 7 specialists, in roster order.
    assert body["count"] == 7
    assert [a["name"] for a in body["roster"]] == [s.name for s in ROSTER]
    # The coordinator is distinguished, with the orchestrator's own duty description.
    assert body["coordinator"]["name"] == "uplift-orchestrator"
    assert body["coordinator"]["is_coordinator"] is True
    assert body["coordinator"]["role"] == "Coordinator"
    assert all(a["is_coordinator"] is False for a in body["roster"])
    # The read was steered by the CLAIMS tenant, and ONLY get() ever ran (read-only).
    assert store.calls == [("get", "A")]


@pytest.mark.integration
def test_full_ids_never_in_response_body():
    # THE TRUNCATION RULE: the full Managed Agents ids are operator material. Not the
    # workspace id (not even truncated — it isn't returned at all), not the environment id,
    # not the coordinator id may appear anywhere in the body.
    client = _client(AgentsDeps(workspace_store=FakeWorkspaceStore({"A": _row()})))
    r = client.get("/agents", headers=H)
    assert r.status_code == 200
    for full_id in (WORKSPACE_ID, ENVIRONMENT_ID, COORDINATOR_ID):
        assert full_id not in r.text
    # The internal tenant_id never leaves the API either.
    assert '"tenant_id"' not in r.text


@pytest.mark.integration
def test_tool_policies_come_from_the_trusted_registry():
    # The autonomy story must be the REGISTRY's truth, tool by tool: every roster tool's
    # policy in the response equals its Tool class policy (auto vs always_ask) — never a
    # hand-written flag, never drift from the definitions the action gate enforces.
    client = _client(AgentsDeps(workspace_store=FakeWorkspaceStore({"A": _row()})))
    body = client.get("/agents", headers=H).json()
    by_name = {a["name"]: a for a in body["roster"]}
    for spec in ROSTER:
        got = by_name[spec.name]["tools"]
        assert [t["name"] for t in got] == list(spec.tools)
        for t in got:
            assert t["policy"] == TOOL_REGISTRY[t["name"]].policy.value
    # Spot-check the story's poles: reads run on their own, mutations always ask.
    assert {"name": "search_rag", "policy": Policy.AUTO.value} in by_name["scout"]["tools"]
    assert {"name": "update_deal", "policy": Policy.ALWAYS_ASK.value} in by_name["ledger"]["tools"]
    assert {"name": "issue_quote", "policy": Policy.ALWAYS_ASK.value} in by_name["margo"]["tools"]
    # draft_email drafts only (no send) — AUTO, the draft-first guarantee made visible.
    assert {"name": "draft_email", "policy": Policy.AUTO.value} in by_name["nadia"]["tools"]
    # The critic carries no custom tools; the coordinator delegates, it doesn't tool-run.
    assert by_name["critic"]["tools"] == []
    assert body["coordinator"]["tools"] == []


@pytest.mark.integration
def test_smuggled_tenant_params_ignored():
    store = FakeWorkspaceStore({
        "A": _row(),
        "B": _row("B", "wrkspc_Bsecret000000", "env_Bsecret000000", "agent_Bsecret000000"),
    })
    client = _client(AgentsDeps(workspace_store=store))
    r = client.get("/agents?tenant_id=B&tenant=B", headers=H)
    assert r.status_code == 200
    # The read was steered by the VERIFIED claim only; tenant B's tails never surface.
    assert all(c == ("get", "A") for c in store.calls)
    assert "Bsecret" not in r.text
    assert r.json()["environment_id_tail"] == ENVIRONMENT_ID[-ID_TAIL_LEN:]


# --------------------------------------------------------------------------- #
# unprovisioned tenant — same roster, honest false
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_unprovisioned_tenant_gets_roster_with_provisioned_false():
    store = FakeWorkspaceStore({})  # no row at all
    client = _client(AgentsDeps(workspace_store=store))
    r = client.get("/agents", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["provisioned"] is False
    assert body["environment_id_tail"] is None
    assert body["coordinator"]["id_tail"] is None
    # The SAME definitions still come back so the UI can show the crew that WILL assemble.
    assert body["count"] == 7
    assert [a["name"] for a in body["roster"]] == [s.name for s in ROSTER]
    assert store.calls == [("get", "A")]


@pytest.mark.integration
def test_incomplete_row_is_unprovisioned():
    # A row missing the coordinator id (mid-provisioning / a rolled-back signup) is NOT a
    # provisioned crew — same contract as the /chat conversation factory.
    store = FakeWorkspaceStore({"A": _row(coord=None)})
    client = _client(AgentsDeps(workspace_store=store))
    body = client.get("/agents", headers=H).json()
    assert body["provisioned"] is False
    assert body["environment_id_tail"] is None
    assert body["coordinator"]["id_tail"] is None


@pytest.mark.integration
def test_stub_row_is_unprovisioned_and_stub_never_leaks():
    # The offline _Noop agent plane persists 'stub-' placeholder ids. Those are NOT a live
    # crew (api/asgi.py refuses to ride them with a real runtime) — provisioned must be
    # false, and no stub fragment (not even a truncated tail) may leave the API.
    store = FakeWorkspaceStore({"A": _row(workspace="stub-ws", env="stub-env",
                                          coord="stub-coordinator")})
    client = _client(AgentsDeps(workspace_store=store))
    r = client.get("/agents", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["provisioned"] is False
    assert body["environment_id_tail"] is None
    assert body["coordinator"]["id_tail"] is None
    assert "stub-" not in r.text


# --------------------------------------------------------------------------- #
# READ-ONLY guarantee
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_route_is_read_only_405_on_writes_and_store_sees_only_get():
    store = FakeWorkspaceStore({"A": _row()})
    client = _client(AgentsDeps(workspace_store=store))
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/agents", headers=H).status_code == 405
    client.get("/agents", headers=H)
    # Every recorded call is a get() — there is no write path through this route at all.
    assert {c[0] for c in store.calls} == {"get"}
