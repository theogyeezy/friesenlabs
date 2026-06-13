"""Unit tests for the GoHighLevel full-extract client (ingest/connectors/gohighlevel_full.py).

No network: most tests inject a fake ``_get``; the 429-backoff + Version-header tests monkeypatch
``urllib.request.urlopen`` (with an injected no-op sleep) so the retry path runs without sleeping.
"""
import urllib.error

import pytest

from ingest.connectors.gohighlevel_full import GoHighLevelFullClient, _normalize

pytestmark = pytest.mark.unit


class _Resp:
    def __init__(self, body: str):
        self._b = body.encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _client():
    c = GoHighLevelFullClient(sleep=lambda _s: None)
    c.set_credentials("test-token", "loc-1")
    return c


# --- normalize ----------------------------------------------------------- #
def test_normalize_flattens_customfields_flags_media_and_associations():
    raw = {
        "id": "9", "firstName": "Ada", "headshotUrl": "https://x/p.png",
        "customFields": [{"id": "cf1", "value": "vip"}, {"id": "cf2", "value": "https://x/intro.mp4"}],
        "associations": [{"objectKey": "company", "recordId": "100"}],
        "dateUpdated": "2026-06-01T00:00:00Z",
    }
    rec = _normalize("contacts", raw)
    assert rec.source_ref_id == "9"
    assert rec.properties["firstName"] == "Ada"
    assert rec.properties["cf_cf1"] == "vip"
    assert rec.properties["cf_cf2"] == "https://x/intro.mp4"  # media URL kept verbatim
    assert set(rec.properties["_media_refs"]) == {"headshotUrl", "cf_cf2"}  # flagged, not fetched
    assert rec.associations == {"company": ["100"]}
    assert rec.updated_at == "2026-06-01T00:00:00Z"


# --- pagination (startAfter / startAfterId) ------------------------------ #
def test_list_records_paginates_via_startafter_cursor():
    c = _client()
    pages = [
        {"contacts": [{"id": "1", "dateUpdated": "u1"}], "meta": {"startAfter": "100", "startAfterId": "1"}},
        {"contacts": [{"id": "2", "dateUpdated": "u2"}], "meta": {}},
    ]
    seen = []

    def fake_get(path, params=None, *, version=None):
        seen.append(params)
        return pages[0] if (params or {}).get("startAfter") is None else pages[1]

    c._get = fake_get  # type: ignore[assignment]
    recs = list(c.list_records("contacts", location_id="loc-1"))
    assert [r.source_ref_id for r in recs] == ["1", "2"]
    assert seen[0]["locationId"] == "loc-1"          # location-scoped
    assert seen[1]["startAfter"] == "100" and seen[1]["startAfterId"] == "1"  # cursors threaded from meta


def test_list_records_incremental_seeds_startafter_with_epoch_millis():
    c = _client()
    captured = {}

    def fake_get(path, params=None, *, version=None):
        captured.update(params or {})
        return {"contacts": [], "meta": {}}

    c._get = fake_get  # type: ignore[assignment]
    list(c.list_records("contacts", location_id="loc-1", since="2026-06-01T00:00:00Z"))
    assert captured["startAfter"].isdigit()  # epoch-millis seed, not ISO


