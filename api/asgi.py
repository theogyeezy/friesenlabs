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

import os
from datetime import date
from typing import Any, Callable

from fastapi import HTTPException

from agents.runtime import FakeRuntime, get_runtime
from agents.tools.base import ToolContext
from agents.tools.spec_generator import AnthropicSpecGenerator
from agents.workspace_store import PgWorkspaceStore, WorkspaceStore
from api.app import ApiDeps, create_app
from api.auth import CognitoJwtVerifier, JwtVerifier
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight, PgApprovalStore
from api.control.types import Action
from api.pg_clients import PgCrmClient, PgRagClient
from api.views import PgSavedViewStore, SavedViews
from conv.session import Conversation
from conv.synthesizer import AnthropicSynthesizer
from ml.registry import registry_from_env
from shared.config import ENV_ANTHROPIC_API_KEY, dsn_from_env


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
    cortex: Any = None,
    synthesizer: Any = None,
    spec_generator: Any = None,
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
            today=(today or date.today)(),
            runtime=runtime,
            coordinator_id=row["coordinator_id"],
            environment_id=row["environment_id"],
            rag=rag,
            crm=db,
            rag_crm=rag_crm,
            cortex=cortex,                  # persistent Cortex registry -> run_model scores live
            synthesizer=synthesizer,
            spec_generator=spec_generator,  # default ctx.extra['generate_spec'] for build_view
            greenlight=greenlight,
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

    # Aurora-backed stores when a crm_app DSN is configured; else in-memory (boots for /healthz).
    dsn = dsn_from_env()
    if dsn:
        greenlight = Greenlight(store=PgApprovalStore(dsn))
        saved_views = SavedViews(store=PgSavedViewStore(dsn))
        workspace_store: WorkspaceStore | None = PgWorkspaceStore(dsn)
        crm = PgCrmClient(dsn)
        rag = PgRagClient(dsn)
        # Real tool executor: registry dispatch with tenant-bound clients (RLS via SET LOCAL).
        executor = make_executor(greenlight=greenlight, crm=crm, rag=rag,
                                 cortex=cortex, spec_generator=spec_generator)
    else:
        greenlight = Greenlight()
        saved_views = SavedViews()
        workspace_store = None
        crm = rag = None
        executor = lambda action: {"status": "noop"}  # noqa: E731 — unconfigured: today's stub

    # /chat factory needs BOTH the DB (workspace rows + tool clients) and the org Anthropic key
    # (API task only — never the worker). Without either, /chat keeps returning the graceful 503.
    if dsn and api_key and workspace_store is not None:
        conversation_factory = make_conversation_factory(
            workspace_store=workspace_store,
            runtime_factory=_managed_runtime_factory(api_key),
            greenlight=greenlight,
            rag=rag,
            crm=crm,
            cortex=cortex,
            synthesizer=AnthropicSynthesizer(api_key=api_key),
            spec_generator=spec_generator,
        )
    else:
        conversation_factory = lambda tenant_id: None  # noqa: E731 — unconfigured: /chat 503

    from api.prod_deps import build_signup_deps

    deps = ApiDeps(
        verifier=_verifier(),
        greenlight=greenlight,
        saved_views=saved_views,
        conversation_factory=conversation_factory,
        autonomy_config=AutonomyConfig(),
        executor=executor,
        # mounts /signup, /verify-*, /checkout, /webhooks/stripe; provisioning persists the
        # tenant's Managed Agents ids into tenant_workspaces when the DB is configured.
        signup=build_signup_deps(workspace_store=workspace_store),
    )
    return create_app(deps)


app = build_app()
