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

from api.account_routes import AccountDeps
from api.account_delete_routes import AccountDeleteDeps
from api.status_routes import StatusDeps
from api.settings_routes import SettingsDeps
from api.modules_routes import ModulesDeps
from api.agents_routes import AgentsDeps
from api.auth import JwtVerifier, TenantClaims, make_current_admin, make_current_tenant
from api.control.autonomy import AutonomyConfig
from api.control.appliers import apply_approved_action, was_performed
from api.control.gate import ActionGate, GateContext
from api.control.greenlight import ComplianceViolation, DEFAULT_APPROVALS_LIMIT, EditNotAllowed, Greenlight
from api.control.killswitch import KillSwitch
from api.control.traces import InMemoryTraceStore, TraceStore
from api.control.types import Action
from api.contacts_routes import ContactsDeps
from api.cortex_routes import CortexDeps
from api.deals_routes import DealsDeps
from api.tasks_routes import TasksDeps
from api.sidecar_routes import SidecarDeps
from api.integrations_routes import IntegrationsDeps, build_integrations_deps
from api.knowledge_routes import KnowledgeDeps
from api.views import SavedViews
from api.workflows_routes import WorkflowsDeps
from shared import view_spec
from shared.gamify_rules import DEAL_CLOSED_WON, points_for

logger = logging.getLogger(__name__)


def _build_public_deps():
    """Lazy default for ApiDeps.public (import api.public_routes only when constructed)."""
    from api.public_routes import build_public_deps  # noqa: PLC0415 — avoid an import cycle
    return build_public_deps()


def _build_studio_deps():
    """Lazy default for ApiDeps.studio (import api.routes_studio only when constructed)."""
    from api.routes_studio import build_studio_deps  # noqa: PLC0415 — avoid an import cycle
    return build_studio_deps()


