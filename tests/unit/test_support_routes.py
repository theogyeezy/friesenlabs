"""Unit: POST /public/support — unauthenticated contact/help intake (api/support_routes.py).

Mounts ``mount_support`` on a bare FastAPI app with an in-memory store (MemorySupportStore)
and a controllable clock; zero DB, zero AWS. Covers:

  * valid submission stored + 201 response
  * validation rejection (missing fields, bad email, unknown fields, empty-after-strip) -> 422
  * oversize raw body over the 2KB cap -> 413 (enforced BEFORE parse)
  * per-IP in-process rate limit -> 429 (honest in-process window)
  * honest 503 when the store is unconfigured (``support_store=None``)
  * never a 500 in the tested scenarios (validation -> 422, oversize -> 413, unconfigured -> 503)

The rate-limit clock is injected via ``deps._limiter`` (same pattern as the integration suite)
so the window can be advanced deterministically without sleeping.
"""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.public_routes import _IpRateLimiter
from api.support_routes import MemorySupportStore, SupportDeps, mount_support


class _Clock:
    """Controllable monotonic-enough clock for rate-limit window tests."""

    def __init__(self, t: float = 1_700_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _client(store="memory", rate: int = 5, clock: _Clock | None = None):
    """Build a TestClient with only POST /public/support mounted.

    ``store="memory"``  -> MemorySupportStore (the happy path)
    ``store=None``      -> unconfigured posture (honest 503)
    ``rate``            -> max requests per minute before 429
    ``clock``           -> injected into the rate limiter for deterministic window tests
    """
    support_store = MemorySupportStore() if store == "memory" else None
    deps = SupportDeps(support_store=support_store, rate_per_minute=rate)
    if clock is not None:
        deps._limiter = _IpRateLimiter(rate, now=clock)
    app = FastAPI()
    mount_support(app, deps)
    return TestClient(app), support_store


_GOOD = {
    "name": "Ada Lovelace",
    "email": "ada@example.com",
    "subject": "Cannot reach my workspace",
    "message": "The dashboard is blank since this morning.",
}


# --------------------------------------------------------------------------- happy path

@pytest.mark.unit
def test_valid_submission_returns_201_and_is_stored():
    client, store = _client()
    r = client.post("/public/support", json={**_GOOD, "tenant": "acme-corp"})
    assert r.status_code == 201
    body = r.json()
    assert body["ok"] is True
    assert body["id"]
    assert len(store.rows) == 1
    row = store.rows[0]
    assert row["name"] == "Ada Lovelace"
    assert row["email"] == "ada@example.com"
    assert row["subject"] == "Cannot reach my workspace"
    assert row["message"] == "The dashboard is blank since this morning."
    assert row["tenant"] == "acme-corp"
    assert row["id"] == body["id"]


@pytest.mark.unit
def test_tenant_hint_is_optional():
    client, store = _client()
    r = client.post("/public/support", json=_GOOD)
    assert r.status_code == 201
    assert store.rows[0]["tenant"] is None


@pytest.mark.unit
def test_source_ip_recorded():
    """The rate-limit key (viewer IP) is stored alongside the request for ops triage."""
    client, store = _client()
    r = client.post("/public/support", json=_GOOD)
    assert r.status_code == 201
    assert store.rows[0]["source_ip"] is not None


# --------------------------------------------------------------------------- validation -> 422

@pytest.mark.unit
def test_missing_required_fields_are_422():
    client, store = _client(rate=50)
    cases = [
        # each drops one required field
        {k: v for k, v in _GOOD.items() if k != "name"},
        {k: v for k, v in _GOOD.items() if k != "email"},
        {k: v for k, v in _GOOD.items() if k != "subject"},
        {k: v for k, v in _GOOD.items() if k != "message"},
    ]
    for payload in cases:
        assert client.post("/public/support", json=payload).status_code == 422, payload
    assert store.rows == []


@pytest.mark.unit
def test_bad_email_shape_is_422():
    client, store = _client(rate=50)
    for bad_email in ("not-an-email", "missing@", "@nodomain", ""):
        payload = {**_GOOD, "email": bad_email}
        assert client.post("/public/support", json=payload).status_code == 422, bad_email
    assert store.rows == []


@pytest.mark.unit
def test_empty_after_strip_fields_are_422():
    client, store = _client(rate=50)
    cases = [
        {**_GOOD, "name": "   "},
        {**_GOOD, "subject": "\t"},
        {**_GOOD, "message": ""},
    ]
    for payload in cases:
        assert client.post("/public/support", json=payload).status_code == 422, payload
    assert store.rows == []


@pytest.mark.unit
def test_unknown_extra_field_is_422():
    """extra=\"forbid\" means unknown keys are a caller error, never silently dropped."""
    client, store = _client()
    r = client.post("/public/support", json={**_GOOD, "evil_field": "injected"})
    assert r.status_code == 422
    assert store.rows == []


@pytest.mark.unit
def test_per_field_cap_exceeded_is_422():
    """Subject exceeds its 200-char per-field cap."""
    client, store = _client()
    r = client.post("/public/support", json={**_GOOD, "subject": "s" * 201})
    assert r.status_code == 422
    assert store.rows == []


@pytest.mark.unit
def test_non_json_body_is_422_not_500():
    """Garbage body parses to a Pydantic ValidationError; must surface as 422, never 500."""
    client, _ = _client()
    r = client.post(
        "/public/support",
        content=b"name=ada&subject=x",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422


# --------------------------------------------------------------------------- 413 body cap

@pytest.mark.unit
def test_oversize_body_over_2kb_is_413_before_parsing():
    """The 2KB cap is enforced on raw bytes BEFORE any JSON parse (no validation work done)."""
    client, store = _client()
    # Build a payload whose raw JSON is unambiguously over 2048 bytes.
    raw = json.dumps({**_GOOD, "message": "x" * 2200}).encode()
    assert len(raw) > 2048, "precondition: payload must exceed 2KB"
    r = client.post(
        "/public/support",
        content=raw,
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 413
    assert store.rows == []  # the oversize request must not store anything


@pytest.mark.unit
def test_body_at_exactly_2kb_boundary_is_allowed():
    """A payload right at the 2048-byte boundary is NOT rejected by the cap."""
    client, store = _client()
    # Craft a JSON payload padded to exactly 2048 bytes.
    base = json.dumps({**_GOOD, "message": ""})
    pad_needed = 2048 - len(base.encode())
    if pad_needed > 0:
        payload = {**_GOOD, "message": "a" * pad_needed}
    else:
        payload = _GOOD
    raw = json.dumps(payload).encode()
    assert len(raw) <= 2048
    r = client.post(
        "/public/support",
        content=raw,
        headers={"content-type": "application/json"},
    )
    # Should pass the size gate (may still be 422 if message is empty, but never 413).
    assert r.status_code != 413


# --------------------------------------------------------------------------- 429 rate limit

@pytest.mark.unit
def test_per_ip_rate_limit_triggers_429():
    clock = _Clock()
    client, store = _client(rate=3, clock=clock)
    for _ in range(3):
        assert client.post("/public/support", json=_GOOD).status_code == 201
    # The fourth request within the same window is over the limit.
    r = client.post("/public/support", json=_GOOD)
    assert r.status_code == 429
    assert len(store.rows) == 3  # the rejected request stored nothing


@pytest.mark.unit
def test_rate_limit_window_resets_after_60s():
    clock = _Clock()
    client, store = _client(rate=2, clock=clock)
    for _ in range(2):
        client.post("/public/support", json=_GOOD)
    assert client.post("/public/support", json=_GOOD).status_code == 429
    clock.advance(61)  # roll over the 60-second fixed window
    assert client.post("/public/support", json=_GOOD).status_code == 201


# --------------------------------------------------------------------------- 503 unconfigured

@pytest.mark.unit
def test_unconfigured_store_returns_503_not_500():
    """store=None is the honest 503 posture — never a 500, never a fake success."""
    client, _ = _client(store=None)
    r = client.post("/public/support", json=_GOOD)
    assert r.status_code == 503


@pytest.mark.unit
def test_unconfigured_store_still_validates_first():
    """Validation happens before the store check — a bad request is 422 even when unconfigured."""
    client, _ = _client(store=None)
    assert client.post("/public/support", json={**_GOOD, "email": "nope"}).status_code == 422
    assert client.post("/public/support", json={**_GOOD, "evil_extra": "x"}).status_code == 422


# --------------------------------------------------------------------------- store class import safety

@pytest.mark.unit
def test_memory_support_store_is_import_safe_without_db():
    """MemorySupportStore construction touches no DB driver or AWS at import/init time."""
    s = MemorySupportStore()
    assert s.rows == []


@pytest.mark.unit
def test_memory_support_store_insert_round_trip():
    """insert() returns a unique UUID string and appends a full row."""
    s = MemorySupportStore()
    rid = s.insert(
        name="Test User",
        email="test@example.com",
        subject="Hello",
        message="World",
        tenant="hint",
        source_ip="1.2.3.4",
    )
    assert rid and isinstance(rid, str)
    assert len(s.rows) == 1
    row = s.rows[0]
    assert row["id"] == rid
    assert row["name"] == "Test User"
    assert row["tenant"] == "hint"
    assert row["source_ip"] == "1.2.3.4"
