"""Unit: GET /account/export — GDPR/portability data-export egress (api/account_routes.py).

Mounts ``mount_account`` on a bare FastAPI app with a fake verifier and stubbed stores;
zero DB, zero AWS. Covers:

  * bundle shape: all sections present (contacts, companies, deals, saved_views, knowledge_docs)
  * tenant-from-claim ONLY: the tenant id in the export always equals the verified claim tenant
  * cross-tenant isolation: two tenants' exports never contain each other's rows
  * 401 unauth: missing or invalid bearer token -> 401
  * 503 unconfigured: all stores None -> 503, never 500
  * 503 does NOT fire when at least one store is configured (partial config is tolerated)
  * saved_views section always present when saved_views is wired
  * knowledge_docs omitted with a note when rag is None
  * contacts/companies/deals omitted with a note when crm is None
  * read-only egress: no delete methods mounted at /account paths
  * tenant_id is STRIPPED from every outbound row (defense-in-depth)
  * cross-tenant row in a store raises 500 isolation violation, not a silent leak
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.account_routes import AccountDeps, mount_account
from api.auth import make_current_tenant


# --------------------------------------------------------------------------- fakes

class FakeVerifier:
    """Accepts any non-empty Bearer; maps 't-A' -> tenant 'A', 't-B' -> tenant 'B'."""

    def verify(self, token: str) -> dict:
        tenant = token.split("-")[1] if token.startswith("t-") else "A"
        return {"sub": f"sub-{tenant}", "custom:tenant_id": tenant, "email": f"{tenant}@x.com"}


class FakeCrm:
    """In-memory crm stub that holds per-tenant rows for contacts, companies, and deals."""

    def __init__(self, contacts=None, companies=None, deals=None):
        # Each entry: a list of dicts that MUST carry 'tenant_id' (mimics what PgCrmClient returns)
        self._contacts: dict[str, list[dict]] = contacts or {}
        self._companies: dict[str, list[dict]] = companies or {}
        self._deals: dict[str, list[dict]] = deals or {}

    def list_contacts_directory(self, *, tenant_id, q=None, limit=501, offset=0):
        rows = [r for r in self._contacts.get(str(tenant_id), [])
                if str(r.get("tenant_id")) == str(tenant_id)]
        sliced = rows[offset: offset + limit]
        return sliced

    def list_companies_directory(self, *, tenant_id, q=None, limit=501, offset=0):
        rows = [r for r in self._companies.get(str(tenant_id), [])
                if str(r.get("tenant_id")) == str(tenant_id)]
        sliced = rows[offset: offset + limit]
        return sliced

    def list_deals_board(self, *, tenant_id):
        return [r for r in self._deals.get(str(tenant_id), [])
                if str(r.get("tenant_id")) == str(tenant_id)]


class FakeRag:
    """In-memory rag stub that holds per-tenant knowledge-doc inventory rows."""

    def __init__(self, inventory=None):
        # inventory: dict[tenant_id -> list[{source, document_count, last_updated}]]
        self._inventory: dict[str, list[dict]] = inventory or {}

    def list_document_inventory(self, *, tenant_id):
        return list(self._inventory.get(str(tenant_id), []))


class FakeSavedViews:
    """In-memory saved-views stub that holds per-tenant view + dashboard rows."""

    def __init__(self, views=None, dashboards=None):
        # views/dashboards: dict[tenant_id -> list[dict]]
        self._views: dict[str, list[dict]] = views or {}
        self._dashboards: dict[str, list[dict]] = dashboards or {}

    def list_views(self, tenant_id: str):
        return list(self._views.get(str(tenant_id), []))

    def list_dashboards(self, tenant_id: str):
        return list(self._dashboards.get(str(tenant_id), []))


# --------------------------------------------------------------------------- helpers

H_A = {"Authorization": "Bearer t-A"}
H_B = {"Authorization": "Bearer t-B"}


def _make_contact(tenant_id: str, contact_id: str = "c1", **extra) -> dict:
    return {"tenant_id": tenant_id, "id": contact_id, "name": f"Contact {contact_id}", **extra}


def _make_company(tenant_id: str, company_id: str = "co1", **extra) -> dict:
    return {"tenant_id": tenant_id, "id": company_id, "name": f"Company {company_id}", **extra}


def _make_deal(tenant_id: str, deal_id: str = "d1", **extra) -> dict:
    return {"tenant_id": tenant_id, "id": deal_id, "title": f"Deal {deal_id}", **extra}


def _make_view(tenant_id: str, view_id: str = "v1", **extra) -> dict:
    return {"tenant_id": tenant_id, "view_id": view_id, "spec_json": {}, **extra}


def _make_doc(source: str = "hubspot", count: int = 5) -> dict:
    return {"source": source, "document_count": count, "last_updated": None}


def _client(
    *,
    crm=None,
    rag=None,
    saved_views=None,
) -> TestClient:
    app = FastAPI()
    deps = AccountDeps(crm=crm, rag=rag, saved_views=saved_views)
    mount_account(app, deps, make_current_tenant(FakeVerifier()))
    return TestClient(app)


def _all_stores_client(tenant_id: str = "A") -> TestClient:
    """A client with all stores wired and one row per section for tenant_id."""
    return _client(
        crm=FakeCrm(
            contacts={tenant_id: [_make_contact(tenant_id)]},
            companies={tenant_id: [_make_company(tenant_id)]},
            deals={tenant_id: [_make_deal(tenant_id)]},
        ),
        rag=FakeRag(inventory={tenant_id: [_make_doc()]}),
        saved_views=FakeSavedViews(
            views={tenant_id: [_make_view(tenant_id)]},
        ),
    )


# --------------------------------------------------------------------------- auth

@pytest.mark.unit
def test_export_requires_bearer():
    """No auth header -> 401."""
    c = _all_stores_client()
    r = c.get("/account/export")
    assert r.status_code == 401


@pytest.mark.unit
def test_export_invalid_token_401():
    """An unparseable / invalid token -> 401 (the verifier raises)."""

    class _RejectAll:
        def verify(self, token):
            raise ValueError("invalid")

    app = FastAPI()
    deps = AccountDeps(crm=FakeCrm(), rag=FakeRag(), saved_views=FakeSavedViews())
    mount_account(app, deps, make_current_tenant(_RejectAll()))
    c = TestClient(app)
    r = c.get("/account/export", headers={"Authorization": "Bearer bad"})
    assert r.status_code == 401


# --------------------------------------------------------------------------- 503 unconfigured

@pytest.mark.unit
def test_export_503_when_all_stores_none():
    """All stores None -> 503 (honest 'nothing to export')."""
    c = _client(crm=None, rag=None, saved_views=None)
    r = c.get("/account/export", headers=H_A)
    assert r.status_code == 503
    assert "configured" in r.json()["detail"].lower()


@pytest.mark.unit
def test_export_not_503_when_only_crm_none():
    """crm=None but rag + saved_views wired -> the export runs (partial, not 503)."""
    c = _client(
        crm=None,
        rag=FakeRag(inventory={"A": [_make_doc()]}),
        saved_views=FakeSavedViews(),
    )
    r = c.get("/account/export", headers=H_A)
    assert r.status_code == 200


@pytest.mark.unit
def test_export_not_503_when_only_rag_none():
    """rag=None but crm + saved_views wired -> export runs."""
    c = _client(
        crm=FakeCrm(contacts={"A": [_make_contact("A")]}, companies={}, deals={}),
        rag=None,
        saved_views=FakeSavedViews(),
    )
    r = c.get("/account/export", headers=H_A)
    assert r.status_code == 200


# --------------------------------------------------------------------------- bundle shape

@pytest.mark.unit
def test_bundle_has_all_sections():
    """A fully-wired export includes contacts, companies, deals, saved_views, knowledge_docs."""
    c = _all_stores_client("A")
    r = c.get("/account/export", headers=H_A)
    assert r.status_code == 200
    body = r.json()
    for section in ("contacts", "companies", "deals", "saved_views", "knowledge_docs"):
        assert section in body, f"missing section: {section}"


@pytest.mark.unit
def test_bundle_tenant_id_field():
    """The bundle carries tenant_id echoed from the verified claim."""
    c = _all_stores_client("A")
    r = c.get("/account/export", headers=H_A)
    body = r.json()
    assert body["tenant_id"] == "A"


@pytest.mark.unit
def test_bundle_contacts_row_shape():
    """Contacts section contains at least one row with the expected fields."""
    crm = FakeCrm(
        contacts={"A": [_make_contact("A", "c1")]},
        companies={},
        deals={},
    )
    c = _client(crm=crm, rag=FakeRag(), saved_views=FakeSavedViews())
    r = c.get("/account/export", headers=H_A)
    body = r.json()
    assert len(body["contacts"]) == 1
    row = body["contacts"][0]
    assert row["id"] == "c1"
    # tenant_id MUST be stripped from the outbound row
    assert "tenant_id" not in row


@pytest.mark.unit
def test_bundle_deals_row_shape():
    """Deals section contains the expected row and strips tenant_id."""
    crm = FakeCrm(
        contacts={},
        companies={},
        deals={"A": [_make_deal("A", "d1")]},
    )
    c = _client(crm=crm, rag=FakeRag(), saved_views=FakeSavedViews())
    r = c.get("/account/export", headers=H_A)
    body = r.json()
    assert len(body["deals"]) == 1
    row = body["deals"][0]
    assert row["id"] == "d1"
    assert "tenant_id" not in row


@pytest.mark.unit
def test_bundle_saved_views_row_shape():
    """Saved views section strips tenant_id from outbound rows."""
    sv = FakeSavedViews(views={"A": [_make_view("A", "v1")]})
    c = _client(crm=FakeCrm(), rag=FakeRag(), saved_views=sv)
    r = c.get("/account/export", headers=H_A)
    body = r.json()
    assert len(body["saved_views"]) == 1
    assert body["saved_views"][0]["view_id"] == "v1"
    assert "tenant_id" not in body["saved_views"][0]


@pytest.mark.unit
def test_bundle_knowledge_docs_shape():
    """Knowledge docs section returns source + document_count + last_updated."""
    rag = FakeRag(inventory={"A": [_make_doc("hubspot", 12)]})
    c = _client(crm=FakeCrm(), rag=rag, saved_views=FakeSavedViews())
    r = c.get("/account/export", headers=H_A)
    body = r.json()
    assert len(body["knowledge_docs"]) == 1
    doc = body["knowledge_docs"][0]
    assert doc["source"] == "hubspot"
    assert doc["document_count"] == 12


# --------------------------------------------------------------------------- tenant isolation

@pytest.mark.unit
def test_tenant_from_claim_not_from_query_or_body():
    """The tenant_id in the export matches the verified-claim tenant, never a caller-supplied value."""
    c = _all_stores_client("A")
    # Attempt to sneak a different tenant in a query param — must be ignored
    r = c.get("/account/export?tenant_id=B", headers=H_A)
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == "A"


@pytest.mark.unit
def test_two_tenants_get_their_own_data():
    """Two tenants calling export each get ONLY their own rows."""
    crm = FakeCrm(
        contacts={
            "A": [_make_contact("A", "cA")],
            "B": [_make_contact("B", "cB")],
        },
        companies={},
        deals={},
    )
    sv = FakeSavedViews(
        views={
            "A": [_make_view("A", "vA")],
            "B": [_make_view("B", "vB")],
        },
    )
    c = _client(crm=crm, rag=FakeRag(), saved_views=sv)

    r_a = c.get("/account/export", headers=H_A)
    r_b = c.get("/account/export", headers=H_B)

    assert r_a.status_code == 200
    assert r_b.status_code == 200

    a_contacts = {row["id"] for row in r_a.json()["contacts"]}
    b_contacts = {row["id"] for row in r_b.json()["contacts"]}
    assert a_contacts == {"cA"}
    assert b_contacts == {"cB"}
    # No overlap
    assert a_contacts.isdisjoint(b_contacts)

    a_views = {row["view_id"] for row in r_a.json()["saved_views"]}
    b_views = {row["view_id"] for row in r_b.json()["saved_views"]}
    assert a_views == {"vA"}
    assert b_views == {"vB"}


@pytest.mark.unit
def test_cross_tenant_row_raises_500():
    """If the CRM returns a row whose tenant_id doesn't match the request tenant, a 500 is raised
    (defense-in-depth: the leak is detected and refused, not silently propagated)."""

    class _PoisonedCrm:
        """Returns a row belonging to tenant B when tenant A asks for contacts."""
        def list_contacts_directory(self, *, tenant_id, q=None, limit=501, offset=0):
            # Always returns tenant B's row — a bug / misconfigured RLS scenario.
            return [{"tenant_id": "B", "id": "cB", "name": "Stolen contact"}]

        def list_companies_directory(self, *, tenant_id, q=None, limit=501, offset=0):
            return []

        def list_deals_board(self, *, tenant_id):
            return []

    c = _client(crm=_PoisonedCrm(), rag=FakeRag(), saved_views=FakeSavedViews())
    r = c.get("/account/export", headers=H_A)
    assert r.status_code == 500
    assert "isolation" in r.json()["detail"].lower()


# --------------------------------------------------------------------------- partial config / unavailable notes

@pytest.mark.unit
def test_knowledge_docs_unavailable_note_when_rag_none():
    """When rag is None, knowledge_docs is [] and sections_unavailable carries a note."""
    c = _client(crm=FakeCrm(), rag=None, saved_views=FakeSavedViews())
    r = c.get("/account/export", headers=H_A)
    assert r.status_code == 200
    body = r.json()
    assert body["knowledge_docs"] == []
    notes = body.get("sections_unavailable", [])
    section_names = [n["section"] for n in notes]
    assert "knowledge_docs" in section_names


@pytest.mark.unit
def test_contacts_companies_deals_unavailable_note_when_crm_none():
    """When crm is None, contacts/companies/deals are [] and sections_unavailable notes contacts."""
    c = _client(crm=None, rag=FakeRag(), saved_views=FakeSavedViews())
    r = c.get("/account/export", headers=H_A)
    assert r.status_code == 200
    body = r.json()
    assert body["contacts"] == []
    assert body["companies"] == []
    assert body["deals"] == []
    notes = body.get("sections_unavailable", [])
    section_names = [n["section"] for n in notes]
    assert "contacts" in section_names


# --------------------------------------------------------------------------- read-only (no delete routes)

@pytest.mark.unit
def test_no_delete_route_mounted():
    """No DELETE method should be mounted under /account — this is pure egress."""
    app = FastAPI()
    deps = AccountDeps()
    mount_account(app, deps, make_current_tenant(FakeVerifier()))

    from fastapi.routing import APIRoute
    delete_routes = [
        route for route in app.routes
        if isinstance(route, APIRoute) and "DELETE" in (route.methods or set())
        and str(route.path).startswith("/account")
    ]
    assert delete_routes == [], f"unexpected DELETE routes: {delete_routes}"


# --------------------------------------------------------------------------- empty tenant

@pytest.mark.unit
def test_empty_tenant_returns_empty_sections():
    """A tenant with no data gets back empty lists (never 404 or 503)."""
    c = _client(
        crm=FakeCrm(contacts={}, companies={}, deals={}),
        rag=FakeRag(inventory={}),
        saved_views=FakeSavedViews(views={}, dashboards={}),
    )
    r = c.get("/account/export", headers=H_A)
    assert r.status_code == 200
    body = r.json()
    assert body["contacts"] == []
    assert body["companies"] == []
    assert body["deals"] == []
    assert body["saved_views"] == []
    assert body["knowledge_docs"] == []
    assert body["tenant_id"] == "A"
