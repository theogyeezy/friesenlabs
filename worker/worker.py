"""Self-hosted tool-execution worker (Build Guide Phase 4, Step 27).

Polls the Managed Agents environment queue, claims work, and executes the custom tools IN YOUR VPC.
Authenticated by the *environment key* (never the org API key — that key must not exist on this host).
Inside each tool, `app.current_tenant` is set from the session metadata before any DB/Cube call so
Postgres RLS applies during tool execution too.

Client wiring: `run()` builds the injectable tool clients (PgCrmClient / PgRagClient / Greenlight)
from env (shared/config.py names — see infra/REQUESTS.md REQ-001 for the task-def wiring) and binds
them per claimed session via `session_tools_factory()`. Import-safe: NOTHING is constructed at
import — psycopg2 / anthropic / boto3 all load lazily inside functions, and an unconfigured env
yields None clients so tools degrade cleanly (read tools error per-call, side-effecting tools still
surface proposals).

Liveness (docs/decisions/workers-polling-heartbeat-assumption.md, RATIFIED 2026-06-10 #123):
the `Uplift/Agents:workers_polling` metric the `worker_absent` alarm watches is emitted by an
EXPLICIT heartbeat task (`heartbeat_loop`, every 30s) running as a sibling of the SDK poll loop —
NEVER piggybacked on the SDK tools callable, which is invoked once per CLAIMED SESSION, not per
poll, and would go silent on an idle queue (permanent false-positive alarms). Structured
concurrency in `run()` guarantees the heartbeat dies when the poll loop dies, so
"metric present ⇔ worker process up and its poll loop not crashed" holds.

AUTHORED ONLY — `run()` is not executed against real Anthropic in this build (beta; needs Nick).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from typing import Any, Callable

from agents.coordinator import COORDINATOR
from agents.roster import ROSTER
from agents.tools import registry as tool_registry
from agents.tools.base import Tool, ToolContext
from shared.config import (
    ENV_ANTHROPIC_API_KEY,
    ENV_CLOUDWATCH_METRICS,
    ENV_UPLIFT_ENV_ID,
    ENV_UPLIFT_ENV_KEY,
    ENV_WORKER_HEARTBEAT_SECONDS,
    dsn_from_env,
)

log = logging.getLogger(__name__)

# The tool list the worker registers. Read-only run; side-effecting route to Greenlight.
# CONTRACT (tested: tests/unit/test_worker_roster_parity.py): served == the EXACT union of the
# tools granted across the agent roster + coordinator (agents/roster, agents/coordinator). A
# granted-but-unserved tool wedges sessions at requires_action forever; a served-but-ungranted
# tool is dead weight nothing can call (send_email was exactly that — no agent grants it; the
# real send still only ever runs through the post-approval Greenlight gate in api/control).
#
# REGISTRY RECONCILIATION (the worker-vs-registry drift fix): the trusted registry
# (agents/tools/registry.py) defines the FULL CRM-write suite — update_deal, update_contact,
# create_activity, create_deal — as real, ALWAYS_ASK, Greenlight-gated proposal tools (the same
# actions the Greenlight appliers execute post-approval). Ledger, the CRM-mutations specialist,
# now owns that whole suite (agents/roster), so all four are SERVED here — no longer
# registered-but-unserved. send_email stays the deliberate exception: it's registered ONLY so the
# action gate can classify it side-effecting; no agent grants it and the real send is the
# post-approval api/control path, so the worker must NOT serve it.
#
# The list is BUILT from the grants through that trusted registry — parity by construction, never
# by hand-curation discipline (a new grant is served automatically; a grant naming no registry
# tool fails at import, not live).
def build_tools(specs) -> list[Tool]:
    """The served tool instances for `specs` — one per granted name, registry-resolved."""
    names = sorted({name for spec in specs for name in spec.tools})
    return [tool_registry.resolve(name) for name in names]


TOOLS = build_tools([*ROSTER, COORDINATOR])

# Brief Option A: a fixed 30s emit interval — two emits per alarm period (60s), so a single
# dropped PutMetricData can never trip the worker_absent alarm on its own.
DEFAULT_HEARTBEAT_INTERVAL_S = 30.0


def _heartbeat_interval_s() -> float:
    """The heartbeat interval: `WORKER_HEARTBEAT_SECONDS` (shared/config.py, a NEW deliberate
    name — deploy invariance) when set to a positive number; the 30s default on unset/junk.
    Never raises — a bad env value must not keep the worker from starting."""
    raw = os.environ.get(ENV_WORKER_HEARTBEAT_SECONDS, "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_HEARTBEAT_INTERVAL_S
    return value if value > 0 else DEFAULT_HEARTBEAT_INTERVAL_S


def emit_polling_metric(cloudwatch: Any = None) -> None:
    """One `workers_polling=1` PutMetricData — the datapoint the worker-absent alarm watches
    (`treat_missing_data=breaching`: the metric going missing IS the alarm signal, by design).

    Env-gated (CLOUDWATCH_METRICS=1 — set on the worker task def only, REQ-001) and boto3 is
    imported lazily INSIDE the function so importing this module stays AWS-free. `heartbeat_loop`
    passes a prebuilt client so the per-emit path never reconstructs one (brief Option A); the
    None fallback keeps the function usable standalone (dev/one-off).
    """
    if os.environ.get(ENV_CLOUDWATCH_METRICS) != "1":
        return
    if cloudwatch is None:
        import boto3  # noqa: PLC0415 — lazy so module import needs no AWS/boto3

        cloudwatch = boto3.client("cloudwatch", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    cloudwatch.put_metric_data(
        Namespace="Uplift/Agents",
        MetricData=[{"MetricName": "workers_polling", "Value": 1, "Unit": "Count"}],
    )


async def heartbeat_loop(
    *,
    interval_s: float | None = None,
    cloudwatch: Any = None,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> None:
    """Explicit `workers_polling` heartbeat (brief Option A — the RATIFIED recommendation).

    Emits the metric immediately, then every `interval_s` (default 30s), for as long as this task
    is alive. `run()` starts it as a SIBLING of the SDK poll loop and cancels it when the loop
    exits, so the metric's presence tracks the poll loop's liveness — independent of SDK callable
    behavior (the tools callable fires once per claimed session, NOT per poll; an idle queue would
    starve any piggybacked emit).

    Failure posture:
    - CLOUDWATCH_METRICS != "1" → no-op (returns immediately; dev/tests stay AWS-free).
    - boto3/client construction fails → log and return (the worker keeps serving, unmetered).
    - A PutMetricData failure → log and CONTINUE (a CloudWatch blip must never kill the worker,
      and one missed 30s emit still leaves a datapoint inside the alarm's 60s period).
    - Cancellation → clean shutdown; the metric then goes missing and the alarm fires, by design.

    boto3 is blocking, so each emit runs via `asyncio.to_thread` to keep the event loop (and the
    SDK's own lease heartbeats — unrelated to this metric) responsive.
    """
    if os.environ.get(ENV_CLOUDWATCH_METRICS) != "1":
        return
    interval = interval_s if interval_s is not None else _heartbeat_interval_s()
    if cloudwatch is None:
        try:
            import boto3  # noqa: PLC0415 — lazy so module import needs no AWS/boto3

            cloudwatch = boto3.client(
                "cloudwatch", region_name=os.environ.get("AWS_REGION", "us-east-1")
            )
        except Exception:
            log.exception("workers_polling heartbeat disabled: CloudWatch client build failed")
            return
    while True:
        try:
            await asyncio.to_thread(emit_polling_metric, cloudwatch)
        except asyncio.CancelledError:
            raise  # clean shutdown — never swallow cancellation
        except Exception:
            log.warning("workers_polling heartbeat emit failed; continuing", exc_info=True)
        await sleep(interval)


def build_clients_from_env() -> dict:
    """Construct the injectable tool clients from env (shared/config.py names). Called from run()
    ONLY — never at import. Unconfigured pieces stay None so `build_context` degrades cleanly.

    - DB (`UPLIFT_DB_URL` or DB_USER/DB_PASS/DB_HOST/...): PgCrmClient (ToolContext.db),
      PgRagClient (ToolContext.rag), and a PgApprovalStore-backed Greenlight — all on the FIXED RLS
      pattern (pooled per-op conn + SET LOCAL app.current_tenant in a transaction).
    - CUBE_ENDPOINT + CUBEJS_API_SECRET_VALUE: the REAL governed-metrics client
      (`agents.tools.cube_client.CubeClient` — the same per-request tenant-JWT minting client the
      API uses), so worker-side `query_cube` returns real rows. Degradations are VISIBLE, never a
      silent []: with only one of the two env pieces set, every call returns the 'unconfigured'
      result; an unreachable Cube returns an 'error' status with detail (query_cube surfaces both
      as cube_status/detail). Both unset keeps the key None (boots byte-identical).
    - CORTEX_S3_BUCKET / CORTEX_LOCAL_DIR: the persistent Cortex model registry
      (`ml.registry.registry_from_env`) -> ToolContext.cortex, so `run_model` scores with the
      tenant's durable champion. All-unset keeps the key ABSENT (unconfigured boots stay
      byte-identical; run_model degrades to "no model registry configured").
    - ANTHROPIC_API_KEY: default build_view spec generator. In the prod posture this key is
      NEVER on the worker (org key is API-task-only — shared/config.py), so this stays absent
      and build_view keeps its explicit raise; the guard exists for dev parity only.
    THE TRUST RULE: these clients take tenant_id per call from the session metadata the API stamped
    from the verified JWT claim — never from this host's env.
    """
    clients: dict = {"db": None, "rag": None, "cube": None, "greenlight": None}
    dsn = dsn_from_env()
    if dsn:
        from api.control.greenlight import Greenlight, PgApprovalStore  # noqa: PLC0415 — lazy
        from api.pg_clients import PgCrmClient, PgRagClient  # noqa: PLC0415 — lazy (psycopg2)

        clients["db"] = PgCrmClient(dsn)            # build_context derives a fresh per-call binding
        clients["rag"] = PgRagClient(dsn)
        clients["greenlight"] = Greenlight(store=PgApprovalStore(dsn))
        # Cortex flywheel close-loop: run_model logs each score to the RLS-scoped predictions
        # table so live-AUC drift has real evidence. The worker is the LIVE serving path (under
        # Managed Agents), so without this the predictions table stays empty and drift can only
        # ever report "insufficient evidence". PgPredictionLog rides the same per-op SET LOCAL.
        from ml.predictions import PgPredictionLog  # noqa: PLC0415 — lazy (psycopg2)

        clients["prediction_log"] = PgPredictionLog(dsn)
    else:
        # No crm_app DSN on this task -> rag (and db/greenlight/prediction_log) stay None. The
        # worker still BOOTS and serves, but search_rag/read_crm error per-call and side-effecting
        # tools can't persist their RLS-scoped proposals — a silent symptom in prod. Warn LOUDLY at
        # startup so an unconfigured data plane is visible in the logs, not discovered per session.
        log.warning(
            "worker: no crm_app DSN (DB_*/UPLIFT_DB_URL unset) — rag/db/greenlight clients are "
            "None; search_rag and read_crm will error per call until the data plane is configured"
        )
    # Governed-metrics client over CUBE_ENDPOINT (+ CUBEJS_API_SECRET_VALUE) — the SAME
    # tenant-JWT-minting CubeClient the API wires (#175); the worker just reuses the factory.
    # THE TRUST RULE: the client takes tenant_id per call (from the session metadata the API
    # stamped from the verified claim) and mints a short-lived HS256 JWT for Cube's checkAuth.
    from agents.tools.cube_client import cube_client_from_env  # noqa: PLC0415 — lazy, import-safe

    cube = cube_client_from_env()
    if cube is not None:
        clients["cube"] = cube
    # Persistent Cortex registry — the real factory now exists (ml/registry.py). Lazy + offline-
    # safe: S3Registry defers boto3 to first blob access; LocalFs is the dev/tests fallback.
    from ml.registry import registry_from_env  # noqa: PLC0415 — lazy, import-safe either way

    cortex = registry_from_env()
    if cortex is not None:
        clients["cortex"] = cortex
    # Default view-spec generator (env-guarded; see the docstring — absent in the prod posture).
    if os.environ.get(ENV_ANTHROPIC_API_KEY):
        from agents.tools.spec_generator import AnthropicSpecGenerator  # noqa: PLC0415 — lazy

        clients["spec_generator"] = AnthropicSpecGenerator()
    return clients


def build_context(session_metadata: dict, clients: dict) -> ToolContext:
    """Build a per-call ToolContext from session metadata, binding the tenant for RLS.

    When `clients["db"]` is a PgCrmClient (exposes `.binding()`), a FRESH per-call adapter is
    derived so tenant state is never shared across concurrent tool calls — `ToolContext.bind_tenant`
    then sets THIS call's tenant on it (from the session metadata the API stamped from the verified
    claim).
    """
    db = clients.get("db")
    if hasattr(db, "binding"):
        db = db.binding()
    # Fresh extra dict per call — tool invocations must never share mutable context state.
    extra: dict = {}
    if clients.get("spec_generator") is not None:
        extra["generate_spec"] = clients["spec_generator"]
    if clients.get("prediction_log") is not None:
        extra["prediction_log"] = clients["prediction_log"]  # run_model close-loop (Cortex flywheel)
    return ToolContext(
        tenant_id=session_metadata["tenant_id"],
        agent=session_metadata.get("agent"),
        db=db,
        cube=clients.get("cube"),
        rag=clients.get("rag"),
        cortex=clients.get("cortex"),
        greenlight=clients.get("greenlight"),
        extra=extra,
    )


class SessionToolBinding:
    """Per-claimed-session tenant binding over the SDK's REAL context seam.

    The installed SDK's `EnvironmentWorker` has NO `context_factory` kwarg (the brief's verified
    finding) — its supported seam is the `tools=` factory, invoked ONCE PER CLAIMED SESSION with
    that session's `AgentToolContext` (`.client` = an environment-key-scoped sub-client,
    `.session_id`). That factory is synchronous, so the session-metadata fetch happens here,
    lazily, on the FIRST tool call of the session (awaitable context), and is cached for the
    session's remaining calls. Each call still gets a FRESH `ToolContext` via `build_context`
    (fresh db binding — tenant state never shared across concurrent calls).

    THE TRUST RULE: tenant_id comes ONLY from the session metadata the API stamped from the
    verified Cognito JWT claim at session create (agents/runtime.py) — never env/header/payload.
    VERIFY (CLAUDE.md hard constraint #4): `sessions.retrieve` under the ENVIRONMENT key on the
    first live run — the brief flags this exact call as the one to confirm before deploy.
    """

    def __init__(self, env: Any, clients: dict) -> None:
        self._env = env
        self._clients = clients
        self._metadata: dict | None = None
        self._lock = asyncio.Lock()

    async def context(self) -> ToolContext:
        if self._metadata is None:
            async with self._lock:
                if self._metadata is None:  # re-check under the lock (concurrent first calls)
                    session = await self._env.client.beta.sessions.retrieve(self._env.session_id)
                    self._metadata = dict(getattr(session, "metadata", None) or {})
        if "tenant_id" not in self._metadata:
            # Fail LOUDLY: a session without a stamped tenant must never run tenant-scoped tools.
            raise RuntimeError(
                f"session {self._env.session_id} has no tenant_id in its metadata — "
                "refusing to build a ToolContext (THE TRUST RULE)"
            )
        return build_context(self._metadata, self._clients)


class SessionBoundTool:
    """SDK-runnable adapter (`name` + async `call`) binding one registry Tool to one session.

    `call` resolves the session's tenant-bound ToolContext from the shared binding, then runs the
    sync `Tool.invoke` (psycopg2 = blocking) via `asyncio.to_thread` so the worker's event loop —
    including the SDK's work-item lease heartbeats — stays responsive. The base-class guarantee is
    unchanged: ALWAYS_ASK tools only ever produce Greenlight proposals (draft gate)."""

    def __init__(self, tool: Tool, binding: SessionToolBinding) -> None:
        self._tool = tool
        self._binding = binding

    @property
    def name(self) -> str:
        return self._tool.name

    def to_dict(self) -> dict:
        return self._tool.to_spec()

    async def call(self, input: object) -> str:
        ctx = await self._binding.context()
        payload = input if isinstance(input, dict) else {}
        result = await asyncio.to_thread(self._tool.invoke, ctx, **payload)
        return json.dumps(result, default=str)


def session_tools_factory(clients: dict) -> Callable[[Any], list[SessionBoundTool]]:
    """The `tools=` callable for `EnvironmentWorker`: per CLAIMED SESSION (the SDK's documented
    cadence — never per poll), wrap the registry TOOLS around that session's tenant binding."""

    def _tools_for_session(env: Any) -> list[SessionBoundTool]:
        binding = SessionToolBinding(env, clients)
        return [SessionBoundTool(tool, binding) for tool in TOOLS]

    return _tools_for_session


async def run() -> None:
    """Connect to the environment queue and serve tools, with the explicit liveness heartbeat.

    Order matters (the brief's crash-loop finding): the `EnvironmentWorker` is CONSTRUCTED before
    the heartbeat task starts, so a dead-on-arrival worker (bad kwargs, bad env) emits ZERO
    heartbeats — a crash-looping task can never feed the very metric that's supposed to prove it's
    serving. The heartbeat then runs as a sibling task and is cancelled (finally) when the poll
    loop exits for ANY reason, keeping "metric present ⇔ poll loop alive" true.

    VERIFY against the live SDK before first use (CLAUDE.md hard constraint #4) — kwargs below are
    confirmed against the installed anthropic SDK's `EnvironmentWorker.__init__` (client,
    environment_id, environment_key, tools, workdir, ... — NO context_factory).
    """
    env_id = os.environ[ENV_UPLIFT_ENV_ID]
    env_key = os.environ[ENV_UPLIFT_ENV_KEY]  # environment key ONLY; never the org API key
    # Build the injectable tool clients once per process (each call still runs its own
    # tenant-scoped SET LOCAL transaction; build_context derives a fresh db binding per call).
    clients = build_clients_from_env()
    # Lazy imports so this module imports with no anthropic/network dependency.
    from anthropic import AsyncAnthropic  # noqa: PLC0415
    from anthropic.lib.environments import EnvironmentWorker  # noqa: PLC0415

    async with AsyncAnthropic(auth_token=env_key) as client:
        worker = EnvironmentWorker(
            client,
            environment_id=env_id,
            environment_key=env_key,
            workdir="/workspace",
            tools=session_tools_factory(clients),
        )
        heartbeat = asyncio.create_task(heartbeat_loop(), name="workers-polling-heartbeat")
        try:
            await worker.run()
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
