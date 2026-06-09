"""Integration: the two-tenant RLS isolation proof (Build Guide Phase 1 "done when").

Connect as the non-owner crm_app role, insert rows for two tenants, and prove each tenant sees
ONLY its own — for both row queries and a vector similarity (ANN) query. This is the isolation
proof the Build Guide says to keep as an automated check.

Runs against a real Postgres+pgvector only when one is reachable:
  - UPLIFT_TEST_DB_URL  -> a superuser/owner URL used to load schema.sql + roles.sql, OR
  - UPLIFT_DB_URL       -> an already-provisioned crm_app URL (skip the load step)
If neither is set / reachable / has pgvector, the test SKIPS with a clear reason (no DB locally).
"""
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


@pytest.fixture(scope="module")
def app_conn():
    if not OWNER_URL and not APP_URL:
        pytest.skip("set UPLIFT_TEST_DB_URL (owner) or UPLIFT_DB_URL (crm_app) to run the RLS proof")

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
        # Build a crm_app DSN from the owner DSN.
        import urllib.parse as up
        parts = up.urlparse(OWNER_URL)
        app_dsn = up.urlunparse(parts._replace(netloc=f"crm_app:testpw@{parts.hostname}:{parts.port or 5432}"))
        conn = _connect(app_dsn)
    else:
        conn = _connect(APP_URL)
    yield conn
    conn.rollback()
    conn.close()


def _set_tenant(cur, tenant_id):
    cur.execute("SET app.current_tenant = %s", (str(tenant_id),))


@pytest.mark.integration
def test_row_and_vector_isolation(app_conn):
    a, b = uuid.uuid4(), uuid.uuid4()
    vec = "[" + ",".join(["0.1"] * 1024) + "]"
    with app_conn.cursor() as cur:
        # Insert one doc per tenant.
        _set_tenant(cur, a)
        cur.execute(
            "INSERT INTO documents (tenant_id, source, content, embedding) VALUES (%s,'test','a-secret',%s)",
            (str(a), vec),
        )
        _set_tenant(cur, b)
        cur.execute(
            "INSERT INTO documents (tenant_id, source, content, embedding) VALUES (%s,'test','b-secret',%s)",
            (str(b), vec),
        )

        # As tenant A: row query must not see B.
        _set_tenant(cur, a)
        cur.execute("SELECT count(*) FROM documents WHERE content = 'b-secret'")
        assert cur.fetchone()[0] == 0, "RLS leak: tenant A read tenant B rows"

        # As tenant A: vector ANN query must only ever return A's rows.
        cur.execute("SET hnsw.iterative_scan = 'relaxed_order'")
        cur.execute(
            "SELECT content FROM documents ORDER BY embedding <=> %s::vector LIMIT 50", (vec,)
        )
        contents = {r[0] for r in cur.fetchall()}
        assert "b-secret" not in contents, "RLS leak: vector query returned another tenant's row"
        assert contents <= {"a-secret"}

        # As tenant A: cannot UPDATE B's rows.
        cur.execute("UPDATE documents SET content = 'hacked' WHERE content = 'b-secret'")
        assert cur.rowcount == 0, "RLS leak: tenant A updated tenant B rows"

        app_conn.rollback()
