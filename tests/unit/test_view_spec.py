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


# ---------------------------------------------------------------------------
# Chart `spec` fragment whitelist (mark / encoding / transform ONLY).
#
# The fragment is spread into vega-embed by the client renderer, so the server
# mirror must reject anything beyond declarative mark/encoding/transform —
# params, signals, data(sets), usermeta, projection, config, and any href/url
# key anywhere inside (link channels, URL-referencing lookup transforms).
# ---------------------------------------------------------------------------


def _spec_with_fragment(fragment):
    spec = _valid_spec()
    spec["layout"][1]["spec"] = fragment
    return spec


@pytest.mark.unit
def test_chart_fragment_inventory_shape_accepted():
    # The exact shape every seed/demo/test fragment in the repo uses today.
    view_spec.validate_schema(_spec_with_fragment({
        "mark": "bar",
        "encoding": {
            "x": {"field": "stage", "type": "nominal", "title": "Stage"},
            "y": {"field": "value", "type": "quantitative", "title": "Value"},
        },
    }))


@pytest.mark.unit
def test_chart_fragment_transform_without_urls_accepted():
    view_spec.validate_schema(_spec_with_fragment({
        "mark": "line",
        "encoding": {"x": {"field": "stage", "type": "nominal"}},
        "transform": [
            {"calculate": "datum.value * 2", "as": "doubled"},
            {"filter": "datum.value > 0"},
        ],
    }))


@pytest.mark.unit
@pytest.mark.parametrize(
    "key,value",
    [
        ("params", [{"name": "p", "value": 1}]),
        ("signals", [{"name": "s"}]),
        ("data", {"url": "https://evil.example/x.json"}),
        ("data", {"values": []}),
        ("datasets", {"d": []}),
        ("usermeta", {"embedOptions": {"loader": {"http": {}}}}),
        ("projection", {"type": "mercator"}),
        ("config", {"mark": {"href": "https://evil.example"}}),
        ("width", 800),
        ("height", 600),
        ("title", "smuggled"),
        ("$schema", "https://vega.github.io/schema/vega/v6.json"),
        ("autosize", "fit"),
    ],
)
def test_chart_fragment_unknown_keys_rejected(key, value):
    with pytest.raises(view_spec.ValidationError):
        view_spec.validate_schema(_spec_with_fragment({"mark": "bar", key: value}))


@pytest.mark.unit
def test_chart_fragment_lookup_transform_with_url_rejected():
    with pytest.raises(view_spec.ValidationError):
        view_spec.validate_schema(_spec_with_fragment({
            "mark": "bar",
            "transform": [{
                "lookup": "stage",
                "from": {"data": {"url": "https://evil.example/leak.json"},
                         "key": "stage", "fields": ["secret"]},
            }],
        }))


@pytest.mark.unit
def test_chart_fragment_lookup_transform_inline_data_accepted():
    # A lookup over INLINE values carries no URL — allowed.
    view_spec.validate_schema(_spec_with_fragment({
        "mark": "bar",
        "transform": [{
            "lookup": "stage",
            "from": {"data": {"values": [{"stage": "won", "rank": 1}]},
                     "key": "stage", "fields": ["rank"]},
        }],
    }))


@pytest.mark.unit
def test_chart_fragment_href_encoding_channel_rejected():
    with pytest.raises(view_spec.ValidationError):
        view_spec.validate_schema(_spec_with_fragment({
            "mark": "point",
            "encoding": {"href": {"field": "link"}},
        }))


@pytest.mark.unit
def test_chart_fragment_url_encoding_channel_rejected():
    # mark=image + a url channel would load external images.
    with pytest.raises(view_spec.ValidationError):
        view_spec.validate_schema(_spec_with_fragment({
            "mark": "image",
            "encoding": {"url": {"field": "img"}},
        }))


@pytest.mark.unit
def test_chart_fragment_nested_href_rejected():
    # href smuggled below the channel level (e.g. inside a condition).
    with pytest.raises(view_spec.ValidationError):
        view_spec.validate_schema(_spec_with_fragment({
            "mark": "bar",
            "encoding": {"x": {"condition": {"href": "https://evil.example"}}},
        }))


@pytest.mark.unit
def test_chart_fragment_object_mark_rejected():
    # Mark definition objects can carry href/url; only string marks are allowed.
    with pytest.raises(view_spec.ValidationError):
        view_spec.validate_schema(_spec_with_fragment({
            "mark": {"type": "point", "href": "https://evil.example"},
        }))


@pytest.mark.unit
def test_chart_fragment_non_object_rejected():
    with pytest.raises(view_spec.ValidationError):
        view_spec.validate_schema(_spec_with_fragment("bar"))


@pytest.mark.unit
def test_chart_fragment_absent_or_empty_still_valid():
    spec = _valid_spec()
    del spec["layout"][1]["spec"]
    view_spec.validate_schema(spec)
    view_spec.validate_schema(_spec_with_fragment({}))


@pytest.mark.unit
def test_chart_fragment_json_schema_alone_enforces_whitelist():
    # The JSON SCHEMA must enforce the fragment whitelist BY ITSELF (the explicit
    # Python walk in validate_schema is defense in depth, not the only gate) —
    # the schema is what the spec-generator prompt embeds and what any non-Python
    # consumer validates against. Pin both directions so the mirrors can't drift.
    import jsonschema

    validator = jsonschema.Draft202012Validator(view_spec.SCHEMA)

    ok = _spec_with_fragment({
        "mark": "bar",
        "encoding": {"x": {"field": "stage", "type": "nominal"}},
        "transform": [{"filter": "datum.value > 0"}],
    })
    assert not list(validator.iter_errors(ok))

    for bad_fragment in (
        {"data": {"url": "https://evil.example"}},
        {"params": []},
        {"mark": {"type": "point", "href": "https://evil.example"}},
        {"encoding": {"href": {"field": "link"}}},
        {"transform": [{"lookup": "s", "from": {"data": {"url": "https://evil.example"}}}]},
    ):
        assert list(validator.iter_errors(_spec_with_fragment(bad_fragment))), bad_fragment
