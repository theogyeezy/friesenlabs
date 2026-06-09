"""Tenant-scoped Postgres tool clients: PgRagClient (pgvector search) + PgCrmClient (CRM reads).

These are the injectable clients for `ToolContext.rag` / `ToolContext.db` (TODO "Wire injected
tool clients", P0): `search_rag` calls `rag.search(tenant_id=..., query=...)` and `read_crm` calls
`db.read(entity=..., limit=...)` after `ToolContext.bind_tenant()` → `db.set_tenant(...)`. The
conversational layer (`conv/rag.py` RagClient) calls `search(tenant_id=..., query=..., limit=...)`.

Both clients copy the FIXED RLS pattern from `api/control/greenlight.py` `PgApprovalStore`: every
operation checks a connection out of a thread-safe pool (or a per-op conn factory) and runs in ONE
transaction that begins with `SET LOCAL app.current_tenant = %s` — so Postgres RLS scopes every
read and the GUC auto-resets at txn end, never leaking past the unit of work across the pooled
connection. NEVER a shared connection or a session-level SET (that was the critical cross-tenant
leak). Connects as the non-owner crm_app role; RLS does the tenant filtering — no hand-written
`WHERE tenant_id = ...` anywhere here.

THE TRUST RULE: `tenant_id` flows in from the caller (the verified Cognito JWT claim threaded
through ToolContext / session metadata) — it is never read from env, headers, or payloads here.

Import-safe: psycopg2 is imported lazily on construction (DSN path only); the query embedder
(Bedrock Titan via `ingest.embed`) is imported lazily at call time only when no embedder was
injected. Importing this module needs no network, AWS, or psycopg2.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Callable, Iterable

# Default result-size guards (read-only tools should never dump a table).
DEFAULT_RAG_LIMIT = 8     # matches conv.rag.RagContext.rag_limit
DEFAULT_CRM_LIMIT = 50    # matches agents.tools.readonly.ReadCrm
MAX_LIMIT = 500


def _clamp_limit(limit: Any, default: int) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return default
    return max(1, min(n, MAX_LIMIT))


def _vector_literal(embedding: Iterable[float]) -> str:
    """Serialize an embedding to the pgvector text format ('[0.1,0.2,...]').

    Passed as a bind parameter (with a ::vector cast), never interpolated into SQL. Each element
    is float-coerced so a misbehaving embedder cannot smuggle non-numeric content into the param.
    """
    values = [float(x) for x in embedding]
    if not values:
        raise ValueError("embedding must be a non-empty sequence of floats")
    return "[" + ",".join(str(v) for v in values) + "]"


def _dict_rows(cur) -> list[dict]:
    """Normalize fetched rows to dicts via cursor.description (works for plain cursors + fakes)."""
    rows = cur.fetchall() or []
    if rows and isinstance(rows[0], dict):
        return [dict(r) for r in rows]
    columns = [d[0] for d in (cur.description or [])]
    return [dict(zip(columns, r)) for r in rows]


class _PgTenantClient:
    """Shared connection plumbing — the `PgApprovalStore` pattern (pool + per-op SET LOCAL txn).

    Construct with EITHER a `dsn` (a ThreadedConnectionPool is built; psycopg2 imported lazily)
    OR a `conn_factory` (a zero-arg callable returning a DB-API connection per operation; the
    connection's `close()` is called when the op finishes — a pooling factory may hand out
    wrappers whose close() returns the conn to its pool).
    """

    def __init__(self, dsn: str | None = None, *,
                 conn_factory: Callable[[], Any] | None = None):
        if (dsn is None) == (conn_factory is None):
            raise ValueError("provide exactly one of dsn or conn_factory")
        self._conn_factory = conn_factory
        self._pool = None
        self._psycopg2 = None
        if dsn is not None:
            import psycopg2  # noqa: PLC0415 — guarded (lazy; import-safe module)
            import psycopg2.pool  # noqa: PLC0415
            self._psycopg2 = psycopg2
            pool_max = int(os.environ.get("UPLIFT_DB_POOL_MAX", "10"))
            # min == max: a fixed-size pool RETAINS returned connections (psycopg2 closes any
            # connection beyond minconn on putconn), avoiding TCP/auth churn under concurrent load.
            self._pool = psycopg2.pool.ThreadedConnectionPool(pool_max, pool_max, dsn)

    def _getconn(self):
        """Check out a pooled connection, waiting briefly if the pool is momentarily exhausted
        (see PgApprovalStore._getconn). With a conn_factory, just build a per-op connection."""
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

        Begins with `SET LOCAL app.current_tenant` (auto-resets at COMMIT/ROLLBACK), commits on
        success / rolls back on error, and always returns the connection. The per-op connection is
        never shared across threads (checked out for the duration of the txn only).
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


class PgRagClient(_PgTenantClient):
    """pgvector cosine search over `documents` (tenant-scoped via RLS).

    Satisfies what `agents.tools.readonly.SearchRag` and `conv.rag.RagClient` expect:
    `search(tenant_id=..., query=..., limit=...)` → `[{ref_id, source, content, score}]`
    where score = cosine similarity (1 - cosine distance, higher is better), served by the
    `documents_embedding_idx` HNSW index (`vector_cosine_ops`).

    `embedder` is a callable `str -> list[float]` injected at construction (a fake in tests).
    When omitted, the real Titan V2 embedder (`ingest.embed.embed`) is imported lazily at CALL
    time — never at import.
    """

    def __init__(self, dsn: str | None = None, *,
                 conn_factory: Callable[[], Any] | None = None,
                 embedder: Callable[[str], list[float]] | None = None):
        super().__init__(dsn, conn_factory=conn_factory)
        self._embedder = embedder

    def _embed(self, query: str) -> list[float]:
        if self._embedder is not None:
            return self._embedder(query)
        from ingest.embed import embed  # noqa: PLC0415 — lazy; Bedrock only at call time
        return embed(query)

    def search(self, *, tenant_id: str, query: str, limit: int = DEFAULT_RAG_LIMIT) -> list[dict]:
        """Cosine-similarity search the tenant's corpus. RLS (via SET LOCAL) scopes the rows."""
        vec = _vector_literal(self._embed(query))
        n = _clamp_limit(limit, DEFAULT_RAG_LIMIT)
        with self._tx(tenant_id) as cur:
            cur.execute(
                "SELECT ref_id, source, content, 1 - (embedding <=> %s::vector) AS score "
                "FROM documents WHERE embedding IS NOT NULL "
                "ORDER BY embedding <=> %s::vector LIMIT %s",
                (vec, vec, n),
            )
            rows = _dict_rows(cur)
        return [
            {
                "ref_id": r.get("ref_id"),
                "source": r.get("source"),
                "content": r.get("content"),
                "score": float(r["score"]) if r.get("score") is not None else None,
            }
            for r in rows
        ]


