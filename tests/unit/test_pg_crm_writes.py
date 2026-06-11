"""Unit: PgCrmClient CRM writes are tenant-scoped and allow-listed.

Mocked psycopg2, no DB. These are the write-side mirror of the read tests: every mutator starts
with SET LOCAL app.current_tenant, uses bind params for values, never hand-writes a tenant WHERE
filter, and rejects non-allow-listed fields before any SQL is issued.
"""
import decimal

import pytest

import psycopg2
import psycopg2.pool

from api.pg_clients import PgCrmClient


class FakeCursor:
    def __init__(self, log, one=None, description=None):
        self.log = log
        self._one = one
        self.description = description

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self._one

    def fetchall(self):
        return []


class FakeConn:
    def __init__(self, log, one=None, description=None):
        self.log = log
        self._one = one
        self._description = description
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, cursor_factory=None):
        return FakeCursor(self.log, self._one, self._description)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakePool:
    def __init__(self, one=None, description=None):
        self.log: list = []
        self._conn = FakeConn(self.log, one, description)
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
def test_update_deal_fields_set_local_bind_params_and_name_maps_to_title(monkeypatch):
    pool = FakePool(one={
        "id": "d-1",
        "title": "New name",
        "stage": "closed_won",
        "amount": decimal.Decimal("1200.00"),
        "company_id": "c-1",
        "created_at": None,
    })
    _patch_pool(monkeypatch, pool)
    crm = PgCrmClient("postgresql://crm_app@h/db")

    out = crm.update_deal_fields(
        tenant_id="T1",
        deal_id="d-1",
        changes={"stage": "closed_won", "amount": 1200, "name": "New name"},
    )

    sql = _sql(pool)
    assert sql[0].startswith("SET LOCAL app.current_tenant")
    assert pool.log[0][1] == ("T1",)
    update_sql, params = pool.log[1]
    assert update_sql.startswith("UPDATE deals SET stage = %s, amount = %s, title = %s")
    assert "WHERE id = %s" in update_sql
    assert "tenant_id =" not in update_sql
    assert params == ("closed_won", 1200, "New name", "d-1")
    assert "closed_won" not in update_sql and "New name" not in update_sql
    assert out["updated"] == {"stage": "closed_won", "amount": 1200, "name": "New name"}
    assert out["deal"]["amount"] == 1200.0


@pytest.mark.unit
def test_update_contact_fields_skips_title_and_binds_allowed_columns(monkeypatch):
    pool = FakePool(one={
        "id": "ct-1",
        "name": "Dana",
        "email": "dana@example.com",
        "phone": "+15550100",
    })
    _patch_pool(monkeypatch, pool)
    crm = PgCrmClient("postgresql://crm_app@h/db")

    out = crm.update_contact_fields(
        tenant_id="T1",
        contact_id="ct-1",
        changes={"name": "Dana", "title": "VP", "phone": "+15550100"},
    )

    sql = _sql(pool)
    assert sql[0].startswith("SET LOCAL app.current_tenant")
    update_sql, params = pool.log[1]
    assert update_sql.startswith("UPDATE contacts SET name = %s, phone = %s")
    assert "title" not in update_sql
    assert "tenant_id =" not in update_sql
    assert params == ("Dana", "+15550100", "ct-1")
    assert out["updated"] == {"name": "Dana", "phone": "+15550100"}
    assert out["skipped"] == {"title": "contacts.title is not in the schema"}


@pytest.mark.unit
def test_insert_activity_set_local_and_bind_params(monkeypatch):
    pool = FakePool(one={
        "id": "act-1",
        "contact_id": None,
        "deal_id": "d-1",
        "kind": "note",
        "body": "followed up",
        "occurred_at": None,
    })
    _patch_pool(monkeypatch, pool)
    crm = PgCrmClient("postgresql://crm_app@h/db")

    out = crm.insert_activity(
        tenant_id="T1", deal_id="d-1", contact_id=None, kind="note", body="followed up"
    )

    sql = _sql(pool)
    assert sql[0].startswith("SET LOCAL app.current_tenant")
    insert_sql, params = pool.log[1]
    assert insert_sql.startswith("INSERT INTO activities")
    assert "VALUES (%s,%s,%s,%s,%s)" in insert_sql
    assert "tenant_id =" not in insert_sql
    assert params == ("T1", None, "d-1", "note", "followed up")
    assert "followed up" not in insert_sql
    assert out["deal_id"] == "d-1"


@pytest.mark.unit
def test_insert_deal_set_local_and_bind_params(monkeypatch):
    pool = FakePool(one={
        "id": "d-1",
        "title": "Expansion",
        "stage": "new",
        "amount": decimal.Decimal("9900.00"),
        "company_id": "c-1",
        "created_at": None,
    })
    _patch_pool(monkeypatch, pool)
    crm = PgCrmClient("postgresql://crm_app@h/db")

    out = crm.insert_deal(
        tenant_id="T1", company_id="c-1", name="Expansion", stage="new", amount=9900
    )

    sql = _sql(pool)
    assert sql[0].startswith("SET LOCAL app.current_tenant")
    insert_sql, params = pool.log[1]
    assert insert_sql.startswith("INSERT INTO deals")
    # 6 columns now (tenant_id, company_id, contact_id, title, stage, amount) — contact_id
    # was added so a deal can carry its contact link (was silently dropped before).
    assert "VALUES (%s,%s,%s,%s,%s,%s)" in insert_sql
    assert "tenant_id =" not in insert_sql
    # contact_id is None when not supplied; company_id "c-1" is preserved.
    assert params == ("T1", "c-1", None, "Expansion", "new", 9900)
    assert "Expansion" not in insert_sql
    assert out["name"] == "Expansion" and out["amount"] == 9900.0


@pytest.mark.unit
def test_insert_deal_binds_contact_id_when_given(monkeypatch):
    """A supplied contact_id rides through to the bind params (the link is persisted)."""
    pool = FakePool(one={
        "id": "d-2", "title": "Linked", "stage": "new",
        "amount": decimal.Decimal("100.00"), "company_id": None,
        "contact_id": "ct-9", "created_at": None,
    })
    _patch_pool(monkeypatch, pool)
    crm = PgCrmClient("postgresql://crm_app@h/db")

    out = crm.insert_deal(
        tenant_id="T1", company_id="", name="Linked", stage="new", amount=100,
        contact_id="ct-9",
    )
    _, params = pool.log[1]
    # blank company_id normalizes to None; contact_id is bound in position 3.
    assert params == ("T1", None, "ct-9", "Linked", "new", 100)
    assert out["contact_id"] == "ct-9"


@pytest.mark.unit
def test_forbidden_write_fields_rejected_before_any_sql(monkeypatch):
    pool = FakePool()
    _patch_pool(monkeypatch, pool)
    crm = PgCrmClient("postgresql://crm_app@h/db")

    with pytest.raises(ValueError, match="not allow-listed"):
        crm.update_deal_fields(
            tenant_id="T1", deal_id="d-1", changes={"stage": "won", "owner_id": "evil"}
        )
    with pytest.raises(ValueError, match="not allow-listed"):
        crm.update_contact_fields(
            tenant_id="T1", contact_id="ct-1", changes={"email": "x@y.com", "tenant_id": "T2"}
        )

    assert pool.log == []
