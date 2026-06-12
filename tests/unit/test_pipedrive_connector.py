"""Unit: the Pipedrive connector + its OAuth envelope handling. FIXTURES only —
the source client is a fake fed from recorded Pipedrive v2 shapes and
oauth.post_form is monkeypatched, so ZERO live Pipedrive calls happen.

Covers:
  * provider registration (authorize/token URLs, PKCE, read-only scopes, refs)
  * OAuth exchange + ROTATING refresh — each refresh returns a NEW refresh_token;
    the connector persists the rotated token back to the vault (old overwritten)
  * api_domain capture on exchange/refresh + vault-envelope round-trip; authenticate()
    injects (bearer, api_domain) and refreshes a near-expired token
  * single-flight refresh: a peer that already rotated under the lock is ridden
    (no second refresh that would 400)
  * API v2 incremental: per-resource update_time watermark via the JSON cursor; the
    PipedriveRestClient builds updated_since + sort_by=update_time + cursor pagination
  * honest-503: the REST client requires api_domain (RuntimeError) and a 503 from the
    provider propagates (never a silent empty page)
  * normalization persons->contacts, organizations->companies, deals->deals,
    activities->activities
"""
import json

import pytest

from ingest.connectors import oauth
from ingest.connectors.base import MissingTenantCredentialError, SecretNotFoundError
from ingest.connectors.pipedrive import (
    PD_API_VERSION,
    PipedriveConnector,
    PipedriveRestClient,
)
from ingest.pipeline import InMemoryRawSink, InMemoryStructuredSink

TENANT = "11111111-1111-1111-1111-111111111111"
PD_REF = f"uplift/{TENANT}/pipedrive"
DOMAIN = "https://acme.pipedrive.com"
CID_REF = "uplift/oauth/pipedrive/client_id"
CSEC_REF = "uplift/oauth/pipedrive/client_secret"


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeSecrets:
    def __init__(self, values):
        self._values = dict(values)

    def get_secret(self, ref):
        if ref not in self._values:
            raise SecretNotFoundError(ref)
        return self._values[ref]

    def set(self, ref, value):
        self._values[ref] = value


class RecordingWriter:
    """A writer that ALSO updates the backing secrets store, so a re-read under the
    single-flight lock sees the rotated envelope (mirrors a real vault)."""

    def __init__(self, secrets=None):
        self.put = {}
        self._secrets = secrets

    def put_secret(self, ref, value):
        self.put[ref] = value
        if self._secrets is not None:
            self._secrets.set(ref, value)


class FakePDClient:
    """Records per-resource `since` floors + injected (token, api_domain); serves
    fixtures. Any resource with no fixture returns []."""

    def __init__(self, fixtures=None, field_defs=None):
        self.fixtures = fixtures or {}
        self.field_defs = field_defs or {}
        self.token = None
        self.api_domain = None
        self.since_seen = {}

    def set_token(self, token):
        self.token = token

    def set_api_domain(self, api_domain):
        self.api_domain = api_domain

    def _list(self, resource, since):
        self.since_seen[resource] = since
        return list(self.fixtures.get(resource, []))

    def list_organizations(self, since): return self._list("organizations", since)
    def list_persons(self, since): return self._list("persons", since)
    def list_deals(self, since): return self._list("deals", since)
    def list_activities(self, since): return self._list("activities", since)

    def fields(self, resource):
        return list(self.field_defs.get(resource, []))


def _connector(client, *, secrets, writer=None):
    return PipedriveConnector(
        TENANT, client=client, secrets=secrets,
        raw_sink=InMemoryRawSink(), structured_sink=InMemoryStructuredSink(),
        secret_writer=writer,
    )


# --------------------------------------------------------------------------- #
# Provider registry
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_pipedrive_provider_registered():
    p = oauth.get_provider("pipedrive")
    assert p is not None
    assert p.authorize_url == "https://oauth.pipedrive.com/oauth/authorize"
    assert p.token_url == "https://oauth.pipedrive.com/oauth/token"
    assert p.pkce is True
    for scope in ("base", "contacts:read", "deals:read", "activities:read"):
        assert scope in p.scopes
    assert p.client_id_ref == CID_REF
    assert p.client_secret_ref == CSEC_REF
    # all the OTHER providers survive the addition
    for other in ("hubspot", "gohighlevel", "salesforce", "microsoft"):
        assert oauth.get_provider(other) is not None


