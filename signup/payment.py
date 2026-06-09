"""Payment — safely (Build Guide Phase 10, Step 54).

Trust the WEBHOOK, not the browser. Provisioning is triggered ONLY by a signature-verified
checkout.session.completed / invoice.paid webhook — never the client success redirect. An idempotency
key on the create call means a double-click never double-charges.
"""
from __future__ import annotations

from dataclasses import dataclass


class PaymentError(Exception):
    pass


@dataclass
class CheckoutResult:
    stripe_customer_id: str
    checkout_id: str


class PaymentService:
    def __init__(self, stripe, accounts, on_paid):
        self.stripe = stripe          # injected Stripe client (construct_event, customers, checkout)
        self.accounts = accounts      # AccountService (for store access)
        self.on_paid = on_paid        # callback(account) -> starts provisioning

    def start_checkout(self, account_id: str, plan: str, idempotency_key: str) -> CheckoutResult:
        acct = self.accounts.store.get(account_id)
        if not acct.may_pay:
            # VERIFY BEFORE PAY — refuse checkout until email + phone are verified.
            raise PaymentError("account not fully verified; cannot take payment")
        customer = self.stripe.create_customer(email=acct.email, idempotency_key=idempotency_key)
        acct.stripe_customer_id = customer["id"]
        self.accounts.store.update(acct)
        session = self.stripe.create_checkout_session(
            customer=customer["id"], plan=plan, client_reference_id=account_id,
            idempotency_key=idempotency_key,  # no double-charge on double-click
        )
        return CheckoutResult(customer["id"], session["id"])

    def handle_webhook(self, payload: bytes, sig_header: str, secret: str) -> dict:
        """The ONLY thing that triggers provisioning. Signature-verified + idempotent."""
        event = self.stripe.construct_event(payload, sig_header, secret)  # raises on bad signature
        if event["type"] not in ("checkout.session.completed", "invoice.paid"):
            return {"handled": False, "reason": f"ignored {event['type']}"}

        account_id = event["data"]["object"]["client_reference_id"]
        acct = self.accounts.store.get(account_id)
        from .accounts import State

        # Idempotent: a re-delivered webhook for an already-paid/provisioned account is a no-op.
        if acct.state in (State.PAID, State.PROVISIONING, State.ACTIVE):
            return {"handled": True, "idempotent": True, "account_id": account_id}

        acct.state = State.PAID
        self.accounts.store.update(acct)
        self.on_paid(acct)            # start provisioning (Step 55)
        return {"handled": True, "account_id": account_id}
