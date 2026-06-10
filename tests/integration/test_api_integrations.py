"""Integration: /integrations endpoints — list/credentials/sync (claims-bound, gated).

Proves the api half of TODO INT/P2:
  * 401 unauth on all three routes (the shared current_tenant dependency)
  * tenant ALWAYS from the verified claims — a smuggled body tenant is ignored
  * the submitted token is NEVER echoed in a response or written to a log
  * unconfigured deps are HONEST: credentials/sync 503, status "unknown" — never fake success
  * the injected writer receives the per-tenant vault ref (uplift/{tenant}/hubspot)
  * the injected runner is kicked for the claims tenant and its SyncResult is serialized
  * unknown integration names 404; empty tokens 422; adapter failures 502 without leaking
"""
import logging

import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.integrations_routes import IntegrationsDeps
from api.views import SavedViews
from ingest.connectors.base import tenant_secret_ref
from ingest.pipeline import SyncResult

SECRET = "pat-na1-super-secret-hubspot-token-DO-NOT-LEAK"
H = {"Authorization": "Bearer t"}


class FakeVerifier:
    def verify(self, token):
        return {"sub": "uA", "custom:tenant_id": "A", "email": "a@x.com"}


class RecordingWriter:
    """Fake SecretWriter — records puts, answers existence from them."""

    def __init__(self):
        self.put: dict[str, str] = {}

    def put_secret(self, ref, value):
        self.put[ref] = value

    def secret_exists(self, ref):
        return ref in self.put


class ExplodingWriter:
    def put_secret(self, ref, value):
        raise RuntimeError("simulated vault outage")

    def secret_exists(self, ref):
        raise RuntimeError("simulated vault outage")


def _client(integrations=None):
    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
        executor=lambda a: None,
        integrations=integrations if integrations is not None else IntegrationsDeps(),
    )
    return TestClient(create_app(deps))


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_unauth_401_on_all_three_routes():
    client = _client()
    assert client.get("/integrations").status_code == 401
    assert client.post("/integrations/hubspot/credentials", json={"token": "x"}).status_code == 401
    assert client.post("/integrations/hubspot/sync").status_code == 401


# --------------------------------------------------------------------------- #
# honest unconfigured stubs (the env-default deps in tests: all-None)
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_list_unconfigured_status_unknown_not_invented():
    r = _client().get("/integrations", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["secrets_configured"] is False
    assert body["sync_configured"] is False
    hub = next(i for i in body["integrations"] if i["name"] == "hubspot")
    assert hub["connected"] is None          # honestly unknown — never a fake True/False
    assert hub["status"] == "unknown"


@pytest.mark.integration
def test_credentials_unconfigured_503_never_fake_success():
    r = _client().post("/integrations/hubspot/credentials", json={"token": SECRET}, headers=H)
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"]
    assert "stored" not in r.text


@pytest.mark.integration
def test_sync_unconfigured_503_never_fake_success():
    r = _client().post("/integrations/hubspot/sync", headers=H)
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"]


@pytest.mark.integration
def test_default_apideps_mounts_routes_with_honest_stubs():
    # ApiDeps without an explicit `integrations` builds the env-default deps —
    # with no env set everything must be the honest stub, not a 404 and not success.
    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
        executor=lambda a: None,
    )
    client = TestClient(create_app(deps))
    assert client.get("/integrations", headers=H).status_code == 200
    assert client.post("/integrations/hubspot/credentials",
                       json={"token": "x"}, headers=H).status_code == 503


# --------------------------------------------------------------------------- #
# credentials — claims-bound vault writes, token redaction
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_credentials_stored_under_claims_tenant_ref():
    writer = RecordingWriter()
    client = _client(IntegrationsDeps(secret_writer=writer))
    r = client.post("/integrations/hubspot/credentials", json={"token": SECRET}, headers=H)
    assert r.status_code == 200
    ref = tenant_secret_ref("A", "hubspot")
    assert writer.put == {ref: SECRET}
    assert r.json() == {"name": "hubspot", "secret_ref": ref, "stored": True,
                        "status": "connected"}


@pytest.mark.integration
def test_smuggled_body_tenant_ignored():
    # THE TRUST RULE: a tenant id in the request body must never pick the vault slot.
    writer = RecordingWriter()
    client = _client(IntegrationsDeps(secret_writer=writer))
    r = client.post(
        "/integrations/hubspot/credentials",
        json={"token": SECRET, "tenant_id": "B", "tenant": "B"},
        headers=H,  # verified claims say tenant A
    )
    assert r.status_code == 200
    assert tenant_secret_ref("A", "hubspot") in writer.put
    assert tenant_secret_ref("B", "hubspot") not in writer.put
    assert all("B" not in ref for ref in writer.put)


@pytest.mark.integration
def test_token_never_echoed_or_logged(caplog):
    writer = RecordingWriter()
    client = _client(IntegrationsDeps(secret_writer=writer))
    with caplog.at_level(logging.DEBUG):
        r = client.post("/integrations/hubspot/credentials",
                        json={"token": SECRET}, headers=H)
        listing = client.get("/integrations", headers=H)
    assert r.status_code == 200
    assert SECRET not in r.text
    assert SECRET not in listing.text
    assert SECRET not in caplog.text


