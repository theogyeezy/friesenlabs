"""Integration: usage_counters + cost_events are genuinely tenant-isolated under FORCE'd RLS,
exercised through the REAL stores (api.usage.PgUsageStore / PgCostRecorder) as the non-owner
crm_app role.

Two tenants bump counters + log cost events; each reads only its own, a cross-tenant raw SELECT
comes back empty, and a spoofed cross-tenant INSERT is rejected by the WITH CHECK policy. Same
DB/skip conventions as test_predictions_rls.py (real Postgres in CI; clean skip locally).
"""
import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from api.usage import PgCostRecorder, PgUsageStore  # noqa: E402

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
def test_usage_counter_is_tenant_isolated_under_rls(app_dsn):
    store = PgUsageStore(conn_factory=lambda: psycopg2.connect(app_dsn))
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())

    assert store.bump(tenant_a, "messages", amount=3) == 3
    assert store.bump(tenant_a, "agent_actions", amount=2) == 5   # running total across metrics
    assert store.bump(tenant_b, "messages", amount=1) == 1        # B's own bucket

    assert store.current(tenant_a)["total"] == 5
    assert store.current(tenant_a)["by_metric"] == {"messages": 3, "agent_actions": 2}
    assert store.current(tenant_b)["total"] == 1

    # Raw probe: as tenant B, tenant A's counters do not exist.
    conn = psycopg2.connect(app_dsn)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SET LOCAL app.current_tenant = %s", (tenant_b,))
            cur.execute("SELECT count(*) FROM usage_counters")
            assert cur.fetchone()[0] == 1   # only B's single row is visible
    finally:
        conn.close()


@pytest.mark.integration
@pytest.mark.isolation
def test_cost_events_are_tenant_isolated_under_rls(app_dsn):
    rec = PgCostRecorder(conn_factory=lambda: psycopg2.connect(app_dsn))
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())

    rec.record(tenant_a, model="claude-haiku-4", in_tok=1_000_000, out_tok=0)   # $1.00
    rec.record(tenant_a, model="claude-haiku-4", in_tok=0, out_tok=1_000_000)   # $5.00
    rec.record(tenant_b, model="claude-opus-4", in_tok=1_000_000, out_tok=0)    # $5.00

    sa = rec.summary(tenant_a)
    assert sa["events"] == 2
    assert sa["in_tok"] == 1_000_000 and sa["out_tok"] == 1_000_000
    assert sa["est_cost"] == 6.0
    # tenant B sees only its own event — A's cost never sums into B.
    sb = rec.summary(tenant_b)
    assert sb["events"] == 1 and sb["est_cost"] == 5.0


@pytest.mark.integration
def test_usage_insert_cannot_spoof_another_tenant(app_dsn):
    """WITH CHECK: an INSERT whose tenant_id differs from the txn GUC is rejected outright."""
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    conn = psycopg2.connect(app_dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SET app.current_tenant = %s", (tenant_a,))
            with pytest.raises(psycopg2.Error) as exc:
                cur.execute(
                    "INSERT INTO usage_counters (tenant_id, period, metric, count) "
                    "VALUES (%s, '2026-06', 'messages', 1)",
                    (tenant_b,),
                )
            assert "row-level security" in str(exc.value)
        conn.rollback()
    finally:
        conn.close()


@pytest.mark.integration
def test_cost_event_insert_cannot_spoof_another_tenant(app_dsn):
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    conn = psycopg2.connect(app_dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SET app.current_tenant = %s", (tenant_a,))
            with pytest.raises(psycopg2.Error) as exc:
                cur.execute(
                    "INSERT INTO cost_events (tenant_id, model, in_tok, out_tok, est_cost) "
                    "VALUES (%s, 'x', 1, 1, 0.1)",
                    (tenant_b,),
                )
            assert "row-level security" in str(exc.value)
        conn.rollback()
    finally:
        conn.close()
