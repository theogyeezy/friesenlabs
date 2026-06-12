"""Unit: the connector registry (ingest/connectors/registry.py) + its wiring.

Proves:
  * the registry knows exactly hubspot | csv | gohighlevel | stripe | salesforce | microsoft | google
  * SYNC_SOURCES excludes csv (push import, no pull sync)
  * build_sync_connector constructs the right class per name, refuses csv and
    unknown names, and threads the injected client through
  * run_sync.build_connector(source=...) rides the registry (offline stubs:
    a full dry sync over any source pulls nothing and touches nothing)
  * the run_sync CLI grew --source (validated against the registry)
  * the API-side metadata mirror (api/integrations_routes.KNOWN_INTEGRATIONS)
    stays IN SYNC with the registry — same names, kinds, experimental flags
    (mirrored, not imported, because the API image must boot without ingest/).
"""
from __future__ import annotations

import pytest

import ingest.run_sync as run_sync
from api.integrations_routes import KNOWN_INTEGRATIONS
from ingest.connectors.gohighlevel import GoHighLevelConnector
from ingest.connectors.hubspot import HubSpotConnector
from ingest.connectors.registry import (
    REGISTRY,
    SYNC_SOURCES,
    build_sync_connector,
    get_spec,
)
from ingest.connectors.salesforce import SalesforceConnector
from ingest.connectors.stripe_data import StripeDataConnector
from ingest.pipeline import InMemoryRawSink, InMemoryStructuredSink

TENANT = "44444444-4444-4444-4444-444444444444"


class _NullSecrets:
    def get_secret(self, ref):
        return "tok"


def _build(name, client=None):
    return build_sync_connector(
        name, TENANT, secrets=_NullSecrets(),
        raw_sink=InMemoryRawSink(), structured_sink=InMemoryStructuredSink(),
        client=client,
    )


# --------------------------------------------------------------------------- shape
@pytest.mark.unit
def test_registry_knows_exactly_the_known_connectors():
    assert set(REGISTRY) == {"hubspot", "csv", "gohighlevel", "stripe", "salesforce",
                             "microsoft", "google"}
    # csv = file import; everything else is a pull sync
    assert set(SYNC_SOURCES) == {"hubspot", "gohighlevel", "stripe", "salesforce",
                                 "microsoft", "google"}
    assert REGISTRY["csv"].kind == "file"
    assert REGISTRY["gohighlevel"].experimental is True
    assert REGISTRY["salesforce"].experimental is True
    assert REGISTRY["microsoft"].experimental is True
    assert REGISTRY["google"].experimental is True
    for name in ("hubspot", "stripe"):
        assert REGISTRY[name].experimental is False


@pytest.mark.unit
def test_get_spec_unknown_name_raises():
    with pytest.raises(KeyError, match="zendesk"):
        get_spec("zendesk")


@pytest.mark.unit
def test_build_constructs_the_right_connector_class():
    assert isinstance(_build("hubspot"), HubSpotConnector)
    assert isinstance(_build("gohighlevel"), GoHighLevelConnector)
    assert isinstance(_build("stripe"), StripeDataConnector)
    assert isinstance(_build("salesforce"), SalesforceConnector)


@pytest.mark.unit
def test_build_threads_injected_client_through():
    sentinel = object()
    conn = _build("stripe", client=sentinel)
    assert conn._client is sentinel


@pytest.mark.unit
def test_build_refuses_csv_and_unknown_names():
    with pytest.raises(ValueError, match="csv/import"):
        _build("csv")
    with pytest.raises(KeyError):
        _build("zendesk")


@pytest.mark.unit
def test_source_tag_matches_registry_name():
    # every landed row/chunk is stamped with the connector's source tag — the
    # registry key must BE that tag (vault slots + documents.source ride it).
    for name in SYNC_SOURCES:
        assert _build(name).source == name


# --------------------------------------------------------------------------- run_sync wiring
@pytest.mark.unit
def test_run_sync_build_connector_by_source_offline(monkeypatch):
    monkeypatch.delenv("INGEST_REAL_STORES", raising=False)
    assert isinstance(run_sync.build_connector(TENANT), HubSpotConnector)  # default intact
    assert isinstance(run_sync.build_connector(TENANT, source="stripe"), StripeDataConnector)
    assert isinstance(
        run_sync.build_connector(TENANT, source="gohighlevel"), GoHighLevelConnector
    )


@pytest.mark.unit
def test_offline_dry_sync_any_source_pulls_nothing(monkeypatch):
    # The full auth -> pull -> land -> cursor path runs with zero records and
    # zero network for EVERY sync source (the registry's empty stub client).
    monkeypatch.delenv("INGEST_REAL_STORES", raising=False)
    from ingest.pipeline import InMemoryCursorStore, InMemoryDocumentStore

    for source in SYNC_SOURCES:
        res = run_sync.run_one(
            TENANT, store=InMemoryDocumentStore(), cursors=InMemoryCursorStore(),
            embedder=lambda t: [0.0] * 1024, raw_sink=InMemoryRawSink(), source=source,
        )
        assert (res.pulled, res.embedded) == (0, 0), source


@pytest.mark.unit
def test_cli_source_flag_validated(capsys, monkeypatch):
    monkeypatch.delenv("INGEST_REAL_STORES", raising=False)
    # unknown source = argparse usage error (exit 2)
    with pytest.raises(SystemExit) as exc:
        run_sync.main(["--tenant", TENANT, "--source", "zendesk"])
    assert exc.value.code == 2
    # a valid non-default source dry-runs clean
    assert run_sync.main(["--tenant", TENANT, "--source", "gohighlevel"]) == 0


# --------------------------------------------------------------------------- API mirror parity
@pytest.mark.unit
def test_api_known_integrations_mirror_stays_in_sync():
    assert set(KNOWN_INTEGRATIONS) == set(REGISTRY)
    for name, spec in REGISTRY.items():
        meta = KNOWN_INTEGRATIONS[name]
        assert meta["kind"] == spec.kind, name
        assert meta["experimental"] is spec.experimental, name
        if spec.kind == "sync":
            # the vault-slot segment is the registry name itself
            assert meta["source"] == name, name
        else:
            assert meta["source"] is None, name
