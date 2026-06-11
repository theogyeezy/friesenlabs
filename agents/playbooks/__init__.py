"""Agent Studio playbooks — named, versioned, declarative agent-team definitions.

A playbook is SPEC, NOT CODE (the same non-negotiable as dashboards): a JSON document —
trigger + roster + autonomy + Greenlight policy — validated against
``shared/schemas/playbook.schema.json`` before anything persists or registers. Two layers:

1. **Schema validation** (jsonschema): shape, closed enums, ``additionalProperties: false``,
   and the draft-only constant — ``greenlight.side_effects`` only admits ``"always_ask"``,
   so a playbook can never grant a send/CRM-write autonomy by construction.
2. **Cross-checks against the OWNED definitions** (this module): every roster ``agent`` must
   exist in the owned roster (``agents/roster.py``), and every listed tool must be a SUBSET
   of that agent's owned tool grant resolved through the trusted server-side registry
   (``agents/tools/registry.py``). A playbook narrows grants; it can never widen them —
   the no-privilege-escalation rule.

Nothing here talks to a database or the network; persistence lives in
``agents/playbooks/store.py``, starter templates in ``agents/playbooks/templates.py``, and
roster registration in ``agents/playbooks/activation.py``.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

import jsonschema

_SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "shared", "schemas", "playbook.schema.json"
)

with open(_SCHEMA_PATH, encoding="utf-8") as _f:
    SCHEMA = json.load(_f)

_VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)

# Playbook lifecycle states (the `playbooks.status` column).
STATUS_DRAFT = "draft"
STATUS_ACTIVE = "active"
VALID_STATUSES = {STATUS_DRAFT, STATUS_ACTIVE}


@dataclass
class PlaybookValidationError(Exception):
    reason: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.reason}: {self.detail}" if self.detail else self.reason


def _owned_specs() -> dict[str, "object"]:
    """name -> owned AgentSpec, lazily imported so this module stays import-cheap."""
    from agents.roster import roster  # noqa: PLC0415 — lazy on purpose

    return {spec.name: spec for spec in roster()}


def validate_schema(definition: dict) -> None:
    """Raise PlaybookValidationError if the definition violates the JSON schema."""
    errors = sorted(_VALIDATOR.iter_errors(definition), key=lambda e: list(e.path))
    if errors:
        e = errors[0]
        raise PlaybookValidationError("schema invalid", f"{list(e.path)}: {e.message}")


def validate_roster(definition: dict) -> None:
    """Cross-check the roster against the OWNED definitions (the trusted source of truth).

    * every ``agent`` must be an owned roster name (agents/roster.py);
    * every tool must exist in the trusted registry AND be in that agent's owned grant —
      a playbook may narrow an agent's tools, never widen them (no privilege escalation).
    """
    from agents.tools.registry import TOOL_REGISTRY  # noqa: PLC0415 — lazy on purpose

    owned = _owned_specs()
    for entry in definition.get("roster", []):
        name = entry.get("agent")
        if name not in owned:
            raise PlaybookValidationError(
                "unknown agent",
                f"{name!r} is not in the owned roster ({', '.join(sorted(owned))})",
            )
        granted = set(owned[name].tools)
        for tool in entry.get("tools") or []:
            if tool not in TOOL_REGISTRY:
                raise PlaybookValidationError(
                    "unknown tool", f"{tool!r} is not in the trusted tool registry"
                )
            if tool not in granted:
                raise PlaybookValidationError(
                    "tool not granted",
                    f"{tool!r} is not in {name!r}'s owned grant "
                    f"({', '.join(sorted(granted)) or 'none'}) — a playbook can narrow an "
                    "agent's tools, never widen them",
                )


def validate(definition: dict) -> None:
    """Full validation: JSON schema + owned-roster/registry cross-checks."""
    if not isinstance(definition, dict):
        raise PlaybookValidationError("schema invalid", "definition must be a JSON object")
    validate_schema(definition)
    validate_roster(definition)


def is_valid(definition: dict) -> bool:
    try:
        validate(definition)
        return True
    except PlaybookValidationError:
        return False


# Execution lives in agents/playbooks/runner.py (it imports back from this module — validate,
# STATUS_ACTIVE, PlaybookValidationError — so it is exposed LAZILY here to keep this package's
# import cheap and cycle-free, the same pattern the roster/registry cross-checks use above).
_LAZY = {"PlaybookRunner", "RunRecord", "TriggerEvent", "run"}


def __getattr__(name: str):  # noqa: D401 — PEP 562 lazy attribute access
    if name in _LAZY:
        from . import runner  # noqa: PLC0415 — lazy on purpose

        return getattr(runner, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
