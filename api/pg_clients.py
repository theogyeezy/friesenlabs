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


def _dict_one(cur) -> dict | None:
    """Normalize one fetched row to a dict via cursor.description (plain cursors + fakes)."""
    row = cur.fetchone()
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    columns = [d[0] for d in (cur.description or [])]
    return dict(zip(columns, row))


def _as_str(value: Any) -> str | None:
    """uuid.UUID / str -> str (JSON-stable ids); None stays None."""
    return None if value is None else str(value)


def _as_float(value: Any) -> float | None:
    """numeric/Decimal -> float for the wire; None stays None."""
    return None if value is None else float(value)


def _as_iso(value: Any) -> str | None:
    """datetime -> ISO 8601 string; pass through strings; None stays None."""
    if value is None:
        return None
    iso = getattr(value, "isoformat", None)
    return iso() if callable(iso) else str(value)


def _escape_ilike(q: str) -> str:
    r"""Escape the LIKE metacharacters (\, %, _) in a user-supplied search term and wrap it
    for a contains-match. The result is ALWAYS passed as a bind parameter with an explicit
    `ESCAPE '\'` clause — a `%`/`_` in the query is matched literally, never as a wildcard,
    so a search term cannot smuggle pattern syntax into the scan."""
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# Slot resolution (conv/slots) wants a SMALL candidate set: >1 match triggers a human
# disambiguation prompt, so anything beyond a handful of prefix hits is noise.
SLOT_SEARCH_LIMIT = 10


def _escape_ilike_prefix(q: str) -> str:
    r"""Escape the LIKE metacharacters (\, %, _) in a user-supplied name and wrap it for a
    PREFIX match ('Acme' -> 'Acme%'). Same discipline as `_escape_ilike`: always a bind
    parameter with an explicit `ESCAPE '\'` clause — user input can never smuggle wildcards."""
    escaped = str(q).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%"


def _clamp_offset(offset: Any, *, max_offset: int = 100_000) -> int:
    """Pagination offset: junk -> 0, negatives -> 0, runaway -> capped (bound, never trusted)."""
    try:
        n = int(offset)
    except (TypeError, ValueError):
        return 0
    return max(0, min(n, max_offset))


def _normalize_deal_row(row: dict) -> dict:
    """One board/detail deal row, JSON-stable (uuids -> str, Decimal -> float, ts -> ISO).

    `tenant_id` is kept (stringified) so the route can run the defense-in-depth re-check the
    other live surfaces do; the route strips it before the row leaves the API.
    """
    out = {
        "id": _as_str(row.get("id")),
        "tenant_id": _as_str(row.get("tenant_id")),
        "title": row.get("title"),
        "stage": row.get("stage"),
        "amount": _as_float(row.get("amount")),
        "currency": row.get("currency"),
        "company_id": _as_str(row.get("company_id")),
        "contact_id": _as_str(row.get("contact_id")),
        "company_name": row.get("company_name"),
        "created_at": _as_iso(row.get("created_at")),
    }
    # Detail-only display fields ride along when the query selected them.
    if "contact_name" in row:
        out["contact_name"] = row.get("contact_name")
    if "contact_email" in row:
        out["contact_email"] = row.get("contact_email")
    return out


def _normalize_contact_row(row: dict) -> dict:
    """One directory contact row, JSON-stable. `tenant_id` is kept (stringified) for the
    route's defense-in-depth re-check; the route strips it before the row leaves the API.

    `title` is always None: the contacts schema carries no title column yet — the wire shape
    names it now (the api/app.py /me `name` pattern) so the UI shape stays stable when the
    column lands, without ever inventing a value."""
    return {
        "id": _as_str(row.get("id")),
        "tenant_id": _as_str(row.get("tenant_id")),
        "name": row.get("name"),
        "title": None,
        "email": row.get("email"),
        "phone": row.get("phone"),
        "company_id": _as_str(row.get("company_id")),
        "company_name": row.get("company_name"),
        "created_at": _as_iso(row.get("created_at")),
        "last_activity_at": _as_iso(row.get("last_activity_at")),
    }


