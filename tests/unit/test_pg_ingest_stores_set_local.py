"""Unit: the ingest Pg stores (PgDocumentStore / PgCursorStore) run on the FIXED
RLS pattern — pooled per-op connection + `SET LOCAL app.current_tenant` in ONE
transaction (the PgApprovalStore pattern). This closes the CLAUDE.md security
follow-up: the old impl held a single shared connection with a session-level
`SET app.current_tenant`, the exact shape behind the request-path cross-tenant leak.

Mocked psycopg2 (no DB). Proves:
  * SET LOCAL precedes EVERY statement, bound to THAT operation's tenant
  * never a session-level `SET app.current_tenant` (the leak pattern)
  * commit on success, rollback on error, connection always returned to the pool
  * the conn_factory path closes its per-op connection
  * get_content_hash derives sha256(content) from the persisted row
"""
import hashlib

import pytest

import psycopg2
import psycopg2.pool

from ingest.pipeline import PgCursorStore, PgDocumentStore


# --------------------------------------------------------------------------- fakes
class FakeCursor:
    def __init__(self, log, rows=None, fail_on=None):
        self.log = log
        self._rows = rows or []
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
    def __init__(self, log, rows=None, fail_on=None):
        self.log = log
        self._rows = rows
        self._fail_on = fail_on
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return FakeCursor(self.log, self._rows, self._fail_on)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class FakePool:
    """Stands in for psycopg2.pool.ThreadedConnectionPool — hands out a single
    shared FakeConn so every statement (and its order) is inspectable."""

    def __init__(self, rows=None, fail_on=None):
        self.log: list = []
        self.conn = FakeConn(self.log, rows, fail_on)
        self.put = 0

    def getconn(self):
        return self.conn

    def putconn(self, conn):
        self.put += 1


def _patch_pool(monkeypatch, pool):
    monkeypatch.setattr(psycopg2.pool, "ThreadedConnectionPool",
                        lambda minc, maxc, dsn: pool)


def _sql(pool):
    return [s for s, _ in pool.log]


TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


# --------------------------------------------------------------------------- documents
@pytest.mark.unit
def test_document_store_set_local_precedes_every_statement(monkeypatch):
    pool = FakePool(rows=[])
    _patch_pool(monkeypatch, pool)
    store = PgDocumentStore("postgresql://crm_app@h/db")

    store.get_content_hash(TENANT_A, "hubspot", "ct-1")
    store.upsert(TENANT_B, "hubspot", "ct-2", "body", [0.1, 0.2], "hash")

    sql = _sql(pool)
    ops = [i for i, s in enumerate(sql) if s.startswith(("SELECT", "INSERT"))]
    assert ops, "no statements issued"
    for i in ops:  # every statement immediately preceded by ITS op's tenant bind
        assert sql[i - 1].startswith("SET LOCAL app.current_tenant")
    # the bind carries each operation's own tenant (passed in, never ambient)
    assert pool.log[0][1] == (TENANT_A,)
    assert pool.log[2][1] == (TENANT_B,)
    # NEVER the session-level SET (the cross-tenant leak pattern)
    assert not any(s.startswith("SET app.current_tenant") for s in sql)
    # committed per op; connection returned per op
    assert pool.conn.commits == 2
    assert pool.put == 2


@pytest.mark.unit
def test_document_store_hash_derived_from_persisted_content(monkeypatch):
    pool = FakePool(rows=[("stored content",)])
    _patch_pool(monkeypatch, pool)
    store = PgDocumentStore("postgresql://crm_app@h/db")
    assert store.get_content_hash(TENANT_A, "hubspot", "x") == hashlib.sha256(
        b"stored content"
    ).hexdigest()

    empty = FakePool(rows=[])
    _patch_pool(monkeypatch, empty)
    assert PgDocumentStore("postgresql://crm_app@h/db").get_content_hash(
        TENANT_A, "hubspot", "x"
    ) is None


@pytest.mark.unit
def test_document_store_rolls_back_and_returns_conn_on_error(monkeypatch):
    pool = FakePool(fail_on="INSERT")
    _patch_pool(monkeypatch, pool)
    store = PgDocumentStore("postgresql://crm_app@h/db")
    with pytest.raises(RuntimeError):
        store.upsert(TENANT_A, "hubspot", "r", "c", [0.0], "h")
    assert pool.conn.rollbacks == 1
    assert pool.conn.commits == 0
    assert pool.put == 1  # always returned, even on error


@pytest.mark.unit
def test_document_store_upsert_passes_vector_as_param(monkeypatch):
    pool = FakePool()
    _patch_pool(monkeypatch, pool)
    PgDocumentStore("postgresql://crm_app@h/db").upsert(
        TENANT_A, "hubspot", "r", "c", [0.25, 0.5], "h"
    )
    insert_params = pool.log[-1][1]
    assert insert_params[-1] == "[0.25,0.5]"  # pgvector literal as a BIND param
    assert "[0.25,0.5]" not in pool.log[-1][0]  # never interpolated into SQL


# --------------------------------------------------------------------------- cursor
@pytest.mark.unit
def test_cursor_store_set_local_per_op_and_no_session_set(monkeypatch):
    pool = FakePool(rows=[("2026-01-05T00:00:00Z",)])
    _patch_pool(monkeypatch, pool)
    cursors = PgCursorStore("postgresql://crm_app@h/db")

    assert cursors.get(TENANT_A, "hubspot") == "2026-01-05T00:00:00Z"
    cursors.set(TENANT_B, "hubspot", "2026-02-01T00:00:00Z")

    sql = _sql(pool)
    ops = [i for i, s in enumerate(sql) if s.startswith(("SELECT", "INSERT"))]
    assert len(ops) == 2
    for i in ops:
        assert sql[i - 1].startswith("SET LOCAL app.current_tenant")
    assert pool.log[0][1] == (TENANT_A,)
    assert pool.log[2][1] == (TENANT_B,)
    assert not any(s.startswith("SET app.current_tenant") for s in sql)
    assert pool.conn.commits == 2
    assert pool.put == 2


@pytest.mark.unit
def test_cursor_store_conn_factory_path_closes_per_op_conn():
    made: list[FakeConn] = []
    log: list = []

    def factory():
        conn = FakeConn(log, rows=[])
        made.append(conn)
        return conn

    cursors = PgCursorStore(conn_factory=factory)
    cursors.get(TENANT_A, "hubspot")
    cursors.set(TENANT_A, "hubspot", "c1")

    assert len(made) == 2          # a FRESH connection per operation — never shared
    assert all(c.closed for c in made)
    assert all(c.commits == 1 for c in made)
    sql = [s for s, _ in log]
    assert sql[0].startswith("SET LOCAL app.current_tenant")
    assert not any(s.startswith("SET app.current_tenant") for s in sql)


@pytest.mark.unit
def test_stores_require_exactly_one_of_dsn_or_factory():
    with pytest.raises(ValueError):
        PgCursorStore()
    with pytest.raises(ValueError):
        PgDocumentStore("dsn", conn_factory=lambda: None)
