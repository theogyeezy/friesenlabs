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

# Settled values for the two status fields Stripe uses. checkout.session.completed carries
# `payment_status` ∈ {paid, unpaid, no_payment_required}; invoice.paid carries `status` ∈
# {paid, open, void, draft, uncollectible}. Only these count as actually-paid; anything else
# (an `unpaid` session, an `open` invoice) must NOT settle.
_PAID_STATUSES = frozenset({"paid", "no_payment_required", "active"})


class PaymentError(Exception):
    pass


class WebhookVerificationError(PaymentError):
    """A signature-valid webhook whose VERIFIED FIELDS (paid status / customer / price / livemode /
    session id) do not match what we asked for at checkout-start. The route turns this into a 400;
    a structured warn line records the attack/misconfig for audit."""


def _field(obj, key, default=None):
    """Read ``obj[key]`` across BOTH plain dicts (injected fakes) and stripe's StripeObject.

    Current stripe libs route attribute access through ``__getattr__`` and expose NO dict-style
    ``.get`` — ``obj.get("x")`` raises ``AttributeError: get`` (caught live by the main-only
    live-signup-e2e job), so the old ``hasattr(obj, "get")`` guards silently disabled account
    resolution on REAL webhook events. Index access is the one protocol both shapes share.
    """
    try:
        val = obj[key]
    except (KeyError, TypeError, IndexError):
        return default
    return default if val is None else val


