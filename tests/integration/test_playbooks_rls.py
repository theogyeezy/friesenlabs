"""Integration: RLS isolation on the new `playbooks` table (real Postgres, crm_app role).

The same two-tenant proof as tests/integration/test_rls_isolation.py, for the Agent Studio
table this branch adds: connect as the NON-OWNER crm_app role, write a playbook per tenant,
and prove each tenant can read/update/delete ONLY its own — at the raw-SQL level AND through
PgPlaybookStore (the per-op `SET LOCAL app.current_tenant` transaction).

Runs only when a real Postgres is reachable (UPLIFT_TEST_DB_URL owner URL to load
schema.sql + roles.sql, or UPLIFT_DB_URL as an already-provisioned crm_app URL); otherwise
every test SKIPS with a clear reason — the offline suite stays green.
"""
import json
import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")

OWNER_URL = os.environ.get("UPLIFT_TEST_DB_URL")
APP_URL = os.environ.get("UPLIFT_DB_URL")
HERE = os.path.dirname(__file__)
DB_DIR = os.path.join(HERE, "..", "..", "db")


def _connect(url):
    try:
        return psycopg2.connect(url)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no reachable Postgres ({e.__class__.__name__})")


def _app_dsn() -> str:
    """The crm_app DSN: from the owner URL (after loading schema+roles) or UPLIFT_DB_URL."""
    if OWNER_URL:
        owner = _connect(OWNER_URL)
        owner.autocommit = True
        with owner.cursor() as cur:
            try:
                cur.execute(open(os.path.join(DB_DIR, "schema.sql")).read())
                cur.execute(open(os.path.join(DB_DIR, "roles.sql")).read())
                cur.execute("ALTER ROLE crm_app PASSWORD 'testpw'")
            except Exception as e:  # noqa: BLE001
                pytest.skip(f"cannot load schema (needs pgvector + privileges): {e}")
        owner.close()
        import urllib.parse as up
        parts = up.urlparse(OWNER_URL)
        return up.urlunparse(
            parts._replace(netloc=f"crm_app:testpw@{parts.hostname}:{parts.port or 5432}")
        )
    if APP_URL:
        return APP_URL
    pytest.skip("set UPLIFT_TEST_DB_URL (owner) or UPLIFT_DB_URL (crm_app) to run the RLS proof")


@pytest.fixture(scope="module")
def app_dsn():
    return _app_dsn()


@pytest.fixture(scope="module")
def app_conn(app_dsn):
    conn = _connect(app_dsn)
    yield conn
    conn.rollback()
    conn.close()


def _set_tenant(cur, tenant_id):
    cur.execute("SET app.current_tenant = %s", (str(tenant_id),))


def _definition(name):
    return {
        "name": name,
        "trigger": {"kind": "manual"},
        "roster": [{"agent": "pip"}],
        "autonomy": "L1",
        "greenlight": {"side_effects": "always_ask"},
    }


@pytest.mark.integration
@pytest.mark.isolation
def test_playbooks_row_isolation_raw_sql(app_conn):
    a, b = uuid.uuid4(), uuid.uuid4()
    with app_conn.cursor() as cur:
        _set_tenant(cur, a)
        cur.execute(
            "INSERT INTO playbooks (tenant_id, name, definition) VALUES (%s,%s,%s)",
            (str(a), "a-playbook", json.dumps(_definition("a-playbook"))),
        )
        _set_tenant(cur, b)
        cur.execute(
            "INSERT INTO playbooks (tenant_id, name, definition) VALUES (%s,%s,%s)",
            (str(b), "b-playbook", json.dumps(_definition("b-playbook"))),
        )

        # As tenant A: B's playbook is invisible.
        _set_tenant(cur, a)
        cur.execute("SELECT count(*) FROM playbooks WHERE name = 'b-playbook'")
        assert cur.fetchone()[0] == 0, "RLS leak: tenant A read tenant B's playbook"
        cur.execute("SELECT name FROM playbooks")
        assert {r[0] for r in cur.fetchall()} == {"a-playbook"}

        # As tenant A: cannot UPDATE or DELETE B's playbook.
        cur.execute("UPDATE playbooks SET status = 'active' WHERE name = 'b-playbook'")
        assert cur.rowcount == 0, "RLS leak: tenant A updated tenant B's playbook"
        cur.execute("DELETE FROM playbooks WHERE name = 'b-playbook'")
        assert cur.rowcount == 0, "RLS leak: tenant A deleted tenant B's playbook"

        # WITH CHECK: tenant A cannot INSERT a row stamped with B's tenant_id.
        with pytest.raises(Exception):
            cur.execute(
                "INSERT INTO playbooks (tenant_id, name, definition) VALUES (%s,%s,%s)",
                (str(b), "forged", json.dumps(_definition("forged"))),
            )
        app_conn.rollback()


