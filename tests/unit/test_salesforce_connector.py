"""Unit: the Salesforce connector + its OAuth envelope handling. FIXTURES only —
the source client is a fake fed from recorded SF shapes and oauth.post_form is
monkeypatched, so ZERO live Salesforce calls happen.

Covers:
  * OAuth exchange/refresh capture instance_url; the vault envelope round-trips it
    (instance_url is the per-org API host every SOQL call uses as its base)
  * authenticate() injects (bearer, instance_url) from the envelope, refreshes a
    near-expired token (persisting the new envelope incl. instance_url), and treats
    a bare string as a trivial session fallback (no instance_url)
  * SOQL incremental: per-object SystemModstamp watermark — each object is pulled
    with its OWN floor (JSON cursor) and next_cursor() emits the per-object maxes;
    the SalesforceRestClient builds `WHERE SystemModstamp > <literal> ORDER BY
    SystemModstamp` and pages via nextRecordsUrl
  * getDeleted tombstoning: deletions become tombstone records (logical `tombstones`
    table, kind="tombstone", deleted=True), and the full-backfill path (no floor)
    emits none; the REST client builds the /sobjects/{S}/deleted/ path
  * normalization Account->company, Contact/Lead->contact, Opportunity->deal,
    Task/Event->activity
"""
import json

import pytest

from ingest.connectors import oauth
from ingest.connectors.base import MissingTenantCredentialError, SecretNotFoundError
from ingest.connectors.salesforce import (
    SF_API_VERSION,
    SalesforceConnector,
    SalesforceRestClient,
)
from ingest.pipeline import InMemoryRawSink, InMemoryStructuredSink

TENANT = "11111111-1111-1111-1111-111111111111"
SF_REF = f"uplift/{TENANT}/salesforce"
INSTANCE = "https://acme.my.salesforce.com"
CID_REF = "uplift/oauth/salesforce/client_id"
CSEC_REF = "uplift/oauth/salesforce/client_secret"


# --------------------------------------------------------------------------- #
# Fakes
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


class FakeSFClient:
    """Records the per-object `since` floors + injected (token, instance_url); serves
    fixtures. Any object with no fixture returns []."""

    def __init__(self, fixtures=None, deletions=None):
        self.fixtures = fixtures or {}
        self.deletions = deletions or {}
        self.token = None
        self.instance_url = None
        self.since_seen = {}
        self.deleted_calls = []

    def set_token(self, token):
        self.token = token

    def set_instance_url(self, instance_url):
        self.instance_url = instance_url

    def _list(self, sobject, since):
        self.since_seen[sobject] = since
        return list(self.fixtures.get(sobject, []))

    def list_accounts(self, since): return self._list("Account", since)
    def list_contacts(self, since): return self._list("Contact", since)
    def list_leads(self, since): return self._list("Lead", since)
    def list_opportunities(self, since): return self._list("Opportunity", since)
    def list_tasks(self, since): return self._list("Task", since)
    def list_events(self, since): return self._list("Event", since)

    def list_deleted(self, sobject, start, end):
        self.deleted_calls.append((sobject, start, end))
        return list(self.deletions.get(sobject, []))


def _connector(client, *, secrets, writer=None):
    return SalesforceConnector(
        TENANT, client=client, secrets=secrets,
        raw_sink=InMemoryRawSink(), structured_sink=InMemoryStructuredSink(),
        secret_writer=writer,
    )


# --------------------------------------------------------------------------- #
# OAuth exchange/refresh + instance_url persistence
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_exchange_code_captures_instance_url(monkeypatch):
    provider = oauth.get_provider("salesforce")
    assert provider is not None and provider.pkce is True
    captured = {}

    def fake_post(url, fields):
        captured["url"] = url
        captured["fields"] = fields
        return {"access_token": "AT", "refresh_token": "RT", "instance_url": INSTANCE}

    monkeypatch.setattr(oauth, "post_form", fake_post)
    tokens = oauth.exchange_code(
        provider, code="c", redirect_uri="https://api/cb",
        client_id="CID", client_secret="CSEC", code_verifier="VER", now=1000,
    )
    assert tokens["instance_url"] == INSTANCE
    assert tokens["access_token"] == "AT"
    # PKCE verifier rides the exchange; the token URL is Salesforce's.
    assert captured["fields"]["code_verifier"] == "VER"
    assert captured["url"] == provider.token_url


