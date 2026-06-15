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


# ---------------------------------------------------------------------------
# Compare-and-set upgrade claim (cross-process exactly-once stamp) + retirement record
# ---------------------------------------------------------------------------
# Two api tasks can both detect a stale roster at deploy time and both re-provision; the per-process
# lock can't see across processes. upsert_coordinator_if_version makes the STORE the arbiter: the
# UPDATE only lands if roster_version is still the value the caller read, so exactly one process
# wins and the row never flip-flops. The loser records its just-minted (now-orphan) roster for the
# reaper via record_retirement.

@pytest.mark.unit
def test_inmemory_cas_wins_when_expected_matches_and_clears_session():
    store = InMemoryWorkspaceStore()
    store.upsert("t-A", "ws", "env", "coord-OLD", roster_version="rv1-stale")
    store.set_session_id("t-A", "sess-OLD")

    won = store.upsert_coordinator_if_version(
        "t-A", "coord-NEW", new_version="rv1-current", expected_version="rv1-stale")
    assert won is True
    row = store.get("t-A")
    assert row["coordinator_id"] == "coord-NEW"
    assert row["roster_version"] == "rv1-current"
    assert row["session_id"] is None          # the old session belongs to the old coordinator


@pytest.mark.unit
def test_inmemory_cas_loses_when_expected_no_longer_matches():
    store = InMemoryWorkspaceStore()
    store.upsert("t-A", "ws", "env", "coord-WINNER", roster_version="rv1-current")  # a peer won

    won = store.upsert_coordinator_if_version(
        "t-A", "coord-MINE", new_version="rv1-current", expected_version="rv1-stale")
    assert won is False
    row = store.get("t-A")
    assert row["coordinator_id"] == "coord-WINNER"   # untouched — the winner's row stands
    assert row["roster_version"] == "rv1-current"


@pytest.mark.unit
def test_inmemory_cas_matches_none_expected_for_unstamped_row():
    store = InMemoryWorkspaceStore()
    store.upsert("t-A", "ws", "env", "coord-OLD")    # roster_version None (legacy/unstamped)
    won = store.upsert_coordinator_if_version(
        "t-A", "coord-NEW", new_version="rv1-current", expected_version=None)
    assert won is True
    assert store.get("t-A")["roster_version"] == "rv1-current"


@pytest.mark.unit
def test_inmemory_cas_on_missing_row_is_a_loss():
    store = InMemoryWorkspaceStore()
    won = store.upsert_coordinator_if_version(
        "t-absent", "coord", new_version="rv1-current", expected_version=None)
    assert won is False


@pytest.mark.unit
def test_inmemory_record_retirement_captures_superseded_roster():
    store = InMemoryWorkspaceStore()
    store.record_retirement("t-A", "coord-OLD", ["agent-1", "agent-2"])
    assert store.retirements == [
        {"tenant_id": "t-A", "coordinator_id": "coord-OLD", "agent_ids": ["agent-1", "agent-2"]}
    ]


# --- Pg CAS over a fake pool that reports rowcount (won=1 / lost=0) ----------------------------
class _RowcountCursor:
    def __init__(self, log, rowcount):
        self.log = log
        self.rowcount = rowcount

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))

    def fetchone(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _RowcountConn:
    def __init__(self, log, rowcount):
        self.log = log
        self._rowcount = rowcount

    def cursor(self, cursor_factory=None):
        return _RowcountCursor(self.log, self._rowcount)

    def commit(self):
        pass

    def rollback(self):
        pass


class _RowcountPool:
    def __init__(self, minconn, maxconn, dsn, rowcount=1):
        self.log: list = []
        self._conn = _RowcountConn(self.log, rowcount)

    def getconn(self):
        return self._conn

    def putconn(self, conn):
        pass


@pytest.mark.unit
@pytest.mark.parametrize("rowcount,expected_won", [(1, True), (0, False)])
def test_pg_cas_issues_conditional_update_and_returns_won(monkeypatch, rowcount, expected_won):
    pool = _RowcountPool(1, 10, None, rowcount=rowcount)
    monkeypatch.setattr(psycopg2.pool, "ThreadedConnectionPool",
                        lambda minc, maxc, dsn: pool)
    store = PgWorkspaceStore("postgresql://crm_app@h/db")

    won = store.upsert_coordinator_if_version(
        "A", "coord-NEW", new_version="rv1-current", expected_version="rv1-stale")
    assert won is expected_won

    sql = [s for s, _ in pool.log]
    assert sql[0].startswith("SET LOCAL app.current_tenant")     # RLS bind first
    update = sql[1]
    assert update.startswith("UPDATE tenant_workspaces SET")
    assert "coordinator_id = %s" in update
    assert "roster_version = %s" in update
    assert "session_id = NULL" in update                          # stale session cleared on swap
    # the guard: only land if the stamp is still what we read (NULL-safe equality)
    assert "roster_version IS NOT DISTINCT FROM %s" in update
    _, params = pool.log[1]
    assert params == ("coord-NEW", "rv1-current", "A", "rv1-stale")


@pytest.mark.unit
def test_pg_record_retirement_inserts_into_retired_rosters(monkeypatch):
    pool = _RowcountPool(1, 10, None, rowcount=1)
    monkeypatch.setattr(psycopg2.pool, "ThreadedConnectionPool",
                        lambda minc, maxc, dsn: pool)
    store = PgWorkspaceStore("postgresql://crm_app@h/db")
    store.record_retirement("A", "coord-OLD", ["agent-1", "agent-2"])

    sql = [s for s, _ in pool.log]
    assert sql[0].startswith("SET LOCAL app.current_tenant")
    assert "INSERT INTO retired_rosters" in sql[1]
    _, params = pool.log[1]
    assert params == ("A", "coord-OLD", ["agent-1", "agent-2"])


# --- schema gate: retired_rosters is RLS-EXEMPT (a pre/cross-tenant ops ledger) ---------------
@pytest.mark.unit
def test_schema_has_retired_rosters_table_rls_exempt():
    sql = _schema()
    assert re.search(r"CREATE TABLE IF NOT EXISTS retired_rosters \(", sql), \
        "retired_rosters table missing"
    # MUST NOT be in the tenant RLS DO-block array — the reaper reads it across ALL tenants, which a
    # FORCE'd tenant_isolation policy (no app.current_tenant set) would return zero rows for.
    m = re.search(r"tenant_tables text\[\] := ARRAY\[(.*?)\];", sql, re.S)
    assert "'retired_rosters'" not in m.group(1)
    # and no explicit FORCE ROW LEVEL SECURITY for it either
    assert not re.search(r"ALTER TABLE retired_rosters\s+FORCE ROW LEVEL SECURITY", sql)
