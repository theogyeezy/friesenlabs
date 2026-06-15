"""Unit: the per-tenant workspace store (tenant_workspaces) round-trips and binds the tenant.

InMemoryWorkspaceStore is proven as a tenant-keyed upsert/get; PgWorkspaceStore is proven (over a
fake psycopg2 pool, no DB) to begin EVERY operation with `SET LOCAL app.current_tenant` (the RLS
bind, auto-reset at txn end) and to write via ON CONFLICT (tenant_id) DO UPDATE — one row per
tenant. Also statically gates the schema: tenant_workspaces must be in the RLS DO-block array AND
carry the explicit ENABLE/FORCE + tenant_isolation policy statements.
"""
import os
import re

import pytest

import psycopg2
import psycopg2.pool

from agents.workspace_store import InMemoryWorkspaceStore, PgWorkspaceStore

SCHEMA = os.path.join(os.path.dirname(__file__), "..", "..", "db", "schema.sql")


# ---------------------------------------------------------------------------
# InMemoryWorkspaceStore
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_inmemory_round_trip():
    store = InMemoryWorkspaceStore()
    assert store.get("t-A") is None  # empty before any upsert

    store.upsert("t-A", "wrkspc_1", "env_1", "coord_1")
    row = store.get("t-A")
    assert row == {"tenant_id": "t-A", "workspace_id": "wrkspc_1",
                   "environment_id": "env_1", "coordinator_id": "coord_1",
                   "session_id": None, "roster_version": None}


@pytest.mark.unit
def test_inmemory_upsert_replaces_existing_row():
    # Same semantics as the Pg ON CONFLICT DO UPDATE: a re-provision overwrites, never duplicates.
    store = InMemoryWorkspaceStore()
    store.upsert("t-A", "wrkspc_1", "env_1", "coord_1")
    store.upsert("t-A", "wrkspc_2", "env_2", "coord_2")
    row = store.get("t-A")
    assert row["workspace_id"] == "wrkspc_2"
    assert row["environment_id"] == "env_2"
    assert row["coordinator_id"] == "coord_2"


@pytest.mark.unit
def test_inmemory_is_tenant_scoped():
    store = InMemoryWorkspaceStore()
    store.upsert("t-A", "wrkspc_A", "env_A", "coord_A")
    assert store.get("t-B") is None  # tenant B never sees tenant A's row
    # mutating a returned row must not write through to the store
    row = store.get("t-A")
    row["coordinator_id"] = "tampered"
    assert store.get("t-A")["coordinator_id"] == "coord_A"


# ---------------------------------------------------------------------------
# PgWorkspaceStore over a fake psycopg2 pool (no DB) — the security-critical bind
# ---------------------------------------------------------------------------

class FakeCursor:
    def __init__(self, log, one):
        self.log = log
        self._one = one

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self._one

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, log, one=None):
        self.log = log
        self._one = one

    def cursor(self, cursor_factory=None):
        return FakeCursor(self.log, self._one)

    def commit(self):
        pass

    def rollback(self):
        pass


class FakePool:
    """Stands in for psycopg2.pool.ThreadedConnectionPool — hands out a single shared FakeConn so
    the test can inspect every statement issued (order matters: the per-op SET LOCAL comes first)."""

    def __init__(self, minconn, maxconn, dsn, one=None):
        self.log: list = []
        self._conn = FakeConn(self.log, one)

    def getconn(self):
        return self._conn

    def putconn(self, conn):
        pass


@pytest.fixture
def patched(monkeypatch):
    pool = FakePool(1, 10, None, one={"tenant_id": "A", "workspace_id": "w", "environment_id": "e",
                                      "coordinator_id": "c", "created_at": None})
    monkeypatch.setattr(
        psycopg2.pool, "ThreadedConnectionPool", lambda minc, maxc, dsn: pool
    )
    return pool


def _sql(pool):
    return [s for s, _ in pool.log]