@pytest.mark.unit
def test_envelope_roundtrips_instance_url():
    value = oauth.oauth_secret_value(
        access_token="AT", refresh_token="RT", expires_at=123, instance_url=INSTANCE)
    parsed = oauth.parse_oauth_secret(value)
    assert parsed["instance_url"] == INSTANCE
    assert parsed["token_type"] == "oauth"
    # HubSpot's envelope (no instance_url) is unaffected.
    hs = json.loads(oauth.oauth_secret_value(access_token="a", refresh_token="b", expires_at=1))
    assert "instance_url" not in hs


@pytest.mark.unit
def test_refresh_captures_instance_url(monkeypatch):
    provider = oauth.get_provider("salesforce")
    monkeypatch.setattr(oauth, "post_form", lambda url, fields: {
        "access_token": "AT2", "instance_url": INSTANCE})  # SF omits refresh_token on refresh
    new = oauth.refresh_access_token(
        provider, refresh_token="RT", client_id="CID", client_secret="CSEC", now=1000)
    assert new["access_token"] == "AT2"
    assert new["refresh_token"] == "RT"  # preserved
    assert new["instance_url"] == INSTANCE


@pytest.mark.unit
def test_authenticate_injects_token_and_instance_url():
    env = oauth.oauth_secret_value(
        access_token="AT", refresh_token="RT", expires_at=0, instance_url=INSTANCE)
    client = FakeSFClient()
    conn = _connector(client, secrets=FakeSecrets({SF_REF: env}))
    conn.authenticate()
    assert client.token == "AT"
    assert client.instance_url == INSTANCE


@pytest.mark.unit
def test_authenticate_refreshes_expired_and_persists_envelope(monkeypatch):
    # expires_at in the deep past -> is_expired True -> refresh.
    env = oauth.oauth_secret_value(
        access_token="OLD", refresh_token="RT", expires_at=1, instance_url=INSTANCE)
    monkeypatch.setattr(oauth, "post_form", lambda url, fields: {
        "access_token": "NEW", "instance_url": INSTANCE, "expires_in": 3600})
    client = FakeSFClient()
    writer = RecordingWriter()
    secrets = FakeSecrets({SF_REF: env, CID_REF: "CID", CSEC_REF: "CSEC"})
    conn = _connector(client, secrets=secrets, writer=writer)
    conn.authenticate()
    assert client.token == "NEW"
    # the refreshed envelope was persisted back, instance_url preserved
    stored = json.loads(writer.put[SF_REF])
    assert stored["access_token"] == "NEW"
    assert stored["refresh_token"] == "RT"
    assert stored["instance_url"] == INSTANCE


@pytest.mark.unit
def test_authenticate_bare_token_session_fallback():
    client = FakeSFClient()
    conn = _connector(client, secrets=FakeSecrets({SF_REF: "session-id-token"}))
    conn.authenticate()
    assert client.token == "session-id-token"
    assert client.instance_url is None  # no org host from a bare token


@pytest.mark.unit
def test_authenticate_missing_secret_is_hard_error():
    conn = _connector(FakeSFClient(), secrets=FakeSecrets({}))
    with pytest.raises(MissingTenantCredentialError):
        conn.authenticate()


@pytest.mark.unit
def test_refresh_without_app_creds_fails_honestly(monkeypatch):
    env = oauth.oauth_secret_value(
        access_token="OLD", refresh_token="RT", expires_at=1, instance_url=INSTANCE)
    monkeypatch.setattr(oauth, "post_form",
                        lambda *a, **k: pytest.fail("must not refresh without app creds"))
    conn = _connector(FakeSFClient(), secrets=FakeSecrets({SF_REF: env}))  # no client_id/secret
    with pytest.raises(MissingTenantCredentialError):
        conn.authenticate()


# --------------------------------------------------------------------------- #
# SOQL incremental — per-object watermark
# --------------------------------------------------------------------------- #
def _authed(client, secrets_values=None):
    secrets_values = secrets_values or {SF_REF: "bare-token"}
    conn = _connector(client, secrets=FakeSecrets(secrets_values))
    conn.authenticate()
    return conn


