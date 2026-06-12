"""Integration: onboarding_state RLS isolation + GET/PUT round-trip, and /onboarding/load-sample
idempotency — all against a real Postgres+pgvector, RLS-bound as the non-owner crm_app role.

Runs only when UPLIFT_TEST_DB_URL (an owner/superuser DSN to load schema.sql + roles.sql) is set
AND a Postgres+pgvector is reachable. Otherwise SKIPS cleanly — the repo's standard integration-DB
gate (see tests/integration/test_load_demo_tenant.py / test_rls_isolation.py).

Proves:
  * OnboardingStateStore.get/upsert run as crm_app under SET LOCAL app.current_tenant — RLS scopes
    every read/write, so tenant A never sees tenant B's onboarding row (isolation)
  * GET/PUT round-trips: an upsert persists, merges steps key-by-key, and a fresh read reflects it
  * crm_app is DENIED DELETE on onboarding_state (the row is durable, never erased by the app)
  * /onboarding/load-sample's reuse of scripts/demo/load_demo_tenant.py is idempotent into the
    CALLING tenant: loading twice yields the fixture counts (never doubled), and a second tenant
    sees none of the first tenant's CRM rows
"""
import json
import os
import urllib.parse as up
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from api.onboarding_routes import (  # noqa: E402
    STEP_IDS,
    OnboardingStateStore,
    _load_sample_into_tenant,
)

OWNER_URL = os.environ.get("UPLIFT_TEST_DB_URL")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DB_DIR = os.path.join(ROOT, "db")


def _app_dsn():
    """Load schema+roles as owner, then return a crm_app (non-owner, RLS-bound) DSN. Skip if no DB."""
    if not OWNER_URL:
        pytest.skip("set UPLIFT_TEST_DB_URL (owner DSN) to run the onboarding-state integration test")
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
        parts._replace(netloc=f"crm_app:testpw@{parts.hostname}:{parts.port or 5432}"))


def _store(dsn):
    """An OnboardingStateStore over a per-op conn_factory (NOT a pool): each operation opens +
    closes ONE crm_app connection. Pool mode would open UPLIFT_DB_POOL_MAX (default 10) eager
    connections per construction and never release them across the suite — exhausting the CI
    Postgres's connection slots and breaking sibling DB tests. The factory keeps the footprint at
    one connection at a time, the same shape the app's per-op SET LOCAL transaction needs."""
    return OnboardingStateStore(conn_factory=lambda: psycopg2.connect(dsn))


def _throwaway_fixture(path: str) -> dict:
    """Write a small, SHAPE-VALID demo fixture with FRESHLY-UNIQUE ids to `path`.

    The committed demo fixture uses FIXED global PKs (companies.id etc.), so two tests loading it
    under different tenants on the SHARED CI test DB collide on companies_pkey (the loader's
    wipe is RLS-scoped to the current tenant — it can't remove another tenant's fixed-id rows).
    This throwaway carries uuid4 ids, so it never collides with test_load_demo_tenant while still
    exercising the EXACT loader reuse path (`_load_sample_into_tenant`). Composite same-tenant FKs
    are satisfied because the loader stamps the chosen tenant_id on every row."""
    co, ct, dl = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    data = {
        "meta": {"tenant_id": None, "counts": {}},
        "companies": [{"id": co, "name": "Throwaway Co", "domain": "throwaway.example",
                       "ref_id": "demo:doc:co:tw", "created_at": "2026-06-01T00:00:00+00:00"}],
        "contacts": [{"id": ct, "company_id": co, "name": "Tess Tester",
                      "email": "tess@throwaway.example", "phone": None, "ref_id": "demo:doc:ct:tw",
                      "created_at": "2026-06-01T00:00:00+00:00"}],
        "deals": [{"id": dl, "company_id": co, "contact_id": ct, "title": "Throwaway deal",
                   "stage": "new", "amount": 1000, "currency": "USD", "ref_id": "demo:doc:dl:tw",
                   "created_at": "2026-06-01T00:00:00+00:00"}],
        "activities": [{"id": str(uuid.uuid4()), "contact_id": ct, "deal_id": dl, "kind": "note",
                        "body": "seed", "occurred_at": "2026-06-02T00:00:00+00:00"}],
        "approvals": [],
        "saved_views": [],
        "documents": [{"source": "demo", "ref_id": "demo:doc:tw1", "content": "throwaway corpus",
                       "created_at": "2026-06-01T00:00:00+00:00"}],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


def _crm_counts(dsn, tenant):
    """CRM row counts for one tenant, RLS-scoped on a fresh crm_app connection."""
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SET app.current_tenant = %s", (tenant,))
            out = {}
            for table in ("companies", "contacts", "deals"):
                cur.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 — fixed table list
                out[table] = cur.fetchone()[0]
        return out
    finally:
        conn.close()


@pytest.mark.integration
def test_onboarding_state_round_trip_and_isolation():
    dsn = _app_dsn()
    store = _store(dsn)
    a, b = str(uuid.uuid4()), str(uuid.uuid4())

    # Fresh tenant: get() returns the honest default (no row yet).
    fresh = store.get(a)
    assert fresh["steps"] == {sid: False for sid in STEP_IDS}
    assert fresh["dismissed"] is False and fresh["sample_loaded"] is False

    # Upsert one step done for A; merge in a second step; flip dismissed.
    store.upsert(a, steps={"try_chat": True})
    store.upsert(a, steps={"invite_team": True}, dismissed=True)
    got_a = store.get(a)
    assert got_a["steps"]["try_chat"] is True
    assert got_a["steps"]["invite_team"] is True
    assert got_a["steps"]["load_data"] is False  # merge never cleared the others
    assert got_a["dismissed"] is True

    # Isolation: B was never touched — it sees ONLY the fresh default, never A's row.
    got_b = store.get(b)
    assert got_b["steps"] == {sid: False for sid in STEP_IDS}
    assert got_b["dismissed"] is False


@pytest.mark.integration
def test_crm_app_cannot_delete_onboarding_state():
    """The row is durable per-tenant state — crm_app holds no DELETE (db/roles.sql / REQ-010)."""
    dsn = _app_dsn()
    tenant = str(uuid.uuid4())
    _store(dsn).upsert(tenant, steps={"load_data": True})
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SET app.current_tenant = %s", (tenant,))
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute("DELETE FROM onboarding_state")
        conn.rollback()
    finally:
        conn.close()


@pytest.mark.integration
def test_load_sample_is_idempotent_into_the_calling_tenant(tmp_path):
    dsn = _app_dsn()
    store = _store(dsn)
    tenant, other = str(uuid.uuid4()), str(uuid.uuid4())
    fixture = str(tmp_path / "throwaway_fixture.json")
    _throwaway_fixture(fixture)

    # Load the fixture into `tenant` TWICE via the route's exact reuse path — must not double.
    counts1 = _load_sample_into_tenant(store, tenant, fixture_path=fixture)
    counts2 = _load_sample_into_tenant(store, tenant, fixture_path=fixture)
    assert counts1["companies"] == counts2["companies"] == 1
    assert counts2["contacts"] == 1 and counts2["deals"] == 1

    got = _crm_counts(dsn, tenant)
    assert got["companies"] == 1, "fixture loaded once, not duplicated on re-run"
    assert got["contacts"] == 1 and got["deals"] == 1

    # Tenant isolation: a second tenant sees none of the first tenant's seeded CRM rows.
    empty = _crm_counts(dsn, other)
    assert all(v == 0 for v in empty.values())
