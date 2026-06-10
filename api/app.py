"""FastAPI control plane (Build Guide Phase 9, Step 49).

Owns JWT verification, the Greenlight/approvals endpoints, view CRUD, agent-session orchestration, and
the action-gate pipeline. Every authed route derives the tenant ONLY from the verified JWT claim
(`api.auth.current_tenant`) and threads it into the gate / greenlight / views / session — never from
the request body or a header.

Built via `create_app(deps)` so it is fully testable offline with a fake verifier + in-memory stores.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from api.auth import JwtVerifier, TenantClaims, make_current_tenant
from api.control.autonomy import AutonomyConfig
from api.control.gate import ActionGate, GateContext
from api.control.greenlight import Greenlight
from api.control.killswitch import KillSwitch
from api.control.traces import InMemoryTraceStore, TraceStore
from api.control.types import Action
from api.views import SavedViews


@dataclass
class ApiDeps:
    verifier: JwtVerifier
    greenlight: Greenlight
    saved_views: SavedViews
    conversation_factory: Callable[[str], Any]          # tenant_id -> conv.session.Conversation
    autonomy_config: AutonomyConfig
    executor: Callable[[Action], Any]                   # performs an approved/auto action
    killswitch: KillSwitch = field(default_factory=KillSwitch)
    trace_store: TraceStore = field(default_factory=InMemoryTraceStore)
    view_patcher: Callable[[dict, str], dict] | None = None  # NL refine: (spec, instruction) -> spec
    signup: Any = None                                  # optional SignupDeps (mounts public routes)


# --- request bodies (note: NONE carry tenant_id — the trust rule forbids it) ---
class DecideBody(BaseModel):
    decision: str
    edits: dict | None = None
    deny_message: str = ""


class SaveViewBody(BaseModel):
    spec: dict
    source_prompt: str = ""


class RefineBody(BaseModel):
    instruction: str


class ChatBody(BaseModel):
    message: str


class ActionBody(BaseModel):
    # NOTE: side_effecting + channel are intentionally NOT accepted from the client — the gate derives
    # them from the trusted tool registry (a forged flag must not bypass Greenlight/compliance).
    name: str
    payload: dict = {}
    reasoning: str = ""
    value_at_stake: float | None = None
    discount: float | None = None


def create_app(deps: ApiDeps) -> FastAPI:
    app = FastAPI(title="Uplift control plane")
    current_tenant = make_current_tenant(deps.verifier)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/approvals")
    def list_approvals(claims: TenantClaims = Depends(current_tenant)):
        return {"approvals": deps.greenlight.list_pending(claims.tenant_id)}

    @app.post("/approvals/{approval_id}/decide")
    def decide_approval(approval_id: str, body: DecideBody, claims: TenantClaims = Depends(current_tenant)):
        # Read tenant-scoped (RLS via the verified claim); re-check post-read as defense in depth.
        rec = deps.greenlight.store.get(claims.tenant_id, approval_id)
        if rec is None or str(rec["tenant_id"]) != str(claims.tenant_id):
            raise HTTPException(status_code=404, detail="no such approval")  # tenant-scoped
        try:
            return deps.greenlight.decide(claims.tenant_id, approval_id, body.decision, edits=body.edits,
                                          deny_message=body.deny_message, decided_by=claims.sub)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/views")
    def list_views(claims: TenantClaims = Depends(current_tenant)):
        views = deps.saved_views.store.list(claims.tenant_id)
        # Defense in depth: never return a row whose tenant_id isn't the verified request tenant
        # (RLS already scopes the read; this re-check makes a silent leak fail loud, not propagate).
        for v in views:
            if str(v["tenant_id"]) != str(claims.tenant_id):
                raise HTTPException(status_code=500, detail="tenant isolation violation")
        return {"views": views}

    @app.get("/views/{view_id}")
    def get_view(view_id: str, claims: TenantClaims = Depends(current_tenant)):
        v = deps.saved_views.get(claims.tenant_id, view_id)
        if v is None:
            raise HTTPException(status_code=404, detail="no such view")
        return v

    @app.post("/views")
    def save_view(body: SaveViewBody, claims: TenantClaims = Depends(current_tenant)):
        try:
            return deps.saved_views.save(claims.tenant_id, body.spec,
                                         source_prompt=body.source_prompt, created_by=claims.sub)
        except Exception as e:  # validation error -> 422
            raise HTTPException(status_code=422, detail=str(e))

    @app.post("/views/{view_id}/refine")
    def refine_view(view_id: str, body: RefineBody, claims: TenantClaims = Depends(current_tenant)):
        if deps.view_patcher is None:
            raise HTTPException(status_code=501, detail="NL refine needs a view_patcher (agent runtime)")
        if deps.saved_views.get(claims.tenant_id, view_id) is None:
            raise HTTPException(status_code=404, detail="no such view")
        try:
            return deps.saved_views.refine_nl(claims.tenant_id, view_id, body.instruction,
                                              deps.view_patcher, created_by=claims.sub)
        except Exception as e:  # validation error on the patched spec -> 422
            raise HTTPException(status_code=422, detail=str(e))

    @app.post("/chat")
    def chat(body: ChatBody, claims: TenantClaims = Depends(current_tenant)):
        convo = deps.conversation_factory(claims.tenant_id)
        if convo is None:  # conversation backend not wired (e.g. agent runtime needs creds) — fail clean
            raise HTTPException(status_code=503, detail="chat backend not configured")
        turn = convo.send(body.message)
        return turn.as_dict() if hasattr(turn, "as_dict") else turn

    @app.post("/actions")
    def run_action(body: ActionBody, claims: TenantClaims = Depends(current_tenant)):
        # Derive whether the action is side-effecting + its channel from the TRUSTED tool registry,
        # never from the request body — a forged flag must not bypass Greenlight/compliance.
        from agents.tools.registry import TOOL_REGISTRY, tool_meta
        if body.name not in TOOL_REGISTRY:
            raise HTTPException(status_code=400, detail=f"unknown tool: {body.name}")
        meta = tool_meta(body.name)
        action = Action(
            name=body.name, tenant_id=claims.tenant_id,  # tenant from the VERIFIED claim only
            agent=claims.sub, side_effecting=meta["side_effecting"],
            channel=meta["channel"], payload=body.payload, reasoning=body.reasoning,
            value_at_stake=body.value_at_stake, discount=body.discount,
        )
        ctx = GateContext(
            tenant_id=claims.tenant_id, autonomy_config=deps.autonomy_config,
            executor=deps.executor, greenlight=deps.greenlight,
            killswitch=deps.killswitch, trace_store=deps.trace_store,
        )
        result = ActionGate().run(action, ctx)
        return {"status": result.status, "decision": result.decision.value, "detail": result.detail,
                "approval": result.approval, "result": result.result}

    # Public, pre-tenant signup + Stripe webhook routes (optional).
    if deps.signup is not None:
        from api.signup_routes import mount_signup
        mount_signup(app, deps.signup)

    return app
