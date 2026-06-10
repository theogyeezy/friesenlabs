"""Gated LIVE signup e2e — Stripe TEST mode end-to-end at the HTTP layer (TODO INT/P2).

Drives the full acquisition funnel against REAL providers in their sandbox/test modes:
signup -> signed email-token verify -> SMS OTP verify -> a REAL Stripe TEST-mode Checkout
Session (live network calls: Customer + checkout.Session) -> a SIGNED webhook (the same
``t=<ts>,v1=<hmac>`` scheme Stripe sends, verified by ``stripe.Webhook.construct_event``
against the TEST-mode signing secret) -> idempotent provisioning to ACTIVE.

GATING — every test in this file SKIPS cleanly unless ALL of:
  * the ``stripe`` package is installed (requirements-api.txt; NOT in the offline dev venv —
    ``pytest.importorskip`` keeps the default suite green without it);
  * ``STRIPE_TEST_SECRET_KEY``    — a Stripe TEST-MODE secret key. A key that is present but
                                    NOT test-mode (``sk_test_``/``rk_test_``) FAILS the run
                                    loudly — this file must never touch live-mode money.
  * ``STRIPE_TEST_WEBHOOK_SECRET`` — the TEST-mode webhook signing secret (``whsec_...``)
                                    used to sign the synthetic webhook deliveries below.

Optional sandbox flags (each swaps ONE provisioning seam from offline fake to a real
SANDBOX provider; everything else stays the offline fakes):
  * ``STRIPE_TEST_PRICE_ID``       — an existing TEST-mode recurring Price id; unset, the
                                    fixture creates a throwaway test-mode product + $1/mo price.
  * ``SIGNUP_E2E_COGNITO_POOL_ID`` — a SANDBOX Cognito user pool id: the real
                                    ``CognitoAdminClient`` then runs the admin create /
                                    set-tenant / confirm ops (needs AWS creds in the env).
  * ``SIGNUP_E2E_RESEND_API_KEY``  — a Resend API key: the REAL ``ResendEmailSender`` is wired
                                    but stays DRAFT-GATED (composes, never delivers).

NO LIVE SENDS — the draft-gate (CLAUDE.md hard constraint #2) stands in full: this module
never sets or honors ALLOW_REAL_SENDS (and fails loudly if the environment carries it as
"true"); every sender is constructed with ``allow_real_sends=False``, so email/SMS delivery is
composed-and-dropped even with a real Resend key supplied. The verification credentials are
therefore minted IN-PROCESS via the very same ``EmailTokenService`` / ``OtpService`` instances
the (draft-gated) senders would have delivered from — the real credential path, minus delivery.

CI: infra/REQUESTS.md REQ-007 asks Lane Nick for a ci.yml job that exports the two
``STRIPE_TEST_*`` GitHub secrets and runs exactly this file. Deploy invariance: the gating
names above are NEW, test-harness-only env — no live task injects them and nothing under
``api/`` or ``signup/`` reads them.
"""
# ruff: noqa: E402 — the repo imports below MUST come after pytest.importorskip("stripe")
import hashlib
import hmac
import json
import os
import time
import uuid

import pytest

stripe_lib = pytest.importorskip(
    "stripe", reason="the live signup e2e needs the `stripe` package (requirements-api.txt)"
)

from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.signup_routes import SignupDeps
from api.views import SavedViews
from signup.accounts import AccountService
from signup.cognito_admin import CognitoAdminClient
from signup.payment import PaymentService
from signup.provisioning import Provisioner
from signup.resend_sender import ResendEmailSender
from signup.stripe_adapter import StripeAdapter
from signup.tokens import EmailTokenService, OtpService

# The offline provisioning fakes are the default "sandbox providers" for the seams no live
# test-mode service exists for (Anthropic admin, Secrets Manager, tenant db, agent plane).
from tests.unit.test_signup_provisioning import (
    AnthropicAdmin, Cognito, DB, Recorder, Secrets, Store,
)

STRIPE_KEY = os.environ.get("STRIPE_TEST_SECRET_KEY", "")
WEBHOOK_SECRET = os.environ.get("STRIPE_TEST_WEBHOOK_SECRET", "")
PRICE_ID = os.environ.get("STRIPE_TEST_PRICE_ID", "")
COGNITO_POOL = os.environ.get("SIGNUP_E2E_COGNITO_POOL_ID", "")
RESEND_KEY = os.environ.get("SIGNUP_E2E_RESEND_API_KEY", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (STRIPE_KEY and WEBHOOK_SECRET),
        reason="set STRIPE_TEST_SECRET_KEY + STRIPE_TEST_WEBHOOK_SECRET (Stripe TEST mode) "
               "to run the live signup e2e",
    ),
]

