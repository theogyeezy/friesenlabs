"""Unit: the OAuth core (ingest/connectors/oauth.py) — signed state, envelope
codec, token exchange/refresh. FIXTURES only: post_form is monkeypatched, so
ZERO live HubSpot calls.

Covers:
  * sign_state -> verify_state round-trips and recovers the tenant_id
  * a TAMPERED state (flipped signature / mutated payload) is REJECTED
  * an EXPIRED state is rejected (max_age enforced)
  * the OAuth envelope round-trips; a bare token parses as None (back-compat)
  * is_expired honors the skew + an unknown expiry
  * exchange_code / refresh_access_token shape the vault envelope; refresh
    preserves the old refresh_token when the provider omits a new one
  * a non-2xx token endpoint surfaces TokenExchangeError (no token material)
"""
import pytest

from ingest.connectors import oauth

SECRET = "test-hmac-signing-secret-not-a-real-one"
PROVIDER = oauth.get_provider("hubspot")


# --------------------------------------------------------------------------- #
# signed state
# --------------------------------------------------------------------------- #
def test_sign_verify_state_roundtrip_recovers_tenant():
    state = oauth.sign_state("tenant-A", SECRET, nonce="n1", issued_at=1000)
    assert oauth.verify_state(state, SECRET, now=1010) == "tenant-A"


def test_tampered_signature_rejected():
    state = oauth.sign_state("tenant-A", SECRET, nonce="n1", issued_at=1000)
    body, sig = state.split(".", 1)
    forged = f"{body}.{sig[:-2]}XY"  # mutate the signature
    with pytest.raises(oauth.StateError):
        oauth.verify_state(forged, SECRET, now=1010)


def test_tampered_payload_rejected():
    # Re-sign a different tenant under the WRONG secret, then present it: the
    # signature won't validate under the real secret -> rejected (no tenant swap).
    forged = oauth.sign_state("attacker-tenant", "attacker-secret", nonce="n", issued_at=1000)
    with pytest.raises(oauth.StateError):
        oauth.verify_state(forged, SECRET, now=1010)


def test_expired_state_rejected():
    state = oauth.sign_state("tenant-A", SECRET, nonce="n1", issued_at=1000)
    with pytest.raises(oauth.StateError):
        oauth.verify_state(state, SECRET, max_age_s=600, now=1000 + 601)


def test_malformed_state_rejected():
    for bad in ("", "no-dot", "a.b.c"):
        with pytest.raises(oauth.StateError):
            oauth.verify_state(bad, SECRET)


def test_state_requires_secret():
    with pytest.raises(oauth.StateError):
        oauth.sign_state("t", "", nonce="n")
    with pytest.raises(oauth.StateError):
        oauth.verify_state("a.b", "")


# --------------------------------------------------------------------------- #
# envelope codec + expiry
# --------------------------------------------------------------------------- #
def test_oauth_envelope_roundtrips():
    value = oauth.oauth_secret_value(access_token="AT", refresh_token="RT", expires_at=12345)
    parsed = oauth.parse_oauth_secret(value)
    assert parsed["access_token"] == "AT"
    assert parsed["refresh_token"] == "RT"
    assert parsed["expires_at"] == 12345
    assert parsed["token_type"] == "oauth"


def test_bare_token_parses_as_none():
    # A legacy pasted private-app token is a plain string — not an OAuth envelope.
    assert oauth.parse_oauth_secret("pat-na1-abc123") is None
    assert oauth.parse_oauth_secret("") is None
    # JSON that isn't our envelope shape is also None (defensive).
    assert oauth.parse_oauth_secret('{"token_type":"other"}') is None
    assert oauth.parse_oauth_secret('{"token_type":"oauth"}') is None  # no tokens


def test_is_expired_honors_skew_and_unknown():
    fresh = {"expires_at": 10_000}
    assert oauth.is_expired(fresh, skew_s=120, now=9_000) is False
    assert oauth.is_expired(fresh, skew_s=120, now=9_881) is True   # within skew
    assert oauth.is_expired(fresh, skew_s=120, now=10_500) is True  # past expiry
    # No expires_at -> unknown -> not expired (don't churn refreshes).
    assert oauth.is_expired({"expires_at": 0}, now=99_999) is False
    assert oauth.is_expired({}, now=99_999) is False


