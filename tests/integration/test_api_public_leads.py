"""Integration: POST /public/leads — unauthenticated lead capture at the HTTP layer.

Proves: strict validation (closed kind enum, required name/email, email shape, unknown-field
rejection), the 1KB raw-body cap (413 BEFORE parsing), the per-IP in-process rate limit (429),
storage into the injected store, and the honest-503 unconfigured posture.
"""
import json

import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.public_routes import PublicDeps
from api.views import SavedViews
from signup.leads import MemoryLeadStore


class Clock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def _client(store="memory", rate=3, clock=None):
    leads_store = MemoryLeadStore() if store == "memory" else None
    public = PublicDeps(leads_store=leads_store, rate_per_minute=rate, now=clock or Clock())
    deps = ApiDeps(verifier=object(), greenlight=Greenlight(), saved_views=SavedViews(),
                   conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
                   executor=lambda a: None, public=public)
    return TestClient(create_app(deps)), leads_store


GOOD = {"kind": "book_call", "name": "Ada Lovelace", "email": "ada@example.com",
        "message": "We want the agentic CRM.", "company": "Analytical Engines"}


@pytest.mark.integration
def test_lead_is_validated_and_stored():
    client, store = _client()
    r = client.post("/public/leads", json=GOOD)
    assert r.status_code == 201
    body = r.json()
    assert body["ok"] is True and body["id"]
    assert len(store.rows) == 1
    row = store.rows[0]
    assert row["kind"] == "book_call" and row["email"] == "ada@example.com"
    assert row["name"] == "Ada Lovelace" and row["company"] == "Analytical Engines"
    assert row["source_ip"]   # the rate-limit key is recorded for ops


@pytest.mark.integration
def test_email_kind_and_optional_fields():
    client, store = _client()
    r = client.post("/public/leads", json={"kind": "email", "name": "Bo", "email": "bo@x.co"})
    assert r.status_code == 201
    assert store.rows[0]["message"] is None and store.rows[0]["company"] is None


@pytest.mark.integration
def test_validation_rejects_bad_input():
    client, store = _client(rate=50)
    cases = [
        {**GOOD, "kind": "phone_call"},          # kind outside the closed enum
        {**GOOD, "email": "not-an-email"},       # email shape
        {**GOOD, "name": "   "},                 # empty-after-strip name
        {"kind": "email", "email": "a@b.co"},    # missing name
        {**GOOD, "evil_extra": "x"},             # unknown field (extra=forbid)
        {**GOOD, "message": "m" * 700},          # field over its per-field cap
    ]
    for payload in cases:
        assert client.post("/public/leads", json=payload).status_code == 422, payload
    assert store.rows == []                      # nothing invalid was ever stored


@pytest.mark.integration
def test_raw_body_over_1kb_is_413_before_parsing():
    client, store = _client()
    raw = json.dumps({**GOOD, "message": "x" * 1200}).encode()
    assert len(raw) > 1024
    r = client.post("/public/leads", content=raw,
                    headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert store.rows == []


@pytest.mark.integration
def test_non_json_body_is_422_not_500():
    client, _ = _client()
    r = client.post("/public/leads", content=b"name=ada&email=x",
                    headers={"content-type": "application/json"})
    assert r.status_code == 422


@pytest.mark.integration
def test_per_ip_rate_limit_429_then_window_reset():
    clock = Clock()
    client, store = _client(rate=3, clock=clock)
    for _ in range(3):
        assert client.post("/public/leads", json=GOOD).status_code == 201
    assert client.post("/public/leads", json=GOOD).status_code == 429
    assert len(store.rows) == 3                  # the limited request stored nothing
    clock.advance(61)                            # window rolls over
    assert client.post("/public/leads", json=GOOD).status_code == 201


@pytest.mark.integration
def test_unconfigured_store_answers_honest_503_after_validation():
    client, _ = _client(store=None)
    # Invalid input is still a 422 (validation first — no oracle about configuration).
    assert client.post("/public/leads", json={**GOOD, "kind": "nope"}).status_code == 422
    # Valid input cannot be silently dropped — honest 503, never a fake success.
    assert client.post("/public/leads", json=GOOD).status_code == 503
