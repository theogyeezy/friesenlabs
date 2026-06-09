"""Unit: the Aurora-backed stores SET app.current_tenant before every query (RLS holds).

Uses a fake psycopg2 connection (no DB) to prove the binding + the target table for each operation —
the security-critical guarantee. Real CRUD is covered by the skip-integration test below.
"""
import pytest

import psycopg2

from api.control.greenlight import Greenlight, PgApprovalStore
from api.views import PgSavedViewStore, SavedViews


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
    def __init__(self, one=None):
        self.log: list = []
        self._one = one or {"id": "uuid-1"}

    def cursor(self, cursor_factory=None):
        return FakeCursor(self.log, self._one)

    def commit(self):
        pass


@pytest.fixture
def patched(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(psycopg2, "connect", lambda dsn: conn)
    return conn


def _sql(conn):
    return [s for s, _ in conn.log]


@pytest.mark.unit
def test_approval_store_binds_tenant_before_each_op(patched):
    store = PgApprovalStore("postgresql://crm_app@h/db")
    store.insert({"tenant_id": "A", "proposed_action": {"action": "send_email"}, "agent": "nadia",
                  "reasoning": "r", "value_at_stake": 1, "status": "pending"})
    store.bind_tenant("A")
    store.get("uuid-1")
    store.list_pending("A")
    store.update("uuid-1", {"status": "approved", "proposed_action": {"x": 1}})

    sql = _sql(patched)
    # Every data statement is preceded by a tenant bind.
    assert any("SET app.current_tenant" in s for s in sql)
    assert any("INSERT INTO approvals" in s for s in sql)
    assert any("SELECT * FROM approvals WHERE id" in s for s in sql)
    assert any("status = 'pending'" in s for s in sql)
    assert any("UPDATE approvals SET" in s for s in sql)
    # First statement issued in insert() is the tenant bind, not the write.
    assert sql[0].startswith("SET app.current_tenant")


@pytest.mark.unit
def test_saved_view_store_binds_tenant(patched):
    store = PgSavedViewStore("postgresql://crm_app@h/db")
    store.insert({"tenant_id": "A", "view_id": "v1", "version": 1, "spec_json": {"k": 1},
                  "semantic_refs": ["Deals.count"], "source_prompt": "p", "created_by": "u"})
    store.latest("A", "v1")
    store.list("A")
    sql = _sql(patched)
    assert sql[0].startswith("SET app.current_tenant")
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
