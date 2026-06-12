"""Microsoft 365 (Graph) connector (read sync) — EXPERIMENTAL.

Mirrors the HubSpot/GoHighLevel reference connectors: the source client is
INJECTED (tests pass a fake fed from recorded Graph fixtures — NO live Graph call
ever happens in CI), credentials resolve from the PER-TENANT vault slot
`uplift/{tenant_id}/microsoft` via the injected SecretProvider, and a missing/empty
per-tenant secret is a HARD MissingTenantCredentialError (no shared-token fallback —
one Microsoft token belongs to ONE M365 tenant, so a fallback would sync someone
else's mailbox under THIS tenant's rows).

Credential format — the vault slot holds ONE of:
  1. An OAuth envelope (the "connect with login" path) — JSON with
     `token_type:"oauth"`, an access_token/refresh_token, expires_at. An expired
     access token is refreshed (grant_type=refresh_token via the shared
     ingest.connectors.oauth helpers) and, when a SecretWriter is wired, the new
     envelope is written back to the slot.
  2. A bare access-token string (back-compat / manual provisioning).
Read sync only: this connector never writes back to Microsoft 365 (draft-only
invariant); the only write is the vault refresh above.

DELTA QUERY SYNC (this is the cursor model — NOT a high-water timestamp):
Microsoft Graph supports delta queries on `/me/messages`, `/me/events`
(via `calendarView`) and `/me/contacts`. The FIRST sync calls `…/delta`; Graph
walks pages via `@odata.nextLink` and ends with an `@odata.deltaLink`. We persist
that deltaLink PER RESOURCE as the cursor; the NEXT sync GETs the stored deltaLink
and Graph returns only what changed since. A `@removed` item is a tombstone (the
object was deleted/moved out of scope) — we skip it (our structured sink upserts;
it has no delete path) rather than normalize a ghost row. If a deltaLink has
expired (HTTP 410 Gone / `syncStateNotFound`), we fall back to a FULL resync of
that resource (delta with no token).

Cursor encoding: the pipeline (ingest/pipeline.py) persists a single high-water
string per (tenant, source) and advances it to `max(record.updated_at)` on a clean
run. So we pack the three resource deltaLinks into ONE JSON string and stamp it on
every record's `updated_at`; a monotonic `v` field (the max object
`lastModifiedDateTime` seen this run) leads the JSON so the pipeline's
lexicographic `>` compare advances correctly. See `_encode_cursor`/`_decode_cursor`.

Normalizes Graph objects to the db/schema.sql shapes:
  messages -> activities  (kind="email")
  events   -> activities  (kind="meeting")
  contacts -> contacts    (+ the contact's organization -> companies)
carrying tenant_id, source='microsoft' on every row.
"""
from __future__ import annotations

import json
import logging
from typing import Iterable, Protocol, runtime_checkable

from .base import (
    Connector,
    MissingTenantCredentialError,
    NormalizedRecord,
    SecretNotFoundError,
    tenant_secret_ref,
)

log = logging.getLogger("ingest.connectors.microsoft")

#: The Graph resources this connector delta-syncs, in pull order (contacts last so
#: their company rows land alongside). Each maps to a `/me/{resource}/delta` query
#: in the real client.
RESOURCES: tuple[str, ...] = ("messages", "events", "contacts")


class DeltaLinkExpired(RuntimeError):
    """A stored `@odata.deltaLink` is no longer usable (Graph 410 Gone /
    `syncStateNotFound`). The connector recovers by full-resyncing that resource
    (delta with no token). Carries no token material; safe to log."""


@runtime_checkable
class MicrosoftGraphClient(Protocol):
    """Minimal source-client interface the connector depends on.

    `delta(resource, delta_link)` returns ``(items, next_delta_link)`` where
    `items` is the list of raw Graph objects changed since `delta_link` (None =
    full sync) — INCLUDING `@removed` tombstones — and `next_delta_link` is the
    `@odata.deltaLink` to persist for the next run. It MUST raise
    :class:`DeltaLinkExpired` when `delta_link` is stale (HTTP 410)."""

    def delta(self, resource: str, delta_link: str | None) -> tuple[list[dict], str]: ...


