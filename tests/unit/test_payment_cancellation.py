"""Unit: subscription-lifecycle webhook handling (cancellation / dunning).

A signed customer.subscription.deleted (or an .updated carrying past_due/unpaid/canceled) is NOT a
provisioning trigger — it only flips the tenant's persisted BILLING status so the app can show a
cancelled/grace banner. These tests pin: the account is resolved by the subscription's CUSTOMER
(never client input), the status mapping is driven by the event type + Subscription.status, the
write is idempotent, an unknown customer is a handled no-op, and provisioning is never invoked.
"""
import pytest

from signup.accounts import Account, AccountService, State
from signup.payment import PaymentService

from tests.unit.test_signup_provisioning import Cognito, Email, Recorder


class BillingStore:
    """In-memory account store WITH the billing-status seam (get_by_stripe_customer_id +
    set/get_billing_status) the cancellation path leans on."""

    def __init__(self):
        self.rows: dict[str, Account] = {}
        self.billing: dict[str, dict] = {}

    def get(self, aid):
        return self.rows.get(aid)

    def insert(self, acct):
        self.rows[acct.id] = acct

    def update(self, acct):
        self.rows[acct.id] = acct

    def get_by_stripe_customer_id(self, customer_id):
        return next((a for a in self.rows.values()
                     if getattr(a, "stripe_customer_id", None) == customer_id), None)

    def set_billing_status(self, account_id, status, *, reason=""):
        self.billing[account_id] = {"status": status, "reason": reason}

    def get_billing_status(self, account_id):
        return self.billing.get(account_id)


class StripeSigned:
    def __init__(self, event):
        self.event = event

    def construct_event(self, payload, sig, secret):
        if sig != "good-sig":
            raise ValueError("bad signature")
        return self.event


def _paid_account(store, *, customer="cus_42"):
    svc = AccountService(store, Cognito(), Email(), Recorder())
    acct = svc.create("a1", "u@x.com", "+15555550100")
    acct.stripe_customer_id = customer
    acct.state = State.ACTIVE
    store.update(acct)
    return svc


def _pay(store, event, provisioned):
    svc = _paid_account(store)
    return PaymentService(StripeSigned(event), svc, on_paid=lambda a: provisioned.append(a.id))


def _sub_event(event_type, *, customer="cus_42", status=None, event_id="evt_sub_1"):
    obj = {"id": "sub_77", "object": "subscription", "customer": customer}
    if status is not None:
        obj["status"] = status
    return {"id": event_id, "type": event_type, "data": {"object": obj}}


@pytest.mark.unit
def test_subscription_deleted_marks_canceled_and_does_not_provision():
    store = BillingStore()
    provisioned = []
    pay = _pay(store, _sub_event("customer.subscription.deleted"), provisioned)
    res = pay.handle_webhook(b"{}", "good-sig", "whsec")
    assert res["handled"] is True and res["billing_status"] == "canceled"
    assert store.get_billing_status("a1") == {"status": "canceled",
                                              "reason": "customer.subscription.deleted"}
    assert provisioned == []                      # cancellation NEVER provisions


@pytest.mark.unit
@pytest.mark.parametrize("status", ["past_due", "unpaid", "canceled"])
def test_subscription_updated_degraded_status_is_recorded(status):
    store = BillingStore()
    provisioned = []
    pay = _pay(store, _sub_event("customer.subscription.updated", status=status), provisioned)
    res = pay.handle_webhook(b"{}", "good-sig", "whsec")
    assert res["handled"] is True and res["billing_status"] == status
    assert store.get_billing_status("a1")["status"] == status
    assert provisioned == []


@pytest.mark.unit
def test_subscription_updated_active_clears_back_to_active():
    store = BillingStore()
    provisioned = []
    pay = _pay(store, _sub_event("customer.subscription.updated", status="active"), provisioned)
    res = pay.handle_webhook(b"{}", "good-sig", "whsec")
    assert res["billing_status"] == "active"
    assert store.get_billing_status("a1")["status"] == "active"


@pytest.mark.unit
def test_subscription_updated_benign_status_is_a_no_op():
    # A trial/incomplete/unknown status update with nothing to flip must not invent a degraded
    # state (we only act on the explicit degraded set + the active-clear).
    store = BillingStore()
    provisioned = []
    pay = _pay(store, _sub_event("customer.subscription.updated", status="incomplete"),
               provisioned)
    res = pay.handle_webhook(b"{}", "good-sig", "whsec")
    assert res["handled"] is False
    assert store.get_billing_status("a1") is None  # nothing written


@pytest.mark.unit
def test_unknown_customer_is_a_handled_no_op():
    store = BillingStore()
    provisioned = []
    pay = _pay(store, _sub_event("customer.subscription.deleted", customer="cus_unknown"),
               provisioned)
    res = pay.handle_webhook(b"{}", "good-sig", "whsec")
    assert res == {"handled": False, "reason": "unknown account"}
    assert store.get_billing_status("a1") is None


@pytest.mark.unit
def test_cancellation_webhook_is_idempotent():
    store = BillingStore()
    provisioned = []
    pay = _pay(store, _sub_event("customer.subscription.deleted"), provisioned)
    first = pay.handle_webhook(b"{}", "good-sig", "whsec")
    second = pay.handle_webhook(b"{}", "good-sig", "whsec")
    assert first["billing_status"] == second["billing_status"] == "canceled"
    assert store.get_billing_status("a1") == {"status": "canceled",
                                              "reason": "customer.subscription.deleted"}


@pytest.mark.unit
def test_bad_signature_rejects_cancellation():
    store = BillingStore()
    provisioned = []
    pay = _pay(store, _sub_event("customer.subscription.deleted"), provisioned)
    with pytest.raises(ValueError, match="bad signature"):
        pay.handle_webhook(b"{}", "forged", "whsec")
    assert store.get_billing_status("a1") is None


@pytest.mark.unit
def test_store_without_billing_seam_is_a_no_op():
    # A store missing set_billing_status (e.g. the bare in-memory test fake) degrades honestly.
    class BareStore(BillingStore):
        set_billing_status = None  # type: ignore[assignment]

    store = BareStore()
    provisioned = []
    pay = _pay(store, _sub_event("customer.subscription.deleted"), provisioned)
    res = pay.handle_webhook(b"{}", "good-sig", "whsec")
    assert res == {"handled": False, "reason": "billing status not persisted"}
