"""Unit + integration: the CRM structured sink (ingest/sinks.py PgCrmStructuredSink).

Two layers, same contract from two angles:

  * MOCKED psycopg2 (always runs) — the wiring proof. Every upsert begins with
    SET LOCAL app.current_tenant (RLS pattern, never a session-level SET); a
    re-sync SELECTs the existing row by its natural (tenant_id, namespaced ref_id)
    key and UPDATEs instead of INSERTing (idempotency); a bad row is isolated in a
    SAVEPOINT + reported, never aborting the batch; activities are skipped (no CRM
    ref column); and default_structured_sink() picks the in-memory vs Pg sink off
    the INGEST_REAL_STORES switch.

  * REAL Postgres (skips cleanly without UPLIFT_TEST_DB_URL) — the behavior proof
    against FORCE'd RLS as the non-owner crm_app role: upsert lands rows, a second
    identical sync does NOT duplicate (idempotent), refs resolve to our uuids, and
    one tenant's sink can NEVER see or upsert over another tenant's rows.
"""
import os
import urllib.parse as up
import uuid

import pytest

import psycopg2
import psycopg2.pool

from ingest.connectors.base import NormalizedRecord
from ingest.sinks import PgCrmStructuredSink, _namespaced_ref


# =========================================================================== #
# Mocked-psycopg2 fakes (no DB) — a programmable cursor that answers SELECTs.
# =========================================================================== #
class FakeCursor:
    """Records every (sql, params). `selects` is a FIFO of fetchone() answers
    consumed in order so a test can script "ref not found" then "row inserted"."""

    def __init__(self, log, selects):
        self.log = log
        self._selects = list(selects)
        self._last = None

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.log.append((flat, params))
        self._last = flat.upper()

    def fetchone(self):
        if self._last and self._last.startswith("SELECT"):
            return self._selects.pop(0) if self._selects else None
        return None


class FakeConn:
    def __init__(self, log, selects):
        self.log = log
        self._selects = selects
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return FakeCursor(self.log, self._selects)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def _sink_with(selects):
    """A PgCrmStructuredSink over a conn_factory yielding one scripted FakeConn."""
    log: list = []
    conn = FakeConn(log, selects)
    sink = PgCrmStructuredSink(conn_factory=lambda: conn)
    return sink, conn, log


TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _sqls(log):
    return [s for s, _ in log]


# --------------------------------------------------------------------------- #
# Mocked: SET LOCAL precedes every statement; never a session-level SET.
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_set_local_binds_tenant_and_no_session_set():
    # ref-resolve SELECTs (none, since no parent refs) + the row's existence
    # SELECT (None -> insert path).
    sink, conn, log = _sink_with(selects=[None])
    sink.upsert_rows("companies", [{
        "tenant_id": TENANT_A, "source": "hubspot", "ref_id": "co-1",
        "name": "Acme", "domain": "acme.com",
    }])

    sqls = _sqls(log)
    assert sqls[0].startswith("SET LOCAL app.current_tenant")
    assert log[0][1] == (TENANT_A,)
    # the FIXED RLS pattern, never the cross-tenant-leak session SET
    assert not any(s.startswith("SET app.current_tenant") for s in sqls)
    assert conn.commits == 1 and conn.rollbacks == 0


@pytest.mark.unit
def test_company_insert_namespaces_ref_and_binds_values():
    sink, _conn, log = _sink_with(selects=[None])  # existence SELECT -> not found
    sink.upsert_rows("companies", [{
        "tenant_id": TENANT_A, "source": "hubspot", "ref_id": "co-1",
        "name": "Acme", "domain": "acme.com",
    }])

    insert = next((s, p) for s, p in log if s.startswith("INSERT INTO companies"))
    sql, params = insert
    assert "ref_id" in sql and "tenant_id" in sql
    # natural key is source-namespaced so two sources can't collide on a bare id
    assert _namespaced_ref("hubspot", "co-1") == "hubspot:co-1"
    assert "hubspot:co-1" in params
    assert TENANT_A in params and "Acme" in params
    # values are bound, never interpolated into the SQL text
    assert "Acme" not in sql