# --------------------------------------------------------------------------- #
# Cursor codec — pack the per-resource deltaLinks into ONE pipeline cursor string.
# --------------------------------------------------------------------------- #
def _encode_cursor(*, high_water: str, links: dict[str, str]) -> str:
    """Serialize ``{resource: deltaLink}`` + a monotonic high-water into the single
    cursor string the pipeline persists.

    `v` (the max object lastModifiedDateTime seen this run) is emitted FIRST so two
    cursor strings compare lexicographically by timestamp — that is what lets the
    pipeline's ``record.updated_at > since`` advance the high-water across runs even
    though the deltaLinks themselves are opaque/unordered."""
    return json.dumps({"v": high_water, "links": links}, separators=(",", ":"))


def _decode_cursor(since: str | None) -> dict[str, str]:
    """Recover ``{resource: deltaLink}`` from a stored cursor string.

    Tolerant: None/empty/legacy/garbage -> ``{}`` (i.e. full delta on every
    resource), never an exception — a sync must never wedge on an unreadable
    cursor."""
    if not since:
        return {}
    try:
        obj = json.loads(since)
    except (ValueError, TypeError):
        return {}
    if not isinstance(obj, dict):
        return {}
    links = obj.get("links")
    if not isinstance(links, dict):
        return {}
    return {k: v for k, v in links.items() if isinstance(v, str) and v}


