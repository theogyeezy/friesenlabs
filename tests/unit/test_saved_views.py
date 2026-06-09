"""Unit: saved views — save/version/refine/edit, tenant-scoped, never persists an invalid spec."""
import pytest

from api.views import SavedViews
from shared.view_spec import ValidationError

ALLOWED = {"Deals.pipeline_value", "Deals.count", "Deals.stage"}


def _spec(view_id="v1", title="Pipeline"):
    return {
        "view_id": view_id, "title": title, "semantic_refs": ["Deals.count"],
        "layout": [{"type": "kpi", "metric": "Deals.pipeline_value"}],
    }


@pytest.mark.unit
def test_save_starts_at_version_1_then_bumps():
    sv = SavedViews(allowed_members=ALLOWED)
    r1 = sv.save("t1", _spec(), source_prompt="show pipeline", created_by="matt")
    assert r1["version"] == 1
    r2 = sv.save("t1", _spec(title="Pipeline v2"))
    assert r2["version"] == 2
    assert sv.get("t1", "v1")["spec_json"]["title"] == "Pipeline v2"


@pytest.mark.unit
def test_refine_nl_patches_and_versions():
    sv = SavedViews(allowed_members=ALLOWED)
    sv.save("t1", _spec())

    def patcher(spec, instruction):
        patched = {**spec}
        patched["layout"] = [{
            "type": "chart", "encoding": "vega-lite", "spec": {"mark": "line"},
            "query": {"measures": ["Deals.count"], "dimensions": ["Deals.stage"]},
        }]
        return patched

    out = sv.refine_nl("t1", "v1", "make it a line chart", patcher)
    assert out["version"] == 2
    assert out["spec_json"]["layout"][0]["spec"]["mark"] == "line"
    assert out["source_prompt"] == "make it a line chart"


@pytest.mark.unit
def test_invalid_spec_never_persists():
    sv = SavedViews(allowed_members=ALLOWED)
    bad = _spec()
    bad["layout"][0]["metric"] = "Deals.not_real"
    with pytest.raises(ValidationError):
        sv.save("t1", bad)
    assert sv.get("t1", "v1") is None  # nothing was written


@pytest.mark.unit
def test_tenant_scoped():
    sv = SavedViews(allowed_members=ALLOWED)
    sv.save("t1", _spec())
    sv.save("t2", _spec(view_id="v2", title="Other"))
    assert {r["view_id"] for r in sv.store.list("t1")} == {"v1"}
    assert {r["view_id"] for r in sv.store.list("t2")} == {"v2"}
    assert sv.get("t2", "v1") is None