def _build_support_deps():
    """Lazy default for ApiDeps.support (import api.support_routes only when constructed)."""
    from api.support_routes import build_support_deps  # noqa: PLC0415 — avoid an import cycle
    return build_support_deps()


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
    # Balto (conv/views.py ViewSynthesizer): NL view creation from chat — saved-view coverage
    # check, Cube member-catalog gate, build_view generation, ephemeral drafts. None -> the
    # /views/synthesize + draft routes answer an honest 503 (never a fake view).
    view_synthesizer: Any | None = None
    signup: Any = None                                  # optional SignupDeps (mounts public routes)
    # optional BillingDeps (api/billing_routes.py) — authed self-service Stripe Customer Portal.
    # None (default) = the routes are not mounted; api/asgi.py wires it from the signup payment
    # adapter + account store when configured.
    billing: Any = None
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
    # /tasks deps (the real CRM tasks/reminders surface). Same inert-default contract as `deals`/
    # `contacts`: the all-None stub mounts honest-503 routes and constructing deps never opens a DB
    # pool; api/asgi.py is the ONLY real wiring (the same PgCrmClient instance the directory + board
    # ride). Pass None to skip mounting the routes entirely.
    tasks: TasksDeps | None = field(default_factory=TasksDeps)
    # /sidecar deps (the real Sidecar agentic layer — grounded next-action suggestions over the
    # tenant's CRM, accept enqueues a Greenlight draft). Same inert-default contract as `deals`:
    # the all-None stub mounts honest-503 routes and opens no DB pool; api/asgi.py wires the same
    # PgCrmClient instance. None skips mounting.
    sidecar: SidecarDeps | None = field(default_factory=SidecarDeps)
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
    # /public/support deps (unauthenticated contact/help intake). Env-built by default so
    # api/asgi.py needs no change: the real PgSupportStore is selected ONLY under SIGNUP_REAL_DEPS
    # + the crm_app DSN (api/support_routes.build_support_deps); otherwise the route answers an
    # honest 503 after validation. Pass None to skip mounting the route entirely.
    support: Any | None = field(default_factory=lambda: _build_support_deps())
    # /studio deps (Agent Studio — playbook composer + library). Env-built by default so
    # api/asgi.py needs no change: the PgPlaybookStore rides ONLY the crm_app DSN gate and its
    # pool opens lazily; with no DSN every store-backed route answers an honest 503 (templates
    # still serve — committed JSON). Pass None to skip mounting the routes entirely.
    studio: Any | None = field(default_factory=lambda: _build_studio_deps())
    # /cortex/health deps (the #194 ml/health.py seam made reachable). Same inert-default
    # contract: the all-None stub mounts the route answering the honest metadata-only
    # "no_registry" shape and constructing deps never opens a DB pool / touches AWS;
    # api/asgi.py is the ONLY real wiring (the SAME env-built registry run_model scores with
    # + a PgPredictionLog on the shared crm_app DSN). Pass None to skip mounting entirely.
    cortex: CortexDeps | None = field(default_factory=CortexDeps)
    # /usage deps (per-tenant monthly usage counter + plan cap + Anthropic cost attribution).
    # Inert-default: the all-None stub mounts GET /usage answering a stable zeroed shape (never
    # 503) and constructing deps never opens a DB pool; api/asgi.py wires the real PgUsageStore +
    # PgCostRecorder on the shared crm_app DSN. Pass None to skip mounting the route entirely.
    usage: Any | None = None
    # /account/export deps (GDPR/portability egress). Same inert-default contract: the all-None
    # stub mounts the route answering the honest 503 and constructing deps never opens a DB pool;
    # api/asgi.py wires the real crm/rag/saved_views (the same instances every other route uses).
    # Pass None to skip mounting the route entirely (e.g. internal/stripped deployments).
    account: AccountDeps | None = field(default_factory=AccountDeps)
    # POST /account/delete deps (GDPR/offboarding teardown). Same inert-default contract: the
    # all-None stub mounts the route answering the honest 503; a destructive live path requires
    # api/asgi.py to deliberately wire a real PgAccountDeleter. None = skip mounting entirely.
    account_delete: AccountDeleteDeps | None = field(default_factory=AccountDeleteDeps)
    # GET /public/status deps (the public status page's per-subsystem probes). Inert default:
    # all-None probes → every subsystem "unknown" (honest) + an operational rollup; api/asgi.py
    # injects the real probes. None = skip mounting the route entirely.
    status: StatusDeps | None = field(default_factory=StatusDeps)
    # GET/PUT /account/settings deps (persisted workspace name + notification prefs). Same inert
    # default contract (store=None → honest 503); api/asgi.py wires the real PgSettingsStore.
    settings: SettingsDeps | None = field(default_factory=SettingsDeps)
    # GET/PUT /account/modules deps (per-tenant module entitlements — the "your suite" surface).
    # Same inert default contract (store=None → honest 503); api/asgi.py wires the real
    # PgSettingsStore (the app gates its nav/routes to the enabled modules). None = skip mounting.
    modules: ModulesDeps | None = field(default_factory=ModulesDeps)
    # Per-tenant rate-limit + quota MIDDLEWARE (a 2-tuple (middleware_class, kwargs) added via
    # app.add_middleware). None -> NOT installed (the default for offline tests, so they aren't
    # throttled). api/asgi.py passes a configured spec when tenant_limits_enabled(); a request with
    # no/invalid tenant claim always passes through (the route's own auth dependency 401s).
    limits_middleware: Any | None = None
    # The per-request-token Cube client (agents/tools/cube_client.CubeClient) that POST
    # /views/{id}/data resolves a saved view-spec's CubeQuery through — the SAME client the
    # executor/chat tools ride (one tenant JWT mint, queryRewrite enforces the filter server-side).
    # None (default) OR a wired-but-unconfigured client -> the data route answers an honest 503
    # (never fake rows). api/asgi.py wires the env-built cube_client_from_env() instance here.
    cube: Any | None = None
    # Sell (gamification) stores — the roster + the append-only points ledger (api/gamify_stores.py).
    # Both INERT by default (None): `members` None -> member-upsert-on-auth is a no-op; `points`
    # None -> close-scoring is a no-op. api/asgi.py wires the real PgMemberStore/PgPointsStore on
    # the SAME crm_app DSN every live surface rides (one pool, per-op SET LOCAL RLS). `members`
    # backs member-upsert-on-auth (threaded into make_current_tenant); `points` backs the
    # close-scoring hook in the approval-decide path (credits the initiating user on closed_won).
    members: Any | None = None
    points: Any | None = None


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


