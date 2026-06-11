"""The action gate (Build Guide Phase 5, Step 28).

Route every action through the same pipeline so control is uniform and auditable:

    propose -> validate (kill-switch + compliance) -> autonomy check -> [Greenlight if needed]
            -> execute -> trace

Read-only actions skip Greenlight but are still validated and traced. Exactly one trace is written per
run. Side-effecting actions NEVER execute unless the gate decides AUTO (within autonomy + compliance)
or a human approves via Greenlight — the executor is injected and is not called on block/deny.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from . import autonomy, compliance, traces
from .autonomy import AutonomyConfig
from .greenlight import Greenlight
from .killswitch import KillSwitch
from .traces import InMemoryTraceStore, TraceStore
from .types import Action, Decision, GateResult


@dataclass
class GateContext:
    tenant_id: str
    autonomy_config: AutonomyConfig
    executor: Callable[[Action], object]          # performs the side effect (or read) — injected
    greenlight: Greenlight = field(default_factory=Greenlight)
    killswitch: KillSwitch = field(default_factory=KillSwitch)
    trace_store: TraceStore = field(default_factory=InMemoryTraceStore)
    compliance_critic: Callable | None = None


class ActionGate:
    def run(self, action: Action, ctx: GateContext) -> GateResult:
        # 1. Kill switch — before anything executes.
        if ctx.killswitch.is_paused(ctx.tenant_id):
            tid = self._trace(ctx, action, "blocked", reasoning="kill switch engaged")
            return GateResult("blocked", Decision.BLOCK, "kill switch engaged", trace_id=tid)

        # 2. Compliance — a hard fail never reaches Greenlight.
        c = compliance.validate(action, critic=ctx.compliance_critic)
        if not c.ok:
            tid = self._trace(ctx, action, "blocked", reasoning=c.reason)
            return GateResult("blocked", Decision.BLOCK, c.reason, trace_id=tid)

        # 3. Autonomy check.
        level = autonomy.resolve(ctx.autonomy_config, action.agent, ctx.tenant_id)
        decision = autonomy.decide(level, action, ctx.autonomy_config)

        # 4. Greenlight if needed.
        if decision is Decision.APPROVE:
            rec = ctx.greenlight.propose(
                tenant_id=ctx.tenant_id,
                action=action.name,
                agent=action.agent,
                reasoning=action.reasoning,
                value_at_stake=action.value_at_stake,
                payload=action.payload,
            )
            tid = self._trace(ctx, action, "pending_approval", reasoning=action.reasoning)
            return GateResult("pending_approval", Decision.APPROVE, f"level={level.value}",
                              approval=rec, trace_id=tid)

        # 5. Execute (AUTO only) + 6. trace.
        result = ctx.executor(action)
        tid = self._trace(ctx, action, "executed", outputs=result, reasoning=action.reasoning)
        return GateResult("ok", Decision.AUTO, f"level={level.value}", result=result, trace_id=tid)

    @staticmethod
    def _trace(ctx: GateContext, action: Action, kind: str, *, outputs=None,
               reasoning: str = "") -> int | str:
        return traces.append_trace(
            ctx.trace_store,
            tenant_id=ctx.tenant_id,
            agent=action.agent,
            tool=action.name,
            kind=kind,
            inputs=action.payload,
            outputs=outputs,
            reasoning=reasoning,
        )
