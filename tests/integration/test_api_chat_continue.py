"""Integration: POST /chat/continue — the async turn contract's API leg (2026-06-12).

A delegation-heavy turn can't settle inside one HTTP request under the edge's 60s ceiling:
/chat returns `settled: false` and the client continues the SAME in-flight turn here (no new
user message). Same auth + kill-switch posture as /chat; honest 503 when chat isn't wired.
"""
import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.views import SavedViews

H = {"Authorization": "Bearer t"}


class FakeVerifier:
    def verify(self, token):
        return {"sub": "uA", "custom:tenant_id": "A", "email": "a@x.com"}


class _Turn:
    def __init__(self, d):
        self._d = d

    def as_dict(self):
        return dict(self._d)


class FakeConvo:
    def __init__(self):
        self.continues = 0

    def send(self, message):
        return _Turn({"answer": "Routing to Scout.", "citations": [], "settled": False})

    def continue_turn(self):
        self.continues += 1
        return _Turn({"answer": "AEs approve up to 10%.",
                      "citations": [{"claim": "x", "source_ref": "demo:kb:p#0", "snippet": "s"}],
                      "settled": True})


def _client(convo=None):
    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=(lambda t: convo), autonomy_config=AutonomyConfig(),
        executor=lambda a: None,
    )
    return TestClient(create_app(deps))


@pytest.mark.integration
def test_continue_returns_the_settled_turn():
    convo = FakeConvo()
    client = _client(convo)
    first = client.post("/chat", headers=H, json={"message": "what can an AE approve?"})
    assert first.status_code == 200 and first.json()["settled"] is False
    cont = client.post("/chat/continue", headers=H)
    assert cont.status_code == 200, cont.text
    body = cont.json()
    assert body["settled"] is True
    assert body["citations"][0]["source_ref"] == "demo:kb:p#0"
    assert convo.continues == 1


@pytest.mark.integration
def test_continue_requires_auth_and_degrades_unwired():
    assert _client(FakeConvo()).post("/chat/continue").status_code == 401
    assert _client(None).post("/chat/continue", headers=H).status_code == 503
