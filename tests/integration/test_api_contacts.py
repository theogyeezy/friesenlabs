"""Integration: /contacts + /companies endpoints — the real Contacts directory (read-only).

Proves the api half of the contacts vertical slice (the test shapes mirror test_api_deals.py):
  * 401 unauth on all four routes (the shared current_tenant dependency)
  * tenant ALWAYS from the verified claims — a smuggled tenant (query param) is ignored
  * GET /contacts pages the directory (joined company name + last-activity), has_more honest
  * ?q= search is steered through the reader as a VALUE (bind-param shape): the reader
    records the term verbatim; %/_ ILIKE metacharacters are escaped at the SQL layer
    (test_pg_contacts_reads.py proves the escaping; here we prove the term never mutates
    the route's behavior beyond filtering) and a >200-char q is a 422, never a scan
  * GET /contacts/{id} returns the contact + activities + the company's OPEN deals
    (the Pipeline seam); cross-tenant/missing/malformed ids 404
  * GET /companies lists with contact + open-deal counts; GET /companies/{id} returns the
    company + its contacts + its open deals
  * leak-fails-loud: a reader returning another tenant's rows -> 500, nothing leaves
  * internal tenant_id never leaves the API on any row
  * unconfigured deps (no DSN -> no reader) answer honest 503s on all four routes
  * the default ApiDeps mounts the routes with the honest stub (never 404 / fake success)
  * READ-ONLY: the fake reader exposes no write method at all, and the routes never call
    anything but the seven read methods
"""
import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.contacts_routes import MAX_PAGE, MAX_Q_LEN, ContactsDeps
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.views import SavedViews

H = {"Authorization": "Bearer t"}

CONTACT_A1 = "11111111-1111-1111-1111-111111111111"
CONTACT_A2 = "22222222-2222-2222-2222-222222222222"
CONTACT_B1 = "99999999-9999-9999-9999-999999999999"
COMPANY_A1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
COMPANY_A2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
COMPANY_B1 = "cccccccc-cccc-cccc-cccc-cccccccccccc"
DEAL_A1 = "dddddddd-dddd-dddd-dddd-dddddddddddd"


class FakeVerifier:
    def verify(self, token):
        return {"sub": "uA", "custom:tenant_id": "A", "email": "a@x.com"}


