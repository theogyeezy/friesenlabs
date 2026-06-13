"""OAuth "connect with login" helpers for connectors — HubSpot + GoHighLevel +
Salesforce + Microsoft 365 + Google + Pipedrive.

This is the small, dependency-free core of the OAuth flow. It changes only WHAT
fills a tenant's vault slot (`uplift/{tenant_id}/{source}`): instead of a pasted
private-app token (a bare string), the slot holds a JSON envelope of
{access_token, refresh_token, expires_at, token_type:"oauth"} that the connector
can refresh on expiry. The vault mechanics (per-tenant ref, SecretWriter,
INTEGRATIONS_REAL_SECRETS gating) are REUSED unchanged.

Four things live here, none of which touch the network at import time:
  1. A provider REGISTRY (:data:`PROVIDERS`) — per-provider authorize/token URLs,
     scopes, and the Secrets Manager *reference names* for the app's client
     credentials (`uplift/oauth/hubspot/client_id` + `.../client_secret`). The
     client id/secret VALUES are resolved by the caller via the existing
     SecretProvider seam — never hardcoded.
  2. SIGNED-STATE codec (:func:`sign_state` / :func:`verify_state`) — the `state`
     query param is an HMAC-SHA256-signed envelope carrying the tenant_id + a
     nonce + an issued-at timestamp (+ a PKCE code_verifier for providers that
     require PKCE — see below). This is the ONLY tenant binding the callback has
     (a top-level provider redirect carries no JWT), and it is CSRF defence:
     a tampered or expired state is rejected. THE TRUST RULE still holds — the
     tenant_id the callback acts on came from a value WE signed, not from the
     browser.
  3. Token EXCHANGE + REFRESH (:func:`exchange_code` / :func:`refresh_access_token`)
     over the provider token endpoint. HTTP goes through the module-level
     :func:`post_form` (stdlib urllib, lazy) so tests monkeypatch one seam and
     make ZERO live calls. Token VALUES are never logged.
  4. PKCE (:func:`generate_pkce_pair`) for providers that require it
     (`provider.pkce` — GoHighLevel/LeadConnector + Salesforce). The `/start` route
     generates a code_verifier, sends only the S256 code_challenge to the provider,
     and carries the verifier INSIDE the signed `state` (the callback is
     unauthenticated, so the signed state is the only safe place to stash it). The
     verifier rides the token exchange. GoHighLevel's token response also carries
     locationId/companyId (the location the user chose at `chooselocation`) and
     Salesforce's carries `instance_url` (the tenant's per-org API host); those are
     persisted in the vault envelope so the connector can make org/location-level
     API calls without a second round trip.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
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
    # PKCE required? (GoHighLevel/LeadConnector require it.) When True the /start
    # route generates a code_verifier+challenge, sends the S256 challenge to the
    # provider, and the verifier rides the signed state into the token exchange.
    pkce: bool = False
    # Extra fields merged into BOTH the authorization_code and refresh_token POST
    # bodies (e.g. GoHighLevel's `user_type=Location`, which selects a
    # location-scoped token). Tuple-of-pairs so the dataclass stays hashable/frozen.
    token_extra: tuple[tuple[str, str], ...] = ()
    # Extra params merged into the AUTHORIZE URL query (not the token POST) — e.g.
    # Google's `access_type=offline` + `prompt=consent`, which are what make Google
    # ISSUE a refresh_token (without them the first exchange returns access-only and
    # the connector can never refresh). Tuple-of-pairs to stay hashable/frozen.
    authorize_extra: tuple[tuple[str, str], ...] = ()

    @property
    def scope_str(self) -> str:
        # HubSpot/GoHighLevel (and the OAuth 2.0 spec) want space-delimited scopes.
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
    "salesforce": OAuthProvider(
        name="salesforce",
        # Salesforce web-server (authorization_code) flow. login.salesforce.com is
        # the production login host; sandboxes use test.salesforce.com (a per-tenant
        # override is a follow-up — see the connector module docstring).
        authorize_url="https://login.salesforce.com/services/oauth2/authorize",
        token_url="https://login.salesforce.com/services/oauth2/token",
        # `api` grants REST/SOQL access; `refresh_token` is required for Salesforce
        # to ISSUE a refresh token (offline access). Read-only by design — Uplift
        # never writes back. (`api` is broad; a narrower per-object scope set is not
        # offered by Salesforce — object access is governed by the connected app's
        # profile/permission-set instead.)
        scopes=("api", "refresh_token"),
        client_id_ref="uplift/oauth/salesforce/client_id",
        client_secret_ref="uplift/oauth/salesforce/client_secret",
        # Salesforce supports (and we require) PKCE S256 on the web-server flow.
        pkce=True,
    ),
    "gohighlevel": OAuthProvider(
        name="gohighlevel",
        # The marketplace "chooselocation" screen lets the agency owner pick the
        # location (sub-account) to connect; the resulting code exchanges to a
        # LOCATION-scoped token whose response carries locationId/companyId.
        authorize_url="https://marketplace.gohighlevel.com/oauth/chooselocation",
        # LeadConnector is GoHighLevel's API host (services.leadconnectorhq.com).
        token_url="https://services.leadconnectorhq.com/oauth/token",
        # Read-only scopes for the FULL extract — every object type the connector pulls
        # (contacts/opportunities/conversations/calendars/products + custom objects +
        # custom fields), plus locations.readonly so a location-scoped token can resolve
        # its own location. Read-only by design — Uplift never writes back. These MUST
        # match the scopes ticked on the GHL Marketplace App or the token 403s on the
        # broader objects (the same failure a too-narrow Private Integration Token gives).
        scopes=(
            "contacts.readonly",
            "opportunities.readonly",
            "conversations.readonly",
            "conversations/message.readonly",
            "calendars.readonly",
            "calendars/events.readonly",
            "products.readonly",
            "objects/schema.readonly",
            "objects/record.readonly",
            "locations.readonly",
            "locations/customFields.readonly",
            "locations/customValues.readonly",
        ),
        client_id_ref="uplift/oauth/gohighlevel/client_id",
        client_secret_ref="uplift/oauth/gohighlevel/client_secret",
        # GoHighLevel requires PKCE on the authorization-code grant.
        pkce=True,
        # `user_type=Location` selects a location-scoped token (vs Company).
        # # VERIFY on first live connect against the LeadConnector token endpoint.
        token_extra=(("user_type", "Location"),),
    ),
    "pipedrive": OAuthProvider(
        name="pipedrive",
        # Pipedrive marketplace authorization_code flow. The token response carries
        # `api_domain` — the tenant's PER-COMPANY API base host (e.g.
        # https://yourco.pipedrive.com) — which EVERY subsequent API call uses as its
        # base; it is persisted in the vault envelope (see oauth_secret_value).
        authorize_url="https://oauth.pipedrive.com/oauth/authorize",
        token_url="https://oauth.pipedrive.com/oauth/token",
        # Read-only scopes for what the connector pulls — persons+organizations ride
        # `contacts:read`, deals `deals:read`, activities `activities:read`, plus the
        # `base` scope every Pipedrive app needs. Read-only by design — Uplift never
        # writes back. # VERIFY scope names against the app's settings on first connect.
        scopes=(
            "base",
            "contacts:read",
            "deals:read",
            "activities:read",
        ),
        client_id_ref="uplift/oauth/pipedrive/client_id",
        client_secret_ref="uplift/oauth/pipedrive/client_secret",
        # Pipedrive supports (and we require) PKCE S256 on the authorization-code flow.
        pkce=True,
    ),
    "microsoft": OAuthProvider(
        name="microsoft",
        # The MULTI-TENANT (`/common`) v2.0 endpoints: any work/school (Azure AD)
        # OR personal Microsoft account can consent, and the resulting token is
        # scoped to whichever tenant the user signed in from. (A single-tenant app
        # would pin one Azure AD tenant in the path; `/common` keeps Uplift's app
        # usable by every customer's M365 tenant.)
        authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        # Read-only Microsoft Graph scopes for what the connector pulls — mail,
        # calendar, contacts, and the signed-in user's profile. `offline_access` is
        # REQUIRED for Graph to return a refresh_token (without it the token set is
        # access-only and the connector cannot refresh). Read-only by design —
        # Uplift never writes back to M365.
        scopes=(
            "Mail.Read",
            "Calendars.Read",
            "Contacts.Read",
            "offline_access",
            "User.Read",
        ),
        client_id_ref="uplift/oauth/microsoft/client_id",
        client_secret_ref="uplift/oauth/microsoft/client_secret",
        # Microsoft Identity Platform supports (and recommends) PKCE on the
        # authorization-code grant even for confidential clients — we send the
        # S256 challenge at /authorize and the verifier at the token exchange.
        pkce=True,
    ),
    "google": OAuthProvider(
        name="google",
        # Google's standard web-server (authorization_code) OAuth 2.0 endpoints.
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        # Read-only scopes for what the connector pulls — Calendar events and
        # Contacts (People API). Gmail is DEFERRED (its scopes are "restricted" and
        # require Google CASA security assessment — out of scope here). Calendar/
        # Contacts are "sensitive" scopes: they need OAuth-consent-screen
        # verification, NOT CASA. Read-only by design — Uplift never writes back.
        scopes=(
            "https://www.googleapis.com/auth/calendar.readonly",
            "https://www.googleapis.com/auth/contacts.readonly",
        ),
        client_id_ref="uplift/oauth/google/client_id",
        client_secret_ref="uplift/oauth/google/client_secret",
        # Google supports (and we require) PKCE S256 on the web-server flow.
        pkce=True,
        # `access_type=offline` + `prompt=consent` are REQUIRED for Google to return
        # a refresh_token: offline asks for one, and consent forces the consent
        # screen again so Google RE-ISSUES it (Google only returns a refresh_token on
        # the FIRST authorization for a user unless consent is forced). Without these
        # the first exchange is access-only and the connector could never refresh.
        authorize_extra=(("access_type", "offline"), ("prompt", "consent")),
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


def sign_state(tenant_id: str, secret: str, *, nonce: str, issued_at: int | None = None,
               code_verifier: str | None = None) -> str:
    """Return an HMAC-signed, URL-safe `state` value binding `tenant_id`.

    Layout: ``<b64url(payload_json)>.<b64url(hmac_sha256)>``. The payload carries
    the tenant_id, a caller-supplied nonce (single-use entropy), and an issued-at
    epoch second so :func:`verify_state` can enforce a max age. `secret` is the
    resolved HMAC signing-secret VALUE (OAUTH_STATE_SECRET), never a ref.

    For PKCE providers, `code_verifier` is carried (signed) under the `cv` key:
    the callback is unauthenticated, so the signed state is the only tamper-proof
    place to stash the verifier between /start and /callback. It is never sent to
    the provider's authorize endpoint (only the S256 challenge is).
    """
    if not secret:
        raise StateError("no state signing secret configured")
    payload = {"t": tenant_id, "n": nonce, "ts": int(issued_at if issued_at is not None else time.time())}
    if code_verifier:
        payload["cv"] = code_verifier
    body = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = _b64url(hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_state_payload(state: str, secret: str, *, max_age_s: int = STATE_MAX_AGE_S,
                         now: int | None = None) -> dict:
    """Verify a signed `state` and return its full validated payload dict.

    Keys: ``t`` (tenant_id), ``ts`` (issued-at), ``n`` (nonce), and — for PKCE
    providers — ``cv`` (the code_verifier). Raises :class:`StateError` on any tamper
    (bad shape, bad signature) or when the state is older than `max_age_s`. The
    signature is checked with a constant-time compare BEFORE the payload is trusted,
    so a forged tenant_id (or verifier) never escapes.
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
    if not isinstance(payload, dict):
        raise StateError("incomplete state payload")
    tenant_id = payload.get("t")
    ts = payload.get("ts")
    if not tenant_id or not isinstance(ts, int):
        raise StateError("incomplete state payload")
    current = int(now if now is not None else time.time())
    if current - ts > max_age_s:
        raise StateError("state expired")
    return payload


