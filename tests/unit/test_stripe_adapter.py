"""Unit: StripeAdapter — mocked stripe lib (NO network): signature success/failure, plan mapping,
unconfigured-stub behavior, and the exact PaymentService call-site contract.

The venv intentionally has no `stripe` installed, so these tests also prove the lazy import:
the module imports and the adapter constructs without the lib; only live calls would need it.
"""
import types

import pytest

from signup.accounts import AccountService
from signup.payment import PaymentService
from signup.stripe_adapter import StripeAdapter, StripeNotConfiguredError, from_config

# Reuse the offline fakes the signup suite already trusts.
from tests.unit.test_signup_provisioning import Cognito, Email, Recorder, Store


class FakeSignatureError(Exception):
    """Stand-in for stripe.error.SignatureVerificationError."""


def _fake_stripe(calls, *, event=None):
    """A duck-typed `stripe` module: records every call, verifies sig == 'good-sig'."""

    def customer_create(**kw):
        calls.append(("Customer.create", kw))
        return {"id": "cus_123"}

    def session_create(**kw):
        calls.append(("checkout.Session.create", kw))
        return {"id": "cs_456"}

    def construct_event(payload, sig_header, secret):
        calls.append(("Webhook.construct_event", {"payload": payload, "sig": sig_header,
                                                  "secret": secret}))
        if sig_header != "good-sig":
            raise FakeSignatureError("signature mismatch")
        return event or {"type": "checkout.session.completed", "data": {"object": {}}}

    return types.SimpleNamespace(
        Customer=types.SimpleNamespace(create=customer_create),
        checkout=types.SimpleNamespace(Session=types.SimpleNamespace(create=session_create)),
        Webhook=types.SimpleNamespace(construct_event=construct_event),
    )


def _adapter(calls, *, api_key="sk_test_injected", event=None, **kw):
    return StripeAdapter(api_key, {"starter": "price_starter", "team": "price_team"},
                         stripe_module=_fake_stripe(calls, event=event), **kw)


# ---------------- import safety / lazy lib ----------------
@pytest.mark.unit
def test_module_imports_and_constructs_without_stripe_installed():
    # `stripe` is NOT in the test venv — import + construction must still work (lazy import).
    import signup.stripe_adapter  # noqa: F401
    adapter = StripeAdapter("", {})
    # Unconfigured live call fails CLEANLY (config check fires before any stripe import).
    with pytest.raises(StripeNotConfiguredError):
        adapter.create_customer(email="u@x.com", idempotency_key="i1")


@pytest.mark.unit
def test_from_config_builds_unconfigured_stub_by_default(monkeypatch):
    for var in ("STRIPE_API_KEY", "STRIPE_PRICE_ID_STARTER", "STRIPE_PRICE_ID_TEAM",
                "STRIPE_PRICE_ID_SCALE"):
        monkeypatch.delenv(var, raising=False)
    adapter = from_config()
    with pytest.raises(StripeNotConfiguredError):
        adapter.create_checkout_session(customer="cus_1", plan="starter",
                                        client_reference_id="a1", idempotency_key="i1")


