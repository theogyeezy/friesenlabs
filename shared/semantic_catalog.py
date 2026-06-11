"""Semantic-catalog glue: the governed Cube member list, importable from Python.

`semantic/model/catalog.json` is the canonical, machine-readable list of every queryable member
in the Cube model (one entry per measure/dimension, hidden members excluded). This module loads
it into the `{"Cube.member", ...}` set that `shared.view_spec.validate(allowed_members=...)` and
`api.views.SavedViews(allowed_members=...)` consume, so view-specs can be checked against the
REAL catalog instead of a hand-typed copy.

tests/unit/test_semantic_catalog.py regex-parses the cube .js files and asserts the JSON never
drifts from them — the glue is tested, not trusted.

Deployment note: the API container image copies shared/ but not semantic/ (see api/Dockerfile),
so `catalog_members_or_none()` answers None there and member validation stays off, exactly the
pre-existing behavior. Wiring the catalog into the live image is a deliberate follow-up, not a
side effect of importing this module.
"""
from __future__ import annotations

import json
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG_PATH = os.path.join(_REPO_ROOT, "semantic", "model", "catalog.json")


def catalog_members(path: str | None = None) -> set[str]:
    """Load the catalog as a set of "Cube.member" strings. Raises if the file is absent/invalid."""
    with open(path or CATALOG_PATH, encoding="utf-8") as f:
        data = json.load(f)
    members: set[str] = set()
    for cube, fields in data["members"].items():
        for field in fields:
            members.add(f"{cube}.{field}")
    return members


def catalog_members_or_none(path: str | None = None) -> set[str] | None:
    """Like catalog_members, but answers None when the catalog file isn't shipped (e.g. the API
    container image) — callers then skip member validation rather than crash at import."""
    try:
        return catalog_members(path)
    except (OSError, KeyError, ValueError):
        return None
