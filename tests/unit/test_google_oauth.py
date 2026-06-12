"""Unit: Google (Calendar + Contacts) OAuth — provider registration, PKCE, the
REQUIRED access_type=offline + prompt=consent authorize extras, and the
authorization_code / refresh_token exchanges. FIXTURES only: oauth.post_form is
monkeypatched, so token exchange/refresh make ZERO live oauth2.googleapis.com calls.

Covers:
  * the google provider is registered with Google's standard OAuth endpoints, PKCE,
    the read-only Calendar+Contacts sensitive scopes (NO Gmail — that's CASA), and
    the Secrets Manager client_id/client_secret refs
  * build_authorize_url emits the scopes, the S256 code_challenge, AND the
    access_type=offline + prompt=consent extras (without which Google never returns
    a refresh_token)
  * exchange_code sends the PKCE verifier + redirect_uri and returns a refresh_token
    captured into the vault envelope
  * refresh_access_token PRESERVES the old refresh_token (Google does not roll it)
  * the OAuth envelope round-trips with no location (HubSpot/Google shape)
"""
import hashlib
import urllib.parse

from ingest.connectors import oauth

PROVIDER = oauth.get_provider("google")

CID_REF = "uplift/oauth/google/client_id"
CSEC_REF = "uplift/oauth/google/client_secret"


# --------------------------------------------------------------------------- #
# provider registry
# --------------------------------------------------------------------------- #
def test_google_provider_registered():
    p = oauth.get_provider("google")
    assert p is not None
    assert p.authorize_url == "https://accounts.google.com/o/oauth2/v2/auth"
    assert p.token_url == "https://oauth2.googleapis.com/token"
    assert p.pkce is True
    # read-only Calendar + Contacts (sensitive scopes — consent verification, NOT CASA)
    assert "https://www.googleapis.com/auth/calendar.readonly" in p.scopes
    assert "https://www.googleapis.com/auth/contacts.readonly" in p.scopes
    # Gmail is DEFERRED (its scopes are "restricted" -> Google CASA) — must NOT appear
    assert not any("gmail" in s for s in p.scopes)
    assert p.client_id_ref == CID_REF
    assert p.client_secret_ref == CSEC_REF
    # Google needs no token_extra (unlike GoHighLevel's user_type)
    assert p.token_extra == ()
    # …but DOES need access_type=offline + prompt=consent on the AUTHORIZE url
    assert ("access_type", "offline") in p.authorize_extra
    assert ("prompt", "consent") in p.authorize_extra


def test_scope_str_is_space_delimited():
    assert PROVIDER.scope_str == (
        "https://www.googleapis.com/auth/calendar.readonly "
        "https://www.googleapis.com/auth/contacts.readonly"
    )


# --------------------------------------------------------------------------- #
# authorize URL (PKCE + offline/consent extras)
# --------------------------------------------------------------------------- #
def test_build_authorize_url_includes_scopes_pkce_and_offline_consent():
    verifier, challenge = oauth.generate_pkce_pair()
    assert challenge == oauth._b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    url = oauth.build_authorize_url(PROVIDER, client_id="CID",
                                    redirect_uri="https://api/cb", state="ST",
                                    code_challenge=challenge)
    assert url.startswith(PROVIDER.authorize_url + "?")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["code_challenge_method"] == ["S256"]
    assert q["code_challenge"] == [challenge]
    # the two extras that make Google ISSUE a refresh_token
    assert q["access_type"] == ["offline"]
    assert q["prompt"] == ["consent"]
    assert "calendar.readonly" in q["scope"][0]
    assert "contacts.readonly" in q["scope"][0]


def test_other_providers_have_no_authorize_extra():
    # the new authorize_extra field must not change non-Google authorize URLs.
    for name in ("hubspot", "gohighlevel", "salesforce", "microsoft"):
        assert oauth.get_provider(name).authorize_extra == ()


# --------------------------------------------------------------------------- #
# token exchange + refresh
# --------------------------------------------------------------------------- #
def test_exchange_code_sends_verifier_and_returns_refresh_token(monkeypatch):
    captured = {}

    def fake_post(url, fields):
        captured["url"] = url
        captured["fields"] = fields
        # access_type=offline + prompt=consent -> Google returns a refresh_token
        return {"access_token": "AT", "refresh_token": "RT", "expires_in": 3599,
                "token_type": "Bearer"}

    monkeypatch.setattr(oauth, "post_form", fake_post)
    out = oauth.exchange_code(PROVIDER, code="the-code", redirect_uri="https://api/cb",
                              client_id="CID", client_secret="CSEC",
                              code_verifier="VERIFIER", now=1000)
    assert captured["url"] == PROVIDER.token_url
    assert captured["fields"]["grant_type"] == "authorization_code"
    assert captured["fields"]["code_verifier"] == "VERIFIER"
    assert captured["fields"]["redirect_uri"] == "https://api/cb"
    assert "user_type" not in captured["fields"]  # no token_extra for google
    assert out["access_token"] == "AT"
    assert out["refresh_token"] == "RT"
    assert out["expires_at"] == 1000 + 3599
    assert out["token_type"] == "oauth"
    # Google passes no location/company/instance — envelope stays the HubSpot shape
    assert "location_id" not in out
    assert "instance_url" not in out


def test_refresh_preserves_old_refresh_token(monkeypatch):
    # Google does NOT return a new refresh_token on refresh — the old one persists.
    def fake_post(url, fields):
        assert fields["grant_type"] == "refresh_token"
        return {"access_token": "AT2", "expires_in": 3599}

    monkeypatch.setattr(oauth, "post_form", fake_post)
    out = oauth.refresh_access_token(PROVIDER, refresh_token="OLD-RT",
                                     client_id="CID", client_secret="CSEC", now=2000)
    assert out["access_token"] == "AT2"
    assert out["refresh_token"] == "OLD-RT"  # preserved
    assert out["expires_at"] == 2000 + 3599


def test_refresh_rolls_refresh_token_when_reissued(monkeypatch):
    monkeypatch.setattr(oauth, "post_form",
                        lambda url, fields: {"access_token": "AT2", "refresh_token": "NEW-RT",
                                             "expires_in": 3599})
    out = oauth.refresh_access_token(PROVIDER, refresh_token="OLD-RT",
                                     client_id="CID", client_secret="CSEC", now=2000)
    assert out["refresh_token"] == "NEW-RT"  # honored when Google DOES re-issue


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
