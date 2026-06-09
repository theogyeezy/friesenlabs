"""Autonomy levels L0-L3 (Build Guide Phase 5, Step 29).

Per agent, per tenant, stored in config and enforced by the gate. Read-only actions are always AUTO;
the level only governs side-effecting actions. L2 uses threshold logic (value-at-stake, discount).
"""
from __future__ import annotations

from dataclasses import dataclass

from .types import Action, Decision, Level


@dataclass
class Thresholds:
    """L2 limits. An action auto-executes only if it stays under every limit."""
    max_auto_value: float = 1000.0   # value-at-stake ceiling for auto-execute
    max_discount: float = 0.10       # 10%


@dataclass
class AutonomyConfig:
    default_level: Level = Level.L1
    # {(agent, tenant): Level} overrides; falls back to per-tenant then default.
    overrides: dict | None = None
    thresholds: Thresholds = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.overrides is None:
            self.overrides = {}
        if self.thresholds is None:
            self.thresholds = Thresholds()


def resolve(config: AutonomyConfig, agent: str | None, tenant_id: str) -> Level:
    """Resolve the effective level for (agent, tenant)."""
    ov = config.overrides
    if (agent, tenant_id) in ov:
        return ov[(agent, tenant_id)]
    if tenant_id in ov:
        return ov[tenant_id]
    return config.default_level


def _under_thresholds(action: Action, th: Thresholds) -> bool:
    if action.value_at_stake is not None and action.value_at_stake >= th.max_auto_value:
        return False
    if action.discount is not None and action.discount >= th.max_discount:
        return False
    return True


def decide(level: Level, action: Action, config: AutonomyConfig) -> Decision:
    """AUTO (execute now) vs APPROVE (route to Greenlight). Compliance/kill-switch handled by the gate."""
    if not action.side_effecting:
        return Decision.AUTO  # read-only always autos (still validated + traced by the gate)

    if level is Level.L0:
        return Decision.APPROVE            # suggest only — nothing executes
    if level is Level.L1:
        return Decision.APPROVE            # ask first — every side effect needs approval
    if level is Level.L2:
        return Decision.AUTO if _under_thresholds(action, config.thresholds) else Decision.APPROVE
    if level is Level.L3:
        return Decision.APPROVE if action.flagged else Decision.AUTO
    raise ValueError(f"unknown level {level!r}")