class SynthesizeViewBody(BaseModel):
    # The NL ask Balto synthesizes a view for (echoed back by the chat turn's view_request).
    request: str


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


def _maybe_score_close(deps: "ApiDeps", decided: dict, apply_result: dict) -> None:
    """Sell (gamification) close-scoring hook: credit the INITIATING user with deal.closed_won
    points when an approved in-app stage move ACTUALLY lands a deal in closed_won.

    The initiator is the approval's `agent` — stamped from the verified JWT `sub` at propose time
    (api/deals_routes move-stage sets agent=claims.sub), NOT the approver (decided_by). The applier
    is only handed `decided_by`, so the initiator's sub is read here, where the approval row still
    carries it. (Greenlight is the only path a board stage move to closed_won takes — update_deal is
    ALWAYS_ASK; see the PR notes.)

    GUARDED + INERT: a no-op when no points store is wired (deps.points is None), when the apply
    didn't really happen (performed=false), or when the move wasn't into closed_won. ANY failure is
    swallowed — scoring must NEVER affect the approval outcome (the CRM write already succeeded)."""
    points_store = getattr(deps, "points", None)
    if points_store is None:
        return
    try:
        if not was_performed(apply_result):
            return
        proposed = decided.get("proposed_action") or {}
        if proposed.get("action") != "update_deal":
            return
        if (proposed.get("changes") or {}).get("stage") != "closed_won":
            return
        user_id = decided.get("agent")
        if not user_id:
            return
        points = points_for(DEAL_CLOSED_WON)
        if points <= 0:
            return
        points_store.append({
            "tenant_id": str(decided.get("tenant_id")),
            "user_id": str(user_id),
            "event_type": DEAL_CLOSED_WON,
            "points": points,
            "deal_id": proposed.get("deal_id"),
        })
    except Exception:  # noqa: BLE001 — scoring must NEVER affect the approval outcome
        logger.exception("close-scoring failed (tenant scoped); credit skipped")


