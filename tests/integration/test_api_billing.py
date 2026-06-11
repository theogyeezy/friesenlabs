"""Integration: authed self-service billing routes at the HTTP layer.

Proves: the portal session is claims-bound (401 without a token, tenant from the verified JWT only),
the customer is resolved server-side from the tenant->account mapping, a tenant with no Stripe
customer gets an honest 403, an unconfigured Stripe adapter degrades to 503 (never a 500), and the
GET /billing read surfaces the plan + billing status from the account row.
"""
import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.billing_routes import BillingDeps
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.views import SavedViews
from signup.accounts import Account, State
from signup.stripe_adapter import StripeNotConfiguredError


class FakeVerifier:
    """Verifies the literal token 'tok-<tenant>' into a claim set with that tenant id."""

    def verify(self, token):
        if not token.startswith("tok-"):
            raise ValueError("bad token")
        return {"custom:tenant_id": token[4:], "sub": "user-1", "email": "u@x.com"}


class AccountStore:
    """In-memory account store keyed by tenant_id (the billing resolver) + billing status meta."""

    def __init__(self, accounts):
        self.by_tenant = {a.tenant_id: a for a in accounts if a.tenant_id}
        self.billing = {}

    def get_by_tenant_id(self, tenant_id):
        return self.by_tenant.get(str(tenant_id))

    def get_billing_status(self, account_id):
        return self.billing.get(account_id)


class FakeStripe:
    def __init__(self, *, configured=True):
        self.configured = configured
        self.calls = []

    def create_billing_portal_session(self, *, customer, return_url):
        self.calls.append((customer, return_url))
        if not self.configured:
            raise StripeNotConfiguredError("no api key")
        return {"id": "bps_1", "url": f"https://billing.stripe.com/p/{customer}"}


def _client(*, accounts, configured=True, return_url="https://app.example/settings/billing"):
    store = AccountStore(accounts)
    stripe = FakeStripe(configured=configured)
    billing = BillingDeps(stripe=stripe, accounts_store=store,
                          return_url=return_url)
    deps = ApiDeps(verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
                   conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
                   executor=lambda a: None, billing=billing)
    return TestClient(create_app(deps)), store, stripe


def _acct(tenant_id="tenant-9", customer="cus_42", plan="team"):
    return Account(id="a1", email="u@x.com", phone="+1555", cognito_sub="s",
                   state=State.ACTIVE, email_verified=True, phone_verified=True,
                   stripe_customer_id=customer, tenant_id=tenant_id, meta={"plan": plan})


def _auth(tenant="tenant-9"):
    return {"Authorization": f"Bearer tok-{tenant}"}


@pytest.mark.integration
def test_portal_session_requires_auth():
    client, _, _stripe = _client(accounts=[_acct()])
    assert client.post("/billing/portal-session").status_code == 401


@pytest.mark.integration
def test_portal_session_returns_url_for_mapped_customer():
    client, _store, stripe = _client(accounts=[_acct()])
    r = client.post("/billing/portal-session", headers=_auth())
    assert r.status_code == 200
    assert r.json() == {"url": "https://billing.stripe.com/p/cus_42"}
    # The customer was resolved server-side; the operator return_url was passed through.
    assert stripe.calls == [("cus_42", "https://app.example/settings/billing")]


@pytest.mark.integration
def test_portal_session_403_when_no_customer_mapping():
    # A tenant whose account has no stripe_customer_id (internal-comp / never paid): honest 403,
    # never a fake portal and never a Stripe call.
    client, _store, stripe = _client(accounts=[_acct(customer=None)])
    r = client.post("/billing/portal-session", headers=_auth())
    assert r.status_code == 403
    assert "billing account" in r.json()["detail"].lower()
    assert stripe.calls == []


@pytest.mark.integration
def test_portal_session_403_when_tenant_unknown():
    # The verified tenant resolves to no account at all -> same honest 403, no Stripe call.
    client, _store, stripe = _client(accounts=[_acct(tenant_id="tenant-other")])
    r = client.post("/billing/portal-session", headers=_auth("tenant-9"))
    assert r.status_code == 403 and stripe.calls == []


@pytest.mark.integration
def test_portal_session_503_when_stripe_unconfigured():
    # An unconfigured Stripe adapter (no api key) must degrade to 503, never a 500.
    client, _store, _stripe = _client(accounts=[_acct()], configured=False)
    r = client.post("/billing/portal-session", headers=_auth())
    assert r.status_code == 503
    assert "API 503" not in r.json()["detail"]   # human copy, never a raw transport string


@pytest.mark.integration
def test_billing_state_reports_plan_and_status():
    client, store, _stripe = _client(accounts=[_acct(plan="scale")])
    store.billing["a1"] = {"status": "past_due", "reason": "customer.subscription.updated"}
    r = client.get("/billing", headers=_auth())
    assert r.status_code == 200
    assert r.json() == {"customer": True, "plan": "scale", "status": "past_due"}


@pytest.mark.integration
def test_billing_state_defaults_active_and_no_customer():
    client, _store, _stripe = _client(accounts=[_acct(customer=None, plan=None)])
    r = client.get("/billing", headers=_auth())
    assert r.json() == {"customer": False, "plan": None, "status": "active"}


@pytest.mark.integration
def test_billing_state_requires_auth():
    client, _store, _stripe = _client(accounts=[_acct()])
    assert client.get("/billing").status_code == 401