# Per-run HMAC signing secret for the token/OTP services (test-harness only, never a deployed
# credential — the deployed secret is SIGNUP_TOKEN_SECRET_VALUE, untouched here).
SIGNING_SECRET = f"live-e2e-{uuid.uuid4()}"


@pytest.fixture(scope="module", autouse=True)
def _safety_gates():
    """Refuse loudly (never skip silently into danger) on a non-test key or a flipped send gate."""
    if not STRIPE_KEY.startswith(("sk_test_", "rk_test_")):
        pytest.fail(
            "STRIPE_TEST_SECRET_KEY is not a Stripe TEST-mode key (sk_test_/rk_test_) — "
            "refusing to run the e2e against anything that could be live mode"
        )
    if os.environ.get("ALLOW_REAL_SENDS", "").strip().lower() == "true":
        pytest.fail(
            "ALLOW_REAL_SENDS must stay unset for the live e2e — every sender here is "
            "draft-gated by design (CLAUDE.md hard constraint #2)"
        )


@pytest.fixture(scope="module")
def price_id():
    """STRIPE_TEST_PRICE_ID, or a throwaway test-mode recurring price (checkout is
    mode=subscription, so the price must be recurring)."""
    if PRICE_ID:
        return PRICE_ID
    product = stripe_lib.Product.create(
        api_key=STRIPE_KEY, name="uplift live-e2e throwaway (test mode)"
    )
    price = stripe_lib.Price.create(
        api_key=STRIPE_KEY, product=product["id"],
        unit_amount=100, currency="usd", recurring={"interval": "month"},
    )
    return price["id"]


class _TokenMailer:
    """AccountService's email seam over a DRAFT-GATED real sender: mints the REAL signed
    token (the same EmailTokenService the route verifies against) before the compose-and-drop."""

    def __init__(self, sender, tokens):
        self.sender = sender
        self.tokens = tokens

    def send_verification(self, email, account_id):
        return self.sender.send_verification(email, self.tokens.issue(str(account_id)))

    def send_welcome(self, email, tenant_id=None):
        return self.sender.send_welcome(email, tenant_id)


class Harness:
    """The HTTP app wired exactly like the offline integration test, except: REAL StripeAdapter
    (test-mode key), REAL token/OTP services, and — under the optional flags — a real sandbox
    Cognito pool and a draft-gated real Resend sender."""

    def __init__(self, price):
        self.store = Store()
        self.tokens = EmailTokenService(SIGNING_SECRET)
        self.otp = OtpService(SIGNING_SECRET)
        self.provisioned: list[str] = []
        self.account_id = f"e2e-{uuid.uuid4()}"

        cognito = (
            CognitoAdminClient(COGNITO_POOL) if COGNITO_POOL else Cognito()
        )
        if RESEND_KEY:
            # DRAFT-GATED on purpose: allow_real_sends=False composes + drops, never delivers.
            sender = ResendEmailSender(RESEND_KEY, "onboarding@resend.dev",
                                       allow_real_sends=False)
            mailer = _TokenMailer(sender, self.tokens)
        else:
            sender = mailer = Recorder()

        accounts = AccountService(self.store, cognito, mailer, Recorder())
        provisioner = Provisioner(
            store=self.store, mint_tenant_id=lambda aid: f"tenant-{aid}", db=DB(),
            anthropic_admin=AnthropicAdmin(), secrets=Secrets(), cognito=cognito,
            cube=Recorder(), resend=sender, agent_plane=Recorder(),
        )

        def on_paid(acct):
            self.provisioned.append(acct.id)
            provisioner.provision(acct)

        adapter = StripeAdapter(
            api_key=STRIPE_KEY, price_ids={"pro": price},
            success_url="https://example.com/billing/success",
            cancel_url="https://example.com/billing/cancel",
            stripe_module=stripe_lib,
        )
        payment = PaymentService(adapter, accounts, on_paid=on_paid)
        signup = SignupDeps(
            accounts=accounts, payment=payment, stripe_webhook_secret=WEBHOOK_SECRET,
            new_account_id=lambda: self.account_id,
            email_token_ok=self.tokens.verify, sms_code_ok=self.otp.verify,
        )
        deps = ApiDeps(verifier=object(), greenlight=Greenlight(), saved_views=SavedViews(),
                       conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
                       executor=lambda a: None, signup=signup)
        self.client = TestClient(create_app(deps))


@pytest.fixture(scope="module")
def harness(price_id):
    return Harness(price_id)


