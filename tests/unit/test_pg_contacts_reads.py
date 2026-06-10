"""Unit: the contacts-directory reads on PgCrmClient (api/pg_clients.py, mocked psycopg2 — no DB).

Security-critical guarantees, same bar as test_pg_deals_reads.py:
  * SET LOCAL app.current_tenant precedes EVERY directory SELECT (per-op txn; never session SET)
  * no hand-written tenant filter anywhere (tenancy is RLS-only — joins AND count/last-activity
    subqueries included)
  * search terms / ids / limit / offset are bind params, never interpolated
  * ILIKE metacharacters in a search term (%, _, \\) are ESCAPED in the bound pattern, with an
    explicit ESCAPE clause in the SQL — an injection probe can never become a wildcard scan
  * open-deal predicates carry only the hand-written closed-stage literals
  * rows are normalized JSON-stable (uuid -> str, datetime -> ISO, counts -> int, title null)
"""
import datetime
import uuid

import pytest

import psycopg2
import psycopg2.pool

from api.pg_clients import MAX_LIMIT, PgCrmClient, _escape_ilike


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
def test_directory_reads_set_local_precedes_every_select_no_tenant_filter(monkeypatch):
    pool = FakePool(rows=[])
    _patch_pool(monkeypatch, pool)
    crm = PgCrmClient("postgresql://crm_app@h/db")

    crm.list_contacts_directory(tenant_id="T1")
    crm.get_contact_directory(tenant_id="T2", contact_id="c-1")
    crm.list_contact_activities(tenant_id="T3", contact_id="c-1")
    crm.list_company_open_deals(tenant_id="T4", company_id="co-1")
    crm.list_companies_directory(tenant_id="T5")
    crm.get_company_directory(tenant_id="T6", company_id="co-1")
    crm.list_company_contacts(tenant_id="T7", company_id="co-1")

    sql = _sql(pool)
    selects = [i for i, s in enumerate(sql) if s.startswith("SELECT")]
    assert len(selects) == 7
    for i in selects:  # every SELECT is immediately preceded by its op's tenant bind
        assert sql[i - 1].startswith("SET LOCAL app.current_tenant")
    # binds carry the caller's tenant (THE TRUST RULE: passed in, not ambient)
    assert [pool.log[i - 1][1] for i in selects] == [
        ("T1",), ("T2",), ("T3",), ("T4",), ("T5",), ("T6",), ("T7",)]
    # tenancy is RLS-only: no hand-written tenant filter in any SELECT (joins + subqueries)
    assert not any("tenant_id =" in s for s in sql if s.startswith("SELECT"))
    assert not any(s.startswith("SET app.current_tenant") for s in sql)


@pytest.mark.unit
def test_contacts_list_joins_company_and_last_activity_binds_paging(monkeypatch):
    pool = FakePool(rows=[])
    _patch_pool(monkeypatch, pool)
    crm = PgCrmClient("postgresql://crm_app@h/db")

    crm.list_contacts_directory(tenant_id="T1", limit=10**9, offset=-3)

    select_sql, params = next((s, p) for s, p in pool.log if s.startswith("SELECT"))
    assert "LEFT JOIN companies co ON co.id = c.company_id" in select_sql
    assert "co.name AS company_name" in select_sql
    assert "max(a.occurred_at)" in select_sql and "AS last_activity_at" in select_sql
    assert "ORDER BY c.created_at DESC" in select_sql
    # runaway limit clamped, junk offset floored — both bound, never interpolated
    assert params == (MAX_LIMIT, 0)
    assert "ILIKE" not in select_sql  # no q -> no search clause at all


@pytest.mark.unit
def test_search_terms_are_escaped_bound_ilike_patterns_with_escape_clause(monkeypatch):
    pool = FakePool(rows=[])
    _patch_pool(monkeypatch, pool)
    crm = PgCrmClient("postgresql://crm_app@h/db")

    probe = "%_\\evil"  # ILIKE-injection probe: wildcards + the escape char itself
    crm.list_contacts_directory(tenant_id="T1", q=probe, limit=10, offset=2)
    crm.list_companies_directory(tenant_id="T1", q=probe, limit=10, offset=2)

    for select_sql, params in [(s, p) for s, p in pool.log if s.startswith("SELECT")]:
        assert "ILIKE %s ESCAPE '\\'" in select_sql
        # the probe never appears in the SQL text — it travels ONLY as bind params
        assert "evil" not in select_sql
        pat = params[0]
        assert pat == "%" + "\\%" + "\\_" + "\\\\" + "evil" + "%"
        assert params[:2] == (pat, pat)      # name + email/domain share the one pattern
        assert params[2:] == (10, 2)         # limit/offset bound after the patterns
    # contacts search filters name/email; companies search filters name/domain
    contact_sql = [s for s, _ in pool.log if "FROM contacts c" in s][0]
    assert "c.name ILIKE" in contact_sql and "c.email ILIKE" in contact_sql
    company_sql = [s for s, _ in pool.log if "FROM companies co WHERE" in s][0]
    assert "co.name ILIKE" in company_sql and "co.domain ILIKE" in company_sql