class MicrosoftConnector(Connector):
    """EXPERIMENTAL read-sync connector for Microsoft 365 / Graph (see module docstring)."""

    source = "microsoft"

    def __init__(self, tenant_id, *, client: MicrosoftGraphClient,
                 secret_writer=None, **kwargs) -> None:
        super().__init__(tenant_id, **kwargs)
        self._client = client
        # Optional write seam (oauth.SecretWriter — any object with put_secret):
        # when present a refreshed OAuth access token is persisted back to the vault
        # slot so the next sync starts fresh. Absent (bare-token path) = no
        # write-back; a refreshed token is still used for THIS run.
        self._secret_writer = secret_writer
        #: the combined cursor produced by the last pull() (also stamped on records).
        self.next_cursor: str | None = None

    # -- auth ------------------------------------------------------------ #
    def authenticate(self) -> None:
        # Resolve the PER-TENANT vaulted credential (uplift/{tenant_id}/microsoft)
        # and ONLY that — never log/echo the raw value, never fall back to a shared
        # token (THE isolation boundary; see module docstring).
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
        token = self._resolve_credential(raw, ref)
        if not token:
            raise MissingTenantCredentialError(
                self.tenant_id, self.source, ref, "credential has no access token"
            )
        set_token = getattr(self._client, "set_token", None)
        if callable(set_token):
            set_token(token)
        self._authed = True

    # -- OAuth-aware credential resolution -------------------------------- #
    def _resolve_credential(self, raw: str, ref: str) -> str:
        """Turn the vaulted secret value into a bearer token for this run.

        OAuth envelope -> use its access_token (refreshing first, and writing the
        new envelope back when a writer is wired, if it's at/near expiry). Anything
        else is treated as a bare access token (back-compat). NEVER logs a token."""
        from .oauth import (  # noqa: PLC0415 — local import keeps base import-light
            get_provider,
            is_expired,
            oauth_secret_value,
            parse_oauth_secret,
            refresh_access_token,
        )

        secret = parse_oauth_secret(raw)
        if secret is None:
            return raw.strip()  # legacy bare access token — unchanged

        if not is_expired(secret):
            return secret["access_token"]

        provider = get_provider(self.source)
        if provider is None:  # defensive — microsoft is registered
            return secret["access_token"]
        # Resolve the app's client credentials (refs, not values) via the reader.
        try:
            client_id = self._secrets.get_secret(provider.client_id_ref)
            client_secret = self._secrets.get_secret(provider.client_secret_ref)
        except SecretNotFoundError as exc:
            # Can't refresh without the app creds — fail honestly (reconnect), never
            # ride a known-expired token into a silent 401 mid-sync.
            log.error(
                "ingest auth failed: event=oauth_refresh_unconfigured tenant_id=%s "
                "source=%s reason=client_creds_not_provisioned", self.tenant_id, self.source,
            )
            raise MissingTenantCredentialError(
                self.tenant_id, self.source, ref,
                "OAuth token expired and app client credentials are not provisioned",
            ) from exc
        # Refresh (HTTP via oauth.post_form — the test seam). Any failure propagates
        # untouched; it carries no token material. Microsoft rolls the refresh_token,
        # so _tokens_from_response captures the new one (falling back to the old).
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
        """Delta-query each resource, normalize, and stamp the combined next-cursor.

        Eager (not lazily yielded): we must know EVERY resource's new deltaLink and
        the global high-water before stamping it on each record, and the pipeline
        list()s the result anyway."""
        self._require_auth()
        prior = _decode_cursor(since_cursor)
        new_links: dict[str, str] = {}
        records: list[NormalizedRecord] = []
        high_water = ""

        for resource in RESOURCES:
            items, delta_link = self._delta(resource, prior.get(resource))
            if delta_link:
                new_links[resource] = delta_link
            for obj in items:
                if "@removed" in obj:
                    # Tombstone: the object left scope (deleted/moved). We upsert-only,
                    # so there is nothing to normalize — skip it, but its deltaLink
                    # still advances so we don't re-see it.
                    log.debug("microsoft: skipping @removed tombstone id=%s resource=%s",
                              obj.get("id"), resource)
                    continue
                for rec in self._normalize(resource, obj):
                    records.append(rec)
                    lm = obj.get("lastModifiedDateTime") or ""
                    if lm > high_water:
                        high_water = lm

        # Preserve deltaLinks for resources that returned nothing new this run so the
        # cursor never loses a resource's position.
        for resource, link in prior.items():
            new_links.setdefault(resource, link)
        self.next_cursor = _encode_cursor(high_water=high_water, links=new_links)
        # Stamp the combined cursor on every record so the pipeline persists it.
        for rec in records:
            rec.updated_at = self.next_cursor
        return records

    def _delta(self, resource: str, delta_link: str | None) -> tuple[list[dict], str]:
        """One resource's delta, with a 410/expired-link FULL-RESYNC fallback."""
        try:
            return self._client.delta(resource, delta_link)
        except DeltaLinkExpired:
            if delta_link is None:
                raise  # a full sync itself failed — not recoverable here
            log.warning("microsoft: deltaLink expired for resource=%s tenant_id=%s — "
                        "full resync", resource, self.tenant_id)
            return self._client.delta(resource, None)

    # -- normalization ---------------------------------------------------- #
    def _normalize(self, resource: str, obj: dict) -> list[NormalizedRecord]:
        if resource == "messages":
            return [self._message(obj)]
        if resource == "events":
            return [self._event(obj)]
        if resource == "contacts":
            return self._contact(obj)
        return []

    def _message(self, obj: dict) -> NormalizedRecord:
        ref = str(obj.get("id", ""))
        subject = obj.get("subject") or ""
        preview = obj.get("bodyPreview") or ""
        sender = (((obj.get("from") or {}).get("emailAddress")) or {}).get("address")
        received = obj.get("receivedDateTime") or ""
        body_parts = [subject] if subject else []
        if preview:
            body_parts.append(preview)
        body = "\n".join(body_parts)
        row = {
            "tenant_id": self.tenant_id,
            "contact_ref_id": None,
            "deal_ref_id": None,
            "kind": "email",
            "body": body,
            "ref_id": ref,
            "source": self.source,
        }
        text = f"Email: {subject}"
        if sender:
            text += f"\nFrom: {sender}"
        if received:
            text += f"\nReceived: {received}"
        if preview:
            text += f"\n{preview}"
        return NormalizedRecord(
            tenant_id=self.tenant_id,
            source=self.source,
            ref_id=ref,
            table="activities",
            row=row,
            raw=obj,
            kind="email",
            text_blocks=[{"ref_id": ref, "kind": "email", "text": text}],
        )

    def _event(self, obj: dict) -> NormalizedRecord:
        ref = str(obj.get("id", ""))
        subject = obj.get("subject") or ""
        preview = obj.get("bodyPreview") or ""
        start = ((obj.get("start") or {}).get("dateTime")) or ""
        organizer = (((obj.get("organizer") or {}).get("emailAddress")) or {}).get("address")
        body_parts = [subject] if subject else []
        if start:
            body_parts.append(f"When: {start}")
        if preview:
            body_parts.append(preview)
        row = {
            "tenant_id": self.tenant_id,
            "contact_ref_id": None,
            "deal_ref_id": None,
            "kind": "meeting",
            "body": "\n".join(body_parts),
            "ref_id": ref,
            "source": self.source,
        }
        text = f"Meeting: {subject}"
        if start:
            text += f"\nWhen: {start}"
        if organizer:
            text += f"\nOrganizer: {organizer}"
        if preview:
            text += f"\n{preview}"
        return NormalizedRecord(
            tenant_id=self.tenant_id,
            source=self.source,
            ref_id=ref,
            table="activities",
            row=row,
            raw=obj,
            kind="meeting",
            text_blocks=[{"ref_id": ref, "kind": "meeting", "text": text}],
        )

    def _contact(self, obj: dict) -> list[NormalizedRecord]:
        ref = str(obj.get("id", ""))
        name = (
            obj.get("displayName")
            or " ".join(x for x in [obj.get("givenName"), obj.get("surname")] if x)
            or ""
        )
        email = self._first_email(obj)
        phone = self._first_phone(obj)
        company_name = obj.get("companyName") or ""
        # The contact's organization -> a companies row (deterministic ref_id =
        # the company name, so repeat contacts at the same org coalesce). The
        # contact's company_ref_id points at it; None when the contact has no org.
        company_ref = company_name or None
        records: list[NormalizedRecord] = []
        if company_name:
            records.append(self._company(company_name, domain=self._email_domain(email)))
        row = {
            "tenant_id": self.tenant_id,
            "company_ref_id": company_ref,
            "name": name,
            "email": email,
            "phone": phone,
            "ref_id": ref,
            "source": self.source,
        }
        parts = [f"Contact: {name}"]
        if email:
            parts.append(f"Email: {email}")
        if phone:
            parts.append(f"Phone: {phone}")
        if obj.get("jobTitle"):
            parts.append(f"Title: {obj['jobTitle']}")
        if company_name:
            parts.append(f"Company: {company_name}")
        records.append(NormalizedRecord(
            tenant_id=self.tenant_id,
            source=self.source,
            ref_id=ref,
            table="contacts",
            row=row,
            raw=obj,
            kind="contact",
            text_blocks=[{"ref_id": ref, "kind": "contact", "text": "\n".join(parts)}],
        ))
        return records

    def _company(self, name: str, *, domain: str | None) -> NormalizedRecord:
        row = {
            "tenant_id": self.tenant_id,
            "name": name,
            "domain": domain,
            "ref_id": name,  # deterministic: the org name is the ref
            "source": self.source,
        }
        text = f"Company: {name}"
        if domain:
            text += f"\nDomain: {domain}"
        return NormalizedRecord(
            tenant_id=self.tenant_id,
            source=self.source,
            ref_id=name,
            table="companies",
            row=row,
            raw={"companyName": name, "domain": domain},
            kind="company",
            text_blocks=[{"ref_id": name, "kind": "company", "text": text}],
        )

    @staticmethod
    def _first_email(obj: dict) -> str | None:
        for entry in obj.get("emailAddresses") or []:
            if isinstance(entry, dict) and entry.get("address"):
                return str(entry["address"])
        return None

    @staticmethod
    def _first_phone(obj: dict) -> str | None:
        for phone in obj.get("businessPhones") or []:
            if phone:
                return str(phone)
        return str(obj["mobilePhone"]) if obj.get("mobilePhone") else None

    @staticmethod
    def _email_domain(email: str | None) -> str | None:
        if email and "@" in email:
            return email.rsplit("@", 1)[1] or None
        return None


