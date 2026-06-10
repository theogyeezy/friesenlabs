"""Authed per-tenant integrations endpoints — the api half of TODO INT/P2
("Build the real integrations/connect UI + backend"; the web screen rides a later cycle).

Three endpoints, all bound to the VERIFIED JWT claims (THE TRUST RULE — tenant never from a
header or the request body):

  GET  /integrations                       known connectors + this tenant's connection status
  POST /integrations/{name}/credentials    store the tenant's token in the per-tenant vault slot
                                           (uplift/{tenant_id}/{source}) via the injected
                                           SecretWriter — the token is never logged or echoed
  POST /integrations/{name}/sync           kick one incremental `sync_tenant` run for THAT
                                           tenant via the injected runner

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
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from api.auth import TenantClaims
from shared.config import ENV_INTEGRATIONS_REAL_SECRETS

log = logging.getLogger("api.integrations")

# --------------------------------------------------------------------------- #
# HOTFIX (post-#67 adversarial review): the production API image (api/Dockerfile)
# does NOT bundle ingest/, so a top-level `from ingest...` import here crash-looped
# the deployed container at boot (ModuleNotFoundError — invisible to pytest, which
# runs from the repo root where ingest/ exists). The two tiny helpers this module
# needs are inlined below as exact mirrors of ingest.connectors.base; every other
# ingest dependency is imported lazily behind try/except ImportError so the API
# stays honest-unconfigured when the package is absent from the image fileset.
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
# --------------------------------------------------------------------------- #
KNOWN_INTEGRATIONS: dict[str, dict[str, str]] = {
    "hubspot": {
        "label": "HubSpot",
        "category": "CRM & Marketing",
        "description": "Sync companies, contacts, deals and notes from HubSpot CRM "
                       "into your Uplift data plane (read-only — Uplift never writes back).",
        "source": "hubspot",
    },
}


# --------------------------------------------------------------------------- #
# The secret WRITE seam — extends the read-only SecretProvider (ingest side).
# --------------------------------------------------------------------------- #
@runtime_checkable
class SecretWriter(Protocol):
    """Writes/inspects a tenant's vaulted credential by reference name.

    The WRITE counterpart of ingest.connectors.base.SecretProvider (read-only).
    `put_secret` stores the raw value untouched (a vault must not mutate the
    secret); `secret_exists` answers connection status without ever reading the
    value back.
    """

    def put_secret(self, ref: str, value: str) -> None: ...

    def secret_exists(self, ref: str) -> bool: ...


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
            client.describe_secret(SecretId=ref)
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
        if name != "hubspot":
            raise ValueError(f"no connector wired for integration {name!r}")
        from ingest.run_sync import (  # noqa: PLC0415 — boto3/psycopg2 only at call time
            build_embedder,
            build_raw_sink,
            build_stores,
            run_one,
        )

        store, cursors = build_stores()
        return run_one(tenant_id, store=store, cursors=cursors,
                       embedder=build_embedder(), raw_sink=build_raw_sink())

    return run


def build_integrations_deps() -> IntegrationsDeps:
    """Env-built deps: real writer only under INTEGRATIONS_REAL_SECRETS; real
    runner only under the ingest plane's own INGEST_REAL_STORES. All-unset =
    all-None = every endpoint honest about being unconfigured."""
    writer = Boto3SecretWriter() if _real_secrets_mode() else None
    return IntegrationsDeps(secret_writer=writer, sync_runner=_build_sync_runner())


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


def mount_integrations(app: FastAPI, deps: IntegrationsDeps, current_tenant) -> None:
    """Mount the /integrations routes on `app`, authed via `current_tenant`
    (the same verified-claims dependency every other authed route uses)."""

    @app.get("/integrations")
    def list_integrations(claims: TenantClaims = Depends(current_tenant)):
        secrets_configured = deps.secret_writer is not None
        items = []
        for name, meta in KNOWN_INTEGRATIONS.items():
            connected: bool | None = None
            if secrets_configured:
                ref = _tenant_secret_ref(claims.tenant_id, meta["source"])  # claims ONLY
                try:
                    connected = bool(deps.secret_writer.secret_exists(ref))
                except Exception as exc:  # noqa: BLE001 — a status read must not 500 the listing
                    log.warning("integrations: status check failed for %s (%s)",
                                ref, type(exc).__name__)
                    connected = None
            items.append({
                "name": name,
                "label": meta["label"],
                "category": meta["category"],
                "description": meta["description"],
                "connected": connected,           # null = honestly unknown
                "status": _STATUS[connected],
            })
        return {
            "integrations": items,
            "secrets_configured": secrets_configured,
            "sync_configured": deps.sync_runner is not None,
        }

    @app.post("/integrations/{name}/credentials")
    def store_credentials(name: str, body: CredentialsBody,
                          claims: TenantClaims = Depends(current_tenant)):
        meta = _known_or_404(name)
        if deps.secret_writer is None:
            # Honest unconfigured answer — never pretend the token was vaulted.
            raise HTTPException(status_code=503, detail=(
                "secret storage not configured — set "
                f"{ENV_INTEGRATIONS_REAL_SECRETS} (REQ-006) to enable the vault writer"
            ))
        if not body.token or not body.token.strip():
            raise HTTPException(status_code=422, detail="token must be non-empty")
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
        return {"name": name, "secret_ref": ref, "stored": True, "status": "connected"}

    @app.post("/integrations/{name}/sync")
    def kick_sync(name: str, claims: TenantClaims = Depends(current_tenant)):
        meta = _known_or_404(name)
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
        try:
            res = deps.sync_runner(claims.tenant_id, name)  # tenant from the VERIFIED claim only
        except Exception as exc:  # noqa: BLE001 — surface as 502, generic detail
            log.error("integrations: sync failed for tenant %s/%s (%s)",
                      claims.tenant_id, name, type(exc).__name__)
            raise HTTPException(status_code=502, detail="sync failed")
        return {"name": name, "result": _result_dict(res)}