@pytest.mark.unit
def test_shared_config_price_id_names(monkeypatch):
    # New env NAMES land in shared/config.py: the plan map reads them at call time.
    from shared import config
    monkeypatch.setenv("STRIPE_PRICE_ID_STARTER", "price_abc")
    monkeypatch.delenv("STRIPE_PRICE_ID_TEAM", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_ID_SCALE", raising=False)
    assert config.stripe_price_ids() == {"starter": "price_abc"}  # unset plans omitted


# ---------------- webhook signature verification ----------------
@pytest.mark.unit
def test_construct_event_good_signature_returns_event():
    calls = []
    event = {"type": "invoice.paid", "data": {"object": {"client_reference_id": "a1"}}}
    adapter = _adapter(calls, event=event)
    got = adapter.construct_event(b'{"raw": 1}', "good-sig", "whsec_x")
    assert got is event
    # The RAW payload + header + secret were handed to stripe.Webhook.construct_event verbatim.
    assert calls == [("Webhook.construct_event",
                      {"payload": b'{"raw": 1}', "sig": "good-sig", "secret": "whsec_x"})]


@pytest.mark.unit
def test_construct_event_bad_signature_raises():
    adapter = _adapter([])
    with pytest.raises(FakeSignatureError):
        adapter.construct_event(b"{}", "forged-sig", "whsec_x")


@pytest.mark.unit
def test_construct_event_refuses_empty_webhook_secret():
    calls = []
    adapter = _adapter(calls)
    with pytest.raises(StripeNotConfiguredError):
        adapter.construct_event(b"{}", "good-sig", "")
    assert calls == []  # refused BEFORE any verification attempt


# ---------------- customers + checkout (plan mapping) ----------------
@pytest.mark.unit
def test_create_customer_forwards_email_and_idempotency_key():
    calls = []
    adapter = _adapter(calls)
    got = adapter.create_customer(email="u@x.com", idempotency_key="idem-1")
    assert got == {"id": "cus_123"}
    name, kw = calls[0]
    assert name == "Customer.create"
    assert kw["email"] == "u@x.com"
    assert kw["idempotency_key"] == "idem-1"
    assert kw["api_key"] == "sk_test_injected"  # per-call key, injected — never global/hardcoded


@pytest.mark.unit
def test_checkout_session_maps_plan_to_price_id():
    calls = []
    adapter = _adapter(calls, success_url="https://app/ok", cancel_url="https://app/no")
    got = adapter.create_checkout_session(customer="cus_123", plan="team",
                                          client_reference_id="acct-1", idempotency_key="idem-2")
    assert got == {"id": "cs_456"}
    name, kw = calls[0]
    assert name == "checkout.Session.create"
    assert kw["line_items"] == [{"price": "price_team", "quantity": 1}]  # plan -> Price ID
    assert kw["mode"] == "subscription"
    assert kw["customer"] == "cus_123"
    assert kw["client_reference_id"] == "acct-1"   # how the webhook finds the account
    assert kw["idempotency_key"] == "idem-2"
    assert kw["metadata"] == {"plan": "team"}      # read back by the H7 funnel revenue event
    assert kw["success_url"] == "https://app/ok"
    assert kw["cancel_url"] == "https://app/no"


@pytest.mark.unit
def test_checkout_unknown_plan_raises_before_any_call():
    calls = []
    adapter = _adapter(calls)
    with pytest.raises(ValueError, match="unknown plan"):
        adapter.create_checkout_session(customer="cus_123", plan="enterprise",
                                        client_reference_id="acct-1", idempotency_key="idem-3")
    assert calls == []  # no Stripe call for an unmapped plan


@pytest.mark.unit
def test_unconfigured_api_key_never_touches_the_lib():
    calls = []
    adapter = _adapter(calls, api_key="")
    with pytest.raises(StripeNotConfiguredError):
        adapter.create_customer(email="u@x.com", idempotency_key="i")
    with pytest.raises(StripeNotConfiguredError):
        adapter.create_checkout_session(customer="c", plan="team",
                                        client_reference_id="a", idempotency_key="i")
    assert calls == []


# ---------------- the exact PaymentService call-site contract ----------------
@pytest.mark.unit
def test_payment_service_runs_end_to_end_through_the_adapter():
    svc = AccountService(Store(), Cognito(), Email(), Recorder())
    svc.create("a1", "u@x.com", "+15555550100")
    svc.verify_email("a1", True)
    svc.verify_phone("a1", True)

    calls = []
    event = {"type": "checkout.session.completed",
             "data": {"object": {"client_reference_id": "a1"}}}
    provisioned = []
    pay = PaymentService(_adapter(calls, event=event), svc,
                         on_paid=lambda a: provisioned.append(a.id))

    res = pay.start_checkout("a1", "starter", "idem-9")
    assert res.stripe_customer_id == "cus_123" and res.checkout_id == "cs_456"

    # A forged webhook raises (no provisioning); the signed one provisions exactly once.
    with pytest.raises(FakeSignatureError):
        pay.handle_webhook(b"{}", "forged-sig", "whsec_x")
    assert provisioned == []
    pay.handle_webhook(b"{}", "good-sig", "whsec_x")
    pay.handle_webhook(b"{}", "good-sig", "whsec_x")  # re-delivery is idempotent
    assert provisioned == ["a1"]
