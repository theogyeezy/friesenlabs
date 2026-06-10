"""HubSpot connector — the reference connector implementation.

The source client is INJECTED: tests pass a fake that returns fixture
contacts/companies/deals/notes. :class:`HubSpotRestClient` (below) is the real
impl over the HubSpot CRM v3 Search API — constructed by the caller
(`ingest/run_sync.py`), never here, and importing this module needs no network.

Credentials (TODO INT/P1): `authenticate()` resolves the PER-TENANT vaulted
token `uplift/{tenant_id}/hubspot` via the injected SecretProvider first; the
single shared `HUBSPOT_TOKEN_SECRET_REF` remains only as a DEPRECATED fallback
(warns) until every tenant has a per-tenant secret provisioned.

Normalizes HubSpot objects to the db/schema.sql shapes
(companies / contacts / deals / activities), carrying tenant_id, source='hubspot',
and ref_id (the HubSpot object id) on every row.
"""
from __future__ import annotations

import warnings
from typing import Any, Iterable, Iterator, Protocol, runtime_checkable

from .base import Connector, NormalizedRecord, tenant_secret_ref


@runtime_checkable
class HubSpotClient(Protocol):
    """Minimal source-client interface the connector depends on.

    Each method returns an iterable of raw HubSpot objects (dicts). `since` is the
    high-water cursor (an ISO-8601 lastmodified timestamp); None means full pull.
    A real impl would page the HubSpot CRM Search API filtered on hs_lastmodifieddate.
    """

    def list_companies(self, since: str | None) -> Iterable[dict]: ...
    def list_contacts(self, since: str | None) -> Iterable[dict]: ...
    def list_deals(self, since: str | None) -> Iterable[dict]: ...
    def list_notes(self, since: str | None) -> Iterable[dict]: ...


# DEPRECATED — the single SHARED token reference (one token for ALL tenants).
# Kept only as a fallback while per-tenant secrets (uplift/{tenant_id}/hubspot,
# see base.tenant_secret_ref) are being provisioned; resolution warns when used.
HUBSPOT_TOKEN_SECRET_REF = "uplift/hubspot-private-app-token"