# --------------------------------------------------------------------------- #
# Mocked: idempotency — an existing row UPDATEs, never a second INSERT.
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_idempotent_resync_updates_existing_row():
    # existence SELECT returns an id -> UPDATE branch (no INSERT).
    sink, _conn, log = _sink_with(selects=[("existing-uuid",)])
    n = sink.upsert_rows("companies", [{
        "tenant_id": TENANT_A, "source": "hubspot", "ref_id": "co-1",
        "name": "Acme Renamed", "domain": "acme.com",
    }])

    sqls = _sqls(log)
    assert n == 1
    assert any(s.startswith("UPDATE companies SET") for s in sqls)
    assert not any(s.startswith("INSERT INTO companies") for s in sqls)
    upd = next((s, p) for s, p in log if s.startswith("UPDATE companies"))
    assert upd[1][-1] == "existing-uuid"  # WHERE id = the resolved natural-key row
    assert "Acme Renamed" in upd[1]


# --------------------------------------------------------------------------- #
# Mocked: child ref resolution — company_ref_id -> our uuid via a parent lookup.
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_contact_resolves_company_ref_to_uuid():
    # 1st SELECT: resolve company_ref_id against companies -> hit.
    # 2nd SELECT: the contact's own existence check -> not found (insert).
    sink, _conn, log = _sink_with(selects=[("company-uuid",), None])
    sink.upsert_rows("contacts", [{
        "tenant_id": TENANT_A, "source": "hubspot", "ref_id": "ct-1",
        "company_ref_id": "co-1", "name": "Dana", "email": "dana@acme.com",
    }])

    # the resolve probe looked up the NAMESPACED parent ref under RLS (current_setting)
    resolve = next((s, p) for s, p in log
                   if s.startswith("SELECT id FROM companies"))
    assert "current_setting('app.current_tenant')" in resolve[0]
    assert resolve[1] == ("hubspot:co-1",)
    insert = next((s, p) for s, p in log if s.startswith("INSERT INTO contacts"))
    assert "company-uuid" in insert[1]  # resolved uuid, not the source ref


@pytest.mark.unit
def test_unresolved_ref_lands_null_not_error():
    # company probe + contact probe both miss -> company_id NULL; then existence
    # SELECT misses -> insert. No error reported.
    sink, _conn, log = _sink_with(selects=[None, None, None])
    sink.upsert_rows("contacts", [{
        "tenant_id": TENANT_A, "source": "hubspot", "ref_id": "ct-9",
        "company_ref_id": "missing-co", "name": "Orphan",
    }])
    insert = next((s, p) for s, p in log if s.startswith("INSERT INTO contacts"))
    assert None in insert[1]  # company_id bound NULL
    assert sink.last_report.errors == []


# --------------------------------------------------------------------------- #
# Mocked: per-row error isolation + report; activities skip.
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_bad_row_isolated_in_savepoint_and_reported():
    sink, conn, log = _sink_with(selects=[None])
    # a row with no tenant_id at all in a single-row batch -> no tenant resolved.
    n = sink.upsert_rows("companies", [{"source": "hubspot", "ref_id": "co-x"}])
    assert n == 0
    assert sink.last_report.errors and "tenant_id" in sink.last_report.errors[0]["reason"]


@pytest.mark.unit
def test_row_failure_rolls_back_savepoint_keeps_batch_alive():
    class FailingCursor(FakeCursor):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            if " ".join(sql.split()).startswith("INSERT INTO companies"):
                raise RuntimeError("boom")

    log: list = []

    class FailConn(FakeConn):
        def cursor(self):
            return FailingCursor(self.log, self._selects)

    conn = FailConn(log, selects=[None, None])  # two rows, both reach INSERT
    sink = PgCrmStructuredSink(conn_factory=lambda: conn)
    n = sink.upsert_rows("companies", [
        {"tenant_id": TENANT_A, "source": "hs", "ref_id": "a", "name": "A"},
        {"tenant_id": TENANT_A, "source": "hs", "ref_id": "b", "name": "B"},
    ])
    sqls = _sqls(log)
    assert n == 0  # both failed
    assert len(sink.last_report.errors) == 2
    # each failure rolled back ONLY its savepoint; the batch transaction committed.
    assert sqls.count("ROLLBACK TO SAVEPOINT crm_row") == 2
    assert conn.commits == 1  # the surrounding tenant txn still committed


