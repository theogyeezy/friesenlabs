"""Integration: per-tenant rate limiting + plan-tier quota middleware + GET /usage.

Proves at the HTTP layer:
  * rate limit 429 boundary — the Nth+1 request in the window is 429 with a Retry-After header,
    keyed on the VERIFIED tenant claim (a different tenant has its own bucket);
  * health + public/unauth paths are EXEMPT (never throttled / never quota-metered);
  * a request with no/invalid bearer passes through the limiter (the route's auth dependency 401s
    — the limiter never converts a would-be 401 into a 429);
  * monthly quota: enforce ("block" -> 429 once over the cap) and expose (GET /usage reports the
    running counter, plan cap, over_quota flag, and cost summary), claims-bound.
"""
import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.limits import PlanResolver, TenantLimitsMiddleware, _SlidingWindowLimiter
from api.usage import InMemoryCostRecorder, InMemoryUsageStore
from api.usage_routes import UsageDeps
from api.views import SavedViews


class _Verifier:
    """Maps opaque tokens to tenants: 'A'->tenant-A, 'B'->tenant-B; anything else is invalid."""

    _TOKENS = {"A": "tenant-A", "B": "tenant-B"}

    def verify(self, token):
        if token not in self._TOKENS:
            raise ValueError("bad token")
        return {"sub": f"user-{token}", "custom:tenant_id": self._TOKENS[token],
                "email": f"{token}@x.com"}


class _Clock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, secs):
        self.t += secs


def _client(*, plan="starter", rate_per_min=None, usage_store=None, cost_recorder=None,
            clock=None, quota_env=None):
    """Build a TestClient with the limits middleware installed. `plan` is the fixed plan every
    tenant resolves to; env overrides (rate/quota) are set per-test."""
    verifier = _Verifier()
    clock = clock or _Clock()
    usage_store = usage_store if usage_store is not None else InMemoryUsageStore()
    cost_recorder = cost_recorder if cost_recorder is not None else InMemoryCostRecorder()
    resolver = PlanResolver(fetch=lambda _tid: plan)
    limiter = _SlidingWindowLimiter(now=clock)
    usage = UsageDeps(usage_store=usage_store, cost_recorder=cost_recorder, plan_resolver=resolver)
    deps = ApiDeps(
        verifier=verifier,
        greenlight=Greenlight(),
        saved_views=SavedViews(),
        conversation_factory=lambda tenant_id: None,
        autonomy_config=AutonomyConfig(),
        executor=lambda action: {"status": "noop"},
        usage=usage,
        limits_middleware=(TenantLimitsMiddleware, {
            "verifier": verifier, "usage_store": usage_store, "plan_resolver": resolver,
            "limiter": limiter,
        }),
    )
    return TestClient(create_app(deps)), usage_store, cost_recorder


# --------------------------------------------------------------------------- rate limit
@pytest.mark.integration
def test_rate_limit_429_boundary(monkeypatch):
    # starter = 120/min by default; override to a tiny cap so the boundary is testable.
    monkeypatch.setenv("RATE_LIMIT_STARTER_PER_MINUTE", "3")
    client, _u, _c = _client(plan="starter")
    h = {"Authorization": "Bearer A"}
    # 3 allowed (the route 404s — there's no such GET — but that's PAST the limiter), then 429.
    for _ in range(3):
        r = client.get("/views", headers=h)
        assert r.status_code != 429
    blocked = client.get("/views", headers=h)
    assert blocked.status_code == 429
    assert blocked.json()["plan"] == "starter"
    assert int(blocked.headers["Retry-After"]) >= 1


