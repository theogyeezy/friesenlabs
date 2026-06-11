"""Unit: GET /billing/invoices — real Stripe invoice list, claims-bound, trust-rule upheld.

Mounts ``mount_billing`` on a bare FastAPI app with a fake verifier, a fake Stripe adapter,
and an in-memory account store. No real DB, no real Stripe calls.

Coverage:
  * Unconfigured Stripe → honest 503 (parity with portal-session route)
  * No Stripe customer mapping → {"invoices": []} (honest empty, never invented)
  * Tenant WITH invoices → normalized rows returned
  * Tenant identity comes ONLY from the verified claim (THE TRUST RULE)
  * A customer_id injected in the request body/query cannot override the server-resolved one
  * /api/billing/invoices alias works (not a 404)
  * Auth: missing bearer → 401
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
    def __init__(self, tenant_id: str, *, stripe_customer_id: str | None = None, plan: str | None = None):
        self.id = f"acct-{tenant_id}"
        self.tenant_id = tenant_id
        self.stripe_customer_id = stripe_customer_id
        self.meta = {"plan": plan} if plan else {}


class FakeAccountStore:
    def __init__(self, accounts: dict | None = None, billing_rows: dict | None = None):
        self._accounts: dict[str, _Account] = accounts or {}
        self._billing_rows: dict[str, dict] = billing_rows or {}

    def get_by_tenant_id(self, tenant_id: str) -> _Account | None:
        return self._accounts.get(str(tenant_id))

    def get_billing_status(self, account_id: str) -> dict | None:
        return self._billing_rows.get(str(account_id))


# A realistic normalized invoice dict
_SAMPLE_INVOICE = {
    "id": "in_test1",
    "number": "INV-0001",
    "amount_due": 9900,
    "amount_paid": 9900,
    "currency": "usd",
    "status": "paid",
    "created": 1700000000,
    "hosted_invoice_url": "https://invoice.stripe.com/i/test1",
    "invoice_pdf": "https://pay.stripe.com/invoice/test1/pdf",
}


class FakeStripe:
    """Minimal Stripe stub implementing the full BillingDeps duck-type."""

    def __init__(
        self,
        portal_url: str = "https://billing.stripe.com/session/bps_test",
        invoices: list[dict] | None = None,
    ):
        self._portal_url = portal_url
        self._invoices = invoices if invoices is not None else []
        self.invoice_calls: list[dict] = []
        self.portal_calls: list[dict] = []

    def create_billing_portal_session(self, *, customer: str, return_url: str) -> dict:
        self.portal_calls.append({"customer": customer, "return_url": return_url})
        return {"id": "bps_test", "url": self._portal_url}

    def list_invoices(self, *, customer: str, limit: int = 24) -> list[dict]:
        self.invoice_calls.append({"customer": customer, "limit": limit})
        return list(self._invoices)


class UnconfiguredStripe:
    """Simulates a StripeAdapter with no api_key — raises StripeNotConfiguredError."""

    def create_billing_portal_session(self, **_kwargs) -> dict:
        raise StripeNotConfiguredError("Stripe api_key not configured")

    def list_invoices(self, **_kwargs) -> list[dict]:
        raise StripeNotConfiguredError("Stripe api_key not configured — cannot list invoices")


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
def test_invoices_requires_bearer():
    """Missing Authorization header → 401."""
    c = _client(store=_store_with_customer())
    r = c.get("/billing/invoices")
    assert r.status_code == 401


# --------------------------------------------------------------------------- unconfigured Stripe → 503 (parity with portal-session)

@pytest.mark.unit
def test_invoices_503_when_stripe_unconfigured():
    """No api_key → StripeNotConfiguredError must surface as 503, never 500. Parity with portal."""
    store = _store_with_customer()
    c = _client(store=store, stripe=UnconfiguredStripe())

    r = c.get("/billing/invoices", headers=H)

    assert r.status_code == 503
    detail = r.json()["detail"]
    assert "billing" in detail.lower()


@pytest.mark.unit
def test_invoices_503_never_500_on_stripe_error():
    """Any Stripe-side exception (transient) must produce 503, not 500."""

    class FlakyStripe:
        def create_billing_portal_session(self, **_kwargs):
            raise RuntimeError("transient")

        def list_invoices(self, **_kwargs):
            raise RuntimeError("transient network failure")

    store = _store_with_customer()
    c = _client(store=store, stripe=FlakyStripe())

    r = c.get("/billing/invoices", headers=H)

    assert r.status_code == 503
    assert r.status_code != 500


# --------------------------------------------------------------------------- no customer mapping → empty list

@pytest.mark.unit
def test_invoices_empty_when_no_customer_mapping():
    """Tenant with no stripe_customer_id gets {invoices: []} — never invented invoices."""
    store = FakeAccountStore(
        accounts={"A": _Account("A", stripe_customer_id=None)},
    )
    c = _client(store=store)

    r = c.get("/billing/invoices", headers=H)

    assert r.status_code == 200
    body = r.json()
    assert body == {"invoices": []}


@pytest.mark.unit
def test_invoices_empty_when_no_account_row():
    """No account row at all (new / provisioning-failed) → {invoices: []}."""
    c = _client(store=FakeAccountStore(accounts={}))

    r = c.get("/billing/invoices", headers=H)

    assert r.status_code == 200
    assert r.json() == {"invoices": []}


@pytest.mark.unit
def test_invoices_empty_does_not_call_stripe():
    """When there is no customer, Stripe must not be called at all."""
    fake_stripe = FakeStripe(invoices=[_SAMPLE_INVOICE])
    store = FakeAccountStore(accounts={"A": _Account("A", stripe_customer_id=None)})
    c = _client(store=store, stripe=fake_stripe)

    c.get("/billing/invoices", headers=H)

    assert len(fake_stripe.invoice_calls) == 0


# --------------------------------------------------------------------------- tenant WITH invoices → normalized rows

@pytest.mark.unit
def test_invoices_happy_path_returns_normalized_rows():
    """Tenant with stripe_customer_id and invoices gets a normalized list back."""
    invoices = [_SAMPLE_INVOICE, {**_SAMPLE_INVOICE, "id": "in_test2", "number": "INV-0002"}]
    fake_stripe = FakeStripe(invoices=invoices)
    store = _store_with_customer(tenant_id="A", customer_id="cus_42")
    c = _client(store=store, stripe=fake_stripe)

    r = c.get("/billing/invoices", headers=H)

    assert r.status_code == 200
    body = r.json()
    assert "invoices" in body
    assert len(body["invoices"]) == 2
    first = body["invoices"][0]
    # All normalized keys present
    for key in ("id", "number", "amount_due", "amount_paid", "currency",
                "status", "created", "hosted_invoice_url", "invoice_pdf"):
        assert key in first, f"missing key: {key}"
    assert first["id"] == "in_test1"
    assert first["number"] == "INV-0001"
    assert first["amount_due"] == 9900
    assert first["amount_paid"] == 9900
    assert first["currency"] == "usd"
    assert first["status"] == "paid"
    assert first["created"] == 1700000000
    assert "stripe.com" in first["hosted_invoice_url"]


@pytest.mark.unit
def test_invoices_zero_invoices_for_real_customer():
    """A paid tenant whose Stripe account has no invoices yet gets an empty list (not an error)."""
    fake_stripe = FakeStripe(invoices=[])
    store = _store_with_customer(tenant_id="A", customer_id="cus_paid_but_no_inv")
    c = _client(store=store, stripe=fake_stripe)

    r = c.get("/billing/invoices", headers=H)

    assert r.status_code == 200
    assert r.json() == {"invoices": []}


@pytest.mark.unit
def test_invoices_passes_verified_customer_id_to_stripe():
    """The customer id sent to Stripe is the one we resolved server-side, capped at 24."""
    fake_stripe = FakeStripe(invoices=[_SAMPLE_INVOICE])
    store = _store_with_customer(tenant_id="A", customer_id="cus_server_resolved")
    c = _client(store=store, stripe=fake_stripe)

    c.get("/billing/invoices", headers=H)

    assert len(fake_stripe.invoice_calls) == 1
    call = fake_stripe.invoice_calls[0]
    assert call["customer"] == "cus_server_resolved"
    assert call["limit"] == 24


# --------------------------------------------------------------------------- THE TRUST RULE

@pytest.mark.unit
def test_invoices_tenant_from_claim_not_body():
    """Tenant identity comes ONLY from the verified claim — two tenants each get their own invoices."""
    inv_a = {**_SAMPLE_INVOICE, "id": "in_a", "number": "INV-A"}
    inv_b = {**_SAMPLE_INVOICE, "id": "in_b", "number": "INV-B"}

    class PerTenantStripe:
        """Returns different invoices based on which customer id is requested."""
        def create_billing_portal_session(self, **_kw):
            return {"id": "bps", "url": "https://billing.stripe.com/x"}

        def list_invoices(self, *, customer: str, limit: int = 24) -> list[dict]:
            if customer == "cus_A":
                return [inv_a]
            if customer == "cus_B":
                return [inv_b]
            return []

    store = FakeAccountStore(
        accounts={
            "A": _Account("A", stripe_customer_id="cus_A"),
            "B": _Account("B", stripe_customer_id="cus_B"),
        }
    )
    c = _client(store=store, stripe=PerTenantStripe())

    r_a = c.get("/billing/invoices", headers={"Authorization": "Bearer t-A"})
    r_b = c.get("/billing/invoices", headers={"Authorization": "Bearer t-B"})

    assert r_a.status_code == 200
    assert r_b.status_code == 200
    ids_a = [inv["id"] for inv in r_a.json()["invoices"]]
    ids_b = [inv["id"] for inv in r_b.json()["invoices"]]
    assert ids_a == ["in_a"]
    assert ids_b == ["in_b"]


@pytest.mark.unit
def test_invoices_body_customer_id_cannot_override_server_resolved():
    """A customer_id injected in the request body must NOT override what the server resolved.

    The route never reads a customer id from the body — it always goes through the verified
    claim -> account -> stripe_customer_id chain. So even if a client sends JSON with a
    different customer id, the server uses its own resolution (THE TRUST RULE).
    """
    fake_stripe = FakeStripe(invoices=[_SAMPLE_INVOICE])
    # Tenant "A" is mapped to cus_legitimate
    store = _store_with_customer(tenant_id="A", customer_id="cus_legitimate")
    c = _client(store=store, stripe=fake_stripe)

    # Client tries to pass a different customer id in the body — the route ignores it
    r = c.get(
        "/billing/invoices",
        headers=H,
        # GET body is technically allowed by HTTP but ignored here; similarly for query params
        params={"customer": "cus_attacker"},  # should be ignored
    )

    assert r.status_code == 200
    # Stripe was called with the SERVER-resolved customer, not the one from the request
    assert len(fake_stripe.invoice_calls) == 1
    assert fake_stripe.invoice_calls[0]["customer"] == "cus_legitimate"


# --------------------------------------------------------------------------- /api/ prefix alias

@pytest.mark.unit
def test_invoices_mounted_on_api_prefix_too():
    """Both /billing/invoices and /api/billing/invoices are mounted (not a 404)."""
    store = _store_with_customer()
    c = _client(store=store)

    r = c.get("/api/billing/invoices", headers=H)
    assert r.status_code != 404
