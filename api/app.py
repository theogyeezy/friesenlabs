"""FastAPI control plane (Build Guide Phase 9, Step 49).

Owns JWT verification, the Greenlight/approvals endpoints, view CRUD, agent-session orchestration, and
the action-gate pipeline. Every authed route derives the tenant ONLY from the verified JWT claim
(`api.auth.current_tenant`) and threads it into the gate / greenlight / views / session — never from
the request body or a header.

Built via `create_app(deps)` so it is fully testable offline with a fake verifier + in-memory stores.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from api.agents_routes import AgentsDeps
from api.auth import JwtVerifier, TenantClaims, make_current_tenant
from api.control.autonomy import AutonomyConfig
from api.control.appliers import apply_approved_action
from api.control.gate import ActionGate, GateContext
from api.control.greenlight import EditNotAllowed, Greenlight
from api.control.killswitch import KillSwitch
from api.control.traces import InMemoryTraceStore, TraceStore
from api.control.types import Action
from api.contacts_routes import ContactsDeps
from api.deals_routes import DealsDeps
from api.integrations_routes import IntegrationsDeps, build_integrations_deps
from api.knowledge_routes import KnowledgeDeps
from api.views import SavedViews
from api.workflows_routes import WorkflowsDeps

logger = logging.getLogger(__name__)


def _build_public_deps():
    """Lazy default for ApiDeps.public (import api.public_routes only when constructed)."""
    from api.public_routes import build_public_deps  # noqa: PLC0415 — avoid an import cycle
    return build_public_deps()


@dataclass
class ApiDeps:
    verifier: JwtVerifier
    greenlight: Greenlight
    saved_views: SavedViews
    conversation_factory: Callable[[str], Any]          # tenant_id -> conv.session.Conversation
    autonomy_config: AutonomyConfig
    executor: Callable[[Action], Any]                   # performs an approved/auto action
    crm: Any | None = None                              # post-approval CRM appliers
    killswitch: KillSwitch = field(default_factory=KillSwitch)
    trace_store: TraceStore = field(default_factory=InMemoryTraceStore)
    # /control autonomy dial (api/routes_control.py). None -> the routes fall back to an
    # AutonomyDial over `autonomy_config` (in-memory; flips are instance-local). api/asgi.py
    # wires the Pg-backed PersistedAutonomyDial whose provider `autonomy_config` resolves, so
    # the dial and the gate read/write ONE persisted per-tenant level.
    autonomy_dial: Any | None = None
    view_patcher: Callable[[dict, str], dict] | None = None  # NL refine: (spec, instruction) -> spec
    signup: Any = None                                  # optional SignupDeps (mounts public routes)
    # /integrations deps (TODO INT/P2). Env-built by default so api/asgi.py needs no change:
    # with no env set every piece is the honest unconfigured stub (credentials/sync 503,
    # status "unknown"); real adapters ride ONLY the deliberate INTEGRATIONS_REAL_SECRETS /
    # INGEST_REAL_STORES switches. Pass None to skip mounting the routes entirely.
    integrations: IntegrationsDeps | None = field(default_factory=build_integrations_deps)
    # /deals deps (the real Pipeline board). The default is the INERT all-None stub — every
    # endpoint answers the honest 503 and constructing deps never opens a DB pool. The real
    # reader is wired ONLY by api/asgi.py, which passes the SAME PgCrmClient the executor/chat
    # tools use (one pool, the dsn_from_env guard the /approvals//views stores ride). Pass None
    # to skip mounting the routes entirely.
    deals: DealsDeps | None = field(default_factory=DealsDeps)
    # /contacts + /companies deps (the real Contacts directory). Same inert-default contract
    # as `deals`: the all-None stub mounts honest-503 routes and constructing deps never opens
    # a DB pool; api/asgi.py is the ONLY real wiring (the same PgCrmClient instance). Pass None
    # to skip mounting the routes entirely.
    contacts: ContactsDeps | None = field(default_factory=ContactsDeps)
    # /agents deps (the real Agents tab — the tenant's crew). Same inert-default contract:
    # the all-None stub mounts an honest-503 route and constructing deps never opens a DB
    # pool; api/asgi.py is the ONLY real wiring (the same PgWorkspaceStore instance the /chat
    # factory + signup provisioning ride). Pass None to skip mounting the route entirely.
    agents: AgentsDeps | None = field(default_factory=AgentsDeps)
    # /workflows deps (the real Workflows tab — the provisioning machine made visible).
    # Same inert-default contract: the all-None stub mounts the route answering the honest
    # not-configured shape (static diagram, executions_available: false) and constructing
    # deps never builds a boto3 client; api/asgi.py is the ONLY real wiring
    # (Config.provisioning_sfn_arn). Pass None to skip mounting the route entirely.
    workflows: WorkflowsDeps | None = field(default_factory=WorkflowsDeps)
    # /knowledge deps (the real Knowledge tab — the tenant's ingested corpus). Same inert-default
    # contract: the all-None stub mounts honest-503 routes and constructing deps never opens a DB
    # pool; api/asgi.py is the ONLY real wiring (the SAME PgRagClient instance the executor/chat
    # RAG tool rides). Pass None to skip mounting the routes entirely.
    knowledge: KnowledgeDeps | None = field(default_factory=KnowledgeDeps)
    # /public/leads deps (unauthenticated lead capture). Env-built by default so api/asgi.py
    # needs no change: the real PgLeadStore is selected ONLY under SIGNUP_REAL_DEPS + the
    # crm_app DSN (api/public_routes.build_public_deps); otherwise the route answers an honest
    # 503 after validation. Pass None to skip mounting the route entirely.
    public: Any | None = field(default_factory=lambda: _build_public_deps())


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

    @app.get("/me")
    @app.get("/api/me")
    def me(claims: TenantClaims = Depends(current_tenant)):
        # SPA identity bootstrap (TODO FE/P2). Everything comes from the VERIFIED claims only
        # (THE TRUST RULE) — unauth/invalid tokens 401 via the current_tenant dependency.
        # Registered at both paths: the deployed Amplify rewrite strips the /api prefix
        # (infra web_hosting custom_rule "/api/<*>" -> "/<*>"), so the browser's /api/me lands on
        # /me; /api/me also answers for direct callers. `name` is null until TenantClaims carries
        # the claim (api/auth.py is outside this cycle's module-A file set) — shape stays stable.
        return {"email": claims.email, "tenant_id": claims.tenant_id, "name": None}

    @app.get("/approvals")
    def list_approvals(claims: TenantClaims = Depends(current_tenant)):
        return {"approvals": deps.greenlight.list_pending(claims.tenant_id)}

    @app.post("/approvals/{approval_id}/decide")
    def decide_approval(approval_id: str, body: DecideBody, claims: TenantClaims = Depends(current_tenant)):
        # Read tenant-scoped (RLS via the verified claim); re-check post-read as defense in depth.
        rec = deps.greenlight.store.get(claims.tenant_id, approval_id)
        if rec is None or str(rec["tenant_id"]) != str(claims.tenant_id):
            raise HTTPException(status_code=404, detail="no such approval")  # tenant-scoped

        wants_apply = body.decision in ("approve", "edit")
        # Kill switch — consulted BEFORE the atomic status flip so an engaged pause (global or
        # tenant) leaves the approval PENDING: the approval is NOT consumed, the human simply
        # re-approves after the pause lifts. The applier can therefore never run while paused.
        if wants_apply and deps.killswitch.is_paused(claims.tenant_id):
            raise HTTPException(status_code=409, detail="kill switch engaged")

        # Step 1 — the ATOMIC pending->decided flip. decide() writes via the store's conditional
        # update (WHERE status='pending'); a concurrent loser raises here, so the applier below
        # can only ever run for the single request that won the transition.
        try:
            decided = deps.greenlight.decide(
                claims.tenant_id,
                approval_id,
                body.decision,
                edits=body.edits,
                deny_message=body.deny_message,
                decided_by=claims.sub,
            )
        except EditNotAllowed as e:  # edit tried to change 'action' / a non-payload key
            raise HTTPException(status_code=422, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if not wants_apply:
            return decided

        # Apply-on-approve: the tenant for the write is the approval row's tenant (stamped
        # from verified claims when proposed), not anything in the request body.
        apply_tenant = str(decided["tenant_id"])

        # Step 2 — the applier ALONE is guarded: an exception here means the CRM write did not
        # happen (or cannot be trusted to have happened), recorded honestly as performed: false.
        try:
            apply_result = apply_approved_action(
                deps.crm, apply_tenant, dict(decided["proposed_action"])
            )
        except Exception as e:  # noqa: BLE001 - response records type only, never internals
            failure = {"performed": False, "error": e.__class__.__name__}
            try:
                deps.greenlight.store.update(apply_tenant, approval_id, {"apply_result": failure})
            except Exception:  # noqa: BLE001 — keep the response honest even if the audit write dies
                logger.exception(
                    "approval %s: applier failed AND the failure audit write failed", approval_id
                )
                out = dict(decided)
                out["apply_result"] = failure
                out["warning"] = "audit write failed; apply_result not persisted"
                return out
            return deps.greenlight.store.get(claims.tenant_id, approval_id)

        # Step 3 — the audit update. The CRM write HAS happened by now; a failure writing the
        # audit row must NEVER be recorded (or reported) as performed: false. Log loudly and
        # return the applied outcome with a warning instead of rewriting history.
        applied_at = datetime.now(timezone.utc)
        try:
            deps.greenlight.store.update(apply_tenant, approval_id, {
                "applied_at": applied_at,
                "apply_result": apply_result,
            })
        except Exception:  # noqa: BLE001 — the apply succeeded; surface that truth regardless
            logger.exception(
                "approval %s: audit write failed AFTER a successful apply — the CRM write "
                "happened; apply_result not persisted", approval_id
            )
            out = dict(decided)
            out["applied_at"] = applied_at
            out["apply_result"] = apply_result
            out["warning"] = "applied, but the audit write failed; apply_result not persisted"
            return out
        return deps.greenlight.store.get(claims.tenant_id, approval_id)

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

    # Authed per-tenant integrations endpoints (TODO INT/P2 — the api half; the web screen
    # rides a later cycle). Same verified-claims dependency as every authed route above —
    # tenant NEVER from the body. Unconfigured deps answer honest 503s, never fake success.
    if deps.integrations is not None:
        from api.integrations_routes import mount_integrations
        mount_integrations(app, deps.integrations, current_tenant)

    # Authed per-tenant deals/pipeline endpoints (the real Pipeline board). Claims-bound like
    # everything above; gate_deps hands the move-stage route THIS app's gate pieces so a stage
    # move runs the exact /actions pipeline (one Greenlight queue, one autonomy policy, one
    # kill switch). Unconfigured deps answer honest 503s, never invented rows.
    if deps.deals is not None:
        from api.deals_routes import mount_deals
        mount_deals(app, deps.deals, current_tenant, gate_deps=deps)

    # Authed per-tenant contacts/companies directory (the real Contacts tab). Claims-bound,
    # READ-ONLY this cycle (no gate deps — CRM writes arrive with a later update_contact tool
    # through the gate). Unconfigured deps answer honest 503s, never invented rows.
    if deps.contacts is not None:
        from api.contacts_routes import mount_contacts
        mount_contacts(app, deps.contacts, current_tenant)

    # Authed per-tenant agent crew (the real Agents tab). Claims-bound, READ-ONLY: the roster
    # comes from the owned definitions + the trusted tool registry; provisioned MA ids ride
    # along TRUNCATED from the tenant's RLS-scoped row. Unconfigured deps answer an honest
    # 503, never an invented crew state.
    if deps.agents is not None:
        from api.agents_routes import mount_agents
        mount_agents(app, deps.agents, current_tenant)

    # Authed per-tenant workflows view (the real Workflows tab). Claims-bound, READ-ONLY:
    # the step diagram is the OWNED provisioning semantics (never a live Describe), and the
    # executions read degrades to an honest "pending IAM grant / not configured" 200 — the
    # api task role holds states:StartExecution only until REQ-009 (see workflows_routes).
    if deps.workflows is not None:
        from api.workflows_routes import mount_workflows
        mount_workflows(app, deps.workflows, current_tenant)

    # Authed per-tenant knowledge view (the real Knowledge tab). Claims-bound, READ-ONLY: the
    # inventory is a plain aggregate over the tenant's documents (no embedder), and search
    # degrades to an honest "search model not configured" 200 when the Titan embedder isn't
    # reachable — never a 500, never a leaked AWS error (see knowledge_routes).
    if deps.knowledge is not None:
        from api.knowledge_routes import mount_knowledge
        mount_knowledge(app, deps.knowledge, current_tenant)

    # Authed per-tenant control surface (kill switch · autonomy dial · decision traces) —
    # always mounted: the deps defaults are in-memory and api/asgi.py wires the Pg-backed
    # stores in prod. Authorization decisions are documented in api/routes_control.py.
    from api.routes_control import mount_control
    mount_control(app, deps, current_tenant)

    # Public, pre-tenant signup + Stripe webhook routes (optional).
    if deps.signup is not None:
        from api.signup_routes import mount_signup
        mount_signup(app, deps.signup)

    # Public, unauthenticated lead capture (POST /public/leads) — validated, 1KB-capped,
    # per-IP rate-limited; the store is honest-503 until the prod gates select PgLeadStore.
    if deps.public is not None:
        from api.public_routes import mount_public
        mount_public(app, deps.public)

    return app
