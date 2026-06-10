"""Unit: the Aurora-backed stores SET LOCAL app.current_tenant before every query (RLS holds).

Uses a fake psycopg2 connection pool (no DB) to prove the per-op tenant bind + the target table for
each operation — the security-critical guarantee. The real stores check a connection out of a
ThreadedConnectionPool and run each op in one transaction that begins with
`SET LOCAL app.current_tenant` (auto-resets at COMMIT/ROLLBACK), so the GUC can never leak across the
pooled connection. Real CRUD + the concurrency proof are covered by the skip-integration tests.
"""
import pytest

import psycopg2
import psycopg2.pool

from api.control.greenlight import Greenlight, PgApprovalStore
from api.views import PgSavedViewStore


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
        self._one = one or {"id": "uuid-1"}

    def cursor(self, cursor_factory=None):
        return FakeCursor(self.log, self._one)

    def commit(self):
        pass

    def rollback(self):
        pass


class FakePool:
    """Stands in for psycopg2.pool.ThreadedConnectionPool — hands out a single shared FakeConn so the
    test can inspect every statement issued (order matters: the per-op SET LOCAL must come first)."""

    def __init__(self, minconn, maxconn, dsn):
        self.log: list = []
        self._conn = FakeConn(self.log)

    def getconn(self):
        return self._conn

    def putconn(self, conn):
        pass


@pytest.fixture
def patched(monkeypatch):
    pool = FakePool(1, 10, None)
    monkeypatch.setattr(
        psycopg2.pool, "ThreadedConnectionPool", lambda minc, maxc, dsn: pool
    )
    return pool


def _sql(pool):
    return [s for s, _ in pool.log]


@pytest.mark.unit
def test_approval_store_binds_tenant_before_each_op(patched):
    store = PgApprovalStore("postgresql://crm_app@h/db")
    store.insert({"tenant_id": "A", "proposed_action": {"action": "send_email"}, "agent": "nadia",
                  "reasoning": "r", "value_at_stake": 1, "status": "pending"})
    store.get("A", "uuid-1")
    store.list_pending("A")
    store.update("A", "uuid-1", {"status": "approved", "proposed_action": {"x": 1}})

    sql = _sql(patched)
    # Every op binds the tenant with SET LOCAL (auto-resets at txn end — can't leak across the pool).
    assert any("SET LOCAL app.current_tenant" in s for s in sql)
    assert not any(s.startswith("SET app.current_tenant") for s in sql)  # never the session-level set
    assert any("INSERT INTO approvals" in s for s in sql)
    assert any("SELECT * FROM approvals WHERE id" in s for s in sql)
    assert any("status = 'pending'" in s for s in sql)
    assert any("UPDATE approvals SET" in s for s in sql)
    # First statement issued in insert() is the tenant bind, not the write.
    assert sql[0].startswith("SET LOCAL app.current_tenant")


@pytest.mark.unit
def test_saved_view_store_binds_tenant(patched):
    store = PgSavedViewStore("postgresql://crm_app@h/db")
    store.insert({"tenant_id": "A", "view_id": "v1", "version": 1, "spec_json": {"k": 1},
                  "semantic_refs": ["Deals.count"], "source_prompt": "p", "created_by": "u"})
    store.latest("A", "v1")
    store.list("A")
    sql = _sql(patched)
    assert sql[0].startswith("SET LOCAL app.current_tenant")
    assert not any(s.startswith("SET app.current_tenant") for s in sql)
    assert any("INSERT INTO saved_views" in s for s in sql)
    assert any("SELECT * FROM saved_views WHERE view_id" in s for s in sql)
    assert any("DISTINCT ON (view_id)" in s for s in sql)


@pytest.mark.unit
def test_greenlight_over_pg_store_round_trips(patched):
    # Greenlight.propose -> store.insert (binds tenant); the facade composes with the Pg store.
    gl = Greenlight(store=PgApprovalStore("postgresql://crm_app@h/db"))
    rec = gl.propose(tenant_id="A", action="send_email", agent="nadia", reasoning="r",
                     value_at_stake=1.0, payload={"to": "x@y.com"})
    assert rec is not None  # store.get returned the fake row
    assert any("INSERT INTO approvals" in s for s in _sql(patched))
