"""Real Stripe adapter — payment plane (Build Guide Phase 10; TODO INT/P0 "real Stripe adapter").

Drop-in for `api/prod_deps._StubStripe` behind the duck-type contract `signup/payment.py`
already calls:

  - ``create_customer(email=..., idempotency_key=...)`` -> ``{"id": "cus_..."}``
  - ``create_checkout_session(customer=..., plan=..., client_reference_id=..., idempotency_key=...)``
    -> ``{"id": "cs_...", "url": "https://checkout.stripe.com/...", "customer": ...,
         "plan": ..., "price_id": ..., "mode": "subscription", "livemode": bool}``
    (the extra fields let start_checkout persist a checkout INTENT the signed webhook is later
    verified against — a valid signature does not prove the payload matches what we requested)
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
        # Account-resolution metadata, stamped at create time on BOTH the Checkout Session AND
        # the subscription it mints (subscription_data.metadata): Stripe INVOICES carry no
        # client_reference_id, so `invoice.paid` resolves the account via the subscription
        # metadata mirrored onto the invoice (payment.handle_webhook), with the stored
        # stripe_customer_id mapping as the final fallback.
        resolution_meta = {"plan": plan, "signup_id": client_reference_id}
        params: dict[str, Any] = {
            "api_key": self._api_key,
            "mode": "subscription",
            "customer": customer,
            # How the signed webhook finds the account (payment.handle_webhook reads it back).
            "client_reference_id": client_reference_id,
            "line_items": [{"price": price_id, "quantity": 1}],
            # Surfaced server-side by payment.handle_webhook for the H7 funnel revenue event.
            "metadata": dict(resolution_meta),
            "subscription_data": {"metadata": dict(resolution_meta)},
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
        # `url` is the Hosted Checkout page the BROWSER must be sent to — the checkout route
        # returns it to the SPA (window.location), so the client never fakes payment success;
        # the signed webhook remains the only provisioning trigger.
        # NOTE: index access, not `.get` — the stripe lib's StripeObject routes attribute
        # lookups through __getattr__ and exposes no dict-style `.get` method on current
        # versions (session.get("url") raises AttributeError: get; caught live by the
        # main-only live-signup-e2e job).
        try:
            url = session["url"]
        except KeyError:
            url = None
        # Surface the canonical, SERVER-known facts about this checkout so start_checkout can
        # persist a "checkout intent" the signed webhook is later verified against (a valid
        # signature alone never proves the payload's amount/price/livemode match what we asked
        # for). `price_id` is the one we sent (authoritative — never read back from the client);
        # `livemode`/`mode` come from the session Stripe returns when present, else the values we
        # requested. Index access with KeyError guards — StripeObject exposes no dict `.get`.
        def _opt(key, default=None):
            try:
                val = session[key]
            except (KeyError, TypeError):
                return default
            return default if val is None else val

        return {
            "id": session["id"],
            "url": url,
            "customer": _opt("customer", customer),
            "plan": plan,
            "price_id": price_id,
            # `mode` is always "subscription" here; `livemode` is True on live keys, False on test
            # keys — Stripe stamps it on the session and mirrors it onto every webhook event.
            "mode": _opt("mode", "subscription"),
            "livemode": bool(_opt("livemode", self._api_key.startswith("sk_live_"))),
        }

    def create_billing_portal_session(self, *, customer: str, return_url: str) -> dict:
        """Create a Stripe-hosted Customer Portal session for an EXISTING customer.

        The portal is Stripe-hosted (free): the tenant changes their card, cancels, or views
        invoices there, then Stripe sends them back to ``return_url``. The ONLY input is the
        ``customer`` id WE resolved server-side from the verified-claim->account mapping (never
        anything the client sends) plus the operator-configured ``return_url``. Returns
        ``{"id": "bps_...", "url": "https://billing.stripe.com/..."}``; the route hands the url
        to the SPA for ``window.location.assign``. Raises StripeNotConfiguredError when the api
        key is unset (clean stub, no network)."""
        self._require_key("create a billing portal session")
        if not customer:
            raise ValueError("billing portal session needs a Stripe customer id")
        session = self._lib().billing_portal.Session.create(
            api_key=self._api_key,            # per-call key — no global stripe.api_key mutation
            customer=customer,
            return_url=return_url or None,    # Stripe accepts None; portal still works
        )
        # Index access, not `.get`: the stripe lib's StripeObject routes attribute lookups through
        # __getattr__ and exposes no dict-style `.get` (session.get("url") raises AttributeError).
        return {"id": session["id"], "url": session["url"]}

    def list_invoices(self, *, customer: str, limit: int = 24) -> list[dict]:
        """List invoices for an EXISTING Stripe customer.

        Returns a list of normalized dicts — each with the keys the billing panel needs. Raises
        :class:`StripeNotConfiguredError` when no api key is set (same pattern as every other live-
        call method — clean stub, no network). Results are capped at ``limit`` (default 24).

        Normalized shape per invoice:
          ``id``, ``number``, ``amount_due``, ``amount_paid``, ``currency``, ``status``,
          ``created`` (Unix timestamp int), ``hosted_invoice_url``, ``invoice_pdf``.
        """
        self._require_key("list invoices")
        if not customer:
            raise ValueError("list_invoices needs a Stripe customer id")
        invoices_resp = self._lib().Invoice.list(
            api_key=self._api_key,   # per-call key — no global stripe.api_key mutation
            customer=customer,
            limit=min(int(limit), 24),
        )
        # `auto_paging_iter` or iteration — the stripe lib returns a ListObject that is iterable.
        rows = []
        for inv in invoices_resp:
            def _g(key, default=None, _inv=inv):
                """Safe index — StripeObject has no dict-style .get on current versions."""
                try:
                    val = _inv[key]
                except (KeyError, TypeError):
                    return default
                return default if val is None else val

            rows.append({
                "id": _g("id", ""),
                "number": _g("number", ""),
                "amount_due": _g("amount_due", 0),
                "amount_paid": _g("amount_paid", 0),
                "currency": _g("currency", "usd"),
                "status": _g("status", ""),
                "created": _g("created", 0),
                "hosted_invoice_url": _g("hosted_invoice_url", ""),
                "invoice_pdf": _g("invoice_pdf", ""),
            })
        return rows

    def sync_subscription_modules(self, *, customer: str, desired_price_ids,
                                  managed_price_ids, idempotency_key: str | None = None) -> dict:
        """Reconcile the customer's active subscription so it carries exactly the desired MODULE
        price items — the "selection sets the price" billing sync (Phase 2).

        Contract:
          * ``desired_price_ids`` — the Price ids the ENABLED modules should be billed at.
          * ``managed_price_ids`` — the FULL set of module Price ids we manage. We only ever add or
            remove items whose Price is in here, so the plan-tier line item (and anything else on
            the subscription) is NEVER touched. This is the safety boundary.
          * Adds a quantity-1 item for each desired Price the subscription lacks; deletes each
            managed item whose Price is no longer desired. A no-op when already in sync.

        Returns ``{"subscription": id, "added": [...], "removed": [...]}``. Raises
        :class:`StripeNotConfiguredError` with no api key. If the customer has no active
        subscription (e.g. internal-comp / unpaid), returns ``{"subscription": None, ...}`` and
        changes nothing — there is no invoice to move.
        """
        self._require_key("sync subscription items")
        if not customer:
            raise ValueError("sync_subscription_modules needs a Stripe customer id")
        desired = {p for p in (desired_price_ids or []) if p}
        managed = {p for p in (managed_price_ids or []) if p} | desired
        lib = self._lib()
        # Resolve the customer's active subscription (one per customer in our single-plan model).
        subs = lib.Subscription.list(api_key=self._api_key, customer=customer, status="active", limit=1)
        sub = next(iter(subs), None)
        if sub is None:
            return {"subscription": None, "added": [], "removed": []}

        def _idx(obj, key, default=None):
            try:
                val = obj[key]
            except (KeyError, TypeError):
                return default
            return default if val is None else val

        sub_id = _idx(sub, "id")
        # Current items: map Price id -> SubscriptionItem id (only the ones we manage matter).
        items_obj = _idx(sub, "items", {})
        present: dict[str, str] = {}
        for it in _idx(items_obj, "data", []) or []:
            price = _idx(it, "price", {})
            pid = _idx(price, "id")
            iid = _idx(it, "id")
            if pid and iid:
                present[pid] = iid

        added, removed = [], []
        # Add desired prices not present.
        for pid in sorted(desired - set(present)):
            kwargs = {"api_key": self._api_key, "subscription": sub_id, "price": pid, "quantity": 1}
            if idempotency_key:
                kwargs["idempotency_key"] = f"{idempotency_key}:add:{pid}"
            lib.SubscriptionItem.create(**kwargs)
            added.append(pid)
        # Remove managed prices that are present but no longer desired (never touch unmanaged items).
        for pid, iid in sorted(present.items()):
            if pid in managed and pid not in desired:
                kwargs = {"api_key": self._api_key}
                if idempotency_key:
                    kwargs["idempotency_key"] = f"{idempotency_key}:del:{pid}"
                lib.SubscriptionItem.delete(iid, **kwargs)
                removed.append(pid)
        return {"subscription": sub_id, "added": added, "removed": removed}

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