# --------------------------------------------------------------------------- #
# OAuth exchange / rotating refresh + api_domain persistence
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_exchange_code_captures_api_domain(monkeypatch):
    provider = oauth.get_provider("pipedrive")
    captured = {}

    def fake_post(url, fields):
        captured["url"] = url
        captured["fields"] = fields
        return {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600,
                "api_domain": DOMAIN}

    monkeypatch.setattr(oauth, "post_form", fake_post)
    tokens = oauth.exchange_code(
        provider, code="c", redirect_uri="https://api/cb",
        client_id="CID", client_secret="CSEC", code_verifier="VER", now=1000,
    )
    assert tokens["api_domain"] == DOMAIN
    assert tokens["access_token"] == "AT"
    assert tokens["expires_at"] == 1000 + 3600
    assert captured["fields"]["code_verifier"] == "VER"  # PKCE rides the exchange
    assert captured["url"] == provider.token_url


@pytest.mark.unit
def test_envelope_roundtrips_api_domain():
    value = oauth.oauth_secret_value(
        access_token="AT", refresh_token="RT", expires_at=123, api_domain=DOMAIN)
    parsed = oauth.parse_oauth_secret(value)
    assert parsed["api_domain"] == DOMAIN
    assert parsed["token_type"] == "oauth"
    # HubSpot's envelope (no api_domain) is unaffected.
    hs = json.loads(oauth.oauth_secret_value(access_token="a", refresh_token="b", expires_at=1))
    assert "api_domain" not in hs


@pytest.mark.unit
def test_refresh_rotates_refresh_token_and_keeps_api_domain(monkeypatch):
    provider = oauth.get_provider("pipedrive")
    # Pipedrive ROTATES the refresh token on every refresh.
    monkeypatch.setattr(oauth, "post_form", lambda url, fields: {
        "access_token": "AT2", "refresh_token": "NEW-RT", "expires_in": 3600,
        "api_domain": DOMAIN})
    new = oauth.refresh_access_token(
        provider, refresh_token="OLD-RT", client_id="CID", client_secret="CSEC", now=2000)
    assert new["access_token"] == "AT2"
    assert new["refresh_token"] == "NEW-RT"  # rotated, NOT the old one
    assert new["api_domain"] == DOMAIN


# --------------------------------------------------------------------------- #
# authenticate(): inject, refresh + ROTATED persistence, fallbacks
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_authenticate_injects_token_and_api_domain():
    env = oauth.oauth_secret_value(
        access_token="AT", refresh_token="RT", expires_at=0, api_domain=DOMAIN)
    client = FakePDClient()
    conn = _connector(client, secrets=FakeSecrets({PD_REF: env}))
    conn.authenticate()
    assert client.token == "AT"
    assert client.api_domain == DOMAIN


@pytest.mark.unit
def test_authenticate_refreshes_expired_and_persists_rotated_envelope(monkeypatch):
    env = oauth.oauth_secret_value(
        access_token="OLD", refresh_token="OLD-RT", expires_at=1, api_domain=DOMAIN)
    monkeypatch.setattr(oauth, "post_form", lambda url, fields: {
        "access_token": "NEW", "refresh_token": "NEW-RT", "expires_in": 3600,
        "api_domain": DOMAIN})
    secrets = FakeSecrets({PD_REF: env, CID_REF: "CID", CSEC_REF: "CSEC"})
    client = FakePDClient()
    writer = RecordingWriter(secrets)
    conn = _connector(client, secrets=secrets, writer=writer)
    conn.authenticate()
    assert client.token == "NEW"
    # the ROTATED refresh_token was persisted back (old overwritten), api_domain kept
    stored = json.loads(writer.put[PD_REF])
    assert stored["access_token"] == "NEW"
    assert stored["refresh_token"] == "NEW-RT"
    assert stored["api_domain"] == DOMAIN


