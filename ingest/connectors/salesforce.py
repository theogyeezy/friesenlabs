"""Salesforce connector (read sync) — EXPERIMENTAL.

Mirrors the HubSpot reference connector (ingest/connectors/hubspot.py): the
source client is INJECTED (tests pass a fake fed from recorded fixtures — NO live
Salesforce call ever happens in CI), credentials resolve from the PER-TENANT
vault slot `uplift/{tenant_id}/salesforce` via the injected SecretProvider, and a
missing/empty per-tenant secret is a HARD MissingTenantCredentialError (no
shared-token fallback, same tenant-isolation rationale as HubSpot).

OAuth-first. The vault slot holds the OAuth envelope written by the
`/integrations/salesforce/oauth/callback` route (see ingest/connectors/oauth.py):
``{access_token, refresh_token, expires_at, token_type:"oauth", instance_url}``.
`instance_url` is the tenant's per-org API host — EVERY REST/SOQL call uses it as
the base host, so it travels in the envelope (a Salesforce access token is
meaningless without its org host). On expiry the connector refreshes via the
refresh_token grant and (when a writer is wired) persists the new envelope.

A trivial session/bare-token fallback is kept for back-compat and offline dry
runs: a bare string vault value is used as the bearer as-is (no instance_url —
real syncs MUST use the OAuth envelope so the org host is known). Read sync only:
this connector never writes back to Salesforce (the draft-only invariant).

Incremental sync is SOQL over `SystemModstamp` per object:
``SELECT <standard fields> FROM <Object> WHERE SystemModstamp > :cursor
  ORDER BY SystemModstamp``
with a PER-OBJECT high-watermark (each object's SystemModstamp progresses
independently). Deletions are tombstoned via the replication getDeleted endpoint
(`/sobjects/{Object}/deleted/?start=..&end=..`).

Normalization to the db/schema.sql shapes:
  Account              -> companies   (name, domain from Website)
  Contact + Lead       -> contacts    (name/email/phone; Contact carries AccountId)
  Opportunity          -> deals       (title/stage/amount, AccountId)
  Task + Event         -> activities  (kind=task|event, subject/description body)
carrying tenant_id, source='salesforce' on every row.

EXPERIMENTAL STATUS: only the STANDARD field set is mapped (no custom `__c`
fields). Describe-driven custom-field mapping (GET /sobjects/{Object}/describe →
map configured custom fields) is a deliberate FOLLOW-UP. Every endpoint/param the
real client touches is flagged `# VERIFY` — confirm against a live org (with API
access) before first prod run.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Iterable, Iterator, Protocol, runtime_checkable

from .base import (
    Connector,
    MissingTenantCredentialError,
    NormalizedRecord,
    SecretNotFoundError,
    tenant_secret_ref,
)

log = logging.getLogger("ingest.connectors.salesforce")

# Salesforce REST API version the SOQL/getDeleted paths are pinned to.
# # VERIFY against the target org's max version before first prod run.
SF_API_VERSION = "60.0"

# The standard object set the connector pulls, in dependency order (Account first
# so Contact/Opportunity can reference its ref_id). Each entry: the SObject name,
# the source-client list method, and the target normalized table.
_SYNC_OBJECTS: tuple[tuple[str, str, str], ...] = (
    ("Account", "list_accounts", "companies"),
    ("Contact", "list_contacts", "contacts"),
    ("Lead", "list_leads", "contacts"),
    ("Opportunity", "list_opportunities", "deals"),
    ("Task", "list_tasks", "activities"),
    ("Event", "list_events", "activities"),
)

# Standard fields per object (NO custom `__c` fields — see the describe-driven
# follow-up in the module docstring). `SystemModstamp` is the incremental cursor
# field on every object.
SOQL_FIELDS: dict[str, tuple[str, ...]] = {
    "Account": ("Id", "Name", "Website", "Phone", "Industry", "SystemModstamp"),
    "Contact": ("Id", "FirstName", "LastName", "Name", "Email", "Phone", "Title",
                "AccountId", "SystemModstamp"),
    "Lead": ("Id", "FirstName", "LastName", "Name", "Email", "Phone", "Company",
             "Title", "Status", "SystemModstamp"),
    "Opportunity": ("Id", "Name", "StageName", "Amount", "CloseDate", "AccountId",
                    "SystemModstamp"),
    "Task": ("Id", "Subject", "Description", "Status", "WhoId", "WhatId",
             "ActivityDate", "SystemModstamp"),
    "Event": ("Id", "Subject", "Description", "WhoId", "WhatId", "ActivityDate",
              "StartDateTime", "SystemModstamp"),
}


@runtime_checkable
class SalesforceClient(Protocol):
    """Minimal source-client interface the connector depends on.

    Each `list_*` returns an iterable of raw Salesforce record dicts (the queried
    fields). `since` is the per-object high-water `SystemModstamp` (an ISO-8601
    string); None = full pull. `list_deleted` returns the getDeleted records
    (``{"id","deletedDate"}``) for one SObject in the `[start, end]` window.
    """

    def list_accounts(self, since: str | None) -> Iterable[dict]: ...
    def list_contacts(self, since: str | None) -> Iterable[dict]: ...
    def list_leads(self, since: str | None) -> Iterable[dict]: ...
    def list_opportunities(self, since: str | None) -> Iterable[dict]: ...
    def list_tasks(self, since: str | None) -> Iterable[dict]: ...
    def list_events(self, since: str | None) -> Iterable[dict]: ...
    def list_deleted(self, sobject: str, start: str, end: str) -> Iterable[dict]: ...


class SalesforceConnector(Connector):
    """EXPERIMENTAL read-sync connector for Salesforce (see module docstring)."""

    source = "salesforce"

    def __init__(self, tenant_id, *, client: SalesforceClient,
                 secret_writer=None, **kwargs) -> None:
        super().__init__(tenant_id, **kwargs)
        self._client = client
        # Optional write seam (oauth.SecretWriter — any object with put_secret):
        # when present, a refreshed OAuth envelope is persisted back so the next
        # sync starts from the new token. Absent (offline / pasted-session path) =
        # no write-back; the refreshed token is still used for THIS run.
        self._secret_writer = secret_writer
        # Per-object high-watermark accumulated during pull() (SObject -> max
        # SystemModstamp seen). Exposed via next_cursor() for callers that persist
        # true per-object incrementality (the pipeline's single global cursor is a
        # safe conservative floor — see pull()).
        self._watermarks: dict[str, str] = {}

    # -- auth ------------------------------------------------------------ #
    def authenticate(self) -> None:
        # Resolve the PER-TENANT vaulted credential (uplift/{tenant_id}/salesforce)
        # and ONLY that — never log/echo the raw value, never fall back to a shared
        # token (one shared Salesforce token belongs to ONE org, so a fallback would
        # sync someone else's org under THIS tenant's rows).
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
        token, instance_url = self._resolve_access(raw, ref)
        if not token:
            raise MissingTenantCredentialError(
                self.tenant_id, self.source, ref, "credential has no usable token"
            )
        # Hand the resolved bearer + org host to the injected client when it accepts
        # them (SalesforceRestClient does; test fakes need not implement the setters).
        set_token = getattr(self._client, "set_token", None)
        if callable(set_token):
            set_token(token)
        set_instance = getattr(self._client, "set_instance_url", None)
        if callable(set_instance) and instance_url:
            set_instance(instance_url)
        self._authed = True

    # -- OAuth-aware access resolution ----------------------------------- #
    def _resolve_access(self, raw_value: str, ref: str) -> tuple[str, str | None]:
        """Turn the vaulted secret value into (bearer_token, instance_url) for this run.

        OAuth envelope -> use its access_token (refreshing first, and persisting the
        new envelope when a writer is wired, if at/near expiry) and instance_url.
        Bare string -> a trivial session fallback (the bare value as the bearer, no
        instance_url). NEVER logs a token value.
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
            # Legacy/offline bare token. No instance_url is known — real syncs MUST
            # connect via OAuth so the org host travels in the envelope.
            return raw_value, None

        instance_url = secret.get("instance_url")
        if not is_expired(secret):
            return secret["access_token"], instance_url

        provider = get_provider(self.source)
        if provider is None:  # defensive — salesforce is registered
            return secret["access_token"], instance_url
        try:
            client_id = self._secrets.get_secret(provider.client_id_ref)
            client_secret = self._secrets.get_secret(provider.client_secret_ref)
        except SecretNotFoundError as exc:
            # Can't refresh without the app creds — fail honestly (reconnect), never
            # ride a known-expired access token into a silent 401 mid-sync.
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
            provider, refresh_token=secret["refresh_token"],
            client_id=client_id, client_secret=client_secret,
        )
        # Salesforce returns instance_url on refresh too; keep the prior one if the
        # provider omitted it on this response.
        instance_url = new.get("instance_url") or instance_url
        log.info("ingest oauth: refreshed access token tenant_id=%s source=%s",
                 self.tenant_id, self.source)
        if self._secret_writer is not None:
            self._secret_writer.put_secret(
                ref,
                oauth_secret_value(
                    access_token=new["access_token"],
                    refresh_token=new["refresh_token"],
                    expires_at=new["expires_at"],
                    instance_url=instance_url,
                ),
            )
        return new["access_token"], instance_url

    # -- pull ------------------------------------------------------------ #
    def pull(self, since_cursor: str | None) -> Iterable[NormalizedRecord]:
        """Yield normalized records changed since the cursor, then deletion tombstones.

        `since_cursor` may be None (full backfill), a plain ISO-8601 string (the
        pipeline's single global high-water — applied as a conservative floor to
        EVERY object; re-pulling a small overlap is cheap + idempotent), or a JSON
        map ``{"Account": "<ts>", ...}`` of true per-object watermarks (what
        :meth:`next_cursor` emits). Each object is pulled with its own floor and its
        own max SystemModstamp is recorded in :attr:`_watermarks`.
        """
        self._require_auth()
        floors = self._parse_since(since_cursor)
        for sobject, method, table in _SYNC_OBJECTS:
            since = floors.get(sobject)
            for obj in getattr(self._client, method)(since):
                yield self._normalize(sobject, table, obj)
        # Deletion sweep: getDeleted over the per-object window [floor, now]. Only
        # meaningful with a floor (a fresh backfill has nothing to tombstone, and the
        # getDeleted window is bounded — Salesforce caps it at ~30 days).
        list_deleted = getattr(self._client, "list_deleted", None)
        if callable(list_deleted):
            end = _now_iso()
            for sobject, _method, table in _SYNC_OBJECTS:
                start = floors.get(sobject)
                if not start:
                    continue
                for dele in list_deleted(sobject, start, end):
                    rec = self._tombstone(sobject, table, dele)
                    if rec is not None:
                        yield rec

    def next_cursor(self) -> str | None:
        """The per-object high-watermark map (JSON) accumulated by the last pull(),
        or None if nothing was seen. Callers that want TRUE per-object incrementality
        persist this and feed it back to pull(); the standard pipeline instead stores
        its own single global high-water (a safe conservative floor)."""
        return json.dumps(self._watermarks, sort_keys=True) if self._watermarks else None

    @staticmethod
    def _parse_since(since_cursor: str | None) -> dict[str, str | None]:
        """Resolve the cursor into a per-object floor map (missing -> None)."""
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
        # A plain timestamp -> the same floor for every object.
        return {sobject: value for sobject, _m, _t in _SYNC_OBJECTS}

    def _record_watermark(self, sobject: str, modstamp: str) -> None:
        if modstamp and modstamp > self._watermarks.get(sobject, ""):
            self._watermarks[sobject] = modstamp

    # -- normalization ---------------------------------------------------- #
    def _normalize(self, sobject: str, table: str, obj: dict) -> NormalizedRecord:
        modstamp = obj.get("SystemModstamp") or ""
        self._record_watermark(sobject, modstamp)
        builder = {
            "Account": self._account_row,
            "Contact": self._contact_row,
            "Lead": self._lead_row,
            "Opportunity": self._opportunity_row,
            "Task": self._activity_row,
            "Event": self._activity_row,
        }[sobject]
        ref, row, kind, text = builder(sobject, obj)
        text_blocks = [{"ref_id": ref, "kind": kind, "text": text}] if text else []
        return NormalizedRecord(
            tenant_id=self.tenant_id,
            source=self.source,
            ref_id=ref,
            table=table,
            row=row,
            raw=obj,
            updated_at=modstamp,
            kind=kind,
            text_blocks=text_blocks,
        )

    @staticmethod
    def _domain_from_website(website: str | None) -> str | None:
        if not website:
            return None
        host = website.strip()
        for scheme in ("https://", "http://"):
            if host.lower().startswith(scheme):
                host = host[len(scheme):]
                break
        host = host.split("/", 1)[0].strip()
        return host or None

    def _account_row(self, _sobject, obj):
        ref = str(obj.get("Id", ""))
        name = obj.get("Name") or ""
        domain = self._domain_from_website(obj.get("Website"))
        row = {
            "tenant_id": self.tenant_id,
            "name": name,
            "domain": domain,
            "ref_id": ref,
            "source": self.source,
        }
        text = f"Company: {name}"
        if domain:
            text += f"\nDomain: {domain}"
        if obj.get("Industry"):
            text += f"\nIndustry: {obj['Industry']}"
        return ref, row, "company", text

    def _contact_row(self, _sobject, obj):
        ref = str(obj.get("Id", ""))
        name = obj.get("Name") or " ".join(
            x for x in [obj.get("FirstName"), obj.get("LastName")] if x
        )
        row = {
            "tenant_id": self.tenant_id,
            "company_ref_id": obj.get("AccountId"),
            "name": name,
            "email": obj.get("Email"),
            "phone": obj.get("Phone"),
            "ref_id": ref,
            "source": self.source,
        }
        parts = [f"Contact: {name}"]
        if row["email"]:
            parts.append(f"Email: {row['email']}")
        if row["phone"]:
            parts.append(f"Phone: {row['phone']}")
        if obj.get("Title"):
            parts.append(f"Title: {obj['Title']}")
        return ref, row, "contact", "\n".join(parts)

    def _lead_row(self, _sobject, obj):
        ref = str(obj.get("Id", ""))
        name = obj.get("Name") or " ".join(
            x for x in [obj.get("FirstName"), obj.get("LastName")] if x
        )
        # A Lead's `Company` is a free-text field, NOT an Account FK — surface it in
        # the text block but leave company_ref_id None (no real Account to link).
        row = {
            "tenant_id": self.tenant_id,
            "company_ref_id": None,
            "name": name,
            "email": obj.get("Email"),
            "phone": obj.get("Phone"),
            "ref_id": ref,
            "source": self.source,
        }
        parts = [f"Contact: {name}"]
        if row["email"]:
            parts.append(f"Email: {row['email']}")
        if row["phone"]:
            parts.append(f"Phone: {row['phone']}")
        if obj.get("Company"):
            parts.append(f"Company: {obj['Company']}")
        if obj.get("Status"):
            parts.append(f"Lead status: {obj['Status']}")
        return ref, row, "contact", "\n".join(parts)

    def _opportunity_row(self, _sobject, obj):
        ref = str(obj.get("Id", ""))
        title = obj.get("Name") or ""
        amount = obj.get("Amount")
        try:
            amount = float(amount) if amount not in (None, "") else None
        except (TypeError, ValueError):
            amount = None
        stage = obj.get("StageName") or "new"
        row = {
            "tenant_id": self.tenant_id,
            "company_ref_id": obj.get("AccountId"),
            "contact_ref_id": None,
            "title": title,
            "stage": stage,
            "amount": amount,
            "currency": "USD",
            "ref_id": ref,
            "source": self.source,
        }
        text = f"Deal: {title}\nStage: {stage}"
        if amount is not None:
            text += f"\nAmount: {amount} {row['currency']}"
        return ref, row, "deal", text

    def _activity_row(self, sobject, obj):
        ref = str(obj.get("Id", ""))
        subject = obj.get("Subject") or ""
        description = obj.get("Description") or ""
        body = "\n".join(p for p in [subject, description] if p)
        kind = sobject.lower()  # "task" | "event"
        # WhoId references a Contact/Lead; WhatId an Account/Opportunity/etc. Best-
        # effort link (WhatId may be a non-Opportunity — describe-driven polymorphic
        # resolution is a follow-up).
        row = {
            "tenant_id": self.tenant_id,
            "contact_ref_id": obj.get("WhoId"),
            "deal_ref_id": obj.get("WhatId"),
            "kind": kind,
            "body": body,
            "ref_id": ref,
            "source": self.source,
        }
        return ref, row, kind, body

    # -- deletion tombstones --------------------------------------------- #
    def _tombstone(self, sobject: str, table: str, dele: dict) -> NormalizedRecord | None:
        """A deletion record from getDeleted -> a tombstone NormalizedRecord.

        Routed to the logical `tombstones` table (NOT the live CRM table) so a
        deletion never lands as a half-empty real row; the row names the object +
        target table a delete-aware sink would act on. No text_blocks (a deletion
        produces no embedding) and no updated_at (deletions don't advance the live
        high-water — the next run re-scans the same getDeleted window from the cursor).
        """
        ref = str(dele.get("id") or dele.get("Id") or "")
        if not ref:
            return None
        row = {
            "tenant_id": self.tenant_id,
            "ref_id": ref,
            "source": self.source,
            "object": sobject,
            "table": table,
            "deleted": True,
            "deleted_at": dele.get("deletedDate") or dele.get("deleted_date"),
        }
        return NormalizedRecord(
            tenant_id=self.tenant_id,
            source=self.source,
            ref_id=ref,
            table="tombstones",
            row=row,
            raw=dele,
            updated_at="",
            kind="tombstone",
            text_blocks=[],
        )


