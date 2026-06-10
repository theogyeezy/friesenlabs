"""Self-hosted tool-execution worker (Build Guide Phase 4, Step 27).

Polls the Managed Agents environment queue, claims work, and executes the custom tools IN YOUR VPC.
Authenticated by the *environment key* (never the org API key — that key must not exist on this host).
Inside each tool, `app.current_tenant` is set from the session metadata before any DB/Cube call so
Postgres RLS applies during tool execution too.

Client wiring: `run()` builds the injectable tool clients (PgCrmClient / PgRagClient / Greenlight)
from env (shared/config.py names — see infra/REQUESTS.md REQ-001 for the task-def wiring) and passes
them into `build_context()` per tool call. Import-safe: NOTHING is constructed at import — psycopg2 /
anthropic / boto3 all load lazily inside functions, and an unconfigured env yields None clients so
tools degrade cleanly (read tools error per-call, side-effecting tools still surface proposals).

AUTHORED ONLY — `run()` is not executed against real Anthropic in this build (beta; needs Nick).
"""
from __future__ import annotations

import os

from agents.tools.base import ToolContext
from agents.tools.readonly import QueryCube, ReadCrm, SearchRag
from agents.tools.sideeffecting import DraftEmail, IssueQuote, SendEmail, UpdateDeal
from shared.config import (
    ENV_CLOUDWATCH_METRICS,
    ENV_CUBE_ENDPOINT,
    ENV_UPLIFT_ENV_ID,
    ENV_UPLIFT_ENV_KEY,
    dsn_from_env,
)

# The tool list the worker registers. Read-only run; side-effecting route to Greenlight.
TOOLS = [SearchRag(), QueryCube(), ReadCrm(), DraftEmail(), SendEmail(), UpdateDeal(), IssueQuote()]


def emit_polling_metric() -> None:
    """Emit the `workers_polling=1` CloudWatch metric the worker-absent alarm watches.

    Called once per poll loop so `Uplift/Agents:workers_polling` stays >= 1 while a worker is
    live; the observability module alarms (treat_missing_data=breaching) when it drops to zero.
    Env-gated (CLOUDWATCH_METRICS=1) and boto3 is imported lazily INSIDE the function so importing
    this module stays AWS-free. Never called from tests; only wired into the live run() loop.
    """
    if os.environ.get(ENV_CLOUDWATCH_METRICS) != "1":
        return
    import boto3  # noqa: PLC0415 — lazy so module import needs no AWS/boto3

    cloudwatch = boto3.client("cloudwatch", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    cloudwatch.put_metric_data(
        Namespace="Uplift/Agents",
        MetricData=[{"MetricName": "workers_polling", "Value": 1, "Unit": "Count"}],
    )


def build_clients_from_env() -> dict:
    """Construct the injectable tool clients from env (shared/config.py names). Called from run()
    ONLY — never at import. Unconfigured pieces stay None so `build_context` degrades cleanly.

    - DB (`UPLIFT_DB_URL` or DB_USER/DB_PASS/DB_HOST/...): PgCrmClient (ToolContext.db),
      PgRagClient (ToolContext.rag), and a PgApprovalStore-backed Greenlight — all on the FIXED RLS
      pattern (pooled per-op conn + SET LOCAL app.current_tenant in a transaction).
    - CUBE_ENDPOINT: recorded for the (not-yet-built) Cube client; None until that client exists.
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
    # Cube client not built yet; keep the endpoint visible for the future client + REQ-001 wiring.
    if os.environ.get(ENV_CUBE_ENDPOINT):
        clients["cube"] = None  # TODO(cube): governed-metrics client over CUBE_ENDPOINT
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
    return ToolContext(
        tenant_id=session_metadata["tenant_id"],
        agent=session_metadata.get("agent"),
        db=db,
        cube=clients.get("cube"),
        rag=clients.get("rag"),
        cortex=clients.get("cortex"),
        greenlight=clients.get("greenlight"),
    )


async def run() -> None:  # pragma: no cover — live Anthropic, BLOCKED: needs Nick
    """Connect to the environment queue and serve tools. VERIFY against the live SDK before use."""
    env_id = os.environ[ENV_UPLIFT_ENV_ID]
    env_key = os.environ[ENV_UPLIFT_ENV_KEY]  # environment key ONLY; never the org API key
    # Build the injectable tool clients once per process (each call still runs its own
    # tenant-scoped SET LOCAL transaction; build_context derives a fresh db binding per call).
    clients = build_clients_from_env()
    # Lazy imports so this module imports with no anthropic/network dependency.
    from anthropic import AsyncAnthropic  # noqa: PLC0415
    from anthropic.lib.environments import EnvironmentWorker  # noqa: PLC0415

    def _tools_for_poll(env):  # pragma: no cover — invoked by the live SDK each poll
        # The SDK requests the tool list each poll iteration; piggyback the heartbeat metric here
        # so `workers_polling` stays >= 1 while this worker is serving (drives the worker-absent alarm).
        emit_polling_metric()
        return TOOLS

    def _context_for(session_metadata: dict) -> ToolContext:  # pragma: no cover — live SDK only
        # Per tool call: tenant from the session metadata (stamped upstream from the verified JWT
        # claim) + the env-built clients -> RLS-scoped ToolContext.
        return build_context(session_metadata, clients)

    emit_polling_metric()  # emit once up front so the alarm clears as soon as we start serving
    async with AsyncAnthropic(auth_token=env_key) as client:
        await EnvironmentWorker(
            client,
            environment_id=env_id,
            environment_key=env_key,
            workdir="/workspace",
            tools=_tools_for_poll,
            # VERIFY: the EnvironmentWorker hook for per-invocation context — the intended flow is
            # context_factory(invocation.session_metadata) -> Tool.invoke(ctx, **input). Confirm
            # the kwarg name/shape against the live SDK before first run.
            context_factory=_context_for,
        ).run()
