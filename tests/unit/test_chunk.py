"""Unit: chunk sizing/overlap + tenant_id/source/ref_id carried on every chunk."""
import pytest

from ingest.chunk import (
    Chunk,
    chunk_record,
    chunk_stripe,
    chunk_text,
    chunk_transcript,
)

TENANT = "11111111-1111-1111-1111-111111111111"


@pytest.mark.unit
def test_short_text_single_chunk():
    assert chunk_text("hello world") == ["hello world"]


@pytest.mark.unit
def test_empty_text_yields_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


@pytest.mark.unit
def test_long_text_splits_with_overlap_and_sizing():
    words = [f"w{i}" for i in range(1000)]
    text = " ".join(words)
    chunks = chunk_text(text, target_tokens=400, overlap=40)

    # More than one chunk, each within the target window.
    assert len(chunks) > 1
    for c in chunks:
        assert 1 <= len(c.split()) <= 400

    # Overlap: the tail of chunk[0] reappears at the head of chunk[1].
    c0, c1 = chunks[0].split(), chunks[1].split()
    assert c0[-40:] == c1[:40]

    # Step = target - overlap = 360; full coverage (no dropped words).
    # Reconstruct by stripping the 40-word overlap from every chunk after the first.
    rebuilt = chunks[0].split()
    for c in chunks[1:]:
        rebuilt += c.split()[40:]
    assert rebuilt == words


@pytest.mark.unit
def test_chunk_sizes_in_target_band():
    words = [f"w{i}" for i in range(2000)]
    chunks = chunk_text(" ".join(words), target_tokens=400, overlap=40)
    # Non-final chunks should be exactly the target window (300-500 band).
    for c in chunks[:-1]:
        assert 300 <= len(c.split()) <= 500


@pytest.mark.unit
def test_invalid_params_raise():
    with pytest.raises(ValueError):
        chunk_text("a b c", target_tokens=0)
    with pytest.raises(ValueError):
        chunk_text("a b c", target_tokens=100, overlap=100)
    with pytest.raises(ValueError):
        chunk_text("a b c", target_tokens=100, overlap=-1)


@pytest.mark.unit
def test_chunk_record_carries_tenant_source_ref():
    blocks = [
        {"ref_id": "c-1", "kind": "contact", "text": "Contact: Ada Lovelace\nEmail: ada@x.io"},
        {"ref_id": "n-9", "kind": "note", "text": "Called about renewal. Wants a quote."},
    ]
    chunks = chunk_record(
        tenant_id=TENANT, source="hubspot", ref_id="c-1", text_blocks=blocks
    )
    assert len(chunks) == 2
    for ch in chunks:
        assert isinstance(ch, Chunk)
        assert ch.tenant_id == TENANT
        assert ch.source == "hubspot"
        assert ch.ref_id  # present
        assert ch.content
    # The note block keeps its own ref_id (independently upsertable).
    kinds = {ch.ref_id: ch.kind for ch in chunks}
    assert kinds["n-9"] == "note"


@pytest.mark.unit
def test_chunk_record_long_note_gets_distinct_doc_ref_ids():
    long_note = " ".join(f"x{i}" for i in range(900))
    blocks = [{"ref_id": "rec-1", "kind": "summary", "text": long_note}]
    chunks = chunk_record(
        tenant_id=TENANT, source="hubspot", ref_id="rec-1", text_blocks=blocks
    )
    assert len(chunks) > 1
    doc_refs = [ch.doc_ref_id for ch in chunks]
    assert len(set(doc_refs)) == len(doc_refs)  # unique per chunk
    assert doc_refs[0] == "rec-1"  # first chunk keeps the base ref


@pytest.mark.unit
def test_transcript_chunks_by_speaker_turn():
    turns = [
        {"speaker": "Rep", "text": "Thanks for hopping on."},
        {"speaker": "Buyer", "text": "Happy to. We need this by Q3."},
    ]
    chunks = chunk_transcript(
        tenant_id=TENANT, source="call", ref_id="call-7", turns=turns
    )
    assert len(chunks) == 2
    assert chunks[0].content.startswith("Rep:")
    assert chunks[1].content.startswith("Buyer:")
    for ch in chunks:
        assert ch.tenant_id == TENANT and ch.source == "call" and ch.ref_id == "call-7"
        assert ch.kind == "transcript_turn"


@pytest.mark.unit
def test_stripe_customer_and_invoice_chunks():
    customer = {
        "name": "Acme Co",
        "email": "ap@acme.io",
        "invoices": [
            {"id": "in_1", "amount": 1200, "currency": "usd", "status": "paid",
             "description": "Annual plan"},
            {"id": "in_2", "amount": 99, "currency": "usd", "status": "open",
             "description": "Seat add-on"},
        ],
    }
    chunks = chunk_stripe(
        tenant_id=TENANT, source="stripe", ref_id="cus_1", customer=customer
    )
    kinds = [c.kind for c in chunks]
    assert kinds.count("customer") == 1
    assert kinds.count("invoice") == 2
    invoice_refs = {c.ref_id for c in chunks if c.kind == "invoice"}
    assert invoice_refs == {"in_1", "in_2"}
    for ch in chunks:
        assert ch.tenant_id == TENANT and ch.source == "stripe"