# --------------------------------------------------------------------------- #
# Real Microsoft Graph client — EXPERIMENTAL (stdlib urllib, no new dependency,
# no import-time network). Satisfies the MicrosoftGraphClient Protocol;
# constructed only by the registry wiring in ingest/run_sync.py, NEVER in CI
# (tests use recorded fixtures via the injected fake).
# --------------------------------------------------------------------------- #
GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

#: Graph delta endpoint per resource. `events` uses calendarView/delta (the
#: documented delta surface for calendar) with a bounded window.
#: # VERIFY against a live mailbox on first prod run.
_DELTA_PATHS = {
    "messages": "/me/messages/delta",
    "events": "/me/calendarView/delta",
    "contacts": "/me/contacts/delta",
}
# calendarView/delta REQUIRES a window; default to a rolling year either side.
# # VERIFY: widen/narrow per product needs once live.
_CALENDAR_WINDOW_DAYS = 365
_MAX_RETRIES = 5


class MicrosoftGraphRestClient:
    """Minimal real Graph client. EXPERIMENTAL — verify before first prod run.

    Constructed UNAUTHENTICATED — `MicrosoftConnector.authenticate()` resolves the
    vaulted credential and injects it via `set_token()`, so the raw token never
    transits the runtime wiring. Read-only GETs only.

    Throttling: Graph answers 429 (and sometimes 503) with a `Retry-After` header;
    `_get` honors it (bounded retries) instead of failing the whole sync.
    """

    def __init__(self, *, base_url: str = GRAPH_API_BASE, page_size: int = 100,
                 timeout_s: float = 30.0, sleep=None) -> None:
        self._base_url = base_url.rstrip("/")
        self._page_size = max(1, min(int(page_size), 100))
        self._timeout_s = timeout_s
        self._token: str | None = None
        # injectable so tests never really sleep; default lazily binds time.sleep.
        self._sleep = sleep

    def set_token(self, token: str) -> None:
        self._token = token

    def _initial_url(self, resource: str) -> str:
        import urllib.parse  # noqa: PLC0415 — lazy
        from datetime import datetime, timedelta, timezone  # noqa: PLC0415

        path = _DELTA_PATHS.get(resource)
        if path is None:
            raise ValueError(f"microsoft: unknown delta resource {resource!r}")
        params: dict[str, str] = {"$top": str(self._page_size)}
        if resource == "events":
            now = datetime.now(timezone.utc)
            window = timedelta(days=_CALENDAR_WINDOW_DAYS)
            params["startDateTime"] = (now - window).strftime("%Y-%m-%dT%H:%M:%SZ")
            params["endDateTime"] = (now + window).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"{self._base_url}{path}?{urllib.parse.urlencode(params)}"

    def delta(self, resource: str, delta_link: str | None) -> tuple[list[dict], str]:
        """Walk a Graph delta query to its `@odata.deltaLink`, honoring 429s.

        Follows `@odata.nextLink` pages, accumulates `value`, and returns
        ``(items, deltaLink)``. Raises :class:`DeltaLinkExpired` on a 410 (stale
        deltaLink) so the connector can full-resync."""
        url = delta_link or self._initial_url(resource)
        items: list[dict] = []
        next_link = ""
        while url:
            page = self._get(url)
            items.extend(page.get("value") or [])
            url = page.get("@odata.nextLink") or ""
            if not url:
                next_link = page.get("@odata.deltaLink") or ""
        return items, next_link

    def _get(self, url: str) -> dict:
        import time as _time  # noqa: PLC0415
        import urllib.error  # noqa: PLC0415 — lazy: no network machinery at import
        import urllib.request  # noqa: PLC0415

        if not self._token:
            raise RuntimeError("MicrosoftGraphRestClient: no token — authenticate() must run first")
        sleep = self._sleep or _time.sleep
        attempt = 0
        while True:
            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/json",
                },
                method="GET",
            )
            try:
                with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:  # noqa: S310 — fixed https base/deltaLink
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 410:
                    # Stale deltaLink — the connector recovers with a full resync.
                    raise DeltaLinkExpired("microsoft: deltaLink returned 410 Gone") from exc
                if exc.code in (429, 503) and attempt < _MAX_RETRIES:
                    retry_after = self._retry_after_seconds(exc)
                    attempt += 1
                    log.warning("microsoft: throttled (HTTP %s) — retrying in %ss "
                                "(attempt %d/%d)", exc.code, retry_after, attempt, _MAX_RETRIES)
                    sleep(retry_after)
                    continue
                raise

    @staticmethod
    def _retry_after_seconds(exc) -> float:
        """Parse the `Retry-After` header (delta-seconds) off a throttled response;
        default to a conservative backoff when absent/unparseable."""
        try:
            headers = exc.headers
            value = headers.get("Retry-After") if headers else None
            if value is not None:
                return max(0.0, float(value))
        except (TypeError, ValueError, AttributeError):
            pass
        return 5.0
