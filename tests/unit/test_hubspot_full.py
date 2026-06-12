"""Unit tests for the HubSpot full-extract client (ingest/connectors/hubspot_full.py).

No network: every test injects a fake ``_get`` so the HTTP layer is never exercised — these
assert the discovery/pull LOGIC (all properties listed, media flagged URL-only, etc.).
"""
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
