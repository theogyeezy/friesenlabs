"""Integration: the knowledge corpus seeds and round-trips through the live RAG search interface.

Runs only when UPLIFT_TEST_DB_URL (an owner DSN to load schema.sql + roles.sql) is set AND a
Postgres+pgvector is reachable; otherwise SKIPS cleanly (the repo's standard integration gate).

Proves the seed_knowledge.py contract:
  * chunks + embeds + upserts the agents/knowledge_seed/*.md corpus into `documents` via the
    production PgDocumentStore (RLS-bound crm_app, SET LOCAL, ON CONFLICT upsert)
  * is idempotent — re-seeding does not duplicate
  * round-trips through PgRagClient.search (the same interface conv/rag.py uses), RLS-scoped,
    and lives in the demo:kb: namespace disjoint from the CRM fixture's demo:doc:
"""
import importlib.util
import os
import urllib.parse as up
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from api.pg_clients import PgRagClient  # noqa: E402
from ingest.pipeline import PgDocumentStore  # noqa: E402
from ingest.run_sync import _stub_embedder  # noqa: E402

OWNER_URL = os.environ.get("UPLIFT_TEST_DB_URL")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DB_DIR = os.path.join(ROOT, "db")
SEEDER_PATH = os.path.join(ROOT, "scripts", "demo", "seed_knowledge.py")


def _seeder():
    spec = importlib.util.spec_from_file_location("seed_knowledge", SEEDER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _app_dsn():
    if not OWNER_URL:
        pytest.skip("set UPLIFT_TEST_DB_URL (owner DSN) to run the knowledge-seed integration test")
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


def _kb_count(dsn, tenant):
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SET app.current_tenant = %s", (tenant,))
            cur.execute("SELECT count(*) FROM documents WHERE ref_id LIKE 'demo:kb:%'")
            return cur.fetchone()[0]
    finally:
        conn.close()


@pytest.mark.integration
def test_knowledge_seeds_idempotently_and_round_trips():
    dsn = _app_dsn()
    seeder = _seeder()
    store = PgDocumentStore(dsn)
    tenant = str(uuid.uuid4())
    other = str(uuid.uuid4())

    counts = seeder.seed(store, _stub_embedder, tenant_id=tenant)
    assert counts["docs"] >= 24
    first = _kb_count(dsn, tenant)
    assert first == counts["chunks"] >= counts["docs"]

    # Idempotent: a second seed upserts in place, no duplication.
    seeder.seed(store, _stub_embedder, tenant_id=tenant)
    assert _kb_count(dsn, tenant) == first

    # Round-trips through the live RAG search interface, RLS-scoped, in the demo:kb: namespace.
    rag = PgRagClient(dsn, embedder=_stub_embedder)
    hits = rag.search(tenant_id=tenant, query="discount authority and the 10% floor", limit=5)
    assert hits, "the knowledge corpus is retrievable via the production search interface"
    assert all(h["ref_id"].startswith("demo:kb:") for h in hits)
    assert all(h["source"] == "upload" and h["content"] for h in hits)
    assert rag.search(tenant_id=other, query="anything", limit=5) == [], "search is RLS-scoped"
