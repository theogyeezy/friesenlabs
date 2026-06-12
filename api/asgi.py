"""Production ASGI entrypoint for the control-plane API (the container runs this).

Builds the FastAPI app from environment config. Boots with the in-memory stores by default so the
container starts and `/healthz` passes the ALB health check; production swaps in the Aurora-backed
stores when a crm_app DSN is configured. The real Cognito verifier is wired from env.

The AI plane is wired through two seams, both constructed lazily from env (shared/config.py names)
and degrading to the parked stub behavior when unconfigured — a deployed API without creds behaves
exactly as today (`/chat` 503, `/healthz` 200, executor noop):

  - `make_conversation_factory(...)`: tenant_id (verified claim) -> `conv.session.Conversation`
    riding the tenant's PERSISTED Managed Agents ids (`tenant_workspaces` row via the
    WorkspaceStore — provisioning happens at signup, never in the request path). No row / no
    coordinator id => the factory returns None and `/chat` keeps its graceful
    503 "not provisioned" path.
  - `make_executor(...)`: dispatches gate-approved actions through the TRUSTED tool registry with
    a ToolContext bound to the action's tenant (set upstream from the verified JWT claim only).
    The gate invariant holds — the executor is only ever invoked on Decision.AUTO, never on
    block/deny — and side-effecting tools STILL route to Greenlight inside `Tool.invoke`
    (the Phase 4 base class guarantees draft-only).
"""
from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any, Callable

from fastapi import HTTPException

from agents.runtime import FakeRuntime, get_runtime
from agents.tools.base import ToolContext
from agents.tools.cube_client import cube_client_from_env
from agents.tools.spec_generator import AnthropicSpecGenerator
from agents.workspace_store import PgWorkspaceStore, WorkspaceStore
from api.agents_routes import AgentsDeps
from api.app import ApiDeps, create_app
from api.auth import CognitoJwtVerifier, JwtVerifier
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight, PgApprovalStore
from api.control.killswitch import KillSwitch
from api.control.settings import PersistedAutonomyDial, PersistedKillSwitch
from api.control.traces import InMemoryTraceStore, PgTraceStore, TraceStore
from api.contacts_routes import ContactsDeps
from api.control.types import Action
from api.cortex_routes import CortexDeps
from api.deals_routes import DealsDeps
from api.sidecar_routes import SidecarDeps
from api.knowledge_routes import KnowledgeDeps
from api.pg_clients import PgControlSettingsStore, PgCrmClient, PgRagClient
from api.limits import PlanResolver, TenantLimitsMiddleware
from api.usage import PgCostRecorder, PgPlanLookup, PgUsageStore
from api.usage_routes import UsageDeps
from api.views import PgSavedViewStore, SavedViews
from api.workflows_routes import WorkflowsDeps
from conv.cache import TenantConversationCache
from conv.session import Conversation
from conv.synthesizer import AnthropicSynthesizer
from conv.view_patcher import AnthropicViewPatcher
from conv.views import ViewSynthesizer
from ml.predictions import PgPredictionLog
from ml.registry import registry_from_env
from api.account_routes import AccountDeps
from api.status_routes import StatusDeps
from api.modules_routes import ModulesDeps
from api.pg_settings import PgSettingsStore
from api.settings_routes import SettingsDeps
from api.routes_studio import StudioDeps, build_studio_deps
from shared.config import (
    ENV_ANTHROPIC_API_KEY,
    dsn_from_env,
    load,
    tenant_limits_enabled,
)
from shared.semantic_catalog import CATALOG_PATH, catalog_members_or_none

log = logging.getLogger("api.asgi")