@pytest.mark.unit
def test_single_flight_rides_peer_rotation_no_second_refresh(monkeypatch):
    # The stored envelope is expired, but a PEER already rotated it before we took the
    # lock — the re-read shows a fresh (un-expired) token, so we must NOT refresh again
    # (a second refresh with the now-invalid old token would 400).
    fresh = oauth.oauth_secret_value(
        access_token="PEER-AT", refresh_token="PEER-RT", expires_at=10**12, api_domain=DOMAIN)
    secrets = FakeSecrets({PD_REF: fresh, CID_REF: "CID", CSEC_REF: "CSEC"})
    monkeypatch.setattr(oauth, "post_form",
                        lambda *a, **k: pytest.fail("must not refresh — peer already rotated"))
    client = FakePDClient()
    conn = _connector(client, secrets=secrets, writer=RecordingWriter(secrets))
    # Force the "expired" entry path: hand authenticate an expired envelope, but the
    # vault re-read (under the lock) returns the peer's fresh one above.
    expired = oauth.oauth_secret_value(
        access_token="STALE", refresh_token="OLD-RT", expires_at=1, api_domain=DOMAIN)
    token, dom = conn._access_from_envelope(oauth.parse_oauth_secret(expired), PD_REF)
    assert token == "PEER-AT"
    assert dom == DOMAIN


@pytest.mark.unit
def test_authenticate_bare_token_fallback():
    client = FakePDClient()
    conn = _connector(client, secrets=FakeSecrets({PD_REF: "bare-api-token"}))
    conn.authenticate()
    assert client.token == "bare-api-token"
    assert client.api_domain is None  # no company host from a bare token


@pytest.mark.unit
def test_authenticate_missing_secret_is_hard_error():
    conn = _connector(FakePDClient(), secrets=FakeSecrets({}))
    with pytest.raises(MissingTenantCredentialError):
        conn.authenticate()


@pytest.mark.unit
def test_refresh_without_app_creds_fails_honestly(monkeypatch):
    env = oauth.oauth_secret_value(
        access_token="OLD", refresh_token="RT", expires_at=1, api_domain=DOMAIN)
    monkeypatch.setattr(oauth, "post_form",
                        lambda *a, **k: pytest.fail("must not refresh without app creds"))
    conn = _connector(FakePDClient(), secrets=FakeSecrets({PD_REF: env}))  # no client_id/secret
    with pytest.raises(MissingTenantCredentialError):
        conn.authenticate()


# --------------------------------------------------------------------------- #
# Incremental — per-resource update_time watermark
# --------------------------------------------------------------------------- #
def _authed(client, secrets_values=None):
    secrets_values = secrets_values or {PD_REF: "bare-token"}
    conn = _connector(client, secrets=FakeSecrets(secrets_values))
    conn.authenticate()
    return conn


@pytest.mark.unit
def test_pull_per_resource_floor_from_json_cursor():
    fixtures = {
        "organizations": [{"id": 1, "name": "Acme", "update_time": "2026-06-02T00:00:00Z"}],
        "persons": [{"id": 3, "name": "Jo", "emails": [{"value": "jo@x.com", "primary": True}],
                     "update_time": "2026-06-03T00:00:00Z"}],
    }
    client = FakePDClient(fixtures=fixtures)
    conn = _authed(client)
    cursor = json.dumps({"organizations": "2026-06-01T00:00:00Z",
                         "persons": "2026-05-01T00:00:00Z"})
    list(conn.pull(cursor))
    assert client.since_seen["organizations"] == "2026-06-01T00:00:00Z"
    assert client.since_seen["persons"] == "2026-05-01T00:00:00Z"
    assert client.since_seen["deals"] is None  # absent from cursor -> full pull
    nxt = json.loads(conn.next_cursor())
    assert nxt["organizations"] == "2026-06-02T00:00:00Z"
    assert nxt["persons"] == "2026-06-03T00:00:00Z"


@pytest.mark.unit
def test_pull_plain_string_cursor_is_floor_for_every_resource():
    client = FakePDClient()
    conn = _authed(client)
    list(conn.pull("2026-06-01T00:00:00Z"))
    for resource in ("organizations", "persons", "deals", "activities"):
        assert client.since_seen[resource] == "2026-06-01T00:00:00Z"


