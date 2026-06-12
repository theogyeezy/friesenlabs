"""Unit tests for the full-extract WIRING (item 7): the registry factory + the run_sync driver.

No network/DB: a fake client captures the token, a fake secrets feeds the reused vault auth, and
the sink is constructed (never used) over a trivial conn_factory.
"""
import pytest

from ingest.connectors.hubspot_full import HubSpotFullConnector
from ingest.connectors.registry import build_hubspot_full_connector
from ingest.sinks import PgCrmRecordsSink

pytestmark = pytest.mark.unit


class _CapClient:
    def __init__(self):
        self.token = None

    def set_token(self, t):
        self.token = t


def test_factory_token_bypass_wires_connector_and_records_sink():
    client = _CapClient()
    conn = build_hubspot_full_connector(
        "tenant-A", client=client, token="bearer-xyz", conn_factory=lambda: None)
    assert isinstance(conn, HubSpotFullConnector)
    assert client.token == "bearer-xyz"             # token set, no auth path
    assert isinstance(conn._sink, PgCrmRecordsSink)  # wired to the full-fidelity sink
    assert conn._client is client


def test_factory_reuses_vault_auth_to_resolve_token():
    client = _CapClient()

    class FakeSecrets:
        def get_secret(self, ref):
            return "pasted-bearer-abc"  # a bare pasted token (not OAuth JSON)

    conn = build_hubspot_full_connector(
        "tenant-A", client=client, secrets=FakeSecrets(), conn_factory=lambda: None)
    # token came through HubSpotConnector.authenticate() (reused vault read), not duplicated logic
    assert client.token == "pasted-bearer-abc"
    assert isinstance(conn, HubSpotFullConnector)


def test_run_full_extract_requires_real_mode(monkeypatch):
    import ingest.run_sync as rs

    monkeypatch.setattr(rs, "real_mode", lambda: False)
    with pytest.raises(RuntimeError, match="real mode"):
        rs.run_full_extract("tenant-A")
