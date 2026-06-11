"""Source connectors for the ingestion plane.

Each connector implements the `Connector` ABC (see base.py): authenticate via a
vaulted (injected) secret provider, pull records since a cursor, and land raw
JSON to S3 + normalized rows to Aurora. HubSpot is the reference implementation;
gohighlevel (EXPERIMENTAL) and stripe_data (read-only revenue) mirror its shape,
and csv_import is the push-style file importer. registry.py is the one place
that knows them all (run_sync + the API list ride it).

`default_structured_sink()` is the one place that maps a connector's normalized
output -> the structured (CRM) sink behind the INGEST_REAL_STORES master switch:
the real Aurora CRM sink (PgCrmStructuredSink — upserts companies/contacts/deals
tenant-scoped via SET LOCAL as crm_app, idempotent on a natural key) in real
mode, the in-memory sink otherwise. The connector run path (ingest/run_sync.py)
rides it so synced rows actually land in the CRM tables when the switch is set.
"""
from __future__ import annotations

from shared.config import ENV_INGEST_REAL_STORES, _switch_env, dsn_from_env


def default_structured_sink():
    """The structured sink for the connector run path, chosen from the env.

    Real mode (INGEST_REAL_STORES exactly "true"/"1") + a configured DSN -> the
    Aurora CRM sink (lands real companies/contacts/deals rows, idempotent +
    RLS-scoped). The switch ON but no DSN is a hard error (same fail-loud contract
    as run_sync.build_stores — never a silent dry run into a throwaway store).
    Offline (switch unset) -> the in-memory structured sink (lands nothing real).

    Import-safe: psycopg2 is touched only inside PgCrmStructuredSink's
    construction, and only in real mode.
    """
    from ingest.pipeline import InMemoryStructuredSink  # noqa: PLC0415 — no DB at import

    if not _switch_env(ENV_INGEST_REAL_STORES):
        return InMemoryStructuredSink()
    dsn = dsn_from_env()
    if not dsn:
        raise RuntimeError(
            f"{ENV_INGEST_REAL_STORES} is set but no DSN is configured "
            "(UPLIFT_DB_URL or DB_USER/DB_PASS/DB_HOST) — refusing to land CRM "
            "rows into a throwaway in-memory store"
        )
    from ingest.sinks import PgCrmStructuredSink  # noqa: PLC0415 — psycopg2 only here

    return PgCrmStructuredSink(dsn)
