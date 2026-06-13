"""Unit/integration: the EXPERIMENTAL GoHighLevel connector — recorded fixtures
only (tests/fixtures/connectors/gohighlevel_*.json), NO live GHL call ever.

Mirrors the HubSpot connector contract:
  * per-tenant vault slot uplift/{tenant}/gohighlevel ONLY — missing/empty
    secret is a HARD MissingTenantCredentialError (no shared-token fallback)
  * credential may be a bare token OR JSON {"token","location_id"} — both are
    parsed and handed to the injected client, never logged
  * contacts -> contacts rows, opportunities -> deals rows (normalization
    tolerant of the recorded shape quirks: contact-as-dict, junk amounts)
  * the incremental cursor advances to the max updatedAt/dateUpdated and the
    second sync pulls nothing
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingest import EMBEDDING_DIM
from ingest.connectors.base import MissingTenantCredentialError, SecretNotFoundError
from ingest.connectors.gohighlevel import GoHighLevelConnector
from ingest.pipeline import (
    InMemoryCursorStore,
    InMemoryDocumentStore,
    InMemoryRawSink,
    InMemoryStructuredSink,
    sync_tenant,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "connectors"
TENANT = "22222222-2222-2222-2222-222222222222"
TOKEN = "ghl-pit-supersecret-token"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class RecordedGhlClient:
    """Replays the recorded fixtures; honors `since` client-side like the real
    client does until server-side filters are verified. Records the credential
    hand-off so tests can assert it without ever logging the value."""

    def __init__(self):
        self.token = None
        self.location = None

    def set_token(self, token):
        self.token = token

    def set_location(self, location_id):
        self.location = location_id

    @staticmethod
    def _newer(obj, since):
        updated = obj.get("dateUpdated") or obj.get("updatedAt") or ""
        return not since or (updated and updated > since)

    def list_contacts(self, since):
        return [c for c in _fixture("gohighlevel_contacts.json")["contacts"]
                if self._newer(c, since)]

    def list_opportunities(self, since):
        return [o for o in _fixture("gohighlevel_opportunities.json")["opportunities"]
                if self._newer(o, since)]


class VaultedSecrets:
    def __init__(self, values: dict[str, str]):
        self.values = dict(values)
        self.asked: list[str] = []

    def get_secret(self, ref: str) -> str:
        self.asked.append(ref)
        if ref not in self.values:
            raise SecretNotFoundError(ref)
        return self.values[ref]


def _connector(secrets, client=None):
    return GoHighLevelConnector(
        TENANT,
        client=client if client is not None else RecordedGhlClient(),
        secrets=secrets,
        raw_sink=InMemoryRawSink(),
        structured_sink=InMemoryStructuredSink(),
    )


def _embed(text: str) -> list[float]:
    return [0.25] * EMBEDDING_DIM


# --------------------------------------------------------------------------- auth
@pytest.mark.unit
def test_missing_per_tenant_secret_is_hard_error_no_fallback():
    secrets = VaultedSecrets({})
    conn = _connector(secrets)
    with pytest.raises(MissingTenantCredentialError):
        conn.authenticate()
    # exactly the per-tenant slot was consulted — nothing else, ever
    assert secrets.asked == [f"uplift/{TENANT}/gohighlevel"]


@pytest.mark.unit
def test_empty_secret_is_hard_error():
    conn = _connector(VaultedSecrets({f"uplift/{TENANT}/gohighlevel": ""}))
    with pytest.raises(MissingTenantCredentialError):
        conn.authenticate()


@pytest.mark.unit
def test_bare_token_credential_handed_to_client():
    client = RecordedGhlClient()
    conn = _connector(VaultedSecrets({f"uplift/{TENANT}/gohighlevel": TOKEN}), client)
    conn.authenticate()
    assert client.token == TOKEN
    assert client.location is None


@pytest.mark.unit
def test_json_credential_parses_token_and_location():
    raw = json.dumps({"token": TOKEN, "location_id": "loc-123"})
    client = RecordedGhlClient()
    conn = _connector(VaultedSecrets({f"uplift/{TENANT}/gohighlevel": raw}), client)
    conn.authenticate()
    assert client.token == TOKEN
    assert client.location == "loc-123"


@pytest.mark.unit
def test_json_credential_without_token_is_hard_error():
    raw = json.dumps({"location_id": "loc-123"})
    conn = _connector(VaultedSecrets({f"uplift/{TENANT}/gohighlevel": raw}))
    with pytest.raises(MissingTenantCredentialError, match="no token"):
        conn.authenticate()


@pytest.mark.unit
def test_pull_requires_authenticate_first():
    conn = _connector(VaultedSecrets({f"uplift/{TENANT}/gohighlevel": TOKEN}))
    with pytest.raises(RuntimeError, match="authenticate"):
        list(conn.pull(None))


# --------------------------------------------------------------------------- normalization
@pytest.mark.integration
def test_recorded_fixtures_normalize_to_crm_shapes():
    conn = _connector(VaultedSecrets({f"uplift/{TENANT}/gohighlevel": TOKEN}))
    conn.authenticate()
    records = list(conn.pull(None))
    by_ref = {r.ref_id: r for r in records}
    assert len(records) == 5  # 3 contacts + 2 opportunities

    ava = by_ref["ghl-c-001"]
    assert ava.table == "contacts"
    assert ava.row["name"] == "Ava Martinez"
    assert ava.row["email"] == "ava@acme.test"
    assert ava.row["source"] == "gohighlevel"
    assert ava.tenant_id == TENANT
    assert "Company: Acme Fencing" in ava.text_blocks[0]["text"]

    # firstName+lastName fallback when contactName is absent
    assert by_ref["ghl-c-002"].row["name"] == "Ben Okafor"

    fence = by_ref["ghl-o-101"]
    assert fence.table == "deals"
    assert fence.row["title"] == "Acme backyard fence"
    assert fence.row["amount"] == 4800.0
    assert fence.row["stage"] == "Proposal Sent"
    assert fence.row["contact_ref_id"] == "ghl-c-001"

    repair = by_ref["ghl-o-102"]
    assert repair.row["amount"] is None          # junk monetaryValue tolerated
    assert repair.row["stage"] == "won"          # status fallback
    assert repair.row["contact_ref_id"] == "ghl-c-002"  # contact-as-dict tolerated


# --------------------------------------------------------------------------- incremental
@pytest.mark.integration
def test_sync_advances_cursor_and_second_run_pulls_nothing():
    secrets = VaultedSecrets({f"uplift/{TENANT}/gohighlevel": TOKEN})
    store, cursors = InMemoryDocumentStore(), InMemoryCursorStore()

    first = sync_tenant(TENANT, _connector(secrets), _embed, store, cursors)
    assert first.pulled == 5
    assert first.embedded == 5
    # max of every dateUpdated/updatedAt in the fixtures
    assert cursors.get(TENANT, "gohighlevel") == "2026-06-04T11:00:00.000Z"

    second = sync_tenant(TENANT, _connector(secrets), _embed, store, cursors)
    assert second.pulled == 0
    assert second.embedded == 0
    assert len(store.docs) == 5  # no duplicates


def test_rest_client_sends_nondefault_user_agent(monkeypatch):
    """GHL's Cloudflare BANS urllib's default UA (error 1010 -> 403 on every call -> sync fails).
    The REST client MUST send a named User-Agent. Regression guard (LIVE-CONFIRMED 2026-06-13)."""
    import urllib.request

    from ingest.connectors.gohighlevel import GoHighLevelRestClient

    seen = {}

    class _Resp:
        def __init__(self, body):
            self._b = body.encode()

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen["ua"] = req.get_header("User-agent")
        return _Resp('{"contacts": [], "meta": {}}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    c = GoHighLevelRestClient()
    c.set_token("t")
    c.set_location("loc-1")
    list(c.list_contacts(None))
    assert seen["ua"] and not seen["ua"].lower().startswith("python-")  # not the banned default
