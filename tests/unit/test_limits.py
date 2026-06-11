"""Unit: per-tenant rate-limit middleware (api/limits.py).

Covers:
  - sliding-window counter returns 429 + Retry-After once over the plan-tier limit
  - separate tenants have separate buckets (no cross-tenant bleed)
  - window reset re-allows after the window elapses
  - exempt paths (/healthz, /public/*, /signup*) are never limited
  - a request with no/invalid bearer token passes THROUGH to the route (401, never 429)
  - limits from config per plan tier (starter/team/scale defaults + env overrides)
  - quota block: POST over the monthly cap returns 429 when enforcement="block"
  - quota warn mode: POST over the monthly cap passes through regardless
  - counter-store error NEVER blocks a request (fail OPEN)
  - GET requests are rate-limited but NOT quota-metered
  - UNLIMITED plan (cap None) skips the quota check
  - unknown/unprovisioned tenant falls back to the most generous tier (never wrongly throttles)
"""
from __future__ import annotations

import pytest

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from api.limits import (
    EXEMPT_PREFIXES,
    PlanResolver,
    TenantLimitsMiddleware,
    _SlidingWindowLimiter,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _echo_app():
    """Minimal Starlette app that echoes back a 200 or an auth 401."""
    async def echo(request: Request):
        return JSONResponse({"ok": True}, status_code=200)

    async def protected(request: Request):
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse({"detail": "not authenticated"}, status_code=401)
        return JSONResponse({"ok": True}, status_code=200)

    return Starlette(routes=[
        Route("/healthz", echo, methods=["GET"]),
        Route("/public/info", echo, methods=["GET"]),
        Route("/public/support", echo, methods=["GET"]),
        Route("/signup", echo, methods=["POST", "GET"]),
        Route("/signup/verify", echo, methods=["GET"]),
        Route("/api/chat", protected, methods=["POST"]),
        Route("/api/data", protected, methods=["GET", "POST"]),
    ])


class _FakeVerifier:
    """Token -> claims dict; raises for anything not in the allow-list."""

    def __init__(self, tokens: dict[str, dict]):
        self._tokens = tokens

    def verify(self, token: str) -> dict:
        if token not in self._tokens:
            raise ValueError(f"invalid token: {token!r}")
        return self._tokens[token]


def _make_client(
    *,
    tokens: dict[str, dict] | None = None,
    limiter: _SlidingWindowLimiter | None = None,
    plan_resolver: PlanResolver | None = None,
    usage_store=None,
    exempt_prefixes=EXEMPT_PREFIXES,
) -> TestClient:
    """Wire up a test client with the TenantLimitsMiddleware."""
    verifier = _FakeVerifier(tokens or {})
    app = _echo_app()
    app.add_middleware(
        TenantLimitsMiddleware,
        verifier=verifier,
        limiter=limiter,
        plan_resolver=plan_resolver,
        usage_store=usage_store,
        exempt_prefixes=exempt_prefixes,
    )
    return TestClient(app, raise_server_exceptions=True)


def _authed_headers(token: str = "tok-t1") -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# _SlidingWindowLimiter unit tests (pure logic, no HTTP)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_sliding_window_allows_up_to_limit():
    lim = _SlidingWindowLimiter()
    for _ in range(3):
        allowed, _ = lim.check("k", 3)
        assert allowed is True


@pytest.mark.unit
def test_sliding_window_blocks_at_limit():
    lim = _SlidingWindowLimiter()
    for _ in range(3):
        lim.check("k", 3)
    allowed, retry = lim.check("k", 3)
    assert allowed is False
    assert retry >= 1.0


@pytest.mark.unit
def test_sliding_window_retry_after_is_positive():
    clock = [0.0]
    lim = _SlidingWindowLimiter(now=lambda: clock[0])
    lim.check("k", 1)  # first hit fills the bucket
    allowed, retry = lim.check("k", 1)
    assert allowed is False
    # retry should be close to 60 seconds (the full window)
    assert 1.0 <= retry <= 60.0


@pytest.mark.unit
def test_sliding_window_separate_keys_are_independent():
    lim = _SlidingWindowLimiter()
    lim.check("a", 1)
    lim.check("a", 1)  # "a" is now blocked
    allowed_a, _ = lim.check("a", 1)
    allowed_b, _ = lim.check("b", 1)  # "b" has its own fresh bucket
    assert allowed_a is False
    assert allowed_b is True


@pytest.mark.unit
def test_sliding_window_resets_after_window_elapses():
    clock = [0.0]
    lim = _SlidingWindowLimiter(now=lambda: clock[0])
    lim.check("k", 2)
    lim.check("k", 2)
    assert lim.check("k", 2)[0] is False  # blocked now
    clock[0] = 61.0  # past the 60-second window
    assert lim.check("k", 2)[0] is True   # re-allowed


@pytest.mark.unit
def test_sliding_window_limit_minimum_one():
    """limit=0 is coerced to 1 — the first request always succeeds."""
    lim = _SlidingWindowLimiter()
    allowed, _ = lim.check("k", 0)
    assert allowed is True
    # Second call is blocked (limit effectively 1)
    allowed2, _ = lim.check("k", 0)
    assert allowed2 is False


# ---------------------------------------------------------------------------
# PlanResolver unit tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_plan_resolver_no_fetch_returns_none():
    """With no fetch function every tenant resolves to None."""
    pr = PlanResolver()
    assert pr.plan("any-tenant") is None


@pytest.mark.unit
def test_plan_resolver_calls_fetch_and_caches():
    calls = []

    def fetch(tid):
        calls.append(tid)
        return "starter"

    pr = PlanResolver(fetch=fetch, ttl_seconds=60.0)
    assert pr.plan("t1") == "starter"
    assert pr.plan("t1") == "starter"   # second call should hit the cache
    assert len(calls) == 1              # fetch called exactly once


@pytest.mark.unit
def test_plan_resolver_fetch_error_returns_none_fail_open():
    def fetch(tid):
        raise RuntimeError("db down")

    pr = PlanResolver(fetch=fetch)
    # Must not raise; fail OPEN to None (-> most generous tier)
    assert pr.plan("t1") is None


@pytest.mark.unit
def test_plan_resolver_ttl_expires_and_refetches():
    clock = [0.0]
    calls = []

    def fetch(tid):
        calls.append(tid)
        return "starter"

    pr = PlanResolver(fetch=fetch, ttl_seconds=10.0, now=lambda: clock[0])
    pr.plan("t1")
    clock[0] = 11.0  # past the TTL
    pr.plan("t1")
    assert len(calls) == 2  # fetched again after expiry


# ---------------------------------------------------------------------------
# Middleware: exempt path tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_healthz_is_never_rate_limited():
    """GET /healthz must always pass through even with a maxed limiter."""
    # Build a limiter that immediately blocks every key.
    clock = [0.0]
    lim = _SlidingWindowLimiter(now=lambda: clock[0])

    client = _make_client(
        tokens={"tok-t1": {"custom:tenant_id": "t1"}},
        limiter=lim,
    )
    # Burn the bucket for t1 on a real route first, then check /healthz
    for _ in range(200):
        lim.check("t1", 1)  # saturate the limiter from outside

    resp = client.get("/healthz")  # no auth header, exempt path
    assert resp.status_code == 200


@pytest.mark.unit
def test_public_paths_are_exempt():
    clock = [0.0]
    lim = _SlidingWindowLimiter(now=lambda: clock[0])
    client = _make_client(
        tokens={"tok-t1": {"custom:tenant_id": "t1"}},
        limiter=lim,
    )
    for _ in range(200):
        lim.check("t1", 1)

    assert client.get("/public/info").status_code == 200
    assert client.get("/public/support").status_code == 200


@pytest.mark.unit
def test_signup_paths_are_exempt():
    clock = [0.0]
    lim = _SlidingWindowLimiter(now=lambda: clock[0])
    client = _make_client(
        tokens={"tok-t1": {"custom:tenant_id": "t1"}},
        limiter=lim,
    )
    for _ in range(200):
        lim.check("t1", 1)

    assert client.post("/signup").status_code == 200
    assert client.get("/signup/verify").status_code == 200


# ---------------------------------------------------------------------------
# Middleware: no/invalid token passes through (never converted to 429)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_no_token_passes_through_to_route_401():
    """A request with no Authorization header is not limited; the route owns the 401."""
    lim = _SlidingWindowLimiter()
    client = _make_client(tokens={}, limiter=lim)
    resp = client.post("/api/chat")  # no Authorization header
    assert resp.status_code == 401  # route's own 401, never 429


@pytest.mark.unit
def test_invalid_token_passes_through_not_converted_to_429():
    """An invalid bearer token cannot be resolved to a tenant — the middleware passes the request
    through and never converts a would-be route-auth failure into a 429.  The key assertion is
    that the limiter does NOT return 429 for an unresolvable token; the route handles its own
    auth response (200 or 401 depending on route logic)."""
    lim = _SlidingWindowLimiter()
    client = _make_client(tokens={}, limiter=lim)
    resp = client.post("/api/chat", headers={"Authorization": "Bearer not-a-real-token"})
    # The middleware passes through (no tenant -> no rate-limit key); definitely NOT 429
    assert resp.status_code != 429


@pytest.mark.unit
def test_no_tenant_claim_in_token_passes_through():
    """A valid JWT with no custom:tenant_id claim is treated as no-tenant -> pass through."""
    tokens = {"tok-no-tenant": {"sub": "user-123"}}  # no custom:tenant_id
    client = _make_client(tokens=tokens)
    # The route itself returns 401 because the auth header is present but not triggering
    # the route's own logic — but the limiter must not block with 429.
    resp = client.post("/api/chat", headers={"Authorization": "Bearer tok-no-tenant"})
    # Route either 200 (open echo) or 401 — but definitely NOT 429
    assert resp.status_code != 429


# ---------------------------------------------------------------------------
# Middleware: per-tenant rate limiting
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_over_rate_limit_returns_429():
    """After burning the per-tenant budget, the next request gets 429."""
    clock = [0.0]
    lim = _SlidingWindowLimiter(now=lambda: clock[0])
    tokens = {"tok-t1": {"custom:tenant_id": "t1"}}

    # Use a plan resolver that gives starter (limit=120/min), but we override the env
    # to set a tiny limit so the test is fast.
    import os
    os.environ["RATE_LIMIT_STARTER_PER_MINUTE"] = "2"
    try:
        pr = PlanResolver(fetch=lambda tid: "starter")
        client = _make_client(tokens=tokens, limiter=lim, plan_resolver=pr)
        headers = _authed_headers("tok-t1")

        resp1 = client.post("/api/chat", headers=headers)
        resp2 = client.post("/api/chat", headers=headers)
        resp3 = client.post("/api/chat", headers=headers)   # over the limit=2

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp3.status_code == 429
    finally:
        os.environ.pop("RATE_LIMIT_STARTER_PER_MINUTE", None)


@pytest.mark.unit
def test_429_includes_retry_after_header():
    import os
    os.environ["RATE_LIMIT_STARTER_PER_MINUTE"] = "1"
    try:
        clock = [0.0]
        lim = _SlidingWindowLimiter(now=lambda: clock[0])
        tokens = {"tok-t1": {"custom:tenant_id": "t1"}}
        pr = PlanResolver(fetch=lambda tid: "starter")
        client = _make_client(tokens=tokens, limiter=lim, plan_resolver=pr)
        headers = _authed_headers("tok-t1")

        client.post("/api/chat", headers=headers)  # burn limit
        resp = client.post("/api/chat", headers=headers)

        assert resp.status_code == 429
        assert "Retry-After" in resp.headers
        retry_after = int(resp.headers["Retry-After"])
        assert retry_after >= 1
    finally:
        os.environ.pop("RATE_LIMIT_STARTER_PER_MINUTE", None)


@pytest.mark.unit
def test_separate_tenants_have_separate_buckets():
    """Burning one tenant's quota must not affect another tenant."""
    import os
    os.environ["RATE_LIMIT_STARTER_PER_MINUTE"] = "2"
    try:
        clock = [0.0]
        lim = _SlidingWindowLimiter(now=lambda: clock[0])
        tokens = {
            "tok-t1": {"custom:tenant_id": "t1"},
            "tok-t2": {"custom:tenant_id": "t2"},
        }
        pr = PlanResolver(fetch=lambda tid: "starter")
        client = _make_client(tokens=tokens, limiter=lim, plan_resolver=pr)

        # Exhaust t1's limit
        client.post("/api/chat", headers=_authed_headers("tok-t1"))
        client.post("/api/chat", headers=_authed_headers("tok-t1"))
        blocked = client.post("/api/chat", headers=_authed_headers("tok-t1"))
        assert blocked.status_code == 429

        # t2 must still be allowed — its bucket is fresh
        resp_t2 = client.post("/api/chat", headers=_authed_headers("tok-t2"))
        assert resp_t2.status_code == 200
    finally:
        os.environ.pop("RATE_LIMIT_STARTER_PER_MINUTE", None)


@pytest.mark.unit
def test_window_reset_re_allows_requests():
    """After the sliding window expires, the tenant's requests are allowed again."""
    import os
    os.environ["RATE_LIMIT_STARTER_PER_MINUTE"] = "1"
    try:
        clock = [0.0]
        lim = _SlidingWindowLimiter(now=lambda: clock[0])
        tokens = {"tok-t1": {"custom:tenant_id": "t1"}}
        pr = PlanResolver(fetch=lambda tid: "starter")
        client = _make_client(tokens=tokens, limiter=lim, plan_resolver=pr)
        headers = _authed_headers("tok-t1")

        client.post("/api/chat", headers=headers)  # uses the 1 allowed
        blocked = client.post("/api/chat", headers=headers)
        assert blocked.status_code == 429

        clock[0] = 61.0  # advance past the 60-second window
        resp = client.post("/api/chat", headers=headers)
        assert resp.status_code == 200
    finally:
        os.environ.pop("RATE_LIMIT_STARTER_PER_MINUTE", None)


# ---------------------------------------------------------------------------
# Plan tier limits from config
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_plan_tier_limits_differ_by_tier(monkeypatch):
    """starter < team < scale in default rate limits."""
    from shared.config import rate_limit_per_minute
    s = rate_limit_per_minute("starter")
    t = rate_limit_per_minute("team")
    sc = rate_limit_per_minute("scale")
    assert s < t < sc


@pytest.mark.unit
def test_plan_tier_limit_env_override_applied(monkeypatch):
    """An env override on a plan tier is respected by the limiter."""
    monkeypatch.setenv("RATE_LIMIT_TEAM_PER_MINUTE", "7")
    from shared.config import rate_limit_per_minute
    assert rate_limit_per_minute("team") == 7


@pytest.mark.unit
def test_unknown_plan_falls_back_to_most_generous_tier():
    """An unresolved tenant (plan=None) is NOT throttled harder than the most generous tier."""
    from shared.config import normalize_plan, rate_limit_per_minute
    fallback = normalize_plan(None)  # should be "scale" — the most generous
    assert rate_limit_per_minute(fallback) == rate_limit_per_minute("scale")


# ---------------------------------------------------------------------------
# Quota: monthly usage cap
# ---------------------------------------------------------------------------

class _StubUsageStore:
    """In-process usage store that just increments a counter per tenant."""

    def __init__(self, starting_at: int = 0):
        self._counters: dict[str, int] = {}
        self._start = starting_at

    def bump(self, tenant_id: str, metric: str) -> int:
        c = self._counters.get(tenant_id, self._start) + 1
        self._counters[tenant_id] = c
        return c


class _ErrorUsageStore:
    """A store that always raises (simulates a DB failure)."""

    def bump(self, tenant_id, metric):
        raise RuntimeError("store exploded")


@pytest.mark.unit
def test_quota_block_returns_429_when_over_cap(monkeypatch):
    """When enforcement=block and the monthly cap is exceeded, POST returns 429."""
    monkeypatch.setenv("QUOTA_ENFORCEMENT", "block")
    monkeypatch.setenv("QUOTA_STARTER_MONTHLY", "2")
    monkeypatch.setenv("RATE_LIMIT_STARTER_PER_MINUTE", "1000")  # keep rate limit out of the way

    clock = [0.0]
    lim = _SlidingWindowLimiter(now=lambda: clock[0])
    # Start the store ALREADY at 2 (== the cap), so the next bump tips it over.
    store = _StubUsageStore(starting_at=2)
    tokens = {"tok-t1": {"custom:tenant_id": "t1"}}
    pr = PlanResolver(fetch=lambda tid: "starter")
    client = _make_client(tokens=tokens, limiter=lim, plan_resolver=pr, usage_store=store)

    resp = client.post("/api/chat", headers=_authed_headers("tok-t1"))
    assert resp.status_code == 429
    body = resp.json()
    assert "quota" in body.get("detail", "").lower()


@pytest.mark.unit
def test_quota_warn_never_blocks(monkeypatch):
    """When enforcement=warn, a POST over the monthly cap still returns 200."""
    monkeypatch.setenv("QUOTA_ENFORCEMENT", "warn")
    monkeypatch.setenv("QUOTA_STARTER_MONTHLY", "1")
    monkeypatch.setenv("RATE_LIMIT_STARTER_PER_MINUTE", "1000")

    clock = [0.0]
    lim = _SlidingWindowLimiter(now=lambda: clock[0])
    store = _StubUsageStore(starting_at=5)  # way over the cap=1
    tokens = {"tok-t1": {"custom:tenant_id": "t1"}}
    pr = PlanResolver(fetch=lambda tid: "starter")
    client = _make_client(tokens=tokens, limiter=lim, plan_resolver=pr, usage_store=store)

    resp = client.post("/api/chat", headers=_authed_headers("tok-t1"))
    assert resp.status_code == 200


@pytest.mark.unit
def test_counter_store_error_never_blocks(monkeypatch):
    """A failing usage store must not block requests (fail OPEN)."""
    monkeypatch.setenv("QUOTA_ENFORCEMENT", "block")
    monkeypatch.setenv("QUOTA_STARTER_MONTHLY", "1")
    monkeypatch.setenv("RATE_LIMIT_STARTER_PER_MINUTE", "1000")

    clock = [0.0]
    lim = _SlidingWindowLimiter(now=lambda: clock[0])
    tokens = {"tok-t1": {"custom:tenant_id": "t1"}}
    pr = PlanResolver(fetch=lambda tid: "starter")
    client = _make_client(
        tokens=tokens, limiter=lim, plan_resolver=pr, usage_store=_ErrorUsageStore()
    )

    resp = client.post("/api/chat", headers=_authed_headers("tok-t1"))
    # The store raised — the request must still succeed (fail OPEN on accounting errors)
    assert resp.status_code == 200


@pytest.mark.unit
def test_get_requests_not_quota_metered(monkeypatch):
    """GET requests are rate-limited but NOT counted against the monthly quota."""
    monkeypatch.setenv("QUOTA_ENFORCEMENT", "block")
    monkeypatch.setenv("QUOTA_STARTER_MONTHLY", "1")
    monkeypatch.setenv("RATE_LIMIT_STARTER_PER_MINUTE", "1000")

    clock = [0.0]
    lim = _SlidingWindowLimiter(now=lambda: clock[0])
    # Usage store would return >cap if ever called (but for GETs it should NOT be called).
    store = _StubUsageStore(starting_at=5)
    tokens = {"tok-t1": {"custom:tenant_id": "t1"}}
    pr = PlanResolver(fetch=lambda tid: "starter")
    client = _make_client(tokens=tokens, limiter=lim, plan_resolver=pr, usage_store=store)

    # GETs must not be blocked by the quota check
    resp = client.get("/api/data", headers=_authed_headers("tok-t1"))
    assert resp.status_code == 200


@pytest.mark.unit
def test_unlimited_plan_skips_quota_check(monkeypatch):
    """A plan with cap=None (UNLIMITED) must never be blocked by the quota gate."""
    monkeypatch.setenv("QUOTA_ENFORCEMENT", "block")
    # Set scale quota to 0 = UNLIMITED
    monkeypatch.setenv("QUOTA_SCALE_MONTHLY", "0")
    monkeypatch.setenv("RATE_LIMIT_SCALE_PER_MINUTE", "1000")

    clock = [0.0]
    lim = _SlidingWindowLimiter(now=lambda: clock[0])
    store = _StubUsageStore(starting_at=999_999)
    tokens = {"tok-t1": {"custom:tenant_id": "t1"}}
    pr = PlanResolver(fetch=lambda tid: "scale")
    client = _make_client(tokens=tokens, limiter=lim, plan_resolver=pr, usage_store=store)

    from shared.config import monthly_quota
    assert monthly_quota("scale") is None  # confirm unlimited

    resp = client.post("/api/chat", headers=_authed_headers("tok-t1"))
    assert resp.status_code == 200
