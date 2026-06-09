"""Chunking for retrieval.

`chunk_text` splits a blob into ~300–500 token windows with light overlap. We
approximate tokens by whitespace-delimited words (no tokenizer dependency — keeps
import AWS/network free); ~0.75 words/token is close enough for sizing decisions
and the embedder truncates anyway.

Plus per-record-type strategies (`chunk_record`):
  - CRM record  → one chunk per record summary, separate chunks per note/activity.
  - transcript  → one chunk per speaker turn.
  - stripe      → customer/invoice-level text.

Every chunk is a `Chunk` carrying tenant_id, source, ref_id — no cross-tenant
mixing, and ref_id drives the incremental upsert in the pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Chunk:
    tenant_id: str
    source: str
    ref_id: str
    content: str
    kind: str = "text"
    seq: int = 0  # ordinal within the source record (for multi-chunk records)

    @property
    def doc_ref_id(self) -> str:
        """Stable per-chunk ref_id for the documents unique index.

        documents is unique on (tenant_id, source, ref_id); a record that yields
        multiple chunks needs distinct ref_ids, so we suffix the sequence.
        """
        return f"{self.ref_id}#{self.seq}" if self.seq else self.ref_id


def _words(text: str) -> list[str]:
    return text.split()


def chunk_text(text: str, target_tokens: int = 400, overlap: int = 40) -> list[str]:
    """Split `text` into overlapping windows of ~target_tokens 'tokens' (words).

    Returns a list of strings. A short text yields a single chunk. Overlap is the
    number of trailing words repeated at the head of the next window for context
    continuity. Empty/whitespace text yields [].
    """
    if target_tokens <= 0:
        raise ValueError("target_tokens must be > 0")
    if overlap < 0 or overlap >= target_tokens:
        raise ValueError("overlap must be >= 0 and < target_tokens")

    words = _words(text)
    if not words:
        return []
    if len(words) <= target_tokens:
        return [" ".join(words)]

    step = target_tokens - overlap
    chunks: list[str] = []
    i = 0
    n = len(words)
    while i < n:
        window = words[i : i + target_tokens]
        chunks.append(" ".join(window))
        if i + target_tokens >= n:
            break
        i += step
    return chunks


def _emit(
    tenant_id: str,
    source: str,
    ref_id: str,
    text: str,
    kind: str,
    *,
    start_seq: int,
    target_tokens: int,
    overlap: int,
) -> list[Chunk]:
    out: list[Chunk] = []
    pieces = chunk_text(text, target_tokens=target_tokens, overlap=overlap)
    for j, piece in enumerate(pieces):
        out.append(
            Chunk(
                tenant_id=tenant_id,
                source=source,
                ref_id=ref_id,
                content=piece,
                kind=kind,
                seq=start_seq + j,
            )
        )
    return out


def chunk_record(
    *,
    tenant_id: str,
    source: str,
    ref_id: str,
    text_blocks: Iterable[dict],
    target_tokens: int = 400,
    overlap: int = 40,
) -> list[Chunk]:
    """CRM-record strategy: one+ chunks per text block (summary, notes, activities).

    `text_blocks` is the connector's per-record blocks
    (e.g. [{"text","kind","ref_id"}]). Each block is independently chunked; a block
    may carry its own ref_id (a note) so it lands as its own document(s).
    """
    chunks: list[Chunk] = []
    seq = 0
    for block in text_blocks:
        text = (block.get("text") or "").strip()
        if not text:
            continue
        block_ref = str(block.get("ref_id") or ref_id)
        block_kind = block.get("kind") or "text"
        produced = _emit(
            tenant_id,
            source,
            block_ref,
            text,
            block_kind,
            start_seq=0 if block_ref != ref_id else seq,
            target_tokens=target_tokens,
            overlap=overlap,
        )
        chunks.extend(produced)
        if block_ref == ref_id:
            seq += len(produced)
    return chunks


def chunk_transcript(
    *,
    tenant_id: str,
    source: str,
    ref_id: str,
    turns: Iterable[dict],
    target_tokens: int = 400,
    overlap: int = 40,
) -> list[Chunk]:
    """Transcript strategy: one chunk per speaker turn (further split if long).

    `turns` is [{"speaker","text"}...]. Each turn is prefixed with the speaker so
    the embedding carries who said it.
    """
    chunks: list[Chunk] = []
    seq = 0
    for turn in turns:
        text = (turn.get("text") or "").strip()
        if not text:
            continue
        speaker = turn.get("speaker") or "Unknown"
        produced = _emit(
            tenant_id,
            source,
            ref_id,
            f"{speaker}: {text}",
            "transcript_turn",
            start_seq=seq,
            target_tokens=target_tokens,
            overlap=overlap,
        )
        chunks.extend(produced)
        seq += len(produced)
    return chunks


def chunk_stripe(
    *,
    tenant_id: str,
    source: str,
    ref_id: str,
    customer: dict,
    target_tokens: int = 400,
    overlap: int = 40,
) -> list[Chunk]:
    """Stripe strategy: customer-level text + one chunk per invoice.

    `customer` is {"name","email","invoices":[{"id","amount","currency","status",
    "description"}...]}. Each invoice gets its own ref_id so it's independently
    upsertable.
    """
    chunks: list[Chunk] = []
    name = customer.get("name") or ""
    email = customer.get("email") or ""
    summary = f"Customer: {name}".strip()
    if email:
        summary += f"\nEmail: {email}"
    chunks.extend(
        _emit(
            tenant_id, source, ref_id, summary, "customer",
            start_seq=0, target_tokens=target_tokens, overlap=overlap,
        )
    )
    for inv in customer.get("invoices", []):
        inv_ref = str(inv.get("id") or f"{ref_id}-inv")
        text = (
            f"Invoice {inv_ref} for {name}: "
            f"{inv.get('amount', '')} {inv.get('currency', '')} "
            f"[{inv.get('status', '')}] {inv.get('description', '')}"
        ).strip()
        chunks.extend(
            _emit(
                tenant_id, source, inv_ref, text, "invoice",
                start_seq=0, target_tokens=target_tokens, overlap=overlap,
            )
        )
    return chunks
