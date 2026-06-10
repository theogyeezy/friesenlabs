"""Integration: POST /signup/{account_id}/retry-provision at the HTTP layer (TODO INT/P2 closure).

Proves the layered gate order and the retry semantics end-to-end:
  * NOT ENABLED (retry_provision unwired — the SIGNUP_REAL_DEPS-off posture) -> 404, always;
  * enabled but NO claims verifier wired -> 403 internal-only (no admin-auth seam exists; the
    operator path is the direct Lambda 'retry' invoke) — and this fires BEFORE any account
    lookup, so an unauthenticated caller gets no account-id existence oracle;
  * a missing/invalid bearer -> the verifier's own 401;
  * a verified claim whose tenant does NOT match the account -> 403 (THE TRUST RULE: tenant
    only from the verified custom:tenant_id, and only for YOUR account);
  * a parked account with a matching claim retries to ACTIVE (idempotent full pipeline);
  * a non-parked account is a structured refusal / an ACTIVE one a skip — never a stealth
    re-provision; provisioning still fires ONLY off the signed Stripe webhook otherwise.
"""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.auth import TenantClaims
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.signup_routes import SignupDeps
from api.views import SavedViews
from signup.accounts import AccountService, State
from signup.payment import PaymentService
from signup.provisioning import Provisioner

# Reuse the fakes from the provisioning unit test (same as test_api_signup.py).
from tests.unit.test_signup_provisioning import (
    AnthropicAdmin, Cognito, DB, Email, Recorder, Secrets, Store,
)


class _ClaimsOk:
    """A claims gate that acts like api.auth.make_current_tenant after JWKS verification."""

    def __init__(self, tenant_id):
        self.tenant_id = tenant_id

    def __call__(self, request):
        return TenantClaims(tenant_id=self.tenant_id, sub="sub-1", email="u@x.com")


def _claims_401(request):
    raise HTTPException(status_code=401, detail="missing bearer token")


def _harness(*, enabled=True, claims=None, admin=None):
    """App + store + provisioner with the retry route wired the way prod_deps does."""
    store = Store()
    accounts = AccountService(store, Cognito(), Email(), Recorder())
    prov = Provisioner(store=store, mint_tenant_id=lambda aid: f"tenant-{aid}", db=DB(),
                       anthropic_admin=admin or AnthropicAdmin(), secrets=Secrets(),
                       cognito=Cognito(), cube=Recorder(), resend=Recorder(),
                       agent_plane=Recorder())
    payment = PaymentService(_NeverStripe(), accounts, on_paid=prov.provision)
    signup = SignupDeps(
        accounts=accounts, payment=payment, stripe_webhook_secret="whsec",
        new_account_id=lambda: "acct1",
        email_token_ok=lambda aid, t: False, sms_code_ok=lambda aid, c: False,
        retry_provision=(lambda aid: prov.retry(store.get(aid))) if enabled else None,
        claims_tenant=claims,
    )
    deps = ApiDeps(verifier=object(), greenlight=Greenlight(), saved_views=SavedViews(),
                   conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
                   executor=lambda a: None, signup=signup)
    return TestClient(create_app(deps)), store, prov


class _NeverStripe:
    def construct_event(self, payload, sig, secret):
        raise ValueError("not under test")


def _parked_account(store, prov, aid="a1"):
    """A fully-verified, PAID account parked by a transient step-2 failure (tenant minted)."""
    svc = AccountService(store, Cognito(), Email(), Recorder())
    svc.create(aid, "u@x.com", "+15555550100")
    svc.verify_email(aid, True)
    svc.verify_phone(aid, True)
    acct = store.get(aid)
    acct.state = State.PAID
    prov.admin.fail_on_key = True
    assert prov.provision(acct).ok is False
    prov.admin.fail_on_key = False        # the transient cause is fixed
    assert store.get(aid).state is State.PROVISIONING_FAILED
    return store.get(aid)


