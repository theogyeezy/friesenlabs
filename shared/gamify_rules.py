"""Sell (gamification) scoring rules — the single source of truth for how a domain event becomes
points. The store (api/gamify_stores.py) persists the append-only ledger; this module only answers
"how many points is this event worth?".

EXTENSIBLE BY DESIGN: scoring a NEW event type is a one-line addition to `POINTS` — no caller
change, no branching. `points_for` returns 0 for anything not (yet) listed, so an unscored event is
inert, never an error.

Import-safe + pure: no I/O, no AWS, no DB — just a dict and a lookup.
"""
from __future__ import annotations

# The canonical event-type names callers should reference (avoids string drift at the call sites).
DEAL_CLOSED_WON = "deal.closed_won"

# event_type -> points. Add a new scored event as a ONE-LINE entry here.
POINTS: dict[str, int] = {
    DEAL_CLOSED_WON: 10,
}


def points_for(event_type: str) -> int:
    """Points awarded for `event_type`; 0 for an unknown / not-yet-scored event.

    Never raises — an event the config doesn't list is worth 0, so adding scoring later is a pure
    config change and a caller can always ask without guarding."""
    return POINTS.get(event_type, 0)


# --------------------------------------------------------------------------- #
# Display rules — how lifetime XP becomes a level, and an activity run a streak.
# These are the single source of truth the /sell/me surface derives from; pure + import-safe.
# --------------------------------------------------------------------------- #
# A flat band: every level is the same XP wide (a closed_won is worth points_for(DEAL_CLOSED_WON),
# so a band is ~XP_PER_LEVEL/that-many closes). Tuned for a clear, honest ramp, not a grind.
XP_PER_LEVEL = 100


def level_for(xp: int) -> int:
    """The level a rep with `xp` lifetime points sits in. Level 1 starts at 0 (a new rep is never
    level 0); each XP_PER_LEVEL crosses into the next. Never raises; clamps negatives to level 1."""
    return max(0, int(xp)) // XP_PER_LEVEL + 1


def level_progress(xp: int) -> dict:
    """The rep's standing within their current band — enough for a progress bar without the caller
    re-deriving the math: {level, xp, into_level, span, to_next, next_level_xp, pct}."""
    xp = max(0, int(xp))
    level = level_for(xp)
    floor = (level - 1) * XP_PER_LEVEL
    into_level = xp - floor
    next_level_xp = level * XP_PER_LEVEL
    return {
        "level": level,
        "xp": xp,
        "into_level": into_level,
        "span": XP_PER_LEVEL,
        "to_next": next_level_xp - xp,
        "next_level_xp": next_level_xp,
        "pct": round(into_level / XP_PER_LEVEL, 4),
    }


def streak_from_days(active_days, today: str) -> int:
    """Consecutive activity days ending AT `today` (a YYYY-MM-DD string).

    `active_days` is any iterable of YYYY-MM-DD strings (the distinct UTC dates a rep was active).
    A streak is alive only if `today` is present — a yesterday-only run is broken (0). Pure date
    arithmetic; never raises on a well-formed `today`."""
    from datetime import date, timedelta  # noqa: PLC0415 — keep the module import-free at top level

    days = set(active_days)
    if today not in days:
        return 0
    cursor = date.fromisoformat(today)
    streak = 0
    while cursor.isoformat() in days:
        streak += 1
        cursor = cursor - timedelta(days=1)
    return streak
