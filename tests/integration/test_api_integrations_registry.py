"""Integration: the /integrations registry surface — hubspot|csv|gohighlevel|stripe|salesforce|microsoft.

Proves (on top of tests/integration/test_api_integrations.py, which covers the
hubspot reference flows):
  * GET /integrations lists all connectors with kind/experimental and a
    per-tenant status per connector
  * csv is a FILE connector: no vault slot (status "available" only when the
    importer is wired), credentials POST answers 409, sync POST answers 409
    pointing at the import endpoint
  * gohighlevel + stripe credentials vault under the CLAIMS tenant's own slot
    (uplift/{tenant}/{source}) and their syncs ride the same credential-gated
    runner path as hubspot (409 when not connected; runner kicked with the
    claims tenant + the right source name)
  * the stripe slot is the TENANT'S key by construction — the vault ref embeds
    the claims tenant id, so the platform billing key's secret name can never
    be addressed from this surface
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.integrations_routes import IntegrationsDeps
from api.views import SavedViews
from ingest.pipeline import SyncResult

H = {"Authorization": "Bearer t"}
SECRET = "rk_test_tenant_key_DO_NOT_LEAK"


class FakeVerifier:
    def verify(self, token):
        return {"sub": "uA", "custom:tenant_id": "A", "email": "a@x.com"}


class RecordingWriter:
    def __init__(self):
        self.put: dict[str, str] = {}

    def put_secret(self, ref, value):
        self.put[ref] = value

    def secret_exists(self, ref):
        return ref in self.put


def _client(integrations=None):
    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
        executor=lambda a: None,
        integrations=integrations if integrations is not None else IntegrationsDeps(),
    )
    return TestClient(create_app(deps))


# --------------------------------------------------------------------------- listing
@pytest.mark.integration
def test_listing_carries_all_connectors_with_kind_and_experimental():
    r = _client().get("/integrations", headers=H)
    assert r.status_code == 200
    items = {i["name"]: i for i in r.json()["integrations"]}
    assert set(items) == {"hubspot", "csv", "gohighlevel", "stripe", "salesforce", "microsoft"}
    assert items["csv"]["kind"] == "file"
    experimental = {"gohighlevel", "salesforce", "microsoft"}
    for name in ("hubspot", "gohighlevel", "stripe", "salesforce", "microsoft"):
        assert items[name]["kind"] == "sync"
        assert items[name]["experimental"] is (name in experimental)
    assert items["hubspot"]["experimental"] is False
    assert items["stripe"]["experimental"] is False


@pytest.mark.integration
def test_csv_status_available_only_when_importer_wired():
    # unconfigured: honestly unknown
    r = _client().get("/integrations", headers=H)
    csv_item = next(i for i in r.json()["integrations"] if i["name"] == "csv")
    assert csv_item["status"] == "unknown"
    assert r.json()["csv_import_configured"] is False
    # importer wired: available (csv is never "connected" — no vault slot)
    client = _client(IntegrationsDeps(csv_importer=lambda *a: {}))
    r = client.get("/integrations", headers=H)
    csv_item = next(i for i in r.json()["integrations"] if i["name"] == "csv")
    assert csv_item["status"] == "available"
    assert csv_item["connected"] is None
    assert r.json()["csv_import_configured"] is True


@pytest.mark.integration
def test_per_tenant_status_is_per_connector():
    writer = RecordingWriter()
    writer.put_secret("uplift/A/stripe", SECRET)  # tenant A connected stripe only
    client = _client(IntegrationsDeps(secret_writer=writer))
    items = {i["name"]: i for i in client.get("/integrations", headers=H).json()["integrations"]}
    assert items["stripe"]["status"] == "connected"
    assert items["hubspot"]["status"] == "not_connected"
    assert items["gohighlevel"]["status"] == "not_connected"


# --------------------------------------------------------------------------- csv guards
@pytest.mark.integration
def test_csv_credentials_409_no_vault_slot():
    client = _client(IntegrationsDeps(secret_writer=RecordingWriter()))
    r = client.post("/integrations/csv/credentials", json={"token": "x"}, headers=H)
    assert r.status_code == 409
    assert "csv/import" in r.json()["detail"]


@pytest.mark.integration
def test_csv_sync_409_points_at_import_endpoint():
    writer = RecordingWriter()
    client = _client(IntegrationsDeps(
        secret_writer=writer, sync_runner=lambda t, n: SyncResult()))
    r = client.post("/integrations/csv/sync", headers=H)
    assert r.status_code == 409
    assert "csv/import" in r.json()["detail"]


# --------------------------------------------------------------------------- new sync connectors
@pytest.mark.integration
@pytest.mark.parametrize("name", ["gohighlevel", "stripe"])
def test_credentials_vault_under_claims_tenant_slot(name):
    writer = RecordingWriter()
    client = _client(IntegrationsDeps(secret_writer=writer))
    r = client.post(f"/integrations/{name}/credentials",
                    json={"token": SECRET, "tenant_id": "B"}, headers=H)
    assert r.status_code == 200
    # the vault slot embeds the VERIFIED claims tenant (A) — by construction
    # this surface can never address another tenant's slot or any
    # platform-level secret name (e.g. the signup Stripe key).
    assert writer.put == {f"uplift/A/{name}": SECRET}
    assert SECRET not in r.text  # the token is never echoed back


@pytest.mark.integration
@pytest.mark.parametrize("name", ["gohighlevel", "stripe"])
def test_sync_requires_connection_then_kicks_runner_with_source_name(name):
    calls = []

    def runner(tenant_id, source):
        calls.append((tenant_id, source))
        return SyncResult(pulled=2, embedded=2)

    writer = RecordingWriter()
    client = _client(IntegrationsDeps(secret_writer=writer, sync_runner=runner))
    # not connected yet -> 409, runner untouched
    assert client.post(f"/integrations/{name}/sync", headers=H).status_code == 409
    assert calls == []
    # connect, then the sync rides the claims tenant + the right source name
    writer.put_secret(f"uplift/A/{name}", SECRET)
    r = client.post(f"/integrations/{name}/sync", headers=H)
    assert r.status_code == 200
    assert calls == [("A", name)]
    assert r.json()["result"]["pulled"] == 2
