"""Integration: the committed demo fixture loads idempotently, RLS-scoped, and RAG round-trips.

Runs only when UPLIFT_TEST_DB_URL (an owner/superuser DSN to load schema.sql + roles.sql) is
set AND a Postgres+pgvector is reachable. Otherwise SKIPS cleanly — the repo's standard
integration-DB gate (see tests/integration/test_ingest_pgvector.py).

Proves the load_demo_tenant.py contract:
  * loads the committed fixture as the RLS-bound crm_app role under SET LOCAL app.current_tenant
  * is idempotent — loading twice yields the fixture's counts, never doubled
  * is tenant-scoped — a second tenant sees none of the first tenant's rows
  * embeds `documents` so they round-trip through the live PgRagClient search interface
"""
import importlib.util
import os
import urllib.parse as up
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from api.pg_clients import PgRagClient  # noqa: E402
from ingest import EMBEDDING_DIM  # noqa: E402
from ingest.run_sync import _stub_embedder  # noqa: E402

OWNER_URL = os.environ.get("UPLIFT_TEST_DB_URL")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DB_DIR = os.path.join(ROOT, "db")
LOADER_PATH = os.path.join(ROOT, "scripts", "demo", "load_demo_tenant.py")


def _loader():
    spec = importlib.util.spec_from_file_location("load_demo_tenant", LOADER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _app_dsn():
    """Load schema+roles as owner, then return a crm_app (non-owner, RLS-bound) DSN. Skip if no DB."""
    if not OWNER_URL:
        pytest.skip("set UPLIFT_TEST_DB_URL (owner DSN) to run the demo-loader integration test")
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


def _counts(dsn, tenant):
    """Row counts for one tenant, RLS-scoped on a fresh crm_app connection."""
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SET app.current_tenant = %s", (tenant,))
            out = {}
            for table in ("companies", "contacts", "deals", "activities", "approvals",
                          "saved_views"):
                cur.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 — fixed table list
                out[table] = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM documents WHERE ref_id LIKE 'demo:doc:%'")
            out["documents"] = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM documents WHERE embedding IS NULL")
            out["null_embeddings"] = cur.fetchone()[0]
        return out
    finally:
        conn.close()


@pytest.mark.integration
def test_load_is_idempotent_rls_scoped_and_searchable():
    dsn = _app_dsn()
    loader = _loader()
    dataset = loader.read_fixture()
    expected = dataset["meta"]["counts"]
    tenant = str(uuid.uuid4())
    other = str(uuid.uuid4())

    # Load twice under the same tenant — must not double.
    for _ in range(2):
        conn = psycopg2.connect(dsn)
        try:
            counts = loader.load(conn, dataset, tenant_id=tenant, embedder=_stub_embedder)
        finally:
            conn.close()
    assert counts["companies"] == expected["companies"]

    got = _counts(dsn, tenant)
    assert got["companies"] == expected["companies"] == 40
    assert got["contacts"] == expected["contacts"] == 120
    assert got["deals"] == expected["deals"] == 60
    assert got["activities"] == expected["activities"]
    assert got["approvals"] == expected["approvals"]
    assert got["saved_views"] == expected["saved_views"] == 2
    assert got["documents"] == expected["documents"], "fixture documents present, not duplicated"
    assert got["null_embeddings"] == 0, "every document was embedded at load time"

    # Tenant isolation: a tenant that was never loaded sees nothing.
    empty = _counts(dsn, other)
    assert all(v == 0 for k, v in empty.items() if k != "null_embeddings")

    # RAG round-trip: the live search interface retrieves the seeded, embedded corpus, RLS-scoped.
    rag = PgRagClient(dsn, embedder=_stub_embedder)
    hits = rag.search(tenant_id=tenant, query="Westlake Galleria chiller retrofit COI", limit=5)
    assert hits, "the embedded corpus is retrievable via the production search interface"
    assert all(h["ref_id"].startswith("demo:doc:") for h in hits)
    assert all(h["content"] for h in hits)
    assert rag.search(tenant_id=other, query="anything", limit=5) == [], "search is RLS-scoped"


@pytest.mark.integration
def test_stub_embedder_dimensionality_matches_schema():
    """Guard the load-time embed contract: the offline embedder is exactly the documents.embedding
    width, so a load never fails the vector(1024) cast."""
    assert len(_stub_embedder("anything")) == EMBEDDING_DIM == 1024
