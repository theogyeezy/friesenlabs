"""Integration: the request-path stores hold tenant isolation under concurrent multi-thread load.

This is the adversarial proof for the shared-connection RLS leak. The live API runs sync handlers on
the anyio threadpool, so MANY threads hit the SAME store singleton at once, interleaving two tenants'
reads/writes. The OLD design (one shared psycopg2 connection + a single session-level
`SET app.current_tenant` GUC + a stateful `self._tenant`) raced: tenant A's SELECT could execute after
tenant B overwrote the GUC, leaking B's rows to A. The FIXED design (a ThreadedConnectionPool + a
per-op transaction that begins with `SET LOCAL app.current_tenant` and never shares a connection across
threads) must be deterministic-clean.

We drive the ACTUAL `PgApprovalStore` / `PgSavedViewStore` singletons (one each, shared across all
threads — exactly like the live `build_app()` singletons) from a thread pool and assert ZERO
cross-tenant leakage over many iterations.

Runs against a real Postgres only when reachable (same gating as test_rls_isolation.py):
  - UPLIFT_TEST_DB_URL  -> a superuser/owner URL used to load schema.sql + roles.sql, OR
  - UPLIFT_DB_URL       -> an already-provisioned crm_app URL (skip the load step)
If neither is set / reachable, the test SKIPS with a clear reason (green locally; hard gate in CI).
"""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from api.control.greenlight import Greenlight, PgApprovalStore  # noqa: E402
from api.views import PgSavedViewStore, SavedViews  # noqa: E402

OWNER_URL = os.environ.get("UPLIFT_TEST_DB_URL")
APP_URL = os.environ.get("UPLIFT_DB_URL")
HERE = os.path.dirname(__file__)
DB_DIR = os.path.join(HERE, "..", "..", "db")

# Enough threads to genuinely interleave on the shared singleton; enough iterations that the old
# raced GUC would flake/leak with overwhelming probability.
N_WORKERS = 16
N_ITERS = 200


def _connect(url):
    try:
        return psycopg2.connect(url)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no reachable Postgres ({e.__class__.__name__})")


@pytest.fixture(scope="module")
def app_dsn():
    if not OWNER_URL and not APP_URL:
        pytest.skip("set UPLIFT_TEST_DB_URL (owner) or UPLIFT_DB_URL (crm_app) to run the concurrency proof")

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
        return up.urlunparse(parts._replace(netloc=f"crm_app:testpw@{parts.hostname}:{parts.port or 5432}"))
    # Already a crm_app URL — verify it connects, then hand it back.
    _connect(APP_URL).close()
    return APP_URL


def _spec(view_id: str) -> dict:
    # semantic_refs must be non-empty per shared/schemas/view_spec.schema.json (SavedViews.save validates).
    return {"view_id": view_id, "title": view_id, "semantic_refs": ["Deals.count"],
            "layout": [{"type": "kpi", "metric": "Deals.count"}], "version": 1}


@pytest.mark.integration
def test_approvals_no_cross_tenant_leak_under_concurrency(app_dsn):
    # ONE shared store singleton — exactly like the live build_app() singleton.
    gl = Greenlight(store=PgApprovalStore(app_dsn))
    a, b = str(uuid.uuid4()), str(uuid.uuid4())

    def work(i: int) -> tuple:
        # Interleave: even iterations act as tenant A, odd as tenant B. Each thread proposes a row,
        # then lists ITS OWN pending and asserts it never sees the other tenant's rows.
        me, other = (a, b) if i % 2 == 0 else (b, a)
        rec = gl.propose(tenant_id=me, action="send_email", agent="nadia", reasoning=f"r{i}",
                         value_at_stake=float(i), payload={"to": f"x{i}@y.com"})
        # The freshly-proposed row must come back scoped to me, with my tenant_id.
        got = gl.list_pending(me)
        leaked = [r for r in got if str(r["tenant_id"]) != me]
        # And reading it back by id under the OTHER tenant must 404 (None) — never cross.
        cross = gl.store.get(other, rec["id"])
        return (rec["tenant_id"] == me, leaked, cross)

    with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
        futures = [ex.submit(work, i) for i in range(N_ITERS)]
        for f in as_completed(futures):
            own_ok, leaked, cross = f.result()
            assert own_ok, "proposed row came back with the wrong tenant_id"
            assert leaked == [], f"RLS leak: list_pending returned another tenant's rows: {leaked}"
            assert cross is None, "RLS leak: a row read back under the other tenant"

    # Final whole-set check per tenant: every pending row a tenant sees is its own.
    for t in (a, b):
        rows = gl.list_pending(t)
        assert rows, "expected this tenant's rows to be present"
        assert all(str(r["tenant_id"]) == t for r in rows), "RLS leak in final list_pending"


@pytest.mark.integration
def test_saved_views_no_cross_tenant_leak_under_concurrency(app_dsn):
    sv = SavedViews(store=PgSavedViewStore(app_dsn), allowed_members={"Deals.count"})
    a, b = str(uuid.uuid4()), str(uuid.uuid4())

    def work(i: int) -> tuple:
        me = a if i % 2 == 0 else b
        view_id = f"v-{me}-{i}"
        sv.save(me, _spec(view_id), source_prompt="p", created_by="u")
        # list() returns purely RLS-scoped rows — the silent saved_views leak path. Assert no foreign row.
        rows = sv.store.list(me)
        leaked = [r for r in rows if str(r["tenant_id"]) != me]
        return leaked

    with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
        futures = [ex.submit(work, i) for i in range(N_ITERS)]
        for f in as_completed(futures):
            leaked = f.result()
            assert leaked == [], f"RLS leak: saved_views list returned another tenant's rows: {leaked}"

    for t in (a, b):
        rows = sv.store.list(t)
        assert rows, "expected this tenant's views to be present"
        assert all(str(r["tenant_id"]) == t for r in rows), "RLS leak in final saved_views list"
