"""Unit: Microsoft 365 (Graph) OAuth — provider registration, PKCE, and the
authorization_code / refresh_token exchanges. FIXTURES only: oauth.post_form is
monkeypatched, so token exchange/refresh make ZERO live login.microsoftonline.com
calls.

Covers:
  * the microsoft provider is registered with the multi-tenant /common endpoints,
    PKCE, the right read-only Graph scopes (incl. the REQUIRED offline_access), and
    the Secrets Manager client_id/client_secret refs
  * build_authorize_url emits the scopes + the S256 code_challenge
  * exchange_code sends the PKCE verifier + redirect_uri and (because of
    offline_access) returns a refresh_token captured into the vault envelope
  * refresh_access_token rolls the refresh_token when Microsoft returns a new one,
    and preserves the old one when it doesn't
  * the OAuth envelope round-trips with no location (HubSpot/Microsoft shape)
"""
import hashlib

from ingest.connectors import oauth

SECRET = "ms-test-hmac-signing-secret"
PROVIDER = oauth.get_provider("microsoft")

CID_REF = "uplift/oauth/microsoft/client_id"
CSEC_REF = "uplift/oauth/microsoft/client_secret"


# --------------------------------------------------------------------------- #
# provider registry
# --------------------------------------------------------------------------- #
def test_microsoft_provider_registered():
    p = oauth.get_provider("microsoft")
    assert p is not None
    # MULTI-TENANT /common endpoints (any M365 tenant can connect)
    assert p.authorize_url == "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
    assert p.token_url == "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    assert p.pkce is True
    # read-only Graph scopes the connector pulls
    for scope in ("Mail.Read", "Calendars.Read", "Contacts.Read", "User.Read"):
        assert scope in p.scopes
    # offline_access is REQUIRED for Graph to return a refresh_token
    assert "offline_access" in p.scopes
    assert p.client_id_ref == CID_REF
    assert p.client_secret_ref == CSEC_REF
    # Microsoft needs no token_extra (unlike GoHighLevel's user_type)
    assert p.token_extra == ()


def test_scope_str_is_space_delimited():
    # Microsoft's /authorize wants space-delimited scopes (OAuth 2.0 spec)
    assert PROVIDER.scope_str == "Mail.Read Calendars.Read Contacts.Read offline_access User.Read"


# --------------------------------------------------------------------------- #
# authorize URL (PKCE)
# --------------------------------------------------------------------------- #
def test_build_authorize_url_includes_scopes_and_pkce():
    verifier, challenge = oauth.generate_pkce_pair()
    assert challenge == oauth._b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    url = oauth.build_authorize_url(PROVIDER, client_id="CID",
                                    redirect_uri="https://api/cb", state="ST",
                                    code_challenge=challenge)
    assert url.startswith(PROVIDER.authorize_url + "?")
    assert "code_challenge_method=S256" in url
    assert "offline_access" in url  # the refresh-token scope must reach the provider
    assert "Mail.Read" in url


# --------------------------------------------------------------------------- #
# token exchange + refresh
# --------------------------------------------------------------------------- #
def test_exchange_code_sends_verifier_and_returns_refresh_token(monkeypatch):
    captured = {}

    def fake_post(url, fields):
        captured["url"] = url
        captured["fields"] = fields
        # offline_access -> Graph returns a refresh_token alongside the access token
        return {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600,
                "token_type": "Bearer"}

    monkeypatch.setattr(oauth, "post_form", fake_post)
    out = oauth.exchange_code(PROVIDER, code="the-code", redirect_uri="https://api/cb",
                              client_id="CID", client_secret="CSEC",
                              code_verifier="VERIFIER", now=1000)
    assert captured["url"] == PROVIDER.token_url
    assert captured["fields"]["grant_type"] == "authorization_code"
    assert captured["fields"]["code_verifier"] == "VERIFIER"
    assert captured["fields"]["redirect_uri"] == "https://api/cb"
    assert "user_type" not in captured["fields"]  # no token_extra for microsoft
    assert out["access_token"] == "AT"
    assert out["refresh_token"] == "RT"
    assert out["expires_at"] == 1000 + 3600
    assert out["token_type"] == "oauth"
    # Microsoft passes no location/company — the envelope stays the HubSpot shape
    assert "location_id" not in out
    assert "company_id" not in out


def test_refresh_rolls_refresh_token(monkeypatch):
    def fake_post(url, fields):
        assert fields["grant_type"] == "refresh_token"
        # Microsoft Identity Platform ROLLS the refresh token on every refresh
        return {"access_token": "AT2", "refresh_token": "NEW-RT", "expires_in": 3600}

    monkeypatch.setattr(oauth, "post_form", fake_post)
    out = oauth.refresh_access_token(PROVIDER, refresh_token="OLD-RT",
                                     client_id="CID", client_secret="CSEC", now=2000)
    assert out["access_token"] == "AT2"
    assert out["refresh_token"] == "NEW-RT"  # rolled
    assert out["expires_at"] == 2000 + 3600


def test_refresh_preserves_old_refresh_token_when_omitted(monkeypatch):
    monkeypatch.setattr(oauth, "post_form",
                        lambda url, fields: {"access_token": "AT2", "expires_in": 3600})
    out = oauth.refresh_access_token(PROVIDER, refresh_token="OLD-RT",
                                     client_id="CID", client_secret="CSEC", now=2000)
    assert out["refresh_token"] == "OLD-RT"  # preserved when not re-issued


# --------------------------------------------------------------------------- #
# vault envelope
# --------------------------------------------------------------------------- #
def test_envelope_roundtrips_without_location():
    value = oauth.oauth_secret_value(access_token="AT", refresh_token="RT", expires_at=999)
    parsed = oauth.parse_oauth_secret(value)
    assert parsed["access_token"] == "AT"
    assert parsed["refresh_token"] == "RT"
    assert parsed["token_type"] == "oauth"
    assert "location_id" not in parsed
