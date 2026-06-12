"""Integration: /contacts/duplicates + /contacts/merge + /companies/duplicates + /companies/merge.

Dedupe/merge surface (CRM-depth #16). Proves the api half:
  * 401 unauth on every route
  * the literal /…/duplicates + /…/merge paths win over /…/{id} (route ordering)
  * GET duplicates returns clusters scoped to the claim tenant; tenant_id never leaks
  * POST merge passes winner/loser ids (THE TRUST RULE: no tenant_id in the body) and the
    claim tenant steers the call; same-id is 422; a missing/cross-tenant row is 404
  * unconfigured deps answer honest 503s
"""
import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.contacts_routes import ContactsDeps
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.views import SavedViews

H = {"Authorization": "Bearer t"}

C_WIN = "11111111-1111-1111-1111-111111111111"
C_LOSE = "22222222-2222-2222-2222-222222222222"
CO_WIN = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CO_LOSE = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
MISSING = "ffffffff-ffff-ffff-ffff-ffffffffffff"


class FakeVerifier:
    def verify(self, token):
        return {"sub": "uA", "custom:tenant_id": "A", "email": "a@x.com"}


class FakeMergeCrm:
    """In-memory PgCrmClient-shaped client for the dedupe/merge surface. Honors the RLS contract
    (a read for tenant A never surfaces tenant B's rows) and records every call so tests can
    assert the claim tenant steered it. Visible ids per tenant gate the merge 404s."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.contact_ids = {"A": {C_WIN, C_LOSE}, "B": set()}
        self.company_ids = {"A": {CO_WIN, CO_LOSE}, "B": set()}

    def find_duplicate_contacts(self, *, tenant_id, limit=50):
        self.calls.append(("dup_contacts", tenant_id))
        if tenant_id != "A":
            return []
        return [{
            "key": "email:dana@x.com", "reason": "email",
            "members": [
                {"id": C_WIN, "tenant_id": "A", "name": "Dana W", "email": "dana@x.com"},
                {"id": C_LOSE, "tenant_id": "A", "name": "Dana Whitfield", "email": "dana@x.com"},
            ],
        }]

    def find_duplicate_companies(self, *, tenant_id, limit=50):
        self.calls.append(("dup_companies", tenant_id))
        if tenant_id != "A":
            return []
        return [{
            "key": "domain:birch.example", "reason": "domain",
            "members": [
                {"id": CO_WIN, "tenant_id": "A", "name": "Birchwood", "domain": "birch.example"},
                {"id": CO_LOSE, "tenant_id": "A", "name": "Birchwood Capital", "domain": "birch.example"},
            ],
        }]

    def merge_contacts(self, *, tenant_id, winner_id, loser_id):
        self.calls.append(("merge_contacts", tenant_id, winner_id, loser_id))
        ids = self.contact_ids.get(tenant_id, set())
        if winner_id not in ids or loser_id not in ids:
            raise ValueError("contact not found or not visible")
        return {
            "winner": {"id": winner_id, "tenant_id": tenant_id, "name": "Dana W", "email": "dana@x.com"},
            "loser_id": loser_id,
            "repointed": {"deals": 1, "activities": 2, "tasks": 0},
        }

    def merge_companies(self, *, tenant_id, winner_id, loser_id):
        self.calls.append(("merge_companies", tenant_id, winner_id, loser_id))
        ids = self.company_ids.get(tenant_id, set())
        if winner_id not in ids or loser_id not in ids:
            raise ValueError("company not found or not visible")
        return {
            "winner": {"id": winner_id, "tenant_id": tenant_id, "name": "Birchwood", "domain": "birch.example"},
            "loser_id": loser_id,
            "repointed": {"contacts": 3, "deals": 2},
        }


def _client(contacts=None):
    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
        executor=lambda a: None,
        contacts=contacts if contacts is not None else ContactsDeps(),
    )
    return TestClient(create_app(deps))


# --- auth -------------------------------------------------------------------
@pytest.mark.integration
def test_unauth_401_on_all_merge_routes():
    client = _client(ContactsDeps(crm=FakeMergeCrm()))
    assert client.get("/contacts/duplicates").status_code == 401
    assert client.post("/contacts/merge", json={"winner_id": C_WIN, "loser_id": C_LOSE}).status_code == 401
    assert client.get("/companies/duplicates").status_code == 401
    assert client.post("/companies/merge", json={"winner_id": CO_WIN, "loser_id": CO_LOSE}).status_code == 401


# --- unconfigured 503 -------------------------------------------------------
@pytest.mark.integration
def test_unconfigured_503():
    client = _client(ContactsDeps(crm=None))
    for r in (client.get("/contacts/duplicates", headers=H),
              client.post("/contacts/merge", json={"winner_id": C_WIN, "loser_id": C_LOSE}, headers=H),
              client.get("/companies/duplicates", headers=H),
              client.post("/companies/merge", json={"winner_id": CO_WIN, "loser_id": CO_LOSE}, headers=H)):
        assert r.status_code == 503


# --- duplicates -------------------------------------------------------------
@pytest.mark.integration
def test_contact_duplicates_clusters_scoped_to_claim_and_strip_tenant_id():
    crm = FakeMergeCrm()
    client = _client(ContactsDeps(crm=crm))
    r = client.get("/contacts/duplicates", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    cluster = body["clusters"][0]
    assert cluster["reason"] == "email"
    assert {m["id"] for m in cluster["members"]} == {C_WIN, C_LOSE}
    for m in cluster["members"]:
        assert "tenant_id" not in m  # stripped
    assert ("dup_contacts", "A") in crm.calls  # claim tenant, not the request


@pytest.mark.integration
def test_company_duplicates_clusters():
    client = _client(ContactsDeps(crm=FakeMergeCrm()))
    r = client.get("/companies/duplicates", headers=H)
    assert r.status_code == 200
    assert r.json()["clusters"][0]["reason"] == "domain"


@pytest.mark.integration
def test_duplicates_literal_path_wins_over_id_route():
    # /contacts/duplicates must NOT be treated as /contacts/{id="duplicates"} (404).
    client = _client(ContactsDeps(crm=FakeMergeCrm()))
    assert client.get("/contacts/duplicates", headers=H).status_code == 200
    assert client.get("/companies/duplicates", headers=H).status_code == 200


# --- merge ------------------------------------------------------------------
@pytest.mark.integration
def test_merge_contacts_uses_claim_tenant_ignores_body_tenant():
    crm = FakeMergeCrm()
    client = _client(ContactsDeps(crm=crm))
    r = client.post("/contacts/merge",
                    json={"winner_id": C_WIN, "loser_id": C_LOSE, "tenant_id": "B"}, headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["loser_id"] == C_LOSE
    assert body["repointed"] == {"deals": 1, "activities": 2, "tasks": 0}
    assert "tenant_id" not in body["winner"]  # stripped
    merge = [c for c in crm.calls if c[0] == "merge_contacts"][0]
    assert merge[1] == "A"  # claim tenant, NOT the smuggled "B"


@pytest.mark.integration
def test_merge_contacts_same_id_422():
    client = _client(ContactsDeps(crm=FakeMergeCrm()))
    r = client.post("/contacts/merge", json={"winner_id": C_WIN, "loser_id": C_WIN}, headers=H)
    assert r.status_code == 422


@pytest.mark.integration
def test_merge_contacts_missing_row_404():
    client = _client(ContactsDeps(crm=FakeMergeCrm()))
    r = client.post("/contacts/merge", json={"winner_id": C_WIN, "loser_id": MISSING}, headers=H)
    assert r.status_code == 404


@pytest.mark.integration
def test_merge_contacts_malformed_id_404():
    client = _client(ContactsDeps(crm=FakeMergeCrm()))
    r = client.post("/contacts/merge", json={"winner_id": "not-a-uuid", "loser_id": C_LOSE}, headers=H)
    assert r.status_code == 404


@pytest.mark.integration
def test_merge_companies_happy_path():
    crm = FakeMergeCrm()
    client = _client(ContactsDeps(crm=crm))
    r = client.post("/companies/merge", json={"winner_id": CO_WIN, "loser_id": CO_LOSE}, headers=H)
    assert r.status_code == 200
    assert r.json()["repointed"] == {"contacts": 3, "deals": 2}
    merge = [c for c in crm.calls if c[0] == "merge_companies"][0]
    assert merge[1] == "A"


@pytest.mark.integration
def test_merge_companies_same_id_422():
    client = _client(ContactsDeps(crm=FakeMergeCrm()))
    r = client.post("/companies/merge", json={"winner_id": CO_WIN, "loser_id": CO_WIN}, headers=H)
    assert r.status_code == 422
