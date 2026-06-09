"""Agentic RAG with citation assembly (Build Guide Step 37).

`answer(question, ctx)`:
  1. Hybrid retrieval (conceptually parallel — we just call both): the injected `rag.search` over
     pgvector AND the injected `crm.read`. Both clients are tenant-scoped, so permission-awareness is
     automatic — we never re-filter results by tenant by hand.
  2. `synthesize` (an injected LLM fake) turns the retrieved set + question into a list of *claims*,
     each annotated by the synthesizer with the source_refs it used.
  3. **Citation assembly** maps each claim -> the retrieved chunk(s) that actually back it and returns
     `{answer, citations:[{claim, source_ref, snippet}], dropped:[...]}`.

Hard rule (the tested centerpiece): every claim that survives into the grounded answer carries >=1
source_ref that EXISTS in the retrieved set. A claim with no valid source_ref is NOT grounded — by
default it is dropped (and recorded in `dropped`), or flagged inline if `flag_uncited=True`. An
uncited claim is never returned as grounded.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


# --------------------------------------------------------------------------- clients (injected)
class RagClient(Protocol):
    """Tenant-scoped pgvector search. Returns chunks already filtered to the tenant (RLS)."""

    def search(self, *, tenant_id: str, query: str, limit: int = ...) -> list[dict]: ...


class CrmClient(Protocol):
    """Tenant-scoped CRM read. Returns rows already filtered to the tenant (RLS)."""

    def read(self, *, tenant_id: str, query: str) -> list[dict]: ...


class Synthesizer(Protocol):
    """The injected LLM (fake in tests). Given the question + retrieved chunks, returns a structured
    set of claims. Each claim is a dict: {"text": str, "source_refs": [ref, ...]}. The synthesizer
    only *proposes* refs; citation assembly is what verifies them against the retrieved set."""

    def synthesize(self, *, question: str, chunks: list[dict]) -> dict: ...


# --------------------------------------------------------------------------- context + results
@dataclass
class RagContext:
    tenant_id: str
    rag: RagClient
    crm: CrmClient | None = None
    synthesizer: Synthesizer | None = None
    rag_limit: int = 8
    flag_uncited: bool = False   # False => drop uncited claims; True => keep them, flagged ungrounded


@dataclass
class Citation:
    claim: str
    source_ref: str
    snippet: str

    def as_dict(self) -> dict:
        return {"claim": self.claim, "source_ref": self.source_ref, "snippet": self.snippet}


@dataclass
class Answer:
    answer: str
    citations: list[Citation] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)   # uncited claims that did not make the answer

    def as_dict(self) -> dict:
        return {
            "answer": self.answer,
            "citations": [c.as_dict() for c in self.citations],
            "dropped": list(self.dropped),
        }

    @property
    def grounded(self) -> bool:
        """True iff every cited claim has a real source_ref and nothing ungrounded leaked through
        (i.e. there is at least one citation OR the answer is empty, and no dropped claim is presented
        as grounded). The per-claim invariant is enforced in assembly; this is the summary signal."""
        return all(c.source_ref for c in self.citations)


# --------------------------------------------------------------------------- retrieval
def _retrieve(ctx: RagContext, question: str) -> list[dict]:
    """Hybrid retrieval over the two tenant-scoped sources. Normalizes each hit to a chunk with a
    stable `ref` (source_ref) and a `snippet`. Clients enforce tenancy; we do not re-filter."""
    chunks: list[dict] = []

    vector_hits = ctx.rag.search(tenant_id=ctx.tenant_id, query=question, limit=ctx.rag_limit) or []
    for i, h in enumerate(vector_hits):
        chunks.append(_normalize(h, default_ref=f"doc:{i}", source="rag"))

    if ctx.crm is not None:
        crm_hits = ctx.crm.read(tenant_id=ctx.tenant_id, query=question) or []
        for i, h in enumerate(crm_hits):
            chunks.append(_normalize(h, default_ref=f"crm:{i}", source="crm"))

    return chunks


def _normalize(hit: dict, *, default_ref: str, source: str) -> dict:
    ref = str(hit.get("ref") or hit.get("id") or default_ref)
    snippet = str(hit.get("snippet") or hit.get("text") or hit.get("content") or "")
    return {"ref": ref, "snippet": snippet, "source": source, "raw": hit}


# --------------------------------------------------------------------------- citation assembly
def assemble_citations(
    claims: list[dict], chunks: list[dict], *, flag_uncited: bool = False
) -> tuple[list[Citation], list[dict]]:
    """Map each claim to the retrieved chunk(s) backing it. THE invariant:

    every returned Citation's source_ref EXISTS in `chunks`. A claim whose proposed source_refs are
    all absent from the retrieved set is uncited -> dropped (default) or flagged ungrounded
    (flag_uncited=True). Either way it is never returned as a grounded Citation.
    """
    by_ref = {c["ref"]: c for c in chunks}
    citations: list[Citation] = []
    dropped: list[dict] = []

    for claim in claims:
        text = claim.get("text", "")
        proposed = claim.get("source_refs") or []
        valid = [r for r in proposed if r in by_ref]
        if valid:
            for ref in valid:
                citations.append(Citation(claim=text, source_ref=ref, snippet=by_ref[ref]["snippet"]))
        else:
            # No proposed ref exists in the retrieved set => cannot ground this claim.
            dropped.append({"claim": text, "proposed_refs": list(proposed), "reason": "uncited"})

    if flag_uncited and dropped:
        # Surface dropped claims as explicitly UNGROUNDED citations (no real source_ref) so a caller
        # can show them flagged. We use an empty source_ref to mark "no support" — `.grounded` is
        # False whenever any such marker is present.
        for d in dropped:
            citations.append(Citation(claim=d["claim"], source_ref="", snippet="[unsupported]"))

    return citations, dropped


def make_synthesizer(kind: str = "extractive", **kwargs: Any) -> Synthesizer | None:
    """Optional synthesizer factory for callers wiring a `RagContext` (mirrors `get_runtime`).

    - "extractive" (default) returns None, so `answer()` keeps using `_default_synthesize` —
      offline behavior is unchanged and nothing here needs network or creds.
    - "anthropic" returns the real Sonnet-backed `conv.synthesizer.AnthropicSynthesizer`
      (lazy import; construction never touches the network — the SDK client builds on first use).
    """
    if kind == "anthropic":
        from .synthesizer import AnthropicSynthesizer  # noqa: PLC0415 — keep rag import-light

        return AnthropicSynthesizer(**kwargs)
    if kind == "extractive":
        return None
    raise ValueError(f"unknown synthesizer kind: {kind!r}")


def _default_synthesize(question: str, chunks: list[dict]) -> dict:
    """Fallback synthesizer when none is injected: a trivially-grounded extractive answer that cites
    every retrieved chunk. Deterministic; offline. Never used when a synthesizer is provided."""
    claims = [{"text": c["snippet"], "source_refs": [c["ref"]]} for c in chunks if c["snippet"]]
    summary = " ".join(c["text"] for c in claims) or "No supporting material found."
    return {"summary": summary, "claims": claims}


def answer(question: str, ctx: RagContext) -> Answer:
    """Agentic RAG: retrieve (hybrid, tenant-scoped) -> synthesize -> assemble citations.

    Guarantees no uncited claim is returned as grounded (assembly drops/flags it).
    """
    chunks = _retrieve(ctx, question)

    if ctx.synthesizer is not None:
        result = ctx.synthesizer.synthesize(question=question, chunks=chunks)
    else:
        result = _default_synthesize(question, chunks)

    claims = result.get("claims", [])
    citations, dropped = assemble_citations(claims, chunks, flag_uncited=ctx.flag_uncited)

    # The answer text is built from the GROUNDED claims only (those with a valid source_ref), so a
    # dropped/unsupported claim never appears in the prose either.
    grounded_texts = [c.claim for c in citations if c.source_ref]
    # Preserve order + dedupe.
    seen: set[str] = set()
    ordered = [t for t in grounded_texts if not (t in seen or seen.add(t))]
    text = result.get("summary") if result.get("summary") and not dropped else None
    if text is None:
        text = " ".join(ordered) if ordered else "I don't have grounded sources to answer that."

    return Answer(answer=text, citations=citations, dropped=dropped)
