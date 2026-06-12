"""Unit: the GoogleConnector — OAuth-aware authenticate(), Calendar + People
SYNC-TOKEN sync + per-resource cursor, deletion tombstones (event status=cancelled
/ contact metadata.deleted), the 410/EXPIRED_SYNC_TOKEN full resync, and the honest
failure when unconfigured. FIXTURES only: the Google client is a FAKE fed from
recorded sync shapes and oauth.post_form is monkeypatched, so ZERO live Google /
oauth2.googleapis.com calls happen.
"""
import json

import pytest

from ingest.connectors import oauth
from ingest.connectors.base import MissingTenantCredentialError, SecretNotFoundError
from ingest.connectors.google import (
    GoogleConnector,
    SyncTokenExpired,
    _decode_cursor,
    _encode_cursor,
)

TENANT = "T1"
REF = "uplift/T1/google"
CID_REF = "uplift/oauth/google/client_id"
CSEC_REF = "uplift/oauth/google/client_secret"


# --------------------------------------------------------------------------- #
# fakes
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


class FakeGoogle:
    """GoogleClient fake. `script` maps resource -> list of (expected_sync_token,
    (items, new_token)) responses consumed in order. A response whose tuple is the
    SyncTokenExpired class is RAISED instead."""

    def __init__(self, script):
        self._script = {k: list(v) for k, v in script.items()}
        self.calls = []
        self.token = None

    def set_token(self, token):
        self.token = token

    def sync(self, resource, sync_token):
        self.calls.append((resource, sync_token))
        queue = self._script.get(resource) or [(None, ([], ""))]
        _expected, result = queue.pop(0)
        if result is SyncTokenExpired:
            raise SyncTokenExpired("expired (fixture)")
        return result


def _connector(secrets_values, client, writer=None):
    return GoogleConnector(
        TENANT, client=client, secret_writer=writer,
        secrets=FakeSecrets(secrets_values), raw_sink=object(), structured_sink=object(),
    )


def _fresh_envelope():
    return oauth.oauth_secret_value(access_token="AT", refresh_token="RT",
                                    expires_at=99_999_999_999)


# --------------------------------------------------------------------------- #
# cursor codec
# --------------------------------------------------------------------------- #
def test_cursor_codec_roundtrips():
    tokens = {"events": "STe", "contacts": "STc"}
    enc = _encode_cursor(high_water="2024-06-01T00:00:00Z", tokens=tokens)
    assert _decode_cursor(enc) == tokens


def test_cursor_codec_tolerates_garbage():
    assert _decode_cursor(None) == {}
    assert _decode_cursor("") == {}
    assert _decode_cursor("not json") == {}
    assert _decode_cursor("[1,2,3]") == {}


def test_cursor_is_monotonic_for_pipeline_advance():
    earlier = _encode_cursor(high_water="2024-06-01T00:00:00Z", tokens={"events": "Z"})
    later = _encode_cursor(high_water="2024-06-02T00:00:00Z", tokens={"events": "A"})
    assert later > earlier


# --------------------------------------------------------------------------- #
# authenticate() — OAuth-aware with honest failures
# --------------------------------------------------------------------------- #
def test_authenticate_missing_secret_is_hard_error():
    conn = _connector({}, FakeGoogle({}))  # no per-tenant slot
    with pytest.raises(MissingTenantCredentialError):
        conn.authenticate()


def test_authenticate_empty_secret_is_hard_error():
    conn = _connector({REF: ""}, FakeGoogle({}))
    with pytest.raises(MissingTenantCredentialError):
        conn.authenticate()


def test_bare_token_back_compat():
    client = FakeGoogle({})
    conn = _connector({REF: "google-bare-access-token"}, client)
    conn.authenticate()
    assert client.token == "google-bare-access-token"


def test_fresh_oauth_envelope_uses_access_token(monkeypatch):
    monkeypatch.setattr(oauth, "post_form",
                        lambda *a, **k: pytest.fail("no refresh on a fresh token"))
    client = FakeGoogle({})
    conn = _connector({REF: _fresh_envelope()}, client)
    conn.authenticate()
    assert client.token == "AT"


