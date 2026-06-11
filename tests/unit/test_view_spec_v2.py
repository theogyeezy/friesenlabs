"""Unit: view-spec v2 — additive catalog (funnel/leaderboard/stat-with-sparkline/cohort-grid/
markdown-note), grid/span layout, spec_version gating, kind=dashboard specs, and v1 round-trip
(backward compatibility is the contract: every v1 spec must keep validating unchanged)."""
import json
import os

import pytest

from shared import view_spec

ALLOWED = {
    "Deals.pipeline_value", "Deals.count", "Deals.stage", "Deals.created_at",
    "Contacts.count", "Contacts.created_at", "Companies.name",
}


def _v1_spec():
    return {
        "view_id": "v1_classic",
        "title": "Classic pipeline",
        "version": 3,
        "semantic_refs": ["Deals.pipeline_value", "Deals.count", "Deals.stage"],
        "layout": [
            {"type": "kpi", "metric": "Deals.pipeline_value"},
            {
                "type": "chart",
                "encoding": "vega-lite",
                "spec": {"mark": "bar"},
                "query": {"measures": ["Deals.count"], "dimensions": ["Deals.stage"]},
            },
            {"type": "table", "query": {"measures": ["Deals.count"]}},
        ],
    }


def _v2_spec():
    return {
        "view_id": "v2_full",
        "title": "Revenue room",
        "spec_version": 2,
        "grid": {"columns": 12},
        "semantic_refs": ["Deals.pipeline_value", "Deals.count", "Deals.stage"],
        "layout": [
            {"type": "funnel", "title": "Stage funnel", "span": 6,
             "query": {"measures": ["Deals.count"], "dimensions": ["Deals.stage"]}},
            {"type": "leaderboard", "title": "Top companies", "limit": 5, "span": 6,
             "query": {"measures": ["Deals.pipeline_value"], "dimensions": ["Companies.name"]}},
            {"type": "stat-with-sparkline", "title": "Pipeline", "span": 3,
             "metric": "Deals.pipeline_value",
             "trend": {"measures": ["Deals.pipeline_value"],
                       "timeDimensions": [{"dimension": "Deals.created_at", "granularity": "week"}]}},
            {"type": "cohort-grid", "title": "Deals by stage and week", "span": 12,
             "query": {"measures": ["Deals.count"],
                       "dimensions": ["Deals.stage", "Deals.created_at"]}},
            {"type": "markdown-note", "title": "Reading guide", "span": 12,
             "body": "# How to read this\n- Funnel counts **open** deals.\n- Numbers refresh hourly."},
        ],
    }


def _dashboard_spec():
    return {
        "kind": "dashboard",
        "view_id": "exec_overview",
        "title": "Executive overview",
        "spec_version": 2,
        "grid": {"columns": 12},
        "items": [{"view_id": "v1_classic", "span": 6}, {"view_id": "v2_full", "span": 6}],
    }


# --- round-trip: v1 and v2 both validate, and survive JSON serialization unchanged -------------

@pytest.mark.unit
def test_v1_spec_still_validates():
    view_spec.validate(_v1_spec(), allowed_members=ALLOWED)


@pytest.mark.unit
def test_v2_spec_validates_with_all_new_components():
    view_spec.validate(_v2_spec(), allowed_members=ALLOWED)


@pytest.mark.unit
@pytest.mark.parametrize("make", [_v1_spec, _v2_spec, _dashboard_spec])
def test_round_trip_through_json(make):
    spec = make()
    again = json.loads(json.dumps(spec))
    assert again == spec
    view_spec.validate(again, allowed_members=ALLOWED)


@pytest.mark.unit
def test_each_v2_component_validates_alone():
    base = _v2_spec()
    for block in base["layout"]:
        spec = {**base, "layout": [block]}
        view_spec.validate(spec, allowed_members=ALLOWED)


# --- spec_version gating -----------------------------------------------------------------------

@pytest.mark.unit
def test_v2_component_without_spec_version_rejected():
    spec = _v2_spec()
    del spec["spec_version"]
    with pytest.raises(view_spec.ValidationError, match="spec_version too low"):
        view_spec.validate_schema(spec)


@pytest.mark.unit
def test_span_without_spec_version_rejected():
    spec = _v1_spec()
    spec["layout"][0]["span"] = 3
    with pytest.raises(view_spec.ValidationError, match="spec_version too low"):
        view_spec.validate_schema(spec)


