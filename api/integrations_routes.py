"""Authed per-tenant integrations endpoints — the api half of TODO INT/P2
("Build the real integrations/connect UI + backend"; the web screen rides a later cycle).

Six endpoints, all bound to the VERIFIED JWT claims (THE TRUST RULE — tenant never from a
header or the request body):

  GET    /integrations                     known connectors (hubspot|csv|gohighlevel|stripe)
                                           + this tenant's per-connector status + last_sync
  POST   /integrations/{name}/credentials  probe (verify-on-connect, best-effort) then store
                                           the tenant's token in the per-tenant vault slot
                                           (uplift/{tenant_id}/{source}) via the injected
                                           SecretWriter — the token is never logged or echoed
  DELETE /integrations/{name}/credentials  disconnect: remove the vault slot (idempotent)
  POST   /integrations/{name}/sync         kick one incremental `sync_tenant` run for THAT
                                           tenant via the injected runner (sync connectors
                                           only). With a SyncRunStore wired this is ASYNC:
                                           202 + a `running` run row a background task
                                           finishes; a concurrent kick answers 409 (the
                                           partial-unique single-runner guard).
  GET    /integrations/{name}/syncs        recent sync-run history (newest first)
  POST   /integrations/csv/import          multipart CSV import (contacts|companies|deals, 5MB
                                           cap, mapping auto-detect + override) through the
                                           tenant-scoped ingest path via the injected importer

This module extends the read-only SecretProvider seam (ingest/connectors/base.py) with a WRITE
seam: the :class:`SecretWriter` Protocol. The real implementation (:class:`Boto3SecretWriter`,
Secrets Manager put-secret-value with a create-secret fallback) is selected ONLY under the NEW
deliberate ``INTEGRATIONS_REAL_SECRETS`` master switch (shared/config.py; infra/REQUESTS.md
REQ-006) — deploy invariance: env the live API task already carries for other features can
never flip this on. Unconfigured = honest 503 "not configured", NEVER a fake success.

IMPORT SAFETY: importing this module touches no AWS/boto3/DB; real clients are built lazily
inside the writer on first use.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import secrets as _csrf_secrets
from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable

from fastapi import (BackgroundTasks, Depends, FastAPI, File, Form, HTTPException,
                     UploadFile)
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from api.auth import TenantClaims
from shared.config import (ENV_INTEGRATIONS_REAL_SECRETS, ENV_OAUTH_APP_RETURN_URL,
                           ENV_OAUTH_REDIRECT_BASE, ENV_OAUTH_STATE_SECRET)

log = logging.getLogger("api.integrations")

# --------------------------------------------------------------------------- #
# BOOT INVARIANT (born as the post-#67 HOTFIX): this module must import — and the
# API must boot — WITHOUT the ingest/ package present. api/Dockerfile DOES bundle
# ingest/ today (it is lazy-imported for syncs + RAG embed), but the invariant is
# kept deliberate and regression-pinned (tests/unit/test_integrations_image_fileset.py)
# so a future image-slimming pass can never crash-loop the container at boot the
# way #67's top-level `from ingest...` import did. Hence: the two tiny helpers
# below are inlined mirrors of ingest.connectors.base, and every other ingest
# dependency is imported lazily behind try/except ImportError so the API stays
# honest-unconfigured when the package is absent from the image fileset.
# Keep these in sync with ingest/connectors/base.py (single-screen helpers).
# --------------------------------------------------------------------------- #
_PER_TENANT_SECRET_TEMPLATE = "uplift/{tenant_id}/{source}"


def _tenant_secret_ref(tenant_id: str, source: str) -> str:
    """Mirror of ingest.connectors.base.tenant_secret_ref (pure name formatter;
    tenant_id arrives from the verified claim — THE TRUST RULE)."""
    return _PER_TENANT_SECRET_TEMPLATE.format(tenant_id=tenant_id, source=source)


def _aws_not_found(exc: Exception) -> bool:
    """Mirror of ingest.connectors.base.Boto3SecretProvider._is_not_found."""
    code = ""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = (response.get("Error") or {}).get("Code", "")
    return code == "ResourceNotFoundException" or (
        exc.__class__.__name__ == "ResourceNotFoundException"
    )


# --------------------------------------------------------------------------- #
# Known connectors — the server-side registry the routes trust (the {name}
# path param is validated against THIS, never used to format a ref blindly).
# `source` is the vault-slot segment (ingest.connectors.base.tenant_secret_ref).
#
# This is the API-side MIRROR of ingest/connectors/registry.py (the ingest
# plane's canonical connector registry) — mirrored, not imported, because the
# production API image must boot without ingest/ present (the HOTFIX note
# above; tests/unit/test_integrations_image_fileset.py). Keep the two in sync;
# tests/unit/test_connector_registry.py asserts parity.
#
# `kind`: "sync" = credentialed pull connector (vault slot + sync route);
#         "file" = push import (csv) — NO vault slot, NO sync route; data
#         arrives via POST /integrations/csv/import.
# --------------------------------------------------------------------------- #
KNOWN_INTEGRATIONS: dict[str, dict[str, Any]] = {
    "hubspot": {
        "label": "HubSpot",
        "category": "CRM & Marketing",
        "description": "Sync companies, contacts, deals and notes from HubSpot CRM "
                       "into your Uplift data plane (read-only — Uplift never writes back).",
        "source": "hubspot",
        "kind": "sync",
        "experimental": False,
    },
    "csv": {
        "label": "CSV Import",
        "category": "Files & Imports",
        "description": "Import contacts, companies or deals from a CSV export (up to 5MB). "
                       "Column mapping is auto-detected and can be overridden per upload.",
        "source": None,           # no vault slot — the file is the data
        "kind": "file",
        "experimental": False,
    },
    "gohighlevel": {
        "label": "GoHighLevel",
        "category": "CRM & Marketing",
        "description": "EXPERIMENTAL: sync contacts and opportunities from a GoHighLevel "
                       "location (read-only — Uplift never writes back).",
        "source": "gohighlevel",
        "kind": "sync",
        "experimental": True,
    },
    "salesforce": {
        "label": "Salesforce",
        "category": "CRM & Marketing",
        "description": "EXPERIMENTAL: sync accounts, contacts, leads, opportunities and "
                       "activities from Salesforce via OAuth + SOQL (read-only — Uplift "
                       "never writes back).",
        "source": "salesforce",
        "kind": "sync",
        "experimental": True,
    },
    "stripe": {
        "label": "Stripe (revenue data)",
        "category": "Payments & Revenue",
        "description": "Pull customers, subscriptions and invoices from YOUR Stripe account "
                       "for revenue views (read-only; connect your own restricted key — "
                       "this is the tenant's key, never the platform's billing key).",
        "source": "stripe",
        "kind": "sync",
        "experimental": False,
    },
    "microsoft": {
        "label": "Microsoft 365",
        "category": "CRM & Marketing",
        "description": "EXPERIMENTAL: sync mail, calendar and contacts from Microsoft 365 "
                       "(Outlook/Exchange) via Microsoft Graph delta queries "
                       "(read-only — Uplift never writes back).",
        "source": "microsoft",
        "kind": "sync",
        "experimental": True,
    },
    "google": {
        "label": "Google (Calendar + Contacts)",
        "category": "CRM & Marketing",
        "description": "EXPERIMENTAL: sync calendar events and contacts from Google "
                       "(Calendar + People APIs) via incremental sync tokens "
                       "(read-only — Uplift never writes back). Gmail is not included.",
        "source": "google",
        "kind": "sync",
        "experimental": True,
    },
    "pipedrive": {
        "label": "Pipedrive",
        "category": "CRM & Marketing",
        "description": "EXPERIMENTAL: sync persons, organizations, deals and activities "
                       "from Pipedrive via OAuth + the API v2 incremental endpoints "
                       "(read-only — Uplift never writes back).",
        "source": "pipedrive",
        "kind": "sync",
        "experimental": True,
    },
}

# 5MB upload cap for POST /integrations/csv/import (mirrored in
# ingest/connectors/csv_import.py MAX_CSV_BYTES — same image-fileset rationale).
MAX_CSV_IMPORT_BYTES = 5 * 1024 * 1024


# --------------------------------------------------------------------------- #
# The secret WRITE seam — extends the read-only SecretProvider (ingest side).
# --------------------------------------------------------------------------- #
@runtime_checkable
class SecretWriter(Protocol):
    """Writes/inspects/removes a tenant's vaulted credential by reference name.

    The WRITE counterpart of ingest.connectors.base.SecretProvider (read-only).
    `put_secret` stores the raw value untouched (a vault must not mutate the
    secret); `secret_exists` answers connection status without ever reading the
    value back; `delete_secret` removes the slot (disconnect / account teardown)
    and reports whether anything existed to remove.
    """

    def put_secret(self, ref: str, value: str) -> None: ...

    def secret_exists(self, ref: str) -> bool: ...

    def delete_secret(self, ref: str) -> bool: ...


class Boto3SecretWriter:
    """Real Secrets Manager-backed SecretWriter.

    Lazy like its read sibling (ingest.connectors.base.Boto3SecretProvider):
    boto3 is imported only on first use when no client was injected — importing
    or constructing this never needs AWS. Tests inject a fake `client`.

    `put_secret` calls put_secret_value first (the common rotate path) and falls
    back to create_secret when the secret does not exist yet (first connect).
    `secret_exists` rides describe_secret — it NEVER fetches the value.
    # VERIFY against live AWS before first prod use: put_secret_value /
    # create_secret / describe_secret shapes + that the REQ-006 resource-scoped
    # IAM (uplift/*/hubspot*) matches the ARN suffix Secrets Manager appends.
    """

    def __init__(self, *, region: str | None = None, client: Any = None) -> None:
        self._region = region
        self._client = client  # injected fake in tests; lazily built otherwise

    def _sm(self) -> Any:
        if self._client is None:
            import boto3  # noqa: PLC0415 — lazy: import-safe module (no AWS at import)

            region = self._region or os.environ.get("AWS_REGION", "us-east-1")
            self._client = boto3.client("secretsmanager", region_name=region)
        return self._client

    # Read-side not-found detection (botocore ClientError code OR a fake
    # exception class named after the AWS error code) — inlined mirror, see
    # the HOTFIX note at the top of this module.
    _is_not_found = staticmethod(_aws_not_found)

    def put_secret(self, ref: str, value: str) -> None:
        client = self._sm()
        try:
            client.put_secret_value(SecretId=ref, SecretString=value)
        except Exception as exc:  # noqa: BLE001 — narrowed immediately below
            if not self._is_not_found(exc):
                raise  # access/throttle errors must surface, never be swallowed
            client.create_secret(Name=ref, SecretString=value)

    def secret_exists(self, ref: str) -> bool:
        client = self._sm()
        try:
            desc = client.describe_secret(SecretId=ref)
        except Exception as exc:  # noqa: BLE001 — narrowed immediately below
            if self._is_not_found(exc):
                return False
            raise
        # A secret scheduled for deletion still answers DescribeSecret (with a
        # DeletedDate) until the deletion completes — that slot is NOT connected.
        if isinstance(desc, dict) and desc.get("DeletedDate") is not None:
            return False
        return True

    def delete_secret(self, ref: str) -> bool:
        """Remove the slot immediately (ForceDeleteWithoutRecovery): the value is the
        TENANT'S OWN token which they can re-paste, so a recovery window buys nothing —
        while a window-scheduled deletion would block a reconnect (put_secret_value on a
        deletion-scheduled secret fails) for up to 30 days. Returns False when nothing
        existed (idempotent disconnect)."""
        client = self._sm()
        try:
            client.delete_secret(SecretId=ref, ForceDeleteWithoutRecovery=True)
        except Exception as exc:  # noqa: BLE001 — narrowed immediately below
            if self._is_not_found(exc):
                return False
            raise
        return True


# --------------------------------------------------------------------------- #
# Injected deps + the env-built default (so api/asgi.py needs no change: the
# ApiDeps default_factory builds THIS, and with no env set every piece is the
# honest unconfigured stub).
# --------------------------------------------------------------------------- #
@dataclass
class IntegrationsDeps:
    # None = secret storage unconfigured -> credentials POST answers 503 and
    # the GET status is "unknown" (never invented).
    secret_writer: SecretWriter | None = None
    # (tenant_id, integration_name) -> ingest.pipeline.SyncResult-shaped result.
    # None = sync unconfigured -> POST .../sync answers 503 (never a fake run).
    sync_runner: Callable[[str, str], Any] | None = None
    # (tenant_id, entity, csv_bytes, mapping|None) -> report dict
    # (ingest.connectors.csv_import.CsvImportReport shape). None = csv import
    # unconfigured -> POST /integrations/csv/import answers 503 (a CSV "import"
    # into throwaway in-memory stores would be a fake success).
    csv_importer: Callable[[str, str, bytes, dict | None], Any] | None = None
    # api.pg_sync_runs.SyncRunStore — sync-run history + the single-runner guard.
    # Present -> POST .../sync is ASYNC (202 + a run row a background task
    # finishes; concurrent kicks 409) and GET /integrations carries last_sync.
    # None -> the legacy inline-sync path (tests / no DB configured).
    sync_runs: Any | None = None
    # (source, token) -> True (provider accepted) | False (provider REJECTED:
    # a definitive 401/403) | None (could not verify: network/unknown shape).
    # None dep = no probing — credentials are stored unverified, like before.
    token_prober: Callable[[str, str], bool | None] | None = None
    # READ-side vault seam (ingest.connectors.base.SecretProvider). The OAuth flow
    # needs to READ the app's client_id/client_secret refs (uplift/oauth/{name}/*)
    # to build the authorize URL + exchange codes. None = no reader -> the OAuth
    # routes answer an honest 503 (never a fake redirect). Built only under
    # INTEGRATIONS_REAL_SECRETS, same as the writer.
    secret_reader: Any | None = None
    # OAuth deployment wiring (state signing secret + redirect base + app return
    # URL). All-empty = OAuth routes 503. Plain strings here so importing this
    # module never needs ingest/ (the boot invariant); the OAuthConfig is built
    # lazily inside the routes.
    oauth_state_secret: str = ""
    oauth_redirect_base: str = ""
    oauth_app_return_url: str = ""


def _real_secrets_mode() -> bool:
    """True only when INTEGRATIONS_REAL_SECRETS is exactly "true" or "1"
    (the shared.config._switch_env fail-CLOSED semantics)."""
    return os.environ.get(ENV_INTEGRATIONS_REAL_SECRETS, "") in ("true", "1")


def _build_sync_runner() -> Callable[[str, str], Any] | None:
    """The default sync runner — wired ONLY when the ingestion plane's own
    deliberate master switch (INGEST_REAL_STORES) is on.

    The unswitched ingest stubs would "succeed" with zero records — a fake
    success the draft-gate forbids — so without the switch the runner is None
    and the route answers an honest 503. NOTE: per REQ-004 the live API task
    carries NONE of the INGEST_* names, so API-kicked syncs stay 503 until
    Lane Nick deliberately wires them (the EventBridge scheduler is the
    primary sync path).
    """
    try:
        # Lazy AND absence-tolerant: the production API image does not bundle
        # ingest/ (see the HOTFIX note above) — no module = no runner = the
        # route answers its honest 503.
        from ingest.run_sync import real_mode  # noqa: PLC0415
    except ImportError:
        return None

    if not real_mode():
        return None

    def run(tenant_id: str, name: str) -> Any:
        # tenant_id arrives from the VERIFIED claim (threaded by the route).
        from ingest.connectors.registry import SYNC_SOURCES  # noqa: PLC0415
        from ingest.run_sync import (  # noqa: PLC0415 — boto3/psycopg2 only at call time
            build_embedder,
            build_raw_sink,
            build_stores,
            run_one,
        )

        if name not in SYNC_SOURCES:
            raise ValueError(f"no sync connector wired for integration {name!r}")
        store, cursors = build_stores()
        return run_one(tenant_id, store=store, cursors=cursors,
                       embedder=build_embedder(), raw_sink=build_raw_sink(),
                       source=name)

    return run


def _build_csv_importer() -> Callable[[str, str, bytes, dict | None], Any] | None:
    """The default csv importer — wired ONLY under the ingest plane's own
    deliberate master switch (INGEST_REAL_STORES), same rationale as the sync
    runner: importing into the unswitched in-memory stores would "succeed"
    while discarding every row (a fake success the draft-gate forbids).
    Lazy AND absence-tolerant: no ingest/ in the image = no importer = the
    route answers its honest 503."""
    try:
        from ingest.run_sync import real_mode  # noqa: PLC0415
    except ImportError:
        return None

    if not real_mode():
        return None

    def run(tenant_id: str, entity: str, data: bytes, mapping: dict | None) -> Any:
        # tenant_id arrives from the VERIFIED claim (threaded by the route).
        from ingest.connectors import default_structured_sink  # noqa: PLC0415
        from ingest.connectors.csv_import import import_csv  # noqa: PLC0415
        from ingest.run_sync import (  # noqa: PLC0415 — boto3/psycopg2 only at call time
            build_embedder,
            build_raw_sink,
            build_stores,
        )

        store, cursors = build_stores()
        # Land structured rows into the Aurora CRM tables (companies/contacts/deals)
        # the SAME way the connector sync path does — default_structured_sink() returns
        # the RLS-scoped PgCrmStructuredSink in real mode (idempotent on the namespaced
        # natural key, child→parent refs resolved to our uuids), and the in-memory sink
        # offline. So a CSV import populates the Pipeline/Contacts boards, not only the
        # `documents` vector store.
        report = import_csv(
            tenant_id, entity, data, mapping,
            store=store, cursor_store=cursors, embedder=build_embedder(),
            raw_sink=build_raw_sink(), structured_sink=default_structured_sink(),
        )
        return report.to_dict()

    return run


def _build_sync_run_store() -> Any | None:
    """PgSyncRunStore when the API task has a DB DSN (the same dsn_from_env the other
    Pg stores ride) — sync history is plain tenant-table persistence, no extra switch.
    No DSN (tests / local) = None = the legacy inline-sync path."""
    try:
        from api.pg_clients import dsn_from_env  # noqa: PLC0415 — lazy: no psycopg2 at import
    except ImportError:
        return None
    dsn = dsn_from_env()
    if not dsn:
        return None
    from api.pg_sync_runs import PgSyncRunStore  # noqa: PLC0415

    return PgSyncRunStore(dsn)


# --------------------------------------------------------------------------- #
# Verify-on-connect probes — ONE cheap authenticated read per provider, so a
# typo'd/revoked token is caught at connect time instead of at the first sync.
# Endpoint + auth shapes mirror the ingest REST clients (hubspot.py /
# gohighlevel.py / stripe_data.py).
# Fail-posture: ONLY a definitive 401/403 from the provider rejects the token
# (False -> 422, nothing stored). Any other outcome — network error, 5xx, an
# unexpected 404 — is "could not verify" (None): the token is STORED and the
# response says verified=null, because refusing to connect during a provider
# outage would be a worse lie than admitting we couldn't check.
# --------------------------------------------------------------------------- #
_PROBES: dict[str, dict[str, Any]] = {
    # GET one contact — the cheapest scoped read a private-app token must hold.
    "hubspot": {"url": "https://api.hubapi.com/crm/v3/objects/contacts?limit=1",
                "headers": {}},
    # GHL v2 requires the API Version header on every call (gohighlevel.py).
    # # VERIFY on first live connect: that this endpoint 200s for a plain
    # location ("sub-account") token with contacts.readonly scope.
    "gohighlevel": {"url": "https://services.leadconnectorhq.com/contacts/?limit=1",
                    "headers": {"Version": "2021-07-28"}},
    # One customer — works for restricted keys with the read scopes we need.
    "stripe": {"url": "https://api.stripe.com/v1/customers?limit=1",
               "headers": {}},
    # The signed-in user's profile — the cheapest Graph read a token with the
    # User.Read scope must hold (microsoft.py). Probes a bare access token; the
    # OAuth callback stores a refreshable envelope and skips this verify path.
    "microsoft": {"url": "https://graph.microsoft.com/v1.0/me",
                  "headers": {}},
    # One connection (page size 1) from the People API — the cheapest read a token
    # with the contacts.readonly scope must hold (google.py). Probes a bare access
    # token; the OAuth callback stores a refreshable envelope and skips this path.
    "google": {"url": "https://people.googleapis.com/v1/people/me/connections"
                      "?personFields=names&pageSize=1",
               "headers": {}},
}

_PROBE_TIMEOUT_SECONDS = 8
# GoHighLevel (and other Cloudflare-fronted providers) BAN urllib's default "Python-urllib/x.y"
# User-Agent (Cloudflare error 1010 → 403), which the prober would misread as "token rejected".
# A named UA clears it; harmless for the non-Cloudflare providers. (Mirrors the connector clients.)
_PROBE_USER_AGENT = "Uplift-Connector/1.0 (+https://friesenlabs.com)"


def probe_token(source: str, token: str) -> bool | None:
    """The default token_prober (stdlib urllib, lazy — same zero-dependency stance
    as HubSpotRestClient). NEVER logs or re-raises with the token in scope."""
    spec = _PROBES.get(source)
    if spec is None:
        return None
    import urllib.error  # noqa: PLC0415 — lazy: no network machinery at import
    import urllib.request  # noqa: PLC0415

    req = urllib.request.Request(spec["url"], method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("User-Agent", _PROBE_USER_AGENT)  # avoid Cloudflare 1010 false "token rejected"
    for k, v in spec["headers"].items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT_SECONDS):
            return True
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return False  # the provider DEFINITIVELY rejected this token
        log.warning("integrations: %s probe inconclusive (HTTP %s)", source, exc.code)
        return None
    except Exception as exc:  # noqa: BLE001 — network/DNS/timeout: inconclusive, never fatal
        log.warning("integrations: %s probe inconclusive (%s)", source, type(exc).__name__)
        return None


def _build_secret_reader() -> Any | None:
    """The read-side vault provider for the OAuth flow (resolves the app's
    client_id/client_secret refs). Built ONLY under INTEGRATIONS_REAL_SECRETS, and
    absence-tolerant: if ingest/ is not in the image (the boot invariant) there is
    no reader and the OAuth routes answer their honest 503."""
    if not _real_secrets_mode():
        return None
    try:
        from ingest.connectors.base import Boto3SecretProvider  # noqa: PLC0415 — lazy: no boto3 at import
    except ImportError:
        return None
    return Boto3SecretProvider()


def build_integrations_deps() -> IntegrationsDeps:
    """Env-built deps: real writer + token probe + OAuth reader only under
    INTEGRATIONS_REAL_SECRETS; real runner/importer only under the ingest plane's
    own INGEST_REAL_STORES; the sync-run store whenever the task has a DB DSN. The
    OAuth state secret/redirect base come from their own deliberate env names —
    any one missing = the OAuth routes stay 503. All-unset = all-None = every
    endpoint honest about being unconfigured."""
    real_secrets = _real_secrets_mode()
    writer = Boto3SecretWriter() if real_secrets else None
    return IntegrationsDeps(secret_writer=writer, sync_runner=_build_sync_runner(),
                            csv_importer=_build_csv_importer(),
                            sync_runs=_build_sync_run_store(),
                            token_prober=probe_token if real_secrets else None,
                            secret_reader=_build_secret_reader(),
                            oauth_state_secret=os.environ.get(ENV_OAUTH_STATE_SECRET, ""),
                            oauth_redirect_base=os.environ.get(ENV_OAUTH_REDIRECT_BASE, ""),
                            oauth_app_return_url=os.environ.get(ENV_OAUTH_APP_RETURN_URL, ""))


# --------------------------------------------------------------------------- #
# Request body — carries the token ONLY. There is deliberately no tenant field;
# pydantic ignores any smuggled extra keys (and the routes never read them).
# --------------------------------------------------------------------------- #
class CredentialsBody(BaseModel):
    token: str


_STATUS = {True: "connected", False: "not_connected", None: "unknown"}


def _known_or_404(name: str) -> dict[str, str]:
    meta = KNOWN_INTEGRATIONS.get(name)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"unknown integration: {name}")
    return meta


def _result_dict(res: Any) -> dict:
    """Serialize a SyncResult (dataclass), mapping, or attribute bag."""
    if dataclasses.is_dataclass(res) and not isinstance(res, type):
        return dataclasses.asdict(res)
    if isinstance(res, dict):
        return res
    fields = ("pulled", "landed_rows", "chunks", "embedded", "skipped", "cursor")
    return {f: getattr(res, f) for f in fields if hasattr(res, f)}


# --------------------------------------------------------------------------- #
# OAuth "connect with login" helpers. The flow's core (provider registry, signed
# state, token exchange/refresh) lives in ingest.connectors.oauth — imported
# LAZILY + absence-tolerantly so the API still boots when ingest/ is absent from
# the image (the boot invariant above): no module = the OAuth routes 503.
# --------------------------------------------------------------------------- #
def _oauth_module() -> Any | None:
    try:
        from ingest.connectors import oauth  # noqa: PLC0415 — lazy: import-safe (no network/AWS)
    except ImportError:
        return None
    return oauth


def _oauth_return(cfg: Any, name: str, *, ok: bool, reason: str | None = None) -> str:
    """The app URL the callback redirects the browser back to, with a status flag
    the SPA integrations page can render (?integration=hubspot&connected=1 or
    &error=denied). No token material is ever placed in the URL."""
    import urllib.parse  # noqa: PLC0415 — lazy, stdlib

    base = cfg.return_url()
    params = {"integration": name}
    if ok:
        params["connected"] = "1"
    else:
        params["error"] = reason or "failed"
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{urllib.parse.urlencode(params)}"


def _is_secret_missing(exc: Exception) -> bool:
    """Whether a secret-read failure means 'not provisioned' (-> honest 503) vs a
    real provider/access error (-> 502). SecretNotFoundError subclasses KeyError;
    a botocore/fake ResourceNotFoundException is caught by the inlined mirror."""
    return isinstance(exc, KeyError) or _aws_not_found(exc)


def mount_integrations(app: FastAPI, deps: IntegrationsDeps, current_tenant) -> None:
    """Mount the /integrations routes on `app`, authed via `current_tenant`
    (the same verified-claims dependency every other authed route uses)."""

    @app.get("/integrations")
    def list_integrations(claims: TenantClaims = Depends(current_tenant)):
        secrets_configured = deps.secret_writer is not None
        # Per-source latest run ("last synced") — one query, never 500s the listing.
        last_runs: dict[str, dict] = {}
        if deps.sync_runs is not None:
            try:
                last_runs = deps.sync_runs.latest(claims.tenant_id)  # claims ONLY
            except Exception as exc:  # noqa: BLE001 — history is auxiliary to the listing
                log.warning("integrations: last-sync lookup failed (%s)", type(exc).__name__)
        # OAuth feature-detection (advertised per-connector so the web leads with
        # the one-click "Connect with {Provider}" button instead of the paste-key
        # fallback). A connector is OAuth-capable when EVERY shared gate the
        # start/callback routes enforce is satisfied EXCEPT the per-provider
        # client-creds vault read (deferred to /oauth/start, which answers an
        # honest 503 if the app isn't registered). Computed once — the module +
        # runtime config don't vary per connector — then the per-name provider
        # lookup decides each card. Any provider added to oauth.PROVIDERS picks
        # this up automatically; no per-connector list to keep in sync.
        oauth_mod = _oauth_module()
        oauth_runtime_ready = False
        if oauth_mod is not None and secrets_configured and deps.secret_reader is not None:
            oauth_runtime_ready = oauth_mod.OAuthConfig(
                state_secret=deps.oauth_state_secret,
                redirect_base=deps.oauth_redirect_base,
                app_return_url=deps.oauth_app_return_url,
            ).configured()
        items = []
        for name, meta in KNOWN_INTEGRATIONS.items():
            connected: bool | None = None
            if meta["kind"] == "file":
                # csv has no vault slot — it is always usable when the importer
                # is wired ("available"), never "connected"/"not_connected".
                status = "available" if deps.csv_importer is not None else "unknown"
            else:
                if secrets_configured:
                    ref = _tenant_secret_ref(claims.tenant_id, meta["source"])  # claims ONLY
                    try:
                        connected = bool(deps.secret_writer.secret_exists(ref))
                    except Exception as exc:  # noqa: BLE001 — a status read must not 500 the listing
                        log.warning("integrations: status check failed for %s (%s)",
                                    ref, type(exc).__name__)
                        connected = None
                status = _STATUS[connected]
            # A sync-kind connector with a registered OAuth provider + ready
            # runtime advertises the login path; file-kind (csv) never does.
            oauth_available = (
                meta["kind"] != "file"
                and oauth_runtime_ready
                and oauth_mod.get_provider(name) is not None
            )
            items.append({
                "name": name,
                "label": meta["label"],
                "category": meta["category"],
                "description": meta["description"],
                "kind": meta["kind"],
                "experimental": meta["experimental"],
                "connected": connected,           # null = honestly unknown / not applicable
                "status": status,
                # True = lead with "Connect with {Provider}" (browser OAuth);
                # the web feature-detects this field and falls back to its own
                # known-capable set only when it is absent (older API images).
                "oauth_available": oauth_available,
                # null = no run recorded (or history not configured) — never invented.
                "last_sync": last_runs.get(meta["source"]) if meta["kind"] == "sync" else None,
            })
        return {
            "integrations": items,
            "secrets_configured": secrets_configured,
            "sync_configured": deps.sync_runner is not None,
            "csv_import_configured": deps.csv_importer is not None,
            "sync_history_configured": deps.sync_runs is not None,
        }

    @app.post("/integrations/{name}/credentials")
    def store_credentials(name: str, body: CredentialsBody,
                          claims: TenantClaims = Depends(current_tenant)):
        meta = _known_or_404(name)
        if meta["kind"] == "file":
            # csv has no vault slot — there is no credential to store, ever.
            raise HTTPException(status_code=409, detail=(
                f"{name} takes no credentials — upload data via POST /integrations/csv/import"
            ))
        if deps.secret_writer is None:
            # Honest unconfigured answer — never pretend the token was vaulted.
            raise HTTPException(status_code=503, detail=(
                "secret storage not configured — set "
                f"{ENV_INTEGRATIONS_REAL_SECRETS} (REQ-006) to enable the vault writer"
            ))
        if not body.token or not body.token.strip():
            raise HTTPException(status_code=422, detail="token must be non-empty")
        # Verify-on-connect (best-effort): a DEFINITIVE provider rejection (401/403)
        # stops a dead token from being vaulted as "connected" — it would otherwise
        # fail silently at the first sync. Inconclusive probes (network, 5xx, no
        # prober wired) store anyway and answer verified=null, never a fake true.
        verified: bool | None = None
        if deps.token_prober is not None:
            verified = deps.token_prober(meta["source"], body.token)
            if verified is False:
                raise HTTPException(status_code=422, detail=(
                    f"{meta['label']} rejected this token (unauthorized) — nothing was "
                    "stored. Check the token's value and scopes, then connect again."
                ))
        # THE TRUST RULE: the vault slot is derived from the VERIFIED claim only —
        # a tenant id smuggled in the body is ignored by construction.
        ref = _tenant_secret_ref(claims.tenant_id, meta["source"])
        try:
            # Stored untouched (a vault must not mutate the secret).
            deps.secret_writer.put_secret(ref, body.token)
        except Exception as exc:  # noqa: BLE001 — surface as 502; NEVER log the value/message
            log.error("integrations: secret write failed for %s (%s)", ref, type(exc).__name__)
            raise HTTPException(status_code=502, detail="secret store write failed")
        # The token is NEVER echoed back; only the slot name + status.
        return {"name": name, "secret_ref": ref, "stored": True, "status": "connected",
                "verified": verified}

    @app.delete("/integrations/{name}/credentials")
    def delete_credentials(name: str, claims: TenantClaims = Depends(current_tenant)):
        """Disconnect: remove the tenant's vault slot. Idempotent — disconnecting an
        unconnected source answers deleted=false, not an error. Run history is kept
        (it is the sync audit trail); a reconnect simply starts a new history."""
        meta = _known_or_404(name)
        if meta["kind"] == "file":
            raise HTTPException(status_code=409, detail=(
                f"{name} takes no credentials — there is nothing to disconnect"
            ))
        if deps.secret_writer is None:
            raise HTTPException(status_code=503, detail=(
                "secret storage not configured — set "
                f"{ENV_INTEGRATIONS_REAL_SECRETS} (REQ-006) to enable the vault writer"
            ))
        # THE TRUST RULE: the slot comes from the VERIFIED claim only.
        ref = _tenant_secret_ref(claims.tenant_id, meta["source"])
        try:
            deleted = bool(deps.secret_writer.delete_secret(ref))
        except Exception as exc:  # noqa: BLE001 — surface as 502; NEVER log the message
            log.error("integrations: secret delete failed for %s (%s)", ref, type(exc).__name__)
            raise HTTPException(status_code=502, detail="secret store delete failed")
        return {"name": name, "deleted": deleted, "status": "not_connected"}

    @app.post("/integrations/{name}/sync")
    def kick_sync(name: str, background: BackgroundTasks,
                  claims: TenantClaims = Depends(current_tenant)):
        meta = _known_or_404(name)
        if meta["kind"] == "file":
            # csv is push-style: nothing to pull — point at the import endpoint.
            raise HTTPException(status_code=409, detail=(
                f"{name} is not a pull-sync source — upload data via "
                "POST /integrations/csv/import"
            ))
        if deps.sync_runner is None:
            # Honest unconfigured answer — never a fake zero-record "success".
            raise HTTPException(status_code=503, detail=(
                "sync not configured — the ingestion plane is not wired on this task "
                "(INGEST_REAL_STORES unset; see infra/REQUESTS.md REQ-004/REQ-006)"
            ))
        # GUARD (post-#67 review MEDIUM): an API-kicked sync must NEVER ride the
        # deprecated SHARED HubSpot token fallback — that would ingest another
        # customer's portal into this tenant's rows. Require the tenant's OWN
        # vaulted credential, verifiably: no writer to check with, or no
        # per-tenant secret, means no API-triggered sync. The scheduled path
        # (operator-controlled INGEST_TENANTS) is unaffected.
        if deps.secret_writer is None:
            raise HTTPException(status_code=503, detail=(
                "sync requires verifiable per-tenant credentials — secret storage "
                "is not configured on this task (REQ-006)"
            ))
        ref = _tenant_secret_ref(claims.tenant_id, meta["source"])
        try:
            connected = bool(deps.secret_writer.secret_exists(ref))
        except Exception as exc:  # noqa: BLE001 — fail CLOSED on status errors
            log.error("integrations: pre-sync credential check failed for %s (%s)",
                      ref, type(exc).__name__)
            raise HTTPException(status_code=502, detail="credential check failed")
        if not connected:
            raise HTTPException(status_code=409, detail=(
                f"connect {name} first — no per-tenant credential is vaulted; "
                "API-triggered syncs never use the shared fallback token"
            ))
        if deps.sync_runs is None:
            # Legacy INLINE path (no run store wired — tests / no DB): run in-request.
            try:
                res = deps.sync_runner(claims.tenant_id, name)  # tenant from the VERIFIED claim only
            except Exception as exc:  # noqa: BLE001 — surface as 502, generic detail
                log.error("integrations: sync failed for tenant %s/%s (%s)",
                          claims.tenant_id, name, type(exc).__name__)
                raise HTTPException(status_code=502, detail="sync failed")
            return {"name": name, "result": _result_dict(res)}

        # ASYNC path: open a `running` run row (the partial-unique index makes this the
        # single-runner guard), hand the actual sync to a background task, answer 202
        # immediately. A first HubSpot pull can take minutes (per-chunk embedding) —
        # far beyond any sane request budget.
        run = deps.sync_runs.start(claims.tenant_id, meta["source"], triggered_by="api")
        if run is None:
            raise HTTPException(status_code=409, detail=(
                f"a {name} sync is already running for this workspace — it will appear "
                "in the sync history when it finishes"
            ))

        tenant_id = claims.tenant_id  # bind OUTSIDE the task: the claim, never request state

        def _run_in_background() -> None:
            try:
                res = deps.sync_runner(tenant_id, name)
            except Exception as exc:  # noqa: BLE001 — terminal status, class name only
                log.error("integrations: background sync failed for tenant %s/%s (%s)",
                          tenant_id, name, type(exc).__name__)
                deps.sync_runs.finish(tenant_id, run["id"], status="failed",
                                      error=type(exc).__name__)
                return
            metrics = _result_dict(res)
            deps.sync_runs.finish(tenant_id, run["id"], status="succeeded",
                                  metrics={k: metrics.get(k) for k in
                                           ("pulled", "landed_rows", "chunks",
                                            "embedded", "skipped")})

        background.add_task(_run_in_background)
        return JSONResponse(status_code=202, content={"name": name, "run": run})

    @app.get("/integrations/{name}/syncs")
    def list_syncs(name: str, claims: TenantClaims = Depends(current_tenant)):
        """Recent sync-run history for one connector (newest first). Honest 503 when
        no run store is wired — history is never invented."""
        meta = _known_or_404(name)
        if meta["kind"] == "file":
            raise HTTPException(status_code=409, detail=(
                f"{name} is not a pull-sync source — it has no sync history"
            ))
        if deps.sync_runs is None:
            raise HTTPException(status_code=503, detail=(
                "sync history not configured — the API task has no database wired"
            ))
        try:
            runs = deps.sync_runs.list_runs(claims.tenant_id, meta["source"])  # claims ONLY
        except Exception as exc:  # noqa: BLE001 — surface as 502, generic detail
            log.error("integrations: sync-history read failed for tenant %s/%s (%s)",
                      claims.tenant_id, name, type(exc).__name__)
            raise HTTPException(status_code=502, detail="sync history read failed")
        return {"name": name, "runs": runs}

    @app.post("/integrations/csv/import")
    async def csv_import(
        file: UploadFile = File(...),
        entity: str = Form(...),
        mapping: str | None = Form(None),
        claims: TenantClaims = Depends(current_tenant),
    ):
        """Import a contacts/companies/deals CSV for the CLAIMS tenant.

        Multipart form: `file` (the CSV, 5MB cap), `entity`
        (contacts|companies|deals), optional `mapping` (a JSON object of
        canonical field -> CSV column overriding the header heuristics).
        Rows land through the existing tenant-scoped ingest path; the response
        carries a per-row error report. THE TRUST RULE: the tenant comes ONLY
        from the verified claims — the form carries no tenant field and any
        smuggled one is never read.
        """
        if entity not in ("contacts", "companies", "deals"):
            raise HTTPException(status_code=422, detail=(
                f"unknown entity {entity!r} — expected contacts, companies or deals"
            ))
        mapping_dict: dict | None = None
        if mapping:
            try:
                mapping_dict = json.loads(mapping)
            except ValueError:
                raise HTTPException(status_code=422, detail="mapping must be a JSON object")
            if not isinstance(mapping_dict, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in mapping_dict.items()
            ):
                raise HTTPException(
                    status_code=422,
                    detail="mapping must be a JSON object of field -> CSV column",
                )
        if deps.csv_importer is None:
            # Honest unconfigured answer — never pretend rows were imported.
            raise HTTPException(status_code=503, detail=(
                "csv import not configured — the ingestion plane is not wired on "
                "this task (INGEST_REAL_STORES unset; see infra/REQUESTS.md REQ-004)"
            ))
        # 5MB cap: read one byte past the limit so an oversized upload is
        # detected without buffering the whole excess.
        data = await file.read(MAX_CSV_IMPORT_BYTES + 1)
        if len(data) > MAX_CSV_IMPORT_BYTES:
            raise HTTPException(status_code=413, detail=(
                f"file exceeds the {MAX_CSV_IMPORT_BYTES // (1024 * 1024)}MB import cap"
            ))
        if not data:
            raise HTTPException(status_code=422, detail="empty file")
        try:
            # tenant from the VERIFIED claim only.
            report = deps.csv_importer(claims.tenant_id, entity, data, mapping_dict)
        except ValueError as exc:
            # Whole-file problems (CsvImportError subclasses ValueError):
            # encoding, no header, unusable mapping — the caller can fix these.
            raise HTTPException(status_code=422, detail=str(exc))
        except Exception as exc:  # noqa: BLE001 — surface as 502, generic detail
            log.error("integrations: csv import failed for tenant %s entity %s (%s)",
                      claims.tenant_id, entity, type(exc).__name__)
            raise HTTPException(status_code=502, detail="csv import failed")
        return {"name": "csv", "report": _result_dict(report)}

    # ----------------------------------------------------------------------- #
    # OAuth "connect with login" — start + callback. Same vault slot, same
    # gating as the pasted-key path; OAuth only changes WHAT fills the slot (an
    # access+refresh token envelope) and adds a refresh path (in the connector).
    # ----------------------------------------------------------------------- #
    def _oauth_provider_or_error(name: str):
        """Resolve (oauth_module, provider, OAuthConfig), enforcing the gating that
        BOTH routes share. Raises an honest HTTPException (404/409/503) otherwise.
        Returns a ready triple only when every piece is wired."""
        _known_or_404(name)
        oauth = _oauth_module()
        if oauth is None:
            raise HTTPException(status_code=503, detail=(
                "OAuth connect is not available on this deployment "
                "(the ingestion plane is not bundled in this image)"
            ))
        provider = oauth.get_provider(name)
        if provider is None:
            # A known integration that simply has no OAuth flow (csv/stripe/ghl):
            # 409, point at the pasted-credentials path.
            raise HTTPException(status_code=409, detail=(
                f"{name} has no OAuth login flow — connect it via "
                "POST /integrations/{name}/credentials"
            ))
        cfg = oauth.OAuthConfig(
            state_secret=deps.oauth_state_secret,
            redirect_base=deps.oauth_redirect_base,
            app_return_url=deps.oauth_app_return_url,
        )
        if (not cfg.configured() or deps.secret_writer is None
                or deps.secret_reader is None):
            raise HTTPException(status_code=503, detail=(
                "OAuth connect is not configured — set "
                f"{ENV_INTEGRATIONS_REAL_SECRETS}, {ENV_OAUTH_STATE_SECRET} and "
                f"{ENV_OAUTH_REDIRECT_BASE}, and register the provider app's "
                f"client_id/client_secret in Secrets Manager ({provider.client_id_ref})"
            ))
        return oauth, provider, cfg

    @app.get("/integrations/{name}/oauth/start")
    def oauth_start(name: str, claims: TenantClaims = Depends(current_tenant)):
        """Begin the OAuth dance: build the provider authorize URL with a SIGNED
        state binding THIS tenant (THE TRUST RULE — tenant from the verified JWT,
        encoded HMAC-signed into `state`), and return it. Honest 503 when OAuth is
        unconfigured / the provider app creds are not registered."""
        oauth, provider, cfg = _oauth_provider_or_error(name)
        try:
            client_id = deps.secret_reader.get_secret(provider.client_id_ref)
        except Exception as exc:  # noqa: BLE001 — narrowed below; NEVER log the value
            if _is_secret_missing(exc):
                raise HTTPException(status_code=503, detail=(
                    f"{name} OAuth app is not registered — its client_id is not in "
                    "the vault yet (owner must register the provider app first)"
                ))
            log.error("integrations: oauth client_id read failed for %s (%s)",
                      name, type(exc).__name__)
            raise HTTPException(status_code=502, detail="oauth credential read failed")
        # PKCE (providers that require it, e.g. GoHighLevel): generate a
        # verifier+challenge, send ONLY the S256 challenge to the provider, and
        # carry the verifier (signed) inside the state so the unauthenticated
        # callback can present it at the token exchange. Non-PKCE providers
        # (HubSpot) skip this entirely — state shape is unchanged.
        code_verifier: str | None = None
        code_challenge: str | None = None
        if provider.pkce:
            code_verifier, code_challenge = oauth.generate_pkce_pair()
        # Signed state = HMAC(tenant_id + nonce + issued_at [+ code_verifier]). The
        # nonce is fresh entropy; the signature is the CSRF + tenant binding the
        # callback trusts.
        state = oauth.sign_state(claims.tenant_id, cfg.state_secret,
                                 nonce=_csrf_secrets.token_urlsafe(16),
                                 code_verifier=code_verifier)
        authorize_url = oauth.build_authorize_url(
            provider, client_id=client_id,
            redirect_uri=cfg.redirect_uri(name), state=state,
            code_challenge=code_challenge,
        )
        return {"name": name, "authorize_url": authorize_url}

    @app.get("/integrations/{name}/oauth/callback")
    def oauth_callback(name: str, code: str | None = None, state: str | None = None,
                       error: str | None = None):
        """Provider redirect target. UNAUTHENTICATED by necessity (a top-level
        browser redirect carries no JWT) — the tenant is recovered from the SIGNED
        state, which is the only identity this route trusts. Exchanges `code` for
        tokens, stores the OAuth envelope in the tenant's vault slot, and redirects
        back to the app. NEVER logs a token value."""
        oauth, provider, cfg = _oauth_provider_or_error(name)
        # User declined consent at the provider — not an error, send them back.
        if error:
            return RedirectResponse(
                url=_oauth_return(cfg, name, ok=False, reason="denied"), status_code=302)
        if not code or not state:
            raise HTTPException(status_code=400, detail="missing code or state")
        # Verify the signed state -> recover the tenant_id (+ the PKCE verifier for
        # providers that use it). A tampered or expired state is a hard 403: we will
        # NOT act on an unsigned/forged tenant.
        try:
            state_payload = oauth.verify_state_payload(state, cfg.state_secret)
        except oauth.StateError as exc:
            log.warning("integrations: oauth callback rejected state for %s (%s)",
                        name, type(exc).__name__)
            raise HTTPException(status_code=403, detail="invalid or expired state")
        tenant_id = state_payload["t"]
        code_verifier = state_payload.get("cv")  # None for non-PKCE providers
        try:
            client_id = deps.secret_reader.get_secret(provider.client_id_ref)
            client_secret = deps.secret_reader.get_secret(provider.client_secret_ref)
        except Exception as exc:  # noqa: BLE001 — narrowed below; NEVER log the value
            if _is_secret_missing(exc):
                raise HTTPException(status_code=503, detail=(
                    f"{name} OAuth app client credentials are not registered"))
            log.error("integrations: oauth creds read failed for %s (%s)",
                      name, type(exc).__name__)
            raise HTTPException(status_code=502, detail="oauth credential read failed")
        # Exchange the code for tokens (HTTP via oauth.post_form). A failure here
        # is the provider's, not the tenant's — send them back with an error flag.
        try:
            tokens = oauth.exchange_code(
                provider, code=code, redirect_uri=cfg.redirect_uri(name),
                client_id=client_id, client_secret=client_secret,
                code_verifier=code_verifier,
            )
        except Exception as exc:  # noqa: BLE001 — TokenExchangeError carries no token material
            log.error("integrations: oauth token exchange failed for tenant %s/%s (%s)",
                      tenant_id, name, type(exc).__name__)
            return RedirectResponse(
                url=_oauth_return(cfg, name, ok=False, reason="exchange_failed"),
                status_code=302)
        # THE TRUST RULE: the vault slot is derived from the state-recovered (and
        # thus WE-signed) tenant_id, never from any browser-supplied value.
        ref = _tenant_secret_ref(tenant_id, KNOWN_INTEGRATIONS[name]["source"])
        # GoHighLevel returns the chosen location/company on the token response,
        # Salesforce returns the per-org instance_url, and Pipedrive returns the
        # per-company api_domain; persist them so the connector can make
        # org/location/company-level calls. Providers that don't (HubSpot) pass None
        # and the envelope is unchanged.
        value = oauth.oauth_secret_value(
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            expires_at=tokens["expires_at"],
            location_id=tokens.get("location_id"),
            company_id=tokens.get("company_id"),
            instance_url=tokens.get("instance_url"),
            api_domain=tokens.get("api_domain"),
        )
        try:
            deps.secret_writer.put_secret(ref, value)
        except Exception as exc:  # noqa: BLE001 — surface; NEVER log the value/message
            log.error("integrations: oauth token store failed for %s (%s)",
                      ref, type(exc).__name__)
            raise HTTPException(status_code=502, detail="secret store write failed")
        log.info("integrations: oauth connected tenant=%s source=%s", tenant_id, name)
        return RedirectResponse(
            url=_oauth_return(cfg, name, ok=True), status_code=302)
