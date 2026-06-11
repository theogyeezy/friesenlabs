"""Stripe DATA connector (read-only) — revenue objects into the CRM data plane.

Pulls the TENANT'S OWN Stripe account data (customers + subscriptions +
invoices) for revenue views, mirroring the HubSpot reference connector shape:
injected source client (tests use recorded fixtures — NO live Stripe call in
CI), per-tenant vaulted credential, hard error when absent.

CREDENTIAL ISOLATION (do not blur this line): the key resolved here is the
tenant's OWN Stripe secret/restricted key from the per-tenant vault slot
`uplift/{tenant_id}/stripe` — NEVER the platform's signup/billing Stripe key
(signup/stripe_adapter.py; platform-level secret, different name, different
plane). The two must never mix: the platform key would pull Friesen Labs'
OWN billing data into a customer tenant's rows. By construction this module
only ever resolves the per-tenant slot, and a missing/empty slot is a HARD
MissingTenantCredentialError — there is no fallback of any kind.

READ-ONLY: list endpoints only; this connector never writes to Stripe
(draft-only invariant). A Stripe RESTRICTED key with read-only scopes for
Customers/Subscriptions/Invoices is the recommended credential.

Maps to the existing CRM schema (companies/deals analog — no schema change):
  customer     -> contacts    (name/email/phone,    ref_id = cus_…)
  subscription -> deals       (plan title/amount/status as stage, ref_id = sub_…)
  invoice      -> activities  (kind='invoice', revenue summary body, ref_id = in_…)
carrying tenant_id, source='stripe' on every row; text_blocks feed the
documents vector store so revenue questions ground in real invoice/sub data.

Incremental cursor: Stripe `created` epoch seconds, stored zero-padded to 10
digits so the pipeline's lexicographic max/compare stays correct.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Iterator, Protocol, runtime_checkable

from .base import (
    Connector,
    MissingTenantCredentialError,
    NormalizedRecord,
    SecretNotFoundError,
    tenant_secret_ref,
)

log = logging.getLogger("ingest.connectors.stripe_data")


@runtime_checkable
class StripeDataClient(Protocol):
    """Minimal source-client interface the connector depends on.

    Each method returns an iterable of raw Stripe objects (dicts). `since` is
    the high-water cursor (epoch seconds as a string); None = full pull.
    """

    def list_customers(self, since: str | None) -> Iterable[dict]: ...
    def list_subscriptions(self, since: str | None) -> Iterable[dict]: ...
    def list_invoices(self, since: str | None) -> Iterable[dict]: ...


def _epoch_cursor(obj: dict) -> str:
    """Stripe `created` (epoch seconds) -> a fixed-width, lexicographically
    orderable cursor string (the pipeline compares cursors as strings)."""
    created = obj.get("created")
    try:
        return f"{int(created):010d}" if created is not None else ""
    except (TypeError, ValueError):
        return ""


def _money(amount_minor: Any, currency: Any) -> tuple[float | None, str]:
    """Stripe minor units (cents) -> (major-unit float | None, UPPER currency)."""
    cur = (str(currency) if currency else "usd").upper()
    try:
        return (int(amount_minor) / 100.0, cur) if amount_minor is not None else (None, cur)
    except (TypeError, ValueError):
        return None, cur


class StripeDataConnector(Connector):
    """Read-only revenue-data connector for the tenant's OWN Stripe account."""

    source = "stripe"

    def __init__(self, tenant_id, *, client: StripeDataClient, **kwargs) -> None:
        super().__init__(tenant_id, **kwargs)
        self._client = client

    # -- auth ------------------------------------------------------------ #
    def authenticate(self) -> None:
        # Resolve the PER-TENANT vaulted key (uplift/{tenant_id}/stripe) and ONLY
        # that. The platform signup Stripe key lives under a DIFFERENT,
        # platform-level secret name and is structurally unreachable from here —
        # never add a fallback to it (see the module docstring).
        ref = tenant_secret_ref(self.tenant_id, self.source)
        try:
            key = self._secrets.get_secret(ref)
        except SecretNotFoundError as exc:
            log.error(
                "ingest auth failed: event=missing_tenant_credential tenant_id=%s "
                "source=%s ref=%s reason=secret_not_provisioned",
                self.tenant_id, self.source, ref,
            )
            raise MissingTenantCredentialError(
                self.tenant_id, self.source, ref, "not provisioned"
            ) from exc
        if not key:
            log.error(
                "ingest auth failed: event=missing_tenant_credential tenant_id=%s "
                "source=%s ref=%s reason=empty_secret_value",
                self.tenant_id, self.source, ref,
            )
            raise MissingTenantCredentialError(
                self.tenant_id, self.source, ref, "empty value"
            )
        set_key = getattr(self._client, "set_key", None)
        if callable(set_key):
            set_key(key)
        self._authed = True

    # -- pull ------------------------------------------------------------ #
    def pull(self, since_cursor: str | None) -> Iterable[NormalizedRecord]:
        self._require_auth()
        # customers first so subscriptions/invoices can reference customer refs.
        for c in self._client.list_customers(since_cursor):
            yield self._customer(c)
        for s in self._client.list_subscriptions(since_cursor):
            yield self._subscription(s)
        for i in self._client.list_invoices(since_cursor):
            yield self._invoice(i)

    # -- normalization ---------------------------------------------------- #
    @staticmethod
    def _customer_ref(obj: dict) -> str | None:
        cust = obj.get("customer")
        if isinstance(cust, dict):  # expanded object
            cust = cust.get("id")
        return str(cust) if cust else None

    def _customer(self, obj: dict) -> NormalizedRecord:
        ref = str(obj.get("id", ""))
        name = obj.get("name") or obj.get("description") or ""
        row = {
            "tenant_id": self.tenant_id,
            "company_ref_id": None,
            "name": name,
            "email": obj.get("email"),
            "phone": obj.get("phone"),
            "ref_id": ref,
            "source": self.source,
        }
        parts = [f"Customer: {name or ref}"]
        if row["email"]:
            parts.append(f"Email: {row['email']}")
        return NormalizedRecord(
            tenant_id=self.tenant_id,
            source=self.source,
            ref_id=ref,
            table="contacts",
            row=row,
            raw=obj,
            updated_at=_epoch_cursor(obj),
            kind="contact",
            text_blocks=[{"ref_id": ref, "kind": "contact", "text": "\n".join(parts)}],
        )

    def _subscription(self, obj: dict) -> NormalizedRecord:
        ref = str(obj.get("id", ""))
        status = obj.get("status") or "active"
        # Plan title + amount from the first subscription item's price.
        items = (obj.get("items") or {}).get("data") or []
        price = (items[0].get("price") or {}) if items else {}
        title = (
            price.get("nickname")
            or (price.get("product") if isinstance(price.get("product"), str) else None)
            or f"Subscription {ref}"
        )
        amount, currency = _money(price.get("unit_amount"), price.get("currency"))
        row = {
            "tenant_id": self.tenant_id,
            "company_ref_id": None,
            "contact_ref_id": self._customer_ref(obj),
            "title": title,
            "stage": status,
            "amount": amount,
            "currency": currency,
            "ref_id": ref,
            "source": self.source,
        }
        text = f"Subscription: {title}\nStatus: {status}"
        if amount is not None:
            interval = (price.get("recurring") or {}).get("interval")
            text += f"\nAmount: {amount} {currency}" + (f" per {interval}" if interval else "")
        return NormalizedRecord(
            tenant_id=self.tenant_id,
            source=self.source,
            ref_id=ref,
            table="deals",
            row=row,
            raw=obj,
            updated_at=_epoch_cursor(obj),
            kind="deal",
            text_blocks=[{"ref_id": ref, "kind": "deal", "text": text}],
        )

    def _invoice(self, obj: dict) -> NormalizedRecord:
        ref = str(obj.get("id", ""))
        status = obj.get("status") or ""
        amount, currency = _money(
            obj.get("amount_paid") if obj.get("status") == "paid" else obj.get("amount_due"),
            obj.get("currency"),
        )
        number = obj.get("number") or ref
        body = f"Invoice {number}: status {status}"
        if amount is not None:
            body += f", amount {amount} {currency}"
        row = {
            "tenant_id": self.tenant_id,
            "contact_ref_id": self._customer_ref(obj),
            "deal_ref_id": (
                str(obj["subscription"]) if isinstance(obj.get("subscription"), str)
                else None
            ),
            "kind": "invoice",
            "body": body,
            "ref_id": ref,
            "source": self.source,
        }
        return NormalizedRecord(
            tenant_id=self.tenant_id,
            source=self.source,
            ref_id=ref,
            table="activities",
            row=row,
            raw=obj,
            updated_at=_epoch_cursor(obj),
            kind="invoice",
            text_blocks=[{"ref_id": ref, "kind": "invoice", "text": body}],
        )