@pytest.mark.unit
def test_grid_without_spec_version_rejected():
    spec = _v1_spec()
    spec["grid"] = {"columns": 6}
    with pytest.raises(view_spec.ValidationError, match="spec_version too low"):
        view_spec.validate_schema(spec)


@pytest.mark.unit
def test_v1_spec_may_declare_spec_version_2():
    spec = _v1_spec()
    spec["spec_version"] = 2
    view_spec.validate_schema(spec)


@pytest.mark.unit
def test_unknown_spec_version_rejected():
    spec = _v1_spec()
    spec["spec_version"] = 3
    with pytest.raises(view_spec.ValidationError):
        view_spec.validate_schema(spec)


@pytest.mark.unit
def test_required_spec_version():
    assert view_spec.required_spec_version(_v1_spec()) == 1
    assert view_spec.required_spec_version(_v2_spec()) == 2
    assert view_spec.required_spec_version(_dashboard_spec()) == 2


# --- the catalog stays closed ------------------------------------------------------------------

@pytest.mark.unit
def test_unknown_component_type_rejected():
    spec = _v2_spec()
    spec["layout"].append({"type": "iframe", "src": "https://evil.example"})
    with pytest.raises(view_spec.ValidationError):
        view_spec.validate_schema(spec)


@pytest.mark.unit
@pytest.mark.parametrize("idx", range(5))
def test_additional_properties_rejected_on_every_v2_component(idx):
    spec = _v2_spec()
    spec["layout"][idx]["onClick"] = "doEvilThing()"
    with pytest.raises(view_spec.ValidationError):
        view_spec.validate_schema(spec)


@pytest.mark.unit
def test_span_bounds_enforced():
    spec = _v2_spec()
    spec["layout"][0]["span"] = 13
    with pytest.raises(view_spec.ValidationError):
        view_spec.validate_schema(spec)
    spec["layout"][0]["span"] = 0
    with pytest.raises(view_spec.ValidationError):
        view_spec.validate_schema(spec)


@pytest.mark.unit
def test_markdown_note_body_required_and_bounded():
    spec = _v2_spec()
    note = spec["layout"][4]
    del note["body"]
    with pytest.raises(view_spec.ValidationError):
        view_spec.validate_schema(spec)
    note["body"] = "x" * 4001
    with pytest.raises(view_spec.ValidationError):
        view_spec.validate_schema(spec)


@pytest.mark.unit
def test_v2_members_checked_against_catalog():
    spec = _v2_spec()
    spec["layout"][2]["trend"]["measures"] = ["Deals.secret_other_tenant"]
    with pytest.raises(view_spec.ValidationError, match="unknown cube members"):
        view_spec.validate(spec, allowed_members=ALLOWED)


# --- kind=dashboard specs ----------------------------------------------------------------------

@pytest.mark.unit
def test_dashboard_spec_validates():
    view_spec.validate(_dashboard_spec(), allowed_members=ALLOWED)
    assert view_spec.is_dashboard(_dashboard_spec())
    assert not view_spec.is_dashboard(_v2_spec())


@pytest.mark.unit
def test_dashboard_requires_spec_version_2():
    spec = _dashboard_spec()
    del spec["spec_version"]
    with pytest.raises(view_spec.ValidationError):
        view_spec.validate_schema(spec)


@pytest.mark.unit
def test_dashboard_requires_items():
    spec = _dashboard_spec()
    spec["items"] = []
    with pytest.raises(view_spec.ValidationError):
        view_spec.validate_schema(spec)


@pytest.mark.unit
def test_dashboard_rejects_layout_and_extras():
    spec = _dashboard_spec()
    spec["layout"] = [{"type": "kpi", "metric": "Deals.count"}]
    with pytest.raises(view_spec.ValidationError):
        view_spec.validate_schema(spec)


# --- the web mirror schema never drifts --------------------------------------------------------

@pytest.mark.unit
def test_web_mirror_schema_matches_shared_schema():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "shared", "schemas", "view_spec.schema.json"), encoding="utf-8") as f:
        shared_schema = json.load(f)
    with open(os.path.join(root, "web", "src", "dashboard", "view_spec.schema.json"), encoding="utf-8") as f:
        mirror = json.load(f)
    # The mirror's description appends its provenance sentence; everything else is identical.
    assert mirror["description"].startswith(shared_schema["description"])
    shared_rest = {k: v for k, v in shared_schema.items() if k != "description"}
    mirror_rest = {k: v for k, v in mirror.items() if k != "description"}
    assert mirror_rest == shared_rest
