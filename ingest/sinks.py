"""Real sink implementations for the ingestion pipeline.

S3RawSink — the production RawSink (the raw lake): untouched source JSON, keyed
for replay as  {prefix}/{tenant_id}/{source}/{ref_id}.json .

PgCrmStructuredSink — the production StructuredSink (the Aurora CRM tables):
upserts a connector's normalized companies/contacts/deals rows into the
db/schema.sql tenant tables, tenant-scoped (SET LOCAL app.current_tenant as the
non-owner crm_app role so RLS applies) and IDEMPOTENT on a natural key, with a
per-row error report.

IMPORT SAFETY: boto3 is imported lazily on the first put when no client was
injected, and psycopg2 only when the Pg sink is constructed with a `dsn` —
importing this module never needs AWS or a database. Tests inject a fake S3
`client` / a fake `conn_factory` (a zero-arg callable returning a DB-API
connection) so the unit path touches neither.

The CRM sink stays behind the same INGEST_REAL_STORES master switch as every
other real adapter: run_sync only constructs it in real mode (offline runs keep
the in-memory structured sink). The registry's `default_structured_sink()` helper
picks the right one from the env so the connector run path lands real rows when —
and only when — the switch is set.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from ingest.pipeline import _PgPooledStore

log = logging.getLogger("ingest.sinks")


class S3RawSink:
    """RawSink over S3 (lazy boto3; injected fake client in tests)."""

    def __init__(self, bucket: str, *, prefix: str = "raw",
                 region: str | None = None, client: Any = None) -> None:
        if not bucket:
            raise ValueError("S3RawSink requires a bucket name")
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._region = region
        self._client = client

    def _s3(self) -> Any:
        if self._client is None:
            import os  # noqa: PLC0415 — lazy with boto3 below

            import boto3  # noqa: PLC0415 — lazy: import-safe module

            region = self._region or os.environ.get("AWS_REGION", "us-east-1")
            self._client = boto3.client("s3", region_name=region)
        return self._client

    def put_raw(self, tenant_id: str, source: str, ref_id: str, record: dict) -> str:
        key = f"{self._prefix}/{tenant_id}/{source}/{ref_id}.json"
        self._s3().put_object(
            Bucket=self._bucket,
            Key=key,
            Body=json.dumps(record, default=str).encode("utf-8"),
            ContentType="application/json",
        )
        return f"s3://{self._bucket}/{key}"


# --------------------------------------------------------------------------- #
# CRM structured sink (Aurora).
#
# THE SCHEMA-MAPPING PROBLEM the old stub punted on: a connector's normalized row
# carries SOURCE-REF columns (ref_id + source, and company_ref_id/contact_ref_id/
# deal_ref_id pointing at OTHER source objects) — but db/schema.sql's CRM tables
# key their relations on OUR uuids (company_id/contact_id/deal_id) and have no
# `source` column. So landing a row needs a ref->uuid resolution pass.
#
# THE NATURAL KEY (idempotency): a CRM row is identified by
# (tenant_id, source, source_ref_id). The CRM tables have a `ref_id text` column
# but no `source` column, so we store a SOURCE-NAMESPACED ref_id "{source}:{ref}"
# — globally unique within a tenant across sources — and dedupe on
# (tenant_id, ref_id). A re-sync of the same source object UPDATEs its existing
# row instead of inserting a duplicate.
#
# REF RESOLUTION: companies land first (the connectors yield companies before the
# contacts/deals that reference them, and `Connector.land` preserves that order
# when it groups by table), so a child's company_ref_id/contact_ref_id resolves
# to the parent's uuid via a (tenant_id, namespaced-ref_id) lookup. An unresolved
# ref is left NULL (MATCH SIMPLE keeps the FK optional) and noted in the report,
# never an error — the parent may simply not be in this incremental batch.
#
# ACTIVITIES are deliberately NOT landed here: the CRM `activities` table has no
# ref_id/source column, so they cannot be made idempotent on the natural key — a
# re-sync would duplicate every note/invoice. They already land in the `documents`
# vector store (the pipeline chunks their text), which IS idempotent by
# (tenant_id, source, ref_id). Landing them here would be a duplicate-row bug;
# the sink records a per-row "skipped" instead.
# --------------------------------------------------------------------------- #

#: tables this sink lands, in the order refs must resolve (parents before children).
_CRM_TABLES = ("companies", "contacts", "deals")

#: columns we write per CRM table (everything else on the normalized row — the
#: source-ref columns + `source` — is mapping metadata, not a CRM column).
_INSERT_COLUMNS: dict[str, tuple[str, ...]] = {
    "companies": ("tenant_id", "name", "domain", "ref_id"),
    "contacts": ("tenant_id", "company_id", "name", "email", "phone", "ref_id"),
    "deals": (
        "tenant_id", "company_id", "contact_id",
        "title", "stage", "amount", "currency", "ref_id",
    ),
}
#: the columns updated on an idempotent re-sync (every insert column except the
#: identity keys tenant_id + ref_id).
_UPDATE_COLUMNS: dict[str, tuple[str, ...]] = {
    t: tuple(c for c in cols if c not in ("tenant_id", "ref_id"))
    for t, cols in _INSERT_COLUMNS.items()
}


@dataclass
class UpsertReport:
    """Per-call outcome of one `upsert_rows` batch.

    `count` is what the StructuredSink protocol returns (rows landed). `errors`
    and `skipped` give the per-row report the task asks for — one entry per row
    that failed mapping/landing or was deliberately not landed (activities).
    """

    count: int = 0
    errors: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)


def _namespaced_ref(source: str, ref_id: str | None) -> str:
    """Source-namespaced natural key: "{source}:{ref_id}" (cross-source unique)."""
    return f"{source}:{ref_id or ''}"


class PgCrmStructuredSink(_PgPooledStore):
    """StructuredSink over the Aurora CRM tables (pooled per-op conn + SET LOCAL).

    Connects as the NON-OWNER crm_app role; every upsert runs in ONE tenant-scoped
    transaction that begins with `SET LOCAL app.current_tenant` (RLS applies, the
    GUC auto-resets at COMMIT/ROLLBACK — never a session-level SET, never a shared
    connection). Idempotent on (tenant_id, namespaced ref_id); per-row errors are
    isolated (a SAVEPOINT around each row) so one bad row never aborts the batch.

    The most recent batch's full report is available on `.last_report`.
    """

    def __init__(self, dsn: str | None = None, *, conn_factory=None) -> None:
        super().__init__(dsn, conn_factory=conn_factory)
        self.last_report = UpsertReport()

    # -- StructuredSink protocol ---------------------------------------- #
    def upsert_rows(self, table: str, rows: list[dict]) -> int:
        """Upsert `rows` into `table`; return the number of rows landed.

        Unknown/unsupported tables (e.g. activities) are reported as skipped and
        contribute 0 — never an error, never a duplicate-row bug.
        """
        report = UpsertReport()
        self.last_report = report
        if not rows:
            return 0

        if table not in _INSERT_COLUMNS:
            for row in rows:
                report.skipped.append({
                    "table": table,
                    "ref_id": row.get("ref_id"),
                    "reason": (
                        "activities have no ref_id/source CRM column — not "
                        "idempotent here; landed in the documents vector store"
                        if table == "activities"
                        else f"table {table!r} is not a CRM structured sink target"
                    ),
                })
            return 0

        tenant_id = self._batch_tenant(rows, report)
        if tenant_id is None:
            return 0

        with self._tx(tenant_id) as cur:
            for row in rows:
                self._upsert_one(cur, table, row, report)
        return report.count

    # -- internals ------------------------------------------------------ #
    @staticmethod
    def _batch_tenant(rows: list[dict], report: UpsertReport) -> str | None:
        """The single tenant this batch binds (Connector.land never mixes
        tenants). A row with a different/absent tenant is a hard mapping error —
        cross-tenant landing is exactly what RLS exists to prevent."""
        tenant_id = None
        for row in rows:
            t = row.get("tenant_id")
            if not t:
                report.errors.append({"ref_id": row.get("ref_id"),
                                      "reason": "row has no tenant_id"})
                continue
            if tenant_id is None:
                tenant_id = str(t)
            elif str(t) != tenant_id:
                report.errors.append({
                    "ref_id": row.get("ref_id"),
                    "reason": f"cross-tenant row {t} in a {tenant_id} batch",
                })
        return tenant_id

    def _upsert_one(self, cur, table: str, row: dict, report: UpsertReport) -> None:
        """Resolve refs + upsert one row inside a per-row SAVEPOINT.

        The SAVEPOINT isolates a bad row: its failure rolls back ONLY that row,
        keeps the surrounding tenant transaction alive, and is recorded in the
        report — so a single malformed object never aborts the whole batch.
        """
        source = row.get("source") or ""
        src_ref = row.get("ref_id")
        ref_id = _namespaced_ref(source, src_ref)
        try:
            cur.execute("SAVEPOINT crm_row")
            company_id = self._resolve_ref(cur, source, row.get("company_ref_id"))
            contact_id = self._resolve_ref(cur, source, row.get("contact_ref_id"))
            values = self._row_values(table, row, ref_id, company_id, contact_id)
            self._upsert_by_ref(cur, table, ref_id, values, report)
            cur.execute("RELEASE SAVEPOINT crm_row")
        except Exception as exc:  # noqa: BLE001 — isolate + report, never abort the batch
            cur.execute("ROLLBACK TO SAVEPOINT crm_row")
            report.errors.append({
                "table": table, "ref_id": src_ref, "source": source,
                "reason": f"{type(exc).__name__}: {exc}",
            })
            log.warning("crm sink: row %s/%s failed: %s", table, src_ref, exc)

    def _resolve_ref(self, cur, source: str, source_ref: str | None) -> str | None:
        """Resolve a source-side parent ref to OUR uuid (NULL if not yet synced).

        Parents land first, so a same-batch parent is already present; a ref to
        an object outside this incremental batch resolves to NULL and the FK stays
        optional (MATCH SIMPLE) — a later sync of the parent does not retro-link,
        which is fine for the read/vector path that drives the product.
        """
        if not source_ref:
            return None
        ref_id = _namespaced_ref(source, source_ref)
        # companies first, then contacts: a ref id is unique within a source, but
        # we don't know which table it points at, so probe both parents.
        for parent in ("companies", "contacts"):
            cur.execute(
                f"SELECT id FROM {parent} WHERE tenant_id = "  # noqa: S608 — table from a fixed allowlist
                "current_setting('app.current_tenant')::uuid AND ref_id = %s",
                (ref_id,),
            )
            hit = cur.fetchone()
            if hit:
                return hit[0]
        return None

    @staticmethod
    def _row_values(table: str, row: dict, ref_id: str,
                    company_id: str | None, contact_id: str | None) -> dict:
        """The CRM-column values for one row (resolved uuids substituted in)."""
        vals = {
            "tenant_id": str(row["tenant_id"]),
            "ref_id": ref_id,
            "company_id": company_id,
            "contact_id": contact_id,
            "name": row.get("name") or "",
            "domain": row.get("domain"),
            "email": row.get("email"),
            "phone": row.get("phone"),
            "title": row.get("title") or "",
            "stage": row.get("stage") or "new",
            "amount": row.get("amount"),
            "currency": row.get("currency") or "USD",
        }
        return {c: vals[c] for c in _INSERT_COLUMNS[table]}

    def _upsert_by_ref(self, cur, table: str, ref_id: str,
                       values: dict, report: UpsertReport) -> None:
        """Idempotent upsert keyed on (tenant_id, ref_id).

        The CRM tables have no unique constraint on (tenant_id, ref_id) (no schema
        change is in scope), so the idempotency is a SELECT-then-INSERT/UPDATE
        inside the same tenant transaction: find the existing row by its natural
        key, UPDATE it if present, INSERT otherwise. Tenant scoping comes from RLS
        (SET LOCAL) — we never hand-write a tenant_id WHERE filter.
        """
        cur.execute(
            f"SELECT id FROM {table} WHERE ref_id = %s",  # noqa: S608 — table from a fixed allowlist
            (ref_id,),
        )
        existing = cur.fetchone()
        if existing:
            update_cols = _UPDATE_COLUMNS[table]
            set_clause = ", ".join(f"{c} = %s" for c in update_cols)
            params = tuple(values[c] for c in update_cols) + (existing[0],)
            cur.execute(
                f"UPDATE {table} SET {set_clause} WHERE id = %s",  # noqa: S608 — fixed cols/table
                params,
            )
        else:
            cols = _INSERT_COLUMNS[table]
            placeholders = ", ".join(["%s"] * len(cols))
            cur.execute(
                f"INSERT INTO {table} ({', '.join(cols)}) "  # noqa: S608 — fixed cols/table
                f"VALUES ({placeholders})",
                tuple(values[c] for c in cols),
            )
        report.count += 1


# --------------------------------------------------------------------------- #
# crm_records — the FULL-FIDELITY sink (every property + associations, JSONB).
# --------------------------------------------------------------------------- #
def _rec_get(rec: Any, key: str) -> Any:
    """Read a field from a hubspot_full.Record dataclass OR a plain dict (the connector may
    yield either) — keeps this sink decoupled from the connector's concrete type."""
    return rec.get(key) if isinstance(rec, dict) else getattr(rec, key, None)


