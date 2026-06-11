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

Credential format: the vaulted secret value is EITHER a bare API token, OR a
JSON object `{"token": "...", "location_id": "..."}` (GHL API v2 scopes most
list endpoints to a location). Read sync only: this connector never writes
back to GoHighLevel (draft-only invariant).

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

    def __init__(self, tenant_id, *, client: GoHighLevelClient, **kwargs) -> None:
        super().__init__(tenant_id, **kwargs)
        self._client = client

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
        token, location_id = self._parse_credential(raw)
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
# GHL API v2 requires a pinned Version header on every call. # VERIFY: current value.
GHL_API_VERSION = "2021-07-28"


class GoHighLevelRestClient:
    """Minimal real GHL v2 client. EXPERIMENTAL — verify before first prod run.

    Constructed UNAUTHENTICATED — `GoHighLevelConnector.authenticate()` resolves
    the vaulted credential and injects it via `set_token()`/`set_location()`,
    so the raw token never transits the runtime wiring. Read-only GETs only.

    # VERIFY against the live GoHighLevel API v2 before first prod run:
    #   * GET /contacts/?locationId=...&limit=...&startAfterId=... pagination
    #     (the v2 contacts list pages via meta.startAfterId / meta.startAfter),
    #   * GET /opportunities/search?location_id=... result + pagination shape,
    #   * server-side incremental filters; until verified, `since` is applied
    #     CLIENT-side on dateUpdated/updatedAt after a full page walk.
    """

    def __init__(self, *, base_url: str = GHL_API_BASE, page_size: int = 100,
                 timeout_s: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._page_size = min(int(page_size), 100)  # VERIFY: GHL caps list pages at 100
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
                "startAfterId": start_after_id,  # VERIFY: v2 cursor param name
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
                "page": page_num,  # VERIFY: v2 search pagination param
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
