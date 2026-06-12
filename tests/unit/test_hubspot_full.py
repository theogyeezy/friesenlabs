"""Unit tests for the HubSpot full-extract client (ingest/connectors/hubspot_full.py).

No network: every test injects a fake ``_get`` so the HTTP layer is never exercised — these
assert the discovery/pull LOGIC (all properties listed, media flagged URL-only, etc.).
"""
from datetime import datetime, timezone

import pytest

from ingest.connectors.hubspot_full import HubSpotFullClient, PropertySet

pytestmark = pytest.mark.unit


# A properties response with a mix of normal fields + one file/media property + a nameless row.
_PROPS_FIXTURE = {
    "results": [
        {"name": "firstname", "type": "string", "fieldType": "text"},
        {"name": "email", "type": "string", "fieldType": "text"},
        {"name": "hs_lastmodifieddate", "type": "datetime", "fieldType": "text"},
        {"name": "headshot", "type": "string", "fieldType": "file"},   # media → URL ref only
        {"name": "intro_video", "type": "file", "fieldType": "file"},  # media (both markers)
        {"type": "string", "fieldType": "text"},                       # no name → skipped
    ],
}


def _client_with(fixture: dict) -> HubSpotFullClient:
    c = HubSpotFullClient()
    c.set_token("test-token")
    c._get = lambda path, params=None: fixture  # type: ignore[assignment]
    return c


def test_discover_properties_lists_every_named_property():
    ps = _client_with(_PROPS_FIXTURE).discover_properties("contacts")
    assert isinstance(ps, PropertySet)
    assert set(ps.names) == {"firstname", "email", "hs_lastmodifieddate", "headshot", "intro_video"}
    # nameless row dropped, no duplicates
    assert len(ps.names) == 5


def test_discover_properties_flags_file_media_only():
    ps = _client_with(_PROPS_FIXTURE).discover_properties("contacts")
    assert ps.media == frozenset({"headshot", "intro_video"})
    # non-media fields are NOT flagged
    assert "email" not in ps.media and "firstname" not in ps.media


def test_discover_properties_empty_results_is_empty_set():
    ps = _client_with({"results": []}).discover_properties("contacts")
    assert ps.names == () and ps.media == frozenset()


def test_get_requires_a_token():
    c = HubSpotFullClient()  # no set_token
    with pytest.raises(RuntimeError, match="no token"):
        c._get("/crm/v3/properties/contacts")


# --- object discovery (item 3) ------------------------------------------- #
_SCHEMAS_FIXTURE = {
    "results": [
        {"name": "pet", "fullyQualifiedName": "p12345_pet", "objectTypeId": "2-12345"},
        {"name": "property", "fullyQualifiedName": "p12345_property", "objectTypeId": "2-67890"},
    ],
}


def test_discover_object_types_unions_standard_engagements_and_custom():
    types = _client_with(_SCHEMAS_FIXTURE).discover_object_types()
    # standard objects + engagements present
    for t in ("contacts", "companies", "deals", "tickets", "calls", "notes", "tasks"):
        assert t in types
    # custom objects appended by fullyQualifiedName
    assert "p12345_pet" in types and "p12345_property" in types
    # stable, de-duplicated
    assert len(types) == len(set(types))


def test_discover_object_types_tolerates_schemas_failure():
    c = HubSpotFullClient()
    c.set_token("t")

    def boom(path, params=None):  # e.g. 403 no schemas scope
        raise RuntimeError("no schemas scope")

    c._get = boom  # type: ignore[assignment]
    types = c.discover_object_types()
    assert "contacts" in types and "notes" in types  # standard+engagements still returned
    assert "p12345_pet" not in types                 # customs absent, but no crash


# --- record pull (item 4) ------------------------------------------------ #
def test_list_records_paginates_full_pull():
    c = HubSpotFullClient()
    c.set_token("t")
    pages = [
        {"results": [{"id": "1", "properties": {"email": "a@x.com"}, "updatedAt": "t1"}],
         "paging": {"next": {"after": "P2"}}},
        {"results": [{"id": "2", "properties": {"email": "b@x.com"}, "updatedAt": "t2"}]},
    ]
    calls = []

    def fake_get(path, params=None):
        calls.append(path)
        return pages[0] if (params or {}).get("after") is None else pages[1]

    c._get = fake_get  # type: ignore[assignment]
    ps = PropertySet(names=("email",), media=frozenset())
    recs = list(c.list_records("contacts", ps, since=None))
    assert [r.source_ref_id for r in recs] == ["1", "2"]   # both pages walked
    assert all("/files" not in p for p in calls)           # Files API never touched


def test_list_records_incremental_filter_value_is_epoch_millis():
    c = HubSpotFullClient()
    c.set_token("t")
    captured = {}

    def fake_post(path, body):
        captured["path"] = path
        captured["body"] = body
        return {"results": []}

    c._post = fake_post  # type: ignore[assignment]
    ps = PropertySet(names=("email", "lastmodifieddate"), media=frozenset())
    list(c.list_records("contacts", ps, since="2026-06-01T00:00:00Z"))
    flt = captured["body"]["filterGroups"][0]["filters"][0]
    assert flt["propertyName"] == "lastmodifieddate" and flt["operator"] == "GTE"
    assert flt["value"].isdigit()  # epoch millis, NOT ISO (the sync-bug fix)
    expected = str(int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp() * 1000))
    assert flt["value"] == expected
    assert captured["path"].endswith("/crm/v3/objects/contacts/search")


def test_list_records_media_kept_as_ref_never_fetched():
    c = HubSpotFullClient()
    c.set_token("t")
    page = {"results": [{"id": "9", "properties": {
        "email": "a@x.com", "headshot": "https://files.hubspot.com/abc.png"}}]}
    paths = []

    def fake_get(path, params=None):
        paths.append(path)
        return page

    c._get = fake_get  # type: ignore[assignment]
    ps = PropertySet(names=("email", "headshot"), media=frozenset({"headshot"}))
    rec = next(iter(c.list_records("contacts", ps, since=None)))
    assert rec.properties["headshot"] == "https://files.hubspot.com/abc.png"  # URL kept verbatim
    assert rec.properties["_media_refs"] == ["headshot"]                      # flagged
    assert all("/files" not in p for p in paths)                             # bytes never fetched


def test_list_records_flattens_associations():
    c = HubSpotFullClient()
    c.set_token("t")
    page = {"results": [{"id": "9", "properties": {}, "associations": {
        "companies": {"results": [{"id": "100"}, {"id": "200"}]}}}]}
    c._get = lambda path, params=None: page  # type: ignore[assignment]
    ps = PropertySet(names=(), media=frozenset())
    rec = next(iter(c.list_records("contacts", ps, since=None)))
    assert rec.associations == {"companies": ["100", "200"]}
