"""Unit: customer document upload — chunk → embed → upsert (knowledge audit P0, 2026-06-11).

`ingest.upload.ingest_document` is the seam behind POST /knowledge/documents: it rides the
SAME production pieces the seeder/connectors use (ingest.chunk.chunk_text, the embedder seam,
the DocumentStore upsert) under `source='upload'` with a deterministic
`upload:<slug>-<hash8>#<seq>` ref scheme — idempotent on re-post, no stale-tail chunks when
content changes (changed content = a new ref namespace, never a partial overwrite).
"""
import pytest

from ingest import EMBEDDING_DIM
from ingest.upload import MAX_DOC_CHARS, MAX_TITLE_LEN, ingest_document


class FakeStore:
    def __init__(self):
        self.rows: dict[tuple, tuple] = {}

    def upsert(self, tenant_id, source, ref_id, content, vec, chash):
        self.rows[(tenant_id, source, ref_id)] = (content, tuple(vec), chash)


def _embedder(dim=EMBEDDING_DIM):
    return lambda text: [0.1] * dim


LONG_CONTENT = " ".join(f"word{i} policy discount onboarding" for i in range(600))


@pytest.mark.unit
def test_ingest_document_lands_chunks_with_stable_upload_refs():
    store = FakeStore()
    out = ingest_document(store, _embedder(), tenant_id="T1",
                          title="Pricing Policy", content="Discounts cap at 15%.")
    assert out["chunks"] == len(store.rows) == 1
    ((tenant, source, ref),) = store.rows.keys()
    assert tenant == "T1" and source == "upload"
    assert ref.startswith("upload:pricing-policy-") and ref.endswith("#0")
    assert out["ref_id"] == ref.rsplit("#", 1)[0]

    # Idempotent: the same title+content re-posted upserts in place, never duplicates.
    again = ingest_document(store, _embedder(), tenant_id="T1",
                            title="Pricing Policy", content="Discounts cap at 15%.")
    assert again["ref_id"] == out["ref_id"]
    assert len(store.rows) == 1


@pytest.mark.unit
def test_changed_content_gets_a_distinct_ref_namespace():
    store = FakeStore()
    a = ingest_document(store, _embedder(), tenant_id="T1",
                        title="Pricing Policy", content="Discounts cap at 15%.")
    b = ingest_document(store, _embedder(), tenant_id="T1",
                        title="Pricing Policy", content="Discounts cap at 20% now.")
    assert a["ref_id"] != b["ref_id"]  # no stale-tail overwrite of the old doc's chunks


@pytest.mark.unit
def test_long_content_chunks_into_multiple_rows():
    store = FakeStore()
    out = ingest_document(store, _embedder(), tenant_id="T1",
                          title="Playbook", content=LONG_CONTENT)
    assert out["chunks"] > 1
    assert len(store.rows) == out["chunks"]


@pytest.mark.unit
def test_embedder_dim_mismatch_refuses_and_lands_nothing():
    store = FakeStore()
    with pytest.raises(ValueError, match="dim"):
        ingest_document(store, _embedder(dim=512), tenant_id="T1",
                        title="Doc", content="text")
    assert store.rows == {}


@pytest.mark.unit
def test_embed_failure_mid_doc_lands_nothing():
    """All chunks embed BEFORE any upsert — a mid-doc embedder failure must never leave a
    partial document in the corpus (the audit's partial-corpus finding, applied to uploads)."""
    calls = {"n": 0}

    def flaky(text):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("bedrock throttled")
        return [0.1] * EMBEDDING_DIM

    store = FakeStore()
    with pytest.raises(RuntimeError):
        ingest_document(store, flaky, tenant_id="T1", title="Playbook", content=LONG_CONTENT)
    assert store.rows == {}


@pytest.mark.unit
def test_blank_and_oversize_inputs_refused():
    store = FakeStore()
    with pytest.raises(ValueError):
        ingest_document(store, _embedder(), tenant_id="T1", title="  ", content="x")
    with pytest.raises(ValueError):
        ingest_document(store, _embedder(), tenant_id="T1", title="Doc", content="   ")
    with pytest.raises(ValueError):
        ingest_document(store, _embedder(), tenant_id="T1",
                        title="T" * (MAX_TITLE_LEN + 1), content="x")
    with pytest.raises(ValueError):
        ingest_document(store, _embedder(), tenant_id="T1",
                        title="Doc", content="x" * (MAX_DOC_CHARS + 1))
    assert store.rows == {}