@pytest.mark.unit
def test_activities_are_skipped_not_landed():
    sink, _conn, log = _sink_with(selects=[])
    n = sink.upsert_rows("activities", [{
        "tenant_id": TENANT_A, "source": "stripe", "ref_id": "in_1",
        "kind": "invoice", "body": "Invoice 1",
    }])
    assert n == 0
    assert sink.last_report.skipped and sink.last_report.skipped[0]["ref_id"] == "in_1"
    # nothing was written for an unsupported table
    assert not any(s.startswith(("INSERT", "UPDATE")) for s in _sqls(log))


@pytest.mark.unit
def test_empty_batch_is_a_noop():
    sink, conn, log = _sink_with(selects=[])
    assert sink.upsert_rows("companies", []) == 0
    assert log == [] and conn.commits == 0


# --------------------------------------------------------------------------- #
# Mocked: registry wiring — default_structured_sink() picks off the env switch.
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_default_structured_sink_offline_is_in_memory(monkeypatch):
    from ingest.connectors import default_structured_sink
    from ingest.pipeline import InMemoryStructuredSink

    monkeypatch.delenv("INGEST_REAL_STORES", raising=False)
    assert isinstance(default_structured_sink(), InMemoryStructuredSink)


@pytest.mark.unit
def test_default_structured_sink_real_without_dsn_fails_loud(monkeypatch):
    from ingest.connectors import default_structured_sink

    monkeypatch.setenv("INGEST_REAL_STORES", "1")
    for k in ("UPLIFT_DB_URL", "DB_USER", "DB_PASS", "DB_HOST"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match="no DSN is configured"):
        default_structured_sink()


# =========================================================================== #
# Real Postgres — RLS behavior proof (skips cleanly without an owner DSN).
# =========================================================================== #
OWNER_URL = os.environ.get("UPLIFT_TEST_DB_URL")
HERE = os.path.dirname(__file__)
DB_DIR = os.path.join(HERE, "..", "..", "db")


def _load(cur, fname):
    with open(os.path.join(DB_DIR, fname)) as fh:
        cur.execute(fh.read())


@pytest.fixture(scope="module")
def app_dsn():
    """Load schema+roles as owner, return a crm_app (NON-OWNER) DSN — exactly the
    prod role under which RLS is enforced (FORCE'd)."""
    if not OWNER_URL:
        pytest.skip("set UPLIFT_TEST_DB_URL (owner DSN) to run the CRM sink RLS proof")
    try:
        owner = psycopg2.connect(OWNER_URL)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no reachable Postgres ({e.__class__.__name__})")
    owner.autocommit = True
    with owner.cursor() as cur:
        try:
            _load(cur, "schema.sql")
            _load(cur, "roles.sql")
            cur.execute("ALTER ROLE crm_app PASSWORD 'testpw'")
        except Exception as e:  # noqa: BLE001
            owner.close()
            pytest.skip(f"cannot load schema (needs pgvector + privileges): {e}")
    owner.close()
    parts = up.urlparse(OWNER_URL)
    return up.urlunparse(
        parts._replace(netloc=f"crm_app:testpw@{parts.hostname}:{parts.port or 5432}")
    )


def _company_rec(tenant, ref, name, domain=None):
    return NormalizedRecord(
        tenant_id=tenant, source="hubspot", ref_id=ref, table="companies",
        row={"tenant_id": tenant, "source": "hubspot", "ref_id": ref,
             "name": name, "domain": domain},
        raw={},
    )


def _count(dsn, tenant, table, ref_id):
    """Count rows for a tenant by natural key — through a fresh crm_app conn with
    SET LOCAL so RLS scopes the read exactly like the sink's writes."""
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL app.current_tenant = %s", (tenant,))
            cur.execute(f"SELECT count(*) FROM {table} WHERE ref_id = %s", (ref_id,))
            return cur.fetchone()[0]
    finally:
        conn.rollback()
        conn.close()


