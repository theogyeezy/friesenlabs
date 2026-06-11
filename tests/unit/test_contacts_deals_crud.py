"""Unit: contacts + deals create/edit endpoints (api/contacts_routes.py,
api/deals_routes.py).

Mounts ``mount_contacts`` and ``mount_deals`` on a bare FastAPI app with a fake
verifier and stubbed CRM — zero DB, zero AWS, zero Greenlight.

Security-critical guarantees verified here:
  * Tenant ALWAYS comes from the verified JWT claim, never a header or body.
  * CREATE: POST /contacts and POST /deals write to the stub keyed by the claim tenant.
  * EDIT: PATCH /contacts/{id} and PATCH /deals/{id} call the stub's update methods.
  * Cross-tenant 404: a contact/deal that belongs to another tenant is indistinguishable
    from a missing one — the endpoint answers 404, not a row from the wrong tenant.
  * 401 unauth: missing / invalid bearer -> 401.
  * 422 validation: empty name/title -> 422.
  * 503 unconfigured: crm=None -> every endpoint answers 503.
"""
from __future__ import annotations

import uuid
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.contacts_routes import ContactsDeps, mount_contacts
from api.deals_routes import DealsDeps, mount_deals
from api.auth import make_current_tenant


# --------------------------------------------------------------------------- fakes

class FakeVerifier:
    """Accepts any 't-{tenant}' bearer; maps it to the verified TenantClaims."""

    def verify(self, token: str) -> dict:
        tenant = token.split("-")[1] if token.startswith("t-") else "A"
        return {"sub": f"sub-{tenant}", "custom:tenant_id": tenant, "email": f"{tenant}@x.com"}


class _RejectAll:
    def verify(self, token: str) -> dict:
        raise ValueError("bad token")


class FakeCrm:
    """In-memory CRM stub covering both contacts and deals surfaces.

    Stores created/edited rows per-tenant; simulates the 404 cross-tenant guard by
    refusing get_deal_board / get_contact_directory for the wrong tenant.
    """

    def __init__(self):
        self._contacts: dict[str, dict] = {}  # id -> row (includes tenant_id)
        self._deals: dict[str, dict] = {}     # id -> row (includes tenant_id)
        self.calls: list[tuple] = []           # (method, kwargs) audit log

    # --- contacts reads (minimal — the write tests call the write methods) ----

    def list_contacts_directory(self, *, tenant_id, q=None, limit=51, offset=0):
        return [
            r for r in self._contacts.values()
            if str(r["tenant_id"]) == str(tenant_id)
        ]

    def get_contact_directory(self, *, tenant_id, contact_id):
        row = self._contacts.get(str(contact_id))
        if row is None or str(row["tenant_id"]) != str(tenant_id):
            return None
        return row

    def list_contact_activities(self, *, tenant_id, contact_id, limit=20):
        return []

    def list_company_open_deals(self, *, tenant_id, company_id, limit=50):
        return []

    def list_companies_directory(self, *, tenant_id, q=None, limit=51, offset=0):
        return []

    def get_company_directory(self, *, tenant_id, company_id):
        return None

    def list_company_contacts(self, *, tenant_id, company_id, limit=50):
        return []

    # --- contacts writes -------------------------------------------------------

    def insert_contact(self, *, tenant_id, name, email=None, phone=None,
                       company_id=None):
        self.calls.append(("insert_contact", dict(
            tenant_id=tenant_id, name=name, email=email, phone=phone,
            company_id=company_id,
        )))
        row_id = str(uuid.uuid4())
        row = {"id": row_id, "name": name, "email": email, "phone": phone,
               "tenant_id": str(tenant_id)}
        self._contacts[row_id] = row
        return {"id": row_id, "name": name, "email": email, "phone": phone}

    def update_contact_fields(self, *, tenant_id, contact_id, changes):
        self.calls.append(("update_contact_fields", dict(
            tenant_id=tenant_id, contact_id=contact_id, changes=changes,
        )))
        row = self._contacts.get(str(contact_id))
        if row is None or str(row["tenant_id"]) != str(tenant_id):
            raise ValueError("contact not found or not visible")
        row.update(changes)
        return {
            "id": str(contact_id),
            "updated": changes,
            "skipped": {},
            "contact": {"id": str(contact_id), "name": row.get("name"),
                        "email": row.get("email"), "phone": row.get("phone")},
        }

    # --- deals reads (minimal) ------------------------------------------------

    def list_deals_board(self, *, tenant_id, limit=500):
        return [
            r for r in self._deals.values()
            if str(r["tenant_id"]) == str(tenant_id)
        ]

    def get_deal_board(self, *, tenant_id, deal_id):
        row = self._deals.get(str(deal_id))
        if row is None or str(row["tenant_id"]) != str(tenant_id):
            return None
        return row

    def list_deal_activities(self, *, tenant_id, deal_id, limit=20):
        return []

    # --- deals writes ---------------------------------------------------------

    def insert_deal(self, *, tenant_id, company_id, name, stage, amount):
        self.calls.append(("insert_deal", dict(
            tenant_id=tenant_id, company_id=company_id, name=name,
            stage=stage, amount=amount,
        )))
        row_id = str(uuid.uuid4())
        row = {"id": row_id, "title": name, "name": name, "stage": stage,
               "amount": amount, "tenant_id": str(tenant_id),
               "company_id": company_id, "contact_id": None, "created_at": None,
               "currency": None, "company_name": None}
        self._deals[row_id] = row
        return {"id": row_id, "name": name, "stage": stage, "amount": amount}

    def update_deal_fields(self, *, tenant_id, deal_id, changes):
        self.calls.append(("update_deal_fields", dict(
            tenant_id=tenant_id, deal_id=deal_id, changes=changes,
        )))
        row = self._deals.get(str(deal_id))
        if row is None or str(row["tenant_id"]) != str(tenant_id):
            raise ValueError("deal not found or not visible")
        # "name" change maps to deals.title
        if "name" in changes:
            row["title"] = changes["name"]
            row["name"] = changes["name"]
        if "amount" in changes:
            row["amount"] = changes["amount"]
        return {
            "id": str(deal_id),
            "updated": changes,
            "deal": {"id": str(deal_id), "name": row.get("name"),
                     "stage": row.get("stage"), "amount": row.get("amount")},
        }


