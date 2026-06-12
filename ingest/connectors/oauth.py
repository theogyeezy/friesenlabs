"""OAuth "connect with login" helpers for connectors — HubSpot first.

This is the small, dependency-free core of the OAuth flow. It changes only WHAT
fills a tenant's vault slot (`uplift/{tenant_id}/{source}`): instead of a pasted
private-app token (a bare string), the slot holds a JSON envelope of
{access_token, refresh_token, expires_at, token_type:"oauth"} that the connector
can refresh on expiry. The vault mechanics (per-tenant ref, SecretWriter,
INTEGRATIONS_REAL_SECRETS gating) are REUSED unchanged.

Three things live here, none of which touch the network at import time:
  1. A provider REGISTRY (:data:`PROVIDERS`) — per-provider authorize/token URLs,
     scopes, and the Secrets Manager *reference names* for the app's client
     credentials (`uplift/oauth/hubspot/client_id` + `.../client_secret`). The
     client id/secret VALUES are resolved by the caller via the existing
     SecretProvider seam — never hardcoded.
  2. SIGNED-STATE codec (:func:`sign_state` / :func:`verify_state`) — the `state`
     query param is an HMAC-SHA256-signed envelope carrying the tenant_id + a
     nonce + an issued-at timestamp. This is the ONLY tenant binding the callback
     has (a top-level provider redirect carries no JWT), and it is CSRF defence:
     a tampered or expired state is rejected. THE TRUST RULE still holds — the
     tenant_id the callback acts on came from a value WE signed, not from the
     browser.
  3. Token EXCHANGE + REFRESH (:func:`exchange_code` / :func:`refresh_access_token`)
     over the provider token endpoint. HTTP goes through the module-level
     :func:`post_form` (stdlib urllib, lazy) so tests monkeypatch one seam and
     make ZERO live calls. Token VALUES are never logged.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

log = logging.getLogger("ingest.connectors.oauth")

# Refresh an OAuth access token this many seconds BEFORE it actually expires, so
# a sync that starts near the boundary doesn't race the expiry mid-pull.
EXPIRY_SKEW_S = 120
# Default max age of a signed `state` param (CSRF window) — an authorize round
# trip is seconds; 10 minutes is generous and bounds replay.
STATE_MAX_AGE_S = 600


# --------------------------------------------------------------------------- #
# Write seam — structurally identical to api.integrations_routes.SecretWriter,
# redeclared here so ingest/ never imports api/ (the API must boot without
# ingest/, and ingest must not depend on api/). Any object with put_secret works.
# --------------------------------------------------------------------------- #
@runtime_checkable
class SecretWriter(Protocol):
    """Persists a tenant's vaulted credential value by reference name."""

    def put_secret(self, ref: str, value: str) -> None: ...


# --------------------------------------------------------------------------- #
# Provider registry.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class OAuthProvider:
    """Static OAuth config for one source. `client_id_ref`/`client_secret_ref`
    are Secrets Manager NAMES (resolved via the SecretProvider seam), never the
    secret values."""

    name: str
    authorize_url: str
    token_url: str
    scopes: tuple[str, ...]
    client_id_ref: str
    client_secret_ref: str

    @property
    def scope_str(self) -> str:
        # HubSpot (and the OAuth 2.0 spec) want space-delimited scopes.
        return " ".join(self.scopes)


PROVIDERS: dict[str, OAuthProvider] = {
    "hubspot": OAuthProvider(
        name="hubspot",
        authorize_url="https://app.hubspot.com/oauth/authorize",
        token_url="https://api.hubapi.com/oauth/v1/token",
        # Read scopes for the objects the connector pulls (companies/contacts/deals
        # + notes ride the objects scopes), plus the base `oauth` scope HubSpot
        # requires on every app. Read-only by design — Uplift never writes back.
        scopes=(
            "oauth",
            "crm.objects.contacts.read",
            "crm.objects.companies.read",
            "crm.objects.deals.read",
        ),
        client_id_ref="uplift/oauth/hubspot/client_id",
        client_secret_ref="uplift/oauth/hubspot/client_secret",
    ),
}


def get_provider(name: str) -> OAuthProvider | None:
    """The OAuth provider config for `name`, or None when the source has no OAuth
    flow wired (e.g. csv/stripe — they keep the pasted-key path)."""
    return PROVIDERS.get(name)


