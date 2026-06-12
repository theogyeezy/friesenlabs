"""Unit: GoHighLevel OAuth — PKCE flow, location-carrying envelope, and the
OAuth-aware GoHighLevelConnector.authenticate(). FIXTURES only: oauth.post_form
is monkeypatched, so token exchange/refresh make ZERO live LeadConnector calls.

Covers:
  * the gohighlevel provider is registered with PKCE + the right URLs/scopes/refs
  * generate_pkce_pair() yields a challenge = b64url(sha256(verifier)) (S256)
  * the signed state carries the PKCE code_verifier and verify_state_payload
    recovers it (tampered/forged states are still rejected)
  * build_authorize_url adds code_challenge + code_challenge_method=S256
  * exchange_code sends the verifier + user_type and captures locationId/companyId
    into the vault envelope; refresh preserves the refresh_token + location
  * the OAuth envelope round-trips with location_id/company_id
  * the connector: bare token + legacy {"token","location_id"} back-compat,
    a fresh OAuth envelope (no refresh), an EXPIRED envelope (refresh + write-back,
    location preserved), and the honest failure when app creds are missing
"""
import hashlib
import json

import pytest

from ingest.connectors import oauth
from ingest.connectors.base import MissingTenantCredentialError, SecretNotFoundError
from ingest.connectors.gohighlevel import GoHighLevelConnector

SECRET = "ghl-test-hmac-signing-secret"
PROVIDER = oauth.get_provider("gohighlevel")

REF = "uplift/T1/gohighlevel"
CID_REF = "uplift/oauth/gohighlevel/client_id"
CSEC_REF = "uplift/oauth/gohighlevel/client_secret"


# --------------------------------------------------------------------------- #
# provider registry
# --------------------------------------------------------------------------- #
def test_gohighlevel_provider_registered():
    p = oauth.get_provider("gohighlevel")
    assert p is not None
    assert p.authorize_url == "https://marketplace.gohighlevel.com/oauth/chooselocation"
    assert p.token_url == "https://services.leadconnectorhq.com/oauth/token"
    assert p.pkce is True
    # read-only scopes the connector pulls
    assert "contacts.readonly" in p.scopes
    assert "opportunities.readonly" in p.scopes
    assert "locations.readonly" in p.scopes
    assert p.client_id_ref == CID_REF
    assert p.client_secret_ref == CSEC_REF
    # user_type=Location selects a location-scoped token
    assert ("user_type", "Location") in p.token_extra


def test_hubspot_provider_is_not_pkce():
    # back-compat: HubSpot stays non-PKCE with no token_extra
    hs = oauth.get_provider("hubspot")
    assert hs.pkce is False
    assert hs.token_extra == ()


# --------------------------------------------------------------------------- #
# PKCE
# --------------------------------------------------------------------------- #
def test_generate_pkce_pair_is_s256():
    verifier, challenge = oauth.generate_pkce_pair()
    # RFC 7636: verifier 43-128 chars; challenge = b64url(sha256(verifier)), no pad
    assert 43 <= len(verifier) <= 128
    expected = oauth._b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    assert challenge == expected
    assert "=" not in challenge


def test_generate_pkce_pair_is_unique():
    a, _ = oauth.generate_pkce_pair()
    b, _ = oauth.generate_pkce_pair()
    assert a != b


def test_signed_state_carries_code_verifier():
    state = oauth.sign_state("tenant-A", SECRET, nonce="n1", issued_at=1000,
                             code_verifier="VERIFIER-123")
    payload = oauth.verify_state_payload(state, SECRET, now=1010)
    assert payload["t"] == "tenant-A"
    assert payload["cv"] == "VERIFIER-123"
    # the thin tenant-only wrapper still works
    assert oauth.verify_state(state, SECRET, now=1010) == "tenant-A"


def test_state_without_verifier_has_no_cv():
    # non-PKCE state shape is unchanged (no cv key)
    state = oauth.sign_state("tenant-A", SECRET, nonce="n1", issued_at=1000)
    payload = oauth.verify_state_payload(state, SECRET, now=1010)
    assert "cv" not in payload