@pytest.mark.integration
@pytest.mark.isolation
def test_pg_playbook_store_isolation(app_dsn):
    from agents.playbooks.store import PgPlaybookStore

    store = PgPlaybookStore(app_dsn)
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        row = store.create(a, _definition("store-a"), template_id="t", created_by="ua")
        assert row["version"] == 1 and row["status"] == "draft"

        # The full contract through the store: cross-tenant reads/writes behave like absence.
        assert store.get(b, row["id"]) is None
        assert store.list(b) == []
        assert store.update_definition(b, row["id"], _definition("stolen")) is None
        assert store.set_status(b, row["id"], "active") is None
        assert store.delete(b, row["id"]) is False

        # Own-tenant lifecycle works.
        assert store.get(a, row["id"])["name"] == "store-a"
        updated = store.update_definition(a, row["id"], _definition("store-a2"))
        assert updated["version"] == 2 and updated["name"] == "store-a2"
        assert store.set_status(a, row["id"], "active")["status"] == "active"
        assert store.get(a, "not-a-uuid") is None  # malformed id = absent, never an error

        # MA registration persistence (audit P0-3): full ids land on the row, version-pinned;
        # a cross-tenant set_registration behaves like absence.
        reg = store.set_registration(a, row["id"], coordinator_id="coord_full_x",
                                     agent_ids=["ag_1", "ag_2"], version=updated["version"])
        assert reg["ma_coordinator_id"] == "coord_full_x"
        assert reg["ma_agent_ids"] == ["ag_1", "ag_2"]
        assert reg["ma_registered_version"] == updated["version"]
        assert store.set_registration(b, row["id"], coordinator_id="steal",
                                      agent_ids=[], version=1) is None
    finally:
        store.delete(a, row["id"])


@pytest.mark.integration
@pytest.mark.isolation
def test_pg_playbook_run_store_isolation_and_append_only(app_dsn):
    """playbook_runs (audit P0-2): tenant-scoped history through the store, RLS-invisible
    cross-tenant, and APPEND-ONLY for crm_app (the roles.sql REVOKE: no UPDATE, no DELETE)."""
    from agents.playbooks.store import PgPlaybookRunStore

    runs = PgPlaybookRunStore(app_dsn)
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    pid = str(uuid.uuid4())

    row = runs.record(a, {"run_id": "r-1", "playbook_id": pid, "status": "pending",
                          "trigger": {"kind": "manual", "name": "run-now"}, "answer": "x"})
    assert row["status"] == "pending" and row["playbook_id"] == pid
    runs.record(a, {"run_id": "r-2", "playbook_id": pid, "status": "ok", "trigger": {}})

    # Tenant-scoped reads: newest first for A; nothing for B.
    listed = runs.list(a, pid)
    assert [r["run_id"] for r in listed] == ["r-2", "r-1"]
    assert runs.list(b) == [] and runs.list(b, pid) == []

    # Append-only: crm_app physically cannot UPDATE or DELETE history (roles.sql REVOKE).
    # A privilege failure aborts the transaction (rolling back the SET too), so each probe
    # gets its own cursor + fresh tenant binding after rollback.
    conn = _connect(app_dsn)
    try:
        for stmt in ("UPDATE playbook_runs SET status = 'rewritten' WHERE run_id = 'r-1'",
                     "DELETE FROM playbook_runs WHERE run_id = 'r-1'"):
            with conn.cursor() as cur:
                _set_tenant(cur, a)
                with pytest.raises(Exception):
                    cur.execute(stmt)
            conn.rollback()
    finally:
        conn.close()
