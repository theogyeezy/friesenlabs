"""Authed per-tenant chat conversations — multi-thread history + rename.

A conversation is one named chat thread. The transcript (conversation_messages) is the display
history; `conversations.session_id` binds the thread to its own Managed-Agents session (resolved by
the conversation-scoped factory). These are DIRECT user reads/writes (listing, renaming, archiving
threads) — they do NOT route through Greenlight; the agent TURN itself (POST /chat) keeps its own
kill-switch + draft-only gates.

Tenancy: THE TRUST RULE (tenant from the verified claim only, never the body), RLS via the store's
SET LOCAL transaction. The store returns curated, tenant_id-free rows, so RLS scoping IS the guard.

Deps follow the INERT-default contract (TasksDeps/ContactsDeps): the all-None stub mounts honest-503
routes and constructing deps never opens a DB pool. api/asgi.py is the only real wiring.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from api.auth import TenantClaims

log = logging.getLogger("api.conversations_routes")

_UNCONFIGURED_DETAIL = (
    "chat history not configured — no crm_app DSN on this task "
    "(DB_*/UPLIFT_DB_URL unset); conversations are unavailable"
)

MAX_TITLE_LEN = 200
DEFAULT_PAGE = 50
MAX_PAGE = 100
MAX_MESSAGES = 500
MAX_OFFSET = 100_000
CONVERSATION_SCOPES = ("active", "archived")


@dataclass
class ConversationsDeps:
    # A PgConversationStore-shaped store (create / list / get / rename / set_archived /
    # list_messages). None = data plane unconfigured -> every endpoint answers the honest 503.
    store: Any | None = None


def _require(deps: ConversationsDeps) -> Any:
    if deps.store is None:
        raise HTTPException(status_code=503, detail=_UNCONFIGURED_DETAIL)
    return deps.store


def _valid_id_or_404(value: str) -> str:
    """Path ids must be uuids (the schema PK type). A malformed id is indistinguishable from a
    missing row to the caller — 404, never a 500."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail="no such conversation")


def _clamp(limit: int, offset: int, max_page: int) -> tuple[int, int]:
    return max(1, min(int(limit), max_page)), max(0, min(int(offset), MAX_OFFSET))


class CreateConversationBody(BaseModel):
    title: str | None = None        # optional; blank/None => the UI shows a default label


class RenameConversationBody(BaseModel):
    title: str                      # must be non-empty (validated below)


def mount_conversations(app: FastAPI, deps: ConversationsDeps, current_tenant) -> None:
    """Mount the /conversations routes on `app`, authed via `current_tenant`."""

    @app.get("/conversations")
    def list_conversations(scope: str = "active", limit: int = DEFAULT_PAGE, offset: int = 0,
                           claims: TenantClaims = Depends(current_tenant)):
        store = _require(deps)
        scope = (scope or "active").strip().lower()
        if scope not in CONVERSATION_SCOPES:
            raise HTTPException(status_code=422,
                                detail=f"scope must be one of {', '.join(CONVERSATION_SCOPES)}")
        n, off = _clamp(limit, offset, MAX_PAGE)
        rows = store.list(tenant_id=claims.tenant_id, scope=scope, limit=n + 1, offset=off)
        has_more = len(rows) > n
        return {"conversations": rows[:n], "count": min(len(rows), n),
                "has_more": has_more, "limit": n, "offset": off, "scope": scope}

    @app.post("/conversations", status_code=201)
    def create_conversation(body: CreateConversationBody,
                            claims: TenantClaims = Depends(current_tenant)):
        store = _require(deps)
        title = (body.title or "").strip() or None
        if title and len(title) > MAX_TITLE_LEN:
            raise HTTPException(status_code=422,
                                detail=f"title must be at most {MAX_TITLE_LEN} characters")
        row = store.create(tenant_id=claims.tenant_id, title=title,
                           created_by=getattr(claims, "sub", None))
        return {"conversation": row}

    @app.get("/conversations/{conversation_id}/messages")
    def conversation_messages(conversation_id: str, limit: int = MAX_MESSAGES, offset: int = 0,
                              claims: TenantClaims = Depends(current_tenant)):
        store = _require(deps)
        cid = _valid_id_or_404(conversation_id)
        if store.get(claims.tenant_id, cid) is None:
            raise HTTPException(status_code=404, detail="no such conversation")
        n, off = _clamp(limit, offset, MAX_MESSAGES)
        msgs = store.list_messages(tenant_id=claims.tenant_id, conversation_id=cid,
                                   limit=n, offset=off)
        return {"conversation_id": cid, "messages": msgs}

    @app.patch("/conversations/{conversation_id}")
    def rename_conversation(conversation_id: str, body: RenameConversationBody,
                            claims: TenantClaims = Depends(current_tenant)):
        store = _require(deps)
        cid = _valid_id_or_404(conversation_id)
        title = (body.title or "").strip()
        if not title:
            raise HTTPException(status_code=422, detail="title must be non-empty")
        if len(title) > MAX_TITLE_LEN:
            raise HTTPException(status_code=422,
                                detail=f"title must be at most {MAX_TITLE_LEN} characters")
        row = store.rename(tenant_id=claims.tenant_id, conversation_id=cid, title=title)
        if row is None:
            raise HTTPException(status_code=404, detail="no such conversation")
        return {"conversation": row}

    @app.post("/conversations/{conversation_id}/archive")
    def archive_conversation(conversation_id: str,
                             claims: TenantClaims = Depends(current_tenant)):
        store = _require(deps)
        cid = _valid_id_or_404(conversation_id)
        row = store.set_archived(tenant_id=claims.tenant_id, conversation_id=cid, archived=True)
        if row is None:
            raise HTTPException(status_code=404, detail="no such conversation")
        return {"conversation": row}

    @app.post("/conversations/{conversation_id}/unarchive")
    def unarchive_conversation(conversation_id: str,
                               claims: TenantClaims = Depends(current_tenant)):
        store = _require(deps)
        cid = _valid_id_or_404(conversation_id)
        row = store.set_archived(tenant_id=claims.tenant_id, conversation_id=cid, archived=False)
        if row is None:
            raise HTTPException(status_code=404, detail="no such conversation")
        return {"conversation": row}
