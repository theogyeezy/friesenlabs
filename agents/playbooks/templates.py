"""Starter playbook library — the 5 committed templates under agents/playbooks/templates/.

Each template file is ``{"template_id", "summary", "definition"}`` where ``definition`` is a
full playbook definition that MUST validate against shared/schemas/playbook.schema.json + the
owned-roster cross-checks (tests/unit/test_playbook_schema.py proves every committed template).

Templates are read once at first use and served as deep copies — instantiating one into a
tenant's library can never mutate the committed source.
"""
from __future__ import annotations

import copy
import json
import os

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")

_cache: list[dict] | None = None


def _load() -> list[dict]:
    global _cache
    if _cache is None:
        out: list[dict] = []
        for fname in sorted(os.listdir(_TEMPLATES_DIR)):
            if not fname.endswith(".json"):
                continue
            with open(os.path.join(_TEMPLATES_DIR, fname), encoding="utf-8") as f:
                out.append(json.load(f))
        _cache = out
    return _cache


def list_templates() -> list[dict]:
    """All committed templates (deep copies, stable order by filename)."""
    return copy.deepcopy(_load())


def get_template(template_id: str) -> dict | None:
    """One template by id (deep copy), or None."""
    for t in _load():
        if t.get("template_id") == template_id:
            return copy.deepcopy(t)
    return None