def _normalize_company_row(row: dict) -> dict:
    """One directory company row, JSON-stable (counts -> int when present)."""
    out = {
        "id": _as_str(row.get("id")),
        "tenant_id": _as_str(row.get("tenant_id")),
        "name": row.get("name"),
        "domain": row.get("domain"),
        "created_at": _as_iso(row.get("created_at")),
    }
    for key in ("contact_count", "open_deal_count"):
        if key in row:
            out[key] = int(row[key]) if row.get(key) is not None else 0
    return out


def _normalize_deal_write_row(row: dict) -> dict:
    """Write result for a deal row. The external write field `name` maps to deals.title."""
    out = {
        "id": _as_str(row.get("id")),
        "name": row.get("title"),
        "stage": row.get("stage"),
        "amount": _as_float(row.get("amount")),
    }
    if "company_id" in row:
        out["company_id"] = _as_str(row.get("company_id"))
    if "contact_id" in row:
        out["contact_id"] = _as_str(row.get("contact_id"))
    if "created_at" in row:
        out["created_at"] = _as_iso(row.get("created_at"))
    return out


def _normalize_contact_write_row(row: dict) -> dict:
    return {
        "id": _as_str(row.get("id")),
        "name": row.get("name"),
        "email": row.get("email"),
        "phone": row.get("phone"),
    }


