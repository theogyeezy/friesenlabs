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
  INGEST_TENANTS       comma-separated tenant ids consumed by --all. NOTE: these
                       are operator-configured schedule INPUTS (which tenants to
                       sync), not an identity seam — RLS scoping rides each
                       store's SET LOCAL bind per tenant, exactly as it does for
                       an id passed via --tenant.
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
from .connectors.registry import SYNC_SOURCES, build_sync_connector
from .pipeline import (
    InMemoryCursorStore,
    InMemoryDocumentStore,
    InMemoryRawSink,
    InMemoryStructuredSink,
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
        structured_sink=InMemoryStructuredSink(),
        client=client,
        real_client=real_mode(),
    )


def resolve_tenants(args: argparse.Namespace) -> list[str]:
    """Tenant ids to sync: explicit --tenant flags, or INGEST_TENANTS for --all."""
    if args.tenant:
        return list(dict.fromkeys(t.strip() for t in args.tenant if t.strip()))
    raw = os.environ.get(ENV_INGEST_TENANTS, "")
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
