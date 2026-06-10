"""Authed per-tenant knowledge endpoints — the api half of the real Knowledge tab
(the sixth honest-stub tab converted to REAL, after Pipeline + Contacts + Agents + Workflows +
Reports; the web half is web/src/api/KnowledgeView.tsx).

Two endpoints, both READ-ONLY and bound to the VERIFIED JWT claims (THE TRUST RULE — tenant
never from a header or the request body):

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
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from api.auth import TenantClaims

log = logging.getLogger("api.knowledge")

_UNCONFIGURED_DETAIL = (
    "knowledge data plane not configured — no crm_app DSN on this task "
    "(DB_*/UPLIFT_DB_URL unset); the knowledge base is unavailable"
)

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
            # 500 and never a leaked AWS error string. Log the TYPE only (it can carry detail).
            log.warning("knowledge: search failed (%s)", type(exc).__name__)
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
