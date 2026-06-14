"""GoHighLevel connector (read sync) — EXPERIMENTAL.

Mirrors the HubSpot reference connector (ingest/connectors/hubspot.py): the
source client is INJECTED (tests pass a fake fed from recorded fixtures —
NO live GoHighLevel call ever happens in CI), credentials resolve from the
PER-TENANT vault slot `uplift/{tenant_id}/gohighlevel` via the injected
SecretProvider, and a missing/empty per-tenant secret is a HARD
MissingTenantCredentialError (no shared-token fallback, same isolation
rationale as HubSpot).

EXPERIMENTAL STATUS: the normalization below is built from recorded GHL API
v2 response shapes, not a certified integration. The real REST client
(:class:`GoHighLevelRestClient`) is constructed only by the runtime wiring
(ingest/run_sync.py via the connector registry), never in CI, and every
endpoint/param it touches is flagged `# VERIFY` — confirm against a live
GoHighLevel location before first prod run.

Credential format: the vaulted secret value is ONE of three shapes, detected at
`authenticate()`:
  1. An OAuth envelope (the "connect with login" path) — JSON with
     `token_type:"oauth"`, an access_token/refresh_token, and (from the
     LeadConnector token response) the chosen `location_id`/`company_id`. An
     expired access token is refreshed (grant_type=refresh_token) and, when a
     SecretWriter is wired, the new envelope is written back to the vault slot.
  2. A legacy JSON object `{"token": "...", "location_id": "..."}` (pasted token +
     explicit location).
  3. A bare API token string.
GHL API v2 scopes most list endpoints to a location, so the location_id is carried
through to the source client. Read sync only: this connector never writes back to
GoHighLevel (draft-only invariant); the only write is the vault refresh above.

Normalizes GHL objects to the db/schema.sql shapes:
  contacts      -> contacts   (name/email/phone, ref_id = GHL contact id)
  opportunities -> deals      (title/stage/amount, ref_id = GHL opportunity id)
carrying tenant_id, source='gohighlevel' on every row. The incremental cursor
rides the object's ISO-8601 `updatedAt`/`dateUpdated` timestamp.
"""
from __future__ import annotations

import json
import logging
from typing import Iterable, Iterator, Protocol, runtime_checkable

from .base import (
    Connector,
    MissingTenantCredentialError,
    NormalizedRecord,
    SecretNotFoundError,
    tenant_secret_ref,
)

log = logging.getLogger("ingest.connectors.gohighlevel")


@runtime_checkable
class GoHighLevelClient(Protocol):
    """Minimal source-client interface the connector depends on.

    Each method returns an iterable of raw GHL objects (dicts). `since` is the
    high-water cursor (an ISO-8601 last-updated timestamp); None = full pull.
    """

    def list_contacts(self, since: str | None) -> Iterable[dict]: ...
    def list_opportunities(self, since: str | None) -> Iterable[dict]: ...


