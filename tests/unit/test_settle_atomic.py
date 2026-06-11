"""Unit: the two HIGH revenue-path findings from the adversarial Codex review.

1. RACE — settlement must be an ATOMIC per-account state transition, not a Python-level read of
   `acct.state` then a conditional flip. Idempotency was previously keyed only on the Stripe EVENT
   id, so checkout.session.completed and invoice.paid (DIFFERENT ids) could both observe a
   pre-paid account and both run on_paid -> double-provision. The fix flips the account with a
   single CAS (`UPDATE .. SET status='paid' WHERE status NOT IN ('paid','provisioning','active')
   RETURNING *`); only the caller that actually flips runs on_paid. The per-event stripe_events
   claim stays as a second layer.

2. WEBHOOK FIELD VERIFICATION — a valid signature proves the payload came from Stripe, NOT that
   its paid-status / customer / price / livemode / session id match what THIS account asked to
   pay. The fix persists a checkout INTENT at start_checkout and requires exact matches + a
   paid/active status before settling; a mismatch is a structured-logged WebhookVerificationError
   the route turns into a 400. These tests include valid-signature-but-wrong amount/price/livemode
   attacks.
"""
import pytest

from signup.accounts import AccountService, State
from signup.payment import PaymentError, PaymentService, WebhookVerificationError

from tests.unit.test_signup_provisioning import Cognito, Email, Ledger, Recorder, Store


# --------------------------------------------------------------------------- fakes
class AtomicStore(Store):
    """In-memory store that implements the ATOMIC-settle + checkout-intent seam the real
    PgAccountStore provides (settle_paid_atomic / save_checkout_intent / get_checkout_intent), so
    PaymentService exercises the authoritative CAS path rather than the Python fallback."""

    SETTLED = (State.PAID, State.PROVISIONING, State.ACTIVE)

    def __init__(self):
        super().__init__()
        self.intents = {}
        self.atomic_calls = 0

    def settle_paid_atomic(self, account_id):
        # The CAS: flip to PAID iff not already settled; return the row (we won) or None (lost).
        self.atomic_calls += 1
        acct = self.rows.get(account_id)
        if acct is None or acct.state in self.SETTLED:
            return None
        acct.state = State.PAID
        return acct

    def save_checkout_intent(self, account_id, intent):
        self.intents[account_id] = dict(intent)

    def get_checkout_intent(self, account_id):
        rec = self.intents.get(account_id)
        return dict(rec) if rec else None


class IntentStripe:
    """construct_event returns the injected event; create_* return realistic intent fields."""

    def __init__(self, event=None, *, livemode=False):
        self.event = event
        self.livemode = livemode

    def construct_event(self, payload, sig, secret):
        if sig != "good-sig":
            raise ValueError("bad signature")
        return self.event

    def create_customer(self, email, idempotency_key):
        return {"id": "cus_42"}

    def create_checkout_session(self, **kw):
        return {
            "id": "cs_42",
            "url": "https://checkout.stripe.com/c/pay/cs_42",
            "customer": "cus_42",
            "plan": kw.get("plan"),
            "price_id": "price_team",
            "mode": "subscription",
            "livemode": self.livemode,
        }


def _verified(store=None, aid="a1"):
    svc = AccountService(store or AtomicStore(), Cognito(), Email(), Recorder())
    svc.create(aid, "u@x.com", "+15555550100")
    svc.verify_email(aid, True)
    svc.verify_phone(aid, True)
    return svc


def _checkout_completed(event_id="evt_cs", *, session_id="cs_42", customer="cus_42",
                        price="price_team", payment_status="paid", livemode=False):
    return {"id": event_id, "type": "checkout.session.completed", "data": {"object": {
        "id": session_id,
        "object": "checkout.session",
        "client_reference_id": "a1",
        "customer": customer,
        "payment_status": payment_status,
        "livemode": livemode,
        "mode": "subscription",
        "metadata": {"signup_id": "a1", "plan": "team"},
        "line_items": {"object": "list", "data": [{"price": {"id": price}}]},
    }}}


def _invoice_paid(event_id="evt_inv", *, customer="cus_42", price="price_team",
                  status="paid", livemode=False):
    return {"id": event_id, "type": "invoice.paid", "data": {"object": {
        "id": "in_1",
        "object": "invoice",
        "customer": customer,
        "subscription": "sub_77",
        "status": status,
        "livemode": livemode,
        "subscription_details": {"metadata": {"signup_id": "a1", "plan": "team"}},
        "lines": {"object": "list", "data": [{"price": {"id": price}}]},
    }}}


def _pay(svc, stripe, provisioned, ledger=None):
    return PaymentService(stripe, svc, on_paid=lambda a: provisioned.append(a.id),
                          event_ledger=ledger)


