"""Integration: POST /public/support — unauthenticated contact/help intake at the HTTP layer.

Proves: strict validation (required name/email/subject/message, email shape, unknown-field
rejection, per-field caps), the 2KB raw-body cap (413 BEFORE parsing), the per-IP in-process rate
limit (429), storage into the injected store, the honest-503 unconfigured posture, and that the
free-text ``tenant`` hint is stored as data only (never trusted for auth — THE TRUST RULE).
"""
import json

import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.public_routes import PublicDeps
from api.support_routes import MemorySupportStore, SupportDeps
from api.views import SavedViews


class Clock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def _client(store="memory", rate=3, clock=None):
    support_store = MemorySupportStore() if store == "memory" else None
    # The support limiter clock is injected via the limiter the deps build; we reuse the leads
    # limiter machinery, which defaults to time.time. For deterministic window tests we pass a
    # clock through a pre-built _IpRateLimiter on the SupportDeps.
    from api.public_routes import _IpRateLimiter
    support = SupportDeps(support_store=support_store, rate_per_minute=rate)
    if clock is not None:
        support._limiter = _IpRateLimiter(rate, now=clock)
    # The route mounts only when deps.public is also present in some wirings; here we pass an
    # inert PublicDeps so the app factory is happy and only /public/support is exercised.
    public = PublicDeps(leads_store=None)
    deps = ApiDeps(verifier=object(), greenlight=Greenlight(), saved_views=SavedViews(),
                   conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
                   executor=lambda a: None, public=public, support=support)
    return TestClient(create_app(deps)), support_store


GOOD = {"name": "Ada Lovelace", "email": "ada@example.com",
        "subject": "Cannot reach my workspace", "message": "The dashboard is blank since this morning.",
        "tenant": "analytical-engines"}


@pytest.mark.integration
def test_request_is_validated_and_stored():
    client, store = _client()
    r = client.post("/public/support", json=GOOD)
    assert r.status_code == 201
    body = r.json()
    assert body["ok"] is True and body["id"]
    assert len(store.rows) == 1
    row = store.rows[0]
    assert row["subject"] == "Cannot reach my workspace"
    assert row["email"] == "ada@example.com" and row["name"] == "Ada Lovelace"
    assert row["tenant"] == "analytical-engines"   # stored as a triage hint only
    assert row["source_ip"]                         # the rate-limit key is recorded for ops


@pytest.mark.integration
def test_tenant_hint_is_optional():
    client, store = _client()
    payload = {k: v for k, v in GOOD.items() if k != "tenant"}
    r = client.post("/public/support", json=payload)
    assert r.status_code == 201
    assert store.rows[0]["tenant"] is None


@pytest.mark.integration
def test_validation_rejects_bad_input():
    client, store = _client(rate=50)
    cases = [
        {**GOOD, "email": "not-an-email"},        # email shape
        {**GOOD, "name": "   "},                  # empty-after-strip name
        {**GOOD, "subject": "  "},                # empty-after-strip subject
        {**GOOD, "message": ""},                  # empty message
        {"name": "Bo", "email": "bo@x.co", "subject": "hi"},   # missing message
        {**GOOD, "evil_extra": "x"},              # unknown field (extra=forbid)
        {**GOOD, "subject": "s" * 250},           # field over its per-field cap
    ]
    for payload in cases:
        assert client.post("/public/support", json=payload).status_code == 422, payload
    assert store.rows == []                        # nothing invalid was ever stored


@pytest.mark.integration
def test_raw_body_over_2kb_is_413_before_parsing():
    client, store = _client()
    raw = json.dumps({**GOOD, "message": "x" * 2200}).encode()
    assert len(raw) > 2048
    r = client.post("/public/support", content=raw,
                    headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert store.rows == []


@pytest.mark.integration
def test_non_json_body_is_422_not_500():
    client, _ = _client()
    r = client.post("/public/support", content=b"name=ada&subject=x",
                    headers={"content-type": "application/json"})
    assert r.status_code == 422


@pytest.mark.integration
def test_per_ip_rate_limit_429_then_window_reset():
    clock = Clock()
    client, store = _client(rate=3, clock=clock)
    for _ in range(3):
        assert client.post("/public/support", json=GOOD).status_code == 201
    assert client.post("/public/support", json=GOOD).status_code == 429
    assert len(store.rows) == 3                    # the limited request stored nothing
    clock.advance(61)                              # window rolls over
    assert client.post("/public/support", json=GOOD).status_code == 201


@pytest.mark.integration
def test_unconfigured_store_answers_honest_503_after_validation():
    client, _ = _client(store=None)
    # Invalid input is still a 422 (validation first — no oracle about configuration).
    assert client.post("/public/support", json={**GOOD, "email": "nope"}).status_code == 422
    # Valid input cannot be silently dropped — honest 503, never a fake success.
    assert client.post("/public/support", json=GOOD).status_code == 503


@pytest.mark.integration
def test_control_chars_stripped_from_freetext():
    client, store = _client()
    r = client.post("/public/support", json={
        **GOOD, "subject": "bad\x00subject", "message": "line1\x07line2"})
    assert r.status_code == 201
    assert "\x00" not in store.rows[0]["subject"]
    assert "\x07" not in store.rows[0]["message"]