def create_app(deps: ApiDeps) -> FastAPI:
    app = FastAPI(title="Uplift control plane")
    # member-upsert-on-auth rides the shared verified-JWT dependency: deps.members None (the default)
    # keeps it a no-op, so the unauth path and every non-asgi constructor are unchanged.
    current_tenant = make_current_tenant(deps.verifier, member_store=deps.members)
    # Tenant-admin gate (RBAC). decide_approval is THE consequential write in the product --
    # approving an edit/approve is what moves a draft to execution -- so it carries the same
    # admin requirement as the other privileged writes (killswitch/autonomy/billing/modules/
    # export/delete/settings). 401 (bad token) still resolves before 403 (not admin).
    current_admin = make_current_admin(current_tenant)

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
    def list_approvals(limit: int = DEFAULT_APPROVALS_LIMIT, cursor: str | None = None,
                       claims: TenantClaims = Depends(current_tenant)):
        """One bounded page of the pending queue (keyset cursor, traces-style) + the tenant's
        total pending count — the count feeds the web nav badge without fetching the queue."""
        try:
            rows, next_cursor = deps.greenlight.page_pending(
                claims.tenant_id, limit=limit, cursor=cursor)
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid cursor")
        return {
            "approvals": rows,
            "cursor": next_cursor,
            "total_pending": deps.greenlight.count_pending(claims.tenant_id),
        }

    @app.post("/approvals/{approval_id}/decide")
    def decide_approval(approval_id: str, body: DecideBody, claims: TenantClaims = Depends(current_admin)):
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
        except ComplianceViolation as e:
            # The decision (typically an `edit` — e.g. stripping the unsubscribe link) produced a
            # snapshot that fails the deterministic compliance floor. decide() raised BEFORE the
            # atomic status flip, so the approval is still PENDING (re-decidable after a compliant
            # edit). The detail is the fixed prefix + the CURATED policy reason the validator
            # authored — never internal exception text.
            raise HTTPException(status_code=422, detail=f"decision rejected by compliance: {e.reason}")
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
                deps.crm, apply_tenant, dict(decided["proposed_action"]),
                approval_id=approval_id, decided_by=claims.sub,
            )
        except Exception as e:  # noqa: BLE001 - response records type only, never internals
            # The full traceback goes to the logs (the response stays internals-free) so an
            # operator can diagnose a failed apply without guessing from a class name.
            logger.exception("approval %s: applier failed", approval_id)
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

        # Record-only honesty: an approved draft that performed nothing real must never read as
        # a sent action — log it so compliance/audit reviews see the distinction server-side
        # (the response already carries performed: false for the UI to surface).
        if not apply_result.get("performed"):
            logger.info(
                "approval %s: %s approved but performed nothing real (%s)", approval_id,
                decided["proposed_action"].get("action"),
                apply_result.get("reason") or "applier reported performed=false",
            )

        # Sell close-scoring: an approved in-app move that actually landed closed_won credits the
        # initiating user (guarded + inert; never affects the approval response — see helper).
        _maybe_score_close(deps, decided, apply_result)

        # Step 3 — the audit update. The CRM write HAS happened by now; a failure writing the
        # audit row must NEVER be recorded (or reported) as performed: false. Log loudly and
        # return the applied outcome with a warning instead of rewriting history.
        # applied_at marks a side effect that ACTUALLY happened — a record-only / no-op apply
        # (performed: false, e.g. a draft-only send_email) carries None so it can never read as
        # "sent", matching the applier-error path above.
        applied_at = datetime.now(timezone.utc) if was_performed(apply_result) else None
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

    def _assert_request_tenant(rows, claims: TenantClaims):
        # Defense in depth: never return a row whose tenant_id isn't the verified request tenant
        # (RLS already scopes the read; this re-check makes a silent leak fail loud, not propagate).
        for v in rows:
            if str(v["tenant_id"]) != str(claims.tenant_id):
                raise HTTPException(status_code=500, detail="tenant isolation violation")

    @app.get("/views")
    def list_views(claims: TenantClaims = Depends(current_tenant)):
        # Renderable view specs only — kind=dashboard composition rows live on GET /dashboards,
        # so every existing /views consumer (gallery, view pickers) keeps seeing only specs the
        # SpecRenderer can draw directly.
        views = deps.saved_views.list_views(claims.tenant_id)
        _assert_request_tenant(views, claims)
        return {"views": views}

    @app.get("/views/{view_id}")
    def get_view(view_id: str, claims: TenantClaims = Depends(current_tenant)):
        v = deps.saved_views.get(claims.tenant_id, view_id)
        if v is None:
            raise HTTPException(status_code=404, detail="no such view")
        return v

    @app.post("/views")
    def save_view(body: SaveViewBody, claims: TenantClaims = Depends(current_tenant)):
        # 422-detail hygiene: only the CURATED view_spec.ValidationError (every reason authored in
        # shared/view_spec.py / api/views.py) is echoed to the client. Any other exception is
        # logged server-side and answered with a FIXED message — internal text (KeyError reprs,
        # driver errors, DSNs) must never reach a client.
        try:
            return deps.saved_views.save(claims.tenant_id, body.spec,
                                         source_prompt=body.source_prompt, created_by=claims.sub)
        except view_spec.ValidationError as e:  # curated validation outcome -> client-actionable 422
            raise HTTPException(status_code=422, detail=str(e))
        except Exception:  # noqa: BLE001 — logged, never echoed
            logger.exception("save_view: spec rejected by an unexpected error")
            raise HTTPException(status_code=422, detail="view spec failed validation")

    @app.post("/views/{view_id}/refine")
    def refine_view(view_id: str, body: RefineBody, claims: TenantClaims = Depends(current_tenant)):
        if deps.view_patcher is None:
            raise HTTPException(status_code=501, detail="NL refine needs a view_patcher (agent runtime)")
        if deps.saved_views.get(claims.tenant_id, view_id) is None:
            raise HTTPException(status_code=404, detail="no such view")
        try:
            return deps.saved_views.refine_nl(claims.tenant_id, view_id, body.instruction,
                                              deps.view_patcher, created_by=claims.sub)
        except view_spec.ValidationError as e:  # curated validation outcome -> client-actionable 422
            raise HTTPException(status_code=422, detail=str(e))
        except Exception:  # noqa: BLE001 — 422-detail hygiene: logged, never echoed
            logger.exception("refine_view: patched spec rejected by an unexpected error")
            raise HTTPException(status_code=422, detail="refined view spec failed validation")

    @app.post("/views/synthesize")
    def synthesize_view(body: SynthesizeViewBody, claims: TenantClaims = Depends(current_tenant)):
        """Balto: synthesize a NEW tenant view from an NL ask (conv/views.py ViewSynthesizer).

        Tenant from the VERIFIED claim only. The result is status-keyed and honest:
        `exists` (a saved view already covers it), `data_not_found` (no Cube member can answer
        it — never hallucinated), `invalid` (generation failed validation), or `ok` with the
        validated spec + an ephemeral draft_id. Nothing is persisted here.
        """
        if deps.view_synthesizer is None:
            raise HTTPException(status_code=503, detail="view synthesis not configured")
        result = deps.view_synthesizer.synthesize(claims.tenant_id, body.request)
        if result.get("status") == "unavailable":
            raise HTTPException(status_code=503,
                                detail=result.get("error") or "view synthesis unavailable")
        return result

    @app.post("/views/drafts/{draft_id}/save")
    def save_view_draft(draft_id: str, claims: TenantClaims = Depends(current_tenant)):
        """Persist a Balto draft via the EXISTING saved-view store (the explicit user save).

        Drafts are tenant-keyed: another tenant's draft id 404s here, never resolves. The spec
        is re-validated by SavedViews.save; discarding a draft is simply never calling this.
        """
        if deps.view_synthesizer is None:
            raise HTTPException(status_code=503, detail="view synthesis not configured")
        try:
            row = deps.view_synthesizer.save_draft(
                claims.tenant_id, draft_id, created_by=claims.sub,
            )
        except view_spec.ValidationError as e:  # curated validation outcome -> client-actionable 422
            raise HTTPException(status_code=422, detail=str(e))
        except Exception:  # noqa: BLE001 — 422-detail hygiene: logged, never echoed
            logger.exception("save_view_draft: drafted spec rejected by an unexpected error")
            raise HTTPException(status_code=422, detail="draft view spec failed validation")
        if row is None:
            raise HTTPException(status_code=404, detail="no such draft")
        return row

    # --- dashboards (spec_version 2) — named compositions of saved views -------------------
    # Additive CRUD over the SAME saved-view store: a dashboard is a saved_views row whose
    # spec_json carries kind="dashboard" (no new table, RLS unchanged). Tenant identity comes
    # only from the verified claim, exactly like /views.

    @app.get("/dashboards")
    def list_dashboards(claims: TenantClaims = Depends(current_tenant)):
        dashboards = deps.saved_views.list_dashboards(claims.tenant_id)
        _assert_request_tenant(dashboards, claims)
        return {"dashboards": dashboards}

    @app.get("/dashboards/{view_id}")
    def get_dashboard(view_id: str, claims: TenantClaims = Depends(current_tenant)):
        resolved = deps.saved_views.resolve_dashboard(claims.tenant_id, view_id)
        if resolved is None:
            raise HTTPException(status_code=404, detail="no such dashboard")
        dash, views = resolved
        _assert_request_tenant([dash, *views.values()], claims)
        return {"dashboard": dash, "views": views}

    @app.post("/dashboards")
    def save_dashboard(body: SaveViewBody, claims: TenantClaims = Depends(current_tenant)):
        # The discriminator is required, not inferred: a body that isn't a dashboard spec is a
        # caller bug, answered as a validation failure — never silently coerced.
        if body.spec.get("kind") != "dashboard":
            raise HTTPException(status_code=422, detail='spec.kind must be "dashboard"')
        try:
            return deps.saved_views.save(claims.tenant_id, body.spec,
                                         source_prompt=body.source_prompt, created_by=claims.sub)
        except view_spec.ValidationError as e:  # curated validation outcome -> client-actionable 422
            raise HTTPException(status_code=422, detail=str(e))
        except Exception:  # noqa: BLE001 — 422-detail hygiene: logged, never echoed
            logger.exception("save_dashboard: spec rejected by an unexpected error")
            raise HTTPException(status_code=422, detail="dashboard spec failed validation")

    @app.post("/chat")
    def chat(body: ChatBody, claims: TenantClaims = Depends(current_tenant)):
        # Kill switch gates the WHOLE turn, on every runtime, at the API boundary (Greenlight
        # audit P1): the self-hosted (HIPAA-fallback) runtime's tool loop has no pause check of
        # its own, and on Managed Agents a paused tenant shouldn't keep queueing proposals from
        # chat either. Approval EXECUTION is separately gated in decide_approval above.
        if deps.killswitch.is_paused(claims.tenant_id):
            raise HTTPException(status_code=409, detail="kill switch engaged — agents are paused")
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

    # Authed per-tenant Sidecar (the agentic layer): GET grounded next-action suggestions over the
    # CRM, POST accept enqueues a DRAFT into THIS app's Greenlight (gate_deps) — same queue/autonomy/
    # kill switch as every other gated action. Unconfigured deps answer the honest 503.
    if deps.sidecar is not None:
        from api.sidecar_routes import mount_sidecar
        mount_sidecar(app, deps.sidecar, current_tenant, gate_deps=deps)

    # Authed per-tenant contacts/companies directory (the real Contacts tab). Claims-bound,
    # READ-ONLY this cycle (no gate deps — CRM writes arrive with a later update_contact tool
    # through the gate). Unconfigured deps answer honest 503s, never invented rows.
    if deps.contacts is not None:
        from api.contacts_routes import mount_contacts
        mount_contacts(app, deps.contacts, current_tenant)

    # Authed per-tenant CRM tasks/reminders (the real Tasks surface). Claims-bound; a direct user
    # write (no agent send), so it does NOT route through Greenlight. Unconfigured deps answer
    # honest 503s, never invented rows.
    if deps.tasks is not None:
        from api.tasks_routes import mount_tasks
        mount_tasks(app, deps.tasks, current_tenant)

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

    # Authed per-tenant Cortex health (the #194 ml/health.py seam). Claims-bound, READ-ONLY,
    # metadata-only: the payload comes from ml.health.cortex_health over the registry manifest
    # listing — no artifact is ever deserialized in this path. Unconfigured deps answer the
    # honest "no_registry" shape, never invented model state.
    if deps.cortex is not None:
        from api.cortex_routes import mount_cortex
        mount_cortex(app, deps.cortex, current_tenant)

    # Authed per-tenant control surface (kill switch · autonomy dial · decision traces) —
    # always mounted: the deps defaults are in-memory and api/asgi.py wires the Pg-backed
    # stores in prod. Authorization decisions are documented in api/routes_control.py.
    from api.routes_control import mount_control
    mount_control(app, deps, current_tenant)

    # Authed per-tenant Sell (gamification) surface — GET /sell/me · /sell/leaderboard · /sell/quests
    # + POST /sell/nudge. Claims-bound like everything above (tenant + user from the verified JWT).
    # Always mounted: the reads answer an honest 503 when deps.points is inert (the default), and the
    # nudge rides THIS app's Greenlight as a draft-only proposal. api/asgi.py wires the real stores.
    from api.sell_routes import mount_sell
    mount_sell(app, deps, current_tenant)

    # Authed per-tenant view-data resolution (POST /views/{id}/data — the web data-loader's
    # dependency). Claims-bound like everything above; loads the saved view RLS-scoped, runs each
    # panel's CubeQuery through deps.cube carrying the verified-claim tenant (THE TRUST RULE), and
    # returns {rows:[...]}. Honest 503 when cube is unconfigured (never a 500, never fake rows).
    from api.cube_data_routes import mount_cube_data
    mount_cube_data(app, deps, current_tenant)

    # Authed per-tenant Agent Studio (playbook composer + starter library). Claims-bound like
    # everything above; definitions are schema-validated SPEC-NOT-CODE and activation registers
    # through the existing runtime seam with side-effects Greenlight-gated (draft-only).
    if deps.studio is not None:
        from api.routes_studio import mount_studio
        mount_studio(app, deps.studio, current_tenant)

    # Authed self-service billing (Stripe Customer Portal) — claims-bound like every authed route.
    if deps.billing is not None:
        from api.billing_routes import mount_billing
        mount_billing(app, deps.billing, current_tenant)

    # Public, pre-tenant signup + Stripe webhook routes (optional).
    if deps.signup is not None:
        from api.signup_routes import mount_signup
        mount_signup(app, deps.signup)

    # Public, unauthenticated lead capture (POST /public/leads) — validated, 1KB-capped,
    # per-IP rate-limited; the store is honest-503 until the prod gates select PgLeadStore.
    if deps.public is not None:
        from api.public_routes import mount_public
        mount_public(app, deps.public)

    # Public, unauthenticated support intake (POST /public/support) — validated, 2KB-capped,
    # per-IP rate-limited; the store is honest-503 until the prod gates select PgSupportStore.
    if deps.support is not None:
        from api.support_routes import mount_support
        mount_support(app, deps.support)

    # Authed per-tenant usage view (monthly quota counter + plan cap + Anthropic cost
    # attribution). Claims-bound, READ-ONLY; inert-default deps answer a stable zeroed shape
    # (never 503). EXEMPT from the quota meter (reading usage never burns quota).
    if deps.usage is not None:
        from api.usage_routes import mount_usage
        mount_usage(app, deps.usage, current_tenant)

    # Authed per-tenant GDPR/portability data export (GET /account/export). Claims-bound;
    # read-only egress over contacts, companies, deals, saved views, and knowledge doc metadata.
    # RLS-scoped via the same SET LOCAL discipline every other authed route uses. Honest 503
    # when all stores are unconfigured — never invented rows, never deletions.
    if deps.account is not None:
        from api.account_routes import mount_account
        mount_account(app, deps.account, current_tenant)

    # Authed per-tenant account teardown (POST /account/delete — the GDPR/offboarding erasure
    # sibling of /account/export). Claims-bound + requires a confirm token matching the verified
    # tenant; deletes only the tenant's OWN mutable rows (append-only audit tables are reported
    # retained, never force-deleted), RLS-scoped, idempotent, SAVEPOINT-per-table. Inert by
    # default (deleter=None -> honest 503): going live is a deliberate api/asgi.py wiring step,
    # so a destructive path never ships functional without explicit review.
    if deps.account_delete is not None:
        from api.account_delete_routes import mount_account_delete
        mount_account_delete(app, deps.account_delete, current_tenant)

    # Public (unauth) status page feed (GET /public/status + /api/status): per-subsystem
    # readiness from injected probes. No auth — it's the public status page. Inert default
    # (no probes) → honest "unknown" subsystems + an operational rollup (api answered).
    if deps.status is not None:
        from api.status_routes import mount_status
        mount_status(app, deps.status)

    # Authed persisted workspace settings (GET/PUT /account/settings — workspace name +
    # notification prefs). Claims-bound, RLS-scoped; honest 503 when the store is unconfigured.
    if deps.settings is not None:
        from api.settings_routes import mount_settings
        mount_settings(app, deps.settings, current_tenant)

    # Per-tenant module entitlements (GET/PUT /account/modules — the "your suite" surface). The web
    # app reads it to show only the enabled modules' surfaces; honest 503 when the store is
    # unconfigured (the web gate degrades to showing everything).
    if deps.modules is not None:
        from api.modules_routes import mount_modules
        mount_modules(app, deps.modules, current_tenant)

    # Per-tenant rate-limit + quota middleware. Installed ONLY when api/asgi.py provides a spec
    # (default None = off, so offline tests aren't throttled). The spec is (cls, kwargs); a
    # request with no/invalid tenant claim passes through (the route's auth dependency 401s),
    # health + the public/signup surface are exempt by prefix.
    if deps.limits_middleware is not None:
        cls, kwargs = deps.limits_middleware
        app.add_middleware(cls, **kwargs)

    return app