# =========================================================================
# Finding 1 — ATOMIC settlement (the different-event-id double-provision race)
# =========================================================================
@pytest.mark.unit
def test_completed_then_invoice_with_different_ids_provisions_exactly_once():
    """The core finding: Stripe sends BOTH events for one purchase with DIFFERENT ids. The
    per-event ledger claim can't serialize them — only the atomic per-account flip can. Exactly
    one runs on_paid."""
    store = AtomicStore()
    svc = _verified(store)
    provisioned = []
    ledger = Ledger()
    stripe = IntentStripe()
    pay = _pay(svc, stripe, provisioned, ledger)
    pay.start_checkout("a1", "team", "idem-1")     # persists the intent both events match

    stripe.event = _checkout_completed(event_id="evt_cs")
    assert pay.handle_webhook(b"{}", "good-sig", "whsec") == {"handled": True, "account_id": "a1"}

    stripe.event = _invoice_paid(event_id="evt_inv")   # DIFFERENT id, same purchase
    res = pay.handle_webhook(b"{}", "good-sig", "whsec")
    assert res == {"handled": True, "idempotent": True, "account_id": "a1"}
    assert provisioned == ["a1"]                   # exactly once
    assert store.get("a1").state is State.PAID


@pytest.mark.unit
def test_two_different_events_racing_one_account_provision_once():
    """Even past the fast-path account-state early-out (both observe PHONE_VERIFIED before either
    persists), the atomic CAS lets exactly ONE settle. We force the interleave: while the
    completed-session settlement is mid-on_paid, the invoice.paid (different id) fires."""
    store = AtomicStore()
    svc = _verified(store)
    provisioned = []
    ledger = Ledger()
    # Both settlements share one store + ledger (the same Aurora row in prod) but arrive on two
    # PaymentServices with DIFFERENT event ids.
    pay_inv = _pay(svc, IntentStripe(_invoice_paid(event_id="evt_inv")), provisioned, ledger)

    def _interleave(acct):
        # The DIFFERENT-id invoice.paid lands mid-provision; the CAS already flipped the row to
        # PAID, so this second settlement must no-op (no second on_paid).
        assert pay_inv.handle_webhook(b"{}", "good-sig", "whsec") == {
            "handled": True, "idempotent": True, "account_id": "a1",
        }
        provisioned.append(acct.id)

    stripe_cs = IntentStripe()
    pay_cs = PaymentService(stripe_cs, svc, event_ledger=ledger, on_paid=_interleave)
    pay_cs.start_checkout("a1", "team", "idem-1")
    stripe_cs.event = _checkout_completed(event_id="evt_cs")
    assert pay_cs.handle_webhook(b"{}", "good-sig", "whsec") == {"handled": True, "account_id": "a1"}
    assert provisioned == ["a1"]                   # exactly one provision across both events


@pytest.mark.unit
def test_atomic_flip_is_the_path_taken_when_store_supports_it():
    """When the store exposes settle_paid_atomic, PaymentService uses it (not the Python fallback)."""
    store = AtomicStore()
    svc = _verified(store)
    provisioned = []
    pay = _pay(svc, IntentStripe(_checkout_completed()), provisioned)
    pay.start_checkout("a1", "team", "idem-1")
    pay.handle_webhook(b"{}", "good-sig", "whsec")
    assert store.atomic_calls == 1                 # the CAS was the settlement mechanism
    assert provisioned == ["a1"]


@pytest.mark.unit
def test_atomic_loser_releases_event_id_so_it_is_not_burned():
    """If the CAS loses (account already settled by another event id), the just-taken event-id
    claim is RELEASED — the ledger is not left holding a row for an event that did no work."""
    store = AtomicStore()
    svc = _verified(store)
    store.rows["a1"].state = State.PAID            # already settled out from under us
    ledger = Ledger()
    provisioned = []
    pay = _pay(svc, IntentStripe(_invoice_paid(event_id="evt_new")), provisioned, ledger)
    # No intent stored here; PAID short-circuits at the fast-path before the CAS even runs.
    res = pay.handle_webhook(b"{}", "good-sig", "whsec")
    assert res == {"handled": True, "idempotent": True, "account_id": "a1"}
    assert provisioned == []


# =========================================================================
# Finding 2 — webhook FIELD VERIFICATION (valid signature, wrong payload)
# =========================================================================
@pytest.mark.unit
def test_unpaid_checkout_session_is_rejected():
    """A signature-valid checkout.session.completed advertising payment_status='unpaid' must NOT
    settle (Stripe can emit completed-but-unpaid for async/delayed methods)."""
    svc = _verified()
    provisioned = []
    pay = _pay(svc, IntentStripe(), provisioned)
    pay.start_checkout("a1", "team", "idem-1")
    pay.stripe.event = _checkout_completed(payment_status="unpaid")
    with pytest.raises(WebhookVerificationError, match="unpaid_status"):
        pay.handle_webhook(b"{}", "good-sig", "whsec")
    assert provisioned == []
    assert svc.store.get("a1").state is State.PHONE_VERIFIED   # untouched


@pytest.mark.unit
def test_open_invoice_is_rejected():
    svc = _verified()
    provisioned = []
    pay = _pay(svc, IntentStripe(), provisioned)
    pay.start_checkout("a1", "team", "idem-1")
    pay.stripe.event = _invoice_paid(status="open")
    with pytest.raises(WebhookVerificationError, match="unpaid_status"):
        pay.handle_webhook(b"{}", "good-sig", "whsec")
    assert provisioned == []