class GoHighLevelConnector(Connector):
    """EXPERIMENTAL read-sync connector for GoHighLevel (see module docstring)."""

    source = "gohighlevel"

    def __init__(self, tenant_id, *, client: GoHighLevelClient,
                 secret_writer=None, **kwargs) -> None:
        super().__init__(tenant_id, **kwargs)
        self._client = client
        # Optional write seam (oauth.SecretWriter — any object with put_secret):
        # when present, a refreshed OAuth access token is persisted back to the
        # vault slot so the next sync starts fresh. Absent (the pasted-key path) =
        # no write-back; a refreshed token is still used for THIS run.
        self._secret_writer = secret_writer

    # -- auth ------------------------------------------------------------ #
    def authenticate(self) -> None:
        # Resolve the PER-TENANT vaulted credential (uplift/{tenant_id}/gohighlevel)
        # and ONLY that — never log/echo the raw value, never fall back to a
        # shared token (one shared GHL token belongs to ONE agency/location, so a
        # fallback would sync someone else's location under THIS tenant's rows).
        ref = tenant_secret_ref(self.tenant_id, self.source)
        try:
            raw = self._secrets.get_secret(ref)
        except SecretNotFoundError as exc:
            log.error(
                "ingest auth failed: event=missing_tenant_credential tenant_id=%s "
                "source=%s ref=%s reason=secret_not_provisioned",
                self.tenant_id, self.source, ref,
            )
            raise MissingTenantCredentialError(
                self.tenant_id, self.source, ref, "not provisioned"
            ) from exc
        if not raw:
            log.error(
                "ingest auth failed: event=missing_tenant_credential tenant_id=%s "
                "source=%s ref=%s reason=empty_secret_value",
                self.tenant_id, self.source, ref,
            )
            raise MissingTenantCredentialError(
                self.tenant_id, self.source, ref, "empty value"
            )
        # The vault slot holds an OAuth envelope ("connect with login"), a legacy
        # JSON {"token", "location_id"}, or a bare token. _resolve_credential tells
        # them apart and (for an expired OAuth envelope) refreshes. NEVER logs the
        # resolved value.
        token, location_id = self._resolve_credential(raw, ref)
        if not token:
            raise MissingTenantCredentialError(
                self.tenant_id, self.source, ref, "credential JSON has no token"
            )
        # Hand the resolved credential to the injected source client when it
        # accepts one (GoHighLevelRestClient does; test fakes need not).
        set_token = getattr(self._client, "set_token", None)
        if callable(set_token):
            set_token(token)
        set_location = getattr(self._client, "set_location", None)
        if callable(set_location) and location_id:
            set_location(location_id)
        self._authed = True

    # -- OAuth-aware credential resolution -------------------------------- #
    def _resolve_credential(self, raw: str, ref: str) -> tuple[str, str | None]:
        """Turn the vaulted secret value into (bearer_token, location_id) for this run.

        OAuth envelope -> use its access_token (refreshing first, and writing the new
        envelope back when a writer is wired, if it's at/near expiry); the location_id
        rides the envelope (preserved across refresh — the LeadConnector refresh grant
        may not re-echo it). Anything else falls through to the legacy bare-token /
        {"token","location_id"} path unchanged (back-compat). NEVER logs a token value.
        """
        from .oauth import (  # noqa: PLC0415 — local import keeps base import-light
            get_provider,
            is_expired,
            oauth_secret_value,
            parse_oauth_secret,
            refresh_access_token,
        )

        secret = parse_oauth_secret(raw)
        if secret is None:
            return self._parse_credential(raw)  # legacy bare token / JSON — unchanged

        location_id = secret.get("location_id")
        if not is_expired(secret):
            return secret["access_token"], location_id

        provider = get_provider(self.source)
        if provider is None:  # defensive — gohighlevel is registered
            return secret["access_token"], location_id
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
        # The refresh response may omit the location/company — preserve the stored one.
        new_location = new.get("location_id") or location_id
        new_company = new.get("company_id") or secret.get("company_id")
        log.info("ingest oauth: refreshed access token tenant_id=%s source=%s",
                 self.tenant_id, self.source)
        if self._secret_writer is not None:
            self._secret_writer.put_secret(
                ref,
                oauth_secret_value(
                    access_token=new["access_token"],
                    refresh_token=new["refresh_token"],
                    expires_at=new["expires_at"],
                    location_id=new_location,
                    company_id=new_company,
                ),
            )
        return new["access_token"], new_location

    @staticmethod
    def _parse_credential(raw: str) -> tuple[str, str | None]:
        """Bare token, or JSON {"token": ..., "location_id": ...} -> (token, location)."""
        value = raw.strip()
        if value.startswith("{"):
            try:
                obj = json.loads(value)
            except ValueError:
                return value, None  # looked like JSON but isn't — treat as a bare token
            if isinstance(obj, dict):
                return str(obj.get("token") or ""), (
                    str(obj["location_id"]) if obj.get("location_id") else None
                )
        return value, None

    # -- pull ------------------------------------------------------------ #
    def pull(self, since_cursor: str | None) -> Iterable[NormalizedRecord]:
        self._require_auth()
        # contacts first so opportunities can reference contact ref_ids.
        for c in self._client.list_contacts(since_cursor):
            yield self._contact(c)
        for o in self._client.list_opportunities(since_cursor):
            yield self._opportunity(o)

    # -- normalization ---------------------------------------------------- #
    @staticmethod
    def _updated(obj: dict) -> str:
        # GHL v2 uses `dateUpdated` on contacts and `updatedAt` on opportunities
        # (both ISO-8601, lexicographically orderable). Tolerate either.
        return obj.get("dateUpdated") or obj.get("updatedAt") or obj.get("dateAdded") or ""

    def _contact(self, obj: dict) -> NormalizedRecord:
        ref = str(obj.get("id", ""))
        name = (
            obj.get("contactName")
            or " ".join(x for x in [obj.get("firstName"), obj.get("lastName")] if x)
            or obj.get("name")
            or ""
        )
        row = {
            "tenant_id": self.tenant_id,
            "company_ref_id": None,
            "name": name,
            "email": obj.get("email"),
            "phone": obj.get("phone"),
            "ref_id": ref,
            "source": self.source,
        }
        parts = [f"Contact: {name}"]
        if row["email"]:
            parts.append(f"Email: {row['email']}")
        if row["phone"]:
            parts.append(f"Phone: {row['phone']}")
        if obj.get("companyName"):
            parts.append(f"Company: {obj['companyName']}")
        return NormalizedRecord(
            tenant_id=self.tenant_id,
            source=self.source,
            ref_id=ref,
            table="contacts",
            row=row,
            raw=obj,
            updated_at=self._updated(obj),
            kind="contact",
            text_blocks=[{"ref_id": ref, "kind": "contact", "text": "\n".join(parts)}],
        )

    def _opportunity(self, obj: dict) -> NormalizedRecord:
        ref = str(obj.get("id", ""))
        title = obj.get("name") or obj.get("title") or ""
        amount = obj.get("monetaryValue", obj.get("amount"))
        try:
            amount = float(amount) if amount not in (None, "") else None
        except (TypeError, ValueError):
            amount = None
        stage = obj.get("pipelineStageName") or obj.get("status") or "new"
        row = {
            "tenant_id": self.tenant_id,
            "company_ref_id": None,
            "contact_ref_id": (
                str(obj["contactId"]) if obj.get("contactId")
                else (str(obj["contact"]["id"])
                      if isinstance(obj.get("contact"), dict) and obj["contact"].get("id")
                      else None)
            ),
            "title": title,
            "stage": stage,
            "amount": amount,
            "currency": (obj.get("currency") or "USD").upper(),
            "ref_id": ref,
            "source": self.source,
        }
        text = f"Deal: {title}\nStage: {stage}"
        if amount is not None:
            text += f"\nAmount: {amount} {row['currency']}"
        return NormalizedRecord(
            tenant_id=self.tenant_id,
            source=self.source,
            ref_id=ref,
            table="deals",
            row=row,
            raw=obj,
            updated_at=self._updated(obj),
            kind="deal",
            text_blocks=[{"ref_id": ref, "kind": "deal", "text": text}],
        )


