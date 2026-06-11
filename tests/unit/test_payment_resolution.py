"""Unit: webhook account RESOLUTION (the invoice.paid fix) + the internal_comp bypass core.

Stripe INVOICES do not carry client_reference_id — the old handler read it anyway and silently
no-op'd every invoice.paid. These tests pin the real resolution order against REALISTIC payload
shapes: subscription metadata mirrored onto the invoice (classic `subscription_details.metadata`
AND the 2025+ `parent.subscription_details.metadata`), per-line metadata, and the stored
stripe_customer_id mapping as the final fallback.
"""
import pytest

from signup.accounts import AccountService, State
from signup.payment import INTERNAL_COMP_EVENT_PREFIX, PaymentError, PaymentService

from tests.unit.test_signup_provisioning import Cognito, Email, Ledger, Recorder, Store


class StripeSigned:
    """construct_event fake: verifies sig == 'good-sig', returns the injected event."""

    def __init__(self, event):
        self.event = event

    def construct_event(self, payload, sig, secret):
        if sig != "good-sig":
            raise ValueError("bad signature")
        return self.event

    def create_customer(self, email, idempotency_key):
        return {"id": "cus_42"}

    def create_checkout_session(self, **kw):
        return {"id": "cs_42", "url": "https://checkout.stripe.com/c/pay/cs_42"}


def _verified_service(aid="a1"):
    svc = AccountService(Store(), Cognito(), Email(), Recorder())
    svc.create(aid, "u@x.com", "+15555550100")
    svc.verify_email(aid, True)
    svc.verify_phone(aid, True)
    return svc


def _invoice_paid(event_id="evt_inv_1", **obj_overrides):
    """A REALISTIC invoice.paid object: customer/subscription ids, lines — and NO
    client_reference_id (invoices never carry it)."""
    obj = {
        "id": "in_1GZ",
        "object": "invoice",
        "customer": "cus_42",
        "subscription": "sub_77",
        "status": "paid",
        "lines": {"object": "list", "data": [{
            "id": "il_1", "object": "line_item", "metadata": {},
            "price": {"id": "price_team"},
        }]},
    }
    obj.update(obj_overrides)
    assert "client_reference_id" not in obj   # the whole point
    return {"id": event_id, "type": "invoice.paid", "data": {"object": obj}}


def _pay(svc, event, provisioned, ledger=None):
    return PaymentService(StripeSigned(event), svc,
                          on_paid=lambda a: provisioned.append(a.id), event_ledger=ledger)


# ---------------- checkout result carries the hosted URL ----------------
@pytest.mark.unit
def test_start_checkout_returns_hosted_url():
    svc = _verified_service()
    pay = _pay(svc, {}, [])
    res = pay.start_checkout("a1", "team", "idem-1")
    assert res.checkout_id == "cs_42"
    assert res.checkout_url == "https://checkout.stripe.com/c/pay/cs_42"
    assert res.stripe_customer_id == "cus_42"


# ---------------- invoice.paid resolution paths ----------------
@pytest.mark.unit
def test_invoice_paid_resolves_via_subscription_details_metadata():
    svc = _verified_service()
    provisioned = []
    event = _invoice_paid(subscription_details={"metadata": {"signup_id": "a1", "plan": "team"}})
    pay = _pay(svc, event, provisioned)
    assert pay.handle_webhook(b"{}", "good-sig", "whsec") == {"handled": True, "account_id": "a1"}
    assert provisioned == ["a1"]


@pytest.mark.unit
def test_invoice_paid_resolves_via_parent_subscription_details_metadata():
    # The 2025+ API shape: invoice.parent.subscription_details.metadata.
    svc = _verified_service()
    provisioned = []
    event = _invoice_paid(
        parent={"type": "subscription_details",
                "subscription_details": {"subscription": "sub_77",
                                         "metadata": {"signup_id": "a1", "plan": "team"}}},
    )
    pay = _pay(svc, event, provisioned)
    assert pay.handle_webhook(b"{}", "good-sig", "whsec") == {"handled": True, "account_id": "a1"}
    assert provisioned == ["a1"]


@pytest.mark.unit
def test_invoice_paid_resolves_via_line_item_metadata():
    svc = _verified_service()
    provisioned = []
    event = _invoice_paid(lines={"object": "list", "data": [
        {"id": "il_1", "metadata": {"signup_id": "a1", "plan": "team"}},
    ]})
    pay = _pay(svc, event, provisioned)
    assert pay.handle_webhook(b"{}", "good-sig", "whsec") == {"handled": True, "account_id": "a1"}
    assert provisioned == ["a1"]


@pytest.mark.unit
def test_invoice_paid_falls_back_to_stored_stripe_customer_id():
    # No metadata anywhere (e.g. a subscription created before the stamping fix): the stored
    # stripe_customer_id mapping written by start_checkout resolves the account.
    svc = _verified_service()
    provisioned = []
    event = _invoice_paid()   # bare invoice — customer id only
    pay = _pay(svc, event, provisioned)
    pay.start_checkout("a1", "team", "idem-1")   # persists stripe_customer_id = cus_42
    assert pay.handle_webhook(b"{}", "good-sig", "whsec") == {"handled": True, "account_id": "a1"}
    assert provisioned == ["a1"]


