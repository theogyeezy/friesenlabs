"""sync_tenant — the ingestion pipeline: pull → land → chunk → embed → upsert.

    sync_tenant(tenant_id, connector, embedder, store, cursor_store)

Flow:
  1. Read the per-tenant/per-source high-water cursor from `cursor_store`.
  2. connector.authenticate(); pull records changed since the cursor; land them
     (raw → S3, rows → Aurora) via the connector's injected sinks.
  3. Chunk each record (CRM strategy: summary + notes), then for each chunk decide
     whether it's new/changed (content hash) vs. already-stored → embed only the
     new/changed ones and UPSERT into `documents` by (tenant_id, source, ref_id).
  4. Advance the cursor to the max record updated_at so the NEXT run pulls ~nothing
     and embeds ~nothing.

`store` (documents) and `cursor_store` are INJECTED interfaces. An in-memory fake
is provided for tests; a psycopg2-backed impl (PgDocumentStore / PgCursorStore) is
defined below but only imports psycopg2 / connects when actually constructed with a
DSN — so importing this module never needs a DB or AWS.

tenant_id is stamped on every landed row (connector) and every chunk (chunker) and
asserted on upsert — no cross-tenant mixing.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Protocol, runtime_checkable

from . import EMBEDDING_DIM
from .chunk import Chunk, chunk_record
from .connectors.base import Connector, NormalizedRecord
from .embed import embed as default_embed


# --------------------------------------------------------------------------- #
# Injected interfaces.
# --------------------------------------------------------------------------- #
Embedder = Callable[[str], list[float]]  # text -> 1024-vector


@runtime_checkable
class DocumentStore(Protocol):
    """The `documents` table seam (vector store)."""

    def get_content_hash(self, tenant_id: str, source: str, ref_id: str) -> str | None:
        """Return the stored content hash for this doc, or None if absent."""
        ...

    def upsert(
        self,
        tenant_id: str,
        source: str,
        ref_id: str,
        content: str,
        embedding: list[float],
        content_hash: str,
    ) -> None:
        """Upsert one document row keyed on (tenant_id, source, ref_id)."""
        ...


@runtime_checkable
class CursorStore(Protocol):
    """Per-tenant/per-source high-water cursor seam."""

    def get(self, tenant_id: str, source: str) -> str | None: ...
    def set(self, tenant_id: str, source: str, cursor: str) -> None: ...


@dataclass
class SyncResult:
    pulled: int = 0
    landed_rows: int = 0
    chunks: int = 0
    embedded: int = 0     # how many chunks were actually embedded (new/changed)
    skipped: int = 0      # unchanged chunks not re-embedded
    cursor: str | None = None
    stored_ref_ids: list[str] = field(default_factory=list)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _chunks_for(rec: NormalizedRecord) -> list[Chunk]:
    """Apply the CRM-record chunking strategy to a normalized record."""
    return chunk_record(
        tenant_id=rec.tenant_id,
        source=rec.source,
        ref_id=rec.ref_id,
        text_blocks=rec.text_blocks,
    )


def sync_tenant(
    tenant_id: str,
    connector: Connector,
    embedder: Embedder | None,
    store: DocumentStore,
    cursor_store: CursorStore,
) -> SyncResult:
    """Run one incremental sync for `tenant_id` over `connector`'s source."""
    if embedder is None:
        embedder = default_embed
    source = connector.source
    result = SyncResult()

    since = cursor_store.get(tenant_id, source)

    connector.authenticate()
    records = list(connector.pull(since))
    result.pulled = len(records)

    # Land raw + structured rows (idempotent upsert by ref_id in the sinks).
    land_res = connector.land(records)
    result.landed_rows = land_res.rows_upserted

    max_cursor = since
    for rec in records:
        if rec.tenant_id != tenant_id:
            raise ValueError(
                f"cross-tenant record {rec.ref_id}: {rec.tenant_id} != {tenant_id}"
            )
        if rec.updated_at and (max_cursor is None or rec.updated_at > max_cursor):
            max_cursor = rec.updated_at

        for ch in _chunks_for(rec):
            result.chunks += 1
            ref = ch.doc_ref_id
            new_hash = _content_hash(ch.content)
            existing = store.get_content_hash(tenant_id, source, ref)
            if existing == new_hash:
                # Identical content already embedded — skip (incremental win).
                result.skipped += 1
                continue
            vec = embedder(ch.content)
            if len(vec) != EMBEDDING_DIM:
                raise ValueError(
                    f"embedder returned dim {len(vec)} != {EMBEDDING_DIM}"
                )
            store.upsert(tenant_id, source, ref, ch.content, vec, new_hash)
            result.embedded += 1
            result.stored_ref_ids.append(ref)

    if max_cursor and max_cursor != since:
        cursor_store.set(tenant_id, source, max_cursor)
    result.cursor = max_cursor
    return result


