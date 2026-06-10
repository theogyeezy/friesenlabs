"""Unit: AnthropicSynthesizer (mocked client) — the citation invariant holds at the synthesizer,
malformed model JSON degrades to the extractive fallback (never crashes), refs outside the
retrieved set are dropped, and the seam stays lazy/import-safe (no network at construction).
"""
import json

import pytest

from agents.roster import SONNET
from conv.rag import RagContext, answer, make_synthesizer
from conv.synthesizer import AnthropicSynthesizer, _parse_claims


# --------------------------------------------------------------------------- fakes
class _Block:
    def __init__(self, text, type="text"):
        self.type = type
        self.text = text


class _Resp:
    def __init__(self, blocks):
        self.content = blocks


class _FakeMessages:
    def __init__(self, payloads, error=None):
        self._payloads = list(payloads)
        self._error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return _Resp([_Block(self._payloads.pop(0))])


class FakeAnthropic:
    """Stands in for anthropic.Anthropic — just the .messages.create surface we use."""

    def __init__(self, payloads=(), error=None):
        self.messages = _FakeMessages(payloads, error)


class FakeRag:
    def __init__(self, hits):
        self.hits = hits

    def search(self, *, tenant_id, query, limit=8):
        return self.hits


CHUNKS = [
    {"ref": "doc:1", "snippet": "Acme renewed for $50k in Q1.", "source": "rag", "raw": {}},
    {"ref": "crm:deal-1", "snippet": "Open deal: expansion, $20k.", "source": "crm", "raw": {}},
]


def _payload(claims):
    return json.dumps({"claims": claims})


# --------------------------------------------------------------------------- invariant
@pytest.mark.unit
def test_refs_outside_retrieved_set_are_filtered_and_unbacked_claims_dropped():
    client = FakeAnthropic(
        payloads=[
            _payload(
                [
                    {"text": "Acme renewed for $50k.", "source_refs": ["doc:1", "doc:1"]},
                    # one real ref + one hallucinated -> filtered down to the real one
                    {"text": "There is a $20k expansion deal.", "source_refs": ["crm:deal-1", "doc:404"]},
                    # only hallucinated refs -> the whole claim is dropped
                    {"text": "Acme is about to churn.", "source_refs": ["doc:999"]},
                    # empty refs -> dropped
                    {"text": "Unattributed claim.", "source_refs": []},
                ]
            )
        ]
    )
    s = AnthropicSynthesizer(client=client)
    out = s.synthesize(question="How is Acme doing?", chunks=CHUNKS)

    assert out["summary"] is None  # prose is built from grounded claims only, never model prose
    assert out["claims"] == [
        {"text": "Acme renewed for $50k.", "source_refs": ["doc:1"]},  # deduped
        {"text": "There is a $20k expansion deal.", "source_refs": ["crm:deal-1"]},
    ]
    retrieved = {c["ref"] for c in CHUNKS}
    assert all(set(c["source_refs"]) <= retrieved for c in out["claims"])


@pytest.mark.unit
def test_citation_invariant_holds_end_to_end_through_rag_answer():
    client = FakeAnthropic(
        payloads=[
            _payload(
                [
                    {"text": "Acme renewed for $50k in Q1.", "source_refs": ["doc:1"]},
                    {"text": "Acme is unhappy and about to leave.", "source_refs": ["doc:404"]},
                ]
            )
        ]
    )
    rag = FakeRag([{"ref": "doc:1", "snippet": "Acme renewed for $50k in Q1."}])
    ctx = RagContext(tenant_id="tenant-A", rag=rag, synthesizer=AnthropicSynthesizer(client=client))
    out = answer("How is Acme doing?", ctx)

    assert out.grounded
    assert out.citations, "expected grounded citations"
    assert all(c.source_ref == "doc:1" for c in out.citations)
    # The hallucinated claim never appears — not as a citation, not in the prose.
    assert "about to leave" not in out.answer
    assert all("about to leave" not in c.claim for c in out.citations)


@pytest.mark.unit
def test_malformed_claim_entries_are_skipped_not_fatal():
    client = FakeAnthropic(
        payloads=[
            _payload(
                [
                    "not-a-dict",
                    {"text": 42, "source_refs": ["doc:1"]},          # non-string text
                    {"text": "   ", "source_refs": ["doc:1"]},       # blank text
                    {"text": "ok", "source_refs": "doc:1"},          # refs not a list
                    {"text": "good claim", "source_refs": ["doc:1", 7]},  # non-string ref ignored
                ]
            )
        ]
    )
    out = AnthropicSynthesizer(client=client).synthesize(question="q", chunks=CHUNKS)
    assert out["claims"] == [{"text": "good claim", "source_refs": ["doc:1"]}]


@pytest.mark.unit
def test_int_refs_are_normalized_via_str_on_both_sides():
    # The non-string-ref edge: _call_model shows the model str(ref), so chunks whose refs are
    # ints (raw row ids) must still match — whether the model echoes the string form OR emits a
    # raw JSON number. Both sides of the filter normalize via str().
    int_chunks = [
        {"ref": 1, "snippet": "Acme renewed for $50k in Q1."},
        {"ref": 2, "snippet": "Open deal: expansion, $20k."},
    ]
    client = FakeAnthropic(
        payloads=[
            _payload(
                [
                    {"text": "Acme renewed.", "source_refs": ["1"]},               # echoed string
                    {"text": "There is an expansion deal.", "source_refs": [2]},   # raw JSON int
                    {"text": "Deduped either way.", "source_refs": [1, "1"]},      # both forms
                    {"text": "Hallucinated.", "source_refs": [404, "404"]},        # not retrieved
                ]
            )
        ]
    )
    out = AnthropicSynthesizer(client=client).synthesize(question="q", chunks=int_chunks)
    assert out["claims"] == [
        {"text": "Acme renewed.", "source_refs": ["1"]},
        {"text": "There is an expansion deal.", "source_refs": ["2"]},
        {"text": "Deduped either way.", "source_refs": ["1"]},
    ]