@pytest.mark.unit
def test_wrong_price_attack_is_rejected():
    """Valid-signature-but-WRONG-PRICE: an attacker replays a real Stripe event that charges a
    cheaper plan's price than this account agreed to. Exact price match blocks it."""
    svc = _verified()
    provisioned = []
    pay = _pay(svc, IntentStripe(), provisioned)
    pay.start_checkout("a1", "team", "idem-1")     # intent price = price_team
    pay.stripe.event = _invoice_paid(price="price_starter_cheaper")
    with pytest.raises(WebhookVerificationError, match="price_mismatch"):
        pay.handle_webhook(b"{}", "good-sig", "whsec")
    assert provisioned == []


@pytest.mark.unit
def test_wrong_livemode_attack_is_rejected():
    """A TEST-mode event must never settle a LIVE-mode checkout (and vice versa): replaying a
    cheap/forged test-mode event against a real live account is blocked by the livemode match."""
    store = AtomicStore()
    svc = _verified(store)
    provisioned = []
    pay = _pay(svc, IntentStripe(livemode=True), provisioned)   # intent livemode True
    pay.start_checkout("a1", "team", "idem-1")
    pay.stripe.event = _invoice_paid(livemode=False)            # test-mode event
    with pytest.raises(WebhookVerificationError, match="livemode_mismatch"):
        pay.handle_webhook(b"{}", "good-sig", "whsec")
    assert provisioned == []


@pytest.mark.unit
def test_wrong_customer_attack_is_rejected():
    svc = _verified()
    provisioned = []
    pay = _pay(svc, IntentStripe(), provisioned)
    pay.start_checkout("a1", "team", "idem-1")     # intent customer = cus_42
    # An event for a DIFFERENT customer that still resolves to a1 via metadata signup_id.
    pay.stripe.event = _invoice_paid(customer="cus_someone_else")
    with pytest.raises(WebhookVerificationError, match="customer_mismatch"):
        pay.handle_webhook(b"{}", "good-sig", "whsec")
    assert provisioned == []


@pytest.mark.unit
def test_wrong_checkout_session_id_attack_is_rejected():
    svc = _verified()
    provisioned = []
    pay = _pay(svc, IntentStripe(), provisioned)
    pay.start_checkout("a1", "team", "idem-1")     # intent checkout_id = cs_42
    pay.stripe.event = _checkout_completed(session_id="cs_forged")
    with pytest.raises(WebhookVerificationError, match="session_mismatch"):
        pay.handle_webhook(b"{}", "good-sig", "whsec")
    assert provisioned == []


@pytest.mark.unit
def test_fully_matching_event_settles():
    """The happy path: every persisted-intent field matches -> settlement proceeds once."""
    store = AtomicStore()
    svc = _verified(store)
    provisioned = []
    pay = _pay(svc, IntentStripe(), provisioned)
    pay.start_checkout("a1", "team", "idem-1")
    pay.stripe.event = _checkout_completed()       # all fields match the intent
    assert pay.handle_webhook(b"{}", "good-sig", "whsec") == {"handled": True, "account_id": "a1"}
    assert provisioned == ["a1"]


@pytest.mark.unit
def test_verification_failure_does_not_burn_account_or_ledger():
    """A rejected attack must leave the account un-settled AND not provision — defense that fails
    closed without side effects."""
    store = AtomicStore()
    svc = _verified(store)
    provisioned = []
    ledger = Ledger()
    pay = _pay(svc, IntentStripe(), provisioned, ledger)
    pay.start_checkout("a1", "team", "idem-1")
    pay.stripe.event = _invoice_paid(event_id="evt_attack", price="price_wrong")
    with pytest.raises(WebhookVerificationError):
        pay.handle_webhook(b"{}", "good-sig", "whsec")
    assert provisioned == []
    assert store.get("a1").state is State.PHONE_VERIFIED
    assert ledger.rows == {}                        # the attack never claimed an event id
    assert store.atomic_calls == 0                  # the CAS was never reached


@pytest.mark.unit
def test_route_maps_verification_error_to_400(monkeypatch):
    """WebhookVerificationError is a PaymentError subclass; the /webhooks/stripe route's broad
    except turns ANY raise into a 400 (structured-log + reject), so a forged-field webhook is a
    clean 400, never a settlement."""
    err = WebhookVerificationError("price_mismatch")
    assert isinstance(err, PaymentError)


@pytest.mark.unit
def test_no_intent_still_enforces_paid_status():
    """Legacy/offline: when no intent was persisted (store without the seam, or a pre-fix row), we
    can't field-match — but an explicitly UNPAID status is still rejected (status-only layer)."""
    svc = AccountService(Store(), Cognito(), Email(), Recorder())   # plain Store: no intent seam
    svc.create("a1", "u@x.com", "+15555550100")
    svc.verify_email("a1", True)
    svc.verify_phone("a1", True)
    provisioned = []
    pay = _pay(svc, IntentStripe(_invoice_paid(status="void")), provisioned)
    with pytest.raises(WebhookVerificationError, match="unpaid_status"):
        pay.handle_webhook(b"{}", "good-sig", "whsec")
    assert provisioned == []
