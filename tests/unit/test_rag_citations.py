"""Unit: agentic RAG + citation assembly (Build Guide Step 37).

The tested centerpiece: every claim in the grounded answer carries >=1 source_ref that exists in the
retrieved set. An unsupported claim is dropped (default) or flagged (flag_uncited=True) — never
returned as grounded. The injected clients are tenant-scoped and are actually used.
"""
import pytest

from conv.rag import Answer, RagContext, answer, assemble_citations


# --------------------------------------------------------------------------- fakes
class FakeRag:
    """Tenant-scoped pgvector search fake. Records the tenant it was called with."""

    def __init__(self, hits_by_tenant):
        self.hits_by_tenant = hits_by_tenant
        self.called_with = []

    def search(self, *, tenant_id, query, limit=8):
        self.called_with.append((tenant_id, query))
        return self.hits_by_tenant.get(tenant_id, [])


class FakeCrm:
    def __init__(self, rows_by_tenant):
        self.rows_by_tenant = rows_by_tenant
        self.called_with = []

    def read(self, *, tenant_id, query):
        self.called_with.append((tenant_id, query))
        return self.rows_by_tenant.get(tenant_id, [])


class FakeSynth:
    """Injected LLM fake — returns whatever claims it is configured with."""

    def __init__(self, claims, summary=None):
        self._claims = claims
        self._summary = summary
        self.saw_chunks = None

    def synthesize(self, *, question, chunks):
        self.saw_chunks = chunks
        return {"summary": self._summary, "claims": self._claims}


def _retrieved():
    return FakeRag(
        {
            "tenant-A": [
                {"ref": "doc:1", "snippet": "Acme renewed for $50k in Q1."},
                {"ref": "doc:2", "snippet": "Acme's main contact is Jane Doe."},
            ]
        }
    )


# --------------------------------------------------------------------------- assembly invariant
@pytest.mark.unit
def test_every_grounded_claim_cites_a_real_retrieved_ref():
    chunks = [
        {"ref": "doc:1", "snippet": "Acme renewed for $50k."},
        {"ref": "doc:2", "snippet": "Contact is Jane."},
    ]
    claims = [
        {"text": "Acme renewed for $50k.", "source_refs": ["doc:1"]},
        {"text": "The contact is Jane.", "source_refs": ["doc:2"]},
    ]
    cites, dropped = assemble_citations(claims, chunks)
    assert dropped == []
    assert all(c.source_ref in {"doc:1", "doc:2"} for c in cites)
    assert {c.source_ref for c in cites} == {"doc:1", "doc:2"}


@pytest.mark.unit
def test_unsupported_claim_is_dropped_by_default():
    chunks = [{"ref": "doc:1", "snippet": "Acme renewed for $50k."}]
    claims = [
        {"text": "Acme renewed for $50k.", "source_refs": ["doc:1"]},
        {"text": "Acme is planning to churn next month.", "source_refs": ["doc:99"]},  # not retrieved
    ]
    cites, dropped = assemble_citations(claims, chunks)
    # The supported claim is cited; the hallucinated one is dropped, never returned as grounded.
    assert [c.claim for c in cites] == ["Acme renewed for $50k."]
    assert len(dropped) == 1
    assert dropped[0]["claim"].startswith("Acme is planning to churn")
    assert dropped[0]["reason"] == "uncited"
    # No grounded citation points at the missing ref.
    assert all(c.source_ref != "doc:99" for c in cites)


@pytest.mark.unit
def test_unsupported_claim_can_be_flagged_ungrounded():
    chunks = [{"ref": "doc:1", "snippet": "Acme renewed for $50k."}]
    claims = [
        {"text": "Acme renewed for $50k.", "source_refs": ["doc:1"]},
        {"text": "Acme will churn.", "source_refs": []},
    ]
    cites, dropped = assemble_citations(claims, chunks, flag_uncited=True)
    grounded = [c for c in cites if c.source_ref]
    flagged = [c for c in cites if not c.source_ref]
    assert [c.claim for c in grounded] == ["Acme renewed for $50k."]
    assert [c.claim for c in flagged] == ["Acme will churn."]
    assert len(dropped) == 1  # still recorded as unsupported


# --------------------------------------------------------------------------- end-to-end answer()
@pytest.mark.unit
def test_answer_grounds_every_claim_and_uses_tenant_scoped_clients():
    rag = _retrieved()
    crm = FakeCrm({"tenant-A": [{"ref": "crm:deal-1", "snippet": "Open deal: expansion, $20k."}]})
    synth = FakeSynth(
        claims=[
            {"text": "Acme renewed for $50k in Q1.", "source_refs": ["doc:1"]},
            {"text": "There is an open $20k expansion deal.", "source_refs": ["crm:deal-1"]},
            {"text": "Acme is unhappy and about to leave.", "source_refs": ["doc:404"]},  # unsupported
        ],
        summary=None,
    )
    ctx = RagContext(tenant_id="tenant-A", rag=rag, crm=crm, synthesizer=synth)
    out = answer("How is Acme doing?", ctx)

    assert isinstance(out, Answer)
    # tenant-scoped clients were called with the right tenant
    assert rag.called_with == [("tenant-A", "How is Acme doing?")]
    assert crm.called_with == [("tenant-A", "How is Acme doing?")]
    # The hybrid set (rag + crm) was handed to the synthesizer.
    refs = {c["ref"] for c in synth.saw_chunks}
    assert {"doc:1", "doc:2", "crm:deal-1"} <= refs

    # Every grounded citation references a retrieved chunk; the hallucination did not survive.
    retrieved_refs = {c["ref"] for c in synth.saw_chunks}
    assert out.citations, "expected grounded citations"
    assert all(c.source_ref in retrieved_refs for c in out.citations)
    assert out.grounded
    assert any(d["claim"].startswith("Acme is unhappy") for d in out.dropped)
    # The unsupported claim never leaks into the prose either.
    assert "about to leave" not in out.answer


@pytest.mark.unit
def test_answer_is_tenant_isolated():
    rag = _retrieved()  # only tenant-A has hits
    synth = FakeSynth(claims=[{"text": "x", "source_refs": ["doc:1"]}])
    ctx = RagContext(tenant_id="tenant-B", rag=rag, synthesizer=synth)
    out = answer("anything", ctx)
    # tenant-B retrieves nothing -> the doc:1 claim can't be grounded -> dropped.
    assert out.citations == []
    assert out.dropped
    assert not any(c.source_ref for c in out.citations)


@pytest.mark.unit
def test_default_synthesizer_is_grounded_when_none_injected():
    rag = _retrieved()
    ctx = RagContext(tenant_id="tenant-A", rag=rag)  # no synthesizer
    out = answer("How is Acme?", ctx)
    assert out.citations
    assert out.grounded
    assert all(c.source_ref in {"doc:1", "doc:2"} for c in out.citations)
