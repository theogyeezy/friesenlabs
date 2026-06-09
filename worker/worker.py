"""Self-hosted tool-execution worker (Build Guide Phase 4, Step 27).

Polls the Managed Agents environment queue, claims work, and executes the custom tools IN YOUR VPC.
Authenticated by the *environment key* (never the org API key — that key must not exist on this host).
Inside each tool, `app.current_tenant` is set from the session metadata before any DB/Cube call so
Postgres RLS applies during tool execution too.

AUTHORED ONLY — `run()` is not executed against real Anthropic in this build (beta; needs Nick).
Construction never touches the network (anthropic is imported lazily).
"""
from __future__ import annotations

import os

from agents.tools.base import ToolContext
from agents.tools.readonly import QueryCube, ReadCrm, SearchRag
from agents.tools.sideeffecting import DraftEmail, IssueQuote, SendEmail, UpdateDeal

# The tool list the worker registers. Read-only run; side-effecting route to Greenlight.
TOOLS = [SearchRag(), QueryCube(), ReadCrm(), DraftEmail(), SendEmail(), UpdateDeal(), IssueQuote()]


def emit_polling_metric() -> None:
    """Emit the `workers_polling=1` CloudWatch metric the worker-absent alarm watches.

    Called once per poll loop so `Uplift/Agents:workers_polling` stays >= 1 while a worker is
    live; the observability module alarms (treat_missing_data=breaching) when it drops to zero.
    Env-gated (CLOUDWATCH_METRICS=1) and boto3 is imported lazily INSIDE the function so importing
    this module stays AWS-free. Never called from tests; only wired into the live run() loop.
    """
    if os.environ.get("CLOUDWATCH_METRICS") != "1":
        return
    import boto3  # noqa: PLC0415 — lazy so module import needs no AWS/boto3

    cloudwatch = boto3.client("cloudwatch", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    cloudwatch.put_metric_data(
        Namespace="Uplift/Agents",
        MetricData=[{"MetricName": "workers_polling", "Value": 1, "Unit": "Count"}],
    )


def build_context(session_metadata: dict, clients: dict) -> ToolContext:
    """Build a per-call ToolContext from session metadata, binding the tenant for RLS."""
    return ToolContext(
        tenant_id=session_metadata["tenant_id"],
        agent=session_metadata.get("agent"),
        db=clients.get("db"),
        cube=clients.get("cube"),
        rag=clients.get("rag"),
        cortex=clients.get("cortex"),
        greenlight=clients.get("greenlight"),
    )


async def run() -> None:  # pragma: no cover — live Anthropic, BLOCKED: needs Nick
    """Connect to the environment queue and serve tools. VERIFY against the live SDK before use."""
    env_id = os.environ["UPLIFT_ENV_ID"]
    env_key = os.environ["UPLIFT_ENV_KEY"]  # environment key ONLY; never the org API key
    # Lazy imports so this module imports with no anthropic/network dependency.
    from anthropic import AsyncAnthropic  # noqa: PLC0415
    from anthropic.lib.environments import EnvironmentWorker  # noqa: PLC0415

    def _tools_for_poll(env):  # pragma: no cover — invoked by the live SDK each poll
        # The SDK requests the tool list each poll iteration; piggyback the heartbeat metric here
        # so `workers_polling` stays >= 1 while this worker is serving (drives the worker-absent alarm).
        emit_polling_metric()
        return TOOLS

    emit_polling_metric()  # emit once up front so the alarm clears as soon as we start serving
    async with AsyncAnthropic(auth_token=env_key) as client:
        await EnvironmentWorker(
            client,
            environment_id=env_id,
            environment_key=env_key,
            workdir="/workspace",
            tools=_tools_for_poll,
        ).run()
