"""Authed per-tenant knowledge endpoints — the api half of the real Knowledge tab
(the sixth honest-stub tab converted to REAL, after Pipeline + Contacts + Agents + Workflows +
Reports; the web half is web/src/api/KnowledgeView.tsx).

Three endpoints, all bound to the VERIFIED JWT claims (THE TRUST RULE — tenant never from a
header or the request body):

  GET /knowledge          the tenant's knowledge-base INVENTORY: per-source document counts +
                          the newest ingested timestamp, plus the totals. This is the always-
                          honest core — a plain aggregate over `documents`, NO embedding model
                          needed, so it works the moment the data plane is wired even if the
                          Titan embedder isn't. An un-ingested tenant gets an honest empty
                          inventory (totals zero), never invented sources.

  GET /knowledge/search   cosine-similarity search over the tenant's corpus (PgRagClient.search):
                          ref_id + source + a content SNIPPET + score, RLS-scoped. The query is
                          embedded at call time by the lazy Titan V2 embedder (Bedrock) — which
                          is env-key-gated on the live task today. So search DEGRADES HONESTLY:
                          if the embedder/model isn't reachable the route answers 200 with
                          `search_available: false` + a reason and an empty result list (the
                          inventory tab stays useful), never a 500 and never a raw AWS error.

  POST /knowledge/documents  the customer corpus-add path (knowledge audit P0): paste a doc,
                          the ingest seam (ingest/upload.py) chunks → embeds (ALL chunks before
                          the first upsert — a mid-doc failure lands NOTHING) → upserts under
                          source='upload' with deterministic upload:<slug>-<hash8>#<seq> refs,
                          PLUS one `#raw` row holding the exact original (embedding NULL) so the
                          document stays readable + editable. Gated on the ingest plane's
                          INGEST_REAL_STORES switch (build_doc_ingestor): unswitched -> honest
                          503; an ingest failure is a LOUD 503 (never search's quiet degrade —
                          a write must not no-op).

  GET /knowledge/documents   the tenant's PAGES — every uploaded document, newest first:
                          ref, title, a bounded preview, chunk count, stamps, and whether it is
                          editable (legacy pre-raw uploads list as read-only). A plain RLS-scoped
                          aggregate, no embedder — honest the moment the data plane is wired.

  GET /knowledge/documents/{ref}  one page in full: the exact original title + body when the
                          raw row exists; a legacy upload degrades honestly to its indexed
                          chunk texts (read-only, `editable: false`) — never invented content.

  PUT /knowledge/documents/{ref}  edit a page: re-ingest the new title+content through the SAME
                          seam as POST (changed content = a NEW ref namespace), then remove the
                          old namespace. The new version lands BEFORE the old one is deleted —
                          a mid-edit failure can duplicate, never lose. Legacy uploads (no raw
                          row) refuse with an honest 409 (re-add to make editable).

  DELETE /knowledge/documents/{ref}  remove a page: every row under the ref namespace (chunks +
                          raw) in one RLS-scoped transaction; 404 when nothing existed. crm_app
                          holds DELETE on documents (db/roles.sql) — no broader grant involved.

Reads ride the SAME crm_app DSN every live surface (/approvals, /views, /deals, /contacts)
already rides — the PgRagClient `SET LOCAL app.current_tenant` per-op transaction (RLS), no
hand-written tenant filter anywhere. The free-text `q` is length-capped (q > MAX_Q_LEN -> 422)
so a hostile query can never become an unbounded scan/embedding term. Unconfigured (no DSN -> no
reader injected) both endpoints answer an honest 503, never invented rows.

IMPORT SAFETY: importing this module touches no AWS/boto3/DB and never imports ingest/ — the
embedder (ingest.embed) is imported lazily INSIDE PgRagClient.search at call time, never at
import. The image-fileset regression test imports api.app (which mounts this module); proven
boto3-free at import by tests/integration/test_api_knowledge.py.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from api.auth import TenantClaims
from api.pg_clients import (  # typed boundaries; psycopg2/boto3-free import
    EmbedderUnavailable,
    PageOrganizeUnavailable,
)

log = logging.getLogger("api.knowledge")

_UNCONFIGURED_DETAIL = (
    "knowledge data plane not configured — no crm_app DSN on this task "
    "(DB_*/UPLIFT_DB_URL unset); the knowledge base is unavailable"
)

_UNCONFIGURED_UPLOAD_DETAIL = (
    "document upload not configured — the ingest plane (INGEST_REAL_STORES + a DSN) "
    "is not wired on this task"
)

# The honest loud-failure detail when the ingest plane raises mid-upload. A WRITE never
# degrades to a quiet 200 the way search does — the customer must know the doc did not land.
REASON_UPLOAD_FAILED = "document ingest failed — the document was not saved; try again"

# Upload bounds — one source of truth with the ingest seam (ingest/upload.py mirrors these;
# the values are duplicated as literals here so importing this module never imports ingest).
MAX_TITLE_LEN = 200
MAX_DOC_CHARS = 100_000

# Free-text search cap (the Contacts/Pipeline hardening note): a q longer than this is a 422,
# never a scan/embedding term. Generous for a real question; hostile for payload smuggling.
MAX_Q_LEN = 500

# How many search hits may leave the API per request, and the default. The reader clamps again.
DEFAULT_SEARCH_LIMIT = 8
MAX_SEARCH_LIMIT = 25
# How deep "show more" may page into the ranked scan — semantic rank decays fast; past this
# the results are noise and the offset is only scan cost.
MAX_SEARCH_OFFSET = 200

# How much of a matched document's content leaves the API per hit. A snippet for display — never
# the full document dump (keeps the payload bounded and avoids over-exposing a long record).
SNIPPET_LEN = 320

# The honest degrade reasons (knowledge audit P1 — differentiated, never one blanket string):
# `REASON_SEARCH_UNAVAILABLE` = the QUERY embedder isn't reachable (Bedrock/Titan env-key-gated
# on the live task — the calm "warming up" story); `REASON_SEARCH_FAILED` = anything after the
# embed (DB read, pool) — transient, the UI offers a retry, NOT "warming up" forever. The web
# keys off `reason_code`; the strings are pinned by the integration tests so the operator
# story stays stable.
REASON_SEARCH_UNAVAILABLE = "search model not configured"
REASON_SEARCH_FAILED = "search failed"
REASON_CODE_EMBEDDER = "embedder_unavailable"
REASON_CODE_SEARCH_ERROR = "search_error"

# --- uploaded-document (pages) surface ------------------------------------------------------
# A page ref is the chunk-family prefix under source='upload'. TWO shapes exist in real
# corpora: customer uploads `upload:<slug>-<hash8>` (ingest/upload.py) and seeded docs
# `demo:kb:<slug>` (scripts/demo/seed_knowledge.py) — so validation is a conservative
# CHARSET bound (lowercase + digits + : . -, no '#'/'%'/'_' so a ref can never carry a seq
# suffix or LIKE wildcards), not one exact shape. The reader LIKE-escapes again — belt and
# suspenders. Literals duplicated from ingest/ so importing this module never imports ingest/.
_REF_RE = re.compile(r"^[a-z0-9][a-z0-9:.-]{0,158}$")
RAW_SUFFIX = "#raw"
# The upload content-hash suffix, stripped when de-slugging a legacy ref into a title.
_HASH8_RE = re.compile(r"-[0-9a-f]{8}$")

# Bounded body preview per page in the LIST response (the title line is separate).
PREVIEW_LEN = 160

DETAIL_DOC_NOT_FOUND = "no document with that ref in your knowledge base"
DETAIL_BAD_REF = "not a valid uploaded-document ref"
# A pre-raw-row upload has no stored original to edit — re-adding it (POST) makes it editable.
DETAIL_DOC_NOT_EDITABLE = (
    "this document predates editable knowledge and has no stored original — "
    "add it again to make it editable, or delete it"
)
# Page organization (hierarchy/order over knowledge_pages) — pinned honest details.
DETAIL_ORGANIZE_UNAVAILABLE = (
    "page organization is rolling out — the knowledge_pages migration hasn't run on this "
    "deployment yet; your pages are unaffected"
)
DETAIL_ORGANIZE_CYCLE = "a page can't be moved under itself or one of its own sub-pages"
DETAIL_ORGANIZE_ONE_OP = (
    'send exactly one operation: parent_ref (a page ref or null) OR move ("up"/"down")'
)


@dataclass
class KnowledgeDeps:
    # A PgRagClient-shaped reader (list_document_inventory / search). None = data plane
    # unconfigured -> both endpoints answer the honest 503, never invented rows. The ONLY real
    # wiring is api/asgi.py passing the SAME PgRagClient instance the executor/chat RAG tool and
    # the agent runtime already use (one pool, the exact dsn_from_env guard the live siblings ride).
    rag: Any | None = None
    # The customer document-add seam (knowledge audit P0): a callable
    # (tenant_id, title, content) -> {ref_id, chunks, source, title} that chunks→embeds→upserts
    # via ingest.upload (build_doc_ingestor below). None = upload unconfigured -> the POST
    # answers an honest 503, never a quiet success that landed nothing.
    ingest_document: Callable[[str, str, str], Any] | None = None


def _require_reader(deps: KnowledgeDeps) -> Any:
    if deps.rag is None:
        raise HTTPException(status_code=503, detail=_UNCONFIGURED_DETAIL)
    return deps.rag


def _clean_q(q: str | None) -> str:
    """Normalize the free-text search param: a blank query is a 422 (search needs a term);
    anything longer than MAX_Q_LEN is refused loudly (422), never truncated into a different
    query."""
    if q is None or not q.strip():
        raise HTTPException(status_code=422, detail="q (a search query) is required")
    if len(q) > MAX_Q_LEN:
        raise HTTPException(status_code=422, detail=f"q must be at most {MAX_Q_LEN} characters")
    return q.strip()


def _iso(value: Any) -> str | None:
    """Serialize the inventory's MAX(created_at) timestamp; tolerate fakes that pass strings."""
    if value is None:
        return None
    iso = getattr(value, "isoformat", None)
    return iso() if callable(iso) else str(value)


