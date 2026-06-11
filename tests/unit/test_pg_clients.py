"""Unit: the tenant-scoped Postgres tool clients (PgRagClient / PgCrmClient).

Mocked psycopg2 (no DB) — proves the security-critical guarantees:
  * SET LOCAL app.current_tenant precedes EVERY SELECT (per-op txn bind; never session-level SET)
  * the query embedding is passed as a bind param to the pgvector query
  * non-allow-listed tables / filter columns are rejected BEFORE any SQL is issued
  * the conn_factory path closes the per-op connection and rolls back on error
  * the clients satisfy what the search_rag / read_crm tools expect from ToolContext
"""
import pytest

import psycopg2
import psycopg2.pool

from agents.tools.base import ToolContext
from agents.tools.readonly import ReadCrm, SearchRag
from api.pg_clients import MAX_LIMIT, PgCrmClient, PgRagClient


# --------------------------------------------------------------------------- fakes
class FakeCursor:
    def __init__(self, log, rows=None, description=None, fail_on=None):
        self.log = log
        self._rows = rows or []
        self.description = description
        self._fail_on = fail_on  # substring: raise when this SQL is executed

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.log.append((flat, params))
        if self._fail_on and self._fail_on in flat:
            raise RuntimeError(f"boom on {self._fail_on}")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, log, rows=None, description=None, fail_on=None):
        self.log = log
        self._rows = rows
        self._description = description
        self._fail_on = fail_on
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self, cursor_factory=None):
        return FakeCursor(self.log, self._rows, self._description, self._fail_on)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class FakePool:
    """Stands in for psycopg2.pool.ThreadedConnectionPool — hands out a single shared FakeConn so
    the test can inspect every statement issued (order matters: SET LOCAL must come first)."""

    def __init__(self, rows=None, description=None):
        self.log: list = []
        self._conn = FakeConn(self.log, rows, description)
        self.put = 0

    def getconn(self):
        return self._conn

    def putconn(self, conn):
        self.put += 1


def _patch_pool(monkeypatch, pool):
    monkeypatch.setattr(psycopg2.pool, "ThreadedConnectionPool",
                        lambda minc, maxc, dsn: pool)


def _sql(pool):
    return [s for s, _ in pool.log]


def _fake_embedder(vec):
    return lambda query: list(vec)


# --------------------------------------------------------------------------- PgRagClient
@pytest.mark.unit
def test_rag_search_set_local_precedes_every_select(monkeypatch):
    pool = FakePool(rows=[])
    _patch_pool(monkeypatch, pool)
    rag = PgRagClient("postgresql://crm_app@h/db", embedder=_fake_embedder([0.5, 0.5]))

    rag.search(tenant_id="T1", query="pipeline this week")
    rag.search(tenant_id="T2", query="another")

    sql = _sql(pool)
    selects = [i for i, s in enumerate(sql) if s.startswith("SELECT")]
    assert selects, "no SELECT issued"
    for i in selects:  # every SELECT is immediately preceded by its op's tenant bind
        assert sql[i - 1].startswith("SET LOCAL app.current_tenant")
    # the bind carries the caller's tenant (THE TRUST RULE: passed in, not ambient)
    assert pool.log[0][1] == ("T1",)
    assert pool.log[2][1] == ("T2",)
    # never the session-level SET (the cross-tenant leak pattern)
    assert not any(s.startswith("SET app.current_tenant") for s in sql)


@pytest.mark.unit
def test_rag_search_passes_embedding_param_to_vector_query(monkeypatch):
    pool = FakePool(rows=[])
    _patch_pool(monkeypatch, pool)
    rag = PgRagClient("postgresql://crm_app@h/db", embedder=_fake_embedder([0.1, 0.2, 0.3]))

    rag.search(tenant_id="T1", query="q", limit=5)

    select_sql, params = next((s, p) for s, p in pool.log if s.startswith("SELECT"))
    assert "documents" in select_sql
    assert "<=> %s::vector" in select_sql            # cosine distance, parameterized
    assert params[0] == "[0.1,0.2,0.3]"              # embedding bound into the similarity expr
    assert params[1] == "[0.1,0.2,0.3]"              # ... and into the ORDER BY
    assert params[2] == 5                            # limit is a bind param too
    assert "[0.1,0.2,0.3]" not in select_sql         # never interpolated into the SQL text