class HubSpotConnector(Connector):
    source = "hubspot"

    def __init__(self, tenant_id, *, client: HubSpotClient, **kwargs) -> None:
        super().__init__(tenant_id, **kwargs)
        self._client = client
        self._token: str | None = None

    # -- auth ------------------------------------------------------------ #
    def authenticate(self) -> None:
        # Resolve the PER-TENANT vaulted token first (uplift/{tenant_id}/hubspot);
        # never log/return the raw value. Any failure on the per-tenant lookup
        # (not provisioned yet / provider that only knows the shared ref) falls
        # back to the DEPRECATED shared token, with a warning.
        per_tenant_ref = tenant_secret_ref(self.tenant_id, self.source)
        token: str | None = None
        try:
            token = self._secrets.get_secret(per_tenant_ref)
        except Exception:  # noqa: BLE001 — absence/legacy-provider both mean "fall back"
            token = None
        if not token:
            warnings.warn(
                f"HubSpot: per-tenant secret {per_tenant_ref!r} not resolved — "
                f"falling back to the SHARED token ({HUBSPOT_TOKEN_SECRET_REF!r}). "
                "The shared token is DEPRECATED; provision the per-tenant secret.",
                DeprecationWarning,
                stacklevel=2,
            )
            token = self._secrets.get_secret(HUBSPOT_TOKEN_SECRET_REF)
        if not token:
            raise RuntimeError("HubSpot: empty token from secret provider")
        self._token = token
        # Hand the resolved token to the injected source client when it accepts
        # one (HubSpotRestClient does; test fakes need not implement set_token).
        set_token = getattr(self._client, "set_token", None)
        if callable(set_token):
            set_token(token)
        self._authed = True

    # -- pull ------------------------------------------------------------ #
    def pull(self, since_cursor: str | None) -> Iterable[NormalizedRecord]:
        self._require_auth()
        # companies first so contacts/deals can reference company ref_ids.
        for c in self._client.list_companies(since_cursor):
            yield self._company(c)
        for c in self._client.list_contacts(since_cursor):
            yield self._contact(c)
        for d in self._client.list_deals(since_cursor):
            yield self._deal(d)
        for n in self._client.list_notes(since_cursor):
            rec = self._note(n)
            if rec is not None:
                yield rec

    # -- normalization ---------------------------------------------------- #
    @staticmethod
    def _props(obj: dict) -> dict:
        # HubSpot wraps fields under "properties"; tolerate flat fixtures too.
        return obj.get("properties", obj)

    @staticmethod
    def _updated(obj: dict, props: dict) -> str:
        return (
            obj.get("updatedAt")
            or props.get("hs_lastmodifieddate")
            or props.get("lastmodifieddate")
            or ""
        )

    def _company(self, obj: dict) -> NormalizedRecord:
        p = self._props(obj)
        ref = str(obj.get("id", p.get("id", "")))
        row = {
            "tenant_id": self.tenant_id,
            "name": p.get("name") or "",
            "domain": p.get("domain"),
            "ref_id": ref,
            "source": self.source,
        }
        text = f"Company: {row['name']}"
        if row["domain"]:
            text += f"\nDomain: {row['domain']}"
        return NormalizedRecord(
            tenant_id=self.tenant_id,
            source=self.source,
            ref_id=ref,
            table="companies",
            row=row,
            raw=obj,
            updated_at=self._updated(obj, p),
            kind="company",
            text_blocks=[{"ref_id": ref, "kind": "company", "text": text}],
        )

    def _contact(self, obj: dict) -> NormalizedRecord:
        p = self._props(obj)
        ref = str(obj.get("id", p.get("id", "")))
        name = " ".join(
            x for x in [p.get("firstname"), p.get("lastname")] if x
        ) or p.get("name") or ""
        row = {
            "tenant_id": self.tenant_id,
            "company_ref_id": p.get("associatedcompanyid"),
            "name": name,
            "email": p.get("email"),
            "phone": p.get("phone"),
            "ref_id": ref,
            "source": self.source,
        }
        parts = [f"Contact: {name}"]
        if row["email"]:
            parts.append(f"Email: {row['email']}")
        if row["phone"]:
            parts.append(f"Phone: {row['phone']}")
        if p.get("jobtitle"):
            parts.append(f"Title: {p['jobtitle']}")
        return NormalizedRecord(
            tenant_id=self.tenant_id,
            source=self.source,
            ref_id=ref,
            table="contacts",
            row=row,
            raw=obj,
            updated_at=self._updated(obj, p),
            kind="contact",
            text_blocks=[{"ref_id": ref, "kind": "contact", "text": "\n".join(parts)}],
        )

    def _deal(self, obj: dict) -> NormalizedRecord:
        p = self._props(obj)
        ref = str(obj.get("id", p.get("id", "")))
        title = p.get("dealname") or ""
        amount = p.get("amount")
        try:
            amount = float(amount) if amount not in (None, "") else None
        except (TypeError, ValueError):
            amount = None
        row = {
            "tenant_id": self.tenant_id,
            "company_ref_id": p.get("associatedcompanyid"),
            "contact_ref_id": p.get("associatedcontactid"),
            "title": title,
            "stage": p.get("dealstage") or "new",
            "amount": amount,
            "currency": p.get("deal_currency_code") or "USD",
            "ref_id": ref,
            "source": self.source,
        }
        text = f"Deal: {title}\nStage: {row['stage']}"
        if amount is not None:
            text += f"\nAmount: {amount} {row['currency']}"
        return NormalizedRecord(
            tenant_id=self.tenant_id,
            source=self.source,
            ref_id=ref,
            table="deals",
            row=row,
            raw=obj,
            updated_at=self._updated(obj, p),
            kind="deal",
            text_blocks=[{"ref_id": ref, "kind": "deal", "text": text}],
        )

    def _note(self, obj: dict) -> NormalizedRecord | None:
        p = self._props(obj)
        ref = str(obj.get("id", p.get("id", "")))
        body = p.get("hs_note_body") or p.get("body") or ""
        if not body:
            return None
        row = {
            "tenant_id": self.tenant_id,
            "contact_ref_id": p.get("hs_contact_id") or p.get("contact_ref_id"),
            "deal_ref_id": p.get("hs_deal_id") or p.get("deal_ref_id"),
            "kind": "note",
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
            updated_at=self._updated(obj, p),
            kind="note",
            text_blocks=[{"ref_id": ref, "kind": "note", "text": body}],
        )