# --------------------------------------------------------------------------- #
# In-memory fakes (used by tests; handy for local dry-runs). No DB, no AWS.
# --------------------------------------------------------------------------- #
class InMemoryDocumentStore:
    """A dict-backed DocumentStore. Keyed by (tenant_id, source, ref_id)."""

    def __init__(self) -> None:
        # key -> {"content","embedding","content_hash"}
        self.docs: dict[tuple[str, str, str], dict] = {}

    def get_content_hash(self, tenant_id, source, ref_id):
        row = self.docs.get((tenant_id, source, ref_id))
        return row["content_hash"] if row else None

    def upsert(self, tenant_id, source, ref_id, content, embedding, content_hash):
        self.docs[(tenant_id, source, ref_id)] = {
            "tenant_id": tenant_id,
            "source": source,
            "ref_id": ref_id,
            "content": content,
            "embedding": list(embedding),
            "content_hash": content_hash,
        }


class InMemoryCursorStore:
    def __init__(self) -> None:
        self.cursors: dict[tuple[str, str], str] = {}

    def get(self, tenant_id, source):
        return self.cursors.get((tenant_id, source))

    def set(self, tenant_id, source, cursor):
        self.cursors[(tenant_id, source)] = cursor


class InMemoryRawSink:
    """A RawSink fake — records raw puts in a dict."""

    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}

    def put_raw(self, tenant_id, source, ref_id, record):
        key = f"{tenant_id}/{source}/{ref_id}.json"
        self.objects[key] = record
        return key


class InMemoryStructuredSink:
    """A StructuredSink fake — upserts rows into per-table dicts by ref_id."""

    def __init__(self) -> None:
        self.tables: dict[str, dict[str, dict]] = {}

    def upsert_rows(self, table, rows):
        tbl = self.tables.setdefault(table, {})
        for row in rows:
            tbl[(row["tenant_id"], row.get("ref_id"))] = row
        return len(rows)