@pytest.mark.unit
def test_escape_ilike_escapes_all_metacharacters():
    assert _escape_ilike("plain") == "%plain%"
    assert _escape_ilike("100%") == "%100\\%%"
    assert _escape_ilike("a_b") == "%a\\_b%"
    assert _escape_ilike("back\\slash") == "%back\\\\slash%"
    assert _escape_ilike("%_") == "%\\%\\_%"


@pytest.mark.unit
def test_detail_activities_and_company_reads_bind_ids(monkeypatch):
    pool = FakePool(rows=[])
    _patch_pool(monkeypatch, pool)
    crm = PgCrmClient("postgresql://crm_app@h/db")

    crm.get_contact_directory(tenant_id="T1", contact_id="abc-123")
    crm.list_contact_activities(tenant_id="T1", contact_id="abc-123", limit=7)
    crm.list_company_open_deals(tenant_id="T1", company_id="co-9")
    crm.get_company_directory(tenant_id="T1", company_id="co-9")
    crm.list_company_contacts(tenant_id="T1", company_id="co-9", limit=4)

    selects = [(s, p) for s, p in pool.log if s.startswith("SELECT")]
    detail_sql, detail_params = selects[0]
    assert "WHERE c.id = %s" in detail_sql and detail_params == ("abc-123",)
    assert "abc-123" not in detail_sql  # bound, never interpolated
    act_sql, act_params = selects[1]
    assert "WHERE contact_id = %s" in act_sql
    assert "ORDER BY occurred_at DESC" in act_sql
    assert act_params == ("abc-123", 7)
    deals_sql, deals_params = selects[2]
    assert "WHERE d.company_id = %s" in deals_sql
    # open = the hand-written closed-stage literals, nothing from input
    assert "d.stage NOT IN ('closed_won', 'closed_lost')" in deals_sql
    assert deals_params[0] == "co-9"
    company_sql, company_params = selects[3]
    assert "WHERE co.id = %s" in company_sql and company_params == ("co-9",)
    assert "AS contact_count" in company_sql and "AS open_deal_count" in company_sql
    assert "NOT IN ('closed_won', 'closed_lost')" in company_sql
    cc_sql, cc_params = selects[4]
    assert "WHERE c.company_id = %s" in cc_sql and cc_params == ("co-9", 4)


@pytest.mark.unit
def test_contact_rows_normalized_json_stable_title_null(monkeypatch):
    contact_id = uuid.uuid4()
    company_id = uuid.uuid4()
    created = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=datetime.timezone.utc)
    last = datetime.datetime(2026, 6, 7, 9, 30, tzinfo=datetime.timezone.utc)
    pool = FakePool(rows=[{
        "id": contact_id, "tenant_id": uuid.uuid4(), "name": "Dana Whitfield",
        "email": "dana@x.com", "phone": "+1 512 555 0150", "company_id": company_id,
        "created_at": created, "company_name": "Birchwood", "last_activity_at": last,
    }])
    _patch_pool(monkeypatch, pool)
    crm = PgCrmClient("postgresql://crm_app@h/db")

    rows = crm.list_contacts_directory(tenant_id="T1")

    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == str(contact_id)                          # uuid -> str
    assert r["company_id"] == str(company_id)
    assert r["created_at"] == "2026-06-01T12:00:00+00:00"      # datetime -> ISO
    assert r["last_activity_at"] == "2026-06-07T09:30:00+00:00"
    assert r["title"] is None  # the schema carries no title column yet — never invented
    assert r["company_name"] == "Birchwood"


@pytest.mark.unit
def test_company_rows_normalized_counts_int_and_none_when_missing(monkeypatch):
    company_id = uuid.uuid4()
    pool = FakePool(rows=[{
        "id": company_id, "tenant_id": uuid.uuid4(), "name": "Birchwood",
        "domain": "birchwood.example", "created_at": None,
        "contact_count": 3, "open_deal_count": None,
    }])
    _patch_pool(monkeypatch, pool)
    crm = PgCrmClient("postgresql://crm_app@h/db")

    rows = crm.list_companies_directory(tenant_id="T1")
    r = rows[0]
    assert r["id"] == str(company_id)
    assert r["contact_count"] == 3 and isinstance(r["contact_count"], int)
    assert r["open_deal_count"] == 0   # a NULL count surfaces as an honest 0
    assert r["created_at"] is None

    # get_* returns None when RLS yields no row (missing OR another tenant's)
    pool2 = FakePool(rows=[])
    _patch_pool(monkeypatch, pool2)
    crm2 = PgCrmClient("postgresql://crm_app@h/db")
    assert crm2.get_company_directory(tenant_id="T1", company_id="x") is None
    assert crm2.get_contact_directory(tenant_id="T1", contact_id="x") is None
