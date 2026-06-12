"""Tool base + the permission policy that makes 'trust the feature' real (Build Guide Phase 4/5).

A read-only tool returns results (`AUTO`). A side-effecting tool (send_email, update_deal,
issue_quote) carries `ALWAYS_ASK`: when invoked it MUST NOT perform the side effect — it returns a
Greenlight *proposal* for a human to approve (Part VII). This module enforces that at the base class
so a tool author cannot accidentally execute a gated side effect.

Every tool sets `app.current_tenant` from the session metadata before any DB/Cube call, so Postgres
RLS applies during tool execution too.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class Policy(str, Enum):
    AUTO = "auto"             # read-only — execute and return results
    ALWAYS_ASK = "always_ask" # side-effecting — route through Greenlight, never auto-execute


class DBSession(Protocol):
    def set_tenant(self, tenant_id: str) -> None: ...


class Greenlight(Protocol):
    def propose(self, *, tenant_id: str, action: str, agent: str | None,
                reasoning: str, value_at_stake: float | None, payload: dict) -> dict: ...


@dataclass
class ToolContext:
    """Per-call context. Carries the tenant + injected clients; sets RLS tenant before DB/Cube use."""
    tenant_id: str
    agent: str | None = None
    db: Any = None          # something with set_tenant(...) + query methods
    cube: Any = None        # governed-metrics client
    rag: Any = None         # vector-search client
    cortex: Any = None      # model-prediction client
    hubspot: Any = None     # live HubSpot client (HubSpotFullClient, token already set per-tenant)
    greenlight: Greenlight | None = None
    extra: dict = field(default_factory=dict)

    def bind_tenant(self) -> None:
        """SET app.current_tenant before any tenant-scoped access (RLS during tool exec)."""
        if self.db is not None and hasattr(self.db, "set_tenant"):
            self.db.set_tenant(self.tenant_id)
        if self.cube is not None and hasattr(self.cube, "set_tenant"):
            self.cube.set_tenant(self.tenant_id)


class Tool(abc.ABC):
    name: str
    description: str
    input_schema: dict
    policy: Policy = Policy.AUTO
    # Comms channel for the compliance validator (None = not a comms send). Server-side truth: the
    # action gate derives side_effecting/channel from the tool class, NEVER from the request body.
    channel: str | None = None

    @property
    def is_side_effecting(self) -> bool:
        return self.policy is Policy.ALWAYS_ASK

    def to_spec(self) -> dict:
        """The MA custom-tool definition shape."""
        return {
            "type": "custom",
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    @abc.abstractmethod
    def _execute(self, ctx: ToolContext, **kwargs) -> dict:
        """Read-only work for AUTO tools. For ALWAYS_ASK tools, this builds the proposal payload
        only (it must NOT perform the side effect)."""

    def invoke(self, ctx: ToolContext, **kwargs) -> dict:
        """Single entry point. Binds the tenant, then either executes (AUTO) or proposes (ALWAYS_ASK).

        The base class guarantees an ALWAYS_ASK tool's side effect can never auto-run: it routes the
        proposal to Greenlight and returns 'pending_approval' without calling any sender/mutator.
        """
        ctx.bind_tenant()
        if self.policy is Policy.AUTO:
            return {"status": "ok", "result": self._execute(ctx, **kwargs)}

        # ALWAYS_ASK: build the proposal (no side effect) and hand it to Greenlight.
        proposal = self._execute(ctx, **kwargs)
        if ctx.greenlight is None:
            return {"status": "pending_approval", "proposal": proposal, "greenlight": "unconfigured"}
        record = ctx.greenlight.propose(
            tenant_id=ctx.tenant_id,
            action=self.name,
            agent=ctx.agent,
            reasoning=proposal.get("reasoning", ""),
            value_at_stake=proposal.get("value_at_stake"),
            payload=proposal,
        )
        return {"status": "pending_approval", "proposal": proposal, "approval": record}


class InMemoryGreenlight:
    """A minimal Greenlight stub (the real queue is Phase 5). Records proposals; approves nothing."""

    def __init__(self):
        self.queue: list[dict] = []

    def propose(self, **kw) -> dict:
        rec = {"id": len(self.queue) + 1, "status": "pending", **kw}
        self.queue.append(rec)
        return rec
