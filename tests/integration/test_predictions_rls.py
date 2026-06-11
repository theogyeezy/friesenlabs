"""Integration: the Cortex `predictions` table is genuinely tenant-isolated under FORCE'd RLS,
exercised through the REAL store (ml.predictions.PgPredictionLog) as the non-owner crm_app role.

Two tenants log predictions; each sees only its own resolved pairs, cross-tenant outcome
backfill resolves nothing, and a raw cross-tenant SELECT comes back empty. Same DB/skip
conventions as test_rls_isolation.py (real Postgres in CI; clean skip locally without one).
"""
import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from ml.predictions import PgPredictionLog  # noqa: E402

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
def app_dsn():
    if not OWNER_URL and not APP_URL:
        pytest.skip("set UPLIFT_TEST_DB_URL (owner) or UPLIFT_DB_URL (crm_app) to run")
    if not OWNER_URL:
        return APP_URL
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
        parts._replace(netloc=f"crm_app:testpw@{parts.hostname}:{parts.port or 5432}"))


@pytest.mark.integration
@pytest.mark.isolation
def test_prediction_log_is_tenant_isolated_under_rls(app_dsn):
    log = PgPredictionLog(conn_factory=lambda: psycopg2.connect(app_dsn))
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    deal_a, deal_b = str(uuid.uuid4()), str(uuid.uuid4())

    log.log(tenant_a, deal_id=deal_a, model_version=1, score=0.91, features={"amount": 1})
    log.log(tenant_b, deal_id=deal_b, model_version=1, score=0.12)

    # Cross-tenant outcome backfill resolves NOTHING (RLS hides A's row from B's txn).
    assert log.record_outcome(tenant_b, deal_a, 0) == 0
    # Same-tenant backfill works.
    assert log.record_outcome(tenant_a, deal_a, 1) == 1
    assert log.record_outcome(tenant_b, deal_b, 0) == 1

    # Each tenant reads only its own resolved pairs.
    assert log.scored_outcomes(tenant_a) == [(0.91, 1)]
    assert log.scored_outcomes(tenant_b) == [(0.12, 0)]

    # Raw probe: as tenant B, tenant A's prediction row does not exist.
    conn = psycopg2.connect(app_dsn)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SET LOCAL app.current_tenant = %s", (tenant_b,))
            cur.execute("SELECT count(*) FROM predictions WHERE deal_id = %s", (deal_a,))
            assert cur.fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.integration
def test_prediction_insert_cannot_spoof_another_tenant(app_dsn):
    """WITH CHECK: an INSERT whose tenant_id differs from the txn GUC is rejected outright."""
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    conn = psycopg2.connect(app_dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SET app.current_tenant = %s", (tenant_a,))
            with pytest.raises(psycopg2.Error) as exc:
                cur.execute(
                    "INSERT INTO predictions (tenant_id, deal_id, model_version, score) "
                    "VALUES (%s, %s, 1, 0.5)",
                    (tenant_b, str(uuid.uuid4())),
                )
            assert "row-level security" in str(exc.value)
        conn.rollback()
    finally:
        conn.close()