# --------------------------------------------------------------------------- helpers

H_A = {"Authorization": "Bearer t-A"}
H_B = {"Authorization": "Bearer t-B"}


def _contacts_client(crm=None) -> tuple[TestClient, FakeCrm]:
    """Build a TestClient with mount_contacts; returns (client, crm_stub)."""
    stub = crm if crm is not None else FakeCrm()
    app = FastAPI()
    deps = ContactsDeps(crm=stub if stub is not None else None)
    mount_contacts(app, deps, make_current_tenant(FakeVerifier()))
    return TestClient(app, raise_server_exceptions=False), stub


def _deals_client(crm=None) -> tuple[TestClient, FakeCrm]:
    """Build a TestClient with mount_deals; returns (client, crm_stub).

    mount_deals requires gate_deps; a minimal duck-typed stub satisfies it since
    POST /deals and PATCH /deals/{id} never touch the gate path.
    """
    stub = crm if crm is not None else FakeCrm()

    class _FakeGateDeps:
        autonomy_config = None
        executor = None
        greenlight = None
        killswitch = None
        trace_store = None

    app = FastAPI()
    deps = DealsDeps(crm=stub if stub is not None else None)
    mount_deals(app, deps, make_current_tenant(FakeVerifier()), gate_deps=_FakeGateDeps())
    return TestClient(app, raise_server_exceptions=False), stub


def _seed_contact(crm: FakeCrm, tenant_id: str, **fields) -> str:
    """Insert a contact row directly into the stub and return its id."""
    row_id = str(uuid.uuid4())
    row = {"id": row_id, "tenant_id": str(tenant_id), "name": "Test Contact",
           "email": None, "phone": None, "company_id": None,
           "company_name": None, "created_at": None, "last_activity_at": None,
           "title": None, **fields}
    crm._contacts[row_id] = row
    return row_id