def _snippet(content: Any) -> str:
    """A bounded display snippet of a matched document — never the full content dump."""
    text = "" if content is None else str(content)
    text = " ".join(text.split())  # collapse whitespace so the snippet reads as one line
    return text if len(text) <= SNIPPET_LEN else text[: SNIPPET_LEN - 1].rstrip() + "…"


def _check_ref(ref_id: str) -> str:
    """An uploaded-document ref must match the ingest scheme exactly — anything else is a 422
    before it can reach the reader (it could only ever be a typo or smuggling attempt)."""
    if not _REF_RE.match(ref_id or ""):
        raise HTTPException(status_code=422, detail=DETAIL_BAD_REF)
    return ref_id


def _parse_raw(raw: str) -> tuple[str, str]:
    """Split a raw-original row back into (title, body). The writer (ingest/upload.py)
    normalizes the title to one line and joins with a blank line, so the FIRST paragraph
    break is the unambiguous separator."""
    head, sep, body = raw.partition("\n\n")
    title = head.strip()
    return (title or "Untitled", body if sep else "")


def _title_from_prefix(ref_prefix: str) -> str:
    """Legacy fallback (no raw row): de-slug the ref into a title. Handles both real shapes —
    'upload:pricing-policy-ab12cd34' -> 'Pricing policy' (content-hash suffix stripped) and
    'demo:kb:pricing-discount-authority' -> 'Pricing discount authority'. Lossy but honest —
    clearly better than 'Untitled'."""
    tail = ref_prefix.rsplit(":", 1)[-1]
    tail = _HASH8_RE.sub("", tail)
    words = [w for w in tail.split("-") if w]
    return " ".join(words).capitalize() if words else "Untitled"


