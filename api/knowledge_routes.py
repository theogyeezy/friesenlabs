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
                          source='upload' with deterministic upload:<slug>-<hash8>#<seq> refs.
                          Gated on the ingest plane's INGEST_REAL_STORES switch
                          (build_doc_ingestor): unswitched -> honest 503; an ingest failure is
                          a LOUD 503 (never search's quiet degrade — a write must not no-op).

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
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from api.auth import TenantClaims

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

# How much of a matched document's content leaves the API per hit. A snippet for display — never
# the full document dump (keeps the payload bounded and avoids over-exposing a long record).
SNIPPET_LEN = 320

# The honest degrade reason when the query embedder/model isn't reachable (Bedrock/Titan
# env-key-gated on the live task today). The web banner keys off `search_available`, not this
# string, but the integration test pins it so the operator story stays stable.
REASON_SEARCH_UNAVAILABLE = "search model not configured"


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


def mount_knowledge(app: FastAPI, deps: KnowledgeDeps, current_tenant) -> None:
    """Mount the /knowledge routes on `app`, authed via `current_tenant` (the same verified-claims
    dependency every other authed route uses). Read-only: no gate deps — nothing here mutates."""

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
                         claims: TenantClaims = Depends(current_tenant)):
        rag = _require_reader(deps)
        query = _clean_q(q)
        n = max(1, min(int(limit), MAX_SEARCH_LIMIT))
        try:
            hits = rag.search(tenant_id=claims.tenant_id, query=query, limit=n)
        except Exception as exc:  # noqa: BLE001 — the query embedder (Bedrock/Titan) is env-key
            # gated on the live task; any embed/model failure degrades to an honest 200, never a
            # 500 and never a leaked AWS error string. The WIRE response stays generic
            # (search_available:false + REASON_SEARCH_UNAVAILABLE); but the SERVER LOG must carry
            # the REAL reason (message + traceback), not just the exception type — otherwise an
            # operator can't tell a missing Bedrock key from a model error from a Postgres outage.
            # The log is server-only (never returned to the tenant), so detail here is safe.
            log.warning("knowledge: search failed for tenant %s: %s: %s",
                        claims.tenant_id, type(exc).__name__, exc, exc_info=True)
            return {"query": query, "results": [], "search_available": False,
                    "reason": REASON_SEARCH_UNAVAILABLE}
        results = [
            {
                "ref_id": h.get("ref_id"),
                "source": h.get("source"),
                "snippet": _snippet(h.get("content")),
                "score": round(float(h["score"]), 4) if h.get("score") is not None else None,
            }
            for h in (hits or [])
        ]
        return {"query": query, "results": results, "search_available": True, "reason": None}

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


class AddDocumentBody(BaseModel):
    title: str
    content: str


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
