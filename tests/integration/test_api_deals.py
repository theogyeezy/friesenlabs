"""Integration: /deals endpoints — the real Pipeline board (list/detail/move-stage).

Proves the api half of the pipeline vertical slice:
  * 401 unauth on all three routes (the shared current_tenant dependency)
  * tenant ALWAYS from the verified claims — a smuggled tenant (query/body) is ignored
  * GET /deals groups into ordered stage columns with the joined company name
  * GET /deals/{id} returns the deal + its recent activities; cross-tenant/missing/bad ids 404
  * POST /deals/{id}/move-stage NEVER writes the deal: it lands EXACTLY ONE Greenlight
    proposal through the existing ActionGate (spy store), the executor is never invoked
    under the L1 default, and the response is {queued: true, approval_id} — honest about
    the deal staying in its current stage
  * kill switch -> 409 blocked, nothing queued; same-stage -> 409; empty to_stage -> 422
  * unconfigured deps (no DSN -> no reader) answer honest 503s on all three routes
  * the default ApiDeps mounts the routes with the honest stub (never 404 / fake success)
"""
import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig, Thresholds
from api.control.greenlight import Greenlight, InMemoryApprovalStore
from api.control.killswitch import KillSwitch
from api.control.types import Level
from api.deals_routes import STAGE_ORDER, DealsDeps
from api.views import SavedViews

H = {"Authorization": "Bearer t"}

DEAL_A1 = "11111111-1111-1111-1111-111111111111"
DEAL_A2 = "22222222-2222-2222-2222-222222222222"
DEAL_B1 = "99999999-9999-9999-9999-999999999999"


class FakeVerifier:
    def verify(self, token):
        return {"sub": "uA", "custom:tenant_id": "A", "email": "a@x.com"}


