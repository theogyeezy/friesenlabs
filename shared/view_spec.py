"""View-spec validation (Build Guide Phase 7, Step 40).

SPEC, NOT CODE — non-negotiable. The agent emits a declarative spec; we validate it against a strict
JSON schema AND check every referenced member exists in the tenant's Cube catalog, before anything
renders. A declarative spec transmits data, not executable code, which kills the UI-injection attack
class.

Spec versioning (additive evolution):
  * spec_version 1 (the default when absent) — the original kpi / chart / table catalog.
  * spec_version 2 — adds funnel, leaderboard, stat-with-sparkline, cohort-grid, markdown-note,
    the grid/span layout primitive, and the `kind: "dashboard"` composition spec (a named set of
    saved views). v1 specs validate unchanged; a spec that uses v2 features MUST declare
    `spec_version: 2`, so a v1-only renderer can refuse it up front instead of half-drawing it.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

import jsonschema

_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schemas", "view_spec.schema.json")

with open(_SCHEMA_PATH, encoding="utf-8") as _f:
    SCHEMA = json.load(_f)

# The schema root is oneOf(viewSpec, dashboardSpec). Validating against the matching branch
# directly (picked by the `kind` discriminator) keeps error messages precise instead of the
# oneOf "matched none" noise.
_VIEW_VALIDATOR = jsonschema.Draft202012Validator(
    {"$ref": "#/$defs/viewSpec", "$defs": SCHEMA["$defs"]}
)
_DASHBOARD_VALIDATOR = jsonschema.Draft202012Validator(
    {"$ref": "#/$defs/dashboardSpec", "$defs": SCHEMA["$defs"]}
)

SPEC_VERSION_LATEST = 2

# Component types introduced by spec_version 2. Anything in this set forces spec_version >= 2.
V2_COMPONENT_TYPES = frozenset(
    {"funnel", "leaderboard", "stat-with-sparkline", "cohort-grid", "markdown-note"}
)

DASHBOARD_KIND = "dashboard"

# Whitelist for a chart block's Vega-Lite `spec` fragment (mirrored in the JSON schema's
# chartSpecFragment and in web/src/dashboard/viewSpec.ts — keep all three in lockstep).
# Everything else — params, signals, data, datasets, usermeta, projection, config, width,
# height, ... — is REJECTED: the renderer owns data/sizing, and a fragment must stay
# declarative data, never code, signals, or a loader.
CHART_FRAGMENT_ALLOWED_KEYS = frozenset({"mark", "encoding", "transform"})

# No key named href/url may appear anywhere inside encoding/transform: kills the `href`
# encoding channel (clickable marks), the `url` channel (external images), and any lookup
# transform that references a URL (`from: {data: {url: ...}}`).
_LINK_KEYS = frozenset({"href", "url"})


@dataclass
class ValidationError(Exception):
    reason: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.reason}: {self.detail}" if self.detail else self.reason


def is_dashboard(spec) -> bool:
    """True when the spec is a kind=dashboard composition (a named set of saved views)."""
    return isinstance(spec, dict) and spec.get("kind") == DASHBOARD_KIND


def required_spec_version(spec: dict) -> int:
    """The minimum spec_version the features in this spec require (1 or 2)."""
    if is_dashboard(spec):
        return 2
    if "grid" in spec or "kind" in spec:
        return 2
    for block in spec.get("layout", []):
        if not isinstance(block, dict):
            continue
        if block.get("type") in V2_COMPONENT_TYPES or "span" in block:
            return 2
    return 1


def _iter_members(spec: dict):
    """Yield every Cube member (Cube.field) referenced anywhere in the spec."""
    for ref in spec.get("semantic_refs", []):
        yield ref
    for block in spec.get("layout", []):
        btype = block.get("type")
        if btype == "kpi":
            yield block["metric"]
            _yield_query_members(block.get("filter"), out := [])
            yield from out
        elif btype == "stat-with-sparkline":
            yield block["metric"]
            _yield_query_members(block.get("filter"), out := [])
            yield from out
            _yield_query_members(block.get("trend"), out := [])
            yield from out
        elif btype in ("chart", "table", "funnel", "leaderboard", "cohort-grid"):
            _yield_query_members(block.get("query"), out := [])
            yield from out
        # markdown-note carries no Cube members.


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


def _check_no_link_keys(value, where: str) -> None:
    """Recursively reject any object key named href/url (mirrors $defs/noLinkValue)."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _LINK_KEYS:
                raise ValidationError(
                    "chart spec fragment invalid",
                    f"{where}: key {key!r} is not allowed (no links or URL loads in a fragment)",
                )
            _check_no_link_keys(item, f"{where}.{key}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _check_no_link_keys(item, f"{where}[{i}]")


def _validate_chart_fragments(spec: dict) -> None:
    """Explicit allow-list check on every chart block's Vega-Lite `spec` fragment.

    The JSON schema ($defs/chartSpecFragment) enforces the same rules; this walk runs first so
    rejections carry precise, generator-friendly reasons, and stays as defense in depth.
    """
    layout = spec.get("layout")
    if not isinstance(layout, list):
        return
    for i, block in enumerate(layout):
        if not isinstance(block, dict) or block.get("type") != "chart":
            continue
        frag = block.get("spec")
        if frag is None:
            continue
        where = f"layout[{i}].spec"
        if not isinstance(frag, dict):
            raise ValidationError("chart spec fragment invalid", f"{where}: must be an object")
        unknown = set(frag) - CHART_FRAGMENT_ALLOWED_KEYS
        if unknown:
            raise ValidationError(
                "chart spec fragment invalid",
                f"{where}: unknown keys {sorted(unknown)} (allowed: "
                f"{', '.join(sorted(CHART_FRAGMENT_ALLOWED_KEYS))})",
            )
        if "mark" in frag and not isinstance(frag["mark"], str):
            raise ValidationError(
                "chart spec fragment invalid", f"{where}.mark: must be a string mark name"
            )
        if "encoding" in frag:
            if not isinstance(frag["encoding"], dict):
                raise ValidationError(
                    "chart spec fragment invalid", f"{where}.encoding: must be an object"
                )
            _check_no_link_keys(frag["encoding"], f"{where}.encoding")
        if "transform" in frag:
            if not isinstance(frag["transform"], list):
                raise ValidationError(
                    "chart spec fragment invalid", f"{where}.transform: must be an array"
                )
            for j, entry in enumerate(frag["transform"]):
                if not isinstance(entry, dict):
                    raise ValidationError(
                        "chart spec fragment invalid",
                        f"{where}.transform[{j}]: must be an object",
                    )
                _check_no_link_keys(entry, f"{where}.transform[{j}]")


def validate_schema(spec: dict) -> None:
    """Raise ValidationError if the spec violates the JSON schema (shape / no code / catalog types)
    or declares a spec_version lower than the features it uses."""
    if isinstance(spec, dict) and not is_dashboard(spec):
        _validate_chart_fragments(spec)
    validator = _DASHBOARD_VALIDATOR if is_dashboard(spec) else _VIEW_VALIDATOR
    errors = sorted(validator.iter_errors(spec), key=lambda e: [str(p) for p in e.path])
    if errors:
        e = errors[0]
        raise ValidationError("schema invalid", f"{list(e.path)}: {e.message}")
    declared = spec.get("spec_version", 1)
    needed = required_spec_version(spec)
    if declared < needed:
        raise ValidationError(
            "spec_version too low",
            f"declares spec_version {declared} but uses spec_version {needed} features",
        )


def validate_members(spec: dict, allowed_members: set[str]) -> None:
    """Raise ValidationError if the spec references any Cube member not in the catalog."""
    referenced = set(_iter_members(spec))
    unknown = referenced - set(allowed_members)
    if unknown:
        raise ValidationError("unknown cube members", ", ".join(sorted(unknown)))


def validate(spec: dict, allowed_members: set[str] | None = None) -> None:
    """Full validation: schema + (if a catalog is supplied) real-member check.

    Dashboard specs carry view references, not Cube members, so the member check is a no-op for
    them — referenced-view existence is the saved-view store's job (it owns the tenant's views).
    """
    validate_schema(spec)
    if allowed_members is not None and not is_dashboard(spec):
        validate_members(spec, allowed_members)


def is_valid(spec: dict, allowed_members: set[str] | None = None) -> bool:
    try:
        validate(spec, allowed_members)
        return True
    except ValidationError:
        return False
