"""Integration: the Switchboard release-readiness surface added on top of the original
four /integrations endpoints (tests/integration/test_api_integrations.py covers those).

Covers, all offline (fake writer/runner/run-store/prober; zero AWS, zero DB):
  * DELETE /integrations/{name}/credentials — disconnect: 401/404/409-file/503-unconfigured,
    idempotent deleted true/false, 502 on a vault outage, slot ref from the CLAIM only
  * verify-on-connect — prober False -> 422 + NOTHING stored; True -> verified:true;
    None (inconclusive) -> stored with verified:null; no prober -> verified:null
  * async sync (run store wired) — 202 + a `running` row the background task finishes
    (succeeded with metrics / failed with the exception CLASS name only); a concurrent
    kick answers 409 via the single-runner guard
  * GET /integrations — last_sync per sync item + sync_history_configured flag
  * GET /integrations/{name}/syncs — 401/404/409-file/503-storeless/newest-first rows
"""
import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.integrations_routes import IntegrationsDeps
from api.pg_sync_runs import InMemorySyncRunStore
from api.views import SavedViews
from ingest.connectors.base import tenant_secret_ref
from ingest.pipeline import SyncResult

SECRET = "pat-na1-super-secret-hubspot-token-DO-NOT-LEAK"
H = {"Authorization": "Bearer t"}

pytestmark = pytest.mark.integration


class FakeVerifier:
    def verify(self, token):
        return {"sub": "uA", "custom:tenant_id": "A", "email": "a@x.com"}


class RecordingWriter:
    """Fake SecretWriter — records puts/deletes, answers existence from them."""

    def __init__(self):
        self.put: dict[str, str] = {}
        self.deleted: list[str] = []

    def put_secret(self, ref, value):
        self.put[ref] = value

    def secret_exists(self, ref):
        return ref in self.put

    def delete_secret(self, ref):
        self.deleted.append(ref)
        return self.put.pop(ref, None) is not None


class ExplodingDeleteWriter(RecordingWriter):
    def delete_secret(self, ref):
        raise RuntimeError("simulated vault outage")


def _client(integrations):
    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
        executor=lambda a: None, integrations=integrations,
    )
    return TestClient(create_app(deps))


def _connected_writer() -> RecordingWriter:
    w = RecordingWriter()
    w.put[tenant_secret_ref("A", "hubspot")] = SECRET
    return w


# --------------------------------------------------------------------------- #
# DELETE /integrations/{name}/credentials
# --------------------------------------------------------------------------- #
def test_disconnect_requires_auth():
    c = _client(IntegrationsDeps())
    assert c.delete("/integrations/hubspot/credentials").status_code == 401


def test_disconnect_unknown_404_and_csv_409():
    c = _client(IntegrationsDeps(secret_writer=RecordingWriter()))
    assert c.delete("/integrations/nope/credentials", headers=H).status_code == 404
    assert c.delete("/integrations/csv/credentials", headers=H).status_code == 409


def test_disconnect_503_when_unconfigured():
    c = _client(IntegrationsDeps())
    r = c.delete("/integrations/hubspot/credentials", headers=H)
    assert r.status_code == 503


def test_disconnect_deletes_claims_slot_and_is_idempotent():
    w = _connected_writer()
    c = _client(IntegrationsDeps(secret_writer=w))
    r = c.delete("/integrations/hubspot/credentials", headers=H)
    assert r.status_code == 200
    assert r.json() == {"name": "hubspot", "deleted": True, "status": "not_connected"}
    assert w.deleted == [tenant_secret_ref("A", "hubspot")]  # the CLAIM tenant's slot
    # Second disconnect: still 200, honestly deleted=false.
    r2 = c.delete("/integrations/hubspot/credentials", headers=H)
    assert r2.status_code == 200
    assert r2.json()["deleted"] is False


def test_disconnect_vault_outage_502_generic_detail():
    c = _client(IntegrationsDeps(secret_writer=ExplodingDeleteWriter()))
    r = c.delete("/integrations/hubspot/credentials", headers=H)
    assert r.status_code == 502
    assert "outage" not in r.json()["detail"]  # the provider error text never leaks


# --------------------------------------------------------------------------- #
# verify-on-connect
# --------------------------------------------------------------------------- #
def test_probe_rejection_422_and_nothing_stored():
    w = RecordingWriter()
    probes: list[tuple[str, str]] = []

    def prober(source, token):
        probes.append((source, token))
        return False  # the provider definitively rejected the token

    c = _client(IntegrationsDeps(secret_writer=w, token_prober=prober))
    r = c.post("/integrations/hubspot/credentials", json={"token": "bad-token"}, headers=H)
    assert r.status_code == 422
    assert w.put == {}  # NOTHING vaulted on a definitive rejection
    assert probes == [("hubspot", "bad-token")]
    # The token itself never appears in the response detail.
    assert "bad-token" not in r.json()["detail"]


def test_probe_accept_reports_verified_true():
    w = RecordingWriter()
    c = _client(IntegrationsDeps(secret_writer=w, token_prober=lambda s, t: True))
    r = c.post("/integrations/hubspot/credentials", json={"token": SECRET}, headers=H)
    assert r.status_code == 200
    assert r.json()["verified"] is True
    assert w.put == {tenant_secret_ref("A", "hubspot"): SECRET}


