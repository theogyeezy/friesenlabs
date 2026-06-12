"""HubSpot connector — the reference connector implementation.

The source client is INJECTED: tests pass a fake that returns fixture
contacts/companies/deals/notes. :class:`HubSpotRestClient` (below) is the real
impl over the HubSpot CRM v3 Search API — constructed by the caller
(`ingest/run_sync.py`), never here, and importing this module needs no network.

Credentials: `authenticate()` resolves the PER-TENANT vaulted token
`uplift/{tenant_id}/hubspot` via the injected SecretProvider — and ONLY that.
A missing or empty per-tenant secret is a HARD MissingTenantCredentialError
(the historical shared-token fallback was removed: one shared HubSpot token
could land another customer's portal under this tenant's rows). Any other
provider failure (access denied, throttle, network) propagates untouched.

Normalizes HubSpot objects to the db/schema.sql shapes
(companies / contacts / deals / activities), carrying tenant_id, source='hubspot',
and ref_id (the HubSpot object id) on every row.
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

log = logging.getLogger("ingest.connectors.hubspot")


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


class HubSpotConnector(Connector):
    source = "hubspot"

    def __init__(self, tenant_id, *, client: HubSpotClient,
                 secret_writer=None, **kwargs) -> None:
        super().__init__(tenant_id, **kwargs)
        self._client = client
        self._token: str | None = None
        # Optional write seam (oauth.SecretWriter — any object with put_secret):
        # when present, a refreshed OAuth access token is persisted back to the
        # vault slot so the next sync starts from the new token. Absent (the
        # default / pasted-key path) = no write-back; the refreshed token is still
        # used for THIS run.
        self._secret_writer = secret_writer

    # -- auth ------------------------------------------------------------ #
    def authenticate(self) -> None:
        # Resolve the PER-TENANT vaulted token (uplift/{tenant_id}/hubspot) and
        # ONLY that; never log/return the raw value. There is deliberately NO
        # shared-token fallback: a shared token belongs to ONE HubSpot portal,
        # so "falling back" would sync that portal's data under THIS tenant.
        # Only "the secret does not exist" maps to the hard credential error —
        # any other provider failure (access denied / throttle / network) is a
        # different operational problem and propagates untouched.
        per_tenant_ref = tenant_secret_ref(self.tenant_id, self.source)
        try:
            token = self._secrets.get_secret(per_tenant_ref)
        except SecretNotFoundError as exc:
            log.error(
                "ingest auth failed: event=missing_tenant_credential tenant_id=%s "
                "source=%s ref=%s reason=secret_not_provisioned",
                self.tenant_id, self.source, per_tenant_ref,
            )
            raise MissingTenantCredentialError(
                self.tenant_id, self.source, per_tenant_ref, "not provisioned"
            ) from exc
        if not token:
            log.error(
                "ingest auth failed: event=missing_tenant_credential tenant_id=%s "
                "source=%s ref=%s reason=empty_secret_value",
                self.tenant_id, self.source, per_tenant_ref,
            )
            raise MissingTenantCredentialError(
                self.tenant_id, self.source, per_tenant_ref, "empty value"
            )
        # The vault slot holds EITHER a legacy pasted private-app token (a bare
        # string) OR — for the OAuth "connect with login" path — a JSON envelope
        # with a refresh_token. parse_oauth_secret tells them apart; a bare token
        # falls through unchanged (back-compat). NEVER log the resolved value.
        self._token = self._resolve_bearer(token, per_tenant_ref)
        # Hand the resolved token to the injected source client when it accepts
        # one (HubSpotRestClient does; test fakes need not implement set_token).
        set_token = getattr(self._client, "set_token", None)
        if callable(set_token):
            set_token(self._token)
        self._authed = True

    # -- OAuth-aware bearer resolution ----------------------------------- #
    def _resolve_bearer(self, raw_value: str, ref: str) -> str:
        """Turn the vaulted secret value into the bearer token to use this run.

        Bare string -> use as-is (legacy pasted private-app token). OAuth envelope
        -> use its access_token, refreshing first (and writing the new tokens back
        to the vault when a writer is wired) if the access token is at/near expiry.
        Refresh resolves the app's client_id/client_secret via the SAME injected
        SecretProvider as the tenant token. NEVER logs a token value.
        """
        from .oauth import (  # noqa: PLC0415 — local import keeps base import-light
            get_provider,
            is_expired,
            oauth_secret_value,
            parse_oauth_secret,
            refresh_access_token,
        )

        secret = parse_oauth_secret(raw_value)
        if secret is None:
            return raw_value  # legacy bare token — unchanged behavior

        if not is_expired(secret):
            return secret["access_token"]

        provider = get_provider(self.source)
        if provider is None:  # defensive — hubspot is registered
            return secret["access_token"]
        # Resolve the app's client credentials (refs, not values) via the reader.
        try:
            client_id = self._secrets.get_secret(provider.client_id_ref)
            client_secret = self._secrets.get_secret(provider.client_secret_ref)
        except SecretNotFoundError as exc:
            # Can't refresh without the app creds — fail honestly (reconnect), never
            # ride a known-expired access token into a silent 401 mid-sync.
            log.error(
                "ingest auth failed: event=oauth_refresh_unconfigured tenant_id=%s "
                "source=%s reason=client_creds_not_provisioned", self.tenant_id, self.source,
            )
            raise MissingTenantCredentialError(
                self.tenant_id, self.source, ref,
                "OAuth token expired and app client credentials are not provisioned",
            ) from exc
        # Refresh (HTTP via oauth.post_form — the test seam). Any failure propagates
        # untouched; it carries no token material.
        new = refresh_access_token(
            provider, refresh_token=secret["refresh_token"],
            client_id=client_id, client_secret=client_secret,
        )
        log.info("ingest oauth: refreshed access token tenant_id=%s source=%s",
                 self.tenant_id, self.source)
        if self._secret_writer is not None:
            self._secret_writer.put_secret(
                ref,
                oauth_secret_value(
                    access_token=new["access_token"],
                    refresh_token=new["refresh_token"],
                    expires_at=new["expires_at"],
                ),
            )
        return new["access_token"]

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
# odd one out — HubSpot CRM v3 docs confirm contacts use `lastmodifieddate`
# while all other types use `hs_lastmodifieddate`).
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

    # API contract (HubSpot CRM v3 Search, confirmed):
    #   * filter `value` for datetime properties accepts ISO-8601 strings —
    #     the cursor stores ISO-8601 `updatedAt` strings, which is the correct
    #     format (epoch-millis are NOT required by the documented API).
    #   * notes ARE searchable at /crm/v3/objects/notes/search (v3 supports
    #     notes as a first-class object type with search).
    #   * association properties (`associatedcompanyid`, `associatedcontactid`)
    #     are returned by search when listed in the `properties` array.
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
