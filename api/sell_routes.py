"""Sell (gamification) routes — GET /sell/me · /sell/leaderboard · /sell/quests · POST /sell/nudge.

The authed surface over the Sell data foundation (the roster `members` + the append-only points
ledger `points`, api/gamify_stores.py) and the display rules (shared/gamify_rules.py).

THE TRUST RULE: every route is claims-bound — the tenant AND the acting user (claims.sub) come from
the verified Cognito JWT only, never a header or request body. No route accepts a tenant_id field.

INERT BY DEFAULT: the reads answer an honest 503 when the points store isn't wired (no crm_app DSN on
this task) — never a fabricated level/board. DRAFT-ONLY: /sell/nudge never sends; it routes the
outbound message through the EXISTING Greenlight gate as a pending draft proposal (deps.greenlight),
exactly like Sidecar — a human must approve, and even then send_email is draft-only.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from api.auth import TenantClaims
from shared.gamify_rules import DEAL_CLOSED_WON, level_progress, points_for, streak_from_days

log = logging.getLogger("api.sell_routes")

_NO_POINTS_DETAIL = (
    "sell points store not configured — no crm_app DSN on this task "
    "(DB_*/UPLIFT_DB_URL unset); gamification is unavailable"
)
# How far back /sell/me looks for streak + today-progress (a streak longer than this reads as capped —
# honest and bounded, never an unbounded ledger scan).
_ME_WINDOW_DAYS = 60
# The single close-based quest's rolling window + milestone.
_QUEST_WINDOW_DAYS = 30
_QUEST_TARGET = 5


class NudgeBody(BaseModel):
    """A nudge to a teammate. `user_id` is the RECIPIENT (a payload field, never an identity claim);
    the tenant + the sender are taken from the verified JWT. NO tenant_id field (THE TRUST RULE)."""
    user_id: str
    message: str
    subject: str = "A nudge from your team"


def _require_points(deps: Any) -> Any:
    points = getattr(deps, "points", None)
    if points is None:
        raise HTTPException(status_code=503, detail=_NO_POINTS_DETAIL)
    return points


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today_iso() -> str:
    return _now().date().isoformat()


def _window_start(days: int) -> str:
    return (_now() - timedelta(days=days)).isoformat()


def _day_of(occurred_at: Any) -> str | None:
    """The YYYY-MM-DD date of an event's occurred_at (an ISO timestamp/date string), or None."""
    if not occurred_at:
        return None
    return str(occurred_at)[:10]


def mount_sell(app: FastAPI, deps: Any, current_tenant: Callable) -> None:
    """Mount the /sell routes, authed via `current_tenant`. `deps` is the ApiDeps bag (duck-typed to
    avoid an api.app import cycle): reads ride deps.points/deps.members; the nudge rides the SAME
    deps.greenlight queue as every other gated action, so a nudge is one draft among the rest."""

    @app.get("/sell/me")
    @app.get("/api/sell/me")
    def sell_me(claims: TenantClaims = Depends(current_tenant)):
        points = _require_points(deps)
        tenant, user = claims.tenant_id, claims.sub  # the VERIFIED claim only

        lifetime = points.user_summary(tenant, user)        # {user_id, points, events}
        xp = int(lifetime.get("points") or 0)
        progress = level_progress(xp)

        recent = points.user_events(tenant, user, since=_window_start(_ME_WINDOW_DAYS))
        today = _today_iso()
        today_points = sum(int(e.get("points") or 0)
                           for e in recent if _day_of(e.get("occurred_at")) == today)
        today_events = sum(1 for e in recent if _day_of(e.get("occurred_at")) == today)
        active_days = {d for e in recent if (d := _day_of(e.get("occurred_at")))}
        streak = streak_from_days(active_days, today)

        return {
            "user_id": user,
            "level": progress["level"],
            "xp": xp,
            "events": int(lifetime.get("events") or 0),
            "streak": streak,
            "today": {"points": today_points, "events": today_events},
            "progress": progress,
        }

    @app.get("/sell/leaderboard")
    @app.get("/api/sell/leaderboard")
    def sell_leaderboard(claims: TenantClaims = Depends(current_tenant)):
        points = _require_points(deps)
        rows = points.leaderboard_rows(claims.tenant_id)  # tenant-scoped (RLS); rows carry tenant_id
        # Defense in depth (the /views pattern): never return a row whose tenant_id isn't the verified
        # request tenant — RLS already scopes the read; this makes a silent leak fail loud, then the
        # internal tenant_id is stripped from the wire (the Sidecar _checked pattern).
        out = []
        for r in rows:
            if str(r.get("tenant_id")) != str(claims.tenant_id):
                raise HTTPException(status_code=500, detail="tenant isolation violation")
            out.append({k: v for k, v in r.items() if k != "tenant_id"})
        return {"leaderboard": out}

    @app.get("/sell/quests")
    @app.get("/api/sell/quests")
    def sell_quests(claims: TenantClaims = Depends(current_tenant)):
        points = _require_points(deps)
        # v1: ONE honest close-based quest derived straight from the ledger — count the rep's real
        # closed_won credits in the rolling window; nothing invented, nothing the data can't back.
        recent = points.user_events(claims.tenant_id, claims.sub,
                                    since=_window_start(_QUEST_WINDOW_DAYS))
        closes = sum(1 for e in recent if e.get("event_type") == DEAL_CLOSED_WON)
        quest = {
            "id": "close-deals",
            "title": f"Close {_QUEST_TARGET} deals",
            "description": f"Win {_QUEST_TARGET} deals in {_QUEST_WINDOW_DAYS} days. "
                           "Each close credits points toward your level and streak.",
            "event_type": DEAL_CLOSED_WON,
            "window_days": _QUEST_WINDOW_DAYS,
            "target": _QUEST_TARGET,
            "current": closes,
            "progress": min(closes, _QUEST_TARGET),
            "complete": closes >= _QUEST_TARGET,
            "reward_points": points_for(DEAL_CLOSED_WON),
        }
        return {"quests": [quest]}

    @app.post("/sell/nudge")
    @app.post("/api/sell/nudge")
    def sell_nudge(body: NudgeBody, claims: TenantClaims = Depends(current_tenant)):
        # The nudge is NEVER sent here. It is proposed as a draft into the EXISTING Greenlight queue
        # (send_email is ALWAYS_ASK + draft-only) — a human must approve, and the applier only ever
        # drafts. tenant + agent come from the verified JWT (THE TRUST RULE), never the body.
        greenlight = getattr(deps, "greenlight", None)
        if greenlight is None:  # pragma: no cover — greenlight is always wired by create_app
            raise HTTPException(status_code=503, detail="approvals queue not configured")
        approval = greenlight.propose(
            tenant_id=claims.tenant_id,
            action="send_email",
            agent=claims.sub,
            reasoning=f"Sell nudge to {body.user_id}: {body.message}",
            value_at_stake=None,
            payload={"to": body.user_id, "subject": body.subject, "body": body.message},
        )
        return {
            "status": "queued",
            "approval_id": str(approval.get("id")) if approval else None,
            "channel": "email",
            "draft_only": True,
        }


__all__ = ["NudgeBody", "mount_sell"]