# --------------------------------------------------------------------------- #
# token exchange + refresh (post_form monkeypatched — no network)
# --------------------------------------------------------------------------- #
def test_exchange_code_shapes_envelope(monkeypatch):
    captured = {}

    def fake_post(url, fields):
        captured["url"] = url
        captured["fields"] = fields
        return {"access_token": "new-AT", "refresh_token": "new-RT", "expires_in": 1800}

    monkeypatch.setattr(oauth, "post_form", fake_post)
    out = oauth.exchange_code(PROVIDER, code="the-code", redirect_uri="https://api/cb",
                              client_id="CID", client_secret="CSEC", now=1000)
    assert out == {"access_token": "new-AT", "refresh_token": "new-RT",
                   "expires_at": 1000 + 1800, "token_type": "oauth"}
    # Correct grant + the exact provider token URL.
    assert captured["url"] == PROVIDER.token_url
    assert captured["fields"]["grant_type"] == "authorization_code"
    assert captured["fields"]["code"] == "the-code"
    assert captured["fields"]["redirect_uri"] == "https://api/cb"


def test_refresh_preserves_old_refresh_when_omitted(monkeypatch):
    # HubSpot may not return a new refresh_token on refresh — keep the old one.
    monkeypatch.setattr(oauth, "post_form",
                        lambda url, fields: {"access_token": "AT2", "expires_in": 1800})
    out = oauth.refresh_access_token(PROVIDER, refresh_token="OLD-RT",
                                     client_id="CID", client_secret="CSEC", now=2000)
    assert out["access_token"] == "AT2"
    assert out["refresh_token"] == "OLD-RT"
    assert out["expires_at"] == 2000 + 1800


def test_refresh_uses_refresh_grant(monkeypatch):
    captured = {}
    monkeypatch.setattr(oauth, "post_form",
                        lambda url, fields: captured.update(fields)
                        or {"access_token": "AT", "refresh_token": "RT", "expires_in": 100})
    oauth.refresh_access_token(PROVIDER, refresh_token="RT0",
                               client_id="CID", client_secret="CSEC")
    assert captured["grant_type"] == "refresh_token"
    assert captured["refresh_token"] == "RT0"
    assert captured["client_secret"] == "CSEC"


def test_missing_access_token_raises(monkeypatch):
    monkeypatch.setattr(oauth, "post_form", lambda url, fields: {"refresh_token": "RT"})
    with pytest.raises(oauth.TokenExchangeError):
        oauth.exchange_code(PROVIDER, code="c", redirect_uri="r",
                            client_id="i", client_secret="s")


# --------------------------------------------------------------------------- #
# authorize URL + config
# --------------------------------------------------------------------------- #
def test_build_authorize_url_carries_params():
    url = oauth.build_authorize_url(PROVIDER, client_id="CID",
                                    redirect_uri="https://api/cb", state="ST")
    assert url.startswith(PROVIDER.authorize_url + "?")
    assert "client_id=CID" in url
    assert "response_type=code" in url
    assert "state=ST" in url
    # scopes space-joined then url-encoded
    assert "crm.objects.contacts.read" in url


def test_oauth_config_gate_and_redirect_uri():
    cfg = oauth.OAuthConfig()
    assert cfg.configured() is False
    cfg = oauth.OAuthConfig(state_secret="s", redirect_base="https://api.x/")
    assert cfg.configured() is True
    assert cfg.redirect_uri("hubspot") == "https://api.x/integrations/hubspot/oauth/callback"
    assert cfg.return_url() == "/"  # empty app_return_url -> root


def test_post_form_sends_nondefault_user_agent(monkeypatch):
    """The OAuth token endpoint (GHL/LeadConnector) is Cloudflare-fronted and bans urllib's default
    UA (1010 -> 403), breaking exchange/refresh from AWS. post_form must send a named UA."""
    import urllib.request

    from ingest.connectors import oauth

    seen = {}

    class _Resp:
        def read(self):
            return b'{"ok": 1}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen["ua"] = req.get_header("User-agent")
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    oauth.post_form("https://services.leadconnectorhq.com/oauth/token", {"grant_type": "x"})
    assert seen["ua"] and not seen["ua"].lower().startswith("python-")
