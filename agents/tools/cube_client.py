"""Tenant-scoped Cube REST client — THE TRUST RULE's Cube leg (TODO AI/P1).

Cube's `checkAuth` (semantic/security.js) accepts only a signed HS256 JWT whose payload carries the
tenant, and `queryRewrite` force-filters every cube to that tenant. This client is the OTHER half:
it mints a fresh, short-lived JWT **per request**, embedding exactly one `tenant_id` — the one the
caller passes from the verified Cognito claim (the session metadata the API stamped). Tenant
identity NEVER comes from env, headers, or request bodies here (CLAUDE.md hard constraint #6).

Security/ops posture:
- The HS256 signing secret is INJECTED at construction. `cube_client_from_env()` reads it from the
  NEW deliberate env name `CUBEJS_API_SECRET_VALUE` (shared/config.py) — the resolved VALUE of the
  same secret the Cube service reads as `CUBEJS_API_SECRET` — never the Secrets Manager reference,
  and never an env name the live API task already injects (deploy invariance).
- Import-safe and lazy: stdlib-only crypto (hmac/hashlib — no PyJWT dependency); `urllib.request`
  is imported inside the default transport, never at module import.
- Unconfigured degradation: without BOTH endpoint and secret, `load()` returns the
  `{"status": "unconfigured", ...}` result and `members()` returns `[]` — no network, no raise, a
  boot without the new env stays byte-identical.
- Tokens are minted per request with a short expiry (default 60s): a leaked token is near-useless,
  and no token ever outlives the request that needed it.

Wiring: `query_cube` / `build_view` accept a CubeClient via `ToolContext.cube` or their constructor
seam; `cube_client_from_env()` is the factory for the later api/asgi.py + conv/ + worker/ wiring
(NOT edited in this change — those files are owned by other lanes this cycle).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from typing import Any, Callable

from shared.config import ENV_CUBE_ENDPOINT, ENV_CUBEJS_API_SECRET_VALUE

# Short per-request token life: the JWT exists only to carry the verified tenant to Cube.
DEFAULT_TTL_S = 60
DEFAULT_TIMEOUT_S = 30.0
# Cube can answer 200 + {"error": "Continue wait"} while a query warms; bounded retries only.
CONTINUE_WAIT_RETRIES = 3
CONTINUE_WAIT_SLEEP_S = 1.0

# Defense in depth on the tenant parameter (same charset as ml/registry.py): the verified claim is
# a UUID, but reject anything that couldn't be a sane id BEFORE it is signed into a token.
_TENANT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

# transport(url, body_or_None, headers, timeout_s) -> (http_status, response_bytes)
Transport = Callable[[str, bytes | None, dict, float], tuple[int, bytes]]


class CubeTokenError(ValueError):
    """A Cube JWT failed verification (bad signature/alg/expiry) or could not be minted."""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(part: str) -> bytes:
    pad = "=" * (-len(part) % 4)
    return base64.urlsafe_b64decode(part + pad)


def _assert_tenant(tenant_id: Any) -> str:
    """THE TRUST RULE's local guard: the tenant must be a sane, non-empty id string.

    Callers pass the verified-claim tenant; this just guarantees junk/forged shapes (None, empty,
    whitespace, structured values) can never be signed into a token."""
    if not isinstance(tenant_id, str) or not _TENANT_ID_RE.match(tenant_id):
        raise CubeTokenError("tenant_id must come from the verified claim (non-empty id string)")
    return tenant_id


def mint_cube_jwt(
    secret: bytes | str,
    tenant_id: str,
    *,
    ttl_s: int = DEFAULT_TTL_S,
    now: Callable[[], float] | None = None,
) -> str:
    """Mint a per-request HS256 Cube JWT carrying EXACTLY the caller's tenant, with a short expiry.

    The payload is `{tenant_id, iat, exp}` and nothing else — there is nothing to forge and nothing
    to leak. `now` is injectable for tests only."""
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    if not key:
        raise CubeTokenError("no Cube signing secret configured (CUBEJS_API_SECRET_VALUE)")
    tenant = _assert_tenant(tenant_id)
    ts = int((now or time.time)())
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(
        json.dumps(
            {"tenant_id": tenant, "iat": ts, "exp": ts + int(ttl_s)}, separators=(",", ":")
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode("ascii")
    sig = _b64url(hmac.new(key, signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def decode_verified(
    token: str,
    secret: bytes | str,
    *,
    now: Callable[[], float] | None = None,
) -> dict:
    """Verify an HS256 Cube JWT and return its payload. Raises CubeTokenError on ANY defect:
    unsigned/malformed shape, non-HS256 alg (incl. `none`), bad signature, missing or past expiry.

    This is the Python mirror of `decodeVerifiedJwt` in semantic/security.js — both sides enforce
    the same contract so a token only one side would accept cannot exist."""
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    if not key:
        raise CubeTokenError("no Cube signing secret configured")
    if not isinstance(token, str):
        raise CubeTokenError("no token")
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        raise CubeTokenError("unsigned or malformed token")
    head_b64, payload_b64, sig_b64 = parts
    try:
        header = json.loads(_b64url_decode(head_b64))
        payload = json.loads(_b64url_decode(payload_b64))
        given_sig = _b64url_decode(sig_b64)
    except (ValueError, TypeError) as exc:
        raise CubeTokenError(f"malformed token: {exc}") from exc
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        raise CubeTokenError("bad alg (only HS256 is accepted)")
    expected = hmac.new(key, f"{head_b64}.{payload_b64}".encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(given_sig, expected):
        raise CubeTokenError("bad signature")
    if not isinstance(payload, dict) or not isinstance(payload.get("exp"), (int, float)):
        raise CubeTokenError("no expiry")
    if payload["exp"] <= (now or time.time)():
        raise CubeTokenError("expired token")
    return payload


def _urllib_transport(url: str, body: bytes | None, headers: dict, timeout_s: float) -> tuple[int, bytes]:
    """Default HTTP transport. urllib is imported HERE so module import stays network-free."""
    import urllib.error  # noqa: PLC0415 — lazy, import-safe
    import urllib.request  # noqa: PLC0415 — lazy, import-safe

    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 — internal Cube URL
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:  # non-2xx still carries a useful Cube error body
        return exc.code, exc.read()


class CubeClient:
    """Governed-metrics client: per-request tenant JWT over Cube REST (`/cubejs-api/v1`).

    Matches the `ctx.cube` protocol the tools already use:
      - `load(tenant_id=..., query=...)`  -> {"status": "ok"|"unconfigured"|"error", "rows": [...]}
      - `members(tenant_id=...)`          -> ["Deals.count", ...]  ([] when unconfigured/erroring)

    Deliberately NO `set_tenant` method: the tenant is a per-call parameter from the verified
    claim, never shared mutable state on the client (no cross-call races, nothing to forget).
    """

    def __init__(
        self,
        endpoint: str | None = None,
        secret: bytes | str | None = None,
        *,
        ttl_s: int = DEFAULT_TTL_S,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        transport: Transport | None = None,
        now: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._endpoint = (endpoint or "").rstrip("/")
        self._secret = secret.encode("utf-8") if isinstance(secret, str) else (secret or b"")
        self._ttl_s = ttl_s
        self._timeout_s = timeout_s
        self._transport = transport or _urllib_transport
        self._now = now
        self._sleep = sleep or time.sleep

    @property
    def configured(self) -> bool:
        """True only when a query can actually be signed AND sent."""
        return bool(self._endpoint and self._secret)

    def mint_jwt(self, tenant_id: str) -> str:
        """Mint this request's token from the caller's verified-claim tenant. Raises unconfigured."""
        return mint_cube_jwt(self._secret, tenant_id, ttl_s=self._ttl_s, now=self._now)

    # ------------------------------------------------------------------ ctx.cube protocol
    def load(self, *, tenant_id: str, query: dict) -> dict:
        """Run a Cube query as `tenant_id`. The tenant filter itself is enforced server-side by
        queryRewrite — this client's job is to deliver the verified tenant inside a signed token."""
        _assert_tenant(tenant_id)
        if not self.configured:
            return {"status": "unconfigured", "rows": [],
                    "detail": "CUBE_ENDPOINT and CUBEJS_API_SECRET_VALUE are required"}
        body = json.dumps({"query": query}).encode("utf-8")
        # VERIFY: REST shape (`POST /cubejs-api/v1/load`, raw-JWT Authorization, `data` rows,
        # 200+"Continue wait" while warming) is per Cube docs — confirm against the deployed
        # service (infra/modules/cube) before first live use.
        for attempt in range(CONTINUE_WAIT_RETRIES + 1):
            status, raw = self._request("/cubejs-api/v1/load", tenant_id=tenant_id, body=body)
            try:
                parsed = json.loads(raw or b"{}")
            except ValueError:
                return {"status": "error", "rows": [], "error": f"non-JSON Cube response (HTTP {status})"}
            if status == 200 and isinstance(parsed, dict) and parsed.get("error") == "Continue wait":
                if attempt < CONTINUE_WAIT_RETRIES:
                    self._sleep(CONTINUE_WAIT_SLEEP_S)
                    continue
                return {"status": "error", "rows": [], "error": "Cube still warming (Continue wait)"}
            if status != 200:
                err = parsed.get("error") if isinstance(parsed, dict) else None
                return {"status": "error", "rows": [], "error": err or f"HTTP {status}"}
            rows = parsed.get("data") if isinstance(parsed, dict) else None
            return {"status": "ok", "rows": rows if isinstance(rows, list) else []}
        return {"status": "error", "rows": [], "error": "unreachable"}  # pragma: no cover

    def members(self, *, tenant_id: str) -> list[str]:
        """List the governed measure/dimension names visible to this tenant (build_view's catalog).
        Degrades to [] when unconfigured or erroring — build_view then has no members to offer,
        which fails CLOSED (a spec can never reference members nobody verified)."""
        _assert_tenant(tenant_id)
        if not self.configured:
            return []
        status, raw = self._request("/cubejs-api/v1/meta", tenant_id=tenant_id, body=None)
        if status != 200:
            return []
        try:
            meta = json.loads(raw or b"{}")
        except ValueError:
            return []
        names: list[str] = []
        for cube in meta.get("cubes", []) if isinstance(meta, dict) else []:
            for kind in ("measures", "dimensions"):
                for member in cube.get(kind, []) or []:
                    name = member.get("name") if isinstance(member, dict) else None
                    if isinstance(name, str):
                        names.append(name)
        return names

    # ------------------------------------------------------------------ internals
    def _request(self, path: str, *, tenant_id: str, body: bytes | None) -> tuple[int, bytes]:
        token = self.mint_jwt(tenant_id)  # fresh, short-lived, this tenant only
        headers = {"Authorization": token, "Content-Type": "application/json"}
        try:
            return self._transport(f"{self._endpoint}{path}", body, headers, self._timeout_s)
        except OSError as exc:  # connection refused/reset/DNS — degrade, never crash the tool call
            return 0, json.dumps({"error": f"cube unreachable: {exc}"}).encode("utf-8")


def cube_client_from_env() -> CubeClient | None:
    """Factory for the later api/asgi.py + conv/session + worker wiring (other lanes own those
    files this cycle). Reads `CUBE_ENDPOINT` + the NEW `CUBEJS_API_SECRET_VALUE`:

    - both unset  -> None (today's behavior: ToolContext.cube stays None, boots byte-identical)
    - any set     -> a CubeClient; with only one piece present it degrades per call with the
                     visible 'unconfigured' result instead of silently vanishing (misconfig shows
                     up in tool output, not as a missing client).
    Real network behavior requires BOTH — i.e. it is gated on the new deliberate secret name,
    never on env the live API task already injects."""
    endpoint = os.environ.get(ENV_CUBE_ENDPOINT, "")
    secret = os.environ.get(ENV_CUBEJS_API_SECRET_VALUE, "")
    if not endpoint and not secret:
        return None
    return CubeClient(endpoint=endpoint, secret=secret)
