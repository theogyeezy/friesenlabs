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
    def __init__(self, stripe, accounts, on_paid, *, funnel=None, event_ledger=None):
        self.stripe = stripe          # injected Stripe client (construct_event, customers, checkout)
        self.accounts = accounts      # AccountService (for store access)
        self.on_paid = on_paid        # callback(account) -> starts provisioning
        self.funnel = funnel          # optional signup.funnel.Funnel; None = no-op (offline tests)
        # Optional cross-task idempotency ledger keyed by the Stripe EVENT id (duck type of
        # signup.store_pg.PgStripeEventLedger: is_handled / mark_handled). The in-memory account
        # state alone can't catch a re-delivery landing on a DIFFERENT Fargate task; the shared
        # ledger can. None = per-task account-state idempotency only (offline tests).
        self.event_ledger = event_ledger

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

        # Cross-task replay check FIRST — before any state is read or mutated. A re-delivered
        # event (same Stripe event id) already claimed by ANY task short-circuits here, so two
        # tasks with separate account stores still provision exactly once.
        event_id = str(event.get("id") or "") if hasattr(event, "get") else ""
        if self.event_ledger is not None and event_id and self.event_ledger.is_handled(event_id):
            return {"handled": True, "idempotent": True, "event_id": event_id}

        obj = event["data"]["object"]
        account_id = obj["client_reference_id"]
        acct = self.accounts.store.get(account_id)
        from .accounts import State

        # M6: a signed event whose client_reference_id matches no account is a handled no-op,
        # not an AttributeError -> opaque 400. (Stale/foreign reference, manual test event, etc.)
        if acct is None:
            return {"handled": False, "reason": "unknown account"}

        # Idempotent: a re-delivered webhook for an already-paid/provisioned account is a no-op.
        if acct.state in (State.PAID, State.PROVISIONING, State.ACTIVE):
            self._mark_handled(event_id, account_id)  # record it so the ledger check wins next time
            return {"handled": True, "idempotent": True, "account_id": account_id}

        acct.state = State.PAID
        self.accounts.store.update(acct)
        # H7: emit the revenue event SERVER-side (from the signed webhook) so ad-blockers can't
        # drop it. Optional/injected — None is a no-op so offline tests need no PostHog.
        if self.funnel is not None:
            plan = obj.get("plan") or (obj.get("metadata") or {}).get("plan") or "unknown"
            mrr = obj.get("mrr") or (obj.get("metadata") or {}).get("mrr") or 0.0
            self.funnel.revenue(account_id, plan, mrr)
        self.on_paid(acct)            # start provisioning (Step 55)
        # Mark AFTER the work: a crash mid-provision leaves the event unclaimed, so Stripe's
        # retry gets to run it again (provision itself is idempotent / parks on failure).
        self._mark_handled(event_id, account_id)
        return {"handled": True, "account_id": account_id}

    def _mark_handled(self, event_id: str, account_id: str | None) -> None:
        if self.event_ledger is not None and event_id:
            self.event_ledger.mark_handled(event_id, account_id)
