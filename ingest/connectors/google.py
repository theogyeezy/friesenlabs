"""Google (Calendar + Contacts) connector (read sync) — EXPERIMENTAL.

Mirrors the Microsoft 365 reference connector: the source client is INJECTED
(tests pass a fake fed from recorded Calendar/People fixtures — NO live Google API
call ever happens in CI), credentials resolve from the PER-TENANT vault slot
`uplift/{tenant_id}/google` via the injected SecretProvider, and a missing/empty
per-tenant secret is a HARD MissingTenantCredentialError (no shared-token fallback —
one Google token belongs to ONE Google account, so a fallback would sync someone
else's calendar/contacts under THIS tenant's rows).

GMAIL IS DEFERRED. Gmail's scopes are Google "restricted" scopes that require a
Google CASA (Cloud Application Security Assessment) third-party audit. Calendar +
Contacts are "sensitive" scopes — they need OAuth-consent-screen verification but
NOT CASA. So this connector pulls ONLY Calendar events + Contacts; no Gmail.

Credential format — the vault slot holds ONE of:
  1. An OAuth envelope (the "connect with login" path) — JSON with
     `token_type:"oauth"`, an access_token/refresh_token, expires_at. An expired
     access token is refreshed (grant_type=refresh_token via the shared
     ingest.connectors.oauth helpers) and, when a SecretWriter is wired, the new
     envelope is written back to the slot. (Google does NOT roll the refresh_token
     on refresh — `_tokens_from_response` preserves the old one via fallback.)
  2. A bare access-token string (back-compat / manual provisioning).
Read sync only: this connector never writes back to Google (draft-only invariant);
the only write is the vault refresh above.

SYNC-TOKEN SYNC (this is the cursor model — NOT a high-water timestamp):
Both Google APIs offer incremental sync via opaque sync tokens:
  * Calendar `events.list` returns a `nextSyncToken` on the final page; the NEXT
    sync passes it as `syncToken` and Calendar returns only what changed. A deleted
    event comes back with `status:"cancelled"` (a tombstone). If the syncToken is
    stale Calendar answers HTTP 410 Gone -> the real client raises SyncTokenExpired
    and the connector FULL-RESYNCs that resource (no syncToken).
  * People `people.connections.list` with `requestSyncToken=true` returns a
    `nextSyncToken`; the NEXT sync passes it as `syncToken`. A deleted contact comes
    back with `metadata.deleted:true` (a tombstone). A stale token yields the People
    error `EXPIRED_SYNC_TOKEN` (HTTP 400) -> the real client raises
    SyncTokenExpired and the connector FULL-RESYNCs.
We persist BOTH resources' nextSyncTokens packed into ONE pipeline cursor string.

Cursor encoding: the pipeline (ingest/pipeline.py) persists a single high-water
string per (tenant, source) and advances it to `max(record.updated_at)` on a clean
run. So we pack the two resource syncTokens into ONE JSON string and stamp it on
every record's `updated_at`; a monotonic `v` field (the max object update time seen
this run) leads the JSON so the pipeline's lexicographic `>` compare advances
correctly. See `_encode_cursor`/`_decode_cursor`.

Normalizes Google objects to the db/schema.sql shapes:
  events   -> activities  (kind="meeting")
  contacts -> contacts     (+ the contact's organization -> companies)
carrying tenant_id, source='google' on every row.
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

log = logging.getLogger("ingest.connectors.google")

#: The Google resources this connector sync-token-syncs, in pull order (contacts
#: last so their company rows land alongside). NO gmail (CASA — see module docstring).
RESOURCES: tuple[str, ...] = ("events", "contacts")


class SyncTokenExpired(RuntimeError):
    """A stored sync token is no longer usable (Calendar HTTP 410 Gone / People
    `EXPIRED_SYNC_TOKEN`). The connector recovers by full-resyncing that resource
    (list with no syncToken). Carries no token material; safe to log."""


@runtime_checkable
class GoogleClient(Protocol):
    """Minimal source-client interface the connector depends on.

    `sync(resource, sync_token)` returns ``(items, next_sync_token)`` where `items`
    is the list of raw Google objects changed since `sync_token` (None = full sync)
    — INCLUDING tombstones (`status:"cancelled"` events / `metadata.deleted`
    contacts) — and `next_sync_token` is the token to persist for the next run. It
    MUST raise :class:`SyncTokenExpired` when `sync_token` is stale (Calendar 410 /
    People EXPIRED_SYNC_TOKEN)."""

    def sync(self, resource: str, sync_token: str | None) -> tuple[list[dict], str]: ...


# --------------------------------------------------------------------------- #
# Cursor codec — pack the per-resource syncTokens into ONE pipeline cursor string.
# --------------------------------------------------------------------------- #
def _encode_cursor(*, high_water: str, tokens: dict[str, str]) -> str:
    """Serialize ``{resource: syncToken}`` + a monotonic high-water into the single
    cursor string the pipeline persists.

    `v` (the max object update time seen this run) is emitted FIRST so two cursor
    strings compare lexicographically by timestamp — that is what lets the pipeline's
    ``record.updated_at > since`` advance the high-water across runs even though the
    syncTokens themselves are opaque/unordered."""
    return json.dumps({"v": high_water, "tokens": tokens}, separators=(",", ":"))


def _decode_cursor(since: str | None) -> dict[str, str]:
    """Recover ``{resource: syncToken}`` from a stored cursor string.

    Tolerant: None/empty/legacy/garbage -> ``{}`` (i.e. full sync on every
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
    tokens = obj.get("tokens")
    if not isinstance(tokens, dict):
        return {}
    return {k: v for k, v in tokens.items() if isinstance(v, str) and v}


class GoogleConnector(Connector):
    """EXPERIMENTAL read-sync connector for Google Calendar + Contacts (see module
    docstring). NO Gmail (CASA)."""

    source = "google"

    def __init__(self, tenant_id, *, client: GoogleClient,
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
        # Resolve the PER-TENANT vaulted credential (uplift/{tenant_id}/google) and
        # ONLY that — never log/echo the raw value, never fall back to a shared
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
        if provider is None:  # defensive — google is registered
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
        # untouched; it carries no token material. Google does NOT roll the
        # refresh_token, so _tokens_from_response preserves the existing one.
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
        """Sync-token query each resource, normalize, and stamp the combined
        next-cursor.

        Eager (not lazily yielded): we must know EVERY resource's new syncToken and
        the global high-water before stamping it on each record, and the pipeline
        list()s the result anyway."""
        self._require_auth()
        prior = _decode_cursor(since_cursor)
        new_tokens: dict[str, str] = {}
        records: list[NormalizedRecord] = []
        high_water = ""

        for resource in RESOURCES:
            items, sync_token = self._sync(resource, prior.get(resource))
            if sync_token:
                new_tokens[resource] = sync_token
            for obj in items:
                if self._is_tombstone(resource, obj):
                    # Tombstone: the object left scope (deleted/cancelled). We
                    # upsert-only, so there is nothing to normalize — skip it, but
                    # its syncToken still advances so we don't re-see it.
                    log.debug("google: skipping deleted tombstone resource=%s", resource)
                    continue
                for rec in self._normalize(resource, obj):
                    records.append(rec)
                lm = self._updated_at(resource, obj)
                if lm > high_water:
                    high_water = lm

        # Preserve syncTokens for resources that returned nothing new this run so the
        # cursor never loses a resource's position.
        for resource, token in prior.items():
            new_tokens.setdefault(resource, token)
        self.next_cursor = _encode_cursor(high_water=high_water, tokens=new_tokens)
        # Stamp the combined cursor on every record so the pipeline persists it.
        for rec in records:
            rec.updated_at = self.next_cursor
        return records

    def _sync(self, resource: str, sync_token: str | None) -> tuple[list[dict], str]:
        """One resource's sync, with a stale-syncToken FULL-RESYNC fallback."""
        try:
            return self._client.sync(resource, sync_token)
        except SyncTokenExpired:
            if sync_token is None:
                raise  # a full sync itself failed — not recoverable here
            log.warning("google: syncToken expired for resource=%s tenant_id=%s — "
                        "full resync", resource, self.tenant_id)
            return self._client.sync(resource, None)

    # -- tombstones / timestamps ------------------------------------------ #
    @staticmethod
    def _is_tombstone(resource: str, obj: dict) -> bool:
        if resource == "events":
            # Calendar marks a deleted/declined event with status:"cancelled".
            return obj.get("status") == "cancelled"
        if resource == "contacts":
            # People marks a removed connection with metadata.deleted:true.
            return bool((obj.get("metadata") or {}).get("deleted"))
        return False

    @staticmethod
    def _updated_at(resource: str, obj: dict) -> str:
        if resource == "events":
            return obj.get("updated") or ""
        # People: the most recent source updateTime under metadata.sources.
        latest = ""
        for src in (obj.get("metadata") or {}).get("sources") or []:
            t = src.get("updateTime") if isinstance(src, dict) else None
            if t and t > latest:
                latest = t
        return latest

    # -- normalization ---------------------------------------------------- #
    def _normalize(self, resource: str, obj: dict) -> list[NormalizedRecord]:
        if resource == "events":
            return [self._event(obj)]
        if resource == "contacts":
            return self._contact(obj)
        return []

    def _event(self, obj: dict) -> NormalizedRecord:
        ref = str(obj.get("id", ""))
        summary = obj.get("summary") or ""
        description = obj.get("description") or ""
        start = self._event_start(obj)
        organizer = (obj.get("organizer") or {}).get("email")
        body_parts = [summary] if summary else []
        if start:
            body_parts.append(f"When: {start}")
        if description:
            body_parts.append(description)
        row = {
            "tenant_id": self.tenant_id,
            "contact_ref_id": None,
            "deal_ref_id": None,
            "kind": "meeting",
            "body": "\n".join(body_parts),
            "ref_id": ref,
            "source": self.source,
        }
        text = f"Meeting: {summary}"
        if start:
            text += f"\nWhen: {start}"
        if organizer:
            text += f"\nOrganizer: {organizer}"
        if description:
            text += f"\n{description}"
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
        # People resourceName (e.g. "people/c123…") is the stable id.
        ref = str(obj.get("resourceName") or "")
        name = self._first_name(obj)
        email = self._first_email(obj)
        phone = self._first_phone(obj)
        org = self._first_org(obj)
        company_name = org.get("name") or ""
        title = org.get("title") or ""
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
        if title:
            parts.append(f"Title: {title}")
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
            raw={"name": name, "domain": domain},
            kind="company",
            text_blocks=[{"ref_id": name, "kind": "company", "text": text}],
        )

    # -- People/Calendar field pickers ------------------------------------ #
    @staticmethod
    def _event_start(obj: dict) -> str:
        start = obj.get("start") or {}
        # timed events carry dateTime; all-day events carry date.
        return start.get("dateTime") or start.get("date") or ""

    @staticmethod
    def _first_name(obj: dict) -> str:
        for entry in obj.get("names") or []:
            if isinstance(entry, dict) and entry.get("displayName"):
                return str(entry["displayName"])
        return ""

    @staticmethod
    def _first_email(obj: dict) -> str | None:
        for entry in obj.get("emailAddresses") or []:
            if isinstance(entry, dict) and entry.get("value"):
                return str(entry["value"])
        return None

    @staticmethod
    def _first_phone(obj: dict) -> str | None:
        for entry in obj.get("phoneNumbers") or []:
            if isinstance(entry, dict) and entry.get("value"):
                return str(entry["value"])
        return None

    @staticmethod
    def _first_org(obj: dict) -> dict:
        for entry in obj.get("organizations") or []:
            if isinstance(entry, dict) and (entry.get("name") or entry.get("title")):
                return entry
        return {}

    @staticmethod
    def _email_domain(email: str | None) -> str | None:
        if email and "@" in email:
            return email.rsplit("@", 1)[1] or None
        return None


# --------------------------------------------------------------------------- #
# Real Google client — EXPERIMENTAL (stdlib urllib, no new dependency, no
# import-time network). Satisfies the GoogleClient Protocol; constructed only by
# the registry wiring in ingest/run_sync.py, NEVER in CI (tests use recorded
# fixtures via the injected fake).
# --------------------------------------------------------------------------- #
CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
PEOPLE_API_BASE = "https://people.googleapis.com/v1"
#: The person fields the People API requires on every connections.list call.
PEOPLE_PERSON_FIELDS = "names,emailAddresses,phoneNumbers,organizations,metadata"
_MAX_RETRIES = 5


class GoogleRestClient:
    """Minimal real Google client. EXPERIMENTAL — verify before first prod run.

    Constructed UNAUTHENTICATED — `GoogleConnector.authenticate()` resolves the
    vaulted credential and injects it via `set_token()`, so the raw token never
    transits the runtime wiring. Read-only GETs only.

    Throttling: Google answers 429 (and sometimes 503) with a `Retry-After` header;
    `_get` honors it (bounded retries) instead of failing the whole sync.
    """

    def __init__(self, *, calendar_base: str = CALENDAR_API_BASE,
                 people_base: str = PEOPLE_API_BASE, page_size: int = 100,
                 timeout_s: float = 30.0, sleep=None) -> None:
        self._calendar_base = calendar_base.rstrip("/")
        self._people_base = people_base.rstrip("/")
        self._page_size = max(1, min(int(page_size), 250))
        self._timeout_s = timeout_s
        self._token: str | None = None
        # injectable so tests never really sleep; default lazily binds time.sleep.
        self._sleep = sleep

    def set_token(self, token: str) -> None:
        self._token = token

    def sync(self, resource: str, sync_token: str | None) -> tuple[list[dict], str]:
        if resource == "events":
            return self._sync_events(sync_token)
        if resource == "contacts":
            return self._sync_contacts(sync_token)
        raise ValueError(f"google: unknown sync resource {resource!r}")

    # -- Calendar events.list ------------------------------------------- #
    def _sync_events(self, sync_token: str | None) -> tuple[list[dict], str]:
        """Walk Calendar events.list pages to its `nextSyncToken`, honoring 429s.

        Follows `nextPageToken`, accumulates `items`, and returns
        ``(items, nextSyncToken)``. Raises :class:`SyncTokenExpired` on a 410 (stale
        syncToken) so the connector can full-resync. A full sync (no syncToken)
        requests `showDeleted` off and a single primary calendar."""
        import urllib.parse  # noqa: PLC0415 — lazy

        items: list[dict] = []
        page_token: str | None = None
        next_sync = ""
        while True:
            params: dict[str, str] = {"maxResults": str(self._page_size),
                                      "singleEvents": "true", "showDeleted": "true"}
            if sync_token:
                params["syncToken"] = sync_token
            if page_token:
                params["pageToken"] = page_token
            url = f"{self._calendar_base}/calendars/primary/events?{urllib.parse.urlencode(params)}"
            page = self._get(url)
            items.extend(page.get("items") or [])
            page_token = page.get("nextPageToken")
            if not page_token:
                next_sync = page.get("nextSyncToken") or ""
                break
        return items, next_sync

    # -- People people.connections.list -------------------------------- #
    def _sync_contacts(self, sync_token: str | None) -> tuple[list[dict], str]:
        """Walk People connections.list pages to its `nextSyncToken`, honoring 429s.

        Always sends `requestSyncToken=true` + `personFields`. Raises
        :class:`SyncTokenExpired` when People reports `EXPIRED_SYNC_TOKEN` (a 400 the
        `_get` surface translates) so the connector can full-resync."""
        import urllib.parse  # noqa: PLC0415 — lazy

        items: list[dict] = []
        page_token: str | None = None
        next_sync = ""
        while True:
            params: dict[str, str] = {
                "pageSize": str(self._page_size),
                "personFields": PEOPLE_PERSON_FIELDS,
                "requestSyncToken": "true",
            }
            if sync_token:
                params["syncToken"] = sync_token
            if page_token:
                params["pageToken"] = page_token
            url = f"{self._people_base}/people/me/connections?{urllib.parse.urlencode(params)}"
            page = self._get(url)
            items.extend(page.get("connections") or [])
            page_token = page.get("nextPageToken")
            if not page_token:
                next_sync = page.get("nextSyncToken") or ""
                break
        return items, next_sync

    def _get(self, url: str) -> dict:
        import time as _time  # noqa: PLC0415
        import urllib.error  # noqa: PLC0415 — lazy: no network machinery at import
        import urllib.request  # noqa: PLC0415

        if not self._token:
            raise RuntimeError("GoogleRestClient: no token — authenticate() must run first")
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
                with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:  # noqa: S310 — fixed https base
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 410:
                    # Calendar's stale-syncToken signal — full resync recovers.
                    raise SyncTokenExpired("google: calendar syncToken returned 410 Gone") from exc
                if exc.code == 400 and self._is_expired_sync_token(exc):
                    # People's stale-syncToken signal (EXPIRED_SYNC_TOKEN, a 400).
                    raise SyncTokenExpired("google: people EXPIRED_SYNC_TOKEN") from exc
                if exc.code in (429, 503) and attempt < _MAX_RETRIES:
                    retry_after = self._retry_after_seconds(exc)
                    attempt += 1
                    log.warning("google: throttled (HTTP %s) — retrying in %ss "
                                "(attempt %d/%d)", exc.code, retry_after, attempt, _MAX_RETRIES)
                    sleep(retry_after)
                    continue
                raise

    @staticmethod
    def _is_expired_sync_token(exc) -> bool:
        """Whether a People 400 is the EXPIRED_SYNC_TOKEN error (vs a real bad
        request). People returns it in the error `status`/`details` body."""
        try:
            body = exc.read().decode("utf-8")
        except Exception:  # noqa: BLE001 — body may be unreadable; treat as "not it"
            return False
        return "EXPIRED_SYNC_TOKEN" in body

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
