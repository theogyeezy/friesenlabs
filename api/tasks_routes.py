"""Authed per-tenant CRM tasks (follow-up reminders with due dates) — CRM-depth #14.

A task is a small piece of work to remember ("Call back Tuesday"), optionally linked to a contact
and/or a deal so it surfaces on those drawers. Tasks are a DIRECT user write (not an agent send —
nothing leaves the system), so they do NOT route through Greenlight; tenancy is enforced exactly
like the rest of the CRM surface: THE TRUST RULE (tenant from the verified claim only, never the
body), RLS via the PgCrmClient SET LOCAL transaction, and the defense-in-depth `_checked_rows`
strip of any row whose tenant_id isn't the request tenant.

The deps follow the same INERT-default contract as ContactsDeps/DealsDeps: the all-None stub mounts
honest-503 routes and constructing deps never opens a DB pool. api/asgi.py is the ONLY real wiring
(the SAME PgCrmClient instance the directory + board ride).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from api.auth import TenantClaims

log = logging.getLogger("api.tasks_routes")

_UNCONFIGURED_DETAIL = (
    "tasks data plane not configured — no crm_app DSN on this task "
    "(DB_*/UPLIFT_DB_URL unset); CRM tasks are unavailable"
)

# Title length cap — a follow-up reminder, not a document. Longer is a 422, never truncated.
MAX_TITLE_LEN = 500

# The task scopes the list endpoint accepts (mirrors PgCrmClient.list_tasks). A junk scope is a
# 422 here, never a silent fall-through to a different query.
TASK_SCOPES = ("open", "overdue", "done", "all", "archived")

DEFAULT_PAGE = 50
MAX_PAGE = 200
MAX_OFFSET = 100_000


@dataclass
class TasksDeps:
    # A PgCrmClient-shaped client (insert_task / list_tasks / get_task / count_open_tasks /
    # update_task_fields / set_task_done / set_archived). None = data plane unconfigured -> every
    # endpoint answers the honest 503, never invented rows.
    crm: Any | None = None


def _require(deps: TasksDeps) -> Any:
    if deps.crm is None:
        raise HTTPException(status_code=503, detail=_UNCONFIGURED_DETAIL)
    return deps.crm


def _valid_id_or_404(value: str, *, kind: str) -> str:
    """Path/link ids must be uuids (the schema PK type). A malformed id is indistinguishable from
    a missing row to the caller — 404, tenant-scoped semantics, never a 500."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail=f"no such {kind}")


def _clamp_page(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(int(limit), MAX_PAGE)), max(0, min(int(offset), MAX_OFFSET))


def _checked_rows(rows: list[dict], tenant_id: str) -> list[dict]:
    """Defense in depth (the contacts/deals pattern): RLS already scopes the read, but never let
    a row whose tenant_id isn't the verified request tenant leave the API — a silent leak fails
    loud (500). The internal tenant_id is then stripped from the payload."""
    out = []
    for r in rows:
        if str(r.get("tenant_id")) != str(tenant_id):
            raise HTTPException(status_code=500, detail="tenant isolation violation")
        out.append({k: v for k, v in r.items() if k != "tenant_id"})
    return out


# --------------------------------------------------------------------------- #
# Request bodies — write paths. THE TRUST RULE: no tenant_id field anywhere.
# --------------------------------------------------------------------------- #
class CreateTaskBody(BaseModel):
    title: str
    due_at: str | None = None        # ISO timestamp; PG casts to timestamptz. None = no due date.
    contact_id: str | None = None    # optional link — surfaces the task on the contact drawer
    deal_id: str | None = None       # optional link — surfaces the task on the deal drawer


class EditTaskBody(BaseModel):
    # Only the editable fields. A `due_at` of "" (empty/blank) CLEARS the date (set to null);
    # omitting it (None) leaves it unchanged. title, when given, must be non-empty.
    title: str | None = None
    due_at: str | None = None


def _validate_links(crm: Any, tenant_id: str, contact_id: str | None,
                    deal_id: str | None) -> tuple[str | None, str | None]:
    """Resolve + existence-check the optional contact/deal links so a bad id is a clean 404, not
    an opaque FK 500 from the composite same-tenant FK (the last line of defense). RLS scopes the
    lookups to the verified tenant — a link to ANOTHER tenant's row reads as missing (404)."""
    cid: str | None = None
    did: str | None = None
    if contact_id:
        cid = _valid_id_or_404(contact_id, kind="contact")
        if crm.get_contact_directory(tenant_id=tenant_id, contact_id=cid) is None:
            raise HTTPException(status_code=404, detail="no such contact")
    if deal_id:
        did = _valid_id_or_404(deal_id, kind="deal")
        if crm.get_deal_board(tenant_id=tenant_id, deal_id=did) is None:
            raise HTTPException(status_code=404, detail="no such deal")
    return cid, did


