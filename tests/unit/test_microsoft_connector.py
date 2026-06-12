"""Unit: the MicrosoftConnector — OAuth-aware authenticate(), Graph DELTA-QUERY
sync + per-resource cursor, @removed tombstones, the 410/expired-deltaLink full
resync, and the honest failure when unconfigured. FIXTURES only: the Graph client
is a FAKE fed from recorded delta shapes and oauth.post_form is monkeypatched, so
ZERO live Graph / login.microsoftonline.com calls happen.
"""
import json

import pytest

from ingest.connectors import oauth
from ingest.connectors.base import MissingTenantCredentialError, SecretNotFoundError
from ingest.connectors.microsoft import (
    DeltaLinkExpired,
    MicrosoftConnector,
    _decode_cursor,
    _encode_cursor,
)

TENANT = "T1"
REF = "uplift/T1/microsoft"
CID_REF = "uplift/oauth/microsoft/client_id"
CSEC_REF = "uplift/oauth/microsoft/client_secret"


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


class FakeGraph:
    """MicrosoftGraphClient fake. `script` maps resource -> list of
    (expected_delta_link, (items, new_link)) responses consumed in order. A
    response whose tuple is the DeltaLinkExpired class is RAISED instead."""

    def __init__(self, script):
        self._script = {k: list(v) for k, v in script.items()}
        self.calls = []
        self.token = None

    def set_token(self, token):
        self.token = token

    def delta(self, resource, delta_link):
        self.calls.append((resource, delta_link))
        queue = self._script.get(resource) or [(None, ([], ""))]
        _expected, result = queue.pop(0)
        if result is DeltaLinkExpired:
            raise DeltaLinkExpired("expired (fixture)")
        return result