@pytest.mark.integration
def test_rate_limit_is_per_tenant(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_STARTER_PER_MINUTE", "2")
    client, _u, _c = _client(plan="starter")
    for _ in range(2):
        assert client.get("/views", headers={"Authorization": "Bearer A"}).status_code != 429
    # tenant-A is now exhausted, but tenant-B has its own untouched bucket.
    assert client.get("/views", headers={"Authorization": "Bearer A"}).status_code == 429
    assert client.get("/views", headers={"Authorization": "Bearer B"}).status_code != 429


@pytest.mark.integration
def test_rate_limit_window_resets(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_STARTER_PER_MINUTE", "1")
    clock = _Clock()
    client, _u, _c = _client(plan="starter", clock=clock)
    h = {"Authorization": "Bearer A"}
    assert client.get("/views", headers=h).status_code != 429
    assert client.get("/views", headers=h).status_code == 429
    clock.advance(61)  # the 60s window has fully aged out
    assert client.get("/views", headers=h).status_code != 429


@pytest.mark.integration
def test_health_and_public_paths_are_exempt(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_STARTER_PER_MINUTE", "1")
    client, _u, _c = _client(plan="starter")
    # /healthz carries no auth and is exempt — hammering it never 429s.
    for _ in range(5):
        assert client.get("/healthz").status_code == 200


@pytest.mark.integration
def test_no_token_passes_through_to_route_auth(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_STARTER_PER_MINUTE", "1")
    client, _u, _c = _client(plan="starter")
    # No bearer: the limiter has no tenant to key on -> pass through; the route's auth 401s.
    # Crucially NOT 429 (the limiter must never turn a would-be 401 into a 429).
    for _ in range(5):
        r = client.get("/views")
        assert r.status_code == 401


@pytest.mark.integration
def test_invalid_token_is_not_rate_limited(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_STARTER_PER_MINUTE", "1")
    client, _u, _c = _client(plan="starter")
    for _ in range(5):
        assert client.get("/views", headers={"Authorization": "Bearer bogus"}).status_code == 401


# --------------------------------------------------------------------------- quota
@pytest.mark.integration
def test_quota_block_returns_429_once_over_cap(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_STARTER_PER_MINUTE", "10000")  # don't let rate limit interfere
    monkeypatch.setenv("QUOTA_STARTER_MONTHLY", "2")
    monkeypatch.setenv("QUOTA_ENFORCEMENT", "block")
    client, usage_store, _c = _client(plan="starter")
    h = {"Authorization": "Bearer A"}
    # POST is the metered method. /actions validates the tool name AFTER the middleware, so an
    # unknown tool 400s — but the quota bump already happened (that's what we're counting).
    body = {"name": "nope", "payload": {}}
    assert client.post("/actions", json=body, headers=h).status_code == 400   # count=1
    assert client.post("/actions", json=body, headers=h).status_code == 400   # count=2 (== cap)
    over = client.post("/actions", json=body, headers=h)                      # count=3 > cap
    assert over.status_code == 429
    assert over.json()["quota"] == 2 and over.json()["used"] == 3


@pytest.mark.integration
def test_quota_warn_never_blocks(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_STARTER_PER_MINUTE", "10000")
    monkeypatch.setenv("QUOTA_STARTER_MONTHLY", "1")
    monkeypatch.setenv("QUOTA_ENFORCEMENT", "warn")
    client, _u, _c = _client(plan="starter")
    h = {"Authorization": "Bearer A"}
    body = {"name": "nope", "payload": {}}
    for _ in range(4):  # way over the cap of 1 — warn mode never 429s
        assert client.post("/actions", json=body, headers=h).status_code == 400


@pytest.mark.integration
def test_get_requests_are_not_quota_metered(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_STARTER_PER_MINUTE", "10000")
    monkeypatch.setenv("QUOTA_STARTER_MONTHLY", "1")
    monkeypatch.setenv("QUOTA_ENFORCEMENT", "block")
    client, usage_store, _c = _client(plan="starter")
    h = {"Authorization": "Bearer A"}
    for _ in range(5):  # GETs never burn quota
        client.get("/views", headers=h)
    assert usage_store.current("tenant-A")["total"] == 0


# --------------------------------------------------------------------------- GET /usage
@pytest.mark.integration
@pytest.mark.parametrize("path", ["/usage", "/api/usage"])
def test_usage_endpoint_reports_counter_cap_and_cost(monkeypatch, path):
    monkeypatch.setenv("RATE_LIMIT_TEAM_PER_MINUTE", "10000")
    monkeypatch.setenv("QUOTA_TEAM_MONTHLY", "100")
    usage_store = InMemoryUsageStore()
    cost = InMemoryCostRecorder()
    usage_store.bump("tenant-A", "messages", amount=7)
    usage_store.bump("tenant-A", "agent_actions", amount=3)
    cost.record("tenant-A", model="claude-haiku-4", in_tok=1_000_000, out_tok=1_000_000)
    client, _u, _c = _client(plan="team", usage_store=usage_store, cost_recorder=cost)
    r = client.get(path, headers={"Authorization": "Bearer A"})
    assert r.status_code == 200
    body = r.json()
    assert body["plan"] == "team"
    assert body["quota"] == 100
    assert body["usage"]["total"] == 10
    assert body["usage"]["by_metric"] == {"messages": 7, "agent_actions": 3}
    assert body["over_quota"] is False
    # haiku is $1/$5 per Mtok -> 1.0 + 5.0 = 6.0 for 1M/1M.
    assert body["cost"]["est_cost"] == 6.0
    assert body["cost"]["in_tok"] == 1_000_000 and body["cost"]["out_tok"] == 1_000_000


@pytest.mark.integration
def test_usage_endpoint_is_claims_bound(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_TEAM_PER_MINUTE", "10000")
    usage_store = InMemoryUsageStore()
    usage_store.bump("tenant-A", "messages", amount=5)
    usage_store.bump("tenant-B", "messages", amount=99)
    client, _u, _c = _client(plan="team", usage_store=usage_store)
    # tenant-A sees only its own 5; tenant-B's 99 never leaks.
    a = client.get("/usage", headers={"Authorization": "Bearer A"}).json()
    b = client.get("/usage", headers={"Authorization": "Bearer B"}).json()
    assert a["usage"]["total"] == 5
    assert b["usage"]["total"] == 99


@pytest.mark.integration
def test_usage_endpoint_requires_auth():
    client, _u, _c = _client(plan="team")
    assert client.get("/usage").status_code == 401
