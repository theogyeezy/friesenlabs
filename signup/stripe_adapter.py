"""Real Stripe adapter — payment plane (Build Guide Phase 10; TODO INT/P0 "real Stripe adapter").

Drop-in for `api/prod_deps._StubStripe` behind the duck-type contract `signup/payment.py`
already calls:

  - ``create_customer(email=..., idempotency_key=...)`` -> ``{"id": "cus_..."}``
  - ``create_checkout_session(customer=..., plan=..., client_reference_id=..., idempotency_key=...)``
    -> ``{"id": "cs_..."}``
  - ``construct_event(payload, sig_header, secret)`` -> verified event (RAISES on bad signature)

Security posture:
  - Key MATERIAL is INJECTED (the ``api_key`` constructor parameter; sourced from env via
    ``shared.config`` in :func:`from_config`). LANE NICK resolves Secrets Manager
    ``friesenlabs/platform/shared/stripe-*`` into the task environment — this module NEVER fetches
    Secrets Manager itself and never hardcodes a key.
  - The signed webhook is the ONLY provisioning trigger: ``construct_event`` delegates to
    ``stripe.Webhook.construct_event`` (HMAC-SHA256 over the raw payload) and raises on a bad or
    missing signature; ``api/signup_routes.py`` turns that into a 400.
  - Unconfigured == clean stub: with an empty ``api_key`` every live-call method raises
    :class:`StripeNotConfiguredError` BEFORE importing the stripe lib or touching the network, so
    the offline container boots and tests run exactly as with ``_StubStripe``.

Import-safe: the ``stripe`` lib is imported lazily on first use (tests inject a fake module).
"""
from __future__ import annotations

from typing import Any, Mapping


class StripeNotConfiguredError(RuntimeError):
    """A live Stripe call was attempted without the required configuration."""


class StripeAdapter:
    """Thin adapter over the ``stripe`` lib satisfying PaymentService's injected-client contract."""

    def __init__(self, api_key: str, price_ids: Mapping[str, str], *,
                 success_url: str = "", cancel_url: str = "",
                 stripe_module: Any = None):
        self._api_key = api_key or ""
        self._price_ids = dict(price_ids or {})   # plan id -> Stripe Price ID (injected, never built here)
        self._success_url = success_url
        self._cancel_url = cancel_url
        self._stripe = stripe_module               # injected fake in tests; lazily imported otherwise

    # ---------------------------------------------------------------- internals
    def _lib(self) -> Any:
        if self._stripe is None:
            try:
                import stripe  # noqa: PLC0415 — lazy: importing this module needs no stripe lib
            except ImportError as exc:
                raise StripeNotConfiguredError(
                    "the `stripe` package is not installed (requirements-api.txt: stripe>=7)"
                ) from exc
            self._stripe = stripe
        return self._stripe

    def _require_key(self, op: str) -> None:
        if not self._api_key:
            raise StripeNotConfiguredError(f"Stripe api_key not configured — cannot {op}")

    # ---------------------------------------------------------------- contract
    def create_customer(self, *, email: str, idempotency_key: str) -> dict:
        self._require_key("create a customer")
        customer = self._lib().Customer.create(
            api_key=self._api_key,            # per-call key — no global stripe.api_key mutation
            email=email,
            idempotency_key=idempotency_key,  # a double-click never mints two customers
        )
        return {"id": customer["id"]}

    def create_checkout_session(self, *, customer: str, plan: str, client_reference_id: str,
                                idempotency_key: str) -> dict:
        self._require_key("create a checkout session")
        price_id = self._price_ids.get(plan)
        if price_id is None:
            raise ValueError(
                f"unknown plan {plan!r}; configured plans: {sorted(self._price_ids) or 'none'}"
            )
        params: dict[str, Any] = {
            "api_key": self._api_key,
            "mode": "subscription",
            "customer": customer,
            # How the signed webhook finds the account (payment.handle_webhook reads it back).
            "client_reference_id": client_reference_id,
            "line_items": [{"price": price_id, "quantity": 1}],
            # Surfaced server-side by payment.handle_webhook for the H7 funnel revenue event.
            "metadata": {"plan": plan},
            "idempotency_key": idempotency_key,   # no double-charge on double-click
        }
        # Redirect URLs are UX only — provisioning trusts the signed webhook, never the browser.
        # VERIFY: hosted Checkout rejects a missing success_url on current API versions — set
        # STRIPE_SUCCESS_URL / STRIPE_CANCEL_URL (shared/config.py) before going live.
        if self._success_url:
            params["success_url"] = self._success_url
        if self._cancel_url:
            params["cancel_url"] = self._cancel_url
        session = self._lib().checkout.Session.create(**params)
        return {"id": session["id"]}

    def construct_event(self, payload: bytes, sig_header: str, secret: str) -> Any:
        """Signature-verify a webhook payload; raises on bad/missing signature.

        Pure HMAC verification — needs the webhook secret (NOT the api key) and no network.
        Raises ``stripe.error.SignatureVerificationError`` on a tampered/foreign payload and
        ``ValueError`` on malformed JSON; both bubble to the route's 400.
        """
        if not secret:
            # Refuse ALL webhooks rather than verifying against an empty secret.
            raise StripeNotConfiguredError(
                "STRIPE_WEBHOOK_SECRET not configured — refusing to accept any webhook"
            )
        return self._lib().Webhook.construct_event(payload, sig_header, secret)


def from_config(cfg: Any = None) -> StripeAdapter:
    """Build a StripeAdapter from shared.config (empty env => clean unconfigured stub)."""
    from shared import config as shared_config  # noqa: PLC0415 — keep module import light
    cfg = cfg or shared_config.load()
    return StripeAdapter(
        api_key=cfg.stripe_api_key,
        price_ids=shared_config.stripe_price_ids(),
        success_url=cfg.stripe_success_url,
        cancel_url=cfg.stripe_cancel_url,
    )
