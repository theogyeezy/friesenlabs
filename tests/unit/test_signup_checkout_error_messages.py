"""Unit: the checkout routes no longer echo str(PaymentError) to clients (api/signup_routes.py).

Both checkout-path 400s used to return `detail=str(e)` — PaymentError wraps Stripe adapter
errors VERBATIM (`PaymentError(str(e))` in signup/payment.py), so an unauthenticated pre-tenant
route was echoing provider internals (adapter messages, config hints) to whoever poked it.
These tests pin: the client now gets a FIXED honest message, the real exception detail appears
ONLY in the server-side log, and nothing of the internal message leaks into the response body.
"""
import logging
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.signup_routes import SignupDeps, mount_signup
from signup.payment import PaymentError

# A deliberately scary internal message — nothing in it may reach the client.
LEAKY = "stripe says: card_declined; api key sk_live_123 lacks capability; retry with idem 9f2"


class _Store:
    def __init__(self, acct):
        self._acct = acct

    def get(self, account_id):
        return self._acct


class _FailingPayment:
    """Raises PaymentError(LEAKY) from both checkout entrypoints."""

    def start_checkout(self, account_id, plan, idem):
        raise PaymentError(LEAKY)

    def internal_comp(self, account_id, plan):
        raise PaymentError(LEAKY)


def _client(bypass_domains=frozenset()):
    acct = SimpleNamespace(id="acct1", email="u@friesenlabs.com")
    deps = SignupDeps(
        accounts=SimpleNamespace(store=_Store(acct)),
        payment=_FailingPayment(),
        stripe_webhook_secret="whsec_test",
        new_account_id=lambda: "acct1",
        email_token_ok=lambda aid, t: False,
        sms_code_ok=lambda aid, c: False,
        internal_bypass_domains=bypass_domains,
    )
    app = FastAPI()
    mount_signup(app, deps)
    return TestClient(app)


@pytest.mark.unit
def test_start_checkout_400_is_fixed_message_not_str_e(caplog):
    client = _client()
    with caplog.at_level(logging.WARNING, logger="api.signup"):
        r = client.post("/signup/acct1/checkout", json={"plan": "pro"})
    assert r.status_code == 400
    assert r.json()["detail"] == "payment could not be started"
    # NOTHING of the internal message reaches the wire.
    assert "sk_live_123" not in r.text and "card_declined" not in r.text
    # The real reason went to the server log (type + message), keyed to the account.
    assert any("PaymentError" in rec.getMessage() and LEAKY in rec.getMessage()
               and "acct1" in rec.getMessage() for rec in caplog.records)


@pytest.mark.unit
def test_internal_comp_400_is_fixed_message_not_str_e(caplog):
    client = _client(bypass_domains=frozenset({"friesenlabs.com"}))
    with caplog.at_level(logging.WARNING, logger="api.signup"):
        r = client.post("/signup/acct1/checkout", json={"plan": "pro"})
    assert r.status_code == 400
    assert r.json()["detail"] == "payment could not be completed"
    assert "sk_live_123" not in r.text and "card_declined" not in r.text
    assert any("PaymentError" in rec.getMessage() and LEAKY in rec.getMessage()
               for rec in caplog.records)
