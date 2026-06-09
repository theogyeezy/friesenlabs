"""Unit: view-spec validation — spec not code; only real Cube members; catalog component types."""
import pytest

from shared import view_spec

ALLOWED = {"Deals.pipeline_value", "Deals.count", "Deals.stage", "Deals.created_at", "Deals.tenant_id"}


def _valid_spec():
    return {
        "view_id": "v1",
        "title": "Cold deals",
        "version": 1,
        "semantic_refs": ["Deals.pipeline_value", "Deals.count", "Deals.stage"],
        "layout": [
            {"type": "kpi", "metric": "Deals.pipeline_value"},
            {
                "type": "chart",
                "encoding": "vega-lite",
                "spec": {"mark": "bar"},
                "query": {"measures": ["Deals.count"], "dimensions": ["Deals.stage"]},
            },
        ],
    }


@pytest.mark.unit
def test_valid_spec_passes():
    view_spec.validate(_valid_spec(), allowed_members=ALLOWED)
    assert view_spec.is_valid(_valid_spec(), ALLOWED)


@pytest.mark.unit
def test_unknown_member_rejected():
    spec = _valid_spec()
    spec["layout"][0]["metric"] = "Deals.secret_other_tenant"
    with pytest.raises(view_spec.ValidationError):
        view_spec.validate(spec, allowed_members=ALLOWED)


@pytest.mark.unit
def test_code_injection_rejected():
    # A spec that tries to smuggle executable content / unknown component type must fail the schema.
    spec = _valid_spec()
    spec["layout"].append({"type": "html", "raw": "<script>alert(1)</script>"})
    with pytest.raises(view_spec.ValidationError):
        view_spec.validate_schema(spec)


@pytest.mark.unit
def test_additional_properties_rejected():
    spec = _valid_spec()
    spec["onClick"] = "doEvilThing()"
    with pytest.raises(view_spec.ValidationError):
        view_spec.validate_schema(spec)


@pytest.mark.unit
def test_chart_must_be_vega_lite():
    spec = _valid_spec()
    spec["layout"][1]["encoding"] = "d3-custom-js"
    with pytest.raises(view_spec.ValidationError):
        view_spec.validate_schema(spec)


@pytest.mark.unit
def test_missing_required_fields_rejected():
    with pytest.raises(view_spec.ValidationError):
        view_spec.validate_schema({"title": "no id or layout"})
