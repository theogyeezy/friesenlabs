"""Compliance validator (Build Guide Phase 5, Step 32).

A guard step that runs BEFORE Greenlight and blocks non-compliant actions outright — a hard fail never
reaches the approval queue. Deterministic checks (TCPA quiet hours/consent for SMS, CAN-SPAM
unsubscribe for email) plus an optional injected LLM critic pass for regulated verticals.

Enforced from TWO layers:
  * `ActionGate.run` (api/control/gate.py) — the full validate, critic included, blocking before
    the queue.
  * `Greenlight.propose` / `Greenlight.decide` (api/control/greenlight.py) — the deterministic
    floor on EVERY proposal path (worker, sidecar, playbooks) and on the post-edit snapshot right
    before it can be applied, with channel classification from the trusted tool registry.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from .types import Action

log = logging.getLogger(__name__)

# TCPA quiet hours: no SMS before 8am / after 9pm local (inclusive-exclusive).
TCPA_QUIET_START = 21  # 9pm
TCPA_QUIET_END = 8     # 8am


@dataclass
class ComplianceResult:
    ok: bool
    reason: str = ""


def _local_hour(action: Action, now: datetime | None) -> ComplianceResult | int | None:
    """The recipient's local hour. A payload `timezone` (IANA name) is authoritative — the hour
    is computed SERVER-SIDE from the wall clock, so a client-claimed `local_hour` can't dodge
    quiet hours. An unknown timezone fails CLOSED (a blocked send beats a 2am text). With no
    timezone we fall back to the payload's claimed local_hour (None = no quiet-hour check)."""
    tz_name = action.payload.get("timezone")
    if tz_name:
        try:
            tz = ZoneInfo(str(tz_name))
        except Exception:  # noqa: BLE001 — zoneinfo raises several lookup error types
            return ComplianceResult(False, f"TCPA: unknown timezone {tz_name!r}")
        at = now or datetime.now(timezone.utc)
        return at.astimezone(tz).hour
    return action.payload.get("local_hour")


def _check_sms(action: Action, now: datetime | None = None) -> ComplianceResult:
    if not action.payload.get("consent"):
        return ComplianceResult(False, "TCPA: no prior express consent for SMS")
    hour = _local_hour(action, now)
    if isinstance(hour, ComplianceResult):
        return hour
    if hour is not None and (hour >= TCPA_QUIET_START or hour < TCPA_QUIET_END):
        return ComplianceResult(False, "TCPA: outside permitted hours (quiet hours)")
    return ComplianceResult(True)


def _check_email(action: Action) -> ComplianceResult:
    body = (action.payload.get("body") or "").lower()
    if "unsubscribe" not in body and not action.payload.get("has_unsubscribe"):
        return ComplianceResult(False, "CAN-SPAM: missing unsubscribe mechanism")
    return ComplianceResult(True)


def _blocked(action: Action, result: ComplianceResult) -> ComplianceResult:
    """Every block is logged (audit P1) — ops can answer "why did the customer's send stop?"
    without reproducing the request. Reason + action name only; never payload contents."""
    log.warning("compliance BLOCK: %s action=%s tenant=%s agent=%s",
                result.reason, action.name, action.tenant_id, action.agent)
    return result


def validate(action: Action, critic: Callable[[Action], ComplianceResult] | None = None,
             *, now: datetime | None = None) -> ComplianceResult:
    """Return ok=False with a reason for any non-compliant action. Read-only actions pass.
    `now` pins the quiet-hours clock for tests; production callers leave it None."""
    if not action.side_effecting:
        return ComplianceResult(True)

    if action.channel == "sms":
        r = _check_sms(action, now)
        if not r.ok:
            return _blocked(action, r)
    elif action.channel == "email":
        r = _check_email(action)
        if not r.ok:
            return _blocked(action, r)

    # Optional LLM critic pass for regulated verticals (RESPA / Fair Housing / UPL). Injected + mocked.
    if critic is not None:
        r = critic(action)
        return r if r.ok else _blocked(action, r)
    return ComplianceResult(True)