def _connector(secrets_values, client, writer=None):
    return MicrosoftConnector(
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
    links = {"messages": "DLm", "events": "DLe", "contacts": "DLc"}
    enc = _encode_cursor(high_water="2024-06-01T00:00:00Z", links=links)
    assert _decode_cursor(enc) == links


def test_cursor_codec_tolerates_garbage():
    assert _decode_cursor(None) == {}
    assert _decode_cursor("") == {}
    assert _decode_cursor("not json") == {}
    assert _decode_cursor("[1,2,3]") == {}


def test_cursor_is_monotonic_for_pipeline_advance():
    # the leading high-water makes two cursor strings compare lexicographically so
    # the pipeline's `record.updated_at > since` advances across runs.
    earlier = _encode_cursor(high_water="2024-06-01T00:00:00Z", links={"messages": "Z"})
    later = _encode_cursor(high_water="2024-06-02T00:00:00Z", links={"messages": "A"})
    assert later > earlier


# --------------------------------------------------------------------------- #
# authenticate() — OAuth-aware with honest failures
# --------------------------------------------------------------------------- #
def test_authenticate_missing_secret_is_hard_error():
    conn = _connector({}, FakeGraph({}))  # no per-tenant slot
    with pytest.raises(MissingTenantCredentialError):
        conn.authenticate()


def test_authenticate_empty_secret_is_hard_error():
    conn = _connector({REF: ""}, FakeGraph({}))
    with pytest.raises(MissingTenantCredentialError):
        conn.authenticate()


def test_bare_token_back_compat():
    client = FakeGraph({})
    conn = _connector({REF: "ms-bare-access-token"}, client)
    conn.authenticate()
    assert client.token == "ms-bare-access-token"


def test_fresh_oauth_envelope_uses_access_token(monkeypatch):
    monkeypatch.setattr(oauth, "post_form",
                        lambda *a, **k: pytest.fail("no refresh on a fresh token"))
    client = FakeGraph({})
    conn = _connector({REF: _fresh_envelope()}, client)
    conn.authenticate()
    assert client.token == "AT"


def test_expired_oauth_refreshes_and_writes_back(monkeypatch):
    monkeypatch.setattr(oauth, "post_form", lambda url, fields: {
        "access_token": "REFRESHED-AT", "refresh_token": "NEW-RT", "expires_in": 3600,
    })
    expired = oauth.oauth_secret_value(access_token="OLD", refresh_token="OLD-RT", expires_at=1)
    writer = RecordingWriter()
    client = FakeGraph({})
    conn = _connector({REF: expired, CID_REF: "CID", CSEC_REF: "CSEC"}, client, writer=writer)
    conn.authenticate()
    assert client.token == "REFRESHED-AT"
    stored = json.loads(writer.put[REF])
    assert stored["access_token"] == "REFRESHED-AT"
    assert stored["refresh_token"] == "NEW-RT"
    assert stored["token_type"] == "oauth"


def test_expired_without_app_creds_fails_honestly(monkeypatch):
    # "honest-503 unconfigured": an expired token with NO app client creds must
    # raise (the route surfaces this as a reconnect), never ride a dead token.
    monkeypatch.setattr(oauth, "post_form",
                        lambda *a, **k: pytest.fail("must not exchange without creds"))
    expired = oauth.oauth_secret_value(access_token="OLD", refresh_token="OLD-RT", expires_at=1)
    client = FakeGraph({})
    conn = _connector({REF: expired}, client)  # no client_id/secret provisioned
    with pytest.raises(MissingTenantCredentialError):
        conn.authenticate()
    assert client.token is None  # never handed a dead token


# --------------------------------------------------------------------------- #
# delta sync + cursor
# --------------------------------------------------------------------------- #
def _authed(client, secrets_values=None):
    conn = _connector(secrets_values or {REF: "bare-token"}, client)
    conn.authenticate()
    return conn


def test_delta_sync_normalizes_and_captures_cursor():
    msg = {"id": "m1", "subject": "Hi", "bodyPreview": "hello there",
           "from": {"emailAddress": {"address": "a@x.com"}},
           "receivedDateTime": "2024-06-01T10:00:00Z",
           "lastModifiedDateTime": "2024-06-01T10:00:00Z"}
    evt = {"id": "e1", "subject": "Sync mtg", "bodyPreview": "agenda",
           "start": {"dateTime": "2024-06-02T09:00:00"},
           "organizer": {"emailAddress": {"address": "boss@x.com"}},
           "lastModifiedDateTime": "2024-06-02T08:00:00Z"}
    contact = {"id": "c1", "displayName": "Jane Doe",
               "emailAddresses": [{"address": "jane@acme.com"}],
               "businessPhones": ["+15551234"], "companyName": "Acme Inc",
               "lastModifiedDateTime": "2024-06-03T00:00:00Z"}
    client = FakeGraph({
        "messages": [(None, ([msg], "DL_m_1"))],
        "events": [(None, ([evt], "DL_e_1"))],
        "contacts": [(None, ([contact], "DL_c_1"))],
    })
    conn = _authed(client)
    records = list(conn.pull(None))

    by_table = {}
    for r in records:
        by_table.setdefault(r.table, []).append(r)
    # message + event -> activities; contact -> contacts; org -> companies
    assert {r.row["kind"] for r in by_table["activities"]} == {"email", "meeting"}
    assert by_table["contacts"][0].row["name"] == "Jane Doe"
    assert by_table["contacts"][0].row["email"] == "jane@acme.com"
    assert by_table["contacts"][0].row["phone"] == "+15551234"
    assert by_table["contacts"][0].row["company_ref_id"] == "Acme Inc"
    company = by_table["companies"][0]
    assert company.row["name"] == "Acme Inc"
    assert company.row["domain"] == "acme.com"  # derived from the contact email

    # cursor carries each resource's new deltaLink, stamped on every record
    cur = _decode_cursor(conn.next_cursor)
    assert cur == {"messages": "DL_m_1", "events": "DL_e_1", "contacts": "DL_c_1"}
    assert all(r.updated_at == conn.next_cursor for r in records)


def test_second_pull_sends_stored_delta_links():
    client = FakeGraph({
        "messages": [("DL_m_1", ([], "DL_m_2"))],
        "events": [("DL_e_1", ([], "DL_e_2"))],
        "contacts": [("DL_c_1", ([], "DL_c_2"))],
    })
    conn = _authed(client)
    prior = _encode_cursor(high_water="2024-06-03T00:00:00Z",
                           links={"messages": "DL_m_1", "events": "DL_e_1", "contacts": "DL_c_1"})
    list(conn.pull(prior))
    # the client received each resource's stored deltaLink (incremental, not full)
    assert ("messages", "DL_m_1") in client.calls
    assert ("events", "DL_e_1") in client.calls
    assert ("contacts", "DL_c_1") in client.calls
    assert _decode_cursor(conn.next_cursor)["messages"] == "DL_m_2"


# --------------------------------------------------------------------------- #
# @removed tombstones
# --------------------------------------------------------------------------- #
def test_removed_tombstone_is_skipped_but_cursor_advances():
    live = {"id": "m1", "subject": "live", "lastModifiedDateTime": "2024-06-01T10:00:00Z"}
    tombstone = {"id": "m0", "@removed": {"reason": "deleted"}}
    client = FakeGraph({
        "messages": [(None, ([live, tombstone], "DL_m_2"))],
        "events": [(None, ([], "DL_e_1"))],
        "contacts": [(None, ([], "DL_c_1"))],
    })
    conn = _authed(client)
    records = list(conn.pull(None))
    refs = {r.ref_id for r in records}
    assert "m1" in refs
    assert "m0" not in refs  # the tombstone never becomes a row
    assert _decode_cursor(conn.next_cursor)["messages"] == "DL_m_2"


# --------------------------------------------------------------------------- #
# 410 Gone / invalid deltaLink -> full resync
# --------------------------------------------------------------------------- #
def test_expired_delta_link_falls_back_to_full_resync():
    msg = {"id": "m1", "subject": "after resync", "lastModifiedDateTime": "2024-06-01T10:00:00Z"}
    client = FakeGraph({
        # first call (with the stale link) raises 410; the connector retries with None
        "messages": [("DL_m_stale", DeltaLinkExpired),
                     (None, ([msg], "DL_m_fresh"))],
        "events": [("DL_e_1", ([], "DL_e_1"))],
        "contacts": [("DL_c_1", ([], "DL_c_1"))],
    })
    conn = _authed(client)
    prior = _encode_cursor(high_water="2024-06-01T00:00:00Z",
                           links={"messages": "DL_m_stale", "events": "DL_e_1", "contacts": "DL_c_1"})
    records = list(conn.pull(prior))
    # the connector recovered: stale link -> 410 -> full resync (delta_link=None)
    assert ("messages", "DL_m_stale") in client.calls
    assert ("messages", None) in client.calls
    assert {r.ref_id for r in records if r.table == "activities"} == {"m1"}
    assert _decode_cursor(conn.next_cursor)["messages"] == "DL_m_fresh"


def test_pull_before_authenticate_raises():
    conn = _connector({REF: "bare-token"}, FakeGraph({}))
    with pytest.raises(RuntimeError):
        list(conn.pull(None))
