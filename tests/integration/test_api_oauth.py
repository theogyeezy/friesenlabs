"""Integration: the OAuth connect routes (/integrations/{name}/oauth/start +
/callback). FIXTURES only — oauth.post_form is monkeypatched, so token exchange
never hits HubSpot.

Proves:
  * start is claims-bound (401 unauth) and builds an authorize URL with a SIGNED
    state — the tenant is THE verified claim, never a body/query value
  * the callback (UNAUTHENTICATED — a provider redirect carries no JWT) verifies
    the signed state, recovers the tenant, exchanges the code, and stores the
    OAuth envelope in uplift/{tenant}/hubspot, then 302s back to the app
  * a TAMPERED/forged state is rejected (403) — no token is stored
  * honest 503 when OAuth is unconfigured (no state secret / redirect base /
    reader / writer)
  * a provider with no OAuth flow (stripe) answers 409
  * the access/refresh token VALUES never appear in any response body
"""
import json

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
CID_REF = "uplift/oauth/hubspot/client_id"
CSEC_REF = "uplift/oauth/hubspot/client_secret"


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
    integrations = IntegrationsDeps(
        secret_writer=writer if writer is not None else RecordingWriter(),
        secret_reader=FakeReader(reader_values),
        oauth_state_secret=STATE_SECRET if configured else "",
        oauth_redirect_base=REDIRECT_BASE if configured else "",
        oauth_app_return_url=APP_RETURN,
    )
    return integrations


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
    assert client.get("/integrations/hubspot/oauth/start").status_code == 401


@pytest.mark.integration
def test_start_builds_authorize_url_with_signed_state():
    client = _client(_deps())
    r = client.get("/integrations/hubspot/oauth/start", headers=H)
    assert r.status_code == 200
    url = r.json()["authorize_url"]
    assert url.startswith(oauth.get_provider("hubspot").authorize_url)
    assert "client_id=CID" in url
    assert f"{REDIRECT_BASE}/integrations/hubspot/oauth/callback" in url.replace("%2F", "/").replace("%3A", ":")
    # The state recovers the VERIFIED tenant — claims-bound, not a query value.
    state = url.split("state=")[1].split("&")[0]
    import urllib.parse
    assert oauth.verify_state(urllib.parse.unquote(state), STATE_SECRET) == "TENANT-A"


@pytest.mark.integration
def test_start_503_when_unconfigured():
    client = _client(_deps(configured=False))
    assert client.get("/integrations/hubspot/oauth/start", headers=H).status_code == 503


@pytest.mark.integration
def test_start_503_when_app_creds_not_registered():
    client = _client(_deps(reader_values={}))  # no client_id in the vault
    r = client.get("/integrations/hubspot/oauth/start", headers=H)
    assert r.status_code == 503


@pytest.mark.integration
def test_start_409_for_non_oauth_provider():
    client = _client(_deps())
    # stripe is a known integration but has no OAuth flow -> 409, not 404/500.
    assert client.get("/integrations/stripe/oauth/start", headers=H).status_code == 409


# --------------------------------------------------------------------------- #
# callback
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_callback_exchanges_and_stores_envelope(monkeypatch):
    monkeypatch.setattr(oauth, "post_form", lambda url, fields: {
        "access_token": "AT-secret", "refresh_token": "RT-secret", "expires_in": 1800,
    })
    writer = RecordingWriter()
    client = _client(_deps(writer=writer))
    state = oauth.sign_state("TENANT-A", STATE_SECRET, nonce="n1")
    r = client.get(f"/integrations/hubspot/oauth/callback?code=the-code&state={state}",
                   headers=H, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith(APP_RETURN)
    assert "connected=1" in r.headers["location"]
    # The OAuth envelope landed in the tenant's vault slot — tenant from the
    # SIGNED state, never the browser.
    stored = json.loads(writer.put["uplift/TENANT-A/hubspot"])
    assert stored["access_token"] == "AT-secret"
    assert stored["refresh_token"] == "RT-secret"
    assert stored["token_type"] == "oauth"
    # The token VALUES never leak into the redirect URL.
    assert "AT-secret" not in r.headers["location"]
    assert "RT-secret" not in r.headers["location"]


@pytest.mark.integration
def test_callback_rejects_tampered_state(monkeypatch):
    monkeypatch.setattr(oauth, "post_form",
                        lambda *a, **k: pytest.fail("exchange must not run on bad state"))
    writer = RecordingWriter()
    client = _client(_deps(writer=writer))
    good = oauth.sign_state("TENANT-A", STATE_SECRET, nonce="n1")
    body, sig = good.split(".", 1)
    forged = f"{body}.{sig[:-2]}XY"
    r = client.get(f"/integrations/hubspot/oauth/callback?code=c&state={forged}",
                   follow_redirects=False)
    assert r.status_code == 403
    assert writer.put == {}  # nothing stored


@pytest.mark.integration
def test_callback_user_denied_redirects_with_error(monkeypatch):
    monkeypatch.setattr(oauth, "post_form",
                        lambda *a, **k: pytest.fail("no exchange on denial"))
    client = _client(_deps())
    r = client.get("/integrations/hubspot/oauth/callback?error=access_denied",
                   follow_redirects=False)
    assert r.status_code == 302
    assert "error=denied" in r.headers["location"]


@pytest.mark.integration
def test_callback_503_when_unconfigured():
    client = _client(_deps(configured=False))
    state = "anything.anything"
    r = client.get(f"/integrations/hubspot/oauth/callback?code=c&state={state}",
                   follow_redirects=False)
    assert r.status_code == 503


@pytest.mark.integration
def test_callback_exchange_failure_redirects_with_error(monkeypatch):
    def boom(url, fields):
        raise oauth.TokenExchangeError("HTTP 400", status=400)

    monkeypatch.setattr(oauth, "post_form", boom)
    writer = RecordingWriter()
    client = _client(_deps(writer=writer))
    state = oauth.sign_state("TENANT-A", STATE_SECRET, nonce="n1")
    r = client.get(f"/integrations/hubspot/oauth/callback?code=c&state={state}",
                   follow_redirects=False)
    assert r.status_code == 302
    assert "error=exchange_failed" in r.headers["location"]
    assert writer.put == {}  # nothing stored on a failed exchange


# --------------------------------------------------------------------------- #
# GET /integrations advertises oauth_available so the web leads with the
# one-click "Connect with {Provider}" button instead of paste-a-key.
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_list_advertises_oauth_available_per_provider():
    client = _client(_deps())
    body = client.get("/integrations", headers=H).json()
    by_name = {i["name"]: i for i in body["integrations"]}
    # Every connector with a registered OAuth provider AND a ready runtime
    # advertises the login path — including Google (the case that was rendering
    # only a paste-a-key field).
    for name in ("google", "hubspot", "gohighlevel", "microsoft", "salesforce", "pipedrive"):
        assert by_name[name]["oauth_available"] is True, name
    # stripe is a known integration with NO OAuth provider -> never advertised.
    assert by_name["stripe"]["oauth_available"] is False
    # csv is file-kind -> never an OAuth login.
    if "csv" in by_name:
        assert by_name["csv"]["oauth_available"] is False


@pytest.mark.integration
def test_list_oauth_available_false_when_unconfigured():
    # No state secret / redirect base -> the runtime isn't ready -> no connector
    # advertises OAuth (the web falls back to its own known-capable set).
    client = _client(_deps(configured=False))
    body = client.get("/integrations", headers=H).json()
    assert all(i["oauth_available"] is False for i in body["integrations"])