# --------------------------------------------------------------------------- #
# psycopg2-backed impls — GUARDED. psycopg2 is imported only when constructed.
# Importing this module does NOT import psycopg2 or connect anywhere.
#
# SECURITY (closes the CLAUDE.md follow-up): these stores previously held ONE
# shared connection with a session-level `SET app.current_tenant` — the exact
# pattern behind the request-path cross-tenant leak. They now copy the FIXED
# `PgApprovalStore` pattern (api/control/greenlight.py): every operation checks
# a connection out of a thread-safe pool (or a per-op conn factory) and runs in
# ONE transaction that begins with `SET LOCAL app.current_tenant = %s`, so the
# GUC auto-resets at COMMIT/ROLLBACK and can never leak across operations or
# threads. NEVER a shared connection or a session-level SET.
# --------------------------------------------------------------------------- #
class _PgPooledStore:
    """Shared plumbing: pooled per-op connection + `SET LOCAL` tenant-bound txn.

    Construct with EITHER a `dsn` (a fixed-size ThreadedConnectionPool is built;
    psycopg2 imported lazily) OR a `conn_factory` (zero-arg callable returning a
    DB-API connection per operation; its `close()` runs when the op finishes).
    """

    def __init__(self, dsn: str | None = None, *,
                 conn_factory: Callable[[], object] | None = None):
        if (dsn is None) == (conn_factory is None):
            raise ValueError("provide exactly one of dsn or conn_factory")
        self._conn_factory = conn_factory
        self._pool = None
        self._psycopg2 = None
        if dsn is not None:
            import os  # noqa: PLC0415 — lazy with psycopg2 below

            import psycopg2  # noqa: PLC0415 — guarded: only on construction
            import psycopg2.pool  # noqa: PLC0415

            self._psycopg2 = psycopg2
            pool_max = int(os.environ.get("UPLIFT_DB_POOL_MAX", "4"))
            # min == max: a fixed-size pool RETAINS returned connections
            # (psycopg2 closes any conn beyond minconn on putconn).
            self._pool = psycopg2.pool.ThreadedConnectionPool(1, pool_max, dsn)

    def _getconn(self):
        if self._pool is None:
            return self._conn_factory()
        import time  # noqa: PLC0415
        deadline = time.monotonic() + 10.0
        while True:
            try:
                return self._pool.getconn()
            except self._psycopg2.pool.PoolError as exc:
                if "exhausted" not in str(exc) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.005)

    def _putconn(self, conn) -> None:
        if self._pool is None:
            close = getattr(conn, "close", None)
            if close is not None:
                close()
        else:
            self._pool.putconn(conn)

    @contextmanager
    def _tx(self, tenant_id):
        """Yield a cursor inside ONE tenant-scoped transaction.

        Begins with `SET LOCAL app.current_tenant` (auto-resets at COMMIT/ROLLBACK),
        commits on success / rolls back on error, and always returns the connection.
        """
        conn = self._getconn()
        try:
            cur = conn.cursor()
            cur.execute("SET LOCAL app.current_tenant = %s", (str(tenant_id),))
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._putconn(conn)


class PgDocumentStore(_PgPooledStore):
    """Postgres/pgvector-backed DocumentStore (pooled per-op conn + SET LOCAL).

    The schema has no hash column, so the stored hash is derived from the
    persisted content at read time (sha256(content)). That keeps us
    schema-compatible with db/schema.sql while still enabling skip-if-unchanged.
    """

    def get_content_hash(self, tenant_id, source, ref_id):
        with self._tx(tenant_id) as cur:
            cur.execute(
                "SELECT content FROM documents "
                "WHERE tenant_id=%s AND source=%s AND ref_id=%s",
                (str(tenant_id), source, ref_id),
            )
            row = cur.fetchone()
        if not row or row[0] is None:
            return None
        return _content_hash(row[0])

    def upsert(self, tenant_id, source, ref_id, content, embedding, content_hash):
        vec = "[" + ",".join(str(float(x)) for x in embedding) + "]"
        with self._tx(tenant_id) as cur:
            cur.execute(
                "INSERT INTO documents (tenant_id, source, ref_id, content, embedding) "
                "VALUES (%s,%s,%s,%s,%s::vector) "
                "ON CONFLICT (tenant_id, source, ref_id) "
                "DO UPDATE SET content=EXCLUDED.content, embedding=EXCLUDED.embedding",
                (str(tenant_id), source, ref_id, content, vec),
            )


class PgCursorStore(_PgPooledStore):
    """Cursor store over `ingest_cursor` (db/schema.sql, tenant-scoped + RLS).

    Connects as the non-owner crm_app role; every get/set runs in its own
    `SET LOCAL`-bound transaction so RLS applies to the cursor table too.
    The table is owned by db/schema.sql (no self-create here).
    """

    def get(self, tenant_id, source):
        with self._tx(tenant_id) as cur:
            cur.execute(
                "SELECT cursor_value FROM ingest_cursor WHERE tenant_id=%s AND source=%s",
                (str(tenant_id), source),
            )
            row = cur.fetchone()
        return row[0] if row else None

    def set(self, tenant_id, source, cursor):
        with self._tx(tenant_id) as cur:
            cur.execute(
                "INSERT INTO ingest_cursor (tenant_id, source, cursor_value) VALUES (%s,%s,%s) "
                "ON CONFLICT (tenant_id, source) DO UPDATE SET cursor_value=EXCLUDED.cursor_value, "
                "updated_at=now()",
                (str(tenant_id), source, cursor),
            )