def mount_tasks(app: FastAPI, deps: TasksDeps, current_tenant) -> None:
    """Mount the /tasks routes on `app`, authed via `current_tenant` (the same verified-claims
    dependency every other authed route uses)."""

    @app.get("/tasks")
    def list_tasks(scope: str = "open", contact_id: str | None = None,
                   deal_id: str | None = None, limit: int = DEFAULT_PAGE, offset: int = 0,
                   claims: TenantClaims = Depends(current_tenant)):
        crm = _require(deps)
        scope = (scope or "open").strip().lower()
        if scope not in TASK_SCOPES:
            raise HTTPException(status_code=422,
                                detail=f"scope must be one of {', '.join(TASK_SCOPES)}")
        cid = _valid_id_or_404(contact_id, kind="contact") if contact_id else None
        did = _valid_id_or_404(deal_id, kind="deal") if deal_id else None
        n, off = _clamp_page(limit, offset)
        rows = _checked_rows(
            crm.list_tasks(tenant_id=claims.tenant_id, scope=scope,
                           contact_id=cid, deal_id=did, limit=n + 1, offset=off),
            claims.tenant_id,
        )
        has_more = len(rows) > n
        counts = crm.count_open_tasks(tenant_id=claims.tenant_id)
        return {
            "tasks": rows[:n],
            "count": min(len(rows), n),
            "has_more": has_more,
            "limit": n,
            "offset": off,
            "scope": scope,
            "open_count": counts.get("open_count", 0),
            "overdue_count": counts.get("overdue_count", 0),
        }

    @app.post("/tasks", status_code=201)
    def create_task(body: CreateTaskBody, claims: TenantClaims = Depends(current_tenant)):
        crm = _require(deps)
        title = (body.title or "").strip()
        if not title:
            raise HTTPException(status_code=422, detail="title must be non-empty")
        if len(title) > MAX_TITLE_LEN:
            raise HTTPException(status_code=422,
                                detail=f"title must be at most {MAX_TITLE_LEN} characters")
        cid, did = _validate_links(crm, claims.tenant_id, body.contact_id, body.deal_id)
        row = crm.insert_task(
            tenant_id=claims.tenant_id, title=title,
            due_at=(body.due_at or "").strip() or None,
            contact_id=cid, deal_id=did, created_by=getattr(claims, "sub", None),
        )
        return {"task": _checked_rows([row], claims.tenant_id)[0]}

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str, claims: TenantClaims = Depends(current_tenant)):
        crm = _require(deps)
        tid = _valid_id_or_404(task_id, kind="task")
        row = crm.get_task(tenant_id=claims.tenant_id, task_id=tid)
        if row is None:
            raise HTTPException(status_code=404, detail="no such task")
        return {"task": _checked_rows([row], claims.tenant_id)[0]}

    @app.patch("/tasks/{task_id}")
    def edit_task(task_id: str, body: EditTaskBody,
                  claims: TenantClaims = Depends(current_tenant)):
        crm = _require(deps)
        tid = _valid_id_or_404(task_id, kind="task")
        changes: dict = {}
        if body.title is not None:
            title = body.title.strip()
            if not title:
                raise HTTPException(status_code=422,
                                    detail="title must be non-empty when provided")
            if len(title) > MAX_TITLE_LEN:
                raise HTTPException(status_code=422,
                                    detail=f"title must be at most {MAX_TITLE_LEN} characters")
            changes["title"] = title
        if body.due_at is not None:
            # "" / blank clears the date; otherwise the ISO string is bound (PG casts it).
            changes["due_at"] = body.due_at.strip() or None
        if not changes:
            raise HTTPException(status_code=422, detail="at least one field must be provided")
        try:
            result = crm.update_task_fields(tenant_id=claims.tenant_id, task_id=tid,
                                            changes=changes)
        except ValueError as exc:
            if "not found" in str(exc):
                raise HTTPException(status_code=404, detail="no such task")
            raise HTTPException(status_code=422, detail=str(exc))
        result["task"] = _checked_rows([result["task"]], claims.tenant_id)[0]
        return result

    def _set_done(task_id: str, done: bool, claims) -> dict:
        crm = _require(deps)
        tid = _valid_id_or_404(task_id, kind="task")
        try:
            row = crm.set_task_done(tenant_id=claims.tenant_id, task_id=tid, done=done)
        except ValueError:
            raise HTTPException(status_code=404, detail="no such task")
        return {"task": _checked_rows([row], claims.tenant_id)[0]}

    @app.post("/tasks/{task_id}/complete")
    def complete_task(task_id: str, claims: TenantClaims = Depends(current_tenant)):
        return _set_done(task_id, True, claims)

    @app.post("/tasks/{task_id}/reopen")
    def reopen_task(task_id: str, claims: TenantClaims = Depends(current_tenant)):
        return _set_done(task_id, False, claims)

    def _archive(task_id: str, archived: bool, claims) -> dict:
        crm = _require(deps)
        tid = _valid_id_or_404(task_id, kind="task")
        try:
            return crm.set_archived(tenant_id=claims.tenant_id, table="tasks",
                                    entity_id=tid, archived=archived)
        except ValueError:
            raise HTTPException(status_code=404, detail="no such task")

    @app.post("/tasks/{task_id}/archive")
    def archive_task(task_id: str, claims: TenantClaims = Depends(current_tenant)):
        return _archive(task_id, True, claims)

    @app.post("/tasks/{task_id}/unarchive")
    def unarchive_task(task_id: str, claims: TenantClaims = Depends(current_tenant)):
        return _archive(task_id, False, claims)