@pytest.mark.integration
def test_writer_failure_502_without_leaking_token(caplog):
    client = _client(IntegrationsDeps(secret_writer=ExplodingWriter()))
    with caplog.at_level(logging.DEBUG):
        r = client.post("/integrations/hubspot/credentials",
                        json={"token": SECRET}, headers=H)
    assert r.status_code == 502
    assert SECRET not in r.text
    assert SECRET not in caplog.text
    assert "simulated vault outage" not in r.text  # adapter internals stay out of responses


@pytest.mark.integration
def test_status_check_failure_degrades_to_unknown_not_500():
    client = _client(IntegrationsDeps(secret_writer=ExplodingWriter()))
    r = client.get("/integrations", headers=H)
    assert r.status_code == 200
    hub = next(i for i in r.json()["integrations"] if i["name"] == "hubspot")
    assert hub["status"] == "unknown"


@pytest.mark.integration
def test_empty_token_422():
    client = _client(IntegrationsDeps(secret_writer=RecordingWriter()))
    for bad in ("", "   "):
        r = client.post("/integrations/hubspot/credentials",
                        json={"token": bad}, headers=H)
        assert r.status_code == 422


@pytest.mark.integration
def test_connected_status_after_store():
    writer = RecordingWriter()
    client = _client(IntegrationsDeps(secret_writer=writer))
    client.post("/integrations/hubspot/credentials", json={"token": SECRET}, headers=H)
    hub = next(i for i in client.get("/integrations", headers=H).json()["integrations"]
               if i["name"] == "hubspot")
    assert hub["connected"] is True
    assert hub["status"] == "connected"


# --------------------------------------------------------------------------- #
# sync — claims-bound runner kick
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_sync_kicks_runner_for_claims_tenant_and_serializes_result():
    calls = []

    def runner(tenant_id, name):
        calls.append((tenant_id, name))
        return SyncResult(pulled=3, landed_rows=3, chunks=5, embedded=4, skipped=1,
                          cursor="2026-06-09T00:00:00Z")

    # The shared-fallback guard requires a verifiable per-tenant credential:
    # vault one for tenant A (the verified-claim tenant) first.
    writer = RecordingWriter()
    writer.put_secret("uplift/A/hubspot", "tok")
    client = _client(IntegrationsDeps(secret_writer=writer, sync_runner=runner))
    # A smuggled body tenant must not steer the runner either (route takes no body).
    r = client.post("/integrations/hubspot/sync", json={"tenant_id": "B"}, headers=H)
    assert r.status_code == 200
    assert calls == [("A", "hubspot")]
    res = r.json()["result"]
    assert (res["pulled"], res["embedded"], res["skipped"]) == (3, 4, 1)
    assert res["cursor"] == "2026-06-09T00:00:00Z"


@pytest.mark.integration
def test_sync_runner_failure_502():
    def runner(tenant_id, name):
        raise RuntimeError("connector blew up")

    writer = RecordingWriter()
    writer.put_secret("uplift/A/hubspot", "tok")
    client = _client(IntegrationsDeps(secret_writer=writer, sync_runner=runner))
    r = client.post("/integrations/hubspot/sync", headers=H)
    assert r.status_code == 502
    assert "connector blew up" not in r.text


# --------------------------------------------------------------------------- #
# the shared-fallback guard (post-#67 review MEDIUM): an API-kicked sync must
# never run without the tenant's OWN verifiable vaulted credential.
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_sync_refused_503_when_no_secret_writer_to_verify_with():
    runner_calls = []
    client = _client(IntegrationsDeps(
        secret_writer=None, sync_runner=lambda t, n: runner_calls.append((t, n))))
    r = client.post("/integrations/hubspot/sync", headers=H)
    assert r.status_code == 503
    assert runner_calls == []


@pytest.mark.integration
def test_sync_refused_409_when_tenant_not_connected():
    runner_calls = []
    client = _client(IntegrationsDeps(
        secret_writer=RecordingWriter(),  # empty vault — tenant A never connected
        sync_runner=lambda t, n: runner_calls.append((t, n))))
    r = client.post("/integrations/hubspot/sync", headers=H)
    assert r.status_code == 409
    assert runner_calls == []
    assert "shared fallback" in r.json()["detail"]


@pytest.mark.integration
def test_sync_fails_closed_502_when_credential_check_errors():
    runner_calls = []
    client = _client(IntegrationsDeps(
        secret_writer=ExplodingWriter(),
        sync_runner=lambda t, n: runner_calls.append((t, n))))
    r = client.post("/integrations/hubspot/sync", headers=H)
    assert r.status_code == 502
    assert runner_calls == []


# --------------------------------------------------------------------------- #
# unknown names
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_unknown_integration_404():
    client = _client(IntegrationsDeps(secret_writer=RecordingWriter(),
                                      sync_runner=lambda t, n: SyncResult()))
    assert client.post("/integrations/salesforce/credentials",
                       json={"token": "x"}, headers=H).status_code == 404
    assert client.post("/integrations/salesforce/sync", headers=H).status_code == 404
