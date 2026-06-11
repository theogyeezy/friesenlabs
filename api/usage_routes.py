"""GET /usage — the tenant's current monthly usage, plan cap, and Anthropic cost attribution.

Authed (the verified-claim dependency, exactly like every other tenant route — tenant NEVER from
the body). Read-only: it reports the running monthly usage counter (messages + agent_actions), the
plan's quota cap (None = unlimited) with a derived over_quota flag, and the per-tenant token-cost
summary for the period. The path is EXEMPT from the quota meter (reading your usage never burns
quota) and is intentionally cheap.

Inert-default contract like the other optional route groups: with `usage_store=None` and
`cost_recorder=None` the endpoint still answers a stable shape (zeroed counters / null cost) so the
SPA renders rather than 503s; the real Pg stores are wired by api/asgi.py under the crm_app DSN.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import Depends, FastAPI

from api.auth import TenantClaims
from api.limits import PlanResolver
from shared.config import monthly_quota, normalize_plan

log = logging.getLogger("api.usage_routes")


@dataclass
class UsageDeps:
    # api.usage.UsageStore (PgUsageStore in prod, InMemoryUsageStore in tests). None -> zeroed.
    usage_store: Any | None = None
    # api.usage.CostRecorder (PgCostRecorder in prod). None -> null cost summary.
    cost_recorder: Any | None = None
    # tenant_id -> plan label (the SAME resolver the limiter uses, so the cap reported here matches
    # the cap enforced). Default resolves every tenant to None -> the generous tier.
    plan_resolver: PlanResolver = field(default_factory=PlanResolver)


def _empty_usage(tenant_id: str) -> dict:
    from api.usage import QUOTA_METRICS, current_period  # noqa: PLC0415
    return {"period": current_period(), "by_metric": {m: 0 for m in QUOTA_METRICS}, "total": 0}


def _empty_cost(tenant_id: str) -> dict:
    from api.usage import current_period  # noqa: PLC0415
    return {"period": current_period(), "events": 0, "in_tok": 0, "out_tok": 0, "est_cost": 0.0}


def mount_usage(app: FastAPI, deps: UsageDeps, current_tenant: Callable) -> None:
    @app.get("/usage")
    @app.get("/api/usage")
    def get_usage(claims: TenantClaims = Depends(current_tenant)):
        tenant_id = claims.tenant_id  # the VERIFIED claim only
        plan = normalize_plan(deps.plan_resolver.plan(tenant_id))
        cap = monthly_quota(plan)

        if deps.usage_store is not None:
            try:
                usage = deps.usage_store.current(tenant_id)
            except Exception:  # noqa: BLE001 — usage read must not 500 the endpoint
                log.warning("usage read failed for tenant=%s", tenant_id,
                            extra={"event": "usage_read_failed"})
                usage = _empty_usage(tenant_id)
        else:
            usage = _empty_usage(tenant_id)

        if deps.cost_recorder is not None:
            try:
                cost = deps.cost_recorder.summary(tenant_id)
            except Exception:  # noqa: BLE001 — cost is measurement; never 500
                log.warning("cost summary failed for tenant=%s", tenant_id,
                            extra={"event": "cost_summary_failed"})
                cost = _empty_cost(tenant_id)
        else:
            cost = _empty_cost(tenant_id)

        over_quota = cap is not None and usage["total"] > cap
        return {
            "plan": plan,
            "usage": usage,
            "quota": cap,                 # None = unlimited
            "over_quota": over_quota,
            "cost": cost,
        }