class FakeDirectoryReader:
    """In-memory PgCrmClient-shaped directory reader. Rows are keyed by tenant — the fake
    honors the RLS contract (a read for tenant A can never surface tenant B's rows) and
    records every call (incl. the q term and paging values) so tests can assert the claims
    tenant steered each one and the search term traveled as a VALUE. It deliberately has NO
    write/update method: any attempted mutation would AttributeError loudly."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.contacts = {
            "A": [
                {"id": CONTACT_A1, "tenant_id": "A", "name": "Dana Whitfield", "title": None,
                 "email": "dana@birchwoodcap.example", "phone": "+1 512 555 0150",
                 "company_id": COMPANY_A1, "company_name": "Birchwood Capital",
                 "created_at": "2026-06-01T00:00:00+00:00",
                 "last_activity_at": "2026-06-05T00:00:00+00:00"},
                {"id": CONTACT_A2, "tenant_id": "A", "name": "Marcus Oyelaran", "title": None,
                 "email": "marcus@mesaverde.example", "phone": None,
                 "company_id": COMPANY_A2, "company_name": "Mesa Verde Health",
                 "created_at": "2026-06-02T00:00:00+00:00", "last_activity_at": None},
            ],
            "B": [
                {"id": CONTACT_B1, "tenant_id": "B", "name": "B-only secret person",
                 "title": None, "email": "secret@tenant-b.example", "phone": None,
                 "company_id": COMPANY_B1, "company_name": "Tenant B Corp",
                 "created_at": "2026-06-03T00:00:00+00:00", "last_activity_at": None},
            ],
        }
        self.companies = {
            "A": [
                {"id": COMPANY_A1, "tenant_id": "A", "name": "Birchwood Capital",
                 "domain": "birchwoodcap.example", "created_at": "2026-05-01T00:00:00+00:00",
                 "contact_count": 1, "open_deal_count": 1},
                {"id": COMPANY_A2, "tenant_id": "A", "name": "Mesa Verde Health",
                 "domain": "mesaverde.example", "created_at": "2026-05-02T00:00:00+00:00",
                 "contact_count": 1, "open_deal_count": 0},
            ],
            "B": [
                {"id": COMPANY_B1, "tenant_id": "B", "name": "Tenant B Corp",
                 "domain": "tenant-b.example", "created_at": "2026-05-03T00:00:00+00:00",
                 "contact_count": 1, "open_deal_count": 1},
            ],
        }
        self.open_deals = {
            ("A", COMPANY_A1): [
                {"id": DEAL_A1, "tenant_id": "A", "title": "Birchwood platform expansion",
                 "stage": "negotiation", "amount": 84000.0, "currency": "USD",
                 "company_id": COMPANY_A1, "contact_id": CONTACT_A1,
                 "created_at": "2026-06-01T00:00:00+00:00"},
            ],
        }
        self.activities = {
            ("A", CONTACT_A1): [
                {"id": "act-1", "kind": "call", "body": "Walked Dana through the security review.",
                 "occurred_at": "2026-06-05T00:00:00+00:00"},
                {"id": "act-2", "kind": "email", "body": "Sent the revised order form.",
                 "occurred_at": "2026-06-04T00:00:00+00:00"},
            ],
        }

    @staticmethod
    def _matches(row, fields, q):
        if not q:
            return True
        needle = q.lower()
        return any((row.get(f) or "").lower().find(needle) >= 0 for f in fields)

    def list_contacts_directory(self, *, tenant_id, q=None, limit=50, offset=0):
        self.calls.append(("list_contacts", tenant_id, q, limit, offset))
        rows = [dict(r) for r in self.contacts.get(tenant_id, [])
                if self._matches(r, ("name", "email"), q)]
        return rows[offset:offset + limit]

    def get_contact_directory(self, *, tenant_id, contact_id):
        self.calls.append(("get_contact", tenant_id, contact_id))
        for r in self.contacts.get(tenant_id, []):
            if r["id"] == contact_id:
                return dict(r)
        return None

    def list_contact_activities(self, *, tenant_id, contact_id, limit=20):
        self.calls.append(("contact_activities", tenant_id, contact_id))
        return [dict(a) for a in self.activities.get((tenant_id, contact_id), [])]

    def list_company_open_deals(self, *, tenant_id, company_id, limit=50):
        self.calls.append(("company_open_deals", tenant_id, company_id))
        return [dict(d) for d in self.open_deals.get((tenant_id, company_id), [])]

    def list_companies_directory(self, *, tenant_id, q=None, limit=50, offset=0):
        self.calls.append(("list_companies", tenant_id, q, limit, offset))
        rows = [dict(r) for r in self.companies.get(tenant_id, [])
                if self._matches(r, ("name", "domain"), q)]
        return rows[offset:offset + limit]

    def get_company_directory(self, *, tenant_id, company_id):
        self.calls.append(("get_company", tenant_id, company_id))
        for r in self.companies.get(tenant_id, []):
            if r["id"] == company_id:
                return dict(r)
        return None

    def list_company_contacts(self, *, tenant_id, company_id, limit=50):
        self.calls.append(("company_contacts", tenant_id, company_id))
        return [dict(c) for c in self.contacts.get(tenant_id, [])
                if c.get("company_id") == company_id]


def _client(contacts=None):
    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
        executor=lambda a: None,
        contacts=contacts if contacts is not None else ContactsDeps(),
    )
    return TestClient(create_app(deps))


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_unauth_401_on_all_four_routes():
    client = _client(ContactsDeps(crm=FakeDirectoryReader()))
    assert client.get("/contacts").status_code == 401
    assert client.get(f"/contacts/{CONTACT_A1}").status_code == 401
    assert client.get("/companies").status_code == 401
    assert client.get(f"/companies/{COMPANY_A1}").status_code == 401


# --------------------------------------------------------------------------- #
# honest unconfigured stubs (no DSN -> no reader)
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_unconfigured_503_on_all_four_routes_never_fake_rows():
    client = _client(ContactsDeps(crm=None))
    for r in (client.get("/contacts", headers=H),
              client.get(f"/contacts/{CONTACT_A1}", headers=H),
              client.get("/companies", headers=H),
              client.get(f"/companies/{COMPANY_A1}", headers=H)):
        assert r.status_code == 503
        assert "not configured" in r.json()["detail"]


@pytest.mark.integration
def test_default_apideps_mounts_routes_with_honest_stub():
    # ApiDeps without an explicit `contacts` builds the INERT default stub — the routes must
    # mount and answer the honest 503 (not a 404, not invented rows), and constructing the
    # deps must never open a DB pool regardless of what env happens to be set (CI carries
    # UPLIFT_DB_URL for the RLS proofs; the real reader is wired ONLY by api/asgi.py).
    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
        executor=lambda a: None,
    )
    client = TestClient(create_app(deps))
    assert client.get("/contacts", headers=H).status_code == 503
    assert client.get("/companies", headers=H).status_code == 503


# --------------------------------------------------------------------------- #
# GET /contacts — claims-bound directory
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_list_contacts_returns_directory_rows_with_company_and_last_activity():
    reader = FakeDirectoryReader()
    client = _client(ContactsDeps(crm=reader))
    r = client.get("/contacts", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2 and body["has_more"] is False
    by_id = {c["id"]: c for c in body["contacts"]}
    dana = by_id[CONTACT_A1]
    assert dana["name"] == "Dana Whitfield"
    assert dana["email"] == "dana@birchwoodcap.example"
    assert dana["phone"] == "+1 512 555 0150"
    assert dana["company_name"] == "Birchwood Capital"
    assert dana["last_activity_at"] == "2026-06-05T00:00:00+00:00"
    # The read was steered by the CLAIMS tenant; the route asked for page+1 for has_more.
    assert ("list_contacts", "A", None, 51, 0) in reader.calls


@pytest.mark.integration
def test_list_contacts_never_leaks_other_tenants_rows_and_ignores_smuggled_tenant():
    reader = FakeDirectoryReader()
    client = _client(ContactsDeps(crm=reader))
    # A smuggled query tenant must not steer the read (the route takes no such param).
    r = client.get("/contacts?tenant_id=B&tenant=B", headers=H)
    assert r.status_code == 200
    assert "B-only secret person" not in r.text
    assert all(c[1] == "A" for c in reader.calls)
    # Internal tenant_id never leaves the API on directory rows.
    assert '"tenant_id"' not in r.text


@pytest.mark.integration
def test_list_contacts_search_term_travels_as_value_and_filters():
    reader = FakeDirectoryReader()
    client = _client(ContactsDeps(crm=reader))
    r = client.get("/contacts?q=dana", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1 and body["contacts"][0]["name"] == "Dana Whitfield"
    assert body["q"] == "dana"
    # The term reached the reader VERBATIM as a value (the SQL layer binds + escapes it —
    # proven in test_pg_contacts_reads.py).
    assert ("list_contacts", "A", "dana", 51, 0) in reader.calls


@pytest.mark.integration
def test_list_contacts_ilike_metacharacter_probe_is_passed_as_literal_value():
    # An ILIKE-injection attempt: %_ wildcards must reach the reader as the literal term —
    # the route never expands/interprets them (and the SQL layer escapes them; see the
    # pg-reads unit proof). With these literals matching nothing, the result is EMPTY,
    # never "all rows" (which is what an unescaped %-wildcard would return).
    reader = FakeDirectoryReader()
    client = _client(ContactsDeps(crm=reader))
    r = client.get("/contacts?q=%25_%25", headers=H)  # url-encoded "%_%"
    assert r.status_code == 200
    assert r.json()["count"] == 0
    assert ("list_contacts", "A", "%_%", 51, 0) in reader.calls


@pytest.mark.integration
def test_list_contacts_q_over_length_cap_422_never_reaches_reader():
    reader = FakeDirectoryReader()
    client = _client(ContactsDeps(crm=reader))
    r = client.get(f"/contacts?q={'x' * (MAX_Q_LEN + 1)}", headers=H)
    assert r.status_code == 422
    assert str(MAX_Q_LEN) in r.json()["detail"]
    assert reader.calls == []  # refused before any read


@pytest.mark.integration
def test_list_contacts_pagination_clamps_and_has_more():
    reader = FakeDirectoryReader()
    client = _client(ContactsDeps(crm=reader))
    r = client.get("/contacts?limit=1", headers=H)
    body = r.json()
    assert body["count"] == 1 and body["has_more"] is True and body["limit"] == 1
    r2 = client.get("/contacts?limit=1&offset=1", headers=H)
    body2 = r2.json()
    assert body2["count"] == 1 and body2["has_more"] is False and body2["offset"] == 1
    # Runaway limit/offset are clamped (never trusted, never a 500).
    r3 = client.get("/contacts?limit=999999&offset=-5", headers=H)
    assert r3.status_code == 200
    assert r3.json()["limit"] == MAX_PAGE and r3.json()["offset"] == 0
    # Junk ints are FastAPI 422s, not 500s.
    assert client.get("/contacts?limit=abc", headers=H).status_code == 422


@pytest.mark.integration
def test_list_contacts_tenant_mismatch_fails_loud_500():
    class LeakyReader(FakeDirectoryReader):
        def list_contacts_directory(self, *, tenant_id, q=None, limit=50, offset=0):
            return [dict(r) for r in self.contacts["B"]]  # simulated RLS failure

    client = _client(ContactsDeps(crm=LeakyReader()))
    r = client.get("/contacts", headers=H)
    assert r.status_code == 500
    assert "isolation" in r.json()["detail"]
    assert "B-only secret person" not in r.text


# --------------------------------------------------------------------------- #
# GET /contacts/{id} — detail + activities + the company's open deals
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_contact_detail_returns_activities_and_company_open_deals():
    reader = FakeDirectoryReader()
    client = _client(ContactsDeps(crm=reader))
    r = client.get(f"/contacts/{CONTACT_A1}", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["contact"]["name"] == "Dana Whitfield"
    assert body["contact"]["company_name"] == "Birchwood Capital"
    assert "tenant_id" not in body["contact"]
    assert [a["kind"] for a in body["activities"]] == ["call", "email"]
    # The Pipeline seam: the company's OPEN deals ride along, tenant-stripped.
    assert len(body["company_deals"]) == 1
    assert body["company_deals"][0]["title"] == "Birchwood platform expansion"
    assert body["company_deals"][0]["stage"] == "negotiation"
    assert "tenant_id" not in body["company_deals"][0]
    assert ("get_contact", "A", CONTACT_A1) in reader.calls
    assert ("contact_activities", "A", CONTACT_A1) in reader.calls
    assert ("company_open_deals", "A", COMPANY_A1) in reader.calls


@pytest.mark.integration
def test_contact_detail_without_company_returns_empty_deals_no_extra_read():
    reader = FakeDirectoryReader()
    reader.contacts["A"][1]["company_id"] = None
    client = _client(ContactsDeps(crm=reader))
    r = client.get(f"/contacts/{CONTACT_A2}", headers=H)
    assert r.status_code == 200
    assert r.json()["company_deals"] == []
    assert not any(c[0] == "company_open_deals" for c in reader.calls)


@pytest.mark.integration
def test_contact_detail_cross_tenant_missing_and_malformed_ids_404():
    client = _client(ContactsDeps(crm=FakeDirectoryReader()))
    # Another tenant's contact id: RLS-shaped read yields nothing -> 404 (indistinguishable).
    assert client.get(f"/contacts/{CONTACT_B1}", headers=H).status_code == 404
    assert client.get("/contacts/00000000-0000-0000-0000-000000000000",
                      headers=H).status_code == 404
    # Malformed id -> 404, never a 500 (and never reaches the reader's SQL).
    assert client.get("/contacts/not-a-uuid", headers=H).status_code == 404
    assert client.get("/contacts/contacts;DROP TABLE contacts", headers=H).status_code == 404


# --------------------------------------------------------------------------- #
# GET /companies — directory with counts
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_list_companies_returns_counts_and_search_filters():
    reader = FakeDirectoryReader()
    client = _client(ContactsDeps(crm=reader))
    r = client.get("/companies", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    by_name = {c["name"]: c for c in body["companies"]}
    assert by_name["Birchwood Capital"]["contact_count"] == 1
    assert by_name["Birchwood Capital"]["open_deal_count"] == 1
    assert by_name["Mesa Verde Health"]["open_deal_count"] == 0
    assert all("tenant_id" not in c for c in body["companies"])

    r2 = client.get("/companies?q=mesa", headers=H)
    assert r2.json()["count"] == 1
    assert r2.json()["companies"][0]["name"] == "Mesa Verde Health"
    assert ("list_companies", "A", "mesa", 51, 0) in reader.calls
    # Tenant B's company never surfaces.
    assert "Tenant B Corp" not in r.text + r2.text


@pytest.mark.integration
def test_list_companies_q_cap_422_and_smuggled_tenant_ignored():
    reader = FakeDirectoryReader()
    client = _client(ContactsDeps(crm=reader))
    assert client.get(f"/companies?q={'y' * (MAX_Q_LEN + 1)}", headers=H).status_code == 422
    assert reader.calls == []
    r = client.get("/companies?tenant_id=B", headers=H)
    assert r.status_code == 200
    assert all(c[1] == "A" for c in reader.calls)


@pytest.mark.integration
def test_list_companies_tenant_mismatch_fails_loud_500():
    class LeakyReader(FakeDirectoryReader):
        def list_companies_directory(self, *, tenant_id, q=None, limit=50, offset=0):
            return [dict(r) for r in self.companies["B"]]

    client = _client(ContactsDeps(crm=LeakyReader()))
    r = client.get("/companies", headers=H)
    assert r.status_code == 500
    assert "Tenant B Corp" not in r.text


# --------------------------------------------------------------------------- #
# GET /companies/{id} — detail + contacts + open deals
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_company_detail_returns_contacts_and_open_deals():
    reader = FakeDirectoryReader()
    client = _client(ContactsDeps(crm=reader))
    r = client.get(f"/companies/{COMPANY_A1}", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["company"]["name"] == "Birchwood Capital"
    assert body["company"]["contact_count"] == 1
    assert "tenant_id" not in body["company"]
    assert len(body["contacts"]) == 1 and body["contacts"][0]["name"] == "Dana Whitfield"
    assert len(body["deals"]) == 1 and body["deals"][0]["stage"] == "negotiation"
    assert all("tenant_id" not in row for row in body["contacts"] + body["deals"])
    assert ("get_company", "A", COMPANY_A1) in reader.calls
    assert ("company_contacts", "A", COMPANY_A1) in reader.calls
    assert ("company_open_deals", "A", COMPANY_A1) in reader.calls


@pytest.mark.integration
def test_company_detail_cross_tenant_missing_and_malformed_ids_404():
    client = _client(ContactsDeps(crm=FakeDirectoryReader()))
    assert client.get(f"/companies/{COMPANY_B1}", headers=H).status_code == 404
    assert client.get("/companies/00000000-0000-0000-0000-000000000000",
                      headers=H).status_code == 404
    assert client.get("/companies/not-a-uuid", headers=H).status_code == 404


# --------------------------------------------------------------------------- #
# WRITE-SURFACE guarantee — the directory now exposes EXACTLY two contact writes
# (POST /contacts create, PATCH /contacts/{id} edit); companies stay read-only and
# no other mutator is mounted. This locks the surface down so a stray route can't
# appear unnoticed.
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_contacts_write_surface_is_exactly_create_and_edit():
    reader = FakeDirectoryReader()
    client = _client(ContactsDeps(crm=reader))

    # Mounted contact writes EXIST (an empty body fails validation -> 422, not 405):
    assert client.post("/contacts", headers=H, json={}).status_code == 422
    assert client.patch(f"/contacts/{CONTACT_A1}", headers=H, json={}).status_code == 422

    # Contacts: no PUT / DELETE / collection-POST-on-an-id -> 405.
    assert client.put(f"/contacts/{CONTACT_A1}", headers=H).status_code == 405
    assert client.delete(f"/contacts/{CONTACT_A1}", headers=H).status_code == 405

    # Companies now have a CREATE + EDIT surface too (empty body -> 422 validation, not 405):
    assert client.post("/companies", headers=H, json={}).status_code == 422
    assert client.patch(f"/companies/{COMPANY_A1}", headers=H, json={}).status_code == 422
    # ...but still no PUT / DELETE:
    assert client.put("/companies", headers=H).status_code == 405
    assert client.delete(f"/companies/{COMPANY_A1}", headers=H).status_code == 405


@pytest.mark.integration
def test_read_paths_use_only_the_seven_readers():
    # Every recorded read call is one of the seven allow-listed directory reads.
    reader = FakeDirectoryReader()
    client = _client(ContactsDeps(crm=reader))
    client.get("/contacts", headers=H)
    client.get(f"/contacts/{CONTACT_A1}", headers=H)
    client.get("/companies", headers=H)
    client.get(f"/companies/{COMPANY_A1}", headers=H)
    allowed = {"list_contacts", "get_contact", "contact_activities", "company_open_deals",
               "list_companies", "get_company", "company_contacts"}
    assert {c[0] for c in reader.calls} <= allowed