def _seed_deal(crm: FakeCrm, tenant_id: str, **fields) -> str:
    """Insert a deal row directly into the stub and return its id."""
    row_id = str(uuid.uuid4())
    row = {"id": row_id, "tenant_id": str(tenant_id), "title": "Test Deal",
           "name": "Test Deal", "stage": "new", "amount": None,
           "currency": None, "company_id": None, "contact_id": None,
           "company_name": None, "created_at": None, **fields}
    crm._deals[row_id] = row
    return row_id


# =========================================================================== #
# POST /contacts
# =========================================================================== #

@pytest.mark.unit
def test_create_contact_requires_bearer():
    """No auth -> 401, never a write."""
    tc, crm = _contacts_client()
    r = tc.post("/contacts", json={"name": "Alice"})
    assert r.status_code == 401
    assert not any(c[0] == "insert_contact" for c in crm.calls)


@pytest.mark.unit
def test_create_contact_invalid_token_401():
    stub = FakeCrm()
    app = FastAPI()
    mount_contacts(app, ContactsDeps(crm=stub), make_current_tenant(_RejectAll()))
    tc = TestClient(app, raise_server_exceptions=False)
    r = tc.post("/contacts", json={"name": "Alice"}, headers={"Authorization": "Bearer bad"})
    assert r.status_code == 401


@pytest.mark.unit
def test_create_contact_empty_name_422():
    """Empty name -> 422, no write."""
    tc, crm = _contacts_client()
    r = tc.post("/contacts", json={"name": "   "}, headers=H_A)
    assert r.status_code == 422
    assert not crm.calls


@pytest.mark.unit
def test_create_contact_success():
    """Valid POST -> 201, insert_contact called with tenant from claim."""
    tc, crm = _contacts_client()
    r = tc.post("/contacts", json={"name": "Alice", "email": "alice@x.com", "phone": "555-1234"},
                headers=H_A)
    assert r.status_code == 201, r.text
    data = r.json()
    assert "contact" in data
    assert data["contact"]["name"] == "Alice"
    # The write was called with tenant_id="A" from the verified claim, not from the body.
    assert len(crm.calls) == 1
    method, kwargs = crm.calls[0]
    assert method == "insert_contact"
    assert kwargs["tenant_id"] == "A"   # THE TRUST RULE
    assert kwargs["name"] == "Alice"
    assert kwargs["email"] == "alice@x.com"


@pytest.mark.unit
def test_create_contact_tenant_from_claim_not_body():
    """Body may not carry a tenant_id — the route ignores any such field entirely."""
    tc, crm = _contacts_client()
    # Send a rogue tenant_id in the body — Pydantic drops unknown fields; the route
    # uses claims.tenant_id only.
    r = tc.post("/contacts", json={"name": "Bob", "tenant_id": "EVIL"}, headers=H_A)
    assert r.status_code == 201
    _, kwargs = crm.calls[0]
    assert kwargs["tenant_id"] == "A"   # EVIL was ignored


@pytest.mark.unit
def test_create_contact_503_unconfigured():
    """crm=None -> 503, never a 500."""
    app = FastAPI()
    mount_contacts(app, ContactsDeps(crm=None), make_current_tenant(FakeVerifier()))
    tc = TestClient(app, raise_server_exceptions=False)
    r = tc.post("/contacts", json={"name": "X"}, headers=H_A)
    assert r.status_code == 503


# =========================================================================== #
# PATCH /contacts/{id}
# =========================================================================== #

@pytest.mark.unit
def test_edit_contact_requires_bearer():
    tc, crm = _contacts_client()
    cid = _seed_contact(crm, "A")
    r = tc.patch(f"/contacts/{cid}", json={"name": "New"})
    assert r.status_code == 401


@pytest.mark.unit
def test_edit_contact_empty_name_422():
    tc, crm = _contacts_client()
    cid = _seed_contact(crm, "A", name="Old")
    r = tc.patch(f"/contacts/{cid}", json={"name": ""}, headers=H_A)
    assert r.status_code == 422