def _prices_in(obj) -> set[str]:
    """Collect every Stripe Price id referenced by a checkout/invoice object, across shapes.

    Checkout Sessions: line_items are not expanded by default, so the price often rides only in
    the persisted intent (we compare against that). Invoices: each ``lines.data[*].price`` (or
    ``price.id``). Returns a set of price-id strings (empty when none are present in the payload —
    then there is nothing to contradict and the price check is skipped)."""
    prices: set[str] = set()

    def _add(price):
        if isinstance(price, str):
            prices.add(price)
        elif price is not None:
            pid = _field(price, "id")
            if pid:
                prices.add(str(pid))

    # invoice / session line items
    for line in _field(_field(obj, "lines", {}), "data", []) or []:
        _add(_field(line, "price"))
    for item in _field(_field(obj, "line_items", {}), "data", []) or []:
        _add(_field(_field(item, "price"), "id") or _field(item, "price"))
    # a top-level price (rare, but cheap to honor)
    _add(_field(obj, "price"))
    return prices


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
        # injected fakes may return only {"id": ...} — url is then honestly None.
        url = _field(session, "url")
        # Persist the SERVER-known checkout INTENT (session id, customer, price, plan, livemode) so
        # the later signed webhook can be verified field-by-field against it. A valid signature only
        # proves the payload came from Stripe — NOT that its customer/price/livemode/amount match
        # what THIS account asked to pay. Best-effort + additive: stores without the method (the
        # in-memory test fake) simply skip persistence and fall back to status-only verification.
        self._save_intent(account_id, {
            "checkout_id": session["id"],
            "customer": _field(session, "customer", customer["id"]),
            "plan": plan,
            "price_id": _field(session, "price_id"),
            "mode": _field(session, "mode", "subscription"),
            "livemode": bool(_field(session, "livemode", False)),
        })
        return CheckoutResult(customer["id"], session["id"], url)

    def _save_intent(self, account_id: str, intent: dict) -> None:
        saver = getattr(self.accounts.store, "save_checkout_intent", None)
        if callable(saver):
            saver(account_id, intent)

    def _get_intent(self, account_id: str) -> dict | None:
        getter = getattr(self.accounts.store, "get_checkout_intent", None)
        if callable(getter):
            return getter(account_id)
        return None

    def handle_webhook(self, payload: bytes, sig_header: str, secret: str) -> dict:
        """The ONLY thing that triggers provisioning. Signature-verified + idempotent."""
        event = self.stripe.construct_event(payload, sig_header, secret)  # raises on bad signature
        if event["type"] not in ("checkout.session.completed", "invoice.paid"):
            return {"handled": False, "reason": f"ignored {event['type']}"}

        event_id = str(_field(event, "id") or "")
        obj = event["data"]["object"]
        acct = self._resolve_account(event["type"], obj)

        # M6: a signed event that matches no account is a handled no-op, not an
        # AttributeError -> opaque 400. (Stale/foreign reference, manual test event, etc.)
        if acct is None:
            return {"handled": False, "reason": "unknown account"}

        # FIELD VERIFICATION (before any settlement): a valid signature proves the payload came
        # from Stripe, NOT that its paid-status / customer / price / livemode / session id match
        # what THIS account asked to pay. Mismatch -> structured warn + WebhookVerificationError
        # (the route maps it to 400). Caught the same way the existing live-e2e job catches shape
        # bugs — and the unit tests below exercise a valid-signature-but-wrong-amount/price/livemode
        # attack.
        self._verify_webhook_fields(event["type"], acct, obj)
        return self._settle_paid(event_id, acct, obj)

    # --------------------------------------------------------- field verification
    def _verify_webhook_fields(self, event_type: str, acct, obj) -> None:
        """Reject a signature-valid event whose verified fields contradict the stored intent.

        Two layers, both fail CLOSED on contradiction:
          1. PAID STATUS — the object must NOT advertise an unpaid/failed status. checkout
             sessions carry ``payment_status`` ("paid"/"unpaid"/"no_payment_required"); invoices
             carry ``status`` ("paid"/"open"/"void"/...). If the field is present it must be a
             settled value; absent = nothing to contradict (older shapes / injected fakes).
          2. INTENT MATCH — when a checkout intent was persisted at start_checkout, the event's
             customer, price id, livemode (and, for checkout sessions, the session id) must match
             it EXACTLY. This is what stops a valid-signature-but-wrong-amount/price/livemode
             forge: an attacker replaying a real but DIFFERENT Stripe event (another tenant's
             cheaper plan, a test-mode event against a live account) is rejected here."""
        # --- layer 1: positive paid/active status -------------------------------------------
        status = _field(obj, "payment_status") or _field(obj, "status")
        if status is not None and str(status).lower() not in _PAID_STATUSES:
            self._reject(acct, "unpaid_status", got=status, event_type=event_type)

        # --- layer 2: exact match against the persisted intent -------------------------------
        intent = self._get_intent(acct.id)
        if not intent:
            return  # no stored intent (offline / legacy / pre-fix) -> status-only verification

        # livemode: a test-mode event must never settle a live-mode checkout (and vice versa).
        ev_livemode = _field(obj, "livemode")
        if ev_livemode is not None and bool(ev_livemode) != bool(intent.get("livemode")):
            self._reject(acct, "livemode_mismatch",
                         got=bool(ev_livemode), want=bool(intent.get("livemode")),
                         event_type=event_type)

        # customer: the event must be for the customer we created at checkout-start.
        ev_customer = _field(obj, "customer")
        want_customer = intent.get("customer")
        if ev_customer is not None and want_customer and str(ev_customer) != str(want_customer):
            self._reject(acct, "customer_mismatch", got=ev_customer, want=want_customer,
                         event_type=event_type)

        # price: the event must charge the price we asked for (cross-plan downgrade forge guard).
        want_price = intent.get("price_id")
        if want_price:
            ev_prices = _prices_in(obj)
            if ev_prices and str(want_price) not in ev_prices:
                self._reject(acct, "price_mismatch", got=sorted(ev_prices), want=want_price,
                             event_type=event_type)

        # checkout session id: a completed-session event must be the SESSION we created.
        if event_type == "checkout.session.completed":
            ev_session = _field(obj, "id")
            want_session = intent.get("checkout_id")
            if ev_session is not None and want_session and str(ev_session) != str(want_session):
                self._reject(acct, "session_mismatch", got=ev_session, want=want_session,
                             event_type=event_type)

    def _reject(self, acct, reason: str, *, event_type: str, got=None, want=None) -> None:
        log.warning(
            "stripe webhook REJECTED (field verification): reason=%s account=%s event_type=%s "
            "got=%r want=%r — signature was valid but the payload contradicts the checkout intent",
            reason, getattr(acct, "id", None), event_type, got, want,
        )
        raise WebhookVerificationError(f"webhook field verification failed: {reason}")

    # ------------------------------------------------------------- account resolution
    def _resolve_account(self, event_type: str, obj):
        """Map a verified event object to the signup Account (None = unknown, handled no-op)."""
        # checkout.session.completed carries client_reference_id (set at session create); some
        # injected fakes put it on other event types too — honor it first wherever present.
        ref = _field(obj, "client_reference_id")
        if not ref:
            ref = self._signup_id_from_metadata(obj)
        if ref:
            acct = self.accounts.store.get(ref)
            if acct is not None:
                return acct
        if event_type == "invoice.paid":
            # Final fallback: the stripe_customer_id mapping start_checkout persisted.
            customer = _field(obj, "customer")
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
        candidates = [
            _field(obj, "metadata", {}),
            _field(_field(obj, "subscription_details", {}), "metadata", {}),
            _field(_field(_field(obj, "parent", {}), "subscription_details", {}), "metadata", {}),
        ]
        lines = _field(_field(obj, "lines", {}), "data", [])
        candidates.extend(_field(line, "metadata", {}) for line in lines)
        for meta in candidates:
            signup_id = _field(meta, "signup_id")
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
        """The idempotent claim -> atomic PAID flip -> on_paid core, shared by the signed webhook
        and the env-gated internal_comp path so the two can never drift.

        TWO independent idempotency layers, in this order:
          1. the per-EVENT-id stripe_events claim (``mark_handled``) — serializes a re-delivery of
             the SAME event id across tasks/restarts;
          2. the per-ACCOUNT atomic state transition (``settle_paid_atomic`` —
             ``UPDATE .. SET status='paid' WHERE status NOT IN ('paid','provisioning','active')``)
             — serializes the DIFFERENT-event-id race: Stripe sends BOTH
             checkout.session.completed and invoice.paid for one purchase, with different ids, so
             the per-event claim alone cannot stop them both reaching here and double-provisioning.
             ONLY the caller that actually flips the row runs on_paid.
        """
        from .accounts import State

        account_id = acct.id
        # Fast-path account-state idempotency: an event (this id or a DIFFERENT one) for an
        # already-paid/provisioned account is a no-op. Best-effort claim so the ledger short-
        # circuits the replay too. (The atomic flip below is the AUTHORITATIVE guard — this is a
        # cheap early-out that avoids burning a ledger row when the in-memory view already shows
        # the account settled.)
        if acct.state in (State.PAID, State.PROVISIONING, State.ACTIVE):
            self._claim(event_id, account_id)
            return {"handled": True, "idempotent": True, "account_id": account_id}

        # LAYER 1 — THE EVENT-ID CLAIM, atomic and BEFORE any state mutation or work.
        # `mark_handled` is INSERT .. ON CONFLICT (event_id) (PgStripeEventLedger): of N tasks
        # racing the SAME event id past the fast-path above, exactly ONE wins the insert; every
        # loser lands here and no-ops without touching its account store. (The old shape —
        # `is_handled` check, work, mark AFTER — let two tasks interleave past the check and BOTH
        # provision.)
        if not self._claim(event_id, account_id):
            return {"handled": True, "idempotent": True, "event_id": event_id}

        try:
            # LAYER 2 — the atomic per-account PAID flip. This is the authoritative guard against
            # the DIFFERENT-event-id double-fire: even if checkout.session.completed and
            # invoice.paid both pass the fast-path and each win their own (different) event-id
            # claim, only ONE wins this CAS; the loser sees `flipped is None` and no-ops without
            # running on_paid.
            flipped = self._atomic_flip_paid(acct)
            if flipped is None:
                self._release(event_id)   # we didn't settle -> don't burn the event id
                return {"handled": True, "idempotent": True, "account_id": account_id}
            acct = flipped                # the authoritative post-flip row
            # H7: emit the revenue event SERVER-side (from the signed webhook) so ad-blockers
            # can't drop it. Optional/injected — None is a no-op so offline tests need no PostHog.
            if self.funnel is not None:
                plan = _field(obj, "plan") or _field(_field(obj, "metadata", {}), "plan") or "unknown"
                mrr = _field(obj, "mrr") or _field(_field(obj, "metadata", {}), "mrr") or 0.0
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

    def _atomic_flip_paid(self, acct):
        """Atomically flip the account to PAID iff it is not already settled; return the post-flip
        Account (this caller won) or None (someone else already settled — idempotent no-op).

        Prefers the store's single-statement CAS (``settle_paid_atomic`` — the real Aurora guard);
        falls back to an in-process compare-and-set for the in-memory test fake (same semantics:
        flip only from a NOT-yet-settled state). The fallback is not concurrency-safe on its own,
        but the in-memory store is single-process and the event-id ledger already serializes the
        same-id cross-task case it can't see."""
        from .accounts import State

        cas = getattr(self.accounts.store, "settle_paid_atomic", None)
        if callable(cas):
            return cas(acct.id)
        # In-memory fallback CAS.
        current = self.accounts.store.get(acct.id) or acct
        if current.state in (State.PAID, State.PROVISIONING, State.ACTIVE):
            return None
        current.state = State.PAID
        self.accounts.store.update(current)
        return current

    def _claim(self, event_id: str, account_id: str | None) -> bool:
        """True = this call owns the event (or no shared ledger is configured / the event has
        no id — per-task account-state idempotency is then the only layer)."""
        if self.event_ledger is None or not event_id:
            return True
        return bool(self.event_ledger.mark_handled(event_id, account_id))

    def _release(self, event_id: str) -> None:
        if self.event_ledger is not None and event_id:
            self.event_ledger.release(event_id)
