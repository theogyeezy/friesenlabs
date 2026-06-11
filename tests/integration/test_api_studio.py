"""Integration: the /studio routes — Agent Studio's composer + playbook library api.

Proves the vertical slice offline (the test shapes mirror test_api_agents.py):
  * 401 unauth on every route (the shared current_tenant dependency)
  * tenant ALWAYS from the verified claims — playbooks are invisible across tenants
    (404, no existence oracle), and nothing in any body can smuggle a tenant
  * SPEC-NOT-CODE: an invalid definition is a 422 with the validator's reason — including
    the draft-only guard (greenlight.side_effects != 'always_ask') and tool escalation
  * CRUD: create -> list -> get -> update (version bump) -> delete; active rows refuse
    edit/delete with 409
  * the starter library: GET /studio/templates serves the 5 committed templates;
    instantiate copies one into the tenant's library (404 unknown id)
  * ACTIVATE registers through the EXISTING roster mechanism (FakeRuntime): owned specs with
    narrowed tools, one coordinator over exactly the playbook's agents, NOTHING executed
    (no sessions, no messages), side-effecting tools still ALWAYS_ASK per the trusted
    registry, and FULL Managed Agents ids never reach the body (tails only)
  * no registrar -> activate still flips status, honestly reporting registered: false
  * unconfigured deps (store=None) answer the honest 503
"""
import pytest
from fastapi.testclient import TestClient

from agents.playbooks.store import InMemoryPlaybookStore
from agents.runtime import FakeRuntime
from agents.tools.base import Policy
from agents.tools.registry import TOOL_REGISTRY
from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.routes_studio import StudioDeps
from api.views import SavedViews

H_A = {"Authorization": "Bearer tenant-a-token"}
H_B = {"Authorization": "Bearer tenant-b-token"}


class TwoTenantVerifier:
    """Maps the bearer token to a tenant — both tenants exercise the SAME app instance."""

    def verify(self, token):
        if token == "tenant-a-token":
            return {"sub": "userA", "custom:tenant_id": "A", "email": "a@x.com"}
        if token == "tenant-b-token":
            return {"sub": "userB", "custom:tenant_id": "B", "email": "b@x.com"}
        raise ValueError("bad token")


def good_definition(name="Studio test playbook"):
    return {
        "name": name,
        "trigger": {"kind": "manual"},
        "roster": [
            {"agent": "scout", "tools": ["search_rag", "read_crm"]},
            {"agent": "ledger", "tools": ["read_crm", "update_deal"]},
        ],
        "autonomy": "L1",
        "greenlight": {"side_effects": "always_ask"},
    }


def _client(studio=None):
    deps = ApiDeps(
        verifier=TwoTenantVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
        executor=lambda a: None,
        studio=studio if studio is not None else StudioDeps(),
    )
    return TestClient(create_app(deps))


def _wired(registrar=None):
    store = InMemoryPlaybookStore()
    deps = StudioDeps(store=store, registrar=registrar)
    return _client(deps), store


# --------------------------------------------------------------------------- #
# auth + honest unconfigured stub
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_unauth_401_everywhere():
    client, _ = _wired()
    assert client.get("/studio/playbooks").status_code == 401
    assert client.get("/studio/templates").status_code == 401
    assert client.post("/studio/playbooks", json={"definition": good_definition()}).status_code == 401
    assert client.post("/studio/templates/stale-deal-nudger/instantiate").status_code == 401


@pytest.mark.integration
def test_unconfigured_store_503_but_templates_serve():
    client = _client(StudioDeps(store=None))
    r = client.get("/studio/playbooks", headers=H_A)
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"]
    assert client.post("/studio/playbooks", headers=H_A,
                       json={"definition": good_definition()}).status_code == 503
    # The starter library is committed JSON — it needs no store.
    r = client.get("/studio/templates", headers=H_A)
    assert r.status_code == 200
    assert len(r.json()["templates"]) == 5


