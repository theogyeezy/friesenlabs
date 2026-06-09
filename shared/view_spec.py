"""View-spec validation (Build Guide Phase 7, Step 40).

SPEC, NOT CODE — non-negotiable. The agent emits a declarative spec; we validate it against a strict
JSON schema AND check every referenced member exists in the tenant's Cube catalog, before anything
renders. A declarative spec transmits data, not executable code, which kills the UI-injection attack
class.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

import jsonschema

_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schemas", "view_spec.schema.json")

with open(_SCHEMA_PATH, encoding="utf-8") as _f:
    SCHEMA = json.load(_f)

_VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)


@dataclass
class ValidationError(Exception):
    reason: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.reason}: {self.detail}" if self.detail else self.reason


def _iter_members(spec: dict):
    """Yield every Cube member (Cube.field) referenced anywhere in the spec."""
    for ref in spec.get("semantic_refs", []):
        yield ref
    for block in spec.get("layout", []):
        if block.get("type") == "kpi":
            yield block["metric"]
            _yield_query_members(block.get("filter"), out := [])
            yield from out
        elif block.get("type") in ("chart", "table"):
            _yield_query_members(block.get("query"), out := [])
            yield from out


def _yield_query_members(query, out: list):
    if not query:
        return
    for m in query.get("measures", []):
        out.append(m)
    for d in query.get("dimensions", []):
        out.append(d)
    for td in query.get("timeDimensions", []):
        if td.get("dimension"):
            out.append(td["dimension"])
    for f in query.get("filters", []):
        if f.get("member"):
            out.append(f["member"])


def validate_schema(spec: dict) -> None:
    """Raise ValidationError if the spec violates the JSON schema (shape / no code / catalog types)."""
    errors = sorted(_VALIDATOR.iter_errors(spec), key=lambda e: e.path)
    if errors:
        e = errors[0]
        raise ValidationError("schema invalid", f"{list(e.path)}: {e.message}")


def validate_members(spec: dict, allowed_members: set[str]) -> None:
    """Raise ValidationError if the spec references any Cube member not in the catalog."""
    referenced = set(_iter_members(spec))
    unknown = referenced - set(allowed_members)
    if unknown:
        raise ValidationError("unknown cube members", ", ".join(sorted(unknown)))


def validate(spec: dict, allowed_members: set[str] | None = None) -> None:
    """Full validation: schema + (if a catalog is supplied) real-member check."""
    validate_schema(spec)
    if allowed_members is not None:
        validate_members(spec, allowed_members)


def is_valid(spec: dict, allowed_members: set[str] | None = None) -> bool:
    try:
        validate(spec, allowed_members)
        return True
    except ValidationError:
        return False
