"""Unit: kind=dashboard rows in the SAME saved-view store — kind split helpers, referential
integrity at save time (no unknown views, no nesting, no self-reference), resolve for render."""
import pytest

from api.views import SavedViews
from shared.view_spec import ValidationError

ALLOWED = {"Deals.pipeline_value", "Deals.count", "Deals.stage"}


def _view(view_id="v1", title="Pipeline"):
    return {
        "view_id": view_id, "title": title, "semantic_refs": ["Deals.count"],
        "layout": [{"type": "kpi", "metric": "Deals.pipeline_value"}],
    }


def _dashboard(view_id="d1", items=("v1",)):
    return {
        "kind": "dashboard", "view_id": view_id, "title": "Overview", "spec_version": 2,
        "items": [{"view_id": i, "span": 6} for i in items],
    }


def _sv():
    return SavedViews(allowed_members=ALLOWED)


@pytest.mark.unit
def test_dashboard_save_versions_like_a_view():
    sv = _sv()
    sv.save("t1", _view())
    d1 = sv.save("t1", _dashboard())
    assert d1["version"] == 1
    d2 = sv.save("t1", _dashboard())
    assert d2["version"] == 2
    assert sv.get("t1", "d1")["spec_json"]["kind"] == "dashboard"


@pytest.mark.unit
def test_kind_split_lists():
    sv = _sv()
    sv.save("t1", _view("v1"))
    sv.save("t1", _view("v2", "Other"))
    sv.save("t1", _dashboard("d1", items=("v1", "v2")))
    assert {r["view_id"] for r in sv.list_views("t1")} == {"v1", "v2"}
    assert {r["view_id"] for r in sv.list_dashboards("t1")} == {"d1"}
    # store.list still returns everything (the raw store is kind-agnostic)
    assert {r["view_id"] for r in sv.store.list("t1")} == {"v1", "v2", "d1"}


@pytest.mark.unit
def test_dashboard_referencing_unknown_view_never_persists():
    sv = _sv()
    with pytest.raises(ValidationError, match="unknown view"):
        sv.save("t1", _dashboard(items=("nope",)))
    assert sv.get("t1", "d1") is None


@pytest.mark.unit
def test_dashboard_cannot_reference_other_tenants_view():
    sv = _sv()
    sv.save("t2", _view("v1"))  # exists, but for ANOTHER tenant
    with pytest.raises(ValidationError, match="unknown view"):
        sv.save("t1", _dashboard(items=("v1",)))


@pytest.mark.unit
def test_dashboard_cannot_embed_dashboard_or_itself():
    sv = _sv()
    sv.save("t1", _view("v1"))
    sv.save("t1", _dashboard("d1"))
    with pytest.raises(ValidationError, match="cannot embed"):
        sv.save("t1", _dashboard("d2", items=("d1",)))
    with pytest.raises(ValidationError, match="references itself"):
        sv.save("t1", _dashboard("d3", items=("d3",)))


@pytest.mark.unit
def test_resolve_dashboard_returns_latest_referenced_rows():
    sv = _sv()
    sv.save("t1", _view("v1"))
    sv.save("t1", _view("v2"))
    sv.save("t1", _dashboard("d1", items=("v1", "v2")))
    sv.save("t1", _view("v1", title="Pipeline v2"))  # bump v1 AFTER the dashboard was saved
    dash, views = sv.resolve_dashboard("t1", "d1")
    assert dash["view_id"] == "d1"
    assert set(views) == {"v1", "v2"}
    assert views["v1"]["spec_json"]["title"] == "Pipeline v2"  # latest version wins


@pytest.mark.unit
def test_resolve_dashboard_none_for_missing_or_plain_view():
    sv = _sv()
    sv.save("t1", _view("v1"))
    assert sv.resolve_dashboard("t1", "missing") is None
    assert sv.resolve_dashboard("t1", "v1") is None  # a plain view is not a dashboard


@pytest.mark.unit
def test_plain_view_save_path_unchanged():
    sv = _sv()
    r1 = sv.save("t1", _view(), source_prompt="show pipeline", created_by="matt")
    assert r1["version"] == 1
    assert sv.list_dashboards("t1") == []