def _normalize_activity_write_row(row: dict) -> dict:
    return {
        "id": _as_str(row.get("id")),
        "contact_id": _as_str(row.get("contact_id")),
        "deal_id": _as_str(row.get("deal_id")),
        "kind": row.get("kind"),
        "body": row.get("body"),
        "occurred_at": _as_iso(row.get("occurred_at")),
    }


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
            self._pool = psycopg2.pool.ThreadedConnectionPool(1, pool_max, dsn)

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

    def list_document_inventory(self, *, tenant_id: str) -> list[dict]:
        """Per-source document counts + newest-ingested timestamp for the tenant's corpus, RLS-
        scoped via SET LOCAL. A PLAIN aggregate — NO embedding model is touched, so the Knowledge
        inventory works the moment the data plane is wired even if the Titan embedder isn't. An
        un-ingested tenant gets an empty list (never a hand-written tenant filter; `documents` has
        created_at only — there is no updated_at column — so last_updated is MAX(created_at))."""
        with self._tx(tenant_id) as cur:
            cur.execute(
                "SELECT source, COUNT(*) AS document_count, MAX(created_at) AS last_updated "
                "FROM documents GROUP BY source ORDER BY document_count DESC, source"
            )
            rows = _dict_rows(cur)
        return [
            {
                "source": r.get("source"),
                "document_count": int(r.get("document_count") or 0),
                "last_updated": r.get("last_updated"),
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
    """Allow-listed CRM reads and post-approval writes (tenant-scoped via RLS).

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

    # ----------------------------------------------------------------- slot-resolution lookups
    # ILIKE PREFIX search for the conversational slot resolver (conv/slots): "Acme account" ->
    # candidate company rows, "email Dana" -> candidate contact rows. Same security discipline
    # as every read above: HAND-WRITTEN column lists, the search term is a bind param run
    # through `_escape_ilike_prefix` with an explicit ESCAPE clause (user `%`/`_` match
    # literally), tenancy is RLS-only via the per-op `SET LOCAL` transaction.

    def search_companies_prefix(self, *, tenant_id: str, name: str,
                                limit: int = SLOT_SEARCH_LIMIT) -> list[dict]:
        """Companies whose name starts with `name` (case-insensitive), tenant-scoped via RLS.
        Returns the slot-resolver row shape: [{id, name, domain}]."""
        n = _clamp_limit(limit, SLOT_SEARCH_LIMIT)
        with self._tx(tenant_id) as cur:
            cur.execute(
                "SELECT id, name, domain FROM companies "
                "WHERE name ILIKE %s ESCAPE '\\' "
                "ORDER BY name ASC LIMIT %s",
                (_escape_ilike_prefix(name), n),
            )
            rows = _dict_rows(cur)
        return [
            {"id": _as_str(r.get("id")), "name": r.get("name"), "domain": r.get("domain")}
            for r in rows
        ]

    def search_contacts_prefix(self, *, tenant_id: str, name: str,
                               limit: int = SLOT_SEARCH_LIMIT) -> list[dict]:
        """Contacts whose name starts with `name` (case-insensitive), tenant-scoped via RLS.
        Returns the slot-resolver row shape: [{id, name, email}]."""
        n = _clamp_limit(limit, SLOT_SEARCH_LIMIT)
        with self._tx(tenant_id) as cur:
            cur.execute(
                "SELECT id, name, email FROM contacts "
                "WHERE name ILIKE %s ESCAPE '\\' "
                "ORDER BY name ASC LIMIT %s",
                (_escape_ilike_prefix(name), n),
            )
            rows = _dict_rows(cur)
        return [
            {"id": _as_str(r.get("id")), "name": r.get("name"), "email": r.get("email")}
            for r in rows
        ]

    # ----------------------------------------------------------------- deals board reads
    # Fixed-SQL reads for api/deals_routes.py (the Pipeline board). Column lists are
    # HAND-WRITTEN (the allow-list discipline: identifiers never come from input), every value
    # is a bind param, and tenancy is RLS-only — the joins carry no tenant filter because the
    # tenant_isolation policies on deals/companies/contacts/activities scope BOTH sides of every
    # join inside the per-op `SET LOCAL` transaction.

    def list_deals_board(self, *, tenant_id: str, limit: int = MAX_LIMIT) -> list[dict]:
        """All board rows for the tenant: deal columns + the joined company name.

        Ordered newest-first; the route does the stage grouping/ordering (presentation concern).
        """
        n = _clamp_limit(limit, MAX_LIMIT)
        with self._tx(tenant_id) as cur:
            cur.execute(
                "SELECT d.id, d.tenant_id, d.title, d.stage, d.amount, d.currency, "
                "d.company_id, d.contact_id, d.created_at, c.name AS company_name "
                "FROM deals d LEFT JOIN companies c ON c.id = d.company_id "
                "ORDER BY d.created_at DESC LIMIT %s",
                (n,),
            )
            return [_normalize_deal_row(r) for r in _dict_rows(cur)]

    def get_deal_board(self, *, tenant_id: str, deal_id: str) -> dict | None:
        """One deal + its company/contact display fields. None when RLS yields no row
        (missing OR another tenant's — indistinguishable by design)."""
        with self._tx(tenant_id) as cur:
            cur.execute(
                "SELECT d.id, d.tenant_id, d.title, d.stage, d.amount, d.currency, "
                "d.company_id, d.contact_id, d.created_at, c.name AS company_name, "
                "p.name AS contact_name, p.email AS contact_email "
                "FROM deals d LEFT JOIN companies c ON c.id = d.company_id "
                "LEFT JOIN contacts p ON p.id = d.contact_id "
                "WHERE d.id = %s",
                (str(deal_id),),
            )
            rows = _dict_rows(cur)
        return _normalize_deal_row(rows[0]) if rows else None

    def list_deal_activities(self, *, tenant_id: str, deal_id: str,
                             limit: int = 20) -> list[dict]:
        """Recent activities for one deal, newest first (RLS-scoped like everything else)."""
        n = _clamp_limit(limit, 20)
        with self._tx(tenant_id) as cur:
            cur.execute(
                "SELECT id, kind, body, occurred_at FROM activities "
                "WHERE deal_id = %s ORDER BY occurred_at DESC LIMIT %s",
                (str(deal_id), n),
            )
            rows = _dict_rows(cur)
        return [
            {
                "id": _as_str(r.get("id")),
                "kind": r.get("kind"),
                "body": r.get("body"),
                "occurred_at": _as_iso(r.get("occurred_at")),
            }
            for r in rows
        ]

    # ----------------------------------------------------------------- contacts directory reads
    # Fixed-SQL reads for api/contacts_routes.py (the Contacts & Companies directory).
    # Same discipline as the deals-board reads above: HAND-WRITTEN column lists, every value a
    # bind param, tenancy RLS-only (the joins and the count/last-activity subqueries carry no
    # tenant filter — the tenant_isolation policies scope every table inside the per-op
    # `SET LOCAL` transaction). Search terms are ILIKE bind params run through _escape_ilike
    # with an explicit ESCAPE clause, so user `%`/`_` match literally. The stage literals in
    # the open-deal predicates are hand-written SQL, never input.

    def list_contacts_directory(self, *, tenant_id: str, q: str | None = None,
                                limit: int = DEFAULT_CRM_LIMIT, offset: int = 0) -> list[dict]:
        """Directory page of contacts: contact columns + the joined company name + the
        newest activity timestamp. `q` (optional) is a contains-match over name/email."""
        n = _clamp_limit(limit, DEFAULT_CRM_LIMIT)
        off = _clamp_offset(offset)
        base = (
            "SELECT c.id, c.tenant_id, c.name, c.email, c.phone, c.company_id, c.created_at, "
            "co.name AS company_name, "
            "(SELECT max(a.occurred_at) FROM activities a WHERE a.contact_id = c.id) "
            "AS last_activity_at "
            "FROM contacts c LEFT JOIN companies co ON co.id = c.company_id "
        )
        tail = "ORDER BY c.created_at DESC LIMIT %s OFFSET %s"
        with self._tx(tenant_id) as cur:
            if q:
                pat = _escape_ilike(q)
                cur.execute(
                    base + "WHERE (c.name ILIKE %s ESCAPE '\\' OR c.email ILIKE %s ESCAPE '\\') "
                    + tail,
                    (pat, pat, n, off),
                )
            else:
                cur.execute(base + tail, (n, off))
            return [_normalize_contact_row(r) for r in _dict_rows(cur)]

    def get_contact_directory(self, *, tenant_id: str, contact_id: str) -> dict | None:
        """One contact + company/last-activity display fields. None when RLS yields no row
        (missing OR another tenant's — indistinguishable by design)."""
        with self._tx(tenant_id) as cur:
            cur.execute(
                "SELECT c.id, c.tenant_id, c.name, c.email, c.phone, c.company_id, "
                "c.created_at, co.name AS company_name, "
                "(SELECT max(a.occurred_at) FROM activities a WHERE a.contact_id = c.id) "
                "AS last_activity_at "
                "FROM contacts c LEFT JOIN companies co ON co.id = c.company_id "
                "WHERE c.id = %s",
                (str(contact_id),),
            )
            rows = _dict_rows(cur)
        return _normalize_contact_row(rows[0]) if rows else None

    def list_contact_activities(self, *, tenant_id: str, contact_id: str,
                                limit: int = 20) -> list[dict]:
        """Recent activities for one contact, newest first (RLS-scoped like everything else)."""
        n = _clamp_limit(limit, 20)
        with self._tx(tenant_id) as cur:
            cur.execute(
                "SELECT id, kind, body, occurred_at FROM activities "
                "WHERE contact_id = %s ORDER BY occurred_at DESC LIMIT %s",
                (str(contact_id), n),
            )
            rows = _dict_rows(cur)
        return [
            {
                "id": _as_str(r.get("id")),
                "kind": r.get("kind"),
                "body": r.get("body"),
                "occurred_at": _as_iso(r.get("occurred_at")),
            }
            for r in rows
        ]

    def list_company_open_deals(self, *, tenant_id: str, company_id: str,
                                limit: int = DEFAULT_CRM_LIMIT) -> list[dict]:
        """A company's OPEN deals (not closed_won/closed_lost — hand-written stage literals),
        newest first. Ties the directory into the Pipeline board's data."""
        n = _clamp_limit(limit, DEFAULT_CRM_LIMIT)
        with self._tx(tenant_id) as cur:
            cur.execute(
                "SELECT d.id, d.tenant_id, d.title, d.stage, d.amount, d.currency, "
                "d.company_id, d.contact_id, d.created_at "
                "FROM deals d WHERE d.company_id = %s "
                "AND d.stage NOT IN ('closed_won', 'closed_lost') "
                "ORDER BY d.created_at DESC LIMIT %s",
                (str(company_id), n),
            )
            return [_normalize_deal_row(r) for r in _dict_rows(cur)]

    def list_companies_directory(self, *, tenant_id: str, q: str | None = None,
                                 limit: int = DEFAULT_CRM_LIMIT, offset: int = 0) -> list[dict]:
        """Directory page of companies with contact + open-deal counts (RLS scopes the count
        subqueries too). `q` (optional) is a contains-match over name/domain."""
        n = _clamp_limit(limit, DEFAULT_CRM_LIMIT)
        off = _clamp_offset(offset)
        base = (
            "SELECT co.id, co.tenant_id, co.name, co.domain, co.created_at, "
            "(SELECT count(*) FROM contacts c WHERE c.company_id = co.id) AS contact_count, "
            "(SELECT count(*) FROM deals d WHERE d.company_id = co.id "
            "AND d.stage NOT IN ('closed_won', 'closed_lost')) AS open_deal_count "
            "FROM companies co "
        )
        tail = "ORDER BY co.created_at DESC LIMIT %s OFFSET %s"
        with self._tx(tenant_id) as cur:
            if q:
                pat = _escape_ilike(q)
                cur.execute(
                    base + "WHERE (co.name ILIKE %s ESCAPE '\\' OR co.domain ILIKE %s "
                    "ESCAPE '\\') " + tail,
                    (pat, pat, n, off),
                )
            else:
                cur.execute(base + tail, (n, off))
            return [_normalize_company_row(r) for r in _dict_rows(cur)]

    def get_company_directory(self, *, tenant_id: str, company_id: str) -> dict | None:
        """One company + its contact/open-deal counts. None when RLS yields no row."""
        with self._tx(tenant_id) as cur:
            cur.execute(
                "SELECT co.id, co.tenant_id, co.name, co.domain, co.created_at, "
                "(SELECT count(*) FROM contacts c WHERE c.company_id = co.id) AS contact_count, "
                "(SELECT count(*) FROM deals d WHERE d.company_id = co.id "
                "AND d.stage NOT IN ('closed_won', 'closed_lost')) AS open_deal_count "
                "FROM companies co WHERE co.id = %s",
                (str(company_id),),
            )
            rows = _dict_rows(cur)
        return _normalize_company_row(rows[0]) if rows else None

    def list_company_contacts(self, *, tenant_id: str, company_id: str,
                              limit: int = DEFAULT_CRM_LIMIT) -> list[dict]:
        """One company's contacts, newest first (no company join — the caller has the company)."""
        n = _clamp_limit(limit, DEFAULT_CRM_LIMIT)
        with self._tx(tenant_id) as cur:
            cur.execute(
                "SELECT c.id, c.tenant_id, c.name, c.email, c.phone, c.company_id, "
                "c.created_at, "
                "(SELECT max(a.occurred_at) FROM activities a WHERE a.contact_id = c.id) "
                "AS last_activity_at "
                "FROM contacts c WHERE c.company_id = %s "
                "ORDER BY c.created_at DESC LIMIT %s",
                (str(company_id), n),
            )
            return [_normalize_contact_row(r) for r in _dict_rows(cur)]

    # ----------------------------------------------------------------- CRM writes
    # These are deliberately small fixed-surface mutators for Greenlight appliers only.
    # Identifiers are selected from allow-lists before SQL is built, values are always bound
    # parameters, and tenancy is enforced by the per-op SET LOCAL transaction + table RLS.

    _DEAL_UPDATE_COLUMNS = {"stage": "stage", "amount": "amount", "name": "title"}
    _CONTACT_UPDATE_COLUMNS = {"name": "name", "email": "email", "phone": "phone"}
    _CONTACT_SKIPPED_FIELDS = {"title": "contacts.title is not in the schema"}

    @staticmethod
    def _change_items(changes: dict, allowed: dict[str, str], *,
                      skipped: dict[str, str] | None = None) -> tuple[list[tuple[str, str, Any]], dict]:
        if not isinstance(changes, dict):
            raise ValueError("changes must be an object")
        skipped = skipped or {}
        bad = [k for k in changes if k not in allowed and k not in skipped]
        if bad:
            allowed_names = sorted([*allowed, *skipped])
            raise ValueError(
                f"change field {bad[0]!r} is not allow-listed "
                f"(allowed: {', '.join(allowed_names)})"
            )
        items = [(key, allowed[key], changes[key]) for key in changes if key in allowed]
        skipped_out = {key: skipped[key] for key in changes if key in skipped}
        return items, skipped_out

    def update_deal_fields(self, *, tenant_id: str, deal_id: str, changes: dict) -> dict:
        """Update allow-listed deal fields after Greenlight approval.

        Logical field `name` maps to the current schema column `deals.title`; callers cannot name
        arbitrary columns and tenant scoping remains RLS-only.
        """
        items, _ = self._change_items(changes, self._DEAL_UPDATE_COLUMNS)
        if not items:
            raise ValueError("changes must include at least one writable deal field")
        set_sql = ", ".join(f"{column} = %s" for _, column, _ in items)
        params = [value for _, _, value in items]
        params.append(str(deal_id))
        with self._tx(tenant_id) as cur:
            cur.execute(
                f"UPDATE deals SET {set_sql} WHERE id = %s "
                "RETURNING id, title, stage, amount, company_id, created_at",
                tuple(params),
            )
            row = _dict_one(cur)
        if row is None:
            raise ValueError("deal not found or not visible")
        return {
            "id": _as_str(row.get("id")),
            "updated": {key: changes[key] for key, _, _ in items},
            "deal": _normalize_deal_write_row(row),
        }

    def update_contact_fields(self, *, tenant_id: str, contact_id: str, changes: dict) -> dict:
        """Update allow-listed contact fields after Greenlight approval.

        `title` is an accepted no-op field for forward-compatible tool payloads; the contacts
        table has no title column today, so it is reported as skipped rather than invented.
        """
        items, skipped = self._change_items(
            changes, self._CONTACT_UPDATE_COLUMNS, skipped=self._CONTACT_SKIPPED_FIELDS
        )
        if not items:
            return {"id": str(contact_id), "updated": {}, "skipped": skipped}
        set_sql = ", ".join(f"{column} = %s" for _, column, _ in items)
        params = [value for _, _, value in items]
        params.append(str(contact_id))
        with self._tx(tenant_id) as cur:
            cur.execute(
                f"UPDATE contacts SET {set_sql} WHERE id = %s "
                "RETURNING id, name, email, phone",
                tuple(params),
            )
            row = _dict_one(cur)
        if row is None:
            raise ValueError("contact not found or not visible")
        return {
            "id": _as_str(row.get("id")),
            "updated": {key: changes[key] for key, _, _ in items},
            "skipped": skipped,
            "contact": _normalize_contact_write_row(row),
        }

    def insert_activity(self, *, tenant_id: str, kind: str, body: str,
                        contact_id: str | None = None, deal_id: str | None = None) -> dict:
        """Insert one CRM activity after Greenlight approval."""
        if contact_id is None and deal_id is None:
            raise ValueError("activity must reference a contact_id or deal_id")
        with self._tx(tenant_id) as cur:
            cur.execute(
                "INSERT INTO activities (tenant_id, contact_id, deal_id, kind, body) "
                "VALUES (%s,%s,%s,%s,%s) "
                "RETURNING id, contact_id, deal_id, kind, body, occurred_at",
                (str(tenant_id), contact_id, deal_id, kind, body),
            )
            row = _dict_one(cur)
        if row is None:
            raise RuntimeError("activity insert returned no row")
        return _normalize_activity_write_row(row)

    def insert_deal(self, *, tenant_id: str, company_id: str, name: str,
                    stage: str, amount: float | int | None,
                    contact_id: str | None = None) -> dict:
        """Insert one CRM deal after Greenlight approval. `name` writes to deals.title.

        `company_id` is normalized to NULL when blank/empty (the schema column is a
        nullable FK; an empty string is not a valid uuid). `contact_id`, when given, is
        the verified-and-existing contact this deal belongs to — its tenant-composite FK
        (deals_tenant_contact_fkey) is the last line of defense, but the route validates
        existence first so a bad id is a clean 404, not an opaque FK 500."""
        company = str(company_id).strip() if company_id else ""
        with self._tx(tenant_id) as cur:
            cur.execute(
                "INSERT INTO deals (tenant_id, company_id, contact_id, title, stage, amount) "
                "VALUES (%s,%s,%s,%s,%s,%s) "
                "RETURNING id, title, stage, amount, company_id, contact_id, created_at",
                (str(tenant_id), company or None, contact_id, name, stage, amount),
            )
            row = _dict_one(cur)
        if row is None:
            raise RuntimeError("deal insert returned no row")
        return _normalize_deal_write_row(row)

    # ----------------------------------------------------------------- ToolContext adapter
    def binding(self) -> "TenantBoundCrm":
        """A fresh, unbound per-request adapter (ToolContext.bind_tenant() will set the tenant)."""
        return TenantBoundCrm(self)

    def for_tenant(self, tenant_id: str) -> "TenantBoundCrm":
        """A per-request adapter pre-bound to `tenant_id` (the verified claim)."""
        return TenantBoundCrm(self, tenant_id)


class PgControlSettingsStore(_PgTenantClient):
    """Control-plane settings over the EXISTING `tenant_settings` table (FORCE'd RLS).

    Backs the persisted kill switch + autonomy dial (api/control/settings.py): one row per
    tenant — `autonomy_level` (seeded 'L1' at provisioning by signup/tenant_defaults.py) and
    `killswitch_engaged` (db/schema.sql append). The GLOBAL kill-switch scope rides the reserved
    all-zeros control row (api/control/settings.py GLOBAL_CONTROL_TENANT), written/read by
    deliberately scoping a transaction to that sentinel — request-path tenant scoping still
    comes ONLY from the verified claim (THE TRUST RULE).

    Same per-op `SET LOCAL app.current_tenant` transaction discipline as everything above
    (RLS scopes every read/write; WITH CHECK covers the upserts). Writes are upserts:
    `ON CONFLICT (tenant_id) DO UPDATE` — unlike the provisioning seed's DO NOTHING, a control
    flip is an explicit operator action and MUST win over the seeded default.
    """

    # The persisted autonomy texts (api/control/types.py Level values) — validated before SQL.
    _VALID_LEVELS = ("L0", "L1", "L2", "L3")

    def get(self, tenant_id) -> dict | None:
        """The tenant's control row (None when not yet seeded/flipped). RLS-scoped."""
        with self._tx(tenant_id) as cur:
            cur.execute(
                "SELECT tenant_id, autonomy_level, killswitch_engaged "
                "FROM tenant_settings WHERE tenant_id = %s",
                (str(tenant_id),),
            )
            row = _dict_one(cur)
        if row is None:
            return None
        return {
            "tenant_id": _as_str(row.get("tenant_id")),
            "autonomy_level": row.get("autonomy_level"),
            "killswitch_engaged": bool(row.get("killswitch_engaged")),
        }

    def set_killswitch(self, tenant_id, engaged: bool) -> None:
        """Upsert the kill-switch flag (audit timestamp rides along). RLS WITH CHECK enforces
        tenant_id == app.current_tenant on both the INSERT and the UPDATE arm."""
        with self._tx(tenant_id) as cur:
            cur.execute(
                "INSERT INTO tenant_settings (tenant_id, killswitch_engaged, killswitch_updated_at) "
                "VALUES (%s,%s,now()) "
                "ON CONFLICT (tenant_id) DO UPDATE SET "
                "killswitch_engaged = EXCLUDED.killswitch_engaged, killswitch_updated_at = now()",
                (str(tenant_id), bool(engaged)),
            )

    def set_autonomy(self, tenant_id, level: str) -> None:
        """Upsert the tenant's autonomy level ('L0'..'L3' — validated BEFORE any SQL)."""
        if level not in self._VALID_LEVELS:
            raise ValueError(
                f"autonomy level must be one of {', '.join(self._VALID_LEVELS)}, got {level!r}"
            )
        with self._tx(tenant_id) as cur:
            cur.execute(
                "INSERT INTO tenant_settings (tenant_id, autonomy_level) VALUES (%s,%s) "
                "ON CONFLICT (tenant_id) DO UPDATE SET autonomy_level = EXCLUDED.autonomy_level",
                (str(tenant_id), level),
            )


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

    # ----------------------------------------------------------------- slot-resolver lookups
    # The `conv.slots.CrmLookup` protocol: `find_companies(tenant_id, name)` /
    # `find_contacts(tenant_id, name)`. The prod /chat path injects THIS adapter as
    # `SlotContext.crm` (asgi wires `crm.for_tenant(tenant_id)` into the Conversation), so
    # these methods are what keeps slot resolution from 500ing on a live turn. ILIKE prefix
    # search, capped at SLOT_SEARCH_LIMIT (10), same per-op SET LOCAL pattern underneath.

    def _slot_tenant(self, tenant_id: str) -> str:
        """THE TRUST RULE, defense in depth: when this adapter is pre-bound (for_tenant /
        set_tenant from the verified claim), a caller-supplied tenant that DISAGREES is a
        cross-tenant attempt — refuse loudly, never silently serve either tenant. Unbound
        adapters use the caller's tenant_id (the slot context threads the verified claim)."""
        tid = str(tenant_id)
        if self._tenant_id is not None and tid != self._tenant_id:
            raise RuntimeError(
                f"cross-tenant slot lookup refused: adapter is bound to {self._tenant_id!r} "
                f"but the lookup asked for {tid!r}"
            )
        return self._tenant_id if self._tenant_id is not None else tid

    def find_companies(self, tenant_id: str, name: str,
                       limit: int = SLOT_SEARCH_LIMIT) -> list[dict]:
        """Slot-resolver company lookup: ILIKE prefix match on name, tenant-scoped (RLS via the
        client's SET LOCAL transaction), limit 10. Rows: [{id, name, domain}]."""
        return self._client.search_companies_prefix(
            tenant_id=self._slot_tenant(tenant_id), name=name, limit=limit
        )

    def find_contacts(self, tenant_id: str, name: str,
                      limit: int = SLOT_SEARCH_LIMIT) -> list[dict]:
        """Slot-resolver contact lookup: ILIKE prefix match on name, tenant-scoped (RLS via the
        client's SET LOCAL transaction), limit 10. Rows: [{id, name, email}]."""
        return self._client.search_contacts_prefix(
            tenant_id=self._slot_tenant(tenant_id), name=name, limit=limit
        )
