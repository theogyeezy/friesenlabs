"""Unit: POST /views/{id}/data — the view data-loader endpoint, claims-bound, RLS-honored.

Mounts the real app (create_app) with a fake verifier, an in-memory SavedViews store, and a
STUBBED cube client (no network), so the contract is proven with NO database / NO Cube:
  * 401 unauth (the shared current_tenant dependency)
  * the tenant the cube query runs as is ALWAYS the verified claim — never a body/header
  * 503 when the cube client is unwired (None) OR wired-but-unconfigured (honest, never 500/fake)
  * 404 for an unknown view id AND for another tenant's view (RLS makes it unknown -> 404)
  * the {rows:[...]} contract: rows is the primary panel; multi-panel views expose `panels`
  * a Cube load error degrades to 502 (never a 500, never partial-success-as-data)
"""
import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.views import SavedViews

ALLOWED = {"Deals.pipeline_value", "Deals.count", "Deals.stage", "Deals.created_at"}
H = {"Authorization": "Bearer t"}


class FakeVerifier:
    """Verifies any token to tenant A — the only tenant identity source (THE TRUST RULE)."""

    def __init__(self, tenant_id="A", sub="uA"):
        self.tenant_id = tenant_id
        self.sub = sub

    def verify(self, token):
        return {"sub": self.sub, "custom:tenant_id": self.tenant_id, "email": "a@x.com"}


class StubCube:
    """Stub of agents.tools.cube_client.CubeClient's load()/configured contract.

    Records the tenant of every load() call so the test can assert the claim steered it, and
    returns canned rows. `configured` toggles the unconfigured-degradation path.
    """

    def __init__(self, *, configured=True, rows=None, status="ok", error=None):
        self.configured = configured
        self._rows = rows if rows is not None else [{"Deals.count": 7}]
        self._status = status
        self._error = error
        self.calls = []

    def load(self, *, tenant_id, query):
        self.calls.append({"tenant_id": tenant_id, "query": query})
        if self._status != "ok":
            return {"status": self._status, "rows": [], "error": self._error}
        return {"status": "ok", "rows": self._rows}


def _view(view_id="v1", layout=None):
    return {
        "view_id": view_id,
        "title": "Pipeline",
        "semantic_refs": ["Deals.count"],
        "layout": layout or [{"type": "table", "query": {"measures": ["Deals.count"]}}],
    }


def _client(*, cube, verifier=None, saved_views=None):
    sv = saved_views or SavedViews(allowed_members=ALLOWED)
    deps = ApiDeps(
        verifier=verifier or FakeVerifier(),
        greenlight=Greenlight(),
        saved_views=sv,
        conversation_factory=lambda tenant_id: None,
        autonomy_config=AutonomyConfig(),
        executor=lambda action: {"status": "noop"},
        cube=cube,
        # Skip mounting every other optional route surface — this test owns only /views/*/data.
        integrations=None, deals=None, contacts=None, agents=None, workflows=None,
        knowledge=None, cortex=None, public=None, support=None, studio=None, usage=None,
    )
    return TestClient(create_app(deps)), sv


@pytest.mark.unit
def test_unauth_401():
    client, _ = _client(cube=StubCube())
    # No Authorization header -> the shared current_tenant dependency 401s before any work.
    assert client.post("/views/v1/data").status_code == 401


@pytest.mark.unit
def test_returns_rows_for_the_verified_tenant():
    cube = StubCube(rows=[{"Deals.count": 7}])
    client, sv = _client(cube=cube)
    sv.save("A", _view())  # the view exists for tenant A (the verified claim)
    r = client.post("/views/v1/data", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["rows"] == [{"Deals.count": 7}]
    # The cube query ran as the VERIFIED claim tenant only — never a body/header value.
    assert cube.calls and cube.calls[0]["tenant_id"] == "A"
    assert cube.calls[0]["query"] == {"measures": ["Deals.count"]}


@pytest.mark.unit
def test_tenant_comes_from_claim_not_body():
    cube = StubCube()
    client, sv = _client(cube=cube)
    sv.save("A", _view())
    # A smuggled tenant in the body changes NOTHING — the claim (A) is the only source.
    r = client.post("/views/v1/data", headers=H, json={"tenant_id": "B"})
    assert r.status_code == 200
    assert cube.calls[0]["tenant_id"] == "A"


@pytest.mark.unit
def test_503_when_cube_unwired():
    # cube=None (the ApiDeps default) -> honest 503, never a 500, never fake rows.
    client, sv = _client(cube=None)
    sv.save("A", _view())
    r = client.post("/views/v1/data", headers=H)
    assert r.status_code == 503


@pytest.mark.unit
def test_503_when_cube_unconfigured():
    # A wired-but-unconfigured client (no endpoint/secret) -> 503 before any view load.
    cube = StubCube(configured=False)
    client, sv = _client(cube=cube)
    sv.save("A", _view())
    r = client.post("/views/v1/data", headers=H)
    assert r.status_code == 503
    assert cube.calls == []  # never even attempted a load


@pytest.mark.unit
def test_404_unknown_view():
    client, _ = _client(cube=StubCube())
    assert client.post("/views/does-not-exist/data", headers=H).status_code == 404


@pytest.mark.unit
def test_cross_tenant_view_is_404():
    cube = StubCube()
    client, sv = _client(cube=cube)
    sv.save("B", _view("v1"))  # the view exists, but for ANOTHER tenant (B)
    # The verified claim is A; A cannot resolve B's view -> 404, and Cube is never queried.
    r = client.post("/views/v1/data", headers=H)
    assert r.status_code == 404
    assert cube.calls == []


@pytest.mark.unit
def test_502_on_cube_error():
    cube = StubCube(status="error", error="Cube still warming")
    client, sv = _client(cube=cube)
    sv.save("A", _view())
    r = client.post("/views/v1/data", headers=H)
    assert r.status_code == 502


@pytest.mark.unit
def test_multi_panel_view_exposes_panels_and_primary_rows():
    cube = StubCube(rows=[{"Deals.count": 3}])
    client, sv = _client(cube=cube)
    sv.save("A", _view("v1", layout=[
        {"type": "kpi", "metric": "Deals.count"},
        {"type": "table", "query": {"measures": ["Deals.pipeline_value"]}},
    ]))
    r = client.post("/views/v1/data", headers=H)
    assert r.status_code == 200
    body = r.json()
    # Two data-bearing panels -> two cube loads, both as tenant A.
    assert len(cube.calls) == 2
    assert all(c["tenant_id"] == "A" for c in cube.calls)
    # kpi panel #0 becomes a single-measure query; table panel #1 carries its own query.
    assert cube.calls[0]["query"] == {"measures": ["Deals.count"]}
    assert cube.calls[1]["query"] == {"measures": ["Deals.pipeline_value"]}
    assert body["rows"] == [{"Deals.count": 3}]  # primary = first data-bearing panel
    assert [p["panel"] for p in body["panels"]] == [0, 1]