@pytest.mark.unit
def test_invoice_paid_with_no_resolvable_account_is_handled_noop():
    svc = _verified_service()
    provisioned = []
    event = _invoice_paid(customer="cus_someone_else")
    pay = _pay(svc, event, provisioned)
    res = pay.handle_webhook(b"{}", "good-sig", "whsec")
    assert res == {"handled": False, "reason": "unknown account"}
    assert provisioned == []
    assert svc.store.get("a1").state is State.PHONE_VERIFIED  # untouched


@pytest.mark.unit
def test_invoice_paid_metadata_pointing_at_unknown_account_falls_through_to_customer():
    # A stale/foreign signup_id in metadata must not crash — and the customer mapping still wins.
    svc = _verified_service()
    provisioned = []
    event = _invoice_paid(subscription_details={"metadata": {"signup_id": "ghost"}})
    pay = _pay(svc, event, provisioned)
    pay.start_checkout("a1", "team", "idem-1")
    assert pay.handle_webhook(b"{}", "good-sig", "whsec") == {"handled": True, "account_id": "a1"}
    assert provisioned == ["a1"]


@pytest.mark.unit
def test_completed_session_then_invoice_paid_provisions_once():
    # Stripe sends BOTH events for one purchase; the second must absorb as idempotent even
    # though it arrives with a DIFFERENT event id and a different resolution path.
    svc = _verified_service()
    provisioned = []
    ledger = Ledger()
    completed = {"id": "evt_cs", "type": "checkout.session.completed",
                 "data": {"object": {"client_reference_id": "a1",
                                     "metadata": {"signup_id": "a1", "plan": "team"}}}}
    stripe = StripeSigned(completed)
    pay = PaymentService(stripe, svc, on_paid=lambda a: provisioned.append(a.id),
                         event_ledger=ledger)
    pay.start_checkout("a1", "team", "idem-1")
    pay.handle_webhook(b"{}", "good-sig", "whsec")
    stripe.event = _invoice_paid(
        event_id="evt_inv",
        subscription_details={"metadata": {"signup_id": "a1", "plan": "team"}},
    )
    res = pay.handle_webhook(b"{}", "good-sig", "whsec")
    assert res == {"handled": True, "idempotent": True, "account_id": "a1"}
    assert provisioned == ["a1"]


@pytest.mark.unit
def test_completed_session_without_client_reference_falls_back_to_session_metadata():
    svc = _verified_service()
    provisioned = []
    event = {"id": "evt_cs2", "type": "checkout.session.completed",
             "data": {"object": {"metadata": {"signup_id": "a1", "plan": "team"}}}}
    pay = _pay(svc, event, provisioned)
    assert pay.handle_webhook(b"{}", "good-sig", "whsec") == {"handled": True, "account_id": "a1"}
    assert provisioned == ["a1"]


# ---------------- internal_comp (the env-gated bypass core) ----------------
@pytest.mark.unit
def test_internal_comp_settles_through_the_same_ledger_and_on_paid_path():
    svc = _verified_service()
    provisioned = []
    ledger = Ledger()
    pay = PaymentService(StripeSigned({}), svc, on_paid=lambda a: provisioned.append(a.id),
                         event_ledger=ledger)
    res = pay.internal_comp("a1", "team")
    assert res == {"handled": True, "account_id": "a1", "internal_comp": True}
    assert provisioned == ["a1"]
    # The ledger row is CLEARLY LABELED — auditable, prefix-greppable, deterministic.
    assert list(ledger.rows) == [f"{INTERNAL_COMP_EVENT_PREFIX}a1"]


@pytest.mark.unit
def test_internal_comp_double_fire_is_idempotent():
    svc = _verified_service()
    provisioned = []
    ledger = Ledger()
    pay = PaymentService(StripeSigned({}), svc, on_paid=lambda a: provisioned.append(a.id),
                         event_ledger=ledger)
    first = pay.internal_comp("a1", "team")
    second = pay.internal_comp("a1", "team")   # double-fire
    assert first["handled"] and second["idempotent"] is True
    assert provisioned == ["a1"]               # exactly one provision
    assert len(ledger.rows) == 1


@pytest.mark.unit
def test_internal_comp_requires_full_verification():
    svc = AccountService(Store(), Cognito(), Email(), Recorder())
    svc.create("a1", "u@friesenlabs.com", "+15555550100")   # NOT verified
    pay = PaymentService(StripeSigned({}), svc, on_paid=lambda a: None)
    with pytest.raises(PaymentError):
        pay.internal_comp("a1", "team")


@pytest.mark.unit
def test_internal_comp_unknown_account_is_a_payment_error():
    svc = _verified_service()
    pay = PaymentService(StripeSigned({}), svc, on_paid=lambda a: None)
    with pytest.raises(PaymentError):
        pay.internal_comp("ghost", "team")