def _preview(text: str) -> str:
    """A bounded one-line body preview for the pages list."""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= PREVIEW_LEN else flat[: PREVIEW_LEN - 1].rstrip() + "…"


def _doc_summary(row: dict) -> dict:
    """Shape one list_uploaded_documents row for the wire: title/preview out of the bounded
    raw head when it exists; the legacy de-slug fallback (editable: false) when it doesn't."""
    raw_head = row.get("raw_head")
    if raw_head:
        title, body = _parse_raw(str(raw_head))
        editable = True
    else:
        title, body = _title_from_prefix(str(row.get("ref_id") or "")), ""
        editable = False
    return {
        "ref_id": row.get("ref_id"),
        "title": title,
        "preview": _preview(body),
        "chunks": int(row.get("chunk_count") or 0),
        "editable": editable,
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def mount_knowledge(app: FastAPI, deps: KnowledgeDeps, current_tenant) -> None:
    """Mount the /knowledge routes on `app`, authed via `current_tenant` (the same verified-claims
    dependency every other authed route uses). The only writes are the tenant's OWN corpus
    (add/edit/delete an uploaded page) — nothing here sends anything, so no Greenlight gate;
    same openness tier as POST /knowledge/documents has had since the knowledge P0s."""

    @app.get("/knowledge")
    def knowledge_inventory(claims: TenantClaims = Depends(current_tenant)):
        rag = _require_reader(deps)
        rows = rag.list_document_inventory(tenant_id=claims.tenant_id)
        sources = [
            {
                "source": r.get("source"),
                "document_count": int(r.get("document_count") or 0),
                "last_updated": _iso(r.get("last_updated")),
            }
            for r in rows
        ]
        total = sum(s["document_count"] for s in sources)
        return {
            "sources": sources,
            "source_count": len(sources),
            "total_documents": total,
        }

    @app.get("/knowledge/search")
    def knowledge_search(q: str | None = None, limit: int = DEFAULT_SEARCH_LIMIT,
                         offset: int = 0,
                         claims: TenantClaims = Depends(current_tenant)):
        rag = _require_reader(deps)
        query = _clean_q(q)
        n = max(1, min(int(limit), MAX_SEARCH_LIMIT))
        # Paging (the P2 note): a bounded OFFSET into the ranked scan. Clamped here AND in the
        # reader; `next_offset` is null once a short page signals the end of the corpus.
        start = max(0, min(int(offset), MAX_SEARCH_OFFSET))
        try:
            hits = rag.search(tenant_id=claims.tenant_id, query=query, limit=n, offset=start)
        except EmbedderUnavailable as exc:
            # The query embedder (Bedrock/Titan) is env-key-gated on the live task; an embed
            # failure degrades to an honest 200 with the "warming up" story, never a 500 and
            # never a leaked AWS error string. The SERVER LOG carries the REAL reason
            # (message + traceback) — server-only, so detail here is safe.
            log.warning("knowledge: query embedder unavailable for tenant %s: %s",
                        claims.tenant_id, exc, exc_info=True)
            return {"query": query, "results": [], "search_available": False,
                    "reason": REASON_SEARCH_UNAVAILABLE,
                    "reason_code": REASON_CODE_EMBEDDER,
                    "offset": start, "next_offset": None}
        except Exception as exc:  # noqa: BLE001 — anything AFTER the embed (DB read/pool) is a
            # TRANSIENT failure, not the embedder story (knowledge audit P1: the UI must not
            # say "warming up" forever over a Postgres outage). Same honesty rules: 200 +
            # search_available:false, generic wire string, real reason in the server log only.
            log.warning("knowledge: search failed for tenant %s: %s: %s",
                        claims.tenant_id, type(exc).__name__, exc, exc_info=True)
            return {"query": query, "results": [], "search_available": False,
                    "reason": REASON_SEARCH_FAILED,
                    "reason_code": REASON_CODE_SEARCH_ERROR,
                    "offset": start, "next_offset": None}
        results = [
            {
                "ref_id": h.get("ref_id"),
                "source": h.get("source"),
                "snippet": _snippet(h.get("content")),
                "score": round(float(h["score"]), 4) if h.get("score") is not None else None,
            }
            for h in (hits or [])
        ]
        # A full page means there MAY be more (the next fetch finding zero ends it honestly);
        # a short page IS the end. The cap stops "show more" from scanning forever.
        more = len(results) == n and (start + n) < MAX_SEARCH_OFFSET
        return {"query": query, "results": results, "search_available": True, "reason": None,
                "reason_code": None, "offset": start,
                "next_offset": (start + n) if more else None}

    @app.post("/knowledge/documents", status_code=201)
    def knowledge_add_document(body: AddDocumentBody,
                               claims: TenantClaims = Depends(current_tenant)):
        """Add one document to the tenant's corpus (paste/upload). Tenant comes ONLY from the
        verified claims (THE TRUST RULE — pydantic ignores any smuggled body keys). The ingest
        seam embeds every chunk before the first upsert, so a failure lands NOTHING — and is a
        loud 503 here, never a quiet 200."""
        if deps.ingest_document is None:
            raise HTTPException(status_code=503, detail=_UNCONFIGURED_UPLOAD_DETAIL)
        title, content = _clean_doc(body)
        try:
            out = deps.ingest_document(claims.tenant_id, title, content)
        except ValueError as exc:
            # The seam re-validates (one source of truth) — surface its message, it is ours.
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except Exception as exc:  # noqa: BLE001 — embedder/DB failure: loud, but never the raw
            # error string (it can carry AWS detail). Log the TYPE; the body says it failed.
            log.error("knowledge: document ingest failed (%s)", type(exc).__name__)
            raise HTTPException(status_code=503, detail=REASON_UPLOAD_FAILED) from None
        return {"ref_id": out.get("ref_id"), "chunks": out.get("chunks"),
                "source": out.get("source"), "title": out.get("title")}

    @app.get("/knowledge/documents")
    def knowledge_list_documents(claims: TenantClaims = Depends(current_tenant)):
        """The tenant's pages. A plain RLS-scoped aggregate (no embedder) — an un-uploaded
        tenant gets an honest empty list, never invented pages. Each page carries its
        ORGANIZATION (parent_ref + sort_order from knowledge_pages; absent row = top level,
        default order) and the response says whether organizing is available at all
        (`organize_available: false` until the migration runs — the client renders the same
        honest flat list it always has)."""
        rag = _require_reader(deps)
        rows = rag.list_uploaded_documents(tenant_id=claims.tenant_id)
        meta = rag.list_page_meta(tenant_id=claims.tenant_id)
        docs = []
        for r in rows:
            d = _doc_summary(r)
            m = (meta or {}).get(str(d.get("ref_id") or ""), {})
            d["parent_ref"] = m.get("parent_ref")
            d["sort_order"] = float(m.get("sort_order") or 0)
            docs.append(d)
        return {"documents": docs, "total": len(docs),
                "organize_available": meta is not None}

    @app.patch("/knowledge/documents/{ref_id}/location")
    def knowledge_move_document(ref_id: str, body: LocationBody,
                                claims: TenantClaims = Depends(current_tenant)):
        """Organize one page: re-parent it (`parent_ref`: a page ref or null for top level)
        OR nudge it within its siblings (`move`: "up" | "down") — one operation per call.
        Cycles are refused (a page can never become its own ancestor), unknown parents are
        refused, and a database that predates the knowledge_pages migration answers an
        honest 503 — never a 500, never a silently-dropped move."""
        rag = _require_reader(deps)
        ref = _check_ref(ref_id)
        reparenting = "parent_ref" in body.model_fields_set
        if reparenting == (body.move is not None):
            raise HTTPException(status_code=422, detail=DETAIL_ORGANIZE_ONE_OP)
        if body.move is not None and body.move not in ("up", "down"):
            raise HTTPException(status_code=422,
                                detail='move must be "up" or "down"')

        rows = rag.list_uploaded_documents(tenant_id=claims.tenant_id)
        refs = [str(r.get("ref_id") or "") for r in rows]
        if ref not in refs:
            raise HTTPException(status_code=404, detail=DETAIL_DOC_NOT_FOUND)
        meta = rag.list_page_meta(tenant_id=claims.tenant_id)
        if meta is None:
            raise HTTPException(status_code=503, detail=DETAIL_ORGANIZE_UNAVAILABLE)

        def parent_of(r: str) -> str | None:
            return (meta.get(r) or {}).get("parent_ref")

        try:
            if reparenting:
                parent = body.parent_ref
                if parent is not None:
                    parent = _check_ref(parent)
                    if parent == ref:
                        raise HTTPException(status_code=422, detail=DETAIL_ORGANIZE_CYCLE)
                    if parent not in refs:
                        raise HTTPException(status_code=422,
                                            detail="parent page not found in your knowledge base")
                    # Walk the would-be ancestor chain: hitting `ref` means the page would
                    # become its own ancestor. Bounded — meta is finite and cycles can't
                    # already exist, but never trust a walk without a leash.
                    seen, cur = 0, parent_of(parent)
                    while cur is not None and seen < 1000:
                        if cur == ref:
                            raise HTTPException(status_code=422, detail=DETAIL_ORGANIZE_CYCLE)
                        cur = parent_of(cur)
                        seen += 1
                # Land at the END of the new sibling group.
                group = [m for m in meta.values() if m.get("parent_ref") == parent]
                order = max((float(m.get("sort_order") or 0) for m in group), default=0.0) + 1.0
                rag.set_page_location(tenant_id=claims.tenant_id, ref_prefix=ref,
                                      parent_ref=parent, sort_order=order)
                return {"ref_id": ref, "parent_ref": parent, "sort_order": order,
                        "organize_available": True}

            # move up/down within the CURRENT sibling group. Effective order mirrors the
            # client: (sort_order, list position) — list position breaks the all-zeroes tie
            # for never-organized pages (newest first, the SQL order).
            parent = parent_of(ref)
            sibs = [r for r in refs if parent_of(r) == parent]
            ordered = sorted(sibs, key=lambda r: (float((meta.get(r) or {}).get("sort_order") or 0),
                                                  refs.index(r)))
            i = ordered.index(ref)
            j = i - 1 if body.move == "up" else i + 1
            if j < 0 or j >= len(ordered):
                # Already at the edge — honest no-op, not an error.
                return {"ref_id": ref, "parent_ref": parent,
                        "sort_order": float((meta.get(ref) or {}).get("sort_order") or 0),
                        "organize_available": True}
            ordered[i], ordered[j] = ordered[j], ordered[i]
            # Materialize the whole group's order (integers by position) so the swap is
            # stable even when every row still carried the default 0.
            for pos, r in enumerate(ordered):
                rag.set_page_location(tenant_id=claims.tenant_id, ref_prefix=r,
                                      parent_ref=parent, sort_order=float(pos))
            return {"ref_id": ref, "parent_ref": parent, "sort_order": float(j),
                    "organize_available": True}
        except PageOrganizeUnavailable:
            raise HTTPException(status_code=503, detail=DETAIL_ORGANIZE_UNAVAILABLE) from None

    @app.get("/knowledge/documents/{ref_id}")
    def knowledge_get_document(ref_id: str,
                               claims: TenantClaims = Depends(current_tenant)):
        """One page in full. The raw original (title + exact body) when it exists; a legacy
        upload degrades honestly to its indexed chunk texts, read-only — never invented
        content, never another tenant's rows (RLS)."""
        rag = _require_reader(deps)
        ref = _check_ref(ref_id)
        doc = rag.get_uploaded_document(tenant_id=claims.tenant_id, ref_prefix=ref)
        if doc is None:
            raise HTTPException(status_code=404, detail=DETAIL_DOC_NOT_FOUND)
        raw = doc.get("raw_content")
        if raw:
            title, body = _parse_raw(str(raw))
            return {"ref_id": ref, "title": title, "content": body, "editable": True,
                    "sections": None, "chunks": int(doc.get("chunk_count") or 0),
                    "created_at": _iso(doc.get("created_at")),
                    "updated_at": _iso(doc.get("updated_at"))}
        return {"ref_id": ref, "title": _title_from_prefix(ref), "content": None,
                "editable": False,
                "sections": [str(c) for c in (doc.get("chunk_contents") or [])],
                "chunks": int(doc.get("chunk_count") or 0),
                "created_at": _iso(doc.get("created_at")),
                "updated_at": _iso(doc.get("updated_at"))}

    @app.put("/knowledge/documents/{ref_id}")
    def knowledge_update_document(ref_id: str, body: AddDocumentBody,
                                  claims: TenantClaims = Depends(current_tenant)):
        """Edit a page: re-ingest through the SAME seam as POST, then remove the old namespace.
        Order is deliberate — the new version lands fully BEFORE the old one is touched, so a
        mid-edit failure can leave a duplicate (visible, deletable), never a lost document.
        When the edit produces the same namespace (unchanged content), the upsert was in place
        and there is nothing to delete."""
        rag = _require_reader(deps)
        if deps.ingest_document is None:
            raise HTTPException(status_code=503, detail=_UNCONFIGURED_UPLOAD_DETAIL)
        ref = _check_ref(ref_id)
        title, content = _clean_doc(body)
        existing = rag.get_uploaded_document(tenant_id=claims.tenant_id, ref_prefix=ref)
        if existing is None:
            raise HTTPException(status_code=404, detail=DETAIL_DOC_NOT_FOUND)
        if not existing.get("raw_content"):
            raise HTTPException(status_code=409, detail=DETAIL_DOC_NOT_EDITABLE)
        try:
            out = deps.ingest_document(claims.tenant_id, title, content)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except Exception as exc:  # noqa: BLE001 — same loud-write contract as POST
            log.error("knowledge: document re-ingest failed (%s)", type(exc).__name__)
            raise HTTPException(status_code=503, detail=REASON_UPLOAD_FAILED) from None
        new_ref = str(out.get("ref_id") or "")
        previous_removed = True
        if new_ref != ref:
            try:
                rag.delete_uploaded_document(tenant_id=claims.tenant_id, ref_prefix=ref)
            except Exception as exc:  # noqa: BLE001 — the NEW version already landed; deleting
                # the old one failing must not turn a successful edit into a reported failure.
                # Honest signal instead: previous_removed=false (the old page is still listed).
                log.error("knowledge: stale namespace cleanup failed (%s)", type(exc).__name__)
                previous_removed = False
            try:
                # Carry the page's spot in the tree to its new ref (and re-point children).
                # Best-effort: a failure leaves the page top-level, never blocks the edit.
                rag.migrate_page_ref(tenant_id=claims.tenant_id, old_ref=ref, new_ref=new_ref)
            except Exception as exc:  # noqa: BLE001
                log.error("knowledge: page-location carry failed (%s)", type(exc).__name__)
        return {"ref_id": new_ref, "chunks": out.get("chunks"), "source": out.get("source"),
                "title": out.get("title"), "replaced_ref_id": ref,
                "previous_removed": previous_removed}

    @app.delete("/knowledge/documents/{ref_id}")
    def knowledge_delete_document(ref_id: str,
                                  claims: TenantClaims = Depends(current_tenant)):
        """Remove a page — every row under the ref namespace (chunks + raw) in one RLS-scoped
        transaction. 404 when nothing existed for THIS tenant (another tenant's ref deletes
        nothing — RLS sees zero rows)."""
        rag = _require_reader(deps)
        ref = _check_ref(ref_id)
        removed = int(rag.delete_uploaded_document(tenant_id=claims.tenant_id,
                                                   ref_prefix=ref) or 0)
        if removed == 0:
            raise HTTPException(status_code=404, detail=DETAIL_DOC_NOT_FOUND)
        try:
            # Children move up to the deleted page's parent; its own location row goes.
            # Best-effort: a failure leaves orphans rendered top-level, never blocks delete.
            rag.delete_page_meta(tenant_id=claims.tenant_id, ref_prefix=ref)
        except Exception as exc:  # noqa: BLE001
            log.error("knowledge: page-location cleanup failed (%s)", type(exc).__name__)
        return {"ref_id": ref, "deleted": True, "rows_removed": removed}


class AddDocumentBody(BaseModel):
    title: str
    content: str


class LocationBody(BaseModel):
    """PATCH /knowledge/documents/{ref}/location — exactly ONE of the two operations.
    `parent_ref` is meaningful even as null (move to top level), so its presence is read
    from model_fields_set, not its value."""
    parent_ref: str | None = None
    move: str | None = None


def _clean_doc(body: AddDocumentBody) -> tuple[str, str]:
    """Bound + strip the upload fields: blank or oversize input is a 422, never truncated."""
    title = body.title.strip()
    content = body.content.strip()
    if not title:
        raise HTTPException(status_code=422, detail="title is required")
    if len(title) > MAX_TITLE_LEN:
        raise HTTPException(status_code=422, detail=f"title must be at most {MAX_TITLE_LEN} characters")
    if not content:
        raise HTTPException(status_code=422, detail="content (the document text) is required")
    if len(content) > MAX_DOC_CHARS:
        raise HTTPException(status_code=422, detail=f"content must be at most {MAX_DOC_CHARS} characters")
    return title, content


def build_doc_ingestor() -> Callable[[str, str, str], Any] | None:
    """The default document ingestor — wired ONLY under the ingest plane's own deliberate
    master switch (INGEST_REAL_STORES), the same rationale as the CSV importer: "uploading"
    into an unswitched in-memory store would succeed while discarding the document. Lazy AND
    absence-tolerant: no ingest/ in the image = no ingestor = the route answers its honest 503."""
    try:
        from ingest.run_sync import real_mode  # noqa: PLC0415
    except ImportError:
        return None

    if not real_mode():
        return None

    def run(tenant_id: str, title: str, content: str) -> Any:
        # tenant_id arrives from the VERIFIED claim (threaded by the route).
        from ingest.run_sync import build_embedder, build_stores  # noqa: PLC0415 — boto3/
        from ingest.upload import ingest_document  # noqa: PLC0415 — psycopg2 at call time only

        store, _cursors = build_stores()
        return ingest_document(store, build_embedder(),
                               tenant_id=tenant_id, title=title, content=content)

    return run