def verify_state(state: str, secret: str, *, max_age_s: int = STATE_MAX_AGE_S,
                 now: int | None = None) -> str:
    """Verify a signed `state` and return the bound tenant_id (thin wrapper over
    :func:`verify_state_payload` for callers that only need the tenant)."""
    return verify_state_payload(state, secret, max_age_s=max_age_s, now=now)["t"]


# --------------------------------------------------------------------------- #
# PKCE (RFC 7636) — required by providers with `provider.pkce` (GoHighLevel).
# --------------------------------------------------------------------------- #
def generate_pkce_pair() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` for the S256 PKCE method.

    The verifier is high-entropy URL-safe text (RFC 7636 allows 43-128 chars from
    the unreserved set, which `token_urlsafe` satisfies); the challenge is
    ``b64url(sha256(verifier))`` with no padding. The verifier is carried (signed)
    in `state`; only the challenge is sent to the provider's authorize endpoint.
    """
    verifier = secrets.token_urlsafe(64)  # ~86 chars, within the 43-128 bound
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


# --------------------------------------------------------------------------- #
# Vault envelope — the OAuth-shaped secret value.
# --------------------------------------------------------------------------- #
TOKEN_TYPE = "oauth"


def oauth_secret_value(*, access_token: str, refresh_token: str, expires_at: int,
                       location_id: str | None = None, company_id: str | None = None,
                       instance_url: str | None = None, api_domain: str | None = None) -> str:
    """Serialize the OAuth token set into the JSON string stored in the vault slot.

    `token_type:"oauth"` is the discriminator the connector uses to tell an OAuth
    envelope apart from a legacy pasted bare token (a plain string). For
    GoHighLevel/LeadConnector the token response also carries the chosen
    `location_id`/`company_id`, Salesforce carries `instance_url` (the tenant's
    per-org API host), and Pipedrive carries `api_domain` (the tenant's per-company
    API base host — every subsequent API call uses it as the base host); they are
    persisted (when present) so the connector can make org/location/company-level
    API calls without a second round trip. HubSpot passes none of them, so its
    envelope is byte-for-byte unchanged.
    """
    obj = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": int(expires_at),
        "token_type": TOKEN_TYPE,
    }
    if location_id:
        obj["location_id"] = str(location_id)
    if company_id:
        obj["company_id"] = str(company_id)
    if instance_url:
        obj["instance_url"] = str(instance_url)
    if api_domain:
        obj["api_domain"] = str(api_domain)
    return json.dumps(obj, separators=(",", ":"))


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
    out = {"access_token": access, "refresh_token": refresh,
           "expires_at": expires_at, "token_type": TOKEN_TYPE}
    # GoHighLevel/LeadConnector return the chosen location (and its company) on
    # both exchange and refresh. Capture them (camelCase per the LeadConnector API,
    # snake_case tolerated) so the caller can persist them in the vault envelope.
    # Providers that don't send these (HubSpot) leave `out` unchanged.
    location_id = resp.get("locationId") or resp.get("location_id")
    company_id = resp.get("companyId") or resp.get("company_id")
    if location_id:
        out["location_id"] = str(location_id)
    if company_id:
        out["company_id"] = str(company_id)
    # Salesforce returns `instance_url` (the tenant's per-org API host) on BOTH the
    # code exchange and the refresh response — capture it so every subsequent REST/
    # SOQL call targets the right org host. Providers that don't send it leave
    # `out` unchanged.
    instance_url = resp.get("instance_url") or resp.get("instanceUrl")
    if instance_url:
        out["instance_url"] = str(instance_url)
    # Pipedrive returns `api_domain` (the tenant's per-company API base host) on BOTH
    # the code exchange and the refresh response — capture it so every subsequent API
    # call targets the right company host. Providers that don't send it leave `out`
    # unchanged.
    api_domain = resp.get("api_domain") or resp.get("apiDomain")
    if api_domain:
        out["api_domain"] = str(api_domain)
    return out


def exchange_code(provider: OAuthProvider, *, code: str, redirect_uri: str,
                  client_id: str, client_secret: str,
                  code_verifier: str | None = None, now: int | None = None) -> dict:
    """Exchange an authorization `code` for a token set (vault envelope dict).

    For PKCE providers (`provider.pkce`) the `code_verifier` is sent so the
    provider can match the S256 challenge from the authorize step. `provider.token_extra`
    fields (e.g. GoHighLevel's `user_type=Location`) are merged into the POST body."""
    fields = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    if code_verifier:
        fields["code_verifier"] = code_verifier
    fields.update(dict(provider.token_extra))
    resp = post_form(provider.token_url, fields)
    return _tokens_from_response(resp, now=now)


def refresh_access_token(provider: OAuthProvider, *, refresh_token: str,
                         client_id: str, client_secret: str, now: int | None = None) -> dict:
    """Exchange a `refresh_token` for a fresh access token (vault envelope dict).
    Preserves the existing refresh_token when the provider doesn't return a new one.
    `provider.token_extra` fields are merged into the POST body (the LeadConnector
    refresh grant takes the same `user_type` as the code exchange)."""
    fields = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
    fields.update(dict(provider.token_extra))
    resp = post_form(provider.token_url, fields)
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
                        state: str, code_challenge: str | None = None) -> str:
    """Assemble the provider authorize URL the browser is sent to (GET).

    For PKCE providers the caller passes the S256 `code_challenge` (the verifier
    itself stays signed inside `state`); we add `code_challenge` +
    `code_challenge_method=S256` to the query."""
    import urllib.parse  # noqa: PLC0415 — lazy, consistent with the rest of this module

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": provider.scope_str,
        "response_type": "code",
        "state": state,
    }
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    # Provider-specific authorize-query extras (e.g. Google's access_type=offline +
    # prompt=consent — what makes Google issue a refresh_token). Empty for providers
    # that don't need them, so their authorize URL is byte-for-byte unchanged.
    params.update(dict(provider.authorize_extra))
    return f"{provider.authorize_url}?{urllib.parse.urlencode(params)}"