# Allow-listed CRM tables + their filterable columns (identifiers are NEVER taken from input
# without passing this list). `tenant_id` is deliberately NOT filterable — tenancy is RLS-only;
# a hand-written tenant filter is the anti-pattern this module exists to prevent.
CRM_TABLES: dict[str, tuple[str, ...]] = {
    "companies": ("id", "name", "domain", "ref_id"),
    "contacts": ("id", "company_id", "name", "email", "phone", "ref_id"),
    "deals": ("id", "company_id", "contact_id", "title", "stage", "currency", "ref_id"),
    "activities": ("id", "contact_id", "deal_id", "kind"),
}

# Deterministic ordering per table (activities has occurred_at, not created_at).
_ORDER_BY: dict[str, str] = {
    "companies": "created_at DESC",
    "contacts": "created_at DESC",
    "deals": "created_at DESC",
    "activities": "occurred_at DESC",
}


class PgCrmClient(_PgTenantClient):
    """Allow-listed, read-only CRM table reads (tenant-scoped via RLS).

    Only `companies`, `contacts`, `deals`, `activities` are reachable; any other entity raises
    ValueError before any SQL is built. Filters are simple equality matches against the table's
    allow-listed columns (unknown columns also raise — identifiers never come from input).

    Core surface takes `tenant_id` explicitly (THE TRUST RULE — the caller passes the verified
    claim). For `ToolContext.db` (which expects `set_tenant(...)` + `read(entity=, limit=)`),
    use `.for_tenant(...)` / `.binding()` to get a small per-request adapter.
    """

    def _check_table(self, entity: str) -> str:
        table = str(entity).strip().lower()
        if table not in CRM_TABLES:
            raise ValueError(
                f"entity {entity!r} is not allow-listed for CRM reads "
                f"(allowed: {', '.join(sorted(CRM_TABLES))})"
            )
        return table

    def read(self, *, tenant_id: str, entity: str, filters: dict | None = None,
             limit: int = DEFAULT_CRM_LIMIT) -> list[dict]:
        """Read rows from one allow-listed CRM table with simple equality filters."""
        table = self._check_table(entity)
        allowed_cols = CRM_TABLES[table]
        where: list[str] = []
        params: list[Any] = []
        for col, value in (filters or {}).items():
            if col not in allowed_cols:
                raise ValueError(f"filter column {col!r} is not allow-listed for {table!r} "
                                 f"(allowed: {', '.join(allowed_cols)})")
            where.append(f"{col} = %s")   # col passed the allow-list; value is a bind param
            params.append(value)
        sql = f"SELECT * FROM {table}"    # table passed the allow-list — safe to interpolate
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {_ORDER_BY[table]} LIMIT %s"
        params.append(_clamp_limit(limit, DEFAULT_CRM_LIMIT))
        with self._tx(tenant_id) as cur:
            cur.execute(sql, tuple(params))
            return _dict_rows(cur)

    # ----------------------------------------------------------------- find_* conveniences
    def find_companies(self, *, tenant_id: str, name: str | None = None, domain: str | None = None,
                       ref_id: str | None = None, limit: int = DEFAULT_CRM_LIMIT) -> list[dict]:
        filters = {k: v for k, v in {"name": name, "domain": domain, "ref_id": ref_id}.items()
                   if v is not None}
        return self.read(tenant_id=tenant_id, entity="companies", filters=filters, limit=limit)

    def find_contacts(self, *, tenant_id: str, name: str | None = None, email: str | None = None,
                      company_id: str | None = None, ref_id: str | None = None,
                      limit: int = DEFAULT_CRM_LIMIT) -> list[dict]:
        filters = {k: v for k, v in {"name": name, "email": email, "company_id": company_id,
                                     "ref_id": ref_id}.items() if v is not None}
        return self.read(tenant_id=tenant_id, entity="contacts", filters=filters, limit=limit)

    def find_deals(self, *, tenant_id: str, stage: str | None = None,
                   company_id: str | None = None, contact_id: str | None = None,
                   ref_id: str | None = None, limit: int = DEFAULT_CRM_LIMIT) -> list[dict]:
        filters = {k: v for k, v in {"stage": stage, "company_id": company_id,
                                     "contact_id": contact_id, "ref_id": ref_id}.items()
                   if v is not None}
        return self.read(tenant_id=tenant_id, entity="deals", filters=filters, limit=limit)

    def find_activities(self, *, tenant_id: str, kind: str | None = None,
                        contact_id: str | None = None, deal_id: str | None = None,
                        limit: int = DEFAULT_CRM_LIMIT) -> list[dict]:
        filters = {k: v for k, v in {"kind": kind, "contact_id": contact_id,
                                     "deal_id": deal_id}.items() if v is not None}
        return self.read(tenant_id=tenant_id, entity="activities", filters=filters, limit=limit)

    # ----------------------------------------------------------------- ToolContext adapter
    def binding(self) -> "TenantBoundCrm":
        """A fresh, unbound per-request adapter (ToolContext.bind_tenant() will set the tenant)."""
        return TenantBoundCrm(self)

    def for_tenant(self, tenant_id: str) -> "TenantBoundCrm":
        """A per-request adapter pre-bound to `tenant_id` (the verified claim)."""
        return TenantBoundCrm(self, tenant_id)


class TenantBoundCrm:
    """Per-request `ToolContext.db` adapter: `set_tenant(...)` + `read(entity=, limit=)`.

    Matches the `agents.tools.base.DBSession` protocol so `ToolContext.bind_tenant()` works and
    `ReadCrm` can call `db.read(entity=..., limit=...)`. This object is built fresh per request
    (never shared across requests/threads), so holding the tenant here cannot race — and every
    actual query still runs through the client's per-op `SET LOCAL` transaction.
    """

    def __init__(self, client: PgCrmClient, tenant_id: str | None = None):
        self._client = client
        self._tenant_id = str(tenant_id) if tenant_id is not None else None

    def set_tenant(self, tenant_id: str) -> None:
        self._tenant_id = str(tenant_id)

    def read(self, *, entity: str, filters: dict | None = None,
             limit: int = DEFAULT_CRM_LIMIT) -> list[dict]:
        if self._tenant_id is None:
            raise RuntimeError("tenant not bound — call set_tenant() (ToolContext.bind_tenant) first")
        return self._client.read(tenant_id=self._tenant_id, entity=entity,
                                 filters=filters, limit=limit)