def _catalog_allowed_members() -> set[str] | None:
    """The governed Cube member catalog (semantic/model/catalog.json) for
    `SavedViews(allowed_members=...)` — #195's deliberate follow-up, now that the api image
    ships the file (api/Dockerfile). FALLBACK: when the catalog isn't present (an older image,
    a stripped fileset) member validation is SKIPPED — exactly the pre-#195 behavior — and a
    STRUCTURED warning is emitted so the missing file is visible in CloudWatch instead of
    silently weakening save-time validation."""
    members = catalog_members_or_none()
    if members is None:
        log.warning(
            "semantic catalog missing — saved-view member validation is OFF "
            "(event=semantic_catalog_missing path=%s)",
            CATALOG_PATH,
            extra={"event": "semantic_catalog_missing", "catalog_path": CATALOG_PATH},
        )
    return members


def _verifier() -> JwtVerifier:
    pool = os.environ.get("COGNITO_USER_POOL_ID")
    client = os.environ.get("COGNITO_CLIENT_ID")
    region = os.environ.get("AWS_REGION", "us-east-1")
    if pool and client:
        # VERIFY: real JWKS verification against the pool (BLOCKED until creds).
        return CognitoJwtVerifier(pool_id=pool, client_id=client, region=region)
    # No pool configured (local/dev): a verifier that rejects everything but lets /healthz serve.

    class _RejectAll:
        def verify(self, token):  # noqa: D401
            raise RuntimeError("auth not configured")

    return _RejectAll()


def make_conversation_factory(
    *,
    workspace_store: WorkspaceStore,
    runtime_factory: Callable[[dict], Any],
    greenlight: Greenlight | None = None,
    rag: Any = None,
    crm: Any = None,
    rag_crm: Any = None,
    cube: Any = None,
    cortex: Any = None,
    prediction_log: Any = None,
    synthesizer: Any = None,
    spec_generator: Any = None,
    cost_recorder: Any = None,
    today: Callable[[], date] | None = None,
) -> Callable[[str], Any]:
    """Build the `/chat` conversation factory: tenant_id -> Conversation | None.

    THE TRUST RULE: `tenant_id` arrives from the verified JWT claim (threaded by `api.app`);
    nothing here reads env/headers/bodies for it. The tenant's persisted Managed Agents ids are
    looked up in the WorkspaceStore (RLS-scoped); a missing/incomplete row means the tenant is not
    provisioned => return None, which `/chat` turns into the graceful 503 — never a 500, and never
    an on-the-fly roster build in the request path. A row holding the offline 'stub-' placeholder
    ids (written by the _Noop agent plane) is likewise refused with a clear 503 when the runtime
    is real — only FakeRuntime may ride stub ids.

    `runtime_factory(row)` builds the runtime for THAT tenant's row (a fresh
    `ManagedAgentsRuntime` bound to the row's environment_id in prod; a FakeRuntime in tests).
    """

    def factory(tenant_id: str):
        row = workspace_store.get(tenant_id)
        if row is None or not row.get("coordinator_id") or not row.get("environment_id"):
            return None  # not provisioned -> /chat's graceful 503 path

        runtime = runtime_factory(row)
        stub_ids = sorted({
            v for v in (row.get("workspace_id"), row.get("environment_id"),
                        row.get("coordinator_id"))
            if isinstance(v, str) and v.startswith("stub-")
        })
        if stub_ids and not isinstance(runtime, FakeRuntime):
            # Offline provisioning (the prod_deps _Noop agent plane) persisted PLACEHOLDER ids.
            # A real runtime pointed at them would surface an opaque Anthropic error as a 500 —
            # refuse up front with a clear 503 instead. FakeRuntime (tests/dev) accepts any ids.
            raise HTTPException(status_code=503, detail=(
                f"tenant agent plane holds offline stub ids ({', '.join(stub_ids)}); chat is "
                "unavailable until this tenant is re-provisioned against live Managed Agents"
            ))

        # Tool-side CRM client: a fresh per-request tenant adapter (never shared across requests).
        db = crm.for_tenant(tenant_id) if hasattr(crm, "for_tenant") else crm

        return Conversation(
            tenant_id=tenant_id,
            # A CALLABLE today provider, resolved fresh per turn inside the Conversation —
            # the per-tenant cache keeps Conversations alive across days, so a date frozen at
            # construction time would silently rot every "this month"/"last quarter" answer.
            today=today or date.today,
            runtime=runtime,
            coordinator_id=row["coordinator_id"],
            environment_id=row["environment_id"],
            rag=rag,
            crm=db,
            rag_crm=rag_crm,
            cube=cube,
            cortex=cortex,                  # persistent Cortex registry -> run_model scores live
            prediction_log=prediction_log,  # run_model logs each score -> predictions (flywheel)
            synthesizer=synthesizer,
            spec_generator=spec_generator,  # default ctx.extra['generate_spec'] for build_view
            greenlight=greenlight,
            cost_recorder=cost_recorder,    # per-turn Anthropic token usage -> cost_events
        )

    return factory