def test_probe_inconclusive_stores_with_verified_null():
    w = RecordingWriter()
    c = _client(IntegrationsDeps(secret_writer=w, token_prober=lambda s, t: None))
    r = c.post("/integrations/hubspot/credentials", json={"token": SECRET}, headers=H)
    assert r.status_code == 200
    assert r.json()["verified"] is None  # stored, honestly unverified
    assert w.put == {tenant_secret_ref("A", "hubspot"): SECRET}


# --------------------------------------------------------------------------- #
# async sync (run store wired)
# --------------------------------------------------------------------------- #
def test_async_sync_202_then_background_success_recorded():
    runs = InMemorySyncRunStore()
    runner_calls: list[tuple[str, str]] = []

    def runner(tenant_id, name):
        runner_calls.append((tenant_id, name))
        return SyncResult(pulled=3, landed_rows=3, chunks=5, embedded=4, skipped=1)

    deps = IntegrationsDeps(secret_writer=_connected_writer(), sync_runner=runner,
                            sync_runs=runs)
    c = _client(deps)
    r = c.post("/integrations/hubspot/sync", headers=H)
    assert r.status_code == 202
    body = r.json()
    assert body["name"] == "hubspot"
    assert body["run"]["status"] == "running"  # the row as opened, pre-completion
    # TestClient executes background tasks before returning — the run is now final.
    assert runner_calls == [("A", "hubspot")]
    final = runs.list_runs("A", "hubspot")[0]
    assert final["status"] == "succeeded"
    assert final["pulled"] == 3 and final["embedded"] == 4 and final["skipped"] == 1


def test_async_sync_failure_records_class_name_only():
    runs = InMemorySyncRunStore()

    def runner(tenant_id, name):
        raise RuntimeError("provider said: token xyz is bad")  # must never surface

    deps = IntegrationsDeps(secret_writer=_connected_writer(), sync_runner=runner,
                            sync_runs=runs)
    c = _client(deps)
    r = c.post("/integrations/hubspot/sync", headers=H)
    assert r.status_code == 202  # the kick succeeded; the failure is the RUN's status
    final = runs.list_runs("A", "hubspot")[0]
    assert final["status"] == "failed"
    assert final["error"] == "RuntimeError"  # class name only, never the message


def test_concurrent_sync_409_via_single_runner_guard():
    runs = InMemorySyncRunStore()
    # A run already in flight for this tenant+source:
    assert runs.start("A", "hubspot") is not None
    deps = IntegrationsDeps(secret_writer=_connected_writer(),
                            sync_runner=lambda t, n: SyncResult(), sync_runs=runs)
    c = _client(deps)
    r = c.post("/integrations/hubspot/sync", headers=H)
    assert r.status_code == 409
    assert "already running" in r.json()["detail"]


def test_sync_without_run_store_stays_inline_200():
    """Legacy/storeless path unchanged: inline result, 200 (the original contract)."""
    deps = IntegrationsDeps(secret_writer=_connected_writer(),
                            sync_runner=lambda t, n: SyncResult(pulled=2, landed_rows=2),
                            sync_runs=None)
    c = _client(deps)
    r = c.post("/integrations/hubspot/sync", headers=H)
    assert r.status_code == 200
    assert r.json()["result"]["pulled"] == 2


# --------------------------------------------------------------------------- #
# last_sync in the listing + the history endpoint
# --------------------------------------------------------------------------- #
def test_listing_carries_last_sync_and_history_flag():
    runs = InMemorySyncRunStore()
    opened = runs.start("A", "hubspot")
    runs.finish("A", opened["id"], status="succeeded", metrics={"pulled": 7, "landed_rows": 7})
    c = _client(IntegrationsDeps(secret_writer=_connected_writer(), sync_runs=runs))
    r = c.get("/integrations", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["sync_history_configured"] is True
    by_name = {i["name"]: i for i in body["integrations"]}
    assert by_name["hubspot"]["last_sync"]["status"] == "succeeded"
    assert by_name["hubspot"]["last_sync"]["pulled"] == 7
    assert by_name["stripe"]["last_sync"] is None   # no run recorded — never invented
    assert by_name["csv"]["last_sync"] is None      # file-kind never has one


def test_listing_without_store_flags_history_unconfigured():
    c = _client(IntegrationsDeps())
    body = c.get("/integrations", headers=H).json()
    assert body["sync_history_configured"] is False
    assert all(i["last_sync"] is None for i in body["integrations"])


def test_history_endpoint_contract():
    runs = InMemorySyncRunStore()
    first = runs.start("A", "hubspot")
    runs.finish("A", first["id"], status="failed", error="RuntimeError")
    second = runs.start("A", "hubspot")
    runs.finish("A", second["id"], status="succeeded", metrics={"pulled": 1})
    c = _client(IntegrationsDeps(sync_runs=runs))
    # auth + shape guards
    assert c.get("/integrations/hubspot/syncs").status_code == 401
    assert c.get("/integrations/nope/syncs", headers=H).status_code == 404
    assert c.get("/integrations/csv/syncs", headers=H).status_code == 409
    r = c.get("/integrations/hubspot/syncs", headers=H)
    assert r.status_code == 200
    rows = r.json()["runs"]
    assert [x["status"] for x in rows] == ["succeeded", "failed"]  # newest first


def test_history_503_when_storeless():
    c = _client(IntegrationsDeps())
    assert c.get("/integrations/hubspot/syncs", headers=H).status_code == 503
