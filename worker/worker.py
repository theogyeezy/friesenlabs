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

    async with AsyncAnthropic(auth_token=env_key) as client:
        await EnvironmentWorker(
            client,
            environment_id=env_id,
            environment_key=env_key,
            workdir="/workspace",
            tools=lambda env: TOOLS,
        ).run()