@pytest.mark.unit
def test_pull_per_object_floor_from_json_cursor():
    fixtures = {
        "Account": [{"Id": "001", "Name": "Acme", "SystemModstamp": "2026-06-02T00:00:00Z"}],
        "Contact": [{"Id": "003", "Name": "Jo", "Email": "jo@x.com",
                     "SystemModstamp": "2026-06-03T00:00:00Z"}],
    }
    client = FakeSFClient(fixtures=fixtures)
    conn = _authed(client)
    cursor = json.dumps({"Account": "2026-06-01T00:00:00Z", "Contact": "2026-05-01T00:00:00Z"})
    list(conn.pull(cursor))
    # each object queried with ITS OWN floor (not a shared one)
    assert client.since_seen["Account"] == "2026-06-01T00:00:00Z"
    assert client.since_seen["Contact"] == "2026-05-01T00:00:00Z"
    # objects absent from the cursor map get no floor (full pull)
    assert client.since_seen["Lead"] is None
    # next_cursor reflects the per-object max SystemModstamp seen this run
    nxt = json.loads(conn.next_cursor())
    assert nxt["Account"] == "2026-06-02T00:00:00Z"
    assert nxt["Contact"] == "2026-06-03T00:00:00Z"


@pytest.mark.unit
def test_pull_plain_string_cursor_is_floor_for_every_object():
    client = FakeSFClient()
    conn = _authed(client)
    list(conn.pull("2026-06-01T00:00:00Z"))
    for sobject in ("Account", "Contact", "Lead", "Opportunity", "Task", "Event"):
        assert client.since_seen[sobject] == "2026-06-01T00:00:00Z"


@pytest.mark.unit
def test_pull_none_cursor_full_backfill_no_floor_no_deletion_sweep():
    client = FakeSFClient(deletions={"Account": [{"id": "001", "deletedDate": "x"}]})
    conn = _authed(client)
    recs = list(conn.pull(None))
    assert all(v is None for v in client.since_seen.values())
    # a full backfill has nothing to tombstone -> getDeleted is never called
    assert client.deleted_calls == []
    assert [r for r in recs if r.kind == "tombstone"] == []


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_normalization_maps_each_object_to_its_table():
    fixtures = {
        "Account": [{"Id": "001", "Name": "Acme", "Website": "https://acme.com/x",
                     "Industry": "Tech", "SystemModstamp": "2026-06-02T00:00:00Z"}],
        "Contact": [{"Id": "003", "FirstName": "Jo", "LastName": "Lee", "Name": "Jo Lee",
                     "Email": "jo@acme.com", "Phone": "555", "AccountId": "001",
                     "SystemModstamp": "2026-06-02T00:00:00Z"}],
        "Lead": [{"Id": "00Q", "Name": "Pat Raw", "Email": "pat@x.com", "Company": "RawCo",
                  "Status": "Open", "SystemModstamp": "2026-06-02T00:00:00Z"}],
        "Opportunity": [{"Id": "006", "Name": "Big Deal", "StageName": "Prospecting",
                         "Amount": "1000", "AccountId": "001",
                         "SystemModstamp": "2026-06-02T00:00:00Z"}],
        "Task": [{"Id": "00T", "Subject": "Call", "Description": "ring them", "WhoId": "003",
                  "WhatId": "006", "SystemModstamp": "2026-06-02T00:00:00Z"}],
        "Event": [{"Id": "00U", "Subject": "Meet", "WhoId": "003",
                   "SystemModstamp": "2026-06-02T00:00:00Z"}],
    }
    conn = _authed(FakeSFClient(fixtures=fixtures))
    by_table = {}
    for rec in conn.pull(None):
        by_table.setdefault(rec.table, []).append(rec)

    acct = by_table["companies"][0]
    assert acct.row["name"] == "Acme" and acct.row["domain"] == "acme.com"

    contacts = {r.ref_id: r for r in by_table["contacts"]}
    assert contacts["003"].row["company_ref_id"] == "001"          # Contact has AccountId
    assert contacts["003"].row["email"] == "jo@acme.com"
    assert contacts["00Q"].row["company_ref_id"] is None           # Lead.Company is text, not an FK

    deal = by_table["deals"][0]
    assert deal.row["title"] == "Big Deal" and deal.row["amount"] == 1000.0
    assert deal.row["company_ref_id"] == "001"

    acts = {r.ref_id: r for r in by_table["activities"]}
    assert acts["00T"].row["kind"] == "task" and acts["00T"].row["contact_ref_id"] == "003"
    assert acts["00T"].row["deal_ref_id"] == "006"
    assert acts["00U"].row["kind"] == "event"


