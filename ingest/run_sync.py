"""Runnable ingestion entrypoint — the code half of the ingestion scheduler.

    python -m ingest.run_sync --tenant <id> [--tenant <id2> ...]
    python -m ingest.run_sync --all

The infra half (infra/REQUESTS.md REQ-004) is an EventBridge schedule that
RunTasks a Fargate one-off executing `python -m ingest.run_sync --all` with the
env below. This module only WIRES existing pieces: connector + stores + embedder
into `pipeline.sync_tenant`, one tenant at a time.

Environment (shared/config.py NAMES — all NEW, deliberate names; safe default
everywhere is "unset" = offline stubs):

  INGEST_REAL_STORES   MASTER SWITCH (exactly "true"/"1"). The live API task
                       already injects DB_*/AWS_REGION for OTHER features, so —
                       deploy invariance, same rationale as SIGNUP_REAL_DEPS —
                       no real adapter is selected here unless THIS flag is
                       deliberately set on the ingest task. Unset = in-memory
                       stores + stub embedder + stub secrets/source client:
                       runnable anywhere, touches nothing.
  INGEST_TENANTS       consumed by --all: either a comma-separated tenant-id list,
                       or the sentinel "auto" — DISCOVER the tenant set from the
                       per-tenant vault slots (Secrets Manager ListSecrets over the
                       uplift/{tenant}/{source} namespace; real mode only). "auto"
                       closes the connect->sync loop: a tenant who vaults a
                       credential through POST /integrations/{name}/credentials is
                       enrolled in the nightly sync with NO operator edit. NOTE:
                       either way these are schedule INPUTS (which tenants to
                       sync), not an identity seam — RLS scoping rides each
                       store's SET LOCAL bind per tenant, exactly as it does for
                       an id passed via --tenant. (IAM: "auto" needs
                       secretsmanager:ListSecrets on the ingest task role —
                       REQ-012; list APIs are metadata-only and not
                       resource-scopable.)
  INGEST_RAW_BUCKET    S3 raw-lake bucket (real mode only; unset = raw landing
                       skipped via the in-memory sink, with a warning).
  UPLIFT_DB_URL / DB_* the crm_app DSN (real mode only; via dsn_from_env()).

Exit codes: 0 = every requested tenant synced (or nothing to do);
            1 = at least one tenant failed (all are attempted; failures logged);
            2 = usage error (argparse).

IMPORT SAFETY: importing this module needs no AWS, boto3, or psycopg2; real
clients are constructed only inside main() in real mode.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Callable

from shared.config import (
    ENV_INGEST_RAW_BUCKET,
    ENV_INGEST_REAL_STORES,
    ENV_INGEST_TENANTS,
    dsn_from_env,
)

from . import EMBEDDING_DIM
from .connectors import default_structured_sink
from .connectors.registry import SYNC_SOURCES, build_sync_connector
from .pipeline import (
    InMemoryCursorStore,
    InMemoryDocumentStore,
    InMemoryRawSink,
    PgCursorStore,
    PgDocumentStore,
    SyncResult,
    sync_tenant,
)

log = logging.getLogger("ingest.run_sync")


def real_mode() -> bool:
    """True only when INGEST_REAL_STORES is exactly "true" or "1" (fail closed —
    mirrors shared.config._switch_env, the SIGNUP_REAL_DEPS semantics)."""
    return os.environ.get(ENV_INGEST_REAL_STORES, "") in ("true", "1")


# --------------------------------------------------------------------------- #
# Offline stubs — what an unswitched run wires (runnable with zero env/AWS/DB).
# --------------------------------------------------------------------------- #
class _StubSecrets:
    """Offline SecretProvider — hands back a marker token for any ref."""

    def get_secret(self, ref: str) -> str:
        return "offline-stub-token"


class _StubHubSpotClient:
    """Offline source client — pulls nothing (the dry run exercises the full
    sync path: auth -> pull -> land -> cursor, with zero records).

    Kept for back-compat with existing tests; the registry's _EmptyListClient
    is the generic equivalent used for every other source."""

    def list_companies(self, since):
        return []

    def list_contacts(self, since):
        return []

    def list_deals(self, since):
        return []

    def list_notes(self, since):
        return []


def _stub_embedder(text: str) -> list[float]:
    """Deterministic offline embedder (correct dimensionality, no AWS)."""
    return [((len(text) * 31 + i) % 997) / 997.0 for i in range(EMBEDDING_DIM)]


# --------------------------------------------------------------------------- #
# Env resolution — each builder returns the real piece in real mode, the stub
# otherwise. Kept as small seams so tests can exercise the selection logic.
# --------------------------------------------------------------------------- #
def build_stores():
    """(DocumentStore, CursorStore) — Pg (pooled per-op + SET LOCAL) in real mode."""
    if not real_mode():
        return InMemoryDocumentStore(), InMemoryCursorStore()
    dsn = dsn_from_env()
    if not dsn:
        # The deliberate switch is ON but the DB isn't wired: fail loudly rather
        # than silently syncing into a throwaway in-memory store.
        raise RuntimeError(
            f"{ENV_INGEST_REAL_STORES} is set but no DSN is configured "
            "(UPLIFT_DB_URL or DB_USER/DB_PASS/DB_HOST) — refusing a silent dry run"
        )
    return PgDocumentStore(dsn), PgCursorStore(dsn)


def build_embedder() -> Callable[[str], list[float]]:
    """Titan V2 (one lazily-built bedrock-runtime client, reused per text) in
    real mode; the deterministic stub otherwise."""
    if not real_mode():
        return _stub_embedder

    from .embed import _default_client, embed  # noqa: PLC0415 — lazy (boto3 at call time)

    holder: dict = {}

    def _embedder(text: str) -> list[float]:
        if "client" not in holder:
            holder["client"] = _default_client()
        return embed(text, client=holder["client"])

    return _embedder


def build_raw_sink():
    """S3 raw lake when real mode + INGEST_RAW_BUCKET; in-memory otherwise."""
    bucket = os.environ.get(ENV_INGEST_RAW_BUCKET, "")
    if real_mode() and bucket:
        from .sinks import S3RawSink  # noqa: PLC0415 — lazy (boto3 on first put)

        return S3RawSink(bucket)
    if real_mode():
        log.warning(
            "%s not set — raw JSON lands in an in-memory sink (discarded). "
            "Set the raw-lake bucket to keep replayable source records.",
            ENV_INGEST_RAW_BUCKET,
        )
    return InMemoryRawSink()


def build_connector(tenant_id: str, *, source: str = "hubspot", raw_sink=None):
    """A sync connector for `tenant_id` over `source` (registry name; default
    hubspot — the original single-source signature stays valid).

    Real mode: Boto3SecretProvider (per-tenant uplift/{tenant_id}/{source} ONLY —
    a missing per-tenant secret is a hard MissingTenantCredentialError, there is
    no shared-token fallback) + the source's real REST client (credential
    injected by authenticate()). Offline: stubs that pull nothing.

    The structured sink stays IN-MEMORY in both modes for now — the normalized
    rows still carry source-ref columns the CRM tables don't have (see
    ingest/sinks.py); the vector store (`documents`) is the real landing zone.
    """
    if real_mode():
        from .connectors.base import Boto3SecretProvider  # noqa: PLC0415 — lazy

        secrets = Boto3SecretProvider()
        client = None  # the registry constructs the source's real REST client
    else:
        secrets = _StubSecrets()
        client = _StubHubSpotClient() if source == "hubspot" else None
    return build_sync_connector(
        source,
        tenant_id,
        secrets=secrets,
        raw_sink=raw_sink if raw_sink is not None else InMemoryRawSink(),
        # Land structured rows into the Aurora CRM tables in real mode (gated by
        # INGEST_REAL_STORES inside the helper); in-memory otherwise.
        structured_sink=default_structured_sink(),
        client=client,
        real_client=real_mode(),
    )


def run_full_extract(tenant_id: str, *, since: int | str | None = None):
    """Drive the HubSpot FULL extract for one tenant → full-fidelity `crm_records`, REUSING the
    vault token resolution. ADDITIVE: a SEPARATE entry point from the default `--all` typed/vector
    sync (which is untouched here). Real mode only (Boto3SecretProvider + the Aurora crm_app DSN);
    returns the `FullSyncResult`."""
    if not real_mode():
        raise RuntimeError(
            f"run_full_extract requires real mode ({ENV_INGEST_REAL_STORES}); the full extract "
            "lands crm_records over the Aurora DSN"
        )
    from .connectors.base import Boto3SecretProvider  # noqa: PLC0415 — lazy
    from .connectors.registry import build_hubspot_full_connector  # noqa: PLC0415

    dsn = dsn_from_env()
    if not dsn:
        raise RuntimeError(
            f"{ENV_INGEST_REAL_STORES} is set but no DSN (UPLIFT_DB_URL or DB_USER/DB_PASS/DB_HOST) "
            "— refusing a silent dry run"
        )
    connector = build_hubspot_full_connector(tenant_id, secrets=Boto3SecretProvider(), dsn=dsn)
    return connector.sync(tenant_id, since=since)


def run_full_extract_ghl(tenant_id: str, *, since: int | str | None = None):
    """Drive the GoHighLevel FULL extract for one tenant → source-agnostic `crm_records`
    (`source='gohighlevel'`), REUSING the vault token + location_id resolution. ADDITIVE: a SEPARATE
    entry point from the default `--all` typed/vector sync (untouched). Real mode only
    (Boto3SecretProvider + the Aurora crm_app DSN); returns the `FullSyncResult`."""
    if not real_mode():
        raise RuntimeError(
            f"run_full_extract_ghl requires real mode ({ENV_INGEST_REAL_STORES}); the full extract "
            "lands crm_records over the Aurora DSN"
        )
    from .connectors.base import Boto3SecretProvider  # noqa: PLC0415 — lazy
    from .connectors.registry import build_gohighlevel_full_connector  # noqa: PLC0415

    dsn = dsn_from_env()
    if not dsn:
        raise RuntimeError(
            f"{ENV_INGEST_REAL_STORES} is set but no DSN (UPLIFT_DB_URL or DB_USER/DB_PASS/DB_HOST) "
            "— refusing a silent dry run"
        )
    connector = build_gohighlevel_full_connector(tenant_id, secrets=Boto3SecretProvider(), dsn=dsn)
    return connector.sync(tenant_id, since=since)


#: Vault-slot namespace (ingest.connectors.base.tenant_secret_ref): uplift/{tenant}/{source}.
_VAULT_PREFIX = "uplift/"


def discover_tenants(source: str, *, client=None) -> list[str]:
    """The INGEST_TENANTS="auto" path: tenant ids that have a vaulted credential for
    `source`, discovered by LISTING the uplift/{tenant}/{source} namespace (names only —
    no secret value is ever fetched here). The vault is the source of truth for
    "connected", so connecting via the API auto-enrolls a tenant in the schedule.
    Slots scheduled for deletion (a disconnect mid-window) are skipped."""
    if client is None:
        import boto3  # noqa: PLC0415 — lazy: real mode only

        client = boto3.client("secretsmanager",
                              region_name=os.environ.get("AWS_REGION", "us-east-1"))
    tenants: list[str] = []
    kwargs: dict = {"Filters": [{"Key": "name", "Values": [_VAULT_PREFIX]}],
                    "MaxResults": 100}
    while True:
        page = client.list_secrets(**kwargs)
        for entry in page.get("SecretList", []):
            if entry.get("DeletedDate") is not None:
                continue
            parts = (entry.get("Name") or "").split("/")
            # exactly uplift/{tenant}/{source} — anything else in the namespace
            # (e.g. uplift/env-id, uplift/demo-user) is not a connector slot.
            if len(parts) == 3 and parts[0] == "uplift" and parts[1] and parts[2] == source:
                tenants.append(parts[1])
        token = page.get("NextToken")
        if not token:
            break
        kwargs["NextToken"] = token
    return sorted(dict.fromkeys(tenants))


def resolve_tenants(args: argparse.Namespace) -> list[str]:
    """Tenant ids to sync: explicit --tenant flags, or INGEST_TENANTS for --all
    (a comma list, or "auto" = vault-slot discovery — see discover_tenants)."""
    if args.tenant:
        return list(dict.fromkeys(t.strip() for t in args.tenant if t.strip()))
    raw = os.environ.get(ENV_INGEST_TENANTS, "").strip()
    if raw.lower() == "auto":
        if not real_mode():
            # Offline stubs have no vault to list — honest empty, same posture as
            # an unset INGEST_TENANTS (main() logs "nothing to do" and exits 0).
            log.warning("%s=auto needs %s (no vault to discover from offline)",
                        ENV_INGEST_TENANTS, ENV_INGEST_REAL_STORES)
            return []
        return discover_tenants(args.source)
    return list(dict.fromkeys(t.strip() for t in raw.split(",") if t.strip()))


def run_one(tenant_id: str, *, store, cursors, embedder, raw_sink,
            source: str = "hubspot") -> SyncResult:
    """One incremental sync for one tenant over one source (fresh connector)."""
    connector = build_connector(tenant_id, source=source, raw_sink=raw_sink)
    return sync_tenant(tenant_id, connector, embedder, store, cursors)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m ingest.run_sync",
        description="Run incremental ingestion sync(s): pull → land → chunk → embed → upsert.",
    )
    who = p.add_mutually_exclusive_group(required=True)
    who.add_argument("--tenant", action="append", metavar="TENANT_ID",
                     help="tenant id to sync (repeatable)")
    who.add_argument("--all", action="store_true",
                     help=f"sync every tenant listed in ${ENV_INGEST_TENANTS} (comma-separated)")
    p.add_argument("--source", default="hubspot", choices=sorted(SYNC_SOURCES),
                   help="which connector to sync (registry name; default: hubspot)")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    args = _parser().parse_args(argv)

    tenants = resolve_tenants(args)
    if not tenants:
        # --all with an empty INGEST_TENANTS: a not-yet-configured schedule must
        # not page anyone — log and exit clean (REQ-004 safe defaults).
        log.warning("no tenants to sync (%s is empty) — nothing to do", ENV_INGEST_TENANTS)
        return 0

    mode = "REAL" if real_mode() else "offline-stub (set %s=1 for real adapters)" % (
        ENV_INGEST_REAL_STORES,
    )
    log.info("ingest sync starting: %d tenant(s), source=%s, mode=%s",
             len(tenants), args.source, mode)

    try:
        store, cursors = build_stores()
        embedder = build_embedder()
        raw_sink = build_raw_sink()
    except Exception:
        log.exception("failed to build stores/embedder from env")
        return 1

    failures = 0
    for tenant_id in tenants:
        try:
            res = run_one(tenant_id, store=store, cursors=cursors,
                          embedder=embedder, raw_sink=raw_sink, source=args.source)
        except Exception:  # noqa: BLE001 — one bad tenant must not stop the rest
            failures += 1
            log.exception("tenant %s: sync FAILED", tenant_id)
            continue
        log.info(
            "tenant %s: pulled=%d landed_rows=%d chunks=%d embedded=%d skipped=%d cursor=%s",
            tenant_id, res.pulled, res.landed_rows, res.chunks,
            res.embedded, res.skipped, res.cursor,
        )
    if failures:
        log.error("%d/%d tenant sync(s) failed", failures, len(tenants))
        return 1
    log.info("ingest sync complete: %d tenant(s) OK", len(tenants))
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised via main() in tests
    sys.exit(main())
