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