# --------------------------------------------------------------------------- #
# getDeleted tombstoning
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_getdeleted_emits_tombstones_with_floor():
    client = FakeSFClient(
        fixtures={"Account": [{"Id": "001", "Name": "Live",
                               "SystemModstamp": "2026-06-02T00:00:00Z"}]},
        deletions={"Account": [{"id": "001D", "deletedDate": "2026-06-01T12:00:00.000+0000"}]},
    )
    conn = _authed(client)
    recs = list(conn.pull("2026-06-01T00:00:00Z"))
    tombs = [r for r in recs if r.kind == "tombstone"]
    assert len(tombs) == 1
    t = tombs[0]
    assert t.table == "tombstones"            # NOT the live companies table
    assert t.ref_id == "001D"
    assert t.row["deleted"] is True
    assert t.row["object"] == "Account" and t.row["table"] == "companies"
    assert t.text_blocks == []                # a deletion produces no embedding
    assert t.updated_at == ""                 # deletions don't advance the live cursor
    # getDeleted was called per object that had a floor, with that floor as start
    assert ("Account", "2026-06-01T00:00:00Z") == client.deleted_calls[0][:2]


# --------------------------------------------------------------------------- #
# Real SalesforceRestClient — SOQL + getDeleted shapes (no network: _get faked)
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_rest_client_builds_soql_and_pages():
    client = SalesforceRestClient()
    client.set_token("AT")
    client.set_instance_url(INSTANCE)
    urls = []
    pages = [
        {"records": [{"Id": "001"}], "done": False, "nextRecordsUrl": "/next/01"},
        {"records": [{"Id": "002"}], "done": True},
    ]

    def fake_get(url):
        urls.append(url)
        return pages[len(urls) - 1]

    client._get = fake_get
    out = list(client.list_accounts("2026-06-01T12:00:00.000+0000"))
    assert [r["Id"] for r in out] == ["001", "002"]
    # first call: a SOQL query with the WHERE + ORDER BY; SystemModstamp normalized
    first = urls[0]
    assert f"/services/data/v{SF_API_VERSION}/query?" in first
    assert "WHERE+SystemModstamp+%3E+2026-06-01T12%3A00%3A00Z" in first
    assert "ORDER+BY+SystemModstamp" in first
    assert "FROM+Account" in first
    # second call rode the absolute nextRecordsUrl
    assert urls[1] == "/next/01"


@pytest.mark.unit
def test_rest_client_full_pull_omits_where():
    client = SalesforceRestClient()
    client.set_token("AT")
    client.set_instance_url(INSTANCE)
    urls = []
    client._get = lambda url: (urls.append(url) or {"records": [], "done": True})
    list(client.list_contacts(None))
    assert "WHERE" not in urls[0]


@pytest.mark.unit
def test_rest_client_getdeleted_path_and_parse():
    client = SalesforceRestClient()
    client.set_token("AT")
    client.set_instance_url(INSTANCE)
    seen = {}
    client._get = lambda url: (seen.update(url=url) or {
        "deletedRecords": [{"id": "001D", "deletedDate": "2026-06-01T00:00:00.000+0000"}]})
    out = list(client.list_deleted("Account", "2026-06-01T00:00:00Z", "2026-06-10T00:00:00Z"))
    assert out[0]["id"] == "001D"
    assert f"/services/data/v{SF_API_VERSION}/sobjects/Account/deleted/?" in seen["url"]
    assert "start=2026-06-01T00%3A00%3A00Z" in seen["url"]


@pytest.mark.unit
def test_rest_client_requires_instance_url():
    client = SalesforceRestClient()
    client.set_token("AT")  # no instance_url
    with pytest.raises(RuntimeError, match="instance_url"):
        list(client.list_accounts(None))