@pytest.mark.unit
def test_edit_contact_no_fields_422():
    """Sending an empty body -> 422 (at least one field required)."""
    tc, crm = _contacts_client()
    cid = _seed_contact(crm, "A")
    r = tc.patch(f"/contacts/{cid}", json={}, headers=H_A)
    assert r.status_code == 422


@pytest.mark.unit
def test_edit_contact_success():
    """Valid PATCH -> 200, update_contact_fields called with tenant from claim."""
    tc, crm = _contacts_client()
    cid = _seed_contact(crm, "A", name="Old Name")
    r = tc.patch(f"/contacts/{cid}", json={"name": "New Name", "email": "new@x.com"},
                 headers=H_A)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "updated" in data
    # tenant from claim, not from anywhere else
    method, kwargs = crm.calls[0]
    assert method == "update_contact_fields"
    assert kwargs["tenant_id"] == "A"   # THE TRUST RULE
    assert kwargs["contact_id"] == cid
    assert "name" in kwargs["changes"]


@pytest.mark.unit
def test_edit_contact_cross_tenant_404():
    """A contact owned by tenant B is a 404 for tenant A."""
    tc, crm = _contacts_client()
    cid = _seed_contact(crm, "B")  # belongs to tenant B
    r = tc.patch(f"/contacts/{cid}", json={"name": "Hijacked"}, headers=H_A)
    assert r.status_code == 404


@pytest.mark.unit
def test_edit_contact_missing_id_404():
    """A well-formed UUID that doesn't exist -> 404 (not a 500)."""
    tc, crm = _contacts_client()
    r = tc.patch(f"/contacts/{uuid.uuid4()}", json={"name": "X"}, headers=H_A)
    assert r.status_code == 404


@pytest.mark.unit
def test_edit_contact_malformed_id_404():
    """A non-UUID path segment -> 404 (indistinguishable from missing)."""
    tc, crm = _contacts_client()
    r = tc.patch("/contacts/not-a-uuid", json={"name": "X"}, headers=H_A)
    assert r.status_code == 404


@pytest.mark.unit
def test_edit_contact_503_unconfigured():
    app = FastAPI()
    mount_contacts(app, ContactsDeps(crm=None), make_current_tenant(FakeVerifier()))
    tc = TestClient(app, raise_server_exceptions=False)
    r = tc.patch(f"/contacts/{uuid.uuid4()}", json={"name": "X"}, headers=H_A)
    assert r.status_code == 503


# =========================================================================== #
# POST /deals
# =========================================================================== #

@pytest.mark.unit
def test_create_deal_requires_bearer():
    tc, crm = _deals_client()
    r = tc.post("/deals", json={"title": "Big Sale"})
    assert r.status_code == 401
    assert not crm.calls


@pytest.mark.unit
def test_create_deal_empty_title_422():
    tc, crm = _deals_client()
    r = tc.post("/deals", json={"title": "  "}, headers=H_A)
    assert r.status_code == 422
    assert not crm.calls


@pytest.mark.unit
def test_create_deal_success():
    """Valid POST -> 201, insert_deal called with tenant from claim."""
    tc, crm = _deals_client()
    r = tc.post("/deals", json={"title": "Enterprise Deal", "amount": 50000.0},
                headers=H_A)
    assert r.status_code == 201, r.text
    data = r.json()
    assert "deal" in data
    assert data["deal"]["name"] == "Enterprise Deal"
    method, kwargs = crm.calls[0]
    assert method == "insert_deal"
    assert kwargs["tenant_id"] == "A"   # THE TRUST RULE
    assert kwargs["name"] == "Enterprise Deal"
    assert kwargs["amount"] == 50000.0


@pytest.mark.unit
def test_create_deal_tenant_from_claim_not_body():
    tc, crm = _deals_client()
    r = tc.post("/deals", json={"title": "X", "tenant_id": "EVIL"}, headers=H_A)
    assert r.status_code == 201
    _, kwargs = crm.calls[0]
    assert kwargs["tenant_id"] == "A"


@pytest.mark.unit
def test_create_deal_default_stage_new():
    """Omitting stage defaults to 'new'."""
    tc, crm = _deals_client()
    r = tc.post("/deals", json={"title": "X"}, headers=H_A)
    assert r.status_code == 201
    _, kwargs = crm.calls[0]
    assert kwargs["stage"] == "new"


