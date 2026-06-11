"""Sidecar — the agentic layer that works on top of your connected tools.

Sidecar reads the tenant's CRM (deals + contacts, RLS-scoped) and surfaces concrete NEXT ACTIONS:
an aging open deal to follow up on, a contact with no reachable email/phone, a deal with no contact
attached. Each suggestion is grounded in a REAL row (never invented), and accepting one enqueues a
DRAFT action in Greenlight (the existing approval gate + appliers) — Sidecar never writes to the CRM
directly. That is the whole honest contract: it proposes, you approve, the gate applies.

This module is the PURE suggestion engine: `build_suggestions(deals, contacts, now=...)` over already-
read rows (deterministic, no DB / no I/O), so it is trivially testable and the route layer
(api/sidecar_routes.py) owns only the RLS reads + the Greenlight enqueue. The proposed `action` of
each suggestion is a real, registered Greenlight action (api/control/appliers.py) so "accept" leads
to an action the gate can actually apply after sign-off.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Stages that mean a deal is CLOSED (no longer actionable). Mirrors ml/data_loader.WON/LOST_STAGES.
CLOSED_STAGES = frozenset({"closed_won", "won", "closed_lost", "lost"})

# An open deal older than this (no movement implied by age) is worth a nudge.
AGING_DEAL_DAYS = 14
# A contact with no activity in this long (or ever) is worth re-engaging.
STALE_CONTACT_DAYS = 30
# Cap the surfaced set so the panel stays scannable; the route reports the true total (no silent
# truncation — the count of what was trimmed is surfaced to the caller).
MAX_SUGGESTIONS = 20


def _as_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def _age_days(value: Any, now: datetime) -> float | None:
    dt = _as_dt(value)
    if dt is None:
        return None
    return max((now - dt).total_seconds() / 86400.0, 0.0)


def _deal_suggestions(deals: list[dict], now: datetime) -> list[dict]:
    out: list[dict] = []
    for d in deals:
        stage = (d.get("stage") or "").lower()
        if stage in CLOSED_STAGES:
            continue  # closed deals aren't actionable
        deal_id = str(d.get("id"))
        title = d.get("title") or "Untitled deal"
        amount = d.get("amount")
        amount_f = float(amount) if amount is not None else None

        # Unlinked deal — no contact attached, so no one to work it.
        if not d.get("contact_id"):
            out.append({
                "id": f"unlinked_deal:{deal_id}",
                "kind": "unlinked_deal",
                "entity_type": "deal",
                "entity_id": deal_id,
                "title": f"Attach a contact to “{title}”",
                "detail": "This open deal has no contact linked — add one so it can be worked.",
                "priority": (amount_f or 0.0) + 1_000_000,  # surface these first; they block work
                "value_at_stake": amount_f,
                "action": {
                    "action": "create_activity",
                    "deal_id": deal_id,
                    "kind": "task",
                    "body": f"Attach a primary contact to the deal “{title}”.",
                },
            })
            continue

        # Aging open deal — open a while; nudge a follow-up.
        age = _age_days(d.get("created_at"), now)
        if age is not None and age >= AGING_DEAL_DAYS:
            days = int(age)
            out.append({
                "id": f"aging_open_deal:{deal_id}",
                "kind": "aging_open_deal",
                "entity_type": "deal",
                "entity_id": deal_id,
                "title": f"Follow up on “{title}”",
                "detail": f"Open for {days} days with no close — log a follow-up to keep it moving.",
                "priority": (amount_f or 0.0) + age,  # higher value + older = more urgent
                "value_at_stake": amount_f,
                "action": {
                    "action": "create_activity",
                    "deal_id": deal_id,
                    "kind": "follow_up",
                    "body": f"Follow up on “{title}” (open {days} days).",
                },
            })
    return out


def _contact_suggestions(contacts: list[dict], now: datetime) -> list[dict]:
    out: list[dict] = []
    for c in contacts:
        contact_id = str(c.get("id"))
        name = c.get("name") or "this contact"

        # Unreachable contact — neither email nor phone.
        if not c.get("email") and not c.get("phone"):
            out.append({
                "id": f"missing_contact_info:{contact_id}",
                "kind": "missing_contact_info",
                "entity_type": "contact",
                "entity_id": contact_id,
                "title": f"Add contact details for {name}",
                "detail": "No email or phone on file — this contact can’t be reached.",
                "priority": 500_000,
                "value_at_stake": None,
                "action": {
                    "action": "create_activity",
                    "contact_id": contact_id,
                    "kind": "task",
                    "body": f"Find and add an email or phone for {name}.",
                },
            })
            continue

        # Stale contact — no activity in a long time (or ever).
        age = _age_days(c.get("last_activity_at"), now)
        if age is None or age >= STALE_CONTACT_DAYS:
            when = "ever" if age is None else f"in {int(age)} days"
            out.append({
                "id": f"stale_contact:{contact_id}",
                "kind": "stale_contact",
                "entity_type": "contact",
                "entity_id": contact_id,
                "title": f"Reconnect with {name}",
                "detail": f"No activity {when} — a quick touch keeps the relationship warm.",
                "priority": (age or STALE_CONTACT_DAYS),
                "value_at_stake": None,
                "action": {
                    "action": "create_activity",
                    "contact_id": contact_id,
                    "kind": "follow_up",
                    "body": f"Reach out to {name} — no recent activity.",
                },
            })
    return out


def build_suggestions(deals: list[dict], contacts: list[dict], *,
                      now: datetime | None = None, limit: int = MAX_SUGGESTIONS) -> dict:
    """Compute Sidecar's grounded next-action suggestions from already-read CRM rows.

    Pure + deterministic: same rows + same `now` -> the identical ordered list. Returns
    ``{"suggestions": [...], "total": N, "truncated": bool}`` — `total` is the full count BEFORE the
    display cap so the caller never mistakes a trimmed list for "all clear" (no silent truncation).
    """
    now = now or datetime.now(timezone.utc)
    found = _deal_suggestions(deals or [], now) + _contact_suggestions(contacts or [], now)
    # Highest priority first; ties broken by id for stable, deterministic ordering.
    found.sort(key=lambda s: (-s["priority"], s["id"]))
    total = len(found)
    shown = found[:limit]
    # `priority` is an internal sort key — don't leak it to the client.
    for s in shown:
        s.pop("priority", None)
    return {"suggestions": shown, "total": total, "truncated": total > len(shown)}
