"""Customer document upload — the seam behind POST/PUT /knowledge/documents (knowledge audit P0).

Rides the SAME production pieces the seeder and connectors use — `ingest.chunk.chunk_text`,
the injected embedder seam, the `DocumentStore.upsert` (`PgDocumentStore` in real use: RLS-bound,
`SET LOCAL` per-op, ON CONFLICT idempotent) — so an upload is just another tenant-scoped corpus
write, never a parallel pipeline.

Ref scheme: `upload:<slug(title)>-<sha256(content)[:8]>#<seq>` under `source='upload'`.
The content hash in the namespace means:
  * re-posting the same title+content upserts IN PLACE (idempotent, never duplicates);
  * changed content lands under a NEW namespace — the old doc's chunks are never partially
    overwritten, so there is no stale-tail state (shorter new content can't leave orphaned
    high-#seq chunks pretending to be current).

Partial-corpus safety: every chunk embeds BEFORE the first upsert — a mid-doc embedder failure
lands NOTHING (the audit's partial-sync finding, applied to uploads).

RAW DOCUMENT ROW (the editable-knowledge upgrade): chunks are stored whitespace-collapsed with
a 40-word overlap, so the ORIGINAL text is NOT reconstructible from them. To make a document
readable and editable later (GET/PUT/DELETE /knowledge/documents/<ref>), the exact original
lands as ONE extra row `<ref_prefix>#raw` with `embedding NULL` — same table, same RLS, same
unique index; invisible to search (which filters `embedding IS NOT NULL`). Its content is
`<title>\n\n<content>` where the title is normalized to a single line, so the first paragraph
break is an unambiguous title/body separator. The raw row is written LAST — chunks land first,
so a mid-write failure can only mean "indexed but not yet editable", never the reverse.

IMPORT SAFETY: importing this module touches no AWS/boto3/psycopg2 — `chunk_text` is pure and
the store/embedder arrive injected.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Callable

from ingest import EMBEDDING_DIM
from ingest.chunk import chunk_text

UPLOAD_SOURCE = "upload"

# The non-numeric seq suffix of the raw-original row (api/knowledge_routes.py and
# api/pg_clients.py duplicate this literal — they must never import ingest/).
RAW_SUFFIX = "#raw"

# Mirror the API's bounds (api/knowledge_routes.py imports these — one source of truth).
MAX_TITLE_LEN = 200
MAX_DOC_CHARS = 100_000

_SLUG_MAX = 40


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:_SLUG_MAX].rstrip("-") or "doc"


def ingest_document(store: Any, embedder: Callable[[str], list[float]], *,
                    tenant_id: str, title: str, content: str) -> dict:
    """Chunk → embed (all first) → upsert one customer document + its raw-original row.
    Returns {ref_id, chunks, source, title}. Raises ValueError on invalid input or a wrong-dim
    embedder; any embedder/store error propagates (the route turns it into a loud 503 —
    a write must never silently no-op)."""
    # The title is normalized to ONE line (inner newlines/runs of whitespace collapse to a
    # single space) so the raw row's first paragraph break separates title from body.
    title = " ".join((title or "").split())
    content = (content or "").strip()
    if not title:
        raise ValueError("title is required")
    if len(title) > MAX_TITLE_LEN:
        raise ValueError(f"title must be at most {MAX_TITLE_LEN} characters")
    if not content:
        raise ValueError("content is required")
    if len(content) > MAX_DOC_CHARS:
        raise ValueError(f"content must be at most {MAX_DOC_CHARS} characters")

    # Fold the title into the embedded text (same rationale as the seeder: a query phrased
    # like the heading should match even when the body words differ).
    text = f"{title}\n\n{content}"
    ref_prefix = f"upload:{_slug(title)}-{hashlib.sha256(content.encode('utf-8')).hexdigest()[:8]}"

    pieces = chunk_text(text)
    embedded: list[tuple[str, str, list[float], str]] = []
    for seq, piece in enumerate(pieces):
        vec = embedder(piece)
        if len(vec) != EMBEDDING_DIM:
            raise ValueError(f"embedder returned dim {len(vec)} != {EMBEDDING_DIM}")
        chash = hashlib.sha256(piece.encode("utf-8")).hexdigest()
        embedded.append((f"{ref_prefix}#{seq}", piece, vec, chash))

    for ref_id, piece, vec, chash in embedded:
        store.upsert(str(tenant_id), UPLOAD_SOURCE, ref_id, piece, vec, chash)

    # The exact original, last (see RAW DOCUMENT ROW above). Same namespace => a re-post of
    # identical content overwrites in place; a title-only edit lands here AND in the re-embedded
    # chunks under the same refs.
    store.upsert_raw(str(tenant_id), UPLOAD_SOURCE, f"{ref_prefix}{RAW_SUFFIX}", text)

    return {"ref_id": ref_prefix, "chunks": len(embedded), "source": UPLOAD_SOURCE,
            "title": title}