# --------------------------------------------------------------------------- #
# Real HubSpot CRM v3 client (stdlib urllib — no new dependency, no import-time
# network). Satisfies the HubSpotClient Protocol; built by ingest/run_sync.py.
# --------------------------------------------------------------------------- #
HUBSPOT_API_BASE = "https://api.hubapi.com"

# The "last modified" property HubSpot uses per object type (contacts are the
# odd one out). # VERIFY: property names against the live CRM v3 API.
_LASTMOD_PROP = {
    "companies": "hs_lastmodifieddate",
    "contacts": "lastmodifieddate",
    "deals": "hs_lastmodifieddate",
    "notes": "hs_lastmodifieddate",
}
_SEARCH_PROPERTIES = {
    "companies": ["name", "domain", "hs_lastmodifieddate"],
    "contacts": ["firstname", "lastname", "email", "phone", "jobtitle",
                 "associatedcompanyid", "lastmodifieddate"],
    "deals": ["dealname", "dealstage", "amount", "deal_currency_code",
              "associatedcompanyid", "associatedcontactid", "hs_lastmodifieddate"],
    "notes": ["hs_note_body", "hs_lastmodifieddate"],
}


class HubSpotRestClient:
    """Minimal real HubSpot client over the CRM v3 Search API (POST
    /crm/v3/objects/{type}/search), paged via `paging.next.after` and filtered
    on the object's last-modified property when `since` is given.

    Constructed UNAUTHENTICATED — `HubSpotConnector.authenticate()` resolves the
    vaulted token and injects it via `set_token()`, so the raw token never
    transits run_sync. stdlib `urllib` only, imported lazily per request; no
    network (or token) is touched at import or construction time.

    # VERIFY: confirm against the live HubSpot API before first prod run —
    #   * the search filter `value` format for datetime properties (ISO-8601
    #     `updatedAt` strings, which our cursor stores, vs epoch-millis),
    #   * `notes` being searchable at /crm/v3/objects/notes/search,
    #   * association properties (associatedcompanyid) arriving via search.
    """

    def __init__(self, *, base_url: str = HUBSPOT_API_BASE, page_size: int = 100,
                 timeout_s: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._page_size = min(int(page_size), 200)  # HubSpot search caps at 200
        self._timeout_s = timeout_s
        self._token: str | None = None

    def set_token(self, token: str) -> None:
        self._token = token

    # -- one paged search ------------------------------------------------- #
    def _post(self, path: str, payload: dict) -> dict:
        import json as _json  # noqa: PLC0415 — lazy with urllib below
        import urllib.request  # noqa: PLC0415 — lazy: no network machinery at import

        if not self._token:
            raise RuntimeError("HubSpotRestClient: no token — authenticate() must run first")
        req = urllib.request.Request(
            f"{self._base_url}{path}",
            data=_json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:  # noqa: S310 — fixed https base
            return _json.loads(resp.read().decode("utf-8"))

    def _search(self, object_type: str, since: str | None) -> Iterator[dict]:
        lastmod = _LASTMOD_PROP[object_type]
        body: dict[str, Any] = {
            "properties": _SEARCH_PROPERTIES[object_type],
            "sorts": [{"propertyName": lastmod, "direction": "ASCENDING"}],
            "limit": self._page_size,
        }
        if since:
            body["filterGroups"] = [
                {"filters": [{"propertyName": lastmod, "operator": "GT", "value": since}]}
            ]
        after: str | None = None
        while True:
            if after:
                body["after"] = after
            page = self._post(f"/crm/v3/objects/{object_type}/search", body)
            yield from page.get("results", [])
            after = (page.get("paging", {}).get("next") or {}).get("after")
            if not after:
                return

    # -- HubSpotClient Protocol ------------------------------------------- #
    def list_companies(self, since: str | None) -> Iterable[dict]:
        return self._search("companies", since)

    def list_contacts(self, since: str | None) -> Iterable[dict]:
        return self._search("contacts", since)

    def list_deals(self, since: str | None) -> Iterable[dict]:
        return self._search("deals", since)

    def list_notes(self, since: str | None) -> Iterable[dict]:
        return self._search("notes", since)