def _now_iso() -> str:
    """Current UTC time as a SOQL-safe ISO-8601 instant (no import-time clock)."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time()))


# --------------------------------------------------------------------------- #
# Real Salesforce REST client — EXPERIMENTAL (stdlib urllib, no new dependency,
# no import-time network). Satisfies the SalesforceClient Protocol; constructed
# only by the registry wiring in ingest/run_sync.py, NEVER in CI (tests use
# recorded fixtures).
# --------------------------------------------------------------------------- #
class SalesforceRestClient:
    """Minimal real Salesforce client over the REST Query (SOQL) + getDeleted APIs.

    Constructed UNAUTHENTICATED — `SalesforceConnector.authenticate()` resolves the
    vaulted OAuth envelope and injects the bearer + org host via `set_token()` /
    `set_instance_url()`, so the raw token never transits the runtime wiring.
    Read-only GETs only.

    # API contract (Salesforce REST v60.0, confirmed from docs):
    #   * Query: GET /services/data/v{ver}/query?q=<SOQL>, paged via the absolute
    #     `nextRecordsUrl` until `done` is true (records under `records`).
    #   * getDeleted: GET /services/data/v{ver}/sobjects/{S}/deleted/?start=&end=
    #     returns `deletedRecords` (`{id, deletedDate}`); window must be <= 30 days.
    #   * SOQL datetime literals are UNQUOTED ISO-8601 (e.g. 2026-06-01T00:00:00Z) —
    #     `_soql_datetime` normalizes the stored SystemModstamp to that form.
    # # VERIFY (live org with API access required) before first prod run.
    """

    def __init__(self, *, api_version: str = SF_API_VERSION, page_size: int = 2000,
                 timeout_s: float = 30.0) -> None:
        self._api_version = api_version
        self._page_size = page_size  # SOQL query result page cap is 2000
        self._timeout_s = timeout_s
        self._token: str | None = None
        self._instance_url: str | None = None

    def set_token(self, token: str) -> None:
        self._token = token

    def set_instance_url(self, instance_url: str) -> None:
        self._instance_url = instance_url.rstrip("/")

    # -- HTTP ------------------------------------------------------------- #
    def _get(self, path_or_url: str) -> dict:
        import urllib.request  # noqa: PLC0415 — lazy: no network machinery at import

        if not self._token:
            raise RuntimeError("SalesforceRestClient: no token — authenticate() must run first")
        if not self._instance_url:
            raise RuntimeError(
                "SalesforceRestClient: no instance_url — connect via OAuth so the "
                "per-org host travels in the vault envelope"
            )
        url = path_or_url if path_or_url.startswith("http") else f"{self._instance_url}{path_or_url}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:  # noqa: S310 — fixed https org host
            return json.loads(resp.read().decode("utf-8"))

    # -- SOQL query (paged) ---------------------------------------------- #
    @staticmethod
    def _soql_datetime(value: str) -> str:
        """Normalize a stored SystemModstamp to a SOQL-safe ISO-8601 literal.

        Salesforce emits e.g. `2026-06-01T12:00:00.000+0000`; SOQL wants
        `2026-06-01T12:00:00Z` (or a `+hh:mm` offset). Drop fractional seconds and
        coerce a `+0000`/`Z` UTC suffix; leave a well-formed `+hh:mm` offset intact.
        """
        v = value.strip()
        if "." in v:
            head, tail = v.split(".", 1)
            # tail like "000Z" / "000+0000" / "123456+00:00" — keep its zone suffix.
            zone = ""
            for marker in ("+", "-", "Z"):
                idx = tail.find(marker, 1) if marker != "Z" else tail.find(marker)
                if idx != -1:
                    zone = tail[idx:]
                    break
            v = head + zone
        if v.endswith("+0000") or v.endswith("-0000"):
            v = v[:-5] + "Z"
        if not (v.endswith("Z") or "+" in v[10:] or v[10:].count("-") > 0):
            v += "Z"
        return v

    def _query(self, sobject: str, since: str | None) -> Iterator[dict]:
        fields = ", ".join(SOQL_FIELDS[sobject])
        soql = f"SELECT {fields} FROM {sobject}"
        if since:
            soql += f" WHERE SystemModstamp > {self._soql_datetime(since)}"
        soql += " ORDER BY SystemModstamp"
        import urllib.parse  # noqa: PLC0415 — lazy

        path = f"/services/data/v{self._api_version}/query?{urllib.parse.urlencode({'q': soql})}"
        page = self._get(path)
        while True:
            yield from page.get("records", [])
            if page.get("done", True):
                return
            next_url = page.get("nextRecordsUrl")
            if not next_url:
                return
            page = self._get(next_url)

    # -- SalesforceClient Protocol --------------------------------------- #
    def list_accounts(self, since): return self._query("Account", since)
    def list_contacts(self, since): return self._query("Contact", since)
    def list_leads(self, since): return self._query("Lead", since)
    def list_opportunities(self, since): return self._query("Opportunity", since)
    def list_tasks(self, since): return self._query("Task", since)
    def list_events(self, since): return self._query("Event", since)

    def list_deleted(self, sobject: str, start: str, end: str) -> Iterator[dict]:
        import urllib.parse  # noqa: PLC0415 — lazy

        qs = urllib.parse.urlencode({
            "start": self._soql_datetime(start),
            "end": self._soql_datetime(end),
        })
        path = f"/services/data/v{self._api_version}/sobjects/{sobject}/deleted/?{qs}"
        page = self._get(path)
        yield from page.get("deletedRecords", [])