@pytest.mark.integration
def test_real_upsert_lands_then_resync_is_idempotent(app_dsn):
    tenant = str(uuid.uuid4())
    sink = PgCrmStructuredSink(app_dsn)

    # first sync: lands one company + one contact referencing it.
    assert sink.upsert_rows("companies", [
        {"tenant_id": tenant, "source": "hubspot", "ref_id": "co-1",
         "name": "Acme", "domain": "acme.com"},
    ]) == 1
    assert sink.upsert_rows("contacts", [
        {"tenant_id": tenant, "source": "hubspot", "ref_id": "ct-1",
         "company_ref_id": "co-1", "name": "Dana", "email": "dana@acme.com"},
    ]) == 1

    assert _count(app_dsn, tenant, "companies", "hubspot:co-1") == 1
    assert _count(app_dsn, tenant, "contacts", "hubspot:ct-1") == 1

    # the contact's company_id resolved to the company's uuid (same tenant).
    conn = psycopg2.connect(app_dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL app.current_tenant = %s", (tenant,))
            cur.execute("SELECT c.company_id, co.id FROM contacts c "
                        "JOIN companies co ON co.id = c.company_id "
                        "WHERE c.ref_id = %s", ("hubspot:ct-1",))
            row = cur.fetchone()
            assert row is not None and row[0] == row[1]
    finally:
        conn.rollback()
        conn.close()

    # RE-SYNC the same objects (renamed) — must UPDATE, never duplicate.
    sink.upsert_rows("companies", [
        {"tenant_id": tenant, "source": "hubspot", "ref_id": "co-1",
         "name": "Acme Inc", "domain": "acme.com"},
    ])
    assert _count(app_dsn, tenant, "companies", "hubspot:co-1") == 1  # still ONE

    conn = psycopg2.connect(app_dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL app.current_tenant = %s", (tenant,))
            cur.execute("SELECT name FROM companies WHERE ref_id = %s", ("hubspot:co-1",))
            assert cur.fetchone()[0] == "Acme Inc"  # the update took
    finally:
        conn.rollback()
        conn.close()


@pytest.mark.integration
def test_real_tenant_isolation(app_dsn):
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    sink = PgCrmStructuredSink(app_dsn)

    sink.upsert_rows("companies", [
        {"tenant_id": tenant_a, "source": "hubspot", "ref_id": "shared-ref",
         "name": "A Co"},
    ])
    sink.upsert_rows("companies", [
        {"tenant_id": tenant_b, "source": "hubspot", "ref_id": "shared-ref",
         "name": "B Co"},
    ])

    # The SAME natural key in two tenants => two distinct rows, each scoped to its
    # tenant. Neither tenant's SET LOCAL view can see the other's row.
    assert _count(app_dsn, tenant_a, "companies", "hubspot:shared-ref") == 1
    assert _count(app_dsn, tenant_b, "companies", "hubspot:shared-ref") == 1

    conn = psycopg2.connect(app_dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL app.current_tenant = %s", (tenant_a,))
            cur.execute("SELECT name FROM companies WHERE ref_id = %s", ("hubspot:shared-ref",))
            rows = cur.fetchall()
            assert rows == [("A Co",)]  # tenant A sees ONLY its own row
    finally:
        conn.rollback()
        conn.close()


@pytest.mark.integration
def test_real_resync_does_not_cross_tenant_overwrite(app_dsn):
    """A re-sync for tenant B with a ref that ALSO exists under tenant A must
    update B's row only — RLS hides A's row from B's existence-check SELECT, so
    the sink inserts/updates strictly within B."""
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    sink = PgCrmStructuredSink(app_dsn)
    sink.upsert_rows("companies", [
        {"tenant_id": tenant_a, "source": "hubspot", "ref_id": "k", "name": "A original"},
    ])
    sink.upsert_rows("companies", [
        {"tenant_id": tenant_b, "source": "hubspot", "ref_id": "k", "name": "B v1"},
    ])
    sink.upsert_rows("companies", [
        {"tenant_id": tenant_b, "source": "hubspot", "ref_id": "k", "name": "B v2"},
    ])

    conn = psycopg2.connect(app_dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL app.current_tenant = %s", (tenant_a,))
            cur.execute("SELECT name FROM companies WHERE ref_id = %s", ("hubspot:k",))
            assert cur.fetchall() == [("A original",)]  # untouched
            cur.execute("SET LOCAL app.current_tenant = %s", (tenant_b,))
            cur.execute("SELECT name FROM companies WHERE ref_id = %s", ("hubspot:k",))
            assert cur.fetchall() == [("B v2",)]  # updated in place, one row
    finally:
        conn.rollback()
        conn.close()
