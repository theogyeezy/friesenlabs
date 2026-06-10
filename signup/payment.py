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
        # signup.store_pg.PgStripeEventLedger: mark_handled — the atomic CLAIM — and release).
        # The in-memory account state alone can't catch a re-delivery landing on a DIFFERENT
        # Fargate task; the shared ledger can. None = per-task account-state idempotency only
        # (offline tests).
        self.event_ledger = event_ledger

    def start_checkout(self, account_id: str, plan: str, idempotency_key: str) -> CheckoutResult:
        acct = self.accounts.store.get(account_id)
        if not acct.may_pay:
            # VERIFY BEFORE PAY — refuse checkout until email + phone are verified.
            raise PaymentError("account not fully verified; cannot take payment")
        customer = self.stripe.create_customer(email=acct.email, idempotency_key=idempotency_key)
        acct.stripe_customer_id = customer["id"]
        self.accounts.store.update(acct)
        try:
            session = self.stripe.create_checkout_session(
                customer=customer["id"], plan=plan, client_reference_id=account_id,
                idempotency_key=idempotency_key,  # no double-charge on double-click
            )
        except ValueError as e:
            # An unknown/unconfigured plan (StripeAdapter raises ValueError when the plan has no
            # Price ID wired) is a client-fixable 400 (the route maps PaymentError), not an
            # opaque 500.
            raise PaymentError(str(e)) from e
        return CheckoutResult(customer["id"], session["id"])

    def handle_webhook(self, payload: bytes, sig_header: str, secret: str) -> dict:
        """The ONLY thing that triggers provisioning. Signature-verified + idempotent."""
        event = self.stripe.construct_event(payload, sig_header, secret)  # raises on bad signature
        if event["type"] not in ("checkout.session.completed", "invoice.paid"):
            return {"handled": False, "reason": f"ignored {event['type']}"}

        event_id = str(event.get("id") or "") if hasattr(event, "get") else ""
        obj = event["data"]["object"]
        account_id = obj["client_reference_id"]
        acct = self.accounts.store.get(account_id)
        from .accounts import State

        # M6: a signed event whose client_reference_id matches no account is a handled no-op,
        # not an AttributeError -> opaque 400. (Stale/foreign reference, manual test event, etc.)
        if acct is None:
            return {"handled": False, "reason": "unknown account"}

        # Account-state idempotency: a webhook (this event id or a DIFFERENT one — Stripe sends
        # both checkout.session.completed and invoice.paid) for an already-paid/provisioned
        # account is a no-op. Best-effort claim so the ledger short-circuits the replay too.
        if acct.state in (State.PAID, State.PROVISIONING, State.ACTIVE):
            self._claim(event_id, account_id)
            return {"handled": True, "idempotent": True, "account_id": account_id}

        # THE CLAIM — atomic, and BEFORE any state mutation or work. `mark_handled` is
        # INSERT .. ON CONFLICT (event_id) (PgStripeEventLedger): of N tasks racing the SAME
        # event id past the account-state check above, exactly ONE wins the insert and does the
        # work; every loser lands here and no-ops without touching its account store.
        # (The old shape — `is_handled` check, work, mark AFTER — let two tasks interleave past
        # the check and BOTH provision.)
        if not self._claim(event_id, account_id):
            return {"handled": True, "idempotent": True, "event_id": event_id}

        try:
            acct.state = State.PAID
            self.accounts.store.update(acct)
            # H7: emit the revenue event SERVER-side (from the signed webhook) so ad-blockers
            # can't drop it. Optional/injected — None is a no-op so offline tests need no PostHog.
            if self.funnel is not None:
                plan = obj.get("plan") or (obj.get("metadata") or {}).get("plan") or "unknown"
                mrr = obj.get("mrr") or (obj.get("metadata") or {}).get("mrr") or 0.0
                self.funnel.revenue(account_id, plan, mrr)
            self.on_paid(acct)        # start provisioning (Step 55)
        except Exception:
            # A FAILED attempt gives the claim back so Stripe's retry is not silently dropped.
            # How much the retry re-runs depends on where the failure landed: before the PAID
            # flip persisted, everything re-runs; after it, the account-state branch above
            # absorbs the retry as a safe no-op (no double charge, no double provision) — the
            # account is parked for operational recovery either way. NOTE the honest window:
            # if the PROCESS DIES between the claim committing and this release running, the
            # event stays claimed forever and Stripe's retry short-circuits — the account is
            # left in a pre-ACTIVE state with no tenant, and recovery is operational (re-fire /
            # sweeper), NOT automatic. That at-most-once trade is deliberate: the alternative
            # (mark after the work) re-ran on crash but let two live tasks provision the same
            # event concurrently.
            self._release(event_id)
            raise
        return {"handled": True, "account_id": account_id}

    def _claim(self, event_id: str, account_id: str | None) -> bool:
        """True = this call owns the event (or no shared ledger is configured / the event has
        no id — per-task account-state idempotency is then the only layer)."""
        if self.event_ledger is None or not event_id:
            return True
        return bool(self.event_ledger.mark_handled(event_id, account_id))

    def _release(self, event_id: str) -> None:
        if self.event_ledger is not None and event_id:
            self.event_ledger.release(event_id)