@pytest.mark.integration
def test_default_api_deps_mount_the_routes(monkeypatch):
    """A bare ApiDeps (every test, any non-asgi constructor) mounts /studio with the honest
    env-built default — with no DSN in the env that is the 503 stub, never a 404. The DSN env
    is cleared explicitly so the assertion holds identically on CI (which exports a real
    UPLIFT_DB_URL for the Postgres-backed tests) and offline."""
    for var in ("UPLIFT_DB_URL", "DB_USER", "DB_PASS", "DB_HOST", "DB_NAME"):
        monkeypatch.delenv(var, raising=False)
    deps = ApiDeps(
        verifier=TwoTenantVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
        executor=lambda a: None,
    )
    client = TestClient(create_app(deps))
    assert client.get("/studio/playbooks", headers=H_A).status_code == 503
    assert client.get("/studio/templates", headers=H_A).status_code == 200


# --------------------------------------------------------------------------- #
# CRUD + validation
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_crud_roundtrip():
    client, _ = _wired()
    r = client.post("/studio/playbooks", headers=H_A, json={"definition": good_definition()})
    assert r.status_code == 201
    row = r.json()
    pid = row["id"]
    assert row["version"] == 1 and row["status"] == "draft"
    assert row["created_by"] == "userA"
    assert "tenant_id" not in row  # internal ids never leave the API

    assert [p["id"] for p in client.get("/studio/playbooks", headers=H_A).json()["playbooks"]] == [pid]
    assert client.get(f"/studio/playbooks/{pid}", headers=H_A).json()["name"] == "Studio test playbook"

    r = client.put(f"/studio/playbooks/{pid}", headers=H_A,
                   json={"definition": good_definition("Renamed")})
    assert r.status_code == 200
    assert r.json()["version"] == 2 and r.json()["name"] == "Renamed"

    r = client.delete(f"/studio/playbooks/{pid}", headers=H_A)
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert client.get(f"/studio/playbooks/{pid}", headers=H_A).status_code == 404


@pytest.mark.integration
def test_invalid_definition_is_422_never_a_row():
    client, store = _wired()
    bad = good_definition()
    bad["greenlight"]["side_effects"] = "auto"  # the draft-only guard
    r = client.post("/studio/playbooks", headers=H_A, json={"definition": bad})
    assert r.status_code == 422
    assert store.rows == {}, "an invalid definition must never persist"

    escalation = good_definition()
    escalation["roster"][0]["tools"] = ["send_email"]  # not in scout's owned grant
    r = client.post("/studio/playbooks", headers=H_A, json={"definition": escalation})
    assert r.status_code == 422
    assert "never widen" in r.json()["detail"]


@pytest.mark.integration
def test_update_validates_too():
    client, _ = _wired()
    pid = client.post("/studio/playbooks", headers=H_A,
                      json={"definition": good_definition()}).json()["id"]
    bad = good_definition()
    del bad["trigger"]
    r = client.put(f"/studio/playbooks/{pid}", headers=H_A, json={"definition": bad})
    assert r.status_code == 422
    # untouched on disk
    assert client.get(f"/studio/playbooks/{pid}", headers=H_A).json()["version"] == 1


@pytest.mark.integration
def test_malformed_id_is_404_not_500():
    client, _ = _wired()
    assert client.get("/studio/playbooks/not-a-uuid", headers=H_A).status_code == 404


# --------------------------------------------------------------------------- #
# tenant isolation (the trust rule end-to-end)
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_playbooks_are_invisible_across_tenants():
    client, _ = _wired()
    pid = client.post("/studio/playbooks", headers=H_A,
                      json={"definition": good_definition()}).json()["id"]

    # B sees an empty library and A's playbook is indistinguishable from absent.
    assert client.get("/studio/playbooks", headers=H_B).json()["playbooks"] == []
    assert client.get(f"/studio/playbooks/{pid}", headers=H_B).status_code == 404
    assert client.put(f"/studio/playbooks/{pid}", headers=H_B,
                      json={"definition": good_definition("stolen")}).status_code == 404
    assert client.delete(f"/studio/playbooks/{pid}", headers=H_B).status_code == 404
    assert client.post(f"/studio/playbooks/{pid}/activate", headers=H_B).status_code == 404

    # A's row is untouched by all of B's attempts.
    mine = client.get(f"/studio/playbooks/{pid}", headers=H_A).json()
    assert mine["name"] == "Studio test playbook" and mine["status"] == "draft"


@pytest.mark.integration
def test_smuggled_tenant_in_body_is_ignored():
    client, store = _wired()
    r = client.post("/studio/playbooks", headers=H_A,
                    json={"definition": good_definition(), "tenant_id": "B"})
    assert r.status_code == 201
    stored = store.rows[r.json()["id"]]
    assert stored["tenant_id"] == "A", "tenant must come from the verified claim ONLY"


