"""Compliance validator (Build Guide Phase 5, Step 32).

A guard step that runs BEFORE Greenlight and blocks non-compliant actions outright — a hard fail never
reaches the approval queue. Deterministic checks (TCPA quiet hours/consent for SMS, CAN-SPAM
unsubscribe for email) plus an optional injected LLM critic pass for regulated verticals.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .types import Action

# TCPA quiet hours: no SMS before 8am / after 9pm local (inclusive-exclusive).
TCPA_QUIET_START = 21  # 9pm
TCPA_QUIET_END = 8     # 8am


@dataclass
class ComplianceResult:
    ok: bool
    reason: str = ""


def _check_sms(action: Action) -> ComplianceResult:
    if not action.payload.get("consent"):
        return ComplianceResult(False, "TCPA: no prior express consent for SMS")
    hour = action.payload.get("local_hour")
    if hour is not None and (hour >= TCPA_QUIET_START or hour < TCPA_QUIET_END):
        return ComplianceResult(False, "TCPA: outside permitted hours (quiet hours)")
    return ComplianceResult(True)


def _check_email(action: Action) -> ComplianceResult:
    body = (action.payload.get("body") or "").lower()
    if "unsubscribe" not in body and not action.payload.get("has_unsubscribe"):
        return ComplianceResult(False, "CAN-SPAM: missing unsubscribe mechanism")
    return ComplianceResult(True)


def validate(action: Action, critic: Callable[[Action], ComplianceResult] | None = None) -> ComplianceResult:
    """Return ok=False with a reason for any non-compliant action. Read-only actions pass."""
    if not action.side_effecting:
        return ComplianceResult(True)

    if action.channel == "sms":
        r = _check_sms(action)
        if not r.ok:
            return r
    elif action.channel == "email":
        r = _check_email(action)
        if not r.ok:
            return r

    # Optional LLM critic pass for regulated verticals (RESPA / Fair Housing / UPL). Injected + mocked.
    if critic is not None:
        return critic(action)
    return ComplianceResult(True)