def test_expired_oauth_refreshes_and_writes_back(monkeypatch):
    # Google does NOT roll the refresh_token — the old one is preserved in the
    # written-back envelope.
    monkeypatch.setattr(oauth, "post_form", lambda url, fields: {
        "access_token": "REFRESHED-AT", "expires_in": 3599,
    })
    expired = oauth.oauth_secret_value(access_token="OLD", refresh_token="KEEP-RT", expires_at=1)
    writer = RecordingWriter()
    client = FakeGoogle({})
    conn = _connector({REF: expired, CID_REF: "CID", CSEC_REF: "CSEC"}, client, writer=writer)
    conn.authenticate()
    assert client.token == "REFRESHED-AT"
    stored = json.loads(writer.put[REF])
    assert stored["access_token"] == "REFRESHED-AT"
    assert stored["refresh_token"] == "KEEP-RT"  # preserved
    assert stored["token_type"] == "oauth"


def test_expired_without_app_creds_fails_honestly(monkeypatch):
    # "honest-503 unconfigured": an expired token with NO app client creds must
    # raise (the route surfaces this as a reconnect), never ride a dead token.
    monkeypatch.setattr(oauth, "post_form",
                        lambda *a, **k: pytest.fail("must not exchange without creds"))
    expired = oauth.oauth_secret_value(access_token="OLD", refresh_token="OLD-RT", expires_at=1)
    client = FakeGoogle({})
    conn = _connector({REF: expired}, client)  # no client_id/secret provisioned
    with pytest.raises(MissingTenantCredentialError):
        conn.authenticate()
    assert client.token is None  # never handed a dead token


# --------------------------------------------------------------------------- #
# sync-token sync + cursor
# --------------------------------------------------------------------------- #
def _authed(client, secrets_values=None):
    conn = _connector(secrets_values or {REF: "bare-token"}, client)
    conn.authenticate()
    return conn


def test_sync_normalizes_and_captures_cursor():
    evt = {"id": "e1", "summary": "Sync mtg", "description": "agenda",
           "start": {"dateTime": "2024-06-02T09:00:00Z"},
           "organizer": {"email": "boss@x.com"}, "status": "confirmed",
           "updated": "2024-06-02T08:00:00Z"}
    contact = {"resourceName": "people/c1",
               "names": [{"displayName": "Jane Doe"}],
               "emailAddresses": [{"value": "jane@acme.com"}],
               "phoneNumbers": [{"value": "+15551234"}],
               "organizations": [{"name": "Acme Inc", "title": "VP"}],
               "metadata": {"sources": [{"updateTime": "2024-06-03T00:00:00Z"}]}}
    client = FakeGoogle({
        "events": [(None, ([evt], "ST_e_1"))],
        "contacts": [(None, ([contact], "ST_c_1"))],
    })
    conn = _authed(client)
    records = list(conn.pull(None))

    by_table = {}
    for r in records:
        by_table.setdefault(r.table, []).append(r)
    # event -> activities(meeting); contact -> contacts; org -> companies
    assert {r.row["kind"] for r in by_table["activities"]} == {"meeting"}
    assert by_table["contacts"][0].row["name"] == "Jane Doe"
    assert by_table["contacts"][0].row["email"] == "jane@acme.com"
    assert by_table["contacts"][0].row["phone"] == "+15551234"
    assert by_table["contacts"][0].row["company_ref_id"] == "Acme Inc"
    assert by_table["contacts"][0].ref_id == "people/c1"
    company = by_table["companies"][0]
    assert company.row["name"] == "Acme Inc"
    assert company.row["domain"] == "acme.com"  # derived from the contact email

    # cursor carries each resource's new syncToken, stamped on every record
    cur = _decode_cursor(conn.next_cursor)
    assert cur == {"events": "ST_e_1", "contacts": "ST_c_1"}
    assert all(r.updated_at == conn.next_cursor for r in records)


def test_all_day_event_uses_date():
    evt = {"id": "e2", "summary": "Offsite", "start": {"date": "2024-07-01"},
           "status": "confirmed", "updated": "2024-06-30T00:00:00Z"}
    client = FakeGoogle({"events": [(None, ([evt], "ST_e_2"))], "contacts": [(None, ([], "ST_c"))]})
    conn = _authed(client)
    records = list(conn.pull(None))
    activity = next(r for r in records if r.table == "activities")
    assert "2024-07-01" in activity.row["body"]


