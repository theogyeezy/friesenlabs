"""Integration: two tenants get DISJOINT rows through the Cube HTTP API (the #177 fix proof).

Issue #177: Cube connects as the non-owner `crm_app` role under FORCE'd RLS but never set
`app.current_tenant`, so every governed query returned ZERO rows. The fix (semantic/security.js
`driverFactory` -> tenant-scoped Postgres driver running a parameterized
`set_config('app.current_tenant', $1, false)` per connection) makes Cube tenant-scoped END TO
END: each tenant sees its own rows and ONLY its own rows.

Skip-if-no-cube pattern (same shape as test_rls_isolation.py): runs only when BOTH a Cube and a
seedable Postgres are reachable —
  - CUBE_ENDPOINT + CUBEJS_API_SECRET_VALUE -> the deployed Cube + its checkAuth signing secret
  - UPLIFT_DB_URL (crm_app) or UPLIFT_TEST_DB_URL (owner) -> the SAME database Cube reads
Otherwise it SKIPS with a clear reason. The query rides the real REST path: a per-request HS256
tenant JWT (agents/tools/cube_client.CubeClient) -> checkAuth -> queryRewrite -> driverFactory.
"""
import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from agents.tools.cube_client import CubeClient  # noqa: E402

CUBE_ENDPOINT = os.environ.get("CUBE_ENDPOINT")
CUBE_SECRET = os.environ.get("CUBEJS_API_SECRET_VALUE")
DB_URL = os.environ.get("UPLIFT_DB_URL") or os.environ.get("UPLIFT_TEST_DB_URL")

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def cube() -> CubeClient:
    if not CUBE_ENDPOINT or not CUBE_SECRET:
        pytest.skip("set CUBE_ENDPOINT + CUBEJS_API_SECRET_VALUE to run the Cube isolation proof")
    return CubeClient(endpoint=CUBE_ENDPOINT, secret=CUBE_SECRET)


@pytest.fixture(scope="module")
def db():
    if not DB_URL:
        pytest.skip("set UPLIFT_DB_URL (crm_app) or UPLIFT_TEST_DB_URL to seed the two tenants")
    try:
        conn = psycopg2.connect(DB_URL)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no reachable Postgres ({e.__class__.__name__})")
    yield conn
    conn.close()


@pytest.fixture()
def two_seeded_tenants(db):
    """Seed ONE distinctively-titled deal per fresh tenant, COMMITTED (Cube reads over its own
    connections — an uncommitted row is invisible to it). Fresh per-run tenant uuids also dodge
    Cube's per-tenant compile/result caches (contextToAppId keys on the tenant). Cleanup deletes
    the rows tenant-scoped and commits."""
    a, b = uuid.uuid4(), uuid.uuid4()
    title_a, title_b = f"cube-iso-{a}", f"cube-iso-{b}"
    with db.cursor() as cur:
        for tenant, title in ((a, title_a), (b, title_b)):
            cur.execute("SET app.current_tenant = %s", (str(tenant),))
            cur.execute(
                "INSERT INTO deals (tenant_id, title, stage, amount) VALUES (%s, %s, 'new', 100)",
                (str(tenant), title),
            )
    db.commit()
    try:
        yield (str(a), title_a), (str(b), title_b)
    finally:
        with db.cursor() as cur:
            for tenant in (a, b):
                cur.execute("SET app.current_tenant = %s", (str(tenant),))
                cur.execute("DELETE FROM deals WHERE tenant_id = %s", (str(tenant),))
        db.commit()


def _deal_titles(cube: CubeClient, tenant_id: str) -> set[str]:
    result = cube.load(
        tenant_id=tenant_id,
        query={"dimensions": ["Deals.title"], "limit": 1000},
    )
    if result.get("status") != "ok":
        detail = str(result.get("error") or result.get("detail") or result)
        if "unreachable" in detail.lower():
            pytest.skip(f"Cube not reachable at {CUBE_ENDPOINT}: {detail}")
        pytest.fail(f"Cube query failed as tenant {tenant_id}: {detail}")
    return {row.get("Deals.title") for row in result.get("rows", []) if isinstance(row, dict)}


def test_two_tenants_get_disjoint_rows_through_cube(cube, two_seeded_tenants):
    (tenant_a, title_a), (tenant_b, title_b) = two_seeded_tenants

    rows_a = _deal_titles(cube, tenant_a)
    rows_b = _deal_titles(cube, tenant_b)

    # The #177 regression: with the GUC never set, BOTH of these came back empty.
    assert title_a in rows_a, (
        "tenant A cannot see its own committed deal through Cube — app.current_tenant is not "
        "being set on Cube's connections (#177)"
    )
    assert title_b in rows_b, (
        "tenant B cannot see its own committed deal through Cube — app.current_tenant is not "
        "being set on Cube's connections (#177)"
    )

    # Isolation both ways: a fresh tenant sees EXACTLY its one seeded deal, and never the other's.
    assert rows_a == {title_a}, f"tenant A saw foreign rows: {sorted(rows_a - {title_a})}"
    assert rows_b == {title_b}, f"tenant B saw foreign rows: {sorted(rows_b - {title_b})}"
    assert rows_a.isdisjoint(rows_b)
