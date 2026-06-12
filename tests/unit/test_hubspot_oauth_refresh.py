"""Unit: HubSpotConnector.authenticate() OAuth-awareness + refresh-on-expiry.

FIXTURES only — oauth.post_form is monkeypatched, so refresh makes ZERO live
calls. Covers:
  * a bare pasted token (legacy) still authenticates unchanged (back-compat)
  * a FRESH OAuth envelope authenticates with its access_token, no refresh
  * an EXPIRED OAuth envelope refreshes, hands the NEW access token to the
    client, and writes the new envelope back to the vault
  * an expired envelope with the app's client creds MISSING fails honestly
    (MissingTenantCredentialError — reconnect), never rides a dead token
"""
import json

import pytest

from ingest.connectors import oauth
from ingest.connectors.base import SecretNotFoundError
from ingest.connectors.hubspot import HubSpotConnector


class FakeSecrets:
    """Read-side SecretProvider fake: serves the tenant token + app client creds."""

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
    """HubSpotClient that records the token it was handed via set_token."""

    def __init__(self):
        self.token = None

    def set_token(self, token):
        self.token = token

    # Protocol stubs (unused — we only test authenticate()).
    def list_companies(self, since): return []
    def list_contacts(self, since): return []
    def list_deals(self, since): return []
    def list_notes(self, since): return []


REF = "uplift/T1/hubspot"
CID_REF = "uplift/oauth/hubspot/client_id"
CSEC_REF = "uplift/oauth/hubspot/client_secret"


def _connector(secrets_values, writer=None):
    client = CapturingClient()
    conn = HubSpotConnector(
        "T1", client=client, secret_writer=writer,
        secrets=FakeSecrets(secrets_values), raw_sink=object(), structured_sink=object(),
    )
    return conn, client


def test_bare_token_back_compat():
    conn, client = _connector({REF: "pat-na1-legacy-token"})
    conn.authenticate()
    assert client.token == "pat-na1-legacy-token"


def test_fresh_oauth_envelope_no_refresh(monkeypatch):
    # post_form would blow up if called — proves no refresh on a fresh token.
    monkeypatch.setattr(oauth, "post_form",
                        lambda *a, **k: pytest.fail("refresh must not run on a fresh token"))
    env = oauth.oauth_secret_value(access_token="FRESH-AT", refresh_token="RT",
                                   expires_at=99_999_999_999)  # far future
    conn, client = _connector({REF: env})
    conn.authenticate()
    assert client.token == "FRESH-AT"


def test_expired_oauth_envelope_refreshes_and_writes_back(monkeypatch):
    monkeypatch.setattr(oauth, "post_form", lambda url, fields: {
        "access_token": "REFRESHED-AT", "refresh_token": "NEW-RT", "expires_in": 1800,
    })
    expired = oauth.oauth_secret_value(access_token="OLD-AT", refresh_token="OLD-RT",
                                       expires_at=1)  # epoch 1 = long past
    writer = RecordingWriter()
    conn, client = _connector(
        {REF: expired, CID_REF: "CID", CSEC_REF: "CSEC"}, writer=writer)
    conn.authenticate()
    # The client got the REFRESHED access token.
    assert client.token == "REFRESHED-AT"
    # The new envelope was written back to the SAME vault slot.
    assert REF in writer.put
    stored = json.loads(writer.put[REF])
    assert stored["access_token"] == "REFRESHED-AT"
    assert stored["refresh_token"] == "NEW-RT"
    assert stored["token_type"] == "oauth"


def test_expired_without_client_creds_fails_honestly(monkeypatch):
    monkeypatch.setattr(oauth, "post_form",
                        lambda *a, **k: pytest.fail("must not exchange without creds"))
    expired = oauth.oauth_secret_value(access_token="OLD-AT", refresh_token="OLD-RT",
                                       expires_at=1)
    # No client_id/client_secret provisioned.
    conn, client = _connector({REF: expired})
    from ingest.connectors.base import MissingTenantCredentialError
    with pytest.raises(MissingTenantCredentialError):
        conn.authenticate()
    assert client.token is None  # never handed a dead token


def test_refresh_without_writer_still_uses_new_token(monkeypatch):
    # No writer wired -> no write-back, but the refreshed token is still used.
    monkeypatch.setattr(oauth, "post_form", lambda url, fields: {
        "access_token": "REFRESHED-AT", "refresh_token": "NEW-RT", "expires_in": 1800,
    })
    expired = oauth.oauth_secret_value(access_token="OLD-AT", refresh_token="OLD-RT",
                                       expires_at=1)
    conn, client = _connector({REF: expired, CID_REF: "CID", CSEC_REF: "CSEC"})
    conn.authenticate()
    assert client.token == "REFRESHED-AT"
