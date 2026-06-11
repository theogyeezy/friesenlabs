"""Unit: sync_tenant is incremental — first run embeds N, second run embeds ~0.

All offline: fake HubSpot client, fake secret provider, in-memory sinks/store/cursor.
Also asserts tenant_id is carried on every stored document.
"""
import pytest

from ingest import EMBEDDING_DIM
from ingest.connectors.base import tenant_secret_ref
from ingest.connectors.hubspot import HubSpotConnector
from ingest.pipeline import (
    InMemoryCursorStore,
    InMemoryDocumentStore,
    InMemoryRawSink,
    InMemoryStructuredSink,
    sync_tenant,
)

TENANT = "22222222-2222-2222-2222-222222222222"


class FakeSecrets:
    def get_secret(self, ref):
        # The connector must resolve THIS tenant's per-tenant ref and only that
        # (the shared-token fallback is gone).
        assert ref == tenant_secret_ref(TENANT, "hubspot")
        return "pat-fake-token"


class FakeHubSpotClient:
    """Returns fixtures; honors `since` so the 2nd run can pull nothing if changed."""

    def __init__(self):
        self.calls = {"companies": 0, "contacts": 0, "deals": 0, "notes": 0}
        self._companies = [
            {"id": "co-1", "updatedAt": "2026-01-01T00:00:00Z",
             "properties": {"name": "Acme", "domain": "acme.io"}},
        ]
        self._contacts = [
            {"id": "ct-1", "updatedAt": "2026-01-02T00:00:00Z",
             "properties": {"firstname": "Ada", "lastname": "Lovelace",
                            "email": "ada@acme.io", "associatedcompanyid": "co-1"}},
            {"id": "ct-2", "updatedAt": "2026-01-03T00:00:00Z",
             "properties": {"firstname": "Bob", "lastname": "Stone",
                            "email": "bob@acme.io"}},
        ]
        self._deals = [
            {"id": "dl-1", "updatedAt": "2026-01-04T00:00:00Z",
             "properties": {"dealname": "Acme Renewal", "amount": "12000",
                            "dealstage": "negotiation", "associatedcompanyid": "co-1"}},
        ]
        self._notes = [
            {"id": "nt-1", "updatedAt": "2026-01-05T00:00:00Z",
             "properties": {"hs_note_body": "Discussed renewal; wants annual quote.",
                            "hs_contact_id": "ct-1"}},
        ]

    @staticmethod
    def _filter(items, since):
        if since is None:
            return list(items)
        return [i for i in items if i["updatedAt"] > since]

    def list_companies(self, since):
        self.calls["companies"] += 1
        return self._filter(self._companies, since)

    def list_contacts(self, since):
        self.calls["contacts"] += 1
        return self._filter(self._contacts, since)

    def list_deals(self, since):
        self.calls["deals"] += 1
        return self._filter(self._deals, since)

    def list_notes(self, since):
        self.calls["notes"] += 1
        return self._filter(self._notes, since)


class CountingEmbedder:
    def __init__(self):
        self.count = 0

    def __call__(self, text):
        self.count += 1
        return [0.001 * (len(text) % 1000)] * EMBEDDING_DIM


def _make_connector(hs_client):
    return HubSpotConnector(
        TENANT,
        client=hs_client,
        secrets=FakeSecrets(),
        raw_sink=InMemoryRawSink(),
        structured_sink=InMemoryStructuredSink(),
    )


@pytest.mark.unit
def test_second_sync_embeds_nothing():
    store = InMemoryDocumentStore()
    cursors = InMemoryCursorStore()
    embedder = CountingEmbedder()

    # --- first run: full pull, embeds every chunk ---
    hs1 = FakeHubSpotClient()
    r1 = sync_tenant(TENANT, _make_connector(hs1), embedder, store, cursors)
    assert r1.pulled == 5  # 1 company + 2 contacts + 1 deal + 1 note
    assert r1.chunks == 5
    assert r1.embedded == 5
    assert r1.skipped == 0
    assert embedder.count == 5
    assert r1.cursor == "2026-01-05T00:00:00Z"  # max updatedAt advanced
    assert len(store.docs) == 5

    embedded_after_first = embedder.count

    # --- second run: change-filtered connector pulls nothing past the cursor ---
    hs2 = FakeHubSpotClient()
    r2 = sync_tenant(TENANT, _make_connector(hs2), embedder, store, cursors)
    assert r2.pulled == 0          # cursor filtered everything out
    assert r2.embedded == 0        # nothing to embed
    assert embedder.count == embedded_after_first  # no new embed calls
    # cursor unchanged
    assert cursors.get(TENANT, "hubspot") == "2026-01-05T00:00:00Z"


@pytest.mark.unit
def test_second_sync_skips_unchanged_even_without_cursor_filter():
    """Even if a connector re-pulls everything (no change filter), the content-hash
    skip prevents re-embedding unchanged docs."""
    store = InMemoryDocumentStore()
    cursors = InMemoryCursorStore()
    embedder = CountingEmbedder()

    class NoFilterClient(FakeHubSpotClient):
        @staticmethod
        def _filter(items, since):
            return list(items)  # always returns everything

    r1 = sync_tenant(TENANT, _make_connector(NoFilterClient()), embedder, store, cursors)
    assert r1.embedded == 5
    first = embedder.count

    r2 = sync_tenant(TENANT, _make_connector(NoFilterClient()), embedder, store, cursors)
    assert r2.pulled == 5          # pulled again
    assert r2.embedded == 0        # but all hashes matched -> embedded nothing
    assert r2.skipped == 5
    assert embedder.count == first  # no extra embed calls


@pytest.mark.unit
def test_changed_content_re_embeds_only_that_doc():
    store = InMemoryDocumentStore()
    cursors = InMemoryCursorStore()
    embedder = CountingEmbedder()

    class NoFilterClient(FakeHubSpotClient):
        @staticmethod
        def _filter(items, since):
            return list(items)

    sync_tenant(TENANT, _make_connector(NoFilterClient()), embedder, store, cursors)
    baseline = embedder.count

    # Mutate one contact's body so its content hash changes.
    class MutatedClient(NoFilterClient):
        def __init__(self):
            super().__init__()
            self._contacts[0]["properties"]["email"] = "ada.new@acme.io"

    r = sync_tenant(TENANT, _make_connector(MutatedClient()), embedder, store, cursors)
    assert r.embedded == 1         # only the changed contact re-embedded
    assert r.skipped == 4
    assert embedder.count == baseline + 1


@pytest.mark.unit
def test_all_stored_docs_carry_tenant_id():
    store = InMemoryDocumentStore()
    cursors = InMemoryCursorStore()
    sync_tenant(TENANT, _make_connector(FakeHubSpotClient()), CountingEmbedder(),
                store, cursors)
    assert store.docs
    for (tid, source, ref), row in store.docs.items():
        assert tid == TENANT
        assert row["tenant_id"] == TENANT
        assert source == "hubspot"
        assert len(row["embedding"]) == EMBEDDING_DIM


@pytest.mark.unit
def test_land_rejects_cross_tenant_record():
    """Sanity: connector.land refuses a record stamped with a different tenant."""
    from ingest.connectors.base import NormalizedRecord

    conn = _make_connector(FakeHubSpotClient())
    conn.authenticate()
    bad = NormalizedRecord(
        tenant_id="99999999-9999-9999-9999-999999999999",
        source="hubspot", ref_id="x", table="contacts", row={}, raw={},
    )
    with pytest.raises(ValueError):
        conn.land([bad])
