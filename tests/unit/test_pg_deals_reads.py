"""Unit: the deals-board reads on PgCrmClient (api/pg_clients.py, mocked psycopg2 — no DB).

Security-critical guarantees, same bar as test_pg_clients.py:
  * SET LOCAL app.current_tenant precedes EVERY board SELECT (per-op txn; never session SET)
  * no hand-written tenant filter anywhere (tenancy is RLS-only, on both sides of the joins)
  * deal_id/limit are bind params, never interpolated
  * rows are normalized JSON-stable (uuid -> str, Decimal -> float, datetime -> ISO)
"""
import datetime
import decimal
import uuid

import pytest

import psycopg2
import psycopg2.pool

from api.pg_clients import MAX_LIMIT, PgCrmClient


class FakeCursor:
    def __init__(self, log, rows=None, description=None):
        self.log = log
        self._rows = rows or []
        self.description = description

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, log, rows=None, description=None):
        self.log = log
        self._rows = rows
        self._description = description
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, cursor_factory=None):
        return FakeCursor(self.log, self._rows, self._description)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakePool:
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


@pytest.mark.unit
def test_board_reads_set_local_precedes_every_select_no_tenant_filter(monkeypatch):
    pool = FakePool(rows=[])
    _patch_pool(monkeypatch, pool)
    crm = PgCrmClient("postgresql://crm_app@h/db")

    crm.list_deals_board(tenant_id="T1")
    crm.get_deal_board(tenant_id="T2", deal_id="d-1")
    crm.list_deal_activities(tenant_id="T3", deal_id="d-1")

    sql = _sql(pool)
    selects = [i for i, s in enumerate(sql) if s.startswith("SELECT")]
    assert len(selects) == 3
    for i in selects:  # every SELECT is immediately preceded by its op's tenant bind
        assert sql[i - 1].startswith("SET LOCAL app.current_tenant")
    # binds carry the caller's tenant (THE TRUST RULE: passed in, not ambient)
    assert pool.log[0][1] == ("T1",)
    assert pool.log[2][1] == ("T2",)
    assert pool.log[4][1] == ("T3",)
    # tenancy is RLS-only: no hand-written tenant filter on either side of the joins
    assert not any("tenant_id =" in s for s in sql if s.startswith("SELECT"))
    assert not any(s.startswith("SET app.current_tenant") for s in sql)


@pytest.mark.unit
def test_board_list_joins_company_name_and_binds_limit(monkeypatch):
    pool = FakePool(rows=[])
    _patch_pool(monkeypatch, pool)
    crm = PgCrmClient("postgresql://crm_app@h/db")

    crm.list_deals_board(tenant_id="T1", limit=10**9)

    select_sql, params = next((s, p) for s, p in pool.log if s.startswith("SELECT"))
    assert "LEFT JOIN companies" in select_sql
    assert "c.name AS company_name" in select_sql
    assert "ORDER BY d.created_at DESC" in select_sql
    assert params == (MAX_LIMIT,)  # runaway limit clamped, bound — never interpolated


@pytest.mark.unit
def test_detail_and_activities_bind_deal_id(monkeypatch):
    pool = FakePool(rows=[])
    _patch_pool(monkeypatch, pool)
    crm = PgCrmClient("postgresql://crm_app@h/db")

    crm.get_deal_board(tenant_id="T1", deal_id="abc-123")
    crm.list_deal_activities(tenant_id="T1", deal_id="abc-123", limit=7)

    selects = [(s, p) for s, p in pool.log if s.startswith("SELECT")]
    detail_sql, detail_params = selects[0]
    assert "WHERE d.id = %s" in detail_sql
    assert "LEFT JOIN contacts" in detail_sql
    assert detail_params == ("abc-123",)
    assert "abc-123" not in detail_sql  # bound, never interpolated
    act_sql, act_params = selects[1]
    assert "FROM activities" in act_sql
    assert "WHERE deal_id = %s" in act_sql
    assert "ORDER BY occurred_at DESC" in act_sql
    assert act_params == ("abc-123", 7)


@pytest.mark.unit
def test_rows_normalized_json_stable(monkeypatch):
    deal_id = uuid.uuid4()
    company_id = uuid.uuid4()
    created = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=datetime.timezone.utc)
    pool = FakePool(rows=[{
        "id": deal_id, "tenant_id": uuid.uuid4(), "title": "Acme renewal",
        "stage": "proposal", "amount": decimal.Decimal("18500.00"), "currency": "USD",
        "company_id": company_id, "contact_id": None, "created_at": created,
        "company_name": "Acme",
    }])
    _patch_pool(monkeypatch, pool)
    crm = PgCrmClient("postgresql://crm_app@h/db")

    rows = crm.list_deals_board(tenant_id="T1")

    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == str(deal_id)               # uuid -> str
    assert r["amount"] == 18500.0                # Decimal -> float
    assert isinstance(r["amount"], float)
    assert r["created_at"] == "2026-06-01T12:00:00+00:00"  # datetime -> ISO
    assert r["company_id"] == str(company_id)
    assert r["contact_id"] is None
    assert r["company_name"] == "Acme"
    # board rows don't carry the detail-only fields
    assert "contact_name" not in r


@pytest.mark.unit
def test_detail_none_when_rls_yields_no_row_and_carries_contact_fields(monkeypatch):
    pool = FakePool(rows=[])
    _patch_pool(monkeypatch, pool)
    crm = PgCrmClient("postgresql://crm_app@h/db")
    assert crm.get_deal_board(tenant_id="T1", deal_id="x") is None

    pool2 = FakePool(rows=[{
        "id": "d-1", "tenant_id": "T1", "title": "Acme", "stage": "new", "amount": None,
        "currency": "USD", "company_id": None, "contact_id": None, "created_at": None,
        "company_name": None, "contact_name": "Dana", "contact_email": "dana@x.com",
    }])
    _patch_pool(monkeypatch, pool2)
    crm2 = PgCrmClient("postgresql://crm_app@h/db")
    row = crm2.get_deal_board(tenant_id="T1", deal_id="d-1")
    assert row["contact_name"] == "Dana" and row["contact_email"] == "dana@x.com"
    assert row["amount"] is None and row["created_at"] is None
