"""Pipedrive connector (read sync) — EXPERIMENTAL.

Mirrors the Salesforce reference connector (ingest/connectors/salesforce.py): the
source client is INJECTED (tests pass a fake fed from recorded Pipedrive shapes —
NO live Pipedrive call ever happens in CI), credentials resolve from the
PER-TENANT vault slot `uplift/{tenant_id}/pipedrive` via the injected
SecretProvider, and a missing/empty per-tenant secret is a HARD
MissingTenantCredentialError (no shared-token fallback — one Pipedrive token
belongs to ONE company, so a fallback would sync someone else's CRM under THIS
tenant's rows).

OAuth-first. The vault slot holds the OAuth envelope written by the
`/integrations/pipedrive/oauth/callback` route (see ingest/connectors/oauth.py):
``{access_token, refresh_token, expires_at, token_type:"oauth", api_domain}``.
`api_domain` is the tenant's per-company API base host (e.g.
`https://yourco.pipedrive.com`) — EVERY API call uses it as the base host, so it
travels in the envelope (a Pipedrive access token is meaningless without its
company host).

ROTATING REFRESH TOKENS. Pipedrive issues a NEW refresh_token on every refresh and
INVALIDATES the old one. Two consequences this connector handles:
  1. We ALWAYS overwrite the stored refresh_token with the freshly-returned one
     (oauth._tokens_from_response captures it; we persist `new["refresh_token"]`).
  2. Concurrent syncs for the same tenant must not BOTH refresh with the same old
     token (the second would 400, and worse, the first's new token could be lost) —
     so the refresh+write-back is SINGLE-FLIGHTED behind a per-(tenant,source) lock
     (`_refresh_lock`). The lock holder re-reads the vault under the lock so a
     loser sees the winner's already-rotated envelope instead of re-refreshing.

A bare-token fallback is kept for back-compat and offline dry runs: a bare string
vault value is used as the bearer as-is (no api_domain — real syncs MUST use the
OAuth envelope so the company host is known). Read sync only: this connector never
writes back to Pipedrive (the draft-only invariant); the only write is the vault
refresh above.

Incremental sync is the API v2 collection endpoints with `updated_since` +
`sort_by=update_time` + cursor pagination (limit 500) PER RESOURCE, each with its
own `update_time` high-watermark (resources progress independently). See
`SYNC_RESOURCES` and the SalesforceConnector-style per-object cursor map.

Normalization to the db/schema.sql shapes:
  persons        -> contacts    (name/email/phone; org_id -> company_ref_id)
  organizations  -> companies   (name)
  deals          -> deals       (title/status/value, person_id + org_id)
  activities     -> activities  (kind=type, subject/note body, person/deal refs)
carrying tenant_id, source='pipedrive' on every row.

EXPERIMENTAL STATUS: only the STANDARD field set is mapped. Pipedrive custom
fields are keyed by a hashed 40-char id; the connector resolves their LABELS via
the Fields v2 endpoints (when the injected client exposes `fields(resource)`) and
appends labelled custom values to the text block. Full custom-field -> column
mapping is a deliberate FOLLOW-UP. Every endpoint/param the real client touches is
flagged `# VERIFY` — confirm against a live company (with API access) before first
prod run.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Iterable, Iterator, Protocol, runtime_checkable

from .base import (
    Connector,
    MissingTenantCredentialError,
    NormalizedRecord,
    SecretNotFoundError,
    tenant_secret_ref,
)

log = logging.getLogger("ingest.connectors.pipedrive")

# Pipedrive REST API version the collection paths are pinned to.
PD_API_VERSION = "v2"
# v2 collection page cap is 500.
PD_PAGE_LIMIT = 500

# The resource set the connector pulls, in dependency order (organizations first so
# persons/deals can reference their ref_id). Each entry: the API v2 resource path
# segment, the source-client list method, and the target normalized table.
SYNC_RESOURCES: tuple[tuple[str, str, str], ...] = (
    ("organizations", "list_organizations", "companies"),
    ("persons", "list_persons", "contacts"),
    ("deals", "list_deals", "deals"),
    ("activities", "list_activities", "activities"),
)

# Module-level per-ref locks single-flighting the rotating-refresh (see module
# docstring). Keyed by the vault ref so two connectors for the SAME tenant/source
# serialize, while different tenants never contend.
_refresh_locks_guard = threading.Lock()
_refresh_locks: dict[str, threading.Lock] = {}


def _lock_for(ref: str) -> threading.Lock:
    with _refresh_locks_guard:
        lock = _refresh_locks.get(ref)
        if lock is None:
            lock = threading.Lock()
            _refresh_locks[ref] = lock
        return lock


@runtime_checkable
class PipedriveClient(Protocol):
    """Minimal source-client interface the connector depends on.

    Each `list_*` returns an iterable of raw Pipedrive v2 record dicts. `since` is
    the per-resource high-water `update_time` (an ISO-8601/RFC-3339 string); None =
    full pull. `fields(resource)` (OPTIONAL — resolved via getattr) returns a
    ``{hashed_key: label}`` map for custom-field resolution.
    """

    def list_persons(self, since: str | None) -> Iterable[dict]: ...
    def list_organizations(self, since: str | None) -> Iterable[dict]: ...
    def list_deals(self, since: str | None) -> Iterable[dict]: ...
    def list_activities(self, since: str | None) -> Iterable[dict]: ...


class PipedriveConnector(Connector):
    """EXPERIMENTAL read-sync connector for Pipedrive (see module docstring)."""

    source = "pipedrive"

    def __init__(self, tenant_id, *, client: PipedriveClient,
                 secret_writer=None, **kwargs) -> None:
        super().__init__(tenant_id, **kwargs)
        self._client = client
        # Optional write seam (oauth.SecretWriter — any object with put_secret):
        # when present, the refreshed (ROTATED) OAuth envelope is persisted back so
        # the next sync starts from the new token. Absent (offline / bare-token path)
        # = no write-back; the refreshed token is still used for THIS run.
        self._secret_writer = secret_writer
        # Per-resource high-watermark accumulated during pull() (resource -> max
        # update_time seen). Exposed via next_cursor() for callers that persist true
        # per-resource incrementality.
        self._watermarks: dict[str, str] = {}
        # Lazily-resolved {resource: {hashed_key: label}} custom-field maps.
        self._field_maps: dict[str, dict[str, str]] = {}

    # -- auth ------------------------------------------------------------ #
    def authenticate(self) -> None:
        # Resolve the PER-TENANT vaulted credential (uplift/{tenant_id}/pipedrive)
        # and ONLY that — never log/echo the raw value, never fall back to a shared
        # token (one shared Pipedrive token belongs to ONE company, so a fallback
        # would sync someone else's company under THIS tenant's rows).
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
        token, api_domain = self._resolve_access(raw, ref)
        if not token:
            raise MissingTenantCredentialError(
                self.tenant_id, self.source, ref, "credential has no usable token"
            )
        # Hand the resolved bearer + company host to the injected client when it
        # accepts them (PipedriveRestClient does; test fakes need not implement them).
        set_token = getattr(self._client, "set_token", None)
        if callable(set_token):
            set_token(token)
        set_domain = getattr(self._client, "set_api_domain", None)
        if callable(set_domain) and api_domain:
            set_domain(api_domain)
        self._authed = True

    # -- OAuth-aware access resolution (rotating-refresh-safe) ----------- #
    def _resolve_access(self, raw_value: str, ref: str) -> tuple[str, str | None]:
        """Turn the vaulted secret value into (bearer_token, api_domain) for this run.

        OAuth envelope -> use its access_token (refreshing first, and persisting the
        new ROTATED envelope when a writer is wired, if at/near expiry) and
        api_domain. Bare string -> a trivial bearer fallback (no api_domain). NEVER
        logs a token value.
        """
        from .oauth import parse_oauth_secret  # noqa: PLC0415 — keep base import-light

        secret = parse_oauth_secret(raw_value)
        if secret is None:
            # Legacy/offline bare token. No api_domain is known — real syncs MUST
            # connect via OAuth so the company host travels in the envelope.
            return raw_value, None
        return self._access_from_envelope(secret, ref)

    def _access_from_envelope(self, secret: dict, ref: str) -> tuple[str, str | None]:
        from .oauth import is_expired  # noqa: PLC0415

        api_domain = secret.get("api_domain")
        if not is_expired(secret):
            return secret["access_token"], api_domain
        return self._refresh_locked(ref, api_domain)

    def _refresh_locked(self, ref: str, api_domain: str | None) -> tuple[str, str | None]:
        """SINGLE-FLIGHT the rotating-refresh behind a per-ref lock.

        Pipedrive invalidates the old refresh_token on each refresh, so two
        concurrent syncs must not both refresh with the same token. The lock holder
        RE-READS the vault under the lock: if a peer already rotated (the re-read
        envelope is no longer expired), we ride its fresh access token instead of
        burning a second refresh that would 400.
        """
        from .oauth import (  # noqa: PLC0415
            get_provider,
            is_expired,
            oauth_secret_value,
            parse_oauth_secret,
            refresh_access_token,
        )

        with _lock_for(ref):
            # Re-read under the lock — a peer may have rotated while we waited.
            current = parse_oauth_secret(self._secrets.get_secret(ref))
            if current is None:
                # Slot was replaced by a bare token mid-flight — use it as-is.
                raw = self._secrets.get_secret(ref)
                return raw, None
            api_domain = current.get("api_domain") or api_domain
            if not is_expired(current):
                return current["access_token"], api_domain

            provider = get_provider(self.source)
            if provider is None:  # defensive — pipedrive is registered
                return current["access_token"], api_domain
            try:
                client_id = self._secrets.get_secret(provider.client_id_ref)
                client_secret = self._secrets.get_secret(provider.client_secret_ref)
            except SecretNotFoundError as exc:
                # Can't refresh without the app creds — fail honestly (reconnect),
                # never ride a known-expired token into a silent 401 mid-sync.
                log.error(
                    "ingest auth failed: event=oauth_refresh_unconfigured tenant_id=%s "
                    "source=%s reason=client_creds_not_provisioned",
                    self.tenant_id, self.source,
                )
                raise MissingTenantCredentialError(
                    self.tenant_id, self.source, ref,
                    "OAuth token expired and app client credentials are not provisioned",
                ) from exc
            new = refresh_access_token(
                provider, refresh_token=current["refresh_token"],
                client_id=client_id, client_secret=client_secret,
            )
            # Pipedrive returns api_domain on refresh too; keep the prior one if the
            # provider omitted it on this response.
            api_domain = new.get("api_domain") or api_domain
            log.info("ingest oauth: refreshed (rotated) access token tenant_id=%s source=%s",
                     self.tenant_id, self.source)
            if self._secret_writer is not None:
                # ALWAYS overwrite the stored refresh_token with the freshly-rotated
                # one — the old token is now invalid at Pipedrive.
                self._secret_writer.put_secret(
                    ref,
                    oauth_secret_value(
                        access_token=new["access_token"],
                        refresh_token=new["refresh_token"],
                        expires_at=new["expires_at"],
                        api_domain=api_domain,
                    ),
                )
            return new["access_token"], api_domain

    # -- pull ------------------------------------------------------------ #
    def pull(self, since_cursor: str | None) -> Iterable[NormalizedRecord]:
        """Yield normalized records changed since the cursor (per-resource floor).

        `since_cursor` may be None (full backfill), a plain ISO-8601 string (the
        pipeline's single global high-water — applied as a conservative floor to
        EVERY resource; re-pulling a small overlap is cheap + idempotent), or a JSON
        map ``{"persons": "<ts>", ...}`` of true per-resource watermarks (what
        :meth:`next_cursor` emits). Each resource is pulled with its own floor and
        its own max update_time is recorded in :attr:`_watermarks`.
        """
        self._require_auth()
        floors = self._parse_since(since_cursor)
        for resource, method, table in SYNC_RESOURCES:
            since = floors.get(resource)
            for obj in getattr(self._client, method)(since):
                yield self._normalize(resource, table, obj)

    def next_cursor(self) -> str | None:
        """The per-resource high-watermark map (JSON) accumulated by the last pull(),
        or None if nothing was seen. Callers that want TRUE per-resource
        incrementality persist this and feed it back to pull(); the standard pipeline
        instead stores its own single global high-water (a safe conservative floor)."""
        return json.dumps(self._watermarks, sort_keys=True) if self._watermarks else None

    @staticmethod
    def _parse_since(since_cursor: str | None) -> dict[str, str | None]:
        """Resolve the cursor into a per-resource floor map (missing -> None)."""
        if not since_cursor:
            return {}
        value = since_cursor.strip()
        if value.startswith("{"):
            try:
                obj = json.loads(value)
            except ValueError:
                obj = None
            if isinstance(obj, dict):
                return {k: (str(v) if v else None) for k, v in obj.items()}
        # A plain timestamp -> the same floor for every resource.
        return {resource: value for resource, _m, _t in SYNC_RESOURCES}

    def _record_watermark(self, resource: str, update_time: str) -> None:
        if update_time and update_time > self._watermarks.get(resource, ""):
            self._watermarks[resource] = update_time

    # -- custom-field resolution (best-effort) --------------------------- #
    def _field_map(self, resource: str) -> dict[str, str]:
        """Lazily resolve {hashed_key: label} for `resource` via the client's
        optional `fields(resource)`. Absent/failed -> {} (no custom-field labels;
        full custom-field column mapping is a follow-up — see module docstring)."""
        if resource in self._field_maps:
            return self._field_maps[resource]
        fields = getattr(self._client, "fields", None)
        result: dict[str, str] = {}
        if callable(fields):
            try:
                for f in fields(resource) or []:
                    key, name = f.get("key"), f.get("name")
                    # Custom fields carry a 40-hex-char key; standard fields are named
                    # (id/name/...). Only the hashed keys need label resolution.
                    if key and name and len(str(key)) == 40:
                        result[str(key)] = str(name)
            except Exception:  # noqa: BLE001 — field resolution is best-effort, never fatal
                log.warning("pipedrive: custom-field resolution failed for resource=%s "
                            "tenant_id=%s (continuing without labels)", resource, self.tenant_id)
        self._field_maps[resource] = result
        return result

    def _custom_field_lines(self, resource: str, obj: dict) -> list[str]:
        """Labelled `Label: value` lines for any resolved custom fields present on
        `obj` (only the hashed keys we have a label for). Empty when none resolve."""
        labels = self._field_map(resource)
        lines: list[str] = []
        for key, label in labels.items():
            val = obj.get(key)
            if val not in (None, "", [], {}):
                lines.append(f"{label}: {val}")
        return lines

    # -- normalization ---------------------------------------------------- #
    def _normalize(self, resource: str, table: str, obj: dict) -> NormalizedRecord:
        update_time = obj.get("update_time") or ""
        self._record_watermark(resource, update_time)
        builder = {
            "organizations": self._organization_row,
            "persons": self._person_row,
            "deals": self._deal_row,
            "activities": self._activity_row,
        }[resource]
        ref, row, kind, text = builder(obj)
        extra = self._custom_field_lines(resource, obj)
        if extra and text:
            text = text + "\n" + "\n".join(extra)
        text_blocks = [{"ref_id": ref, "kind": kind, "text": text}] if text else []
        return NormalizedRecord(
            tenant_id=self.tenant_id,
            source=self.source,
            ref_id=ref,
            table=table,
            row=row,
            raw=obj,
            updated_at=update_time,
            kind=kind,
            text_blocks=text_blocks,
        )

    @staticmethod
    def _primary_value(items, key: str = "value") -> str | None:
        """First `value` from a Pipedrive v2 array-of-objects field (emails/phones),
        preferring the entry flagged `primary`."""
        if not isinstance(items, list):
            return None
        primary = None
        first = None
        for it in items:
            if isinstance(it, dict) and it.get(key):
                if first is None:
                    first = str(it[key])
                if it.get("primary"):
                    primary = str(it[key])
                    break
            elif it:  # tolerate a bare-string array
                first = first or str(it)
        return primary or first

    @staticmethod
    def _ref(obj: dict) -> str:
        return str(obj.get("id", ""))

    @staticmethod
    def _rel_id(value) -> str | None:
        """A v2 relation id (org_id/person_id/deal_id) as a string ref, or None.

        v2 returns a bare integer id; tolerate a legacy nested ``{"value": id}``."""
        if isinstance(value, dict):
            value = value.get("value")
        return str(value) if value not in (None, "", 0) else None

    def _organization_row(self, obj):
        ref = self._ref(obj)
        name = obj.get("name") or ""
        row = {
            "tenant_id": self.tenant_id,
            "name": name,
            "domain": None,  # Pipedrive organizations carry no domain field
            "ref_id": ref,
            "source": self.source,
        }
        return ref, row, "company", f"Company: {name}"

    def _person_row(self, obj):
        ref = self._ref(obj)
        name = obj.get("name") or " ".join(
            x for x in [obj.get("first_name"), obj.get("last_name")] if x
        )
        email = self._primary_value(obj.get("emails"))
        phone = self._primary_value(obj.get("phones"))
        company_ref = self._rel_id(obj.get("org_id"))
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
        return ref, row, "contact", "\n".join(parts)

    def _deal_row(self, obj):
        ref = self._ref(obj)
        title = obj.get("title") or ""
        amount = obj.get("value")
        try:
            amount = float(amount) if amount not in (None, "") else None
        except (TypeError, ValueError):
            amount = None
        # Prefer the human status (open/won/lost); fall back to the stage id.
        stage = obj.get("status") or (
            f"stage_{obj['stage_id']}" if obj.get("stage_id") else "open"
        )
        row = {
            "tenant_id": self.tenant_id,
            "company_ref_id": self._rel_id(obj.get("org_id")),
            "contact_ref_id": self._rel_id(obj.get("person_id")),
            "title": title,
            "stage": stage,
            "amount": amount,
            "currency": obj.get("currency") or "USD",
            "ref_id": ref,
            "source": self.source,
        }
        text = f"Deal: {title}\nStage: {stage}"
        if amount is not None:
            text += f"\nAmount: {amount} {row['currency']}"
        return ref, row, "deal", text

    def _activity_row(self, obj):
        ref = self._ref(obj)
        subject = obj.get("subject") or ""
        note = obj.get("note") or ""
        body = "\n".join(p for p in [subject, note] if p)
        kind = obj.get("type") or "activity"  # Pipedrive activity type (call/meeting/...)
        row = {
            "tenant_id": self.tenant_id,
            "contact_ref_id": self._rel_id(obj.get("person_id")),
            "deal_ref_id": self._rel_id(obj.get("deal_id")),
            "kind": kind,
            "body": body,
            "ref_id": ref,
            "source": self.source,
        }
        return ref, row, kind, body


# --------------------------------------------------------------------------- #
# Real Pipedrive REST client — EXPERIMENTAL (stdlib urllib, no new dependency, no
# import-time network). Satisfies the PipedriveClient Protocol; constructed only by
# the registry wiring in ingest/run_sync.py, NEVER in CI (tests use recorded
# fixtures via the injected fake).
# --------------------------------------------------------------------------- #
_MAX_RETRIES = 5


class PipedriveRestClient:
    """Minimal real Pipedrive client over the API v2 collection endpoints.

    Constructed UNAUTHENTICATED — `PipedriveConnector.authenticate()` resolves the
    vaulted OAuth envelope and injects the bearer + company host via `set_token()` /
    `set_api_domain()`, so the raw token never transits the runtime wiring.
    Read-only GETs only.

    # API contract (Pipedrive API v2, confirmed from docs):
    #   * Collections: GET {api_domain}/api/v2/{resource}?limit=500&
    #     sort_by=update_time&sort_direction=asc[&updated_since=<RFC3339>], paged via
    #     `additional_data.next_cursor` (pass it back as `cursor=`) until absent;
    #     records under `data`.
    #   * Auth: Authorization: Bearer <access_token>.
    #   * Fields v2: GET {api_domain}/api/v2/{resource}Fields -> `data` of
    #     {key, name, ...}; the connector uses it to label hashed custom-field keys.
    #   * Throttling: 429 carries a Retry-After header; `_get` honors it (bounded
    #     retries). A 503 propagates HONESTLY (no silent empty page).
    # # VERIFY (live company with API access required) before first prod run.
    """

    # API v2 collection segment + its Fields v2 segment, per resource.
    _FIELDS_SEGMENT = {
        "persons": "personFields",
        "organizations": "organizationFields",
        "deals": "dealFields",
        "activities": "activityFields",
    }

    def __init__(self, *, page_size: int = PD_PAGE_LIMIT, timeout_s: float = 30.0,
                 sleep=None) -> None:
        self._page_size = max(1, min(int(page_size), PD_PAGE_LIMIT))
        self._timeout_s = timeout_s
        self._token: str | None = None
        self._api_domain: str | None = None
        self._sleep = sleep  # injectable so tests never really sleep

    def set_token(self, token: str) -> None:
        self._token = token

    def set_api_domain(self, api_domain: str) -> None:
        self._api_domain = api_domain.rstrip("/")

    # -- HTTP ------------------------------------------------------------- #
    def _get(self, path_or_url: str) -> dict:
        import time as _time  # noqa: PLC0415
        import urllib.error  # noqa: PLC0415 — lazy: no network machinery at import
        import urllib.request  # noqa: PLC0415

        if not self._token:
            raise RuntimeError("PipedriveRestClient: no token — authenticate() must run first")
        if not self._api_domain:
            raise RuntimeError(
                "PipedriveRestClient: no api_domain — connect via OAuth so the "
                "per-company host travels in the vault envelope"
            )
        url = (path_or_url if path_or_url.startswith("http")
               else f"{self._api_domain}{path_or_url}")
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
                with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:  # noqa: S310 — fixed https company host
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < _MAX_RETRIES:
                    retry_after = self._retry_after_seconds(exc)
                    attempt += 1
                    log.warning("pipedrive: throttled (HTTP 429) — retrying in %ss "
                                "(attempt %d/%d)", retry_after, attempt, _MAX_RETRIES)
                    sleep(retry_after)
                    continue
                # Everything else (incl. 503) propagates HONESTLY — never a silent
                # empty page that would look like "no data" and drop real records.
                raise

    @staticmethod
    def _retry_after_seconds(exc) -> float:
        try:
            headers = exc.headers
            value = headers.get("Retry-After") if headers else None
            if value is not None:
                return max(0.0, float(value))
        except (TypeError, ValueError, AttributeError):
            pass
        return 5.0

    # -- collection query (cursor-paged) --------------------------------- #
    def _query(self, resource: str, since: str | None) -> Iterator[dict]:
        import urllib.parse  # noqa: PLC0415 — lazy

        cursor: str | None = None
        while True:
            params = {
                "limit": str(self._page_size),
                "sort_by": "update_time",
                "sort_direction": "asc",
            }
            if since:
                params["updated_since"] = since
            if cursor:
                params["cursor"] = cursor
            path = f"/api/{PD_API_VERSION}/{resource}?{urllib.parse.urlencode(params)}"
            page = self._get(path)
            yield from page.get("data") or []
            cursor = ((page.get("additional_data") or {}).get("next_cursor")) or None
            if not cursor:
                return

    # -- PipedriveClient Protocol ---------------------------------------- #
    def list_organizations(self, since): return self._query("organizations", since)
    def list_persons(self, since): return self._query("persons", since)
    def list_deals(self, since): return self._query("deals", since)
    def list_activities(self, since): return self._query("activities", since)

    def fields(self, resource: str) -> list[dict]:
        """Resolve a resource's field definitions (Fields v2) for custom-field
        labelling. Returns the raw `data` list of `{key, name, ...}` dicts."""
        segment = self._FIELDS_SEGMENT.get(resource)
        if segment is None:
            return []
        page = self._get(f"/api/{PD_API_VERSION}/{segment}")
        return page.get("data") or []