@pytest.mark.unit
def test_rag_search_normalizes_hits_and_clamps_limit(monkeypatch):
    rows = [
        {"ref_id": "hs-1", "source": "hubspot", "content": "Acme renewal", "score": 0.91},
        {"ref_id": "call-2", "source": "call", "content": "intro call", "score": 0.42},
    ]
    pool = FakePool(rows=rows)
    _patch_pool(monkeypatch, pool)
    rag = PgRagClient("postgresql://crm_app@h/db", embedder=_fake_embedder([1.0]))

    hits = rag.search(tenant_id="T1", query="acme", limit=10**9)

    assert hits == [
        {"ref_id": "hs-1", "source": "hubspot", "content": "Acme renewal", "score": 0.91},
        {"ref_id": "call-2", "source": "call", "content": "intro call", "score": 0.42},
    ]
    _, params = next((s, p) for s, p in pool.log if s.startswith("SELECT"))
    assert params[2] == MAX_LIMIT  # runaway limit clamped


@pytest.mark.unit
def test_rag_search_maps_tuple_rows_via_description(monkeypatch):
    pool = FakePool(rows=[("r1", "upload", "hello", 0.8)],
                    description=[("ref_id",), ("source",), ("content",), ("score",)])
    _patch_pool(monkeypatch, pool)
    rag = PgRagClient("postgresql://crm_app@h/db", embedder=_fake_embedder([1.0]))
    hits = rag.search(tenant_id="T1", query="x")
    assert hits == [{"ref_id": "r1", "source": "upload", "content": "hello", "score": 0.8}]


@pytest.mark.unit
def test_rag_rejects_empty_embedding(monkeypatch):
    pool = FakePool()
    _patch_pool(monkeypatch, pool)
    rag = PgRagClient("postgresql://crm_app@h/db", embedder=_fake_embedder([]))
    with pytest.raises(ValueError):
        rag.search(tenant_id="T1", query="x")
    assert pool.log == []  # nothing reached the DB


# --------------------------------------------------------------------------- PgCrmClient
@pytest.mark.unit
def test_crm_read_set_local_first_with_filters(monkeypatch):
    pool = FakePool(rows=[])
    _patch_pool(monkeypatch, pool)
    crm = PgCrmClient("postgresql://crm_app@h/db")

    crm.read(tenant_id="T1", entity="deals", filters={"stage": "won"}, limit=10)

    sql = _sql(pool)
    assert sql[0].startswith("SET LOCAL app.current_tenant")
    assert pool.log[0][1] == ("T1",)
    select_sql, params = pool.log[1]
    assert select_sql.startswith("SELECT * FROM deals")
    assert "stage = %s" in select_sql
    assert "tenant_id" not in select_sql  # tenancy is RLS-only — never a hand-written filter
    assert params == ("won", 10)


@pytest.mark.unit
def test_crm_find_methods_hit_allowlisted_tables_with_set_local(monkeypatch):
    pool = FakePool(rows=[])
    _patch_pool(monkeypatch, pool)
    crm = PgCrmClient("postgresql://crm_app@h/db")

    crm.find_companies(tenant_id="T1", domain="acme.com")
    crm.find_contacts(tenant_id="T1", email="a@acme.com")
    crm.find_deals(tenant_id="T1", stage="open", company_id="c-1")
    crm.find_activities(tenant_id="T1", kind="call")

    sql = _sql(pool)
    selects = [s for s in sql if s.startswith("SELECT")]
    assert len(selects) == 4
    for i, s in enumerate(sql):
        if s.startswith("SELECT"):  # SET LOCAL precedes every SELECT
            assert sql[i - 1].startswith("SET LOCAL app.current_tenant")
    assert "FROM companies" in selects[0] and "domain = %s" in selects[0]
    assert "FROM contacts" in selects[1] and "email = %s" in selects[1]
    assert "FROM deals" in selects[2] and "stage = %s" in selects[2] and "company_id = %s" in selects[2]
    assert "FROM activities" in selects[3] and "kind = %s" in selects[3]
    assert not any(s.startswith("SET app.current_tenant") for s in sql)


@pytest.mark.unit
@pytest.mark.parametrize("entity", [
    "documents", "approvals", "saved_views", "traces", "ingest_cursor",
    "users", "deals; DROP TABLE companies", "pg_catalog.pg_tables", "",
])
def test_crm_rejects_non_allowlisted_table(monkeypatch, entity):
    pool = FakePool()
    _patch_pool(monkeypatch, pool)
    crm = PgCrmClient("postgresql://crm_app@h/db")
    with pytest.raises(ValueError, match="not allow-listed"):
        crm.read(tenant_id="T1", entity=entity)
    assert pool.log == []  # rejected before ANY SQL (not even the tenant bind)


@pytest.mark.unit
def test_crm_rejects_non_allowlisted_filter_column(monkeypatch):
    pool = FakePool()
    _patch_pool(monkeypatch, pool)
    crm = PgCrmClient("postgresql://crm_app@h/db")
    with pytest.raises(ValueError, match="filter column"):
        crm.read(tenant_id="T1", entity="contacts", filters={"email = '' OR 1=1 --": "x"})
    with pytest.raises(ValueError, match="filter column"):
        crm.read(tenant_id="T1", entity="deals", filters={"tenant_id": "T2"})  # RLS-only, always
    assert pool.log == []


