"""Per-tenant rate limiting + plan-tier monthly usage quota (the request-path gate).

A Starlette middleware that, for AUTHED requests only, keys a sliding-window rate limit and a
monthly-quota check on the VERIFIED Cognito tenant claim (THE TRUST RULE — the tenant comes from
the same JWT verification the routes use, never a header/body). It runs in front of the app so a
runaway tenant is throttled before any work is done.

What it does NOT touch (so the unauth surface + health are never blocked):
  * unauthenticated / public paths (`/healthz`, `/public/*`, signup webhook + signup flow) and any
    request with no/invalid bearer token — those carry no tenant to key on; the route's own auth
    dependency returns the 401, the limiter stays out of the way (fail OPEN on no-tenant).
  * read-only/health paths are exempt by prefix (EXEMPT_PREFIXES).

Rate limit (429 + Retry-After):
  * a per-tenant sliding-window counter (in-process, bounded, TTL-swept). MULTI-INSTANCE CAVEAT:
    in-process state means with N Fargate tasks the effective ceiling is N×limit — this is
    per-tenant FAIRNESS, not the DoS edge (the CloudFront WAF rate rule is the real flood gate).
    A Postgres-backed limiter is the drop-in upgrade if a hard cross-instance cap is needed.

Quota (monthly messages + agent_actions vs the plan cap):
  * the gate BUMPS the `messages` counter for a metered request and compares the running monthly
    total to the plan cap. enforcement="block" -> 429 once over; "warn" -> never blocks (GET /usage
    surfaces over_quota). UNLIMITED plans (cap None) skip the check. A counter-store error NEVER
    blocks a request (fail OPEN — usage accounting must not take the API down).

The plan tier is resolved via an injected `PlanResolver` (tenant_id -> plan label), cached; an
unresolved tenant falls back to the most generous tier (config.normalize_plan) so a missing
accounts row never wrongly throttles a paying customer.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Callable, Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from shared.config import (
    monthly_quota,
    normalize_plan,
    quota_enforcement,
    rate_limit_per_minute,
)

log = logging.getLogger("api.limits")

_WINDOW_SECONDS = 60.0

# Path prefixes the limiter NEVER gates: health + the unauthenticated/public surface (these carry
# no tenant claim to key on; signup runs pre-tenant). Everything else is an authed tenant route.
EXEMPT_PREFIXES = ("/healthz", "/public", "/signup")
# Only metered (counted against the monthly quota) when the method mutates / drives agent work.
# GETs are rate-limited but NOT quota-metered (reading your own dashboards shouldn't burn quota).
_QUOTA_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def _bearer_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    tok = auth[7:].strip()
    return tok or None


class _SlidingWindowLimiter:
    """Per-key sliding-window counter (timestamps in a deque, evicted past the window).

    `check(key, limit)` returns (allowed, retry_after_seconds). Bounded memory: idle keys are
    swept when the key table grows large. Thread-safe.
    """

    def __init__(self, now: Callable[[], float] = time.time, max_keys: int = 50_000):
        self.now = now
        self.max_keys = max_keys
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def check(self, key: str, limit: int) -> tuple[bool, float]:
        limit = max(1, int(limit))
        with self._lock:
            t = self.now()
            cutoff = t - _WINDOW_SECONDS
            dq = self._hits.get(key)
            if dq is None:
                dq = deque()
                self._hits[key] = dq
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= limit:
                # Retry-After = when the oldest in-window hit ages out (ceil to whole seconds, >=1).
                retry = max(1.0, (dq[0] + _WINDOW_SECONDS) - t)
                return False, retry
            dq.append(t)
            if len(self._hits) > self.max_keys:  # bounded: drop fully-aged keys wholesale
                self._hits = {k: d for k, d in self._hits.items() if d and d[-1] > cutoff}
            return True, 0.0


class PlanResolver:
    """tenant_id -> plan label, with a short in-process TTL cache. The lookup is injected
    (`fetch`); with no fetch every tenant resolves to None (-> the most generous tier via
    config.normalize_plan). Resolution errors are swallowed to None (fail OPEN — never throttle
    harder on a transient store error)."""

    def __init__(self, fetch: Callable[[str], str | None] | None = None, *,
                 ttl_seconds: float = 300.0, now: Callable[[], float] = time.time):
        self._fetch = fetch
        self._ttl = ttl_seconds
        self._now = now
        self._cache: dict[str, tuple[float, str | None]] = {}
        self._lock = threading.Lock()

    def plan(self, tenant_id: str) -> str | None:
        key = str(tenant_id)
        t = self._now()
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None and hit[0] > t:
                return hit[1]
        value: str | None = None
        if self._fetch is not None:
            try:
                value = self._fetch(key)
            except Exception:  # noqa: BLE001 — fail open to None (most generous tier)
                log.warning("plan resolution failed for tenant=%s (defaulting generous)", key,
                            extra={"event": "plan_resolution_failed"})
                value = None
        with self._lock:
            self._cache[key] = (t + self._ttl, value)
        return value


class TenantLimitsMiddleware(BaseHTTPMiddleware):
    """Rate-limit + quota gate, keyed on the verified tenant claim.

    `verifier` is the SAME JwtVerifier the app's auth dependency uses — the middleware verifies the
    bearer to read `custom:tenant_id` and does nothing else with it (no header/body trust). An
    absent/invalid token, or a verified token with no tenant, means "no tenant to key on" -> the
    request passes through and the route's auth dependency handles it (the limiter never converts a
    would-be 401 into a 429).
    """

    def __init__(self, app, *, verifier: Any, usage_store: Any = None,
                 plan_resolver: PlanResolver | None = None,
                 limiter: _SlidingWindowLimiter | None = None,
                 exempt_prefixes: Iterable[str] = EXEMPT_PREFIXES):
        super().__init__(app)
        self._verifier = verifier
        self._usage = usage_store
        self._plans = plan_resolver or PlanResolver()
        self._limiter = limiter or _SlidingWindowLimiter()
        self._exempt = tuple(exempt_prefixes)

    def _tenant_of(self, request: Request) -> str | None:
        token = _bearer_token(request)
        if not token:
            return None
        try:
            claims = self._verifier.verify(token)
        except Exception:  # noqa: BLE001 — invalid token: no tenant; let the route 401
            return None
        tid = claims.get("custom:tenant_id") if isinstance(claims, dict) else None
        return str(tid) if tid else None

    def _is_exempt(self, path: str) -> bool:
        return any(path == p or path.startswith(p + "/") or path == p for p in self._exempt)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if self._is_exempt(path):
            return await call_next(request)

        tenant_id = self._tenant_of(request)
        if tenant_id is None:
            # No tenant to key on (unauth/invalid/no-tenant) — pass through; the route auth gates.
            return await call_next(request)

        plan = normalize_plan(self._plans.plan(tenant_id))

        # 1) Rate limit (sliding window per tenant).
        allowed, retry_after = self._limiter.check(tenant_id, rate_limit_per_minute(plan))
        if not allowed:
            secs = int(retry_after) if retry_after == int(retry_after) else int(retry_after) + 1
            return JSONResponse(
                {"detail": "rate limit exceeded", "plan": plan, "retry_after": secs},
                status_code=429,
                headers={"Retry-After": str(max(1, secs))},
            )

        # 2) Monthly quota (only for metered, mutating requests). Counter errors NEVER block.
        cap = monthly_quota(plan)
        if cap is not None and self._usage is not None and request.method in _QUOTA_METHODS:
            try:
                total = self._usage.bump(tenant_id, "messages")
            except Exception:  # noqa: BLE001 — usage accounting must not take the API down
                log.warning("usage bump failed for tenant=%s (request allowed)", tenant_id,
                            extra={"event": "usage_bump_failed"})
                total = None
            if total is not None and total > cap and quota_enforcement() == "block":
                return JSONResponse(
                    {"detail": "monthly usage quota exceeded", "plan": plan,
                     "used": total, "quota": cap},
                    status_code=429,
                    headers={"Retry-After": "3600"},
                )

        return await call_next(request)