@pytest.mark.unit
def test_create_deal_503_unconfigured():
    app = FastAPI()
    deps = DealsDeps(crm=None)

    class _FakeGateDeps:
        autonomy_config = None
        executor = None
        greenlight = None
        killswitch = None
        trace_store = None

    mount_deals(app, deps, make_current_tenant(FakeVerifier()), gate_deps=_FakeGateDeps())
    tc = TestClient(app, raise_server_exceptions=False)
    r = tc.post("/deals", json={"title": "X"}, headers=H_A)
    assert r.status_code == 503


# =========================================================================== #
# PATCH /deals/{id}
# =========================================================================== #

@pytest.mark.unit
def test_edit_deal_requires_bearer():
    tc, crm = _deals_client()
    did = _seed_deal(crm, "A")
    r = tc.patch(f"/deals/{did}", json={"title": "New Title"})
    assert r.status_code == 401


@pytest.mark.unit
def test_edit_deal_empty_title_422():
    tc, crm = _deals_client()
    did = _seed_deal(crm, "A")
    r = tc.patch(f"/deals/{did}", json={"title": ""}, headers=H_A)
    assert r.status_code == 422


@pytest.mark.unit
def test_edit_deal_no_fields_422():
    tc, crm = _deals_client()
    did = _seed_deal(crm, "A")
    r = tc.patch(f"/deals/{did}", json={}, headers=H_A)
    assert r.status_code == 422


@pytest.mark.unit
def test_edit_deal_success_title():
    tc, crm = _deals_client()
    did = _seed_deal(crm, "A", title="Old Title", name="Old Title")
    r = tc.patch(f"/deals/{did}", json={"title": "New Title"}, headers=H_A)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "updated" in data
    # update_deal_fields receives "name" (the internal change key) for the title field
    method, kwargs = crm.calls[0]
    assert method == "update_deal_fields"
    assert kwargs["tenant_id"] == "A"   # THE TRUST RULE
    assert kwargs["deal_id"] == did
    assert "name" in kwargs["changes"]   # title -> "name" change key


@pytest.mark.unit
def test_edit_deal_success_amount():
    tc, crm = _deals_client()
    did = _seed_deal(crm, "A")
    r = tc.patch(f"/deals/{did}", json={"amount": 99999.0}, headers=H_A)
    assert r.status_code == 200, r.text
    method, kwargs = crm.calls[0]
    assert method == "update_deal_fields"
    assert kwargs["changes"]["amount"] == 99999.0


@pytest.mark.unit
def test_edit_deal_cross_tenant_404():
    """A deal owned by tenant B is a 404 for tenant A (RLS-scoped existence check)."""
    tc, crm = _deals_client()
    did = _seed_deal(crm, "B")  # tenant B owns this deal
    r = tc.patch(f"/deals/{did}", json={"title": "Hijacked"}, headers=H_A)
    assert r.status_code == 404


@pytest.mark.unit
def test_edit_deal_missing_id_404():
    tc, crm = _deals_client()
    r = tc.patch(f"/deals/{uuid.uuid4()}", json={"title": "X"}, headers=H_A)
    assert r.status_code == 404


@pytest.mark.unit
def test_edit_deal_malformed_id_404():
    tc, crm = _deals_client()
    r = tc.patch("/deals/not-a-uuid", json={"title": "X"}, headers=H_A)
    assert r.status_code == 404


@pytest.mark.unit
def test_edit_deal_503_unconfigured():
    app = FastAPI()
    deps = DealsDeps(crm=None)

    class _FakeGateDeps:
        autonomy_config = None
        executor = None
        greenlight = None
        killswitch = None
        trace_store = None

    mount_deals(app, deps, make_current_tenant(FakeVerifier()), gate_deps=_FakeGateDeps())
    tc = TestClient(app, raise_server_exceptions=False)
    r = tc.patch(f"/deals/{uuid.uuid4()}", json={"title": "X"}, headers=H_A)
    assert r.status_code == 503