class PgCrmRecordsSink(_PgPooledStore):
    """Full-fidelity sink over the ``crm_records`` JSONB table (pooled per-op conn + SET LOCAL).

    UPSERTs the connector's normalized records — the FULL property bag (media as URL refs only,
    never bytes), the association graph, and the provider last-modified — keyed on
    ``(tenant_id, source, object_type, source_ref_id)``. ``tenant_id`` comes from the GUC
    (``current_setting('app.current_tenant')``), NEVER hand-written, so RLS scopes the write; the
    txn begins with ``SET LOCAL`` and the GUC auto-resets at COMMIT/ROLLBACK. Per-row errors are
    isolated by a SAVEPOINT so one bad record never aborts the batch. Connects as the NON-OWNER
    ``crm_app`` role. The most recent batch's report is on ``.last_report``.
    """

    def __init__(self, dsn: str | None = None, *, conn_factory=None, source: str = "hubspot") -> None:
        super().__init__(dsn, conn_factory=conn_factory)
        self._source = source
        self.last_report = UpsertReport()

    def upsert_records(self, tenant_id: str, records: list[Any]) -> int:
        """Upsert full records for ONE tenant; return the count landed. ON CONFLICT DO UPDATE
        refreshes properties/associations/updated_at and un-archives (archived_at = NULL)."""
        report = UpsertReport()
        self.last_report = report
        if not records:
            return 0
        with self._tx(tenant_id) as cur:
            for rec in records:
                self._upsert_one(cur, rec, report)
        return report.count

    def _upsert_one(self, cur: Any, rec: Any, report: UpsertReport) -> None:
        obj_type = _rec_get(rec, "object_type")
        src_ref = _rec_get(rec, "source_ref_id")
        if not obj_type or not src_ref:
            report.errors.append({
                "object_type": obj_type, "source_ref_id": src_ref,
                "reason": "record missing object_type/source_ref_id",
            })
            return
        props = _rec_get(rec, "properties") or {}
        assoc = _rec_get(rec, "associations") or {}
        cur.execute("SAVEPOINT crm_rec")
        try:
            cur.execute(
                "INSERT INTO crm_records "
                "(tenant_id, source, object_type, source_ref_id, properties, associations, updated_at) "
                "VALUES (current_setting('app.current_tenant')::uuid, %s, %s, %s, %s::jsonb, %s::jsonb, %s) "
                "ON CONFLICT (tenant_id, source, object_type, source_ref_id) DO UPDATE SET "
                "properties = EXCLUDED.properties, associations = EXCLUDED.associations, "
                "updated_at = EXCLUDED.updated_at, archived_at = NULL, synced_at = now()",
                (self._source, obj_type, src_ref,
                 json.dumps(props), json.dumps(assoc), _rec_get(rec, "updated_at")),
            )
            report.count += 1
        except Exception as exc:  # noqa: BLE001 — isolate + report, never abort the batch
            cur.execute("ROLLBACK TO SAVEPOINT crm_rec")
            report.errors.append({
                "object_type": obj_type, "source_ref_id": src_ref,
                "reason": f"{type(exc).__name__}: {exc}",
            })
            log.warning("crm_records sink: %s/%s failed: %s", obj_type, src_ref, exc)