# ---------------------------------------------------------------- gate order
@pytest.mark.integration
def test_unwired_route_is_404_not_enabled():
    client, _, _ = _harness(enabled=False, claims=_ClaimsOk("tenant-a1"))
    r = client.post("/signup/a1/retry-provision")
    assert r.status_code == 404
    assert "not enabled" in r.json()["detail"]


@pytest.mark.integration
def test_no_claims_verifier_is_403_internal_only_even_for_unknown_accounts():
    client, _, _ = _harness(enabled=True, claims=None)
    # The internal-only refusal fires BEFORE the account lookup: same 403 for a real and a
    # phantom account — no unauthenticated existence oracle.
    for aid in ("a1", "ghost"):
        r = client.post(f"/signup/{aid}/retry-provision")
        assert r.status_code == 403
        assert "internal-only" in r.json()["detail"]


@pytest.mark.integration
def test_bad_token_is_the_verifiers_401():
    client, store, prov = _harness(enabled=True, claims=_claims_401)
    _parked_account(store, prov)
    r = client.post("/signup/a1/retry-provision")
    assert r.status_code == 401


@pytest.mark.integration
def test_tenant_mismatch_is_403():
    client, store, prov = _harness(enabled=True, claims=_ClaimsOk("tenant-SOMEONE-ELSE"))
    _parked_account(store, prov)   # parked with tenant-a1 minted
    r = client.post("/signup/a1/retry-provision")
    assert r.status_code == 403
    assert "does not match" in r.json()["detail"]
    assert store.get("a1").state is State.PROVISIONING_FAILED   # nothing re-ran


@pytest.mark.integration
def test_unknown_account_is_404_after_auth():
    client, _, _ = _harness(enabled=True, claims=_ClaimsOk("tenant-a1"))
    r = client.post("/signup/ghost/retry-provision")
    assert r.status_code == 404
    assert r.json()["detail"] == "no such account"


@pytest.mark.integration
def test_unminted_tenant_can_never_match_stays_operator_only():
    # An early-step park can leave tenant_id unset — the claim gate can then never pass:
    # those accounts are operator-retry-only (the direct Lambda invoke) by design.
    client, store, prov = _harness(enabled=True, claims=_ClaimsOk("tenant-a1"))
    svc = AccountService(store, Cognito(), Email(), Recorder())
    svc.create("a1", "u@x.com", "+15555550100")
    acct = store.get("a1")
    acct.state = State.PROVISIONING_FAILED   # parked pre-mint: no tenant_id
    r = client.post("/signup/a1/retry-provision")
    assert r.status_code == 403


# ---------------------------------------------------------------- retry semantics
@pytest.mark.integration
def test_parked_account_with_matching_claim_retries_to_active():
    client, store, prov = _harness(enabled=True, claims=_ClaimsOk("tenant-a1"))
    _parked_account(store, prov)
    r = client.post("/signup/a1/retry-provision")
    assert r.status_code == 200
    body = r.json()
    assert body["step"] == "retry" and body["status"] == "ok"
    assert body["state"] == "active" and body["tenant_id"] == "tenant-a1"
    assert store.get("a1").state is State.ACTIVE
    assert store.get("a1").tenant_id == "tenant-a1"   # the SAME tenant, never a second mint


@pytest.mark.integration
def test_active_account_is_a_skip_and_non_parked_a_refusal():
    client, store, prov = _harness(enabled=True, claims=_ClaimsOk("tenant-a1"))
    acct = _parked_account(store, prov)
    prov.provision(acct)                      # operator already fixed it out-of-band
    assert store.get("a1").state is State.ACTIVE
    r = client.post("/signup/a1/retry-provision")
    assert r.status_code == 200
    assert r.json()["status"] == "skipped" and r.json()["reason"] == "already_active"

    # A merely-PAID account (provisioning never failed) is refused, structured.
    store.get("a1").state = State.PAID
    r2 = client.post("/signup/a1/retry-provision")
    assert r2.status_code == 200
    assert r2.json()["status"] == "refused" and "paid" in r2.json()["reason"]
    assert store.get("a1").state is State.PAID   # untouched — no stealth re-provision