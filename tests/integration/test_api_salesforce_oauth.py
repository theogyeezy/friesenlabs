"""Integration: the Salesforce OAuth connect routes (/integrations/salesforce/
oauth/start + /callback). FIXTURES only — oauth.post_form is monkeypatched, so
token exchange never hits Salesforce.

Proves:
  * honest 503 when OAuth is unconfigured (no state secret / redirect base)
  * /start builds a PKCE authorize URL (code_challenge + S256) with the SF scopes
    and a SIGNED, claims-bound state (tenant = the verified JWT, never a query value)
  * the callback verifies the state, exchanges the code (PKCE verifier rides it),
    and persists the OAuth envelope INCLUDING instance_url in uplift/{tenant}/salesforce
  * the access/refresh token + instance_url never leak into the redirect URL
"""
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
STATE_SECRET = "integration-test-state-secret"
REDIRECT_BASE = "https://api.test.example"
APP_RETURN = "https://app.test.example/integrations"
CID_REF = "uplift/oauth/salesforce/client_id"
CSEC_REF = "uplift/oauth/salesforce/client_secret"
INSTANCE = "https://acme.my.salesforce.com"


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


@pytest.mark.integration
def test_salesforce_start_503_when_unconfigured():
    client = _client(_deps(configured=False))
    assert client.get("/integrations/salesforce/oauth/start", headers=H).status_code == 503


@pytest.mark.integration
def test_salesforce_start_builds_pkce_authorize_url():
    client = _client(_deps())
    r = client.get("/integrations/salesforce/oauth/start", headers=H)
    assert r.status_code == 200
    url = r.json()["authorize_url"]
    assert url.startswith(oauth.get_provider("salesforce").authorize_url)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    # PKCE S256 + the SF scopes; the verifier itself is NOT in the URL (only the challenge).
    assert q["code_challenge_method"] == ["S256"]
    assert q["code_challenge"][0]
    assert q["scope"] == ["api refresh_token"]
    # claims-bound signed state recovers the VERIFIED tenant
    assert oauth.verify_state(q["state"][0], STATE_SECRET) == "TENANT-A"


@pytest.mark.integration
def test_salesforce_callback_persists_instance_url(monkeypatch):
    monkeypatch.setattr(oauth, "post_form", lambda url, fields: {
        "access_token": "AT-secret", "refresh_token": "RT-secret",
        "instance_url": INSTANCE, "id": "https://login.salesforce.com/id/00D/005"})
    writer = RecordingWriter()
    client = _client(_deps(writer=writer))
    # round-trip: /start mints the signed state (carrying the PKCE verifier), the
    # callback presents it back.
    start = client.get("/integrations/salesforce/oauth/start", headers=H)
    state = urllib.parse.parse_qs(urllib.parse.urlparse(start.json()["authorize_url"]).query)["state"][0]
    r = client.get(f"/integrations/salesforce/oauth/callback?code=the-code&state={state}",
                   follow_redirects=False)
    assert r.status_code == 302
    assert "connected=1" in r.headers["location"]
    stored = json.loads(writer.put["uplift/TENANT-A/salesforce"])
    assert stored["access_token"] == "AT-secret"
    assert stored["refresh_token"] == "RT-secret"
    assert stored["instance_url"] == INSTANCE
    assert stored["token_type"] == "oauth"
    # nothing sensitive leaks into the redirect
    for leak in ("AT-secret", "RT-secret", INSTANCE):
        assert leak not in r.headers["location"]