# --------------------------------------------------------------------------- conn_factory path
@pytest.mark.unit
def test_conn_factory_per_op_connection_closed_and_set_local_first():
    log: list = []
    conns: list[FakeConn] = []

    def factory():
        conn = FakeConn(log, rows=[])
        conns.append(conn)
        return conn

    crm = PgCrmClient(conn_factory=factory)
    crm.read(tenant_id="T1", entity="companies")
    crm.read(tenant_id="T1", entity="contacts")

    assert len(conns) == 2                       # one connection per operation, never shared
    assert all(c.closed for c in conns)          # always returned/closed
    assert all(c.commits == 1 for c in conns)
    sql = [s for s, _ in log]
    assert sql[0].startswith("SET LOCAL app.current_tenant")
    assert sql[2].startswith("SET LOCAL app.current_tenant")


@pytest.mark.unit
def test_conn_factory_rolls_back_and_closes_on_error():
    log: list = []
    conn = FakeConn(log, rows=[], fail_on="SELECT")
    rag = PgRagClient(conn_factory=lambda: conn, embedder=_fake_embedder([1.0]))
    with pytest.raises(RuntimeError, match="boom"):
        rag.search(tenant_id="T1", query="x")
    assert conn.rollbacks == 1 and conn.commits == 0 and conn.closed


@pytest.mark.unit
def test_constructor_requires_exactly_one_of_dsn_or_factory():
    with pytest.raises(ValueError):
        PgCrmClient()
    with pytest.raises(ValueError):
        PgRagClient("postgresql://x", conn_factory=lambda: None)


# --------------------------------------------------------------------------- ToolContext fit
@pytest.mark.unit
def test_tenant_bound_adapter_requires_bind_then_scopes_reads():
    log: list = []
    crm = PgCrmClient(conn_factory=lambda: FakeConn(log, rows=[]))
    bound = crm.binding()
    with pytest.raises(RuntimeError, match="tenant not bound"):
        bound.read(entity="deals")
    bound.set_tenant("T9")
    bound.read(entity="deals", limit=3)
    assert log[0][0].startswith("SET LOCAL app.current_tenant") and log[0][1] == ("T9",)

    # for_tenant pre-binds (the verified claim passed by the caller)
    log2: list = []
    crm2 = PgCrmClient(conn_factory=lambda: FakeConn(log2, rows=[]))
    crm2.for_tenant("T7").read(entity="companies")
    assert log2[0][1] == ("T7",)


@pytest.mark.unit
def test_search_rag_and_read_crm_tools_accept_the_clients(monkeypatch):
    """End-to-end signature fit: the real tools drive the clients through ToolContext."""
    pool = FakePool(rows=[{"ref_id": "d1", "source": "upload", "content": "c", "score": 1.0}])
    _patch_pool(monkeypatch, pool)
    rag = PgRagClient("postgresql://crm_app@h/db", embedder=_fake_embedder([0.4]))
    crm = PgCrmClient("postgresql://crm_app@h/db")

    ctx = ToolContext(tenant_id="T1", db=crm.binding(), rag=rag)
    out = SearchRag().invoke(ctx, q="acme")
    assert out["status"] == "ok"
    assert out["result"]["hits"][0]["ref_id"] == "d1"

    out = ReadCrm().invoke(ctx, entity="contacts", limit=2)
    assert out["status"] == "ok"

    sql = _sql(pool)
    for i, s in enumerate(sql):
        if s.startswith("SELECT"):
            assert sql[i - 1].startswith("SET LOCAL app.current_tenant")
    # every bind carries the ToolContext tenant (flowed from the verified claim upstream)
    assert all(p == ("T1",) for s, p in pool.log if s.startswith("SET LOCAL"))


# --------------------------------------------------------------------------- slot lookups
# The live-/chat 500 regression (conv/slots): the slot resolver calls
# crm.find_companies(tenant_id, name) / find_contacts(tenant_id, name) on the injected
# TenantBoundCrm adapter — these prove the adapter serves them: ILIKE PREFIX search, limit 10,
# tenant-scoped via the same per-op SET LOCAL pattern, cross-tenant input refused.
from api.pg_clients import SLOT_SEARCH_LIMIT  # noqa: E402


@pytest.mark.unit
def test_search_companies_prefix_sql_shape(monkeypatch):
    pool = FakePool(rows=[])
    _patch_pool(monkeypatch, pool)
    crm = PgCrmClient("postgresql://crm_app@h/db")

    crm.search_companies_prefix(tenant_id="T1", name="Acme")

    sql = _sql(pool)
    assert sql[0].startswith("SET LOCAL app.current_tenant")
    assert pool.log[0][1] == ("T1",)
    select_sql, params = pool.log[1]
    assert "FROM companies" in select_sql
    assert "ILIKE %s ESCAPE" in select_sql
    assert "tenant_id" not in select_sql            # tenancy is RLS-only
    assert params == ("Acme%", SLOT_SEARCH_LIMIT)    # PREFIX pattern + the default limit 10
    assert SLOT_SEARCH_LIMIT == 10


