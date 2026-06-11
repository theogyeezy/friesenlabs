"""Unit: the demo-tenant knowledge corpus + seeder pure paths (no DB).

Guards the committed markdown under agents/knowledge_seed/ and the parts of
scripts/demo/seed_knowledge.py that need neither AWS nor a database:

  * corpus shape — ~25 docs, each with title/category frontmatter, covering the brief's
    families (FAQ, pricing, playbook, onboarding)
  * fabrication discipline — no real PII / freemail / project principals in any doc
  * chunk plan — every chunk lands under the demo:kb: namespace, disjoint from the CRM
    fixture's demo:doc:, stable + unique ref_ids (idempotent upsert key)
  * seeder reuse — seed() drives an in-memory DocumentStore through the production chunk path
    and is idempotent (re-running does not duplicate)
  * import-safety — the seeder module imports with no AWS/DB
"""
import importlib.util
import os
import re

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DOCS_DIR = os.path.join(ROOT, "agents", "knowledge_seed")
SEEDER_PATH = os.path.join(ROOT, "scripts", "demo", "seed_knowledge.py")


def _seeder():
    spec = importlib.util.spec_from_file_location("seed_knowledge", SEEDER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def seeder():
    return _seeder()


@pytest.fixture(scope="module")
def corpus(seeder):
    return seeder.load_corpus()


# --------------------------------------------------------------------------- corpus shape
@pytest.mark.unit
def test_corpus_size_and_metadata(corpus):
    assert 24 <= len(corpus) <= 30, "the brief asks for ~25 knowledge docs"
    for doc in corpus:
        assert doc["title"] and doc["title"] != doc["slug"], f"{doc['slug']} missing a title"
        assert doc["category"] != "knowledge", f"{doc['slug']} missing a category"
        assert len(doc["content"]) > 300, f"{doc['slug']} is too thin to be useful"
        assert doc["content"].startswith(doc["title"]), "title is folded into embedded content"


@pytest.mark.unit
def test_corpus_covers_the_brief_families(corpus):
    cats = {d["category"] for d in corpus}
    assert {"faq", "pricing", "playbook", "onboarding"} <= cats


@pytest.mark.unit
def test_readme_is_not_seeded(corpus):
    slugs = {d["slug"] for d in corpus}
    assert "readme" not in slugs and "README" not in slugs


# --------------------------------------------------------------------------- fabrication discipline
@pytest.mark.unit
def test_no_real_pii_in_any_doc():
    blob = ""
    for fname in os.listdir(DOCS_DIR):
        if fname.endswith(".md"):
            blob += open(os.path.join(DOCS_DIR, fname), encoding="utf-8").read() + "\n"
    # No email addresses at all (knowledge docs are prose, not contact data), no freemail,
    # no project principals.
    assert "@" not in blob, "knowledge docs must not contain email addresses"
    assert not re.search(r"\b(gmail|yahoo|hotmail|outlook)\b", blob, re.I)
    for name in ("Nick Friesen", "Matthew Yee", "Matt Yee"):
        assert name not in blob


# --------------------------------------------------------------------------- chunk plan
@pytest.mark.unit
def test_chunk_plan_namespace_and_uniqueness(seeder, corpus):
    rows = seeder.plan_chunks(corpus)
    assert len(rows) >= len(corpus)
    for r in rows:
        assert r["ref_id"].startswith("demo:kb:"), r["ref_id"]
        assert "#" in r["ref_id"], "chunk ref_id carries a sequence suffix"
        assert not r["ref_id"].startswith("demo:doc:"), "disjoint from the CRM fixture namespace"
    assert len({r["ref_id"] for r in rows}) == len(rows), "ref_ids are unique (upsert key)"


@pytest.mark.unit
def test_chunk_plan_is_deterministic(seeder, corpus):
    a = seeder.plan_chunks(corpus)
    b = seeder.plan_chunks(seeder.load_corpus())
    assert a == b


# --------------------------------------------------------------------------- seeder reuse + idempotency
@pytest.mark.unit
def test_seed_drives_documentstore_and_is_idempotent(seeder):
    from ingest import EMBEDDING_DIM
    from ingest.pipeline import InMemoryDocumentStore
    from ingest.run_sync import _stub_embedder

    store = InMemoryDocumentStore()
    tenant = "11111111-1111-1111-1111-111111111111"

    counts = seeder.seed(store, _stub_embedder, tenant_id=tenant)
    assert counts["docs"] >= 24 and counts["chunks"] >= counts["docs"]
    assert len(store.docs) == counts["chunks"]
    # every stored row is a demo:kb: upload with a correctly-sized embedding, scoped to tenant
    for (t, source, ref_id), row in store.docs.items():
        assert t == tenant and source == "upload" and ref_id.startswith("demo:kb:")
        assert len(row["embedding"]) == EMBEDDING_DIM

    before = dict(store.docs)
    seeder.seed(store, _stub_embedder, tenant_id=tenant)  # re-run
    assert len(store.docs) == len(before), "re-seeding does not duplicate (upsert by ref_id)"
