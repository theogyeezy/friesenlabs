"""Unit: semantic catalog glue — semantic/model/catalog.json never drifts from the Cube model.

The catalog is the machine-readable member list view-spec validation consumes
(shared/semantic_catalog.py). This test regex-parses every cube under semantic/model/cubes/*.js
and asserts exact parity: every visible measure/dimension is in the JSON, every JSON entry exists
in the model, and hidden members (shown: false) are excluded."""
import json
import os
import re

import pytest

from shared import semantic_catalog

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CUBES_DIR = os.path.join(ROOT, "semantic", "model", "cubes")

_CUBE_RE = re.compile(r"cube\('([A-Za-z][A-Za-z0-9_]*)'")
_SECTION_RE = re.compile(r"^(  measures|  dimensions): {", re.MULTILINE)
_MEMBER_RE = re.compile(r"^    ([A-Za-z][A-Za-z0-9_]*): {(.*)$", re.MULTILINE)


def _members_from_js() -> set[str]:
    members: set[str] = set()
    for fname in sorted(os.listdir(CUBES_DIR)):
        if not fname.endswith(".js"):
            continue
        text = open(os.path.join(CUBES_DIR, fname), encoding="utf-8").read()
        cube_match = _CUBE_RE.search(text)
        assert cube_match, f"{fname}: no cube(...) declaration found"
        cube = cube_match.group(1)
        # Walk measures/dimensions sections; one member per 4-space-indented "name: {" line.
        in_section = False
        for line in text.splitlines():
            if re.match(r"^  (measures|dimensions): \{", line):
                in_section = True
                continue
            if in_section and re.match(r"^  \},?\s*$", line):
                in_section = False
                continue
            if not in_section:
                continue
            m = re.match(r"^    ([A-Za-z][A-Za-z0-9_]*): \{(.*)$", line)
            if not m:
                continue
            name, rest = m.group(1), m.group(2)
            if "shown: false" in rest:
                continue  # hidden members are not part of the queryable catalog
            members.add(f"{cube}.{name}")
    return members


@pytest.mark.unit
def test_catalog_json_matches_cube_model():
    from_js = _members_from_js()
    from_json = semantic_catalog.catalog_members()
    assert from_json == from_js, (
        f"catalog.json drifted from the cube model. "
        f"missing={sorted(from_js - from_json)} extra={sorted(from_json - from_js)}"
    )


@pytest.mark.unit
def test_hidden_tenant_id_is_never_in_the_catalog():
    for member in semantic_catalog.catalog_members():
        assert not member.endswith(".tenant_id")


@pytest.mark.unit
def test_catalog_members_or_none_missing_file():
    assert semantic_catalog.catalog_members_or_none("/nonexistent/catalog.json") is None
    assert semantic_catalog.catalog_members_or_none() is not None


@pytest.mark.unit
def test_catalog_feeds_view_spec_validation():
    from shared import view_spec
    members = semantic_catalog.catalog_members()
    good = {
        "view_id": "v", "title": "Deals by stage", "semantic_refs": ["Deals.count"],
        "layout": [{"type": "kpi", "metric": "Deals.pipeline_value"}],
    }
    view_spec.validate(good, allowed_members=members)
    bad = {**good, "layout": [{"type": "kpi", "metric": "Deals.tenant_id"}]}
    with pytest.raises(view_spec.ValidationError):
        view_spec.validate(bad, allowed_members=members)