@pytest.mark.unit
def test_pull_none_cursor_full_backfill_no_floor():
    client = FakePDClient()
    conn = _authed(client)
    list(conn.pull(None))
    assert all(v is None for v in client.since_seen.values())


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_normalization_maps_each_resource_to_its_table():
    fixtures = {
        "organizations": [{"id": 1, "name": "Acme", "update_time": "2026-06-02T00:00:00Z"}],
        "persons": [{"id": 3, "first_name": "Jo", "last_name": "Lee", "name": "Jo Lee",
                     "emails": [{"value": "jo@acme.com", "primary": True}],
                     "phones": [{"value": "555", "primary": True}],
                     "org_id": 1, "update_time": "2026-06-02T00:00:00Z"}],
        "deals": [{"id": 7, "title": "Big Deal", "value": 1000, "currency": "USD",
                   "status": "open", "org_id": 1, "person_id": 3,
                   "update_time": "2026-06-02T00:00:00Z"}],
        "activities": [{"id": 9, "type": "call", "subject": "Ring", "note": "ring them",
                        "person_id": 3, "deal_id": 7, "update_time": "2026-06-02T00:00:00Z"}],
    }
    conn = _authed(FakePDClient(fixtures=fixtures))
    by_table = {}
    for rec in conn.pull(None):
        by_table.setdefault(rec.table, []).append(rec)

    org = by_table["companies"][0]
    assert org.row["name"] == "Acme" and org.ref_id == "1"

    contact = by_table["contacts"][0]
    assert contact.row["company_ref_id"] == "1"        # org_id -> company_ref_id
    assert contact.row["email"] == "jo@acme.com"       # primary email value
    assert contact.row["phone"] == "555"

    deal = by_table["deals"][0]
    assert deal.row["title"] == "Big Deal" and deal.row["amount"] == 1000.0
    assert deal.row["company_ref_id"] == "1" and deal.row["contact_ref_id"] == "3"
    assert deal.row["stage"] == "open"

    act = by_table["activities"][0]
    assert act.row["kind"] == "call"
    assert act.row["contact_ref_id"] == "3" and act.row["deal_ref_id"] == "7"


@pytest.mark.unit
def test_custom_field_labels_resolved_via_fields_v2():
    hashed = "a" * 40  # a custom-field hashed key (40 hex chars)
    fixtures = {"persons": [{"id": 3, "name": "Jo", hashed: "VIP",
                             "update_time": "2026-06-02T00:00:00Z"}]}
    field_defs = {"persons": [{"key": hashed, "name": "Tier"},
                              {"key": "name", "name": "Name"}]}  # standard key ignored
    conn = _authed(FakePDClient(fixtures=fixtures, field_defs=field_defs))
    rec = next(iter(conn.pull(None)))
    # the resolved custom-field label + value rides the text block
    assert "Tier: VIP" in rec.text_blocks[0]["text"]


# --------------------------------------------------------------------------- #
# Real PipedriveRestClient — v2 collection + Fields shapes (no network: _get faked)
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_rest_client_builds_query_with_updated_since_and_cursor():
    client = PipedriveRestClient()
    client.set_token("AT")
    client.set_api_domain(DOMAIN)
    urls = []
    pages = [
        {"data": [{"id": 1}], "additional_data": {"next_cursor": "CUR2"}},
        {"data": [{"id": 2}], "additional_data": {"next_cursor": None}},
    ]

    def fake_get(url):
        urls.append(url)
        return pages[len(urls) - 1]

    client._get = fake_get
    out = list(client.list_persons("2026-06-01T00:00:00Z"))
    assert [r["id"] for r in out] == [1, 2]
    first = urls[0]
    assert f"/api/{PD_API_VERSION}/persons?" in first
    assert "limit=500" in first
    assert "sort_by=update_time" in first
    assert "updated_since=2026-06-01T00%3A00%3A00Z" in first
    assert "cursor" not in urls[0]          # first page has no cursor
    assert "cursor=CUR2" in urls[1]         # second page rides next_cursor


@pytest.mark.unit
def test_rest_client_full_pull_omits_updated_since():
    client = PipedriveRestClient()
    client.set_token("AT")
    client.set_api_domain(DOMAIN)
    urls = []
    client._get = lambda url: (urls.append(url) or {"data": [], "additional_data": {}})
    list(client.list_deals(None))
    assert "updated_since" not in urls[0]


@pytest.mark.unit
def test_rest_client_requires_api_domain():
    client = PipedriveRestClient()
    client.set_token("AT")  # no api_domain
    with pytest.raises(RuntimeError, match="api_domain"):
        list(client.list_persons(None))


@pytest.mark.unit
def test_rest_client_503_propagates_honestly(monkeypatch):
    import urllib.error
    import urllib.request

    client = PipedriveRestClient()
    client.set_token("AT")
    client.set_api_domain(DOMAIN)

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable",
                                     hdrs=None, fp=None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    # A 503 is NOT retried/swallowed — it propagates (never a silent empty page that
    # would look like "no data" and drop real records).
    with pytest.raises(urllib.error.HTTPError) as exc:
        list(client.list_persons(None))
    assert exc.value.code == 503