def _sign(payload: bytes, secret: str, ts: int | None = None) -> str:
    """Stripe's webhook signature scheme: HMAC-SHA256 over ``f"{ts}.{payload}"``."""
    ts = int(time.time()) if ts is None else ts
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def _event_payload(event_id: str, session: dict) -> bytes:
    return json.dumps({
        "id": event_id,
        "object": "event",
        "type": "checkout.session.completed",
        "data": {"object": session},
    }).encode()


def test_full_signup_to_active_against_stripe_test_mode(harness):
    client, aid = harness.client, harness.account_id

    # 1. signup
    email = f"e2e+{uuid.uuid4().hex[:12]}@example.com"
    r = client.post("/signup", json={"email": email, "phone": "+15555550100"})
    assert r.status_code == 200 and r.json()["state"] == "created"

    # VERIFY BEFORE PAY: checkout refused while unverified (and no Stripe call is made).
    assert client.post(f"/signup/{aid}/checkout", json={"plan": "pro"}).status_code == 400

    # 2. email-token verify — the REAL signed single-use token from the same service the
    #    (draft-gated) mailer embeds in the link.
    token = harness.tokens.issue(aid)
    r = client.post(f"/signup/{aid}/verify-email", json={"token": token})
    assert r.status_code == 200 and r.json()["email_verified"] is True
    # single-use: the same token never verifies twice (flip already happened; flag is sticky).
    assert harness.tokens.verify(aid, token) is False

    # 3. OTP verify — a REAL code minted by the same OtpService the route checks against.
    code = harness.otp.issue(aid)
    r = client.post(f"/signup/{aid}/verify-phone", json={"code": code})
    assert r.status_code == 200 and r.json()["phone_verified"] is True

    # 4. REAL Stripe TEST-mode checkout: a live test-mode Customer + hosted Checkout Session.
    r = client.post(f"/signup/{aid}/checkout", json={"plan": "pro"})
    assert r.status_code == 200
    body = r.json()
    assert body["checkout_id"].startswith("cs_")
    assert body["stripe_customer_id"].startswith("cus_")
    assert harness.provisioned == []   # creating the session NEVER provisions

    # 5. the SIGNED webhook — the ONLY provisioning trigger.
    payload = _event_payload(f"evt_e2e_{uuid.uuid4().hex}", {
        "id": body["checkout_id"],
        "object": "checkout.session",
        "client_reference_id": aid,
        "metadata": {"plan": "pro"},
    })

    # 5a. bad signature -> 400, nothing provisioned.
    bad = client.post("/webhooks/stripe", content=payload,
                      headers={"stripe-signature": _sign(payload, "whsec_wrong")})
    assert bad.status_code == 400 and harness.provisioned == []

    # 5b. stale timestamp (outside stripe's default 5-minute tolerance) -> 400 too.
    stale = _sign(payload, WEBHOOK_SECRET, ts=int(time.time()) - 3600)
    assert client.post("/webhooks/stripe", content=payload,
                       headers={"stripe-signature": stale}).status_code == 400
    assert harness.provisioned == []

    # 5c. the correctly signed delivery provisions to ACTIVE.
    good = _sign(payload, WEBHOOK_SECRET)
    r = client.post("/webhooks/stripe", content=payload, headers={"stripe-signature": good})
    assert r.status_code == 200 and r.json()["handled"] is True

    state = client.get(f"/signup/{aid}").json()
    assert state["state"] == "active"
    assert state["tenant_id"]          # minted at provisioning, never before
    assert harness.provisioned == [aid]

    # 5d. re-delivery of the same signed payload is an idempotent no-op.
    r2 = client.post("/webhooks/stripe", content=payload, headers={"stripe-signature": good})
    assert r2.status_code == 200 and r2.json().get("idempotent") is True
    assert harness.provisioned == [aid]   # provisioned exactly once


def test_signed_webhook_for_unknown_account_is_a_handled_noop(harness):
    # A correctly SIGNED event whose client_reference_id matches nothing must be absorbed
    # (stale/foreign reference, dashboard test event) — never a 500, never a provision.
    payload = _event_payload(f"evt_e2e_{uuid.uuid4().hex}", {
        "id": "cs_test_unknown",
        "object": "checkout.session",
        "client_reference_id": f"no-such-account-{uuid.uuid4()}",
    })
    r = harness.client.post("/webhooks/stripe", content=payload,
                            headers={"stripe-signature": _sign(payload, WEBHOOK_SECRET)})
    assert r.status_code == 200
    assert r.json() == {"handled": False, "reason": "unknown account"}
