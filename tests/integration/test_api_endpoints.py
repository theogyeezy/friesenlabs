"""Integration: API endpoints — views CRUD, chat (cited answer), actions through the gate."""
import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig, Thresholds
from api.control.greenlight import Greenlight
from api.control.types import Level
from api.views import SavedViews


class FakeVerifier:
    def verify(self, token):
        return {"sub": "uA", "custom:tenant_id": "A", "email": "a@x.com"}


class FakeTurn:
    def as_dict(self):
        return {"answer": "Your pipeline is healthy [1].",
                "citations": [{"claim": "Your pipeline is healthy", "source_ref": "doc1", "snippet": "..."}]}


class FakeConversation:
    def __init__(self, tenant_id):
        self.tenant_id = tenant_id

    def send(self, message, **kw):
        return FakeTurn()


def _line_chart_patcher(spec, instruction):
    patched = {**spec}
    patched["layout"] = [{"type": "chart", "encoding": "vega-lite", "spec": {"mark": "line"},
                          "query": {"measures": ["Deals.count"]}}]
    return patched


def _client(level=Level.L1):
    executed = []
    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=FakeConversation,
        autonomy_config=AutonomyConfig(default_level=level, thresholds=Thresholds(max_auto_value=1000)),
        executor=lambda a: executed.append(a) or {"ran": True},
        view_patcher=_line_chart_patcher,
    )
    return TestClient(create_app(deps)), executed


H = {"Authorization": "Bearer t"}


@pytest.mark.integration
def test_view_save_and_get():
    client, _ = _client()
    spec = {"view_id": "v1", "title": "Pipeline", "semantic_refs": ["Deals.count"],
            "layout": [{"type": "kpi", "metric": "Deals.count"}]}
    assert client.post("/views", json={"spec": spec}, headers=H).json()["version"] == 1
    got = client.get("/views/v1", headers=H).json()
    assert got["spec_json"]["title"] == "Pipeline"


@pytest.mark.integration
def test_invalid_view_rejected_422():
    client, _ = _client()
    bad = {"view_id": "v1", "title": "x", "semantic_refs": [], "layout": []}  # empty refs/layout
    assert client.post("/views", json={"spec": bad}, headers=H).status_code == 422


@pytest.mark.integration
def test_view_refine_patches_and_versions():
    client, _ = _client()
    spec = {"view_id": "v1", "title": "Pipeline", "semantic_refs": ["Deals.count"],
            "layout": [{"type": "kpi", "metric": "Deals.count"}]}
    client.post("/views", json={"spec": spec}, headers=H)
    r = client.post("/views/v1/refine", json={"instruction": "make it a line chart"}, headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == 2
    assert body["spec_json"]["layout"][0]["spec"]["mark"] == "line"


@pytest.mark.integration
def test_chat_returns_cited_answer():
    client, _ = _client()
    r = client.post("/chat", json={"message": "how is my pipeline?"}, headers=H).json()
    assert r["citations"][0]["source_ref"] == "doc1"


@pytest.mark.integration
def test_action_autoexecutes_under_l3():
    client, executed = _client(level=Level.L3)
    r = client.post("/actions", json={"name": "read_crm", "side_effecting": False}, headers=H).json()
    assert r["status"] == "ok"
    assert len(executed) == 1


@pytest.mark.integration
def test_action_pends_under_l1():
    client, executed = _client(level=Level.L1)
    body = {"name": "send_email", "side_effecting": True, "channel": "email",
            "payload": {"body": "hi unsubscribe"}}
    r = client.post("/actions", json=body, headers=H).json()
    assert r["status"] == "pending_approval"
    assert executed == []  # never executed
    # ...and it shows up in that tenant's approval queue.
    assert len(client.get("/approvals", headers=H).json()["approvals"]) == 1


@pytest.mark.integration
def test_action_blocked_by_compliance():
    client, executed = _client(level=Level.L3)
    body = {"name": "send_email", "payload": {"body": "no unsub"}}
    r = client.post("/actions", json=body, headers=H).json()
    assert r["status"] == "blocked"
    assert executed == []


@pytest.mark.integration
def test_forged_side_effecting_flag_cannot_bypass_greenlight():
    # SECURITY (H1): a client forging side_effecting:false on send_email must NOT auto-execute.
    client, executed = _client(level=Level.L1)
    body = {"name": "send_email", "side_effecting": False, "channel": None,
            "payload": {"body": "hi unsubscribe"}}
    r = client.post("/actions", json=body, headers=H).json()
    assert r["status"] == "pending_approval"   # derived from the registry, not the body
    assert executed == []                       # never sent


@pytest.mark.integration
def test_unknown_tool_rejected():
    client, _ = _client(level=Level.L3)
    assert client.post("/actions", json={"name": "rm_-rf"}, headers=H).status_code == 400


@pytest.mark.integration
def test_chat_503_when_backend_unconfigured():
    from api.app import ApiDeps, create_app
    deps = ApiDeps(verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
                   conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
                   executor=lambda a: None)
    c = TestClient(create_app(deps))
    assert c.post("/chat", json={"message": "hi"}, headers=H).status_code == 503
