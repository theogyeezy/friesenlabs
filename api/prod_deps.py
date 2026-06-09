"""Production dependency wiring for the ASGI app.

Builds the signup/webhook deps so the routes are actually MOUNTED in the container (the audit found
they weren't). The external integrations (Cognito, Stripe, Resend, SNS) are live and need credentials —
they are wired here as explicit stubs that no-op where safe and raise a clear "needs configuration"
error where a real call is required, so:
  - the routes EXIST (POST /signup, /verify-*, /checkout, /webhooks/stripe),
  - account creation works in-memory,
  - operations that genuinely need live creds fail loudly instead of silently faking success.
Replace the stubs with real clients (BLOCKED: needs Nick) to make the flow live.
"""
from __future__ import annotations

import os
import uuid

from api.signup_routes import SignupDeps
from signup.accounts import AccountService
from signup.payment import PaymentService
from signup.provisioning import Provisioner


class _AccountStore:
    """In-memory account store (prod swaps in an Aurora-backed store under RLS)."""

    def __init__(self):
        self.rows: dict[str, object] = {}

    def get(self, account_id):
        return self.rows.get(account_id)

    def get_by_email(self, email):
        return next((a for a in self.rows.values() if getattr(a, "email", None) == email), None)

    def insert(self, acct):
        self.rows[acct.id] = acct

    def update(self, acct):
        self.rows[acct.id] = acct


class _StubCognito:
    def create_unconfirmed_user(self, email):
        return f"stub-sub-{uuid.uuid4()}"  # real Cognito user creation: needs Nick

    def set_tenant_id(self, sub, tenant_id):
        pass  # real Cognito admin update: needs Nick

    def confirm(self, sub):
        pass


class _Noop:
    """Email (Resend) / SMS (SNS) sender stub — real delivery needs Nick."""

    def __getattr__(self, _name):
        def _f(*a, **k):
            return None
        return _f


class _StubStripe:
    """Stripe stub — real payment/verification needs Nick + STRIPE_WEBHOOK_SECRET + the stripe lib."""

    def create_customer(self, **kw):
        raise NotImplementedError("Stripe not configured — needs Nick")

    def create_checkout_session(self, **kw):
        raise NotImplementedError("Stripe not configured — needs Nick")

    def construct_event(self, payload, sig, secret):
        raise NotImplementedError("Stripe not configured — needs Nick")


def build_signup_deps() -> SignupDeps:
    store = _AccountStore()
    accounts = AccountService(store, _StubCognito(), _Noop(), _Noop())
    provisioner = Provisioner(
        store=store, mint_tenant_id=lambda aid: str(uuid.uuid4()), db=_Noop(),
        anthropic_admin=_Noop(), secrets=_Noop(), cognito=_StubCognito(), cube=_Noop(),
        resend=_Noop(), agent_plane=_Noop(),
    )
    payment = PaymentService(_StubStripe(), accounts, on_paid=provisioner.provision)
    return SignupDeps(
        accounts=accounts,
        payment=payment,
        stripe_webhook_secret=os.environ.get("STRIPE_WEBHOOK_SECRET", ""),
        new_account_id=lambda: str(uuid.uuid4()),
        # No real email/SMS verifier wired yet → verification cannot complete until configured (safe).
        email_token_ok=lambda aid, token: False,
        sms_code_ok=lambda aid, code: False,
    )
