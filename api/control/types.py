"""Shared types for the control plane."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Decision(str, Enum):
    AUTO = "auto"       # execute now
    APPROVE = "approve" # route to Greenlight first
    BLOCK = "block"     # compliance/kill-switch hard stop — never executes


class Level(str, Enum):
    L0 = "L0"  # suggest only — nothing executes
    L1 = "L1"  # ask first — side-effecting needs approval
    L2 = "L2"  # act within limits — auto under thresholds, approve above
    L3 = "L3"  # fully autonomous — acts + reports; only flagged cases pause


@dataclass
class Action:
    """A proposed agent action flowing through the gate."""
    name: str
    # THE TRUST RULE: set by the API from the verified JWT claim ONLY (request bodies cannot carry
    # it). The executor binds its ToolContext to this tenant; it never reads env/header/payload.
    tenant_id: str | None = None
    agent: str | None = None
    side_effecting: bool = False
    channel: str | None = None          # "email" | "sms" | None
    payload: dict = field(default_factory=dict)
    reasoning: str = ""
    value_at_stake: float | None = None
    discount: float | None = None       # fraction, e.g. 0.12 == 12%
    flagged: bool = False               # an L3 exception that must still pause


@dataclass
class GateResult:
    status: str                          # "ok" | "pending_approval" | "blocked"
    decision: Decision
    detail: str = ""
    result: Any = None                   # executor output when executed
    approval: dict | None = None         # Greenlight record when pending
    trace_id: int | str | None = None    # int (in-memory store) or uuid str (PgTraceStore)