def test_tampered_state_with_verifier_rejected():
    state = oauth.sign_state("tenant-A", SECRET, nonce="n1", issued_at=1000,
                             code_verifier="V")
    body, sig = state.split(".", 1)
    forged = f"{body}.{sig[:-2]}XY"
    with pytest.raises(oauth.StateError):
        oauth.verify_state_payload(forged, SECRET, now=1010)


# --------------------------------------------------------------------------- #
# authorize URL + exchange/refresh
# --------------------------------------------------------------------------- #
def test_build_authorize_url_includes_pkce_challenge():
    url = oauth.build_authorize_url(PROVIDER, client_id="CID",
                                    redirect_uri="https://api/cb", state="ST",
                                    code_challenge="CHALLENGE")
    assert url.startswith(PROVIDER.authorize_url + "?")
    assert "code_challenge=CHALLENGE" in url
    assert "code_challenge_method=S256" in url
    assert "contacts.readonly" in url


def test_build_authorize_url_omits_pkce_when_absent():
    url = oauth.build_authorize_url(PROVIDER, client_id="CID",
                                    redirect_uri="https://api/cb", state="ST")
    assert "code_challenge" not in url


def test_exchange_code_sends_verifier_and_user_type_and_captures_location(monkeypatch):
    captured = {}

    def fake_post(url, fields):
        captured["url"] = url
        captured["fields"] = fields
        return {"access_token": "AT", "refresh_token": "RT", "expires_in": 86400,
                "locationId": "loc-123", "companyId": "co-456"}

    monkeypatch.setattr(oauth, "post_form", fake_post)
    out = oauth.exchange_code(PROVIDER, code="the-code", redirect_uri="https://api/cb",
                              client_id="CID", client_secret="CSEC",
                              code_verifier="VERIFIER", now=1000)
    assert captured["url"] == PROVIDER.token_url
    assert captured["fields"]["grant_type"] == "authorization_code"
    assert captured["fields"]["code_verifier"] == "VERIFIER"
    assert captured["fields"]["user_type"] == "Location"  # from token_extra
    # locationId/companyId captured into the envelope dict
    assert out["location_id"] == "loc-123"
    assert out["company_id"] == "co-456"
    assert out["expires_at"] == 1000 + 86400


def test_refresh_preserves_location_and_refresh_token(monkeypatch):
    captured = {}

    def fake_post(url, fields):
        captured.update(fields)
        # LeadConnector refresh may omit a new refresh_token / location
        return {"access_token": "AT2", "expires_in": 86400}

    monkeypatch.setattr(oauth, "post_form", fake_post)
    out = oauth.refresh_access_token(PROVIDER, refresh_token="OLD-RT",
                                     client_id="CID", client_secret="CSEC", now=2000)
    assert captured["grant_type"] == "refresh_token"
    assert captured["user_type"] == "Location"  # token_extra applies to refresh too
    assert out["access_token"] == "AT2"
    assert out["refresh_token"] == "OLD-RT"  # preserved when omitted
    assert "location_id" not in out  # none echoed -> caller preserves the stored one


# --------------------------------------------------------------------------- #
# vault envelope
# --------------------------------------------------------------------------- #
def test_envelope_roundtrips_with_location():
    value = oauth.oauth_secret_value(access_token="AT", refresh_token="RT",
                                     expires_at=999, location_id="loc-1",
                                     company_id="co-1")
    parsed = oauth.parse_oauth_secret(value)
    assert parsed["location_id"] == "loc-1"
    assert parsed["company_id"] == "co-1"
    assert parsed["token_type"] == "oauth"


def test_envelope_without_location_omits_keys():
    # HubSpot path: no location -> envelope unchanged
    value = oauth.oauth_secret_value(access_token="AT", refresh_token="RT", expires_at=1)
    parsed = json.loads(value)
    assert "location_id" not in parsed
    assert "company_id" not in parsed


# --------------------------------------------------------------------------- #
# connector authenticate() — OAuth-aware, with legacy fallback
# --------------------------------------------------------------------------- #
class FakeSecrets:
    def __init__(self, values):
        self._values = values

    def get_secret(self, ref):
        if ref not in self._values:
            raise SecretNotFoundError(ref)
        return self._values[ref]