# --- 429 backoff + Version header (real _get, mocked urlopen) ------------- #
def test_get_retries_on_429_then_succeeds(monkeypatch):
    c = _client()
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "rate", {"Retry-After": "0"}, None)
        return _Resp('{"contacts": [], "meta": {}}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    out = c._get("/contacts/", {"locationId": "loc-1"}, version="2021-07-28")
    assert out == {"contacts": [], "meta": {}}
    assert calls["n"] == 2  # retried once after the 429


def test_get_sends_per_resource_version_header(monkeypatch):
    c = _client()
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["version"] = req.get_header("Version")
        seen["auth"] = req.get_header("Authorization")
        return _Resp('{"x": 1}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    c._get("/conversations/", {"locationId": "loc-1"}, version="2021-04-15")
    assert seen["version"] == "2021-04-15"            # per-resource Version pinned
    assert seen["auth"] == "Bearer test-token"


def test_get_sends_a_nondefault_user_agent(monkeypatch):
    # GHL's Cloudflare BANS urllib's default UA (error 1010 -> 403 on every call); the client must
    # send a named UA. LIVE-CONFIRMED 2026-06-12. Regression guard so the header is never dropped.
    c = _client()
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["ua"] = req.get_header("User-agent")
        return _Resp('{"x": 1}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    c._get("/contacts/", {"locationId": "loc-1"})
    assert seen["ua"] and not seen["ua"].lower().startswith("python-")  # not the banned default


# --- per-resource path / location-param overrides (LIVE-CONFIRMED) -------- #
def test_opportunities_uses_search_path_and_snake_location_param():
    c = _client()
    seen = {}

    def fake_get(path, params=None, *, version=None):
        seen["path"] = path
        seen["params"] = params
        return {"opportunities": [{"id": "o1"}], "meta": {}}

    c._get = fake_get  # type: ignore[assignment]
    recs = list(c.list_records("opportunities", location_id="loc-1"))
    assert seen["path"] == "/opportunities/search"       # /search subpath
    assert "location_id" in seen["params"] and "locationId" not in seen["params"]  # snake_case
    assert recs[0].source_ref_id == "o1"


def test_conversations_uses_search_path():
    c = _client()
    seen = {}
    c._get = lambda path, params=None, *, version=None: (  # type: ignore[assignment]
        seen.update(path=path) or {"conversations": [], "meta": {}})
    list(c.list_records("conversations", location_id="loc-1"))
    assert seen["path"] == "/conversations/search"


def test_calendars_is_a_flat_list_without_pagination_params():
    # calendars 422s on limit/startAfter; list_records must pull it as one flat page with no paging.
    c = _client()
    seen = {}

    def fake_get(path, params=None, *, version=None):
        seen["params"] = params
        return {"calendars": [{"id": "cal1"}]}  # NO meta cursors

    c._get = fake_get  # type: ignore[assignment]
    recs = list(c.list_records("calendars", location_id="loc-1"))
    assert "limit" not in seen["params"] and "startAfter" not in seen["params"]
    assert [r.source_ref_id for r in recs] == ["cal1"]


def test_get_requires_a_token():
    c = GoHighLevelFullClient()  # no credentials
    with pytest.raises(RuntimeError, match="no token"):
        c._get("/contacts/")


# --- object discovery ---------------------------------------------------- #
def test_discover_object_types_unions_standard_and_user_custom_objects():
    c = _client()
    # GHL user-defined custom objects are namespaced (custom_objects.<name>); built-in schema keys
    # (contact/opportunity/business) are NOT and must be filtered out (they 404 on a records pull).
    c._get = lambda path, params=None, *, version=None: {  # type: ignore[assignment]
        "objects": [{"key": "custom_objects.pet"}, {"key": "contact"}, {"key": "business"}]}
    types = c.discover_object_types()
    assert "contacts" in types and "opportunities" in types and "conversations" in types
    assert "custom_objects.pet" in types                  # user custom object surfaced
    assert "business" not in types and "contact" not in types  # built-in schema keys filtered
    assert len(types) == len(set(types))


def test_discover_object_types_tolerates_schema_failure():
    c = _client()

    def boom(*a, **k):
        raise RuntimeError("no custom-objects scope")

    c._get = boom  # type: ignore[assignment]
    types = c.discover_object_types()
    assert "contacts" in types and "custom_objects.pet" not in types  # standard set, no crash


# --- bounded live search ------------------------------------------------- #
def test_search_live_returns_bounded_records():
    c = _client()
    c._get = lambda path, params=None, *, version=None: {  # type: ignore[assignment]
        "contacts": [{"id": "1", "firstName": "Ada"}]}
    recs = c.search_live("contacts", q="ada", location_id="loc-1")
    assert len(recs) == 1 and recs[0].properties["firstName"] == "Ada"


# --- connector orchestration (item 3) ------------------------------------ #
from types import SimpleNamespace  # noqa: E402

from ingest.connectors.gohighlevel_full import GoHighLevelFullConnector  # noqa: E402
from ingest.connectors.hubspot_full import Record  # noqa: E402 — source-agnostic record shape


class _FakeClient:
    def __init__(self, types, records):
        self._types = types
        self._records = records          # {object_type: [Record,...] | Exception}
        self.calls = []                  # (object_type, location_id, since)
        self.discover_called = False

    def discover_object_types(self):
        self.discover_called = True
        return self._types

    def list_records(self, object_type, *, location_id=None, since=None):
        self.calls.append((object_type, location_id, since))
        recs = self._records.get(object_type, [])
        if isinstance(recs, Exception):
            raise recs
        return iter(recs)


class _FakeSink:
    def __init__(self):
        self.batches = []
        self.last_report = SimpleNamespace(errors=[])

    def upsert_records(self, tenant_id, records):
        recs = list(records)
        self.batches.append((tenant_id, recs))
        return len(recs)


def test_connector_lands_each_object_type_and_forwards_location():
    client = _FakeClient(
        ("contacts", "opportunities"),
        {"contacts": [Record("contacts", "1", {}, {}, None)],
         "opportunities": [Record("opportunities", "2", {}, {}, None)]},
    )
    sink = _FakeSink()
    res = GoHighLevelFullConnector(client, sink).sync("tenant-A", location_id="loc-1")
    assert res.pulled == 2 and res.landed == 2
    assert res.by_type == {"contacts": 1, "opportunities": 1}
    assert res.failed_types == []
    assert all(t == "tenant-A" for t, _ in sink.batches)   # tenant-scoped
    assert client.calls[0][1] == "loc-1"                    # location forwarded


def test_connector_skips_a_failing_object_type():
    client = _FakeClient(
        ("contacts", "weird_custom"),
        {"contacts": [Record("contacts", "1", {}, {}, None)],
         "weird_custom": RuntimeError("404 bad custom object")},
    )
    res = GoHighLevelFullConnector(client, _FakeSink()).sync("t", location_id="loc-1")
    assert res.by_type == {"contacts": 1}
    assert res.failed_types == ["weird_custom"]
    assert res.landed == 1


def test_connector_honors_object_types_override():
    client = _FakeClient(("SHOULD_NOT_USE",), {"contacts": [Record("contacts", "1", {}, {}, None)]})
    res = GoHighLevelFullConnector(client, _FakeSink()).sync("t", object_types=("contacts",))
    assert res.by_type == {"contacts": 1}
    assert client.discover_called is False


def test_connector_forwards_since():
    client = _FakeClient(("contacts",), {"contacts": []})
    GoHighLevelFullConnector(client, _FakeSink()).sync("t", location_id="loc-1", since="1700000000000")
    assert client.calls[0][2] == "1700000000000"