def make_executor(
    *,
    greenlight: Greenlight | None = None,
    crm: Any = None,
    rag: Any = None,
    cube: Any = None,
    cortex: Any = None,
    spec_generator: Any = None,
) -> Callable[[Action], Any]:
    """Build the real tool executor: dispatch through `agents.tools.registry` with a ToolContext
    bound to the action's tenant.

    - Registry resolution: `resolve(action.name)` raises on unknown tools (never default-allow);
      whether the tool is side-effecting comes from the TOOL'S OWN class, never the request.
    - Tenant binding: `Action.tenant_id` was set by the API from the verified claim ONLY; an
      action without it is refused (loudly) rather than run unscoped.
    - Gate invariant: the gate calls this executor only on Decision.AUTO — never on block/deny —
      and even then a side-effecting tool's `invoke` routes a PROPOSAL to Greenlight without
      performing the side effect (the Phase 4 base-class draft-only guarantee).
    """
    from agents.tools.registry import resolve  # trusted, server-side registry

    def execute(action: Action):
        tool = resolve(action.name)  # KeyError on unknown tools — reject, never default-allow
        tenant_id = getattr(action, "tenant_id", None)
        if not tenant_id:
            raise ValueError(
                "action carries no tenant binding — the API must set Action.tenant_id from the "
                "verified JWT claim before the gate runs"
            )
        # Fresh per-call DB adapter (PgCrmClient.binding()) so tenant state is never shared.
        db = crm.binding() if hasattr(crm, "binding") else crm
        # Fresh extra dict per call — tool invocations must never share mutable context state.
        extra: dict = {}
        if spec_generator is not None:
            extra["generate_spec"] = spec_generator  # default build_view generator (env-guarded)
        ctx = ToolContext(
            tenant_id=tenant_id,
            agent=action.agent,
            db=db,
            cube=cube,
            rag=rag,
            cortex=cortex,  # persistent per-tenant model registry (ml.registry) -> run_model
            greenlight=greenlight,
            extra=extra,
        )
        return tool.invoke(ctx, **(action.payload or {}))

    return execute


def _managed_runtime_factory(api_key: str) -> Callable[[dict], Any]:
    """Prod runtime factory: a fresh ManagedAgentsRuntime per conversation, bound to the TENANT'S
    persisted environment id (never an instance-global serving every tenant). Construction is lazy
    (no network until the first call) and the org key stays in the API task only."""

    def build(row: dict):
        return get_runtime(
            {"runtime": "managed", "api_key": api_key, "environment_id": row.get("environment_id")}
        )

    return build


