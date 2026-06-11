"""Integration: Balto NL view creation over the API — chat intent turn, /views/synthesize,
draft save round-trip, tenant isolation, honest 503/404 paths."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.views import SavedViews
from conv.session import Conversation
from conv.views import BALTO_STATUS, DATA_NOT_ON_PLATFORM, ViewSynthesizer

MEMBERS = ["Deals.count", "Deals.totalValue", "Deals.stage", "Contacts.count"]

VALID_SPEC = {
    "view_id": "deals_by_stage",
    "title": "Deals by stage",
    "semantic_refs": ["Deals.count", "Deals.stage"],
    "layout": [
        {
            "type": "chart",
            "encoding": "vega-lite",
            "spec": {"mark": "bar"},
            "query": {"measures": ["Deals.count"], "dimensions": ["Deals.stage"]},
        }
    ],
}


class FakeVerifier:
    def __init__(self, tenant="A", sub="uA"):
        self.tenant, self.sub = tenant, sub

    def verify(self, token):
        return {"sub": self.sub, "custom:tenant_id": self.tenant, "email": "a@x.com"}


class FakeCube:
    configured = True

    def members(self, *, tenant_id):
        return list(MEMBERS)


class FakeGenerator:
    def generate(self, *, request, allowed_members):
        return {"valid": True, "spec": dict(VALID_SPEC), "errors": [], "attempts": 1}


def _deps(*, tenant="A", sub="uA", saved_views=None, synthesizer=None):
    return ApiDeps(
        verifier=FakeVerifier(tenant, sub),
        greenlight=Greenlight(),
        saved_views=saved_views or SavedViews(allowed_members=set(MEMBERS)),
        # A REAL Conversation on the offline FakeRuntime — the chat view-intent turn under test.
        conversation_factory=lambda t: Conversation(tenant_id=t, today=date(2026, 6, 10)),
        autonomy_config=AutonomyConfig(),
        executor=lambda a: {"ran": True},
        view_synthesizer=synthesizer,
    )


def _client(**kw):
    return TestClient(create_app(_deps(**kw)))


H = {"Authorization": "Bearer t"}


@pytest.mark.integration
def test_chat_view_ask_answers_the_exact_balto_line():
    client = _client()
    r = client.post("/chat", json={"message": "Show me a chart of deals by stage"}, headers=H)
    body = r.json()
    assert body["answer"] == BALTO_STATUS
    assert body["view_intent"] is True
    assert body["view_request"] == "Show me a chart of deals by stage"


@pytest.mark.integration
def test_chat_non_view_ask_carries_no_view_intent():
    client = _client()
    body = client.post("/chat", json={"message": "How is the Acme account doing?"},
                       headers=H).json()
    assert body["view_intent"] is False
    assert body["view_request"] is None


@pytest.mark.integration
def test_synthesize_unwired_answers_503():
    client = _client(synthesizer=None)
    r = client.post("/views/synthesize", json={"request": "chart of deals"}, headers=H)
    assert r.status_code == 503
    r = client.post("/views/drafts/abc/save", headers=H)
    assert r.status_code == 503


@pytest.mark.integration
def test_synthesize_then_save_round_trip():
    saved = SavedViews(allowed_members=set(MEMBERS))
    synth = ViewSynthesizer(saved_views=saved, cube=FakeCube(), generator=FakeGenerator())
    client = _client(saved_views=saved, synthesizer=synth)

    r = client.post("/views/synthesize",
                    json={"request": "show me a chart of deals by stage"}, headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["spec"]["view_id"] == "deals_by_stage"
    draft_id = body["draft_id"]

    # Nothing persisted until the explicit save.
    assert client.get("/views", headers=H).json()["views"] == []

    r = client.post(f"/views/drafts/{draft_id}/save", headers=H)
    assert r.status_code == 200
    row = r.json()
    assert row["version"] == 1
    assert row["created_by"] == "uA"
    assert row["source_prompt"] == "show me a chart of deals by stage"

    # The saved view now shows up in the dropdown's list endpoint.
    views = client.get("/views", headers=H).json()["views"]
    assert [v["view_id"] for v in views] == ["deals_by_stage"]

    # The draft was consumed — a second save 404s instead of double-versioning.
    assert client.post(f"/views/drafts/{draft_id}/save", headers=H).status_code == 404


@pytest.mark.integration
def test_synthesize_data_not_found_is_honest():
    synth = ViewSynthesizer(saved_views=SavedViews(allowed_members=set(MEMBERS)),
                            cube=FakeCube(), generator=FakeGenerator())
    client = _client(synthesizer=synth)
    body = client.post("/views/synthesize",
                       json={"request": "graph the daily weather in Austin"}, headers=H).json()
    assert body["status"] == "data_not_found"
    assert body["message"] == DATA_NOT_ON_PLATFORM


@pytest.mark.integration
def test_synthesize_reports_existing_view_instead_of_duplicating():
    saved = SavedViews(allowed_members=set(MEMBERS))
    saved.save("A", dict(VALID_SPEC), source_prompt="deals by stage", created_by="uA")
    synth = ViewSynthesizer(saved_views=saved, cube=FakeCube(), generator=FakeGenerator())
    client = _client(saved_views=saved, synthesizer=synth)
    body = client.post("/views/synthesize",
                       json={"request": "view deals by stage"}, headers=H).json()
    assert body["status"] == "exists"
    assert body["view"]["view_id"] == "deals_by_stage"


@pytest.mark.integration
def test_invalid_generation_never_returns_a_spec():
    class BadGenerator:
        def generate(self, *, request, allowed_members):
            # Violates the schema (empty refs/layout) — must be rejected, never rendered.
            return {"valid": True,
                    "spec": {"view_id": "x", "title": "x", "semantic_refs": [], "layout": []},
                    "errors": [], "attempts": 1}

    synth = ViewSynthesizer(saved_views=SavedViews(allowed_members=set(MEMBERS)),
                            cube=FakeCube(), generator=BadGenerator())
    client = _client(synthesizer=synth)
    body = client.post("/views/synthesize",
                       json={"request": "chart of deals by stage"}, headers=H).json()
    assert body["status"] == "invalid"
    assert "spec" not in body


@pytest.mark.integration
def test_draft_save_is_tenant_scoped():
    # One synthesizer shared by two tenants' apps (exactly the prod shape) — tenant B can
    # never resolve tenant A's draft id.
    saved = SavedViews(allowed_members=set(MEMBERS))
    synth = ViewSynthesizer(saved_views=saved, cube=FakeCube(), generator=FakeGenerator())
    client_a = _client(tenant="A", sub="uA", saved_views=saved, synthesizer=synth)
    client_b = _client(tenant="B", sub="uB", saved_views=saved, synthesizer=synth)

    draft_id = client_a.post("/views/synthesize",
                             json={"request": "chart of deals by stage"},
                             headers=H).json()["draft_id"]
    assert client_b.post(f"/views/drafts/{draft_id}/save", headers=H).status_code == 404
    # A's own save still works after the cross-tenant attempt.
    assert client_a.post(f"/views/drafts/{draft_id}/save", headers=H).status_code == 200
