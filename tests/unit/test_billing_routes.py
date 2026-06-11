"""Unit: the /billing routes (Stripe Customer Portal) — claims-bound, trust-rule upheld.

Mounts ``mount_billing`` on a bare FastAPI app with a fake verifier, a stubbed Stripe adapter,
and an in-memory account store. No real DB, no real Stripe calls.

Coverage:
  * POST /billing/portal-session happy path — tenant with stripe_customer_id returns {"url": ...}
  * POST /billing/portal-session 403 — tenant has no stripe_customer_id (no billing account yet)
  * POST /billing/portal-session 503 — Stripe unconfigured (no api_key); never a 500
  * POST /billing/portal-session 401 — missing/invalid Bearer token
  * GET /billing — returns {"customer": bool, "plan": str|None, "status": str}
  * GET /billing — tenant with no account returns customer=False and default status
  * GET /billing — tenant with billing_status row surfaces the persisted status
  * GET /billing — tenant identity comes ONLY from the verified claim (never a body/query param)
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import make_current_tenant
from api.billing_routes import BillingDeps, mount_billing
from signup.stripe_adapter import StripeNotConfiguredError


# --------------------------------------------------------------------------- fakes

class FakeVerifier:
    """Accepts any non-empty Bearer; maps token value to a deterministic tenant."""

    def verify(self, token: str) -> dict:
        # Token "t-A" -> tenant "A"; "t-B" -> tenant "B"; anything else -> tenant "A"
        tenant = token.split("-")[1] if token.startswith("t-") else "A"
        return {"sub": f"sub-{tenant}", "custom:tenant_id": tenant, "email": f"{tenant}@x.com"}


class _Account:
    """Minimal account object with stripe_customer_id and meta."""

    def __init__(self, tenant_id: str, *, stripe_customer_id: str | None = None, plan: str | None = None):
        self.id = f"acct-{tenant_id}"
        self.tenant_id = tenant_id
        self.stripe_customer_id = stripe_customer_id
        self.meta = {"plan": plan} if plan else {}


class FakeAccountStore:
    """In-memory account store keyed by tenant_id; honors the duck-type contract used by billing_routes."""

    def __init__(self, accounts: dict | None = None, billing_rows: dict | None = None):
        # tenant_id -> _Account
        self._accounts: dict[str, _Account] = accounts or {}
        # account_id -> {"status": str}
        self._billing_rows: dict[str, dict] = billing_rows or {}

    def get_by_tenant_id(self, tenant_id: str) -> _Account | None:
        return self._accounts.get(str(tenant_id))

    def get_billing_status(self, account_id: str) -> dict | None:
        return self._billing_rows.get(str(account_id))


class FakeStripe:
    """Minimal Stripe stub that records calls and returns controlled responses."""

    def __init__(self, portal_url: str = "https://billing.stripe.com/session/bps_test"):
        self._portal_url = portal_url
        self.calls: list[dict] = []

    def create_billing_portal_session(self, *, customer: str, return_url: str) -> dict:
        self.calls.append({"customer": customer, "return_url": return_url})
        return {"id": "bps_test", "url": self._portal_url}


class UnconfiguredStripe:
    """Simulates an unconfigured StripeAdapter (no api_key) — raises StripeNotConfiguredError."""

    def create_billing_portal_session(self, **_kwargs) -> dict:
        raise StripeNotConfiguredError("Stripe api_key not configured — cannot create a billing portal session")


# --------------------------------------------------------------------------- helpers

H = {"Authorization": "Bearer t-A"}   # verified tenant "A"


def _client(
    *,
    store: FakeAccountStore | None = None,
    stripe: object | None = None,
    return_url: str = "https://app.example.com/settings/billing",
) -> TestClient:
    app = FastAPI()
    deps = BillingDeps(
        stripe=stripe or FakeStripe(),
        accounts_store=store or FakeAccountStore(),
        return_url=return_url,
    )
    mount_billing(app, deps, make_current_tenant(FakeVerifier()))
    return TestClient(app)


def _store_with_customer(
    tenant_id: str = "A",
    customer_id: str = "cus_42",
    plan: str | None = "team",
) -> FakeAccountStore:
    return FakeAccountStore(
        accounts={tenant_id: _Account(tenant_id, stripe_customer_id=customer_id, plan=plan)},
    )


# --------------------------------------------------------------------------- auth

@pytest.mark.unit
def test_portal_session_requires_bearer():
    c = _client(store=_store_with_customer())
    r = c.post("/billing/portal-session")
    assert r.status_code == 401


@pytest.mark.unit
def test_billing_state_requires_bearer():
    c = _client(store=_store_with_customer())
    r = c.get("/billing")
    assert r.status_code == 401


# --------------------------------------------------------------------------- happy path: portal-session

@pytest.mark.unit
def test_portal_session_happy_path_returns_url():
    """Tenant with a stripe_customer_id gets back a Stripe portal URL."""
    stripe = FakeStripe(portal_url="https://billing.stripe.com/session/bps_abc")
    store = _store_with_customer(tenant_id="A", customer_id="cus_42")
    c = _client(store=store, stripe=stripe)

    r = c.post("/billing/portal-session", headers=H)

    assert r.status_code == 200
    body = r.json()
    assert body == {"url": "https://billing.stripe.com/session/bps_abc"}


@pytest.mark.unit
def test_portal_session_passes_verified_customer_id_to_stripe():
    """The customer id passed to Stripe comes from the account row (the verified claim path),
    never from anything the client sent."""
    stripe = FakeStripe()
    store = _store_with_customer(tenant_id="A", customer_id="cus_verified")
    c = _client(store=store, stripe=stripe, return_url="https://return.example.com")

    c.post("/billing/portal-session", headers=H)

    assert len(stripe.calls) == 1
    call = stripe.calls[0]
    assert call["customer"] == "cus_verified"
    assert call["return_url"] == "https://return.example.com"


@pytest.mark.unit
def test_portal_session_tenant_identity_from_claim_only():
    """A second tenant (token t-B) gets their OWN account looked up — the claim steers the lookup,
    never anything else."""
    stripe_a = FakeStripe(portal_url="https://billing.stripe.com/A")
    store = FakeAccountStore(
        accounts={
            "A": _Account("A", stripe_customer_id="cus_A"),
            "B": _Account("B", stripe_customer_id="cus_B"),
        }
    )
    c = _client(store=store, stripe=stripe_a)

    r_a = c.post("/billing/portal-session", headers={"Authorization": "Bearer t-A"})
    r_b = c.post("/billing/portal-session", headers={"Authorization": "Bearer t-B"})

    assert r_a.status_code == 200
    assert r_b.status_code == 200
    # Both called Stripe; the customer id for each came from THEIR account row
    assert stripe_a.calls[0]["customer"] == "cus_A"
    assert stripe_a.calls[1]["customer"] == "cus_B"


# --------------------------------------------------------------------------- 403: no billing account

@pytest.mark.unit
def test_portal_session_403_when_no_customer_mapping():
    """Tenant with no stripe_customer_id (internal-comp / pre-paid) gets an honest 403."""
    store = FakeAccountStore(
        accounts={"A": _Account("A", stripe_customer_id=None)},
    )
    c = _client(store=store)

    r = c.post("/billing/portal-session", headers=H)

    assert r.status_code == 403
    detail = r.json()["detail"]
    assert "billing account" in detail.lower()


@pytest.mark.unit
def test_portal_session_403_when_no_account_row():
    """Tenant with NO account row at all (brand-new / provisioning-failed) also gets a 403."""
    store = FakeAccountStore(accounts={})   # empty — tenant "A" has no row
    c = _client(store=store)

    r = c.post("/billing/portal-session", headers=H)

    assert r.status_code == 403


# --------------------------------------------------------------------------- 503: stripe unconfigured

@pytest.mark.unit
def test_portal_session_503_when_stripe_unconfigured():
    """No Stripe api_key → StripeNotConfiguredError must surface as 503, never 500."""
    store = _store_with_customer()
    c = _client(store=store, stripe=UnconfiguredStripe())

    r = c.post("/billing/portal-session", headers=H)

    assert r.status_code == 503
    detail = r.json()["detail"]
    assert "billing" in detail.lower()


@pytest.mark.unit
def test_portal_session_never_500_on_stripe_error():
    """Any Stripe-side exception (transient or config) must produce 503, not 500."""

    class FlakyStripe:
        def create_billing_portal_session(self, **_kwargs):
            raise RuntimeError("transient network failure")

    store = _store_with_customer()
    c = _client(store=store, stripe=FlakyStripe())

    r = c.post("/billing/portal-session", headers=H)

    assert r.status_code == 503
    assert r.status_code != 500


# --------------------------------------------------------------------------- GET /billing

@pytest.mark.unit
def test_billing_state_with_customer():
    """Tenant with stripe_customer_id: customer=True, plan and status reflect the account row."""
    store = FakeAccountStore(
        accounts={"A": _Account("A", stripe_customer_id="cus_42", plan="team")},
        billing_rows={"acct-A": {"status": "active"}},
    )
    c = _client(store=store)

    r = c.get("/billing", headers=H)

    assert r.status_code == 200
    body = r.json()
    assert body["customer"] is True
    assert body["plan"] == "team"
    assert body["status"] == "active"


@pytest.mark.unit
def test_billing_state_no_account_row():
    """No account row: customer=False, plan=None, status='active' (the safe default)."""
    c = _client(store=FakeAccountStore(accounts={}))

    r = c.get("/billing", headers=H)

    assert r.status_code == 200
    body = r.json()
    assert body["customer"] is False
    assert body["plan"] is None
    assert body["status"] == "active"


@pytest.mark.unit
def test_billing_state_no_customer_id():
    """Account row exists but no stripe_customer_id: customer=False."""
    store = FakeAccountStore(
        accounts={"A": _Account("A", stripe_customer_id=None, plan="starter")},
    )
    c = _client(store=store)

    r = c.get("/billing", headers=H)

    body = r.json()
    assert body["customer"] is False
    assert body["plan"] == "starter"


@pytest.mark.unit
def test_billing_state_surfaces_persisted_billing_status():
    """get_billing_status row takes precedence over the default 'active'."""
    store = FakeAccountStore(
        accounts={"A": _Account("A", stripe_customer_id="cus_42", plan="team")},
        billing_rows={"acct-A": {"status": "past_due"}},
    )
    c = _client(store=store)

    r = c.get("/billing", headers=H)

    assert r.json()["status"] == "past_due"


@pytest.mark.unit
def test_billing_state_canceled_status():
    """Canceled subscription status surfaces correctly."""
    store = FakeAccountStore(
        accounts={"A": _Account("A", stripe_customer_id="cus_42", plan="team")},
        billing_rows={"acct-A": {"status": "canceled"}},
    )
    c = _client(store=store)

    r = c.get("/billing", headers=H)

    assert r.json()["status"] == "canceled"


@pytest.mark.unit
def test_billing_state_tenant_identity_from_claim():
    """Two tenants with different accounts get their own data — claim steers the lookup."""
    store = FakeAccountStore(
        accounts={
            "A": _Account("A", stripe_customer_id="cus_A", plan="starter"),
            "B": _Account("B", stripe_customer_id=None, plan=None),
        },
    )
    c = _client(store=store)

    r_a = c.get("/billing", headers={"Authorization": "Bearer t-A"})
    r_b = c.get("/billing", headers={"Authorization": "Bearer t-B"})

    assert r_a.json()["customer"] is True
    assert r_a.json()["plan"] == "starter"
    assert r_b.json()["customer"] is False
    assert r_b.json()["plan"] is None


# --------------------------------------------------------------------------- api/ prefix aliases

@pytest.mark.unit
def test_portal_session_mounted_on_api_prefix_too():
    """Both /billing/portal-session and /api/billing/portal-session are mounted."""
    store = _store_with_customer()
    c = _client(store=store)

    r = c.post("/api/billing/portal-session", headers=H)
    # 200 or 403 are both fine — we just want to confirm it's NOT a 404
    assert r.status_code != 404


@pytest.mark.unit
def test_billing_state_mounted_on_api_prefix_too():
    """Both /billing and /api/billing are mounted."""
    c = _client(store=FakeAccountStore(accounts={}))

    r = c.get("/api/billing", headers=H)
    assert r.status_code != 404