def build_app():
    # Persistent Cortex registry (S3 wins over LocalFs; all-unset -> None and run_model degrades
    # cleanly) + the default view-spec generator. Both are env-built, lazy, and offline-safe: the
    # registry touches no AWS until first blob access, and the generator is constructed ONLY when
    # the org key is present — without it, ctx.extra carries no 'generate_spec' and build_view
    # keeps its explicit raise (the current unconfigured behavior, preserved).
    cortex = registry_from_env()
    api_key = os.environ.get(ENV_ANTHROPIC_API_KEY)
    spec_generator = AnthropicSpecGenerator(api_key=api_key) if api_key else None

    # Save-time member validation against the governed catalog (None = skipped, with the
    # structured warning — see _catalog_allowed_members). Resolved once at boot: the catalog is
    # a build artifact of the image, not runtime state.
    allowed_members = _catalog_allowed_members()

    # Aurora-backed stores when a crm_app DSN is configured; else in-memory (boots for /healthz).
    dsn = dsn_from_env()
    if dsn:
        greenlight = Greenlight(store=PgApprovalStore(dsn))
        saved_views = SavedViews(store=PgSavedViewStore(dsn), allowed_members=allowed_members)
        workspace_store: WorkspaceStore | None = PgWorkspaceStore(dsn)
        crm = PgCrmClient(dsn)
        rag = PgRagClient(dsn)
        # Persisted control plane (the accountability surface): kill switch + autonomy dial over
        # tenant_settings, decision traces over the traces table — ALL tenant-scoped via the same
        # per-op SET LOCAL pattern. The short-TTL read-through facades mean a flip on one API task
        # is seen by every peer within seconds; the gate (POST /actions, /deals move-stage) and
        # the approval-decide path consult these SAME objects.
        control_settings = PgControlSettingsStore(dsn)
        killswitch = PersistedKillSwitch(control_settings)
        autonomy_dial = PersistedAutonomyDial(control_settings)
        autonomy_config = AutonomyConfig(level_provider=autonomy_dial.provider)
        trace_store: TraceStore = PgTraceStore(dsn)  # the gate's per-run writes land in Pg
        # Per-tenant usage counter (monthly quota) + Anthropic cost attribution — same per-op
        # SET LOCAL RLS plumbing as the trace store. The cost recorder is observational
        # (never blocks a turn); the usage store backs the quota gate + GET /usage. REUSE the
        # PgCrmClient's pool (it IS a _PgTenantClient) so these open NO extra connection pools —
        # the Aurora connection budget is finite and the api task already builds several Pg stores.
        usage_store: Any = PgUsageStore(client=crm)
        cost_recorder: Any = PgCostRecorder(client=crm)
        plan_lookup: Any = PgPlanLookup(client=crm)
        # Governed metrics: live only when CUBE_ENDPOINT + CUBEJS_API_SECRET_VALUE are BOTH
        # injected (api_cube_env flag). None only when both are unset; endpoint-without-secret
        # (cube_endpoint wired, flag not yet flipped — the live state at this commit) yields the
        # DEGRADED client: every call returns {"status": "unconfigured", rows: []} instead of the
        # bare empty-rows shape — visible misconfiguration by design, not byte-identical.
        cube = cube_client_from_env()
        # Real tool executor: registry dispatch with tenant-bound clients (RLS via SET LOCAL).
        executor = make_executor(greenlight=greenlight, crm=crm, rag=rag, cube=cube,
                                 cortex=cortex, spec_generator=spec_generator)
    else:
        greenlight = Greenlight()
        saved_views = SavedViews(allowed_members=allowed_members)
        workspace_store = None
        crm = rag = None
        cube = None
        # Unconfigured: in-memory control plane (instance-local — fine for /healthz-only boots).
        killswitch = KillSwitch()
        autonomy_dial = None
        autonomy_config = AutonomyConfig()
        trace_store = InMemoryTraceStore()
        usage_store = None      # no DSN -> /usage answers a zeroed shape; quota gate is inert
        cost_recorder = None
        plan_lookup = None
        executor = lambda action: {"status": "noop"}  # noqa: E731 — unconfigured: today's stub

    # /chat factory needs BOTH the DB (workspace rows + tool clients) and the org Anthropic key
    # (API task only — never the worker). Without either, /chat keeps returning the graceful 503.
    if dsn and api_key and workspace_store is not None:
        # #147: wrap in the per-tenant cache — ONE Conversation (one MA session) per tenant
        # across requests, so worker-resolved thread reports are read by the NEXT turn instead
        # of being orphaned in a session nothing revisits. The cache (conv/cache.py) holds no
        # lock across Anthropic I/O, serializes turns per tenant only, and evicts LRU+TTL
        # (UPLIFT_CONV_CACHE_MAX / UPLIFT_CONV_CACHE_TTL_SECONDS).
        conversation_factory = TenantConversationCache(make_conversation_factory(
            workspace_store=workspace_store,
            runtime_factory=_managed_runtime_factory(api_key),
            greenlight=greenlight,
            rag=rag,
            crm=crm,
            cube=cube,
            cortex=cortex,
            prediction_log=PgPredictionLog(dsn),  # run_model -> predictions (Cortex flywheel)
            synthesizer=AnthropicSynthesizer(api_key=api_key),
            spec_generator=spec_generator,
            cost_recorder=cost_recorder,
        ))
    else:
        conversation_factory = lambda tenant_id: None  # noqa: E731 — unconfigured: /chat 503

    # Agent Studio: wire a PER-TENANT registrar so activate/run register + drive a REAL crew
    # instead of being record-only. The factory resolves the tenant's persisted Managed Agents
    # environment from the workspace store and binds a runtime to it; a tenant without a provisioned
    # environment resolves to None -> the honest record-only path. Gated on the agent plane being
    # configured (api_key + the DSN-backed workspace store); otherwise the store-only/inert default.
    playbook_dispatcher = None  # in-process event producer (deals/contacts); None = inert
    if dsn and api_key and workspace_store is not None:
        from agents.playbooks.dispatch import BackgroundDispatcher, PlaybookDispatcher  # noqa: PLC0415 — lazy
        from agents.playbooks.store import PgPlaybookRunStore, PgPlaybookStore  # noqa: PLC0415
        from agents.runtime import get_runtime  # noqa: PLC0415 — lazy

        def _studio_registrar_factory(tenant_id, _ws=workspace_store, _key=api_key):
            row = _ws.get(tenant_id) or {}
            env_id = row.get("environment_id")
            if not env_id:
                return None  # tenant not provisioned -> honest record-only
            runtime = get_runtime({"runtime": "managed", "api_key": _key, "environment_id": env_id})
            return (runtime, env_id, row.get("vault_id"))

        playbook_store = PgPlaybookStore(dsn)
        playbook_run_store = PgPlaybookRunStore(dsn)  # run history (audit P0-2; lazy pool)

        # The in-process EVENT leg (audit P0-4): domain events (lead.created from POST /contacts,
        # deal.created from POST /deals) fire the tenant's ACTIVE event-playbooks through the
        # SAME runner seam the manual run route uses — per-tenant runtime resolution, persisted
        # run history, draft-only through Greenlight. Unprovisioned tenants land an honest
        # error record (never a crash — runner.run contains everything).
        def _dispatch_run_playbook(tenant_id, playbook_id, event,
                                   _factory=_studio_registrar_factory,
                                   _store=playbook_store, _runs=playbook_run_store):
            from agents.playbooks import runner as runner_mod  # noqa: PLC0415 — lazy

            resolved = _factory(tenant_id)
            if resolved is None:
                record = runner_mod.RunRecord(
                    playbook_id=str(playbook_id), tenant_id=str(tenant_id), status="error",
                    trigger={"kind": event.kind, "name": event.name},
                    error="tenant not provisioned (no environment_id)")
                try:
                    _runs.record(tenant_id, record.as_dict())
                except Exception:  # noqa: BLE001 — history is best-effort
                    pass
                return record
            runtime, env_id, vault_id = resolved
            return runner_mod.run(runtime, _store, tenant_id, playbook_id, event,
                                  environment_id=env_id, vault_id=vault_id,
                                  run_store=_runs)

        # Fire-and-forget: a user-facing create must never block on an agent run (an MA
        # coordinator turn can take tens of seconds). The run's outcome lands in the
        # persisted run history + Greenlight queue, never in the producer's request.
        playbook_dispatcher = BackgroundDispatcher(
            PlaybookDispatcher(playbook_store, _dispatch_run_playbook))
        studio_deps = StudioDeps(
            store=playbook_store,
            run_store=playbook_run_store,
            registrar_factory=_studio_registrar_factory,
            # Dispatch honesty (audit P0-4): the schedule leg is live only when the owner flips
            # the EventBridge rule AND stamps PLAYBOOK_DISPATCH_ENABLED=1 on the api task (the
            # same go-live act — GO_LIVE_CHECKLIST); the event leg is live right here.
            scheduling_enabled=os.environ.get("PLAYBOOK_DISPATCH_ENABLED") == "1",
            events_enabled=True,
        )
    else:
        studio_deps = build_studio_deps()

    # Balto view synthesis (conv/views.py): rides the SAME SavedViews facade + Cube client +
    # spec generator the rest of the app uses. Honest degradation per piece: no cube/secret ->
    # 'unavailable' (route 503), no generator -> 'unavailable' — never a hallucinated view.
    view_synthesizer = ViewSynthesizer(
        saved_views=saved_views, cube=cube, generator=spec_generator,
    )

    # NL refine (POST /views/{id}/refine): the EDIT sibling of the Balto CREATE generator. Built
    # from the SAME org Anthropic key seam — None when unconfigured, so the route keeps its honest
    # 501 (view_patcher missing). The patched spec is checked against the governed catalog here AND
    # re-validated against the tenant's live members by SavedViews.refine_nl before persisting.
    view_patcher = (
        AnthropicViewPatcher(api_key=api_key, allowed_members=allowed_members)
        if api_key else None
    )

    from api.prod_deps import build_signup_deps

    # ONE verifier instance: the routes' auth dependency AND the rate-limit middleware below both
    # read the tenant from the SAME verification (THE TRUST RULE — no header/body tenant anywhere).
    verifier = _verifier()

    signup_deps = build_signup_deps(workspace_store=workspace_store)
    # Authed self-service billing (Stripe Customer Portal): reuse the SAME Stripe adapter the
    # payment plane uses + the SAME account store (so the stripe_customer_id mapping is the one
    # checkout wrote). The portal session is claims-bound in api/billing_routes.py. The return URL
    # is operator-configured (STRIPE_PORTAL_RETURN_URL — Lane Nick injects it; empty = Stripe
    # default). Built only when the signup deps carry a payment adapter + an account store.
    from api.billing_routes import BillingDeps

    billing_deps = None
    _pay = getattr(signup_deps, "payment", None)
    _accounts = getattr(signup_deps, "accounts", None)
    if _pay is not None and _accounts is not None:
        billing_deps = BillingDeps(
            stripe=getattr(_pay, "stripe", None),
            accounts_store=getattr(_accounts, "store", None),
            return_url=os.environ.get("STRIPE_PORTAL_RETURN_URL", ""),
        )

    # Phase-2 module billing ("selection sets the price"): reconcile a tenant's Stripe subscription
    # items to its enabled modules on PUT /account/modules. Built from the SAME Stripe adapter +
    # account store as the portal, plus the per-module Price ids from env (STRIPE_PRICE_ID_MODULE_*).
    # from_env returns None until at least one per-module Price is configured (owner mints them in
    # Stripe), so this is fully inert today — the toggle persists + re-gates the UI, no charge moves.
    from api.module_billing import from_env as _module_billing_from_env

    module_billing = _module_billing_from_env(
        accounts_store=getattr(_accounts, "store", None) if _accounts is not None else None,
        stripe=getattr(_pay, "stripe", None) if _pay is not None else None,
        env=os.environ,
    )

    deps = ApiDeps(
        verifier=verifier,
        greenlight=greenlight,
        saved_views=saved_views,
        cube=cube,  # backs POST /views/{id}/data (cube_data_routes); None -> honest 503
        conversation_factory=conversation_factory,
        autonomy_config=autonomy_config,
        executor=executor,
        crm=crm,
        # The persisted control plane (Pg-backed when the DSN is configured; in-memory else).
        # killswitch + trace_store are the gate's own deps; autonomy_dial backs /control/autonomy
        # and its provider is what autonomy_config resolves — one level, dial and gate agree.
        killswitch=killswitch,
        trace_store=trace_store,
        autonomy_dial=autonomy_dial,
        # Balto: POST /views/synthesize + /views/drafts/{id}/save (NL view creation from chat).
        view_synthesizer=view_synthesizer,
        # NL refine: POST /views/{id}/refine (the EDIT path). None when the org key is unconfigured
        # -> the route answers its honest 501. Built from the same ENV_ANTHROPIC_API_KEY seam.
        view_patcher=view_patcher,
        # mounts /signup, /verify-*, /checkout, /webhooks/stripe; provisioning persists the
        # tenant's Managed Agents ids into tenant_workspaces when the DB is configured.
        signup=signup_deps,
        # mounts POST /billing/portal-session + GET /billing (authed, Stripe Customer Portal).
        billing=billing_deps,
        # /deals (the real Pipeline board) rides the SAME PgCrmClient instance the executor +
        # /chat tool clients use — one pool, one SET LOCAL discipline. crm is None when the
        # DSN is unconfigured, so the routes answer their honest 503s. The dispatcher makes
        # POST /deals fire deal.created event-playbooks (inert when None — guarded producer).
        deals=DealsDeps(crm=crm, dispatcher=playbook_dispatcher),
        # /contacts + /companies (the real Contacts directory) — the same single PgCrmClient.
        # The dispatcher makes POST /contacts fire lead.created (the shipped
        # lead_followup_drafter template's trigger — audit P0-4).
        contacts=ContactsDeps(crm=crm, dispatcher=playbook_dispatcher),
        # /sidecar (the real agentic layer) — reads the SAME PgCrmClient and proposes Greenlight
        # drafts. crm is None when the DSN is unconfigured -> honest 503.
        sidecar=SidecarDeps(crm=crm),
        # /agents (the real Agents tab) rides the SAME PgWorkspaceStore instance the /chat
        # conversation factory + signup provisioning use — one pool, one SET LOCAL discipline.
        # workspace_store is None when the DSN is unconfigured, so the route answers its
        # honest 503 (never an invented crew state).
        agents=AgentsDeps(workspace_store=workspace_store),
        # /workflows (the real Workflows tab) reads the provisioning machine ARN from
        # Config (PROVISIONING_SFN_ARN, REQ-005 — un-injected on the live task today, so
        # the route answers its honest not-configured shape). The boto3 client is lazy
        # (request path only); the api task role's missing read perms degrade to the
        # honest pending-IAM shape until REQ-009 (see api/workflows_routes.py).
        workflows=WorkflowsDeps(state_machine_arn=load().provisioning_sfn_arn or None),
        # /knowledge (the real Knowledge tab) rides the SAME PgRagClient instance the executor +
        # /chat RAG tool use — one pool, one SET LOCAL discipline. The inventory is a plain
        # aggregate (no embedder); search embeds lazily via Titan (Bedrock, env-key-gated) and
        # degrades honestly. rag is None when the DSN is unconfigured -> honest 503.
        knowledge=KnowledgeDeps(rag=rag),
        # GET /cortex/health (the #194 ml/health.py seam) rides the SAME env-built registry
        # run_model scores with, plus a PgPredictionLog over the shared crm_app DSN (per-op
        # SET LOCAL — RLS) for the live-AUC drift leg. Unconfigured pieces degrade honestly
        # inside ml.health.cortex_health ("no_registry" / insufficient-evidence drift).
        cortex=CortexDeps(registry=cortex,
                          prediction_log=PgPredictionLog(dsn) if dsn else None),
        # GET /usage: the tenant's monthly usage counter + plan cap + Anthropic cost summary.
        # The plan resolver is SHARED with the rate-limit middleware below so the cap reported
        # equals the cap enforced. With no DSN the stores are None -> a stable zeroed shape.
        usage=UsageDeps(
            usage_store=usage_store,
            cost_recorder=cost_recorder,
            plan_resolver=PlanResolver(fetch=plan_lookup.plan if plan_lookup else None),
        ),
        # GET /account/export (GDPR/portability egress) — the SAME crm/rag/saved_views instances
        # every other authed route rides. Inert (None stores) only when no DSN; with Aurora wired
        # the export is live. (account_delete is DELIBERATELY left to its inert default — a
        # destructive teardown ships non-functional until an explicit owner wiring step. At that
        # step pass BOTH deleter=PgAccountDeleter(dsn) AND, when INTEGRATIONS_REAL_SECRETS is on,
        # secret_writer=Boto3SecretWriter() — the erasure response then includes the connector-
        # vault purge (uplift/{tenant}/{source} tokens must not outlive the account).)
        # Agent Studio with the per-tenant registrar wired (activate/run drive a real crew when the
        # tenant is provisioned; honest record-only otherwise) — see _studio_registrar_factory above.
        studio=studio_deps,
        account=AccountDeps(crm=crm, rag=rag, saved_views=saved_views) if dsn else AccountDeps(),
        # GET/PUT /account/modules — per-tenant module entitlements (the "your suite" surface the web
        # gates its nav/routes against). Wired LIVE (PgSettingsStore over tenant_settings) so the app
        # can read the enabled set; GET degrades to the default catalog if the column predates the
        # live migrate. (account_delete + settings stay inert by their own gates.)
        modules=ModulesDeps(store=PgSettingsStore(dsn), billing=module_billing) if dsn else ModulesDeps(),
        # GET/PUT /account/settings — persisted workspace name + notification prefs. Wired LIVE
        # (PgSettingsStore over tenant_settings, the SAME store ModulesDeps rides) so the panel
        # stops answering its inert 503; the all-None SettingsDeps() default stands only with no DSN.
        settings=SettingsDeps(store=PgSettingsStore(dsn)) if dsn else SettingsDeps(),
        # GET /public/status — per-subsystem readiness. The "api" component is always operational
        # (this endpoint answered); these probes report whether each subsystem is WIRED on this
        # deployment (the API process is up + its dep is configured), not a deep liveness ping —
        # an honest, strict improvement over the previous permanently-"unknown" subsystems. Ingest
        # runs on a SEPARATE Fargate task the API can't reach, so it stays honestly "unknown".
        status=StatusDeps(
            data_plane=(lambda: "operational") if dsn else None,
            agent_plane=(lambda: "operational") if (workspace_store is not None and api_key) else None,
            ingest=None,
        ),
        # Per-tenant rate-limit + quota middleware spec (cls, kwargs). Installed only when
        # tenant_limits_enabled() (default ON) — a request with no/invalid tenant claim passes
        # through (the route auth 401s); health + public/signup are prefix-exempt. The middleware
        # verifies the SAME bearer the routes do to read the tenant claim (THE TRUST RULE).
        limits_middleware=(
            (TenantLimitsMiddleware, {
                "verifier": verifier,
                "usage_store": usage_store,
                "plan_resolver": PlanResolver(fetch=plan_lookup.plan if plan_lookup else None),
            })
            if tenant_limits_enabled() else None
        ),
    )
    app = create_app(deps)
    # /onboarding (first-run experience: per-tenant checklist state + one-click load-sample) —
    # mounted here on the SAME crm_app DSN every live surface rides (per-op SET LOCAL RLS). The
    # all-None default (no DSN) makes GET serve the honest fresh-tenant default and PUT/load-sample
    # answer the honest 503. Claims-bound via the SAME make_current_tenant(verifier) dependency the
    # rest of the app uses — the tenant is ONLY ever the verified JWT claim (THE TRUST RULE).
    from api.auth import make_current_tenant
    from api.onboarding_routes import deps_from_dsn as onboarding_deps_from_dsn, mount_onboarding
    mount_onboarding(app, onboarding_deps_from_dsn(dsn), make_current_tenant(deps.verifier))
    return app


app = build_app()
