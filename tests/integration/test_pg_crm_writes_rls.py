"""Integration: PgCrmClient writes obey Postgres RLS.

Runs only when UPLIFT_TEST_DB_URL is set to an owner/superuser DSN that can load schema.sql and
roles.sql. The existing scripts stay untouched; this test proves the new write methods cannot update
another tenant's rows and that inserts are visible only to the bound tenant.
"""
import os
import urllib.parse as up
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from api.pg_clients import PgCrmClient  # noqa: E402

OWNER_URL = os.environ.get("UPLIFT_TEST_DB_URL")
HERE = os.path.dirname(__file__)
DB_DIR = os.path.join(HERE, "..", "..", "db")


def _app_dsn():
    if not OWNER_URL:
        pytest.skip("set UPLIFT_TEST_DB_URL (owner DSN) to run the CRM write RLS proof")
    try:
        owner = psycopg2.connect(OWNER_URL)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no reachable Postgres ({e.__class__.__name__})")
    owner.autocommit = True
    with owner.cursor() as cur:
        try:
            cur.execute(open(os.path.join(DB_DIR, "schema.sql")).read())
            cur.execute(open(os.path.join(DB_DIR, "roles.sql")).read())
            cur.execute("ALTER ROLE crm_app PASSWORD 'testpw'")
        except Exception as e:  # noqa: BLE001
            owner.close()
            pytest.skip(f"cannot load schema (needs pgvector + privileges): {e}")
    owner.close()
    parts = up.urlparse(OWNER_URL)
    return up.urlunparse(
        parts._replace(netloc=f"crm_app:testpw@{parts.hostname}:{parts.port or 5432}")
    )


def _set_tenant(cur, tenant_id):
    cur.execute("SET app.current_tenant = %s", (str(tenant_id),))


@pytest.mark.integration
def test_crm_writes_are_rls_scoped():
    dsn = _app_dsn()
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    crm = PgCrmClient(dsn)

    setup = psycopg2.connect(dsn)
    try:
        with setup.cursor() as cur:
            _set_tenant(cur, tenant_a)
            cur.execute(
                "INSERT INTO companies (tenant_id, name) VALUES (%s, %s) RETURNING id",
                (tenant_a, "Tenant A Co"),
            )
            company_a = str(cur.fetchone()[0])
            _set_tenant(cur, tenant_b)
            cur.execute(
                "INSERT INTO companies (tenant_id, name) VALUES (%s, %s) RETURNING id",
                (tenant_b, "Tenant B Co"),
            )
            company_b = str(cur.fetchone()[0])
            cur.execute(
                "INSERT INTO deals (tenant_id, company_id, title, stage, amount) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (tenant_b, company_b, "B secret", "proposal", 500),
            )
            deal_b = str(cur.fetchone()[0])
        setup.commit()
    finally:
        setup.close()

    deal_a = crm.insert_deal(
        tenant_id=tenant_a,
        company_id=company_a,
        name="A expansion",
        stage="new",
        amount=100,
    )
    crm.update_deal_fields(
        tenant_id=tenant_a,
        deal_id=deal_a["id"],
        changes={"stage": "closed_won", "amount": 125},
    )
    activity = crm.insert_activity(
        tenant_id=tenant_a,
        deal_id=deal_a["id"],
        kind="note",
        body="approved update applied",
    )

    with pytest.raises(ValueError, match="not found|not visible"):
        crm.update_deal_fields(
            tenant_id=tenant_a, deal_id=deal_b, changes={"stage": "hacked"}
        )

    check = psycopg2.connect(dsn)
    try:
        with check.cursor() as cur:
            _set_tenant(cur, tenant_a)
            cur.execute("SELECT stage, amount FROM deals WHERE id = %s", (deal_a["id"],))
            assert cur.fetchone() == ("closed_won", pytest.approx(125))
            cur.execute("SELECT count(*) FROM activities WHERE id = %s", (activity["id"],))
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT count(*) FROM deals WHERE id = %s", (deal_b,))
            assert cur.fetchone()[0] == 0

            _set_tenant(cur, tenant_b)
            cur.execute("SELECT stage FROM deals WHERE id = %s", (deal_b,))
            assert cur.fetchone()[0] == "proposal"
            cur.execute("SELECT count(*) FROM activities WHERE id = %s", (activity["id"],))
            assert cur.fetchone()[0] == 0
    finally:
        check.rollback()
        check.close()