# --------------------------------------------------------------------------- #
# Real GoHighLevel API v2 client — EXPERIMENTAL (stdlib urllib, no new
# dependency, no import-time network). Satisfies the GoHighLevelClient
# Protocol; constructed only by the registry wiring in ingest/run_sync.py,
# NEVER in CI (integration tests use recorded fixtures).
# --------------------------------------------------------------------------- #
GHL_API_BASE = "https://services.leadconnectorhq.com"
# GHL API v2 requires a pinned Version header on every call.
# "2021-07-28" is the stable canonical value documented for the v2 API.
GHL_API_VERSION = "2021-07-28"
# GHL fronts the API with Cloudflare, which BANS urllib's default "Python-urllib/x.y" User-Agent
# (Cloudflare error 1010 "browser_signature_banned" → 403 on EVERY call → sync fails). A named,
# non-default UA clears it. LIVE-CONFIRMED 2026-06-13 (default UA → 403 1010; this UA → 200).
GHL_USER_AGENT = "Uplift-Connector/1.0 (+https://friesenlabs.com)"


class GoHighLevelRestClient:
    """Minimal real GHL v2 client. EXPERIMENTAL — verify before first prod run.

    Constructed UNAUTHENTICATED — `GoHighLevelConnector.authenticate()` resolves
    the vaulted credential and injects it via `set_token()`/`set_location()`,
    so the raw token never transits the runtime wiring. Read-only GETs only.

    # API contract (GHL v2, confirmed from docs):
    #   * GET /contacts/ pages via `meta.startAfterId`; the cursor param is
    #     `startAfterId` (confirmed in GHL v2 contacts list documentation).
    #   * GET /opportunities/search pages via `meta.nextPage`; the page param
    #     is `page` with `limit` (confirmed in GHL v2 opportunities docs).
    #   * Max page size is 100 for both endpoints (documented cap).
    # # VERIFY (live access required): GHL v2 contacts/opportunities do not
    # # document a server-side "updated since" query param — `since` filtering
    # # is applied CLIENT-SIDE on dateUpdated/updatedAt after a full page walk
    # # until a live test confirms a server-side filter param exists.
    """

    def __init__(self, *, base_url: str = GHL_API_BASE, page_size: int = 100,
                 timeout_s: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._page_size = min(int(page_size), 100)  # GHL v2 documents 100 as the max page size
        self._timeout_s = timeout_s
        self._token: str | None = None
        self._location_id: str | None = None

    def set_token(self, token: str) -> None:
        self._token = token

    def set_location(self, location_id: str) -> None:
        self._location_id = location_id

    def _get(self, path: str, params: dict) -> dict:
        import urllib.parse  # noqa: PLC0415 — lazy: no network machinery at import
        import urllib.request  # noqa: PLC0415

        if not self._token:
            raise RuntimeError("GoHighLevelRestClient: no token — authenticate() must run first")
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(
            f"{self._base_url}{path}?{qs}",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Version": GHL_API_VERSION,
                "Accept": "application/json",
                "User-Agent": GHL_USER_AGENT,  # avoid Cloudflare 1010 ban (see GHL_USER_AGENT note)
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:  # noqa: S310 — fixed https base
            return json.loads(resp.read().decode("utf-8"))

    def _newer_than(self, obj: dict, since: str | None) -> bool:
        if not since:
            return True
        updated = obj.get("dateUpdated") or obj.get("updatedAt") or obj.get("dateAdded") or ""
        return bool(updated) and updated > since

    # -- GoHighLevelClient Protocol ---------------------------------------- #
    def list_contacts(self, since: str | None) -> Iterator[dict]:
        if not self._location_id:
            raise RuntimeError(
                "GoHighLevelRestClient: no location_id — vault the credential as "
                'JSON {"token": ..., "location_id": ...} for live syncs'
            )
        start_after_id: str | None = None
        while True:
            page = self._get("/contacts/", {
                "locationId": self._location_id,
                "limit": self._page_size,
                "startAfterId": start_after_id,  # GHL v2 contacts cursor param (confirmed)
            })
            items = page.get("contacts", []) or []
            for obj in items:
                if self._newer_than(obj, since):
                    yield obj
            meta = page.get("meta") or {}
            start_after_id = meta.get("startAfterId")
            if not start_after_id or not items:
                return

    def list_opportunities(self, since: str | None) -> Iterator[dict]:
        if not self._location_id:
            raise RuntimeError(
                "GoHighLevelRestClient: no location_id — vault the credential as "
                'JSON {"token": ..., "location_id": ...} for live syncs'
            )
        page_num = 1
        while True:
            page = self._get("/opportunities/search", {
                "location_id": self._location_id,
                "limit": self._page_size,
                "page": page_num,  # GHL v2 opportunities search pagination param (confirmed)
            })
            items = page.get("opportunities", []) or []
            for obj in items:
                if self._newer_than(obj, since):
                    yield obj
            meta = page.get("meta") or {}
            next_page = meta.get("nextPage")
            if not next_page or not items:
                return
            page_num = int(next_page)