# --------------------------------------------------------------------------- #
# Signed state (CSRF + tenant binding).
# --------------------------------------------------------------------------- #
def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


class StateError(ValueError):
    """The `state` param is malformed, tampered (bad signature), or expired.

    Distinct from a token-exchange failure so the callback can answer a precise
    403/422 (reject) versus a 502 (provider exchange failed)."""


def sign_state(tenant_id: str, secret: str, *, nonce: str, issued_at: int | None = None) -> str:
    """Return an HMAC-signed, URL-safe `state` value binding `tenant_id`.

    Layout: ``<b64url(payload_json)>.<b64url(hmac_sha256)>``. The payload carries
    the tenant_id, a caller-supplied nonce (single-use entropy), and an issued-at
    epoch second so :func:`verify_state` can enforce a max age. `secret` is the
    resolved HMAC signing-secret VALUE (OAUTH_STATE_SECRET), never a ref.
    """
    if not secret:
        raise StateError("no state signing secret configured")
    payload = {"t": tenant_id, "n": nonce, "ts": int(issued_at if issued_at is not None else time.time())}
    body = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = _b64url(hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_state(state: str, secret: str, *, max_age_s: int = STATE_MAX_AGE_S,
                 now: int | None = None) -> str:
    """Verify a signed `state` and return the bound tenant_id.

    Raises :class:`StateError` on any tamper (bad shape, bad signature) or when the
    state is older than `max_age_s`. The signature is checked with a constant-time
    compare BEFORE the payload is trusted, so a forged tenant_id never escapes.
    """
    if not secret:
        raise StateError("no state signing secret configured")
    if not state or state.count(".") != 1:
        raise StateError("malformed state")
    body, sig = state.split(".", 1)
    expected = _b64url(hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise StateError("bad state signature")
    try:
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise StateError("undecodable state payload") from exc
    tenant_id = payload.get("t")
    ts = payload.get("ts")
    if not tenant_id or not isinstance(ts, int):
        raise StateError("incomplete state payload")
    current = int(now if now is not None else time.time())
    if current - ts > max_age_s:
        raise StateError("state expired")
    return tenant_id


# --------------------------------------------------------------------------- #
# Vault envelope — the OAuth-shaped secret value.
# --------------------------------------------------------------------------- #
TOKEN_TYPE = "oauth"


def oauth_secret_value(*, access_token: str, refresh_token: str, expires_at: int) -> str:
    """Serialize the OAuth token set into the JSON string stored in the vault slot.

    `token_type:"oauth"` is the discriminator the connector uses to tell an OAuth
    envelope apart from a legacy pasted bare token (a plain string).
    """
    return json.dumps(
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": int(expires_at),
            "token_type": TOKEN_TYPE,
        },
        separators=(",", ":"),
    )


def parse_oauth_secret(value: str) -> dict | None:
    """Return the OAuth token dict if `value` is an OAuth envelope, else None.

    None means "treat this as a legacy bare token" (back-compat: a pasted
    private-app token is a plain string and is still a valid bearer token).
    """
    if not value:
        return None
    stripped = value.strip()
    if not stripped.startswith("{"):
        return None
    try:
        obj = json.loads(stripped)
    except ValueError:
        return None
    if not isinstance(obj, dict) or obj.get("token_type") != TOKEN_TYPE:
        return None
    if not obj.get("refresh_token") or not obj.get("access_token"):
        return None
    return obj


def is_expired(secret: dict, *, skew_s: int = EXPIRY_SKEW_S, now: int | None = None) -> bool:
    """Whether the access token is at/within the refresh skew of expiry.

    A missing/zero `expires_at` is treated as "unknown -> not expired" so we don't
    churn a refresh on every run; a real expiry that has passed forces a refresh.
    """
    expires_at = secret.get("expires_at")
    if not expires_at:
        return False
    current = int(now if now is not None else time.time())
    return current >= int(expires_at) - skew_s


# --------------------------------------------------------------------------- #
# Token endpoint — exchange + refresh. HTTP rides post_form (the single test seam).
# --------------------------------------------------------------------------- #
class TokenExchangeError(RuntimeError):
    """The provider token endpoint failed (network, non-2xx, or unparseable).

    Carries no token material; safe to log/surface. The HTTP status (when known)
    is on `.status`.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        self.status = status
        super().__init__(message)


_POST_TIMEOUT_S = 15.0


def post_form(url: str, fields: dict) -> dict:
    """POST application/x-www-form-urlencoded and return the parsed JSON body.

    stdlib urllib, imported lazily (zero import-time network, no new dependency) —
    the SAME zero-dependency stance as HubSpotRestClient. This is the ONE seam
    tests monkeypatch (`oauth.post_form = fake`) so token exchange/refresh never
    hit the live provider. NEVER logs `fields` (it carries the client_secret +
    auth code / refresh token).
    """
    import urllib.error  # noqa: PLC0415 — lazy: no network machinery at import
    import urllib.parse  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_POST_TIMEOUT_S) as resp:  # noqa: S310 — fixed https provider URL
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Do NOT echo the body — a token endpoint error body can include hints we
        # don't want in logs. Status only.
        raise TokenExchangeError(f"token endpoint returned HTTP {exc.code}", status=exc.code) from exc
    except Exception as exc:  # noqa: BLE001 — network/DNS/timeout/parse: uniform, token-free
        raise TokenExchangeError(f"token endpoint call failed: {type(exc).__name__}") from exc


def _tokens_from_response(resp: dict, *, fallback_refresh: str | None = None,
                          now: int | None = None) -> dict:
    """Normalize a token-endpoint JSON body into our vault envelope dict.

    On refresh some providers omit a new refresh_token (the old one stays valid) —
    `fallback_refresh` preserves it. `expires_in` (seconds) becomes an absolute
    `expires_at` epoch second.
    """
    access = resp.get("access_token")
    if not access:
        raise TokenExchangeError("token response missing access_token")
    refresh = resp.get("refresh_token") or fallback_refresh
    if not refresh:
        raise TokenExchangeError("token response missing refresh_token")
    expires_in = resp.get("expires_in")
    current = int(now if now is not None else time.time())
    try:
        expires_at = current + int(expires_in) if expires_in is not None else 0
    except (TypeError, ValueError):
        expires_at = 0
    return {"access_token": access, "refresh_token": refresh,
            "expires_at": expires_at, "token_type": TOKEN_TYPE}


def exchange_code(provider: OAuthProvider, *, code: str, redirect_uri: str,
                  client_id: str, client_secret: str, now: int | None = None) -> dict:
    """Exchange an authorization `code` for a token set (vault envelope dict)."""
    resp = post_form(provider.token_url, {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "code": code,
    })
    return _tokens_from_response(resp, now=now)


def refresh_access_token(provider: OAuthProvider, *, refresh_token: str,
                         client_id: str, client_secret: str, now: int | None = None) -> dict:
    """Exchange a `refresh_token` for a fresh access token (vault envelope dict).
    Preserves the existing refresh_token when the provider doesn't return a new one."""
    resp = post_form(provider.token_url, {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    })
    return _tokens_from_response(resp, fallback_refresh=refresh_token, now=now)


# --------------------------------------------------------------------------- #
# Deployment config for the routes (state secret + redirect base + return URL).
# --------------------------------------------------------------------------- #
@dataclass
class OAuthConfig:
    """Per-deployment OAuth wiring (api/integrations_routes builds this from env).

    `state_secret` is the resolved HMAC value; `redirect_base` the public API base
    the provider redirect_uri is built from; `app_return_url` where the browser is
    sent after the callback. `configured()` is the gate the routes check before
    doing anything real.
    """

    state_secret: str = ""
    redirect_base: str = ""
    app_return_url: str = ""

    def configured(self) -> bool:
        return bool(self.state_secret and self.redirect_base)

    def redirect_uri(self, name: str) -> str:
        base = self.redirect_base.rstrip("/")
        return f"{base}/integrations/{name}/oauth/callback"

    def return_url(self) -> str:
        return self.app_return_url or "/"


def build_authorize_url(provider: OAuthProvider, *, client_id: str, redirect_uri: str,
                        state: str) -> str:
    """Assemble the provider authorize URL the browser is sent to (GET)."""
    import urllib.parse  # noqa: PLC0415 — lazy, consistent with the rest of this module

    query = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": provider.scope_str,
        "response_type": "code",
        "state": state,
    })
    return f"{provider.authorize_url}?{query}"