class RecordingWriter:
    def __init__(self):
        self.put = {}

    def put_secret(self, ref, value):
        self.put[ref] = value


class CapturingClient:
    """GoHighLevelClient that records the token + location it was handed."""

    def __init__(self):
        self.token = None
        self.location = None

    def set_token(self, token):
        self.token = token

    def set_location(self, location_id):
        self.location = location_id

    def list_contacts(self, since):
        return []

    def list_opportunities(self, since):
        return []


def _connector(secrets_values, writer=None):
    client = CapturingClient()
    conn = GoHighLevelConnector(
        "T1", client=client, secret_writer=writer,
        secrets=FakeSecrets(secrets_values), raw_sink=object(), structured_sink=object(),
    )
    return conn, client


def test_bare_token_back_compat():
    conn, client = _connector({REF: "ghl-bare-token"})
    conn.authenticate()
    assert client.token == "ghl-bare-token"
    assert client.location is None


def test_legacy_json_credential_back_compat():
    conn, client = _connector({REF: json.dumps({"token": "tok", "location_id": "loc-9"})})
    conn.authenticate()
    assert client.token == "tok"
    assert client.location == "loc-9"


def test_fresh_oauth_envelope_uses_access_token_and_location(monkeypatch):
    monkeypatch.setattr(oauth, "post_form",
                        lambda *a, **k: pytest.fail("no refresh on a fresh token"))
    env = oauth.oauth_secret_value(access_token="FRESH-AT", refresh_token="RT",
                                   expires_at=99_999_999_999, location_id="loc-7")
    conn, client = _connector({REF: env})
    conn.authenticate()
    assert client.token == "FRESH-AT"
    assert client.location == "loc-7"


def test_expired_oauth_refreshes_writes_back_preserves_location(monkeypatch):
    # refresh response omits a new location -> the stored one must be preserved.
    monkeypatch.setattr(oauth, "post_form", lambda url, fields: {
        "access_token": "REFRESHED-AT", "refresh_token": "NEW-RT", "expires_in": 86400,
    })
    expired = oauth.oauth_secret_value(access_token="OLD-AT", refresh_token="OLD-RT",
                                       expires_at=1, location_id="loc-5", company_id="co-5")
    writer = RecordingWriter()
    conn, client = _connector({REF: expired, CID_REF: "CID", CSEC_REF: "CSEC"}, writer=writer)
    conn.authenticate()
    assert client.token == "REFRESHED-AT"
    assert client.location == "loc-5"  # preserved across refresh
    stored = json.loads(writer.put[REF])
    assert stored["access_token"] == "REFRESHED-AT"
    assert stored["refresh_token"] == "NEW-RT"
    assert stored["location_id"] == "loc-5"
    assert stored["company_id"] == "co-5"
    assert stored["token_type"] == "oauth"


def test_expired_without_client_creds_fails_honestly(monkeypatch):
    monkeypatch.setattr(oauth, "post_form",
                        lambda *a, **k: pytest.fail("must not exchange without creds"))
    expired = oauth.oauth_secret_value(access_token="OLD-AT", refresh_token="OLD-RT",
                                       expires_at=1, location_id="loc-5")
    conn, client = _connector({REF: expired})  # no client creds provisioned
    with pytest.raises(MissingTenantCredentialError):
        conn.authenticate()
    assert client.token is None  # never handed a dead token


def test_refresh_without_writer_still_uses_new_token(monkeypatch):
    monkeypatch.setattr(oauth, "post_form", lambda url, fields: {
        "access_token": "REFRESHED-AT", "refresh_token": "NEW-RT", "expires_in": 86400,
    })
    expired = oauth.oauth_secret_value(access_token="OLD-AT", refresh_token="OLD-RT",
                                       expires_at=1, location_id="loc-5")
    conn, client = _connector({REF: expired, CID_REF: "CID", CSEC_REF: "CSEC"})
    conn.authenticate()
    assert client.token == "REFRESHED-AT"
    assert client.location == "loc-5"