class FakeDealsReader:
    """In-memory PgCrmClient-shaped reader. Rows are keyed by tenant — the fake honors the
    RLS contract (a read for tenant A can never surface tenant B's rows) and records every
    call so tests can assert the claims tenant steered each one. It deliberately has NO
    write/update method: any attempted deal mutation would AttributeError loudly."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.rows = {
            "A": [
                {"id": DEAL_A1, "tenant_id": "A", "title": "Birchwood platform expansion",
                 "stage": "negotiation", "amount": 84000.0, "currency": "USD",
                 "company_id": "c-1", "contact_id": "p-1",
                 "company_name": "Birchwood Capital", "created_at": "2026-06-01T00:00:00+00:00"},
                {"id": DEAL_A2, "tenant_id": "A", "title": "Mesa Verde pilot",
                 "stage": "new", "amount": 9500.0, "currency": "USD",
                 "company_id": "c-2", "contact_id": "p-2",
                 "company_name": "Mesa Verde Health", "created_at": "2026-06-02T00:00:00+00:00"},
            ],
            "B": [
                {"id": DEAL_B1, "tenant_id": "B", "title": "B-only secret deal",
                 "stage": "proposal", "amount": 555000.0, "currency": "USD",
                 "company_id": "c-9", "contact_id": "p-9",
                 "company_name": "Tenant B Corp", "created_at": "2026-06-03T00:00:00+00:00"},
            ],
        }
        self.activities = {
            (
                "A", DEAL_A1): [
                {"id": "act-1", "kind": "call", "body": "Walked Dana through the security review.",
                 "occurred_at": "2026-06-05T00:00:00+00:00"},
                {"id": "act-2", "kind": "email", "body": "Sent the revised order form.",
                 "occurred_at": "2026-06-04T00:00:00+00:00"},
            ],
        }

    def list_deals_board(self, *, tenant_id, limit=500):
        self.calls.append(("list", tenant_id))
        return [dict(r) for r in self.rows.get(tenant_id, [])]

    def get_deal_board(self, *, tenant_id, deal_id):
        self.calls.append(("get", tenant_id, deal_id))
        for r in self.rows.get(tenant_id, []):
            if r["id"] == deal_id:
                return {**r, "contact_name": "Dana Whitfield", "contact_email": "dana@x.com"}
        return None

    def list_deal_activities(self, *, tenant_id, deal_id, limit=20):
        self.calls.append(("activities", tenant_id, deal_id))
        return [dict(a) for a in self.activities.get((tenant_id, deal_id), [])]


class SpyApprovalStore(InMemoryApprovalStore):
    """The real in-memory store + an insert counter, so 'exactly one proposal' is provable."""

    def __init__(self):
        super().__init__()
        self.inserts: list[dict] = []

    def insert(self, row):
        self.inserts.append(dict(row))
        return super().insert(row)


def _client(deals=None, *, level=Level.L1, killswitch=None):
    executed: list = []
    spy_store = SpyApprovalStore()
    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=Greenlight(store=spy_store),
        saved_views=SavedViews(), conversation_factory=lambda t: None,
        autonomy_config=AutonomyConfig(default_level=level,
                                       thresholds=Thresholds(max_auto_value=1000)),
        executor=lambda a: executed.append(a) or {"ran": True},
        killswitch=killswitch or KillSwitch(),
        deals=deals if deals is not None else DealsDeps(),
    )
    return TestClient(create_app(deps)), spy_store, executed


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_unauth_401_on_all_three_routes():
    client, _, _ = _client(DealsDeps(crm=FakeDealsReader()))
    assert client.get("/deals").status_code == 401
    assert client.get(f"/deals/{DEAL_A1}").status_code == 401
    assert client.post(f"/deals/{DEAL_A1}/move-stage",
                       json={"to_stage": "proposal"}).status_code == 401


# --------------------------------------------------------------------------- #
# honest unconfigured stubs (no DSN -> no reader)
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_unconfigured_503_on_all_three_routes_never_fake_rows():
    client, store, executed = _client(DealsDeps(crm=None))
    for r in (client.get("/deals", headers=H),
              client.get(f"/deals/{DEAL_A1}", headers=H),
              client.post(f"/deals/{DEAL_A1}/move-stage",
                          json={"to_stage": "proposal"}, headers=H)):
        assert r.status_code == 503
        assert "not configured" in r.json()["detail"]
    assert store.inserts == [] and executed == []


@pytest.mark.integration
def test_default_apideps_mounts_routes_with_honest_stub():
    # ApiDeps without an explicit `deals` builds the INERT default stub — the routes must
    # mount and answer the honest 503 (not a 404, not invented rows), and constructing the
    # deps must never open a DB pool regardless of what env happens to be set (CI carries
    # UPLIFT_DB_URL for the RLS proofs; the real reader is wired ONLY by api/asgi.py).
    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
        executor=lambda a: None,
    )
    client = TestClient(create_app(deps))
    assert client.get("/deals", headers=H).status_code == 503


# --------------------------------------------------------------------------- #
# GET /deals — claims-bound board
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_list_groups_stages_in_order_with_company_names():
    reader = FakeDealsReader()
    client, _, _ = _client(DealsDeps(crm=reader))
    r = client.get("/deals", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert body["stage_order"] == list(STAGE_ORDER)
    stages = body["stages"]
    # Canonical column spine, in order, even when some stages are empty.
    assert [s["stage"] for s in stages][:6] == list(STAGE_ORDER)
    by = {s["stage"]: s for s in stages}
    assert by["new"]["count"] == 1
    assert by["new"]["deals"][0]["title"] == "Mesa Verde pilot"
    assert by["new"]["deals"][0]["company_name"] == "Mesa Verde Health"
    assert by["negotiation"]["count"] == 1
    assert by["negotiation"]["total_amount"] == 84000.0
    assert by["proposal"]["count"] == 0 and by["proposal"]["deals"] == []
    # The read was steered by the CLAIMS tenant.
    assert ("list", "A") in reader.calls


@pytest.mark.integration
def test_list_never_leaks_other_tenants_rows_and_ignores_smuggled_tenant():
    reader = FakeDealsReader()
    client, _, _ = _client(DealsDeps(crm=reader))
    # A smuggled query tenant must not steer the read (the route takes no such param).
    r = client.get("/deals?tenant_id=B&tenant=B", headers=H)
    assert r.status_code == 200
    assert "B-only secret deal" not in r.text
    assert all(c[1] == "A" for c in reader.calls)
    # Internal tenant_id never leaves the API on board rows.
    assert '"tenant_id"' not in r.text


@pytest.mark.integration
def test_list_unknown_stage_grouped_into_appended_column_never_dropped():
    reader = FakeDealsReader()
    reader.rows["A"].append(
        {"id": "33333333-3333-3333-3333-333333333333", "tenant_id": "A",
         "title": "Legacy import", "stage": "zz_custom", "amount": None, "currency": "USD",
         "company_id": None, "contact_id": None, "company_name": None,
         "created_at": "2026-06-03T00:00:00+00:00"})
    client, _, _ = _client(DealsDeps(crm=reader))
    body = client.get("/deals", headers=H).json()
    assert body["total"] == 3
    stages = [s["stage"] for s in body["stages"]]
    assert stages == list(STAGE_ORDER) + ["zz_custom"]  # appended after the canonical spine
    custom = body["stages"][-1]
    assert custom["count"] == 1 and custom["total_amount"] == 0


@pytest.mark.integration
def test_list_tenant_mismatch_fails_loud_500():
    class LeakyReader(FakeDealsReader):
        def list_deals_board(self, *, tenant_id, limit=500):
            return [dict(r) for r in self.rows["B"]]  # simulated RLS failure

    client, _, _ = _client(DealsDeps(crm=LeakyReader()))
    r = client.get("/deals", headers=H)
    assert r.status_code == 500
    assert "isolation" in r.json()["detail"]
    assert "B-only secret deal" not in r.text


# --------------------------------------------------------------------------- #
# GET /deals/{id} — detail + activities
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_detail_returns_deal_and_recent_activities():
    reader = FakeDealsReader()
    client, _, _ = _client(DealsDeps(crm=reader))
    r = client.get(f"/deals/{DEAL_A1}", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["deal"]["title"] == "Birchwood platform expansion"
    assert body["deal"]["company_name"] == "Birchwood Capital"
    assert body["deal"]["contact_name"] == "Dana Whitfield"
    assert "tenant_id" not in body["deal"]
    kinds = [a["kind"] for a in body["activities"]]
    assert kinds == ["call", "email"]
    assert ("get", "A", DEAL_A1) in reader.calls
    assert ("activities", "A", DEAL_A1) in reader.calls


@pytest.mark.integration
def test_detail_cross_tenant_missing_and_malformed_ids_404():
    client, _, _ = _client(DealsDeps(crm=FakeDealsReader()))
    # Another tenant's deal id: RLS-shaped read yields nothing -> 404 (indistinguishable).
    assert client.get(f"/deals/{DEAL_B1}", headers=H).status_code == 404
    # Unknown uuid -> 404.
    assert client.get("/deals/00000000-0000-0000-0000-000000000000",
                      headers=H).status_code == 404
    # Malformed id -> 404, never a 500 (and never reaches the reader's SQL).
    assert client.get("/deals/not-a-uuid", headers=H).status_code == 404
    assert client.get("/deals/deals;DROP TABLE deals", headers=H).status_code == 404


# --------------------------------------------------------------------------- #
# POST /deals/{id}/move-stage — the Greenlight-gated draft path
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_move_stage_lands_exactly_one_proposal_and_never_touches_deals():
    reader = FakeDealsReader()
    client, store, executed = _client(DealsDeps(crm=reader))
    r = client.post(f"/deals/{DEAL_A1}/move-stage",
                    json={"to_stage": "closed_won"}, headers=H)
    assert r.status_code == 200
    body = r.json()
    # Honest queued response: never claims the move happened.
    assert body["queued"] is True
    assert body["status"] == "pending_approval"
    assert body["from_stage"] == "negotiation" and body["to_stage"] == "closed_won"
    assert "until a human approves" in body["detail"]

    # EXACTLY ONE Greenlight proposal, tenant-stamped from the verified claim.
    assert len(store.inserts) == 1
    rec = store.inserts[0]
    assert rec["tenant_id"] == "A"
    assert rec["status"] == "pending"
    assert rec["proposed_action"]["action"] == "update_deal"
    assert rec["proposed_action"]["deal_id"] == DEAL_A1
    assert rec["proposed_action"]["changes"] == {"stage": "closed_won"}
    assert rec["value_at_stake"] == 84000.0
    # approval_id points at the stored record.
    assert store.get("A", body["approval_id"]) is not None

    # The executor was NEVER invoked (L1 + ALWAYS_ASK -> propose, not execute) and the
    # reader exposes no mutator at all: the deal row is untouched by construction.
    assert executed == []
    assert reader.rows["A"][0]["stage"] == "negotiation"
    assert not any(c[0] not in ("list", "get", "activities") for c in reader.calls)

    # The proposal is visible in THIS app's Greenlight queue (one shared queue).
    pending = client.get("/approvals", headers=H).json()["approvals"]
    assert len(pending) == 1 and pending[0]["proposed_action"]["action"] == "update_deal"


@pytest.mark.integration
def test_move_stage_smuggled_tenant_ignored():
    reader = FakeDealsReader()
    client, store, _ = _client(DealsDeps(crm=reader))
    r = client.post(f"/deals/{DEAL_A1}/move-stage",
                    json={"to_stage": "proposal", "tenant_id": "B", "tenant": "B"}, headers=H)
    assert r.status_code == 200
    assert len(store.inserts) == 1
    assert store.inserts[0]["tenant_id"] == "A"  # claims tenant, always


@pytest.mark.integration
def test_move_stage_cross_tenant_deal_404_nothing_queued():
    client, store, executed = _client(DealsDeps(crm=FakeDealsReader()))
    r = client.post(f"/deals/{DEAL_B1}/move-stage",
                    json={"to_stage": "closed_won"}, headers=H)
    assert r.status_code == 404
    assert store.inserts == [] and executed == []


@pytest.mark.integration
def test_move_stage_same_stage_409_nothing_queued():
    client, store, _ = _client(DealsDeps(crm=FakeDealsReader()))
    r = client.post(f"/deals/{DEAL_A1}/move-stage",
                    json={"to_stage": "negotiation"}, headers=H)
    assert r.status_code == 409
    assert "already in stage" in r.json()["detail"]
    assert store.inserts == []


@pytest.mark.integration
def test_move_stage_empty_to_stage_422():
    client, store, _ = _client(DealsDeps(crm=FakeDealsReader()))
    for bad in ("", "   "):
        r = client.post(f"/deals/{DEAL_A1}/move-stage",
                        json={"to_stage": bad}, headers=H)
        assert r.status_code == 422
    assert store.inserts == []


@pytest.mark.integration
def test_move_stage_killswitch_blocks_409_nothing_queued():
    ks = KillSwitch()
    ks.pause_tenant("A")
    client, store, executed = _client(DealsDeps(crm=FakeDealsReader()), killswitch=ks)
    r = client.post(f"/deals/{DEAL_A1}/move-stage",
                    json={"to_stage": "proposal"}, headers=H)
    assert r.status_code == 409
    assert "kill switch" in r.json()["detail"]
    assert store.inserts == [] and executed == []


@pytest.mark.integration
def test_move_stage_under_l3_autonomy_still_only_proposes_draft_gate_stands():
    """Even with autonomy raised to L3 (gate decides AUTO -> executor runs), update_deal is
    ALWAYS_ASK: the real executor's Tool.invoke only PROPOSES to Greenlight. The route must
    surface that as queued — never claim the move executed."""
    from agents.tools.base import ToolContext
    from agents.tools.registry import resolve

    reader = FakeDealsReader()
    spy_store = SpyApprovalStore()
    greenlight = Greenlight(store=spy_store)

    def real_style_executor(action):
        tool = resolve(action.name)
        ctx = ToolContext(tenant_id=action.tenant_id, agent=action.agent,
                          greenlight=greenlight)
        return tool.invoke(ctx, **{k: v for k, v in (action.payload or {}).items()
                                   if k in ("deal_id", "changes")})

    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=greenlight, saved_views=SavedViews(),
        conversation_factory=lambda t: None,
        autonomy_config=AutonomyConfig(default_level=Level.L3),
        executor=real_style_executor,
        deals=DealsDeps(crm=reader),
    )
    client = TestClient(create_app(deps))
    r = client.post(f"/deals/{DEAL_A1}/move-stage",
                    json={"to_stage": "proposal"}, headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["queued"] is True and body["status"] == "pending_approval"
    # Still exactly ONE proposal (the tool's own Greenlight routing), deal untouched.
    assert len(spy_store.inserts) == 1
    assert spy_store.inserts[0]["proposed_action"]["action"] == "update_deal"
    assert reader.rows["A"][0]["stage"] == "negotiation"