@pytest.mark.unit
def test_pg_store_binds_tenant_before_every_query(patched):
    store = PgWorkspaceStore("postgresql://crm_app@h/db")
    store.upsert("A", "wrkspc_1", "env_1", "coord_1")
    store.get("A")

    sql = _sql(patched)
    # upsert: [SET LOCAL, INSERT]; get: [SET LOCAL, SELECT] — the bind precedes EVERY query.
    assert len(sql) == 4
    assert sql[0].startswith("SET LOCAL app.current_tenant")
    assert "INSERT INTO tenant_workspaces" in sql[1]
    assert sql[2].startswith("SET LOCAL app.current_tenant")
    assert "SELECT * FROM tenant_workspaces" in sql[3]
    # never the session-level set (the cross-tenant leak pattern — leaks across the pool)
    assert not any(s.startswith("SET app.current_tenant") for s in sql)
    # the bind carries THIS op's tenant (THE TRUST RULE: flows in from the caller)
    assert patched.log[0][1] == ("A",)
    assert patched.log[2][1] == ("A",)


@pytest.mark.unit
def test_pg_upsert_is_on_conflict_do_update(patched):
    store = PgWorkspaceStore("postgresql://crm_app@h/db")
    store.upsert("A", "wrkspc_1", "env_1", "coord_1")

    insert_sql, params = patched.log[1]
    assert "ON CONFLICT (tenant_id) DO UPDATE" in insert_sql
    assert "workspace_id = EXCLUDED.workspace_id" in insert_sql
    assert "environment_id = EXCLUDED.environment_id" in insert_sql
    assert "coordinator_id = EXCLUDED.coordinator_id" in insert_sql
    # roster_version is COALESCE-preserved (a bare upsert keeps the prior stamp); it rides as a
    # trailing param (None here, since the caller didn't pass a version).
    assert "roster_version = COALESCE(EXCLUDED.roster_version, tenant_workspaces.roster_version)" \
        in insert_sql
    assert params == ("A", "wrkspc_1", "env_1", "coord_1", None)


@pytest.mark.unit
def test_pg_get_round_trips_row_and_none(patched, monkeypatch):
    store = PgWorkspaceStore("postgresql://crm_app@h/db")
    row = store.get("A")
    assert row == {"tenant_id": "A", "workspace_id": "w", "environment_id": "e",
                   "coordinator_id": "c", "created_at": None}

    empty = FakePool(1, 10, None, one=None)
    monkeypatch.setattr(psycopg2.pool, "ThreadedConnectionPool", lambda minc, maxc, dsn: empty)
    assert PgWorkspaceStore("postgresql://crm_app@h/db").get("A") is None


# ---------------------------------------------------------------------------
# Schema gate — tenant_workspaces must carry the full RLS contract (static, no DB)
# ---------------------------------------------------------------------------

def _schema() -> str:
    with open(SCHEMA, "r", encoding="utf-8") as f:
        return f.read()


@pytest.mark.unit
def test_schema_has_tenant_workspaces_table():
    sql = _schema()
    m = re.search(r"CREATE TABLE IF NOT EXISTS tenant_workspaces \((.*?)\n\);", sql, re.S)
    assert m, "no CREATE TABLE found for tenant_workspaces"
    body = m.group(1)
    assert re.search(r"tenant_id\s+uuid\s+PRIMARY KEY", body)
    for col in ("workspace_id", "environment_id", "coordinator_id"):
        assert re.search(rf"{col}\s+text", body), f"tenant_workspaces missing {col}"
    assert re.search(r"created_at\s+timestamptz\s+NOT NULL\s+DEFAULT now\(\)", body)


@pytest.mark.unit
def test_schema_tenant_workspaces_in_rls_do_block():
    sql = _schema()
    m = re.search(r"tenant_tables text\[\] := ARRAY\[(.*?)\];", sql, re.S)
    assert m, "RLS DO-block tenant_tables array not found"
    assert "'tenant_workspaces'" in m.group(1)


@pytest.mark.unit
def test_schema_tenant_workspaces_enables_and_forces_rls_explicitly():
    """Without FORCE, the table owner bypasses RLS — tenant isolation silently fails."""
    sql = _schema()
    assert re.search(r"ALTER TABLE tenant_workspaces\s+ENABLE ROW LEVEL SECURITY", sql)
    assert re.search(r"ALTER TABLE tenant_workspaces\s+FORCE ROW LEVEL SECURITY", sql)
    assert re.search(
        r"CREATE POLICY tenant_isolation ON tenant_workspaces\s+"
        r"USING \(tenant_id = current_setting\('app\.current_tenant', true\)::uuid\)\s+"
        r"WITH CHECK \(tenant_id = current_setting\('app\.current_tenant', true\)::uuid\)",
        sql,
    )
