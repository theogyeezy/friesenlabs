"""Integration: /dashboards CRUD — additive routes over the saved-view store (kind=dashboard),
tenant-scoped via the verified claim, /views list stays dashboard-free."""
import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig, Thresholds
from api.control.greenlight import Greenlight
from api.views import SavedViews


class FakeVerifier:
    """Tenant comes from the token string itself so two tenants can share one app instance."""

    def verify(self, token):
        tenant = token or "A"
        return {"sub": f"u{tenant}", "custom:tenant_id": tenant, "email": f"{tenant}@x.com"}


def _client():
    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda tenant_id: None,
        autonomy_config=AutonomyConfig(thresholds=Thresholds(max_auto_value=1000)),
        executor=lambda a: {"ran": True},
    )
    return TestClient(create_app(deps))


HA = {"Authorization": "Bearer A"}
HB = {"Authorization": "Bearer B"}


def _view_spec(view_id="v1", title="Pipeline"):
    return {"view_id": view_id, "title": title, "semantic_refs": ["Deals.count"],
            "layout": [{"type": "kpi", "metric": "Deals.count"}]}


def _dash_spec(view_id="d1", items=("v1",)):
    return {"kind": "dashboard", "view_id": view_id, "title": "Overview", "spec_version": 2,
            "grid": {"columns": 12}, "items": [{"view_id": i, "span": 6} for i in items]}


@pytest.mark.integration
def test_dashboard_crud_roundtrip():
    client = _client()
    assert client.post("/views", json={"spec": _view_spec("v1")}, headers=HA).status_code == 200
    assert client.post("/views", json={"spec": _view_spec("v2", "Other")}, headers=HA).status_code == 200

    r = client.post("/dashboards", json={"spec": _dash_spec("d1", items=("v1", "v2")),
                                         "source_prompt": "exec overview"}, headers=HA)
    assert r.status_code == 200
    assert r.json()["version"] == 1

    listed = client.get("/dashboards", headers=HA).json()["dashboards"]
    assert [d["view_id"] for d in listed] == ["d1"]

    got = client.get("/dashboards/d1", headers=HA).json()
    assert got["dashboard"]["spec_json"]["kind"] == "dashboard"
    assert set(got["views"]) == {"v1", "v2"}
    assert got["views"]["v1"]["spec_json"]["title"] == "Pipeline"

    # Save again -> version bump (same versioning machinery as views).
    r2 = client.post("/dashboards", json={"spec": _dash_spec("d1", items=("v1",))}, headers=HA)
    assert r2.json()["version"] == 2


@pytest.mark.integration
def test_views_list_excludes_dashboards():
    client = _client()
    client.post("/views", json={"spec": _view_spec("v1")}, headers=HA)
    client.post("/dashboards", json={"spec": _dash_spec("d1")}, headers=HA)
    views = client.get("/views", headers=HA).json()["views"]
    assert [v["view_id"] for v in views] == ["v1"]
    # The dashboard row is still directly addressable (it IS a saved view under the hood).
    assert client.get("/views/d1", headers=HA).status_code == 200


@pytest.mark.integration
def test_dashboard_requires_kind_discriminator():
    client = _client()
    client.post("/views", json={"spec": _view_spec("v1")}, headers=HA)
    r = client.post("/dashboards", json={"spec": _view_spec("not_a_dash")}, headers=HA)
    assert r.status_code == 422
    assert "kind" in r.json()["detail"]


@pytest.mark.integration
def test_dashboard_with_unknown_view_rejected_422():
    client = _client()
    r = client.post("/dashboards", json={"spec": _dash_spec("d1", items=("ghost",))}, headers=HA)
    assert r.status_code == 422
    assert "unknown view" in r.json()["detail"]


@pytest.mark.integration
def test_dashboard_tenant_isolation():
    client = _client()
    client.post("/views", json={"spec": _view_spec("v1")}, headers=HA)
    client.post("/dashboards", json={"spec": _dash_spec("d1")}, headers=HA)
    # Tenant B sees no dashboards, cannot fetch A's, and cannot reference A's views.
    assert client.get("/dashboards", headers=HB).json()["dashboards"] == []
    assert client.get("/dashboards/d1", headers=HB).status_code == 404
    assert client.post("/dashboards", json={"spec": _dash_spec("dB", items=("v1",))},
                       headers=HB).status_code == 422


@pytest.mark.integration
def test_get_dashboard_404_for_plain_view_or_missing():
    client = _client()
    client.post("/views", json={"spec": _view_spec("v1")}, headers=HA)
    assert client.get("/dashboards/v1", headers=HA).status_code == 404
    assert client.get("/dashboards/missing", headers=HA).status_code == 404


@pytest.mark.integration
def test_v2_view_spec_saves_through_existing_route():
    client = _client()
    spec = {
        "view_id": "v2view", "title": "Stage funnel", "spec_version": 2,
        "semantic_refs": ["Deals.count"],
        "layout": [{"type": "funnel", "span": 6,
                    "query": {"measures": ["Deals.count"], "dimensions": ["Deals.stage"]}}],
    }
    assert client.post("/views", json={"spec": spec}, headers=HA).status_code == 200
    # ...and the spec_version gate holds over HTTP too.
    legacy = {**spec, "view_id": "v2bad"}
    legacy.pop("spec_version")
    r = client.post("/views", json={"spec": legacy}, headers=HA)
    assert r.status_code == 422
    assert "spec_version" in r.json()["detail"]
