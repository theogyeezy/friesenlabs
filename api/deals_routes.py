"""Authed per-tenant deals/pipeline endpoints — the api half of the real Pipeline board
(the first honest-stub tab converted to REAL; the web half is web/src/api/PipelineBoard.tsx).

Five endpoints, all bound to the VERIFIED JWT claims (THE TRUST RULE — tenant never from a
header or the request body):

  GET   /deals                       the board: deals grouped into ordered stage columns, each
                                     card carrying the joined company name (RLS-scoped reads)
  GET   /deals/{deal_id}             one deal + its recent activities (the detail drawer)
  POST  /deals                       create a deal: {title, amount?, stage?, contact_id?} —
                                     direct write, tenant from the VERIFIED claim, RLS-scoped
                                     via SET LOCAL (NOT the Greenlight path; user-initiated,
                                     not agent-side-effecting)
  PATCH /deals/{deal_id}             edit title/amount — direct write, same pattern
  POST  /deals/{deal_id}/move-stage  does NOT write the deal. It builds an `update_deal` Action
                                     and runs it through the EXISTING ActionGate exactly like
                                     POST /actions does — autonomy-gated, so under the deployed
                                     default (L1, and update_deal is ALWAYS_ASK either way) it
                                     lands ONE Greenlight proposal and answers
                                     {queued: true, approval_id}. The deal row is untouched
                                     until a human approves in Greenlight. The draft-gate
                                     (CLAUDE.md hard constraint #2) stands.

Reads ride the same crm_app DSN every live surface (/approvals, /views, the /chat tool clients)
already rides — RLS via the per-op `SET LOCAL app.current_tenant` transaction
(api/pg_clients.py), allow-listed hand-written column lists, no hand-written tenant filter
anywhere. Unconfigured (no DSN -> no reader injected) every endpoint answers an honest 503,
never invented rows. The move-stage path introduces NO new side-effect class: it lands a
Greenlight proposal through the identical gate pipeline POST /actions already exposes live.

IMPORT SAFETY: importing this module touches no AWS/boto3/DB and never imports ingest/ (the
production API image does not bundle it — see api/integrations_routes.py HOTFIX note; the
image-fileset regression test imports api.app, which mounts this module). The tool registry is
imported lazily inside the move-stage route, mirroring POST /actions.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from api.auth import TenantClaims
from api.control.gate import ActionGate, GateContext
from api.control.types import Action

log = logging.getLogger("api.deals_routes")

# --------------------------------------------------------------------------- #
# Stage catalog — the canonical pipeline order for the board columns. Stages
# are free text in the schema (deals.stage, default 'new'); rows whose stage is
# not in this catalog are NEVER dropped — they group into appended columns
# (alphabetical, after the canonical ones) so the board stays honest about
# whatever is actually in the tenant's data.
# --------------------------------------------------------------------------- #
STAGE_ORDER: tuple[str, ...] = (
    "new", "qualified", "proposal", "negotiation", "closed_won", "closed_lost",
)
STAGE_LABELS: dict[str, str] = {
    "new": "New",
    "qualified": "Qualified",
    "proposal": "Proposal",
    "negotiation": "Negotiation",
    "closed_won": "Closed won",
    "closed_lost": "Closed lost",
}

_UNCONFIGURED_DETAIL = (
    "deals data plane not configured — no crm_app DSN on this task "
    "(DB_*/UPLIFT_DB_URL unset); the pipeline board is unavailable"
)


def _stage_label(stage: str) -> str:
    return STAGE_LABELS.get(stage, stage.replace("_", " ").strip().capitalize() or stage)


def _group_stages(deals: list[dict]) -> list[dict]:
    """Group board rows into ordered stage columns (canonical order first, then any
    extra stages found in the data, alphabetical). Empty canonical columns are kept so
    the board renders a stable spine even for a sparse pipeline."""
    by_stage: dict[str, list[dict]] = {s: [] for s in STAGE_ORDER}
    for d in deals:
        stage = d.get("stage") or "new"
        by_stage.setdefault(stage, []).append(d)
    ordered = list(STAGE_ORDER) + sorted(s for s in by_stage if s not in STAGE_ORDER)
    return [
        {
            "stage": s,
            "label": _stage_label(s),
            "deals": by_stage[s],
            "count": len(by_stage[s]),
            "total_amount": sum(d["amount"] for d in by_stage[s] if d.get("amount") is not None),
        }
        for s in ordered
    ]


# --------------------------------------------------------------------------- #
# Injected deps (the integrations_routes mount pattern, with a DELIBERATELY
# inert default: the ApiDeps default_factory builds the all-None stub, so a
# bare create_app(ApiDeps(...)) — every test, any non-asgi constructor — mounts
# the routes answering the honest 503 and NEVER opens a DB pool as a side
# effect of constructing deps. The ONLY real wiring is api/asgi.py passing the
# SAME PgCrmClient instance the executor/chat tools already use (one pool, the
# exact dsn_from_env guard the /approvals//views siblings ride).
# --------------------------------------------------------------------------- #
@dataclass
class DealsDeps:
    # A PgCrmClient-shaped reader (list_deals_board / get_deal_board /
    # list_deal_activities). None = data plane unconfigured -> every endpoint
    # answers the honest 503, never invented rows.
    crm: Any | None = None
    # A PlaybookDispatcher-shaped producer (dispatch_event(tenant_id, event_name,
    # payload)). None = no dispatcher wired -> the create route is INERT (it never
    # tries to fire event playbooks). The boss wires the live instance in api/asgi.py;
    # every test / non-asgi constructor leaves it None so creating deps opens nothing.
    dispatcher: Any | None = None


# --------------------------------------------------------------------------- #
# Request bodies — write paths. THE TRUST RULE: no tenant_id field anywhere.
# --------------------------------------------------------------------------- #
class CreateDealBody(BaseModel):
    title: str
    amount: float | int | None = None
    stage: str = "new"
    contact_id: str | None = None
    company_id: str | None = None


class EditDealBody(BaseModel):
    title: str | None = None
    amount: float | int | None = None


# --------------------------------------------------------------------------- #
# Request body — carries the target stage ONLY. There is deliberately no
# tenant field (THE TRUST RULE) and no other writable deal columns: this
# endpoint proposes exactly one change, the stage move.
# --------------------------------------------------------------------------- #
class MoveStageBody(BaseModel):
    to_stage: str


def _emit_created(deps: DealsDeps, event_name: str, tenant_id: str, row: dict) -> None:
    """Producer seam: fire ACTIVE event-playbooks bound to `event_name` for the VERIFIED
    tenant, carrying the freshly-created record as the trigger payload.

    INERT without a dispatcher (deps.dispatcher is None for every test / non-asgi
    constructor) and CONTAINED: a dispatch failure is logged and swallowed so an event
    playbook can never fail the user-initiated create that already succeeded. The
    dispatcher itself runs each playbook draft-only through Greenlight (runner.run)."""
    dispatcher = getattr(deps, "dispatcher", None)
    if dispatcher is None:
        return
    try:
        dispatcher.dispatch_event(tenant_id, event_name, {"deal": row})
    except Exception:  # noqa: BLE001 — a playbook must never break deal creation
        log.exception("event dispatch failed for %s (tenant scoped)", event_name)


def _require_reader(deps: DealsDeps) -> Any:
    if deps.crm is None:
        raise HTTPException(status_code=503, detail=_UNCONFIGURED_DETAIL)
    return deps.crm


def _valid_deal_id_or_404(deal_id: str) -> str:
    """Path ids must be uuids (the schema's PK type). A malformed id is indistinguishable
    from a missing row to the caller — 404, tenant-scoped semantics, never a 500."""
    try:
        return str(uuid.UUID(str(deal_id)))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail="no such deal")


def _valid_contact_id_or_422(contact_id: str) -> str:
    """A deal's optional contact_id must be a uuid (deals.contact_id FK type). A malformed
    body value is a client error on a body field — 422 'invalid contact_id', NOT the
    deal-path's 404 'no such deal' (which would lie about what was wrong)."""
    try:
        return str(uuid.UUID(str(contact_id)))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=422, detail="invalid contact_id")


