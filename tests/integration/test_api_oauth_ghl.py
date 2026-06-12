"""Integration: the OAuth connect routes for GoHighLevel — PKCE start + callback.
FIXTURES only — oauth.post_form is monkeypatched, so token exchange never hits
LeadConnector.

Proves:
  * start (claims-bound) builds a chooselocation authorize URL with a SIGNED
    state that carries the PKCE code_verifier, and an S256 code_challenge that
    matches that verifier — the verifier itself never appears in the URL
  * the callback verifies the signed state, recovers the tenant + verifier,
    exchanges the code (sending the verifier + user_type), and stores an OAuth
    envelope carrying locationId/companyId in uplift/{tenant}/gohighlevel
  * honest 503 when OAuth is unconfigured
  * the access/refresh token VALUES never appear in the redirect URL
"""
import hashlib
import json
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.integrations_routes import IntegrationsDeps
from api.views import SavedViews
from ingest.connectors import oauth

H = {"Authorization": "Bearer t"}
STATE_SECRET = "ghl-integration-test-state-secret"
REDIRECT_BASE = "https://api.test.example"
APP_RETURN = "https://app.test.example/integrations"
CID_REF = "uplift/oauth/gohighlevel/client_id"
CSEC_REF = "uplift/oauth/gohighlevel/client_secret"


class FakeVerifier:
    def verify(self, token):
        return {"sub": "uA", "custom:tenant_id": "TENANT-A", "email": "a@x.com"}


class RecordingWriter:
    def __init__(self):
        self.put = {}

    def put_secret(self, ref, value):
        self.put[ref] = value

    def secret_exists(self, ref):
        return ref in self.put

    def delete_secret(self, ref):
        return bool(self.put.pop(ref, None))


class FakeReader:
    def __init__(self, values):
        self._values = values

    def get_secret(self, ref):
        from ingest.connectors.base import SecretNotFoundError
        if ref not in self._values:
            raise SecretNotFoundError(ref)
        return self._values[ref]


def _deps(*, configured=True, reader_values=None, writer=None):
    reader_values = reader_values if reader_values is not None else {CID_REF: "CID", CSEC_REF: "CSEC"}
    return IntegrationsDeps(
        secret_writer=writer if writer is not None else RecordingWriter(),
        secret_reader=FakeReader(reader_values),
        oauth_state_secret=STATE_SECRET if configured else "",
        oauth_redirect_base=REDIRECT_BASE if configured else "",
        oauth_app_return_url=APP_RETURN,
    )


def _client(integrations):
    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
        executor=lambda a: None, integrations=integrations,
    )
    return TestClient(create_app(deps))


# --------------------------------------------------------------------------- #
# start
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_start_unauth_401():
    client = _client(_deps())
    assert client.get("/integrations/gohighlevel/oauth/start").status_code == 401


@pytest.mark.integration
def test_start_builds_pkce_authorize_url():
    client = _client(_deps())
    r = client.get("/integrations/gohighlevel/oauth/start", headers=H)
    assert r.status_code == 200
    url = r.json()["authorize_url"]
    assert url.startswith(oauth.get_provider("gohighlevel").authorize_url)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["client_id"] == ["CID"]
    assert q["code_challenge_method"] == ["S256"]
    # The state recovers the VERIFIED tenant AND carries the PKCE verifier; the
    # challenge in the URL must be the S256 hash of that signed verifier.
    payload = oauth.verify_state_payload(q["state"][0], STATE_SECRET)
    assert payload["t"] == "TENANT-A"
    verifier = payload["cv"]
    expected_challenge = oauth._b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    assert q["code_challenge"] == [expected_challenge]
    # The raw verifier never leaks into the authorize URL (only the challenge).
    assert verifier not in url


@pytest.mark.integration
def test_start_503_when_unconfigured():
    client = _client(_deps(configured=False))
    assert client.get("/integrations/gohighlevel/oauth/start", headers=H).status_code == 503


@pytest.mark.integration
def test_start_503_when_app_creds_not_registered():
    client = _client(_deps(reader_values={}))  # no client_id in the vault
    assert client.get("/integrations/gohighlevel/oauth/start", headers=H).status_code == 503


# --------------------------------------------------------------------------- #
# callback
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_callback_exchanges_with_pkce_and_stores_location_envelope(monkeypatch):
    captured = {}

    def fake_post(url, fields):
        captured["fields"] = fields
        return {"access_token": "AT-secret", "refresh_token": "RT-secret",
                "expires_in": 86400, "locationId": "loc-123", "companyId": "co-456"}

    monkeypatch.setattr(oauth, "post_form", fake_post)
    writer = RecordingWriter()
    client = _client(_deps(writer=writer))
    # Mint a state the way /start does — with the PKCE verifier signed in.
    state = oauth.sign_state("TENANT-A", STATE_SECRET, nonce="n1", code_verifier="VERIFIER-XYZ")
    r = client.get(f"/integrations/gohighlevel/oauth/callback?code=the-code&state={state}",
                   follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith(APP_RETURN)
    assert "connected=1" in r.headers["location"]
    # The verifier from the signed state rode the exchange (PKCE proof).
    assert captured["fields"]["code_verifier"] == "VERIFIER-XYZ"
    assert captured["fields"]["user_type"] == "Location"
    # The OAuth envelope landed in the tenant's slot WITH the chosen location.
    stored = json.loads(writer.put["uplift/TENANT-A/gohighlevel"])
    assert stored["access_token"] == "AT-secret"
    assert stored["refresh_token"] == "RT-secret"
    assert stored["location_id"] == "loc-123"
    assert stored["company_id"] == "co-456"
    assert stored["token_type"] == "oauth"
    # Token VALUES never leak into the redirect URL.
    assert "AT-secret" not in r.headers["location"]
    assert "RT-secret" not in r.headers["location"]


@pytest.mark.integration
def test_callback_rejects_tampered_state(monkeypatch):
    monkeypatch.setattr(oauth, "post_form",
                        lambda *a, **k: pytest.fail("exchange must not run on bad state"))
    writer = RecordingWriter()
    client = _client(_deps(writer=writer))
    good = oauth.sign_state("TENANT-A", STATE_SECRET, nonce="n1", code_verifier="V")
    body, sig = good.split(".", 1)
    forged = f"{body}.{sig[:-2]}XY"
    r = client.get(f"/integrations/gohighlevel/oauth/callback?code=c&state={forged}",
                   follow_redirects=False)
    assert r.status_code == 403
    assert writer.put == {}


@pytest.mark.integration
def test_callback_503_when_unconfigured():
    client = _client(_deps(configured=False))
    r = client.get("/integrations/gohighlevel/oauth/callback?code=c&state=a.b",
                   follow_redirects=False)
    assert r.status_code == 503
