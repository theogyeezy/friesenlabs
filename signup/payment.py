"""Payment — safely (Build Guide Phase 10, Step 54).

Trust the WEBHOOK, not the browser. Provisioning is triggered ONLY by a signature-verified
checkout.session.completed / invoice.paid webhook — never the client success redirect. An idempotency
key on the create call means a double-click never double-charges.

Account resolution per event type (the revenue-lane fix — Stripe INVOICES carry no
client_reference_id, so the old ``obj['client_reference_id']`` lookup silently no-op'd every
``invoice.paid``):
  * checkout.session.completed — ``client_reference_id`` (set at session create), falling back
    to the session ``metadata.signup_id`` stamped by StripeAdapter.create_checkout_session;
  * invoice.paid — the SUBSCRIPTION metadata stamped at session-create time
    (``subscription_data.metadata.signup_id``), which Stripe mirrors onto the invoice as
    ``subscription_details.metadata`` (older API shapes) / ``parent.subscription_details.metadata``
    (2025+ API shapes) / per-line ``lines.data[*].metadata``; final fallback is the stored
    ``stripe_customer_id`` mapping written by ``start_checkout``.

ONE additional, env-gated settlement path: ``internal_comp`` (TESTING-ONLY @internal-domain
bypass — shared/config.py SIGNUP_INTERNAL_BYPASS_DOMAINS, default EMPTY = off). It mints a
synthetic, clearly-labeled ``internal_comp:<account_id>`` event id and runs the SAME idempotent
ledger-claim + PAID-flip + on_paid path as the webhook — never a separate provisioning trigger.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# The clearly-labeled ledger prefix for the env-gated internal-domain settlement path (every
# synthetic event id in stripe_events starts with this — trivially greppable/auditable).
INTERNAL_COMP_EVENT_PREFIX = "internal_comp:"


class PaymentError(Exception):
    pass


@dataclass
class CheckoutResult:
    stripe_customer_id: str
    checkout_id: str
    # The Stripe-hosted Checkout page URL — returned by the route to the SPA so the browser is
    # actually sent to Stripe (None when the injected client supplies no url, e.g. old fakes).
    checkout_url: str | None = None


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
        # Stripe scopes idempotency keys to ONE endpoint: reusing the caller's key verbatim on
        # both calls 400s with idempotency_error on the second. Per-endpoint suffixes keep the
        # double-click guarantee (same client retry -> same suffixed key per call).
        customer = self.stripe.create_customer(
            email=acct.email, idempotency_key=f"{idempotency_key}:customer",
        )
        acct.stripe_customer_id = customer["id"]
        self.accounts.store.update(acct)
        try:
            session = self.stripe.create_checkout_session(
                customer=customer["id"], plan=plan, client_reference_id=account_id,
                idempotency_key=f"{idempotency_key}:checkout",  # no double-charge on double-click
            )
        except ValueError as e:
            # An unknown/unconfigured plan (StripeAdapter raises ValueError when the plan has no
            # Price ID wired) is a client-fixable 400 (the route maps PaymentError), not an
            # opaque 500.
            raise PaymentError(str(e)) from e
        # session.get: injected fakes may return only {"id": ...} — url is then honestly None.
        url = session.get("url") if hasattr(session, "get") else None
        return CheckoutResult(customer["id"], session["id"], url)

    def handle_webhook(self, payload: bytes, sig_header: str, secret: str) -> dict:
        """The ONLY thing that triggers provisioning. Signature-verified + idempotent."""
        event = self.stripe.construct_event(payload, sig_header, secret)  # raises on bad signature
        if event["type"] not in ("checkout.session.completed", "invoice.paid"):
            return {"handled": False, "reason": f"ignored {event['type']}"}

        event_id = str(event.get("id") or "") if hasattr(event, "get") else ""
        obj = event["data"]["object"]
        acct = self._resolve_account(event["type"], obj)

        # M6: a signed event that matches no account is a handled no-op, not an
        # AttributeError -> opaque 400. (Stale/foreign reference, manual test event, etc.)
        if acct is None:
            return {"handled": False, "reason": "unknown account"}
        return self._settle_paid(event_id, acct, obj)

    # ------------------------------------------------------------- account resolution
    def _resolve_account(self, event_type: str, obj):
        """Map a verified event object to the signup Account (None = unknown, handled no-op)."""
        # checkout.session.completed carries client_reference_id (set at session create); some
        # injected fakes put it on other event types too — honor it first wherever present.
        ref = obj.get("client_reference_id") if hasattr(obj, "get") else None
        if not ref:
            ref = self._signup_id_from_metadata(obj)
        if ref:
            acct = self.accounts.store.get(ref)
            if acct is not None:
                return acct
        if event_type == "invoice.paid":
            # Final fallback: the stripe_customer_id mapping start_checkout persisted.
            customer = obj.get("customer")
            if customer:
                return self._account_by_customer(str(customer))
        return None

    @staticmethod
    def _signup_id_from_metadata(obj) -> str | None:
        """Pull the signup_id stamped at session-create time, across real Stripe shapes.

        Invoices mirror SUBSCRIPTION metadata as ``subscription_details.metadata`` (classic
        shape) or ``parent.subscription_details.metadata`` (2025+ API shapes); line items carry
        their own ``metadata``. Checkout Sessions carry it as plain ``metadata``.
        """
        if not hasattr(obj, "get"):
            return None
        candidates = [
            (obj.get("metadata") or {}),
            ((obj.get("subscription_details") or {}).get("metadata") or {}),
            (((obj.get("parent") or {}).get("subscription_details") or {}).get("metadata") or {}),
        ]
        lines = ((obj.get("lines") or {}).get("data") or [])
        candidates.extend((line.get("metadata") or {}) for line in lines if hasattr(line, "get"))
        for meta in candidates:
            signup_id = meta.get("signup_id")
            if signup_id:
                return str(signup_id)
        return None

    def _account_by_customer(self, customer_id: str):
        """Find the account whose stored stripe_customer_id matches (start_checkout wrote it)."""
        getter = getattr(self.accounts.store, "get_by_stripe_customer_id", None)
        if callable(getter):
            return getter(customer_id)
        rows = getattr(self.accounts.store, "rows", None)  # in-memory fallback (tests/dev)
        if isinstance(rows, dict):
            for acct in rows.values():
                if getattr(acct, "stripe_customer_id", None) == customer_id:
                    return acct
        return None

    # ------------------------------------------------------------- settlement (shared core)
    def _settle_paid(self, event_id: str, acct, obj) -> dict:
        """The idempotent claim -> PAID -> on_paid core, shared by the signed webhook and the
        env-gated internal_comp path so the two can never drift."""
        from .accounts import State

        account_id = acct.id
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

    # ------------------------------------------------------------- internal comp (env-gated)
    def internal_comp(self, account_id: str, plan: str) -> dict:
        """TESTING-ONLY settlement for allow-listed internal domains — NO Stripe involved.

        The route gates this on the VERIFIED signup email's domain being in
        SIGNUP_INTERNAL_BYPASS_DOMAINS (default EMPTY = the path does not exist). Same
        guarantees as the webhook: verify-before-pay enforced, the synthetic event id
        (``internal_comp:<account_id>``) flows through the SAME atomic ledger claim, and a
        double-fire is idempotent (account-state branch + the deterministic event id). The
        ledger row is clearly labeled by the prefix; a structured log line records the act.
        """
        acct = self.accounts.store.get(account_id)
        if acct is None:
            raise PaymentError("no such account")
        if not acct.may_pay:
            # VERIFY BEFORE PAY holds for the bypass too.
            raise PaymentError("account not fully verified; cannot take payment")
        event_id = f"{INTERNAL_COMP_EVENT_PREFIX}{account_id}"
        log.info(
            "internal_comp settlement: account=%s email_domain=%s plan=%s event_id=%s "
            "(env-gated Stripe bypass — no charge, same idempotent provisioning path)",
            account_id, (acct.email or "").rsplit("@", 1)[-1], plan, event_id,
        )
        synthetic_obj = {"metadata": {"plan": plan, "internal_comp": "true", "mrr": 0.0}}
        result = self._settle_paid(event_id, acct, synthetic_obj)
        return {**result, "internal_comp": True}

    def _claim(self, event_id: str, account_id: str | None) -> bool:
        """True = this call owns the event (or no shared ledger is configured / the event has
        no id — per-task account-state idempotency is then the only layer)."""
        if self.event_ledger is None or not event_id:
            return True
        return bool(self.event_ledger.mark_handled(event_id, account_id))

    def _release(self, event_id: str) -> None:
        if self.event_ledger is not None and event_id:
            self.event_ledger.release(event_id)