def _valid_company_id_or_422(company_id: str) -> str:
    """A deal's optional company_id must be a uuid (deals.company_id FK type). A malformed
    body value is a client error on a body field — 422 'invalid company_id'."""
    try:
        return str(uuid.UUID(str(company_id)))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=422, detail="invalid company_id")


def _checked_rows(rows: list[dict], tenant_id: str) -> list[dict]:
    """Defense in depth (the /views pattern): never let a row whose tenant_id isn't the
    verified request tenant leave the API — RLS already scopes the read; this makes a
    silent leak fail loud. The internal tenant_id is then stripped from the payload."""
    out = []
    for r in rows:
        if str(r.get("tenant_id")) != str(tenant_id):
            raise HTTPException(status_code=500, detail="tenant isolation violation")
        out.append({k: v for k, v in r.items() if k != "tenant_id"})
    return out


def mount_deals(app: FastAPI, deps: DealsDeps, current_tenant, *, gate_deps: Any) -> None:
    """Mount the /deals routes on `app`, authed via `current_tenant` (the same
    verified-claims dependency every other authed route uses).

    `gate_deps` is the ApiDeps bag (duck-typed to avoid an api.app import cycle): the
    move-stage route reuses ITS autonomy_config / executor / greenlight / killswitch /
    trace_store so a stage move runs through the exact same gate pipeline as POST /actions —
    one Greenlight queue, one autonomy policy, one kill switch.
    """

    @app.get("/deals")
    def list_deals(claims: TenantClaims = Depends(current_tenant)):
        crm = _require_reader(deps)
        rows = _checked_rows(crm.list_deals_board(tenant_id=claims.tenant_id), claims.tenant_id)
        return {
            "stages": _group_stages(rows),
            "total": len(rows),
            "stage_order": list(STAGE_ORDER),
        }

    @app.get("/deals/{deal_id}")
    def get_deal(deal_id: str, claims: TenantClaims = Depends(current_tenant)):
        crm = _require_reader(deps)
        did = _valid_deal_id_or_404(deal_id)
        row = crm.get_deal_board(tenant_id=claims.tenant_id, deal_id=did)
        if row is None:  # missing OR another tenant's — indistinguishable by design
            raise HTTPException(status_code=404, detail="no such deal")
        deal = _checked_rows([row], claims.tenant_id)[0]
        activities = crm.list_deal_activities(tenant_id=claims.tenant_id, deal_id=did)
        return {"deal": deal, "activities": activities}

    @app.post("/deals", status_code=201)
    def create_deal(body: CreateDealBody,
                    claims: TenantClaims = Depends(current_tenant)):
        crm = _require_reader(deps)
        title = (body.title or "").strip()
        if not title:
            raise HTTPException(status_code=422, detail="title must be non-empty")
        stage = (body.stage or "new").strip() or "new"
        contact_id: str | None = None
        if body.contact_id:
            # Validate shape (422 on a malformed body value), then existence under the
            # verified tenant: a contact this tenant can't see can't anchor its deal.
            # Returning a clean 404 'no such contact' beats letting the composite FK
            # (deals_tenant_contact_fkey) throw an opaque 500 at write time.
            contact_id = _valid_contact_id_or_422(body.contact_id)
            if crm.get_contact_directory(
                tenant_id=claims.tenant_id, contact_id=contact_id
            ) is None:
                raise HTTPException(status_code=404, detail="no such contact")
        company_id: str | None = None
        if body.company_id:
            # Validate shape (422 on a malformed body value), then existence under the
            # verified tenant: a company this tenant can't see can't anchor its deal.
            # Returning a clean 404 'no such company' beats letting the FK throw an opaque 500.
            company_id = _valid_company_id_or_422(body.company_id)
            if crm.get_company_directory(
                tenant_id=claims.tenant_id, company_id=company_id
            ) is None:
                raise HTTPException(status_code=404, detail="no such company")
        # Direct write — tenant from the VERIFIED claim, RLS-scoped via SET LOCAL.
        # company_id and contact_id ride through when given so links are actually persisted;
        # insert_deal normalizes "" -> NULL for either nullable FK.
        row = crm.insert_deal(
            tenant_id=claims.tenant_id,
            company_id=company_id or "",
            name=title,
            stage=stage,
            amount=body.amount,
            contact_id=contact_id,
        )
        # Producer: a successful CREATE is a domain event. Fire every ACTIVE event-playbook
        # bound to 'deal.created' for the VERIFIED tenant (THE TRUST RULE — tenant from the
        # claim, never the body). Guarded + inert: with no dispatcher wired (every test /
        # non-asgi deps) this is a no-op, and a playbook failure NEVER fails the create.
        _emit_created(deps, "deal.created", claims.tenant_id, row)
        return {"deal": row}

    @app.patch("/deals/{deal_id}")
    def edit_deal(deal_id: str, body: EditDealBody,
                  claims: TenantClaims = Depends(current_tenant)):
        crm = _require_reader(deps)
        did = _valid_deal_id_or_404(deal_id)
        changes: dict = {}
        if body.title is not None:
            title = body.title.strip()
            if not title:
                raise HTTPException(status_code=422, detail="title must be non-empty when provided")
            changes["name"] = title  # deals.title maps to the "name" change key
        if body.amount is not None:
            changes["amount"] = body.amount
        if not changes:
            raise HTTPException(status_code=422, detail="at least one field must be provided")
        # RLS-scoped existence check: a deal this tenant can't see can't be edited.
        row = crm.get_deal_board(tenant_id=claims.tenant_id, deal_id=did)
        if row is None:
            raise HTTPException(status_code=404, detail="no such deal")
        try:
            result = crm.update_deal_fields(
                tenant_id=claims.tenant_id, deal_id=did, changes=changes
            )
        except ValueError as exc:
            msg = str(exc)
            if "not found" in msg:
                raise HTTPException(status_code=404, detail="no such deal")
            raise HTTPException(status_code=422, detail=msg)
        return result

    @app.post("/deals/{deal_id}/move-stage")
    def move_stage(deal_id: str, body: MoveStageBody,
                   claims: TenantClaims = Depends(current_tenant)):
        crm = _require_reader(deps)
        did = _valid_deal_id_or_404(deal_id)
        to_stage = body.to_stage.strip()
        if not to_stage:
            raise HTTPException(status_code=422, detail="to_stage must be non-empty")

        # RLS-scoped existence check FIRST: a deal this tenant can't read can't be moved
        # (and can't even be probed — 404 either way). Also yields the honest from-stage.
        row = crm.get_deal_board(tenant_id=claims.tenant_id, deal_id=did)
        if row is None:
            raise HTTPException(status_code=404, detail="no such deal")
        from_stage = row.get("stage")
        if to_stage == from_stage:
            raise HTTPException(status_code=409,
                                detail=f"deal is already in stage {from_stage!r}")

        # EXACTLY the POST /actions pipeline: side_effecting/channel come from the TRUSTED
        # tool registry (update_deal is Policy.ALWAYS_ASK — a forged flag cannot exist here
        # because the client supplies nothing but to_stage), tenant from the VERIFIED claim.
        from agents.tools.registry import tool_meta  # noqa: PLC0415 — mirrors /actions

        meta = tool_meta("update_deal")
        title = row.get("title") or did
        action = Action(
            name="update_deal",
            tenant_id=claims.tenant_id,           # tenant from the VERIFIED claim only
            agent=claims.sub,
            side_effecting=meta["side_effecting"],
            channel=meta["channel"],
            payload={"deal_id": did, "changes": {"stage": to_stage}, "from_stage": from_stage},
            reasoning=f"Move deal {title!r} from stage {from_stage!r} to {to_stage!r} "
                      "(requested on the pipeline board).",
            value_at_stake=row.get("amount"),
        )
        ctx = GateContext(
            tenant_id=claims.tenant_id,
            autonomy_config=gate_deps.autonomy_config,
            executor=gate_deps.executor,
            greenlight=gate_deps.greenlight,
            killswitch=gate_deps.killswitch,
            trace_store=gate_deps.trace_store,
        )
        result = ActionGate().run(action, ctx)

        if result.status == "blocked":
            # Kill switch / compliance — surfaced honestly, nothing queued, nothing moved.
            raise HTTPException(status_code=409, detail=f"move blocked: {result.detail}")
        if result.status == "pending_approval" and result.approval is not None:
            # The normal path (L1 default / ALWAYS_ASK): ONE Greenlight proposal, no write.
            return {
                "queued": True,
                "approval_id": result.approval.get("id"),
                "status": "pending_approval",
                "from_stage": from_stage,
                "to_stage": to_stage,
                "detail": "queued for approval in Greenlight — the deal stays in "
                          f"{from_stage!r} until a human approves",
            }
        # Decision.AUTO (an operator-raised autonomy level): the executor ran — and for an
        # ALWAYS_ASK tool the Phase 4 base class STILL only proposes to Greenlight (the
        # draft-only guarantee). Surface that proposal as queued; never claim the move ran.
        tool_result = result.result if isinstance(result.result, dict) else {}
        approval = tool_result.get("approval") if isinstance(tool_result.get("approval"), dict) \
            else None
        if tool_result.get("status") == "pending_approval":
            return {
                "queued": True,
                "approval_id": approval.get("id") if approval else None,
                "status": "pending_approval",
                "from_stage": from_stage,
                "to_stage": to_stage,
                "detail": "queued for approval in Greenlight — the deal stays in "
                          f"{from_stage!r} until a human approves",
            }
        # Anything else would mean a side effect executed without Greenlight — that path
        # does not exist for update_deal (ALWAYS_ASK); refuse loudly rather than pretend.
        raise HTTPException(status_code=500,
                            detail="unexpected gate outcome for update_deal")