# --------------------------------------------------------------------------- #
# starter library
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_templates_list_and_instantiate():
    client, _ = _wired()
    templates = client.get("/studio/templates", headers=H_A).json()["templates"]
    assert {t["template_id"] for t in templates} == {
        "lead-followup-drafter", "pipeline-hygiene-scout", "weekly-summary-reporter",
        "stale-deal-nudger", "data-quality-auditor",
    }

    r = client.post("/studio/templates/lead-followup-drafter/instantiate", headers=H_A)
    assert r.status_code == 201
    row = r.json()
    assert row["template_id"] == "lead-followup-drafter"
    assert row["status"] == "draft"
    assert row["definition"]["name"] == "Lead follow-up drafter"
    # It landed in THIS tenant's library only.
    assert len(client.get("/studio/playbooks", headers=H_A).json()["playbooks"]) == 1
    assert client.get("/studio/playbooks", headers=H_B).json()["playbooks"] == []


@pytest.mark.integration
def test_instantiate_unknown_template_404():
    client, _ = _wired()
    assert client.post("/studio/templates/nope/instantiate", headers=H_A).status_code == 404


# --------------------------------------------------------------------------- #
# activation — the existing roster mechanism, behind the existing gates
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_activate_registers_with_fake_runtime():
    runtime = FakeRuntime()
    client, _ = _wired(registrar=runtime)
    pid = client.post("/studio/playbooks", headers=H_A,
                      json={"definition": good_definition()}).json()["id"]

    r = client.post(f"/studio/playbooks/{pid}/activate", headers=H_A)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "active" and body["registered"] is True
    assert body["registration"]["agents"] == ["scout", "ledger"]

    # The EXISTING mechanism: owned AgentSpecs with narrowed tools + one coordinator
    # over exactly the playbook's agents.
    specs = {s.name: s for s in runtime.agents.values() if not s.name.startswith("playbook-")}
    assert set(specs) == {"scout", "ledger"}
    assert specs["scout"].tools == ["search_rag", "read_crm"]  # narrowed from the owned 5
    assert specs["ledger"].tools == ["read_crm", "update_deal"]
    (coordinator_roster,) = runtime.coordinators.values()
    assert len(coordinator_roster) == 2

    # NOTHING executed: registration opens no session and sends no message.
    assert runtime.sessions == {} and runtime.sent == []

    # DRAFT-ONLY held: the side-effecting tool registered for ledger is still ALWAYS_ASK
    # per the trusted registry — Greenlight gates it at execution time, not the playbook.
    assert TOOL_REGISTRY["update_deal"].policy is Policy.ALWAYS_ASK

    # Full Managed Agents ids never reach the body — tails only.
    flat = str(body)
    for full_id in list(runtime.agents) + list(runtime.coordinators):
        assert full_id not in flat
    assert body["registration"]["coordinator_id_tail"] is not None


@pytest.mark.integration
def test_activate_without_registrar_is_honest_record_only():
    client, _ = _wired(registrar=None)
    pid = client.post("/studio/playbooks", headers=H_A,
                      json={"definition": good_definition()}).json()["id"]
    r = client.post(f"/studio/playbooks/{pid}/activate", headers=H_A)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "active"
    assert body["registered"] is False
    assert "not configured" in body["registration_reason"]


@pytest.mark.integration
def test_active_playbook_refuses_edit_and_delete():
    client, _ = _wired()
    pid = client.post("/studio/playbooks", headers=H_A,
                      json={"definition": good_definition()}).json()["id"]
    client.post(f"/studio/playbooks/{pid}/activate", headers=H_A)

    assert client.put(f"/studio/playbooks/{pid}", headers=H_A,
                      json={"definition": good_definition("nope")}).status_code == 409
    assert client.delete(f"/studio/playbooks/{pid}", headers=H_A).status_code == 409

    # Deactivate -> editable again.
    r = client.post(f"/studio/playbooks/{pid}/deactivate", headers=H_A)
    assert r.status_code == 200 and r.json()["status"] == "draft"
    assert client.put(f"/studio/playbooks/{pid}", headers=H_A,
                      json={"definition": good_definition("ok now")}).status_code == 200
    assert client.delete(f"/studio/playbooks/{pid}", headers=H_A).status_code == 200