@pytest.mark.unit
def test_refless_chunks_are_never_citable():
    # A chunk with no ref serializes as "" in the prompt; an empty proposed ref must not be
    # able to cite it (the retrieved set discards the empty marker).
    chunks = [{"ref": "doc:1", "snippet": "real"}, {"snippet": "ref-less"}]
    client = FakeAnthropic(
        payloads=[_payload([{"text": "sneaky empty-ref claim.", "source_refs": [""]}])]
    )
    out = AnthropicSynthesizer(client=client).synthesize(question="q", chunks=chunks)
    assert out["claims"] == []


# --------------------------------------------------------------------------- graceful degradation
@pytest.mark.unit
def test_non_json_model_output_falls_back_to_extractive():
    client = FakeAnthropic(payloads=["Sure! Acme renewed and there is an open deal."])
    out = AnthropicSynthesizer(client=client).synthesize(question="q", chunks=CHUNKS)
    # Extractive fallback: every retrieved chunk becomes a trivially-grounded claim.
    assert out["claims"] == [
        {"text": "Acme renewed for $50k in Q1.", "source_refs": ["doc:1"]},
        {"text": "Open deal: expansion, $20k.", "source_refs": ["crm:deal-1"]},
    ]
    assert out["summary"]  # the extractive default provides a deterministic summary


@pytest.mark.unit
def test_wrong_json_shape_falls_back_to_extractive():
    for bad in ['["a", "b"]', '{"answers": []}', '{"claims": "nope"}', "null"]:
        client = FakeAnthropic(payloads=[bad])
        out = AnthropicSynthesizer(client=client).synthesize(question="q", chunks=CHUNKS)
        assert len(out["claims"]) == 2, f"expected extractive fallback for {bad!r}"


@pytest.mark.unit
def test_api_error_falls_back_to_extractive_never_raises():
    client = FakeAnthropic(error=RuntimeError("simulated API outage"))
    out = AnthropicSynthesizer(client=client).synthesize(question="q", chunks=CHUNKS)
    assert out["claims"] and all(c["source_refs"] for c in out["claims"])


@pytest.mark.unit
def test_markdown_fenced_json_is_tolerated():
    fenced = "```json\n" + _payload([{"text": "Acme renewed.", "source_refs": ["doc:1"]}]) + "\n```"
    client = FakeAnthropic(payloads=[fenced])
    out = AnthropicSynthesizer(client=client).synthesize(question="q", chunks=CHUNKS)
    assert out["claims"] == [{"text": "Acme renewed.", "source_refs": ["doc:1"]}]


@pytest.mark.unit
def test_json_wrapped_in_prose_is_recovered():
    wrapped = "Here you go:\n" + _payload([{"text": "Acme renewed.", "source_refs": ["doc:1"]}])
    assert _parse_claims(wrapped) == [{"text": "Acme renewed.", "source_refs": ["doc:1"]}]


@pytest.mark.unit
def test_model_says_no_support_yields_empty_claims_not_fallback():
    # A *valid* empty-claims response is respected (rag.answer then says "no grounded sources"),
    # it is NOT treated as a failure that triggers the extractive fallback.
    client = FakeAnthropic(payloads=[_payload([])])
    out = AnthropicSynthesizer(client=client).synthesize(question="q", chunks=CHUNKS)
    assert out == {"summary": None, "claims": []}


# --------------------------------------------------------------------------- seam hygiene
@pytest.mark.unit
def test_empty_chunks_short_circuit_without_any_model_call():
    client = FakeAnthropic(payloads=[])
    out = AnthropicSynthesizer(client=client).synthesize(question="q", chunks=[])
    assert out == {"summary": None, "claims": []}
    assert client.messages.calls == []


@pytest.mark.unit
def test_client_is_lazy_and_construction_needs_no_network():
    s = AnthropicSynthesizer(api_key="unused")
    assert s._client is None  # nothing built until the first real call
    assert s.model == SONNET  # the repo's Sonnet-tier model id


@pytest.mark.unit
def test_model_call_shape_uses_repo_sonnet_and_carries_refs():
    client = FakeAnthropic(payloads=[_payload([])])
    AnthropicSynthesizer(client=client).synthesize(question="How is Acme?", chunks=CHUNKS)
    (call,) = client.messages.calls
    assert call["model"] == SONNET
    assert call["max_tokens"] > 0
    assert call["messages"][0]["role"] == "user"
    body = call["messages"][0]["content"]
    assert "How is Acme?" in body and "doc:1" in body and "crm:deal-1" in body
    assert "source_refs" in call["system"]  # the JSON contract is in the system prompt


@pytest.mark.unit
def test_make_synthesizer_factory():
    assert make_synthesizer() is None  # default: offline extractive path unchanged
    assert make_synthesizer("extractive") is None
    s = make_synthesizer("anthropic", client=FakeAnthropic(payloads=[]))
    assert isinstance(s, AnthropicSynthesizer)
    with pytest.raises(ValueError):
        make_synthesizer("nope")
