"""AnthropicSynthesizer — the real (Sonnet-backed) RAG synthesizer (TODO P1).

Implements the `conv.rag.Synthesizer` protocol with a live Anthropic `messages.create` call:
given the question + the retrieved chunks, the model is prompted to emit
`{"claims": [{"text": ..., "source_refs": [...]}]}` JSON.

The citation invariant is enforced HERE as well as in `conv.rag.assemble_citations` (defense in
depth — the model only *proposes* refs, it never gets to mint them):

  - every proposed `source_refs` entry is FILTERED to the refs that actually exist in the
    retrieved set; a claim left with no valid refs after filtering is dropped.
  - the returned `summary` is always None, so `conv.rag.answer` builds the answer prose from the
    GROUNDED claims only — free-form model prose can never leak an uncited statement.
  - malformed model output (non-JSON, wrong shape) and API errors degrade gracefully to the
    deterministic extractive `_default_synthesize` — a turn never crashes on a bad generation.

Offline/import-safe: the `anthropic` SDK is imported lazily on first use; constructing the class
needs no network and no creds. Tests inject a fake `client`.

Note on the beta header: this is a plain `/v1/messages` call, so the Managed Agents beta header
(`anthropic-beta: managed-agents-2026-04-01`) is NOT required — the SDK applies it automatically
to the `client.beta.{agents,sessions,environments,vaults,memory_stores}.*` namespaces only
(verified against the claude-api skill / SDK docs, 2026-06). The repo-wide "MA header on every
Anthropic call" convention is about the agent plane behind `agents/runtime.py`, which already sets
it on its own lazy client.
"""
from __future__ import annotations

import json
from typing import Any

from agents.roster import SONNET

from .rag import _default_synthesize

_SYSTEM = """\
You are the synthesis stage of a retrieval-augmented answering pipeline for a multi-tenant CRM.
You are given a user question and a set of retrieved source chunks. Each chunk has a stable
"ref" id and a "snippet" of text. Respond with ONLY a JSON object of this exact shape:

{"claims": [{"text": "<one short factual claim that helps answer the question>", "source_refs": ["<ref>"]}]}

Rules:
- Every claim MUST cite at least one ref, and ONLY refs that appear in the provided sources.
- Ground every claim in the cited snippet(s). Never speculate or use outside knowledge.
- Keep each claim a single short sentence; emit several claims rather than one long one.
- If the sources do not support an answer, return {"claims": []}.
- Output raw JSON only — no prose, no markdown fences."""


def _text_of(response: Any) -> str:
    """Concatenate the text blocks of a Messages API response (tolerates dict-shaped fakes)."""
    parts: list[str] = []
    for block in getattr(response, "content", None) or []:
        if isinstance(block, dict):
            btype, text = block.get("type"), block.get("text")
        else:
            btype, text = getattr(block, "type", None), getattr(block, "text", None)
        if btype == "text" and isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _parse_claims(raw: str) -> list | None:
    """Defensively parse the model's `{claims: [...]}` JSON. Returns None when unusable."""
    text = (raw or "").strip()
    if not text:
        return None
    # Tolerate markdown fences despite the prompt forbidding them.
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        # Last resort: the outermost {...} span (models sometimes wrap JSON in prose).
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except (ValueError, TypeError):
            return None
    if not isinstance(data, dict):
        return None
    claims = data.get("claims")
    if not isinstance(claims, list):
        return None
    return claims


class AnthropicSynthesizer:
    """`conv.rag.Synthesizer` backed by a Sonnet-tier Anthropic model.

    Lazy client: nothing touches the network at import or construction time. Inject `client`
    (any object with `.messages.create(...)`) in tests, or `api_key` for an explicit key —
    with neither, the SDK default credential resolution (ANTHROPIC_API_KEY env) applies on
    first use.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any = None,
        model: str = SONNET,
        max_tokens: int = 4096,
    ) -> None:
        self._api_key = api_key
        self._client = client  # built lazily; tests inject a fake
        self.model = model
        self.max_tokens = max_tokens

    def _client_or_build(self) -> Any:
        if self._client is None:
            from anthropic import Anthropic  # noqa: PLC0415 — lazy on purpose (import-safety)

            self._client = Anthropic(api_key=self._api_key)
        return self._client

    # ------------------------------------------------------------------ protocol
    def synthesize(self, *, question: str, chunks: list[dict]) -> dict:
        """Question + retrieved chunks -> {"summary": None, "claims": [...]}.

        Never raises on model failure: malformed output / API errors fall back to the
        extractive default so offline behavior is the worst case, not a crash.
        """
        if not chunks:
            return {"summary": None, "claims": []}

        try:
            raw = self._call_model(question, chunks)
            claims = _parse_claims(raw)
        except Exception:
            claims = None  # API error / unexpected response shape -> extractive fallback
        if claims is None:
            return _default_synthesize(question, chunks)

        retrieved = {c.get("ref") for c in chunks}
        kept: list[dict] = []
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            text = claim.get("text")
            proposed = claim.get("source_refs")
            if not isinstance(text, str) or not text.strip() or not isinstance(proposed, list):
                continue
            # THE invariant: refs are filtered to the retrieved set; order kept, dupes dropped.
            seen: set[str] = set()
            valid = [
                r
                for r in proposed
                if isinstance(r, str) and r in retrieved and not (r in seen or seen.add(r))
            ]
            if valid:  # a claim with no surviving refs is dropped, not returned uncited
                kept.append({"text": text, "source_refs": valid})

        # summary stays None so conv.rag.answer assembles prose from grounded claims only.
        return {"summary": None, "claims": kept}

    # ------------------------------------------------------------------ model call
    def _call_model(self, question: str, chunks: list[dict]) -> str:
        sources = json.dumps(
            [
                {"ref": str(c.get("ref", "")), "snippet": str(c.get("snippet", ""))}
                for c in chunks
            ],
            ensure_ascii=False,
        )
        user = f"Question:\n{question}\n\nSources (JSON array of {{ref, snippet}}):\n{sources}"
        response = self._client_or_build().messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        return _text_of(response)
