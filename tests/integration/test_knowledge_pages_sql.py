"""Integration: the uploaded-pages SQL against REAL Postgres (PgRagClient document methods).

The pages surface's correctness lives in SQL, so it gets a real-DB proof (same gating as the
RLS siblings — green locally without a DB; the hard gate runs in CI):

  * list_uploaded_documents returns ONLY chunked document families (#0..#n / #raw members):
    single-row corpus shadows that also ride source='upload' (the demo fixture's
    `demo:doc:act:N` activity notes) must NEVER list as pages — pre-HAVING they flooded the
    rail as junk read-only entries titled by their trailing digit.
  * the raw head is bounded; chunk_count counts EMBEDDED rows only (the #raw mirror has
    embedding NULL).
  * list_document_inventory excludes the #raw mirror rows from per-source counts.
  * delete_uploaded_document removes the whole namespace (chunks + raw) for the calling
    tenant only — RLS keeps another tenant's identically-named ref untouchable.

Gating:
  - UPLIFT_TEST_DB_URL  -> a superuser/owner URL used to load schema.sql + roles.sql, OR
  - UPLIFT_DB_URL       -> an already-provisioned crm_app URL (skip the load step)
"""
import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from api.pg_clients import PgRagClient  # noqa: E402

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
        pytest.skip("set UPLIFT_TEST_DB_URL (owner) or UPLIFT_DB_URL (crm_app) to run "
                    "the knowledge pages SQL proof")
    if OWNER_URL:
        owner = _connect(OWNER_URL)
        owner.autocommit = True
        with owner.cursor() as cur:
            cur.execute(open(os.path.join(DB_DIR, "schema.sql")).read())
            cur.execute(open(os.path.join(DB_DIR, "roles.sql")).read())
            cur.execute("ALTER ROLE crm_app PASSWORD 'testpw'")
        owner.close()
        host = OWNER_URL.split("@")[-1]
        return f"postgresql://crm_app:testpw@{host}"
    return APP_URL


def _seed(client: PgRagClient, tenant: str) -> None:
    """One chunked editable doc + one chunked legacy doc + two single-row activity shadows,
    all under source='upload' — straight INSERTs via the client's own RLS-bound tx."""
    vec = "[" + ",".join(["0.01"] * 1024) + "]"
    with client._tx(tenant) as cur:
        rows = [
            # editable page: 2 chunks + the raw original
            ("upload:pricing-policy-ab12cd34#0", "Pricing policy chunk zero", vec),
            ("upload:pricing-policy-ab12cd34#1", "Pricing policy chunk one", vec),
            ("upload:pricing-policy-ab12cd34#raw",
             "Pricing policy\n\nDiscounts cap at 15%.", None),
            # legacy chunked doc (no raw row) — the seeded demo:kb shape
            ("demo:kb:discount-authority#0", "Discount authority chunk", vec),
            # single-row corpus shadows: retrieval fodder, NOT pages
            ("demo:doc:act:1", "Note — field visit", vec),
            ("demo:doc:act:2", "Meeting — kickoff", vec),
        ]
        for ref, content, embedding in rows:
            cur.execute(
                "INSERT INTO documents (tenant_id, source, ref_id, content, embedding) "
                "VALUES (%s, 'upload', %s, %s, %s::vector) "
                "ON CONFLICT (tenant_id, source, ref_id) DO UPDATE SET "
                "content=EXCLUDED.content, embedding=EXCLUDED.embedding",
                (tenant, ref, content, embedding),
            )


@pytest.mark.integration
def test_pages_are_chunked_families_only_and_namespace_delete_is_tenant_scoped(app_dsn):
    client = PgRagClient(app_dsn)
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    _seed(client, tenant_a)
    _seed(client, tenant_b)

    docs = client.list_uploaded_documents(tenant_id=tenant_a)
    refs = [d["ref_id"] for d in docs]
    # The two chunked families list; the act:N shadows NEVER do.
    assert sorted(refs) == ["demo:kb:discount-authority", "upload:pricing-policy-ab12cd34"]
    by_ref = {d["ref_id"]: d for d in docs}
    page = by_ref["upload:pricing-policy-ab12cd34"]
    assert page["chunk_count"] == 2  # embedded rows only — the raw mirror doesn't count
    assert page["raw_head"].startswith("Pricing policy\n\n")
    assert by_ref["demo:kb:discount-authority"]["raw_head"] is None  # legacy: not editable

    # The inventory's per-source count excludes the raw mirror (6 rows seeded, 1 is #raw).
    inv = {r["source"]: r["document_count"]
           for r in client.list_document_inventory(tenant_id=tenant_a)}
    assert inv["upload"] == 5

    # get: the act shadow is not a page (None); the page returns raw + ordered chunks.
    assert client.get_uploaded_document(tenant_id=tenant_a, ref_prefix="demo:doc:act:1") is None
    got = client.get_uploaded_document(tenant_id=tenant_a,
                                       ref_prefix="upload:pricing-policy-ab12cd34")
    assert got["raw_content"] == "Pricing policy\n\nDiscounts cap at 15%."
    assert got["chunk_contents"] == ["Pricing policy chunk zero", "Pricing policy chunk one"]

    # delete: the whole namespace for THIS tenant; tenant B's identical refs survive (RLS).
    removed = client.delete_uploaded_document(tenant_id=tenant_a,
                                              ref_prefix="upload:pricing-policy-ab12cd34")
    assert removed == 3
    assert [d["ref_id"] for d in client.list_uploaded_documents(tenant_id=tenant_a)] == [
        "demo:kb:discount-authority"]
    assert "upload:pricing-policy-ab12cd34" in [
        d["ref_id"] for d in client.list_uploaded_documents(tenant_id=tenant_b)]