# --------------------------------------------------------------------------- #
# Real Stripe API client (stdlib urllib — read-only GETs, no new dependency,
# no import-time network). Satisfies the StripeDataClient Protocol;
# constructed only by the registry wiring in ingest/run_sync.py, never in CI.
# --------------------------------------------------------------------------- #
STRIPE_API_BASE = "https://api.stripe.com"


class StripeRestClient:
    """Minimal real Stripe client over GET /v1/{customers|subscriptions|invoices}.

    Constructed UNAUTHENTICATED — `StripeDataConnector.authenticate()` resolves
    the TENANT'S OWN vaulted key and injects it via `set_key()`; the raw key
    never transits the runtime wiring. List endpoints only (read-only).

    Incremental: `created[gt]=<epoch>` server-side filter; paged via
    `starting_after=<last id>`. `status=all` on subscriptions so canceled subs
    still land (their stage column reflects it).
    # VERIFY against live Stripe before first prod run: param shapes above and
    # that the tenant's RESTRICTED key scopes cover the three list endpoints.
    """

    def __init__(self, *, base_url: str = STRIPE_API_BASE, page_size: int = 100,
                 timeout_s: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._page_size = min(int(page_size), 100)  # Stripe list cap
        self._timeout_s = timeout_s
        self._key: str | None = None

    def set_key(self, key: str) -> None:
        self._key = key

    def _get(self, path: str, params: dict) -> dict:
        import json as _json  # noqa: PLC0415 — lazy with urllib below
        import urllib.parse  # noqa: PLC0415 — lazy: no network machinery at import
        import urllib.request  # noqa: PLC0415

        if not self._key:
            raise RuntimeError("StripeRestClient: no key — authenticate() must run first")
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(
            f"{self._base_url}{path}?{qs}",
            headers={"Authorization": f"Bearer {self._key}"},
            method="GET",  # read-only by construction
        )
        with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:  # noqa: S310 — fixed https base
            return _json.loads(resp.read().decode("utf-8"))

    def _list(self, path: str, since: str | None, extra: dict | None = None) -> Iterator[dict]:
        params: dict[str, Any] = {"limit": self._page_size}
        if extra:
            params.update(extra)
        if since:
            try:
                params["created[gt]"] = int(since)
            except (TypeError, ValueError):
                pass  # malformed cursor -> full pull (safe: upserts are idempotent)
        starting_after: str | None = None
        while True:
            if starting_after:
                params["starting_after"] = starting_after
            page = self._get(path, params)
            items = page.get("data", []) or []
            yield from items
            if not page.get("has_more") or not items:
                return
            starting_after = str(items[-1].get("id"))

    # -- StripeDataClient Protocol ----------------------------------------- #
    def list_customers(self, since: str | None) -> Iterable[dict]:
        return self._list("/v1/customers", since)

    def list_subscriptions(self, since: str | None) -> Iterable[dict]:
        return self._list("/v1/subscriptions", since, {"status": "all"})

    def list_invoices(self, since: str | None) -> Iterable[dict]:
        return self._list("/v1/invoices", since)