@pytest.mark.unit
def test_search_prefix_escapes_wildcards_and_clamps_limit(monkeypatch):
    pool = FakePool(rows=[])
    _patch_pool(monkeypatch, pool)
    crm = PgCrmClient("postgresql://crm_app@h/db")

    crm.search_contacts_prefix(tenant_id="T1", name="50%_off\\co", limit=10**9)

    select_sql, params = pool.log[1]
    assert "FROM contacts" in select_sql
    # User %/_/\ match literally — never smuggled pattern syntax; only OUR trailing % remains.
    assert params[0] == "50\\%\\_off\\\\co%"
    assert params[1] == MAX_LIMIT  # runaway limit clamped


@pytest.mark.unit
def test_search_prefix_normalizes_slot_resolver_row_shapes(monkeypatch):
    import uuid
    cid = uuid.uuid4()
    pool = FakePool(rows=[{"id": cid, "name": "Acme Corp", "domain": "acme.com"}])
    _patch_pool(monkeypatch, pool)
    crm = PgCrmClient("postgresql://crm_app@h/db")
    rows = crm.search_companies_prefix(tenant_id="T1", name="Acme")
    assert rows == [{"id": str(cid), "name": "Acme Corp", "domain": "acme.com"}]


@pytest.mark.unit
def test_tenant_bound_adapter_serves_slot_lookups_positionally():
    # The conv.slots.CrmLookup protocol call shape: find_companies(tenant_id, name) POSITIONAL.
    log: list = []
    crm = PgCrmClient(conn_factory=lambda: FakeConn(log, rows=[]))
    bound = crm.for_tenant("T1")

    bound.find_companies("T1", "Acme")
    bound.find_contacts("T1", "Dana")

    sql = [s for s, _ in log]
    assert sql[0].startswith("SET LOCAL app.current_tenant") and log[0][1] == ("T1",)
    assert "FROM companies" in sql[1] and "ILIKE %s ESCAPE" in sql[1]
    assert sql[2].startswith("SET LOCAL app.current_tenant") and log[2][1] == ("T1",)
    assert "FROM contacts" in sql[3] and "ILIKE %s ESCAPE" in sql[3]
    assert log[1][1] == ("Acme%", SLOT_SEARCH_LIMIT)
    assert log[3][1] == ("Dana%", SLOT_SEARCH_LIMIT)


@pytest.mark.unit
def test_tenant_bound_adapter_refuses_cross_tenant_slot_lookup():
    log: list = []
    crm = PgCrmClient(conn_factory=lambda: FakeConn(log, rows=[]))
    bound = crm.for_tenant("T1")
    with pytest.raises(RuntimeError, match="cross-tenant"):
        bound.find_companies("T2", "Acme")
    with pytest.raises(RuntimeError, match="cross-tenant"):
        bound.find_contacts("T2", "Dana")
    assert log == []  # refused before ANY SQL (not even the tenant bind)


@pytest.mark.unit
def test_unbound_adapter_uses_the_callers_verified_tenant():
    # SlotContext threads the verified claim; an unbound adapter scopes to exactly that tenant.
    log: list = []
    crm = PgCrmClient(conn_factory=lambda: FakeConn(log, rows=[]))
    crm.binding().find_companies("T7", "Acme")
    assert log[0][1] == ("T7",)


@pytest.mark.unit
def test_slot_resolver_resolves_company_through_the_real_adapter():
    """The exact live path that 500'd: conv.slots.resolve_slots -> SlotContext.crm
    (= TenantBoundCrm) -> find_companies. One prefix hit resolves cleanly to company_id."""
    from datetime import date as _date

    from conv.slots import SlotContext, resolve_slots

    import uuid
    cid = uuid.uuid4()
    log: list = []
    rows = [{"id": cid, "name": "Acme Corp", "domain": "acme.com"}]
    crm = PgCrmClient(conn_factory=lambda: FakeConn(log, rows=rows))
    ctx = SlotContext(tenant_id="T1", today=_date(2026, 6, 10), crm=crm.for_tenant("T1"))

    out = resolve_slots("how is the Acme account doing?", ctx)

    assert out.slots["company_id"] == str(cid)
    assert out.ambiguous == [] and out.unresolved == []
    assert log[0][0].startswith("SET LOCAL app.current_tenant") and log[0][1] == ("T1",)
    assert log[1][1][0] == "Acme%"