def test_second_pull_sends_stored_sync_tokens():
    client = FakeGoogle({
        "events": [("ST_e_1", ([], "ST_e_2"))],
        "contacts": [("ST_c_1", ([], "ST_c_2"))],
    })
    conn = _authed(client)
    prior = _encode_cursor(high_water="2024-06-03T00:00:00Z",
                           tokens={"events": "ST_e_1", "contacts": "ST_c_1"})
    list(conn.pull(prior))
    assert ("events", "ST_e_1") in client.calls
    assert ("contacts", "ST_c_1") in client.calls
    assert _decode_cursor(conn.next_cursor)["events"] == "ST_e_2"


# --------------------------------------------------------------------------- #
# deletion tombstones
# --------------------------------------------------------------------------- #
def test_cancelled_event_is_skipped_but_cursor_advances():
    live = {"id": "e1", "summary": "live", "status": "confirmed",
            "start": {"dateTime": "2024-06-01T10:00:00Z"}, "updated": "2024-06-01T10:00:00Z"}
    cancelled = {"id": "e0", "status": "cancelled"}
    client = FakeGoogle({
        "events": [(None, ([live, cancelled], "ST_e_2"))],
        "contacts": [(None, ([], "ST_c_1"))],
    })
    conn = _authed(client)
    records = list(conn.pull(None))
    refs = {r.ref_id for r in records}
    assert "e1" in refs
    assert "e0" not in refs  # cancelled event never becomes a row
    assert _decode_cursor(conn.next_cursor)["events"] == "ST_e_2"


def test_deleted_contact_is_skipped_but_cursor_advances():
    live = {"resourceName": "people/c1", "names": [{"displayName": "Live"}],
            "metadata": {"sources": [{"updateTime": "2024-06-01T00:00:00Z"}]}}
    deleted = {"resourceName": "people/c0", "metadata": {"deleted": True}}
    client = FakeGoogle({
        "events": [(None, ([], "ST_e_1"))],
        "contacts": [(None, ([live, deleted], "ST_c_2"))],
    })
    conn = _authed(client)
    records = list(conn.pull(None))
    refs = {r.ref_id for r in records}
    assert "people/c1" in refs
    assert "people/c0" not in refs  # metadata.deleted contact never becomes a row
    assert _decode_cursor(conn.next_cursor)["contacts"] == "ST_c_2"


# --------------------------------------------------------------------------- #
# stale syncToken -> full resync (Calendar 410 / People EXPIRED_SYNC_TOKEN)
# --------------------------------------------------------------------------- #
def test_expired_event_sync_token_falls_back_to_full_resync():
    evt = {"id": "e1", "summary": "after resync", "status": "confirmed",
           "start": {"dateTime": "2024-06-01T10:00:00Z"}, "updated": "2024-06-01T10:00:00Z"}
    client = FakeGoogle({
        "events": [("ST_e_stale", SyncTokenExpired),
                   (None, ([evt], "ST_e_fresh"))],
        "contacts": [("ST_c_1", ([], "ST_c_1"))],
    })
    conn = _authed(client)
    prior = _encode_cursor(high_water="2024-06-01T00:00:00Z",
                           tokens={"events": "ST_e_stale", "contacts": "ST_c_1"})
    records = list(conn.pull(prior))
    assert ("events", "ST_e_stale") in client.calls
    assert ("events", None) in client.calls  # recovered via full resync
    assert {r.ref_id for r in records if r.table == "activities"} == {"e1"}
    assert _decode_cursor(conn.next_cursor)["events"] == "ST_e_fresh"


def test_expired_contacts_sync_token_falls_back_to_full_resync():
    contact = {"resourceName": "people/c1", "names": [{"displayName": "Recovered"}],
               "metadata": {"sources": [{"updateTime": "2024-06-01T00:00:00Z"}]}}
    client = FakeGoogle({
        "events": [("ST_e_1", ([], "ST_e_1"))],
        "contacts": [("ST_c_stale", SyncTokenExpired),
                     (None, ([contact], "ST_c_fresh"))],
    })
    conn = _authed(client)
    prior = _encode_cursor(high_water="2024-06-01T00:00:00Z",
                           tokens={"events": "ST_e_1", "contacts": "ST_c_stale"})
    records = list(conn.pull(prior))
    assert ("contacts", "ST_c_stale") in client.calls
    assert ("contacts", None) in client.calls
    assert {r.ref_id for r in records if r.table == "contacts"} == {"people/c1"}
    assert _decode_cursor(conn.next_cursor)["contacts"] == "ST_c_fresh"


def test_pull_before_authenticate_raises():
    conn = _connector({REF: "bare-token"}, FakeGoogle({}))
    with pytest.raises(RuntimeError):
        list(conn.pull(None))
