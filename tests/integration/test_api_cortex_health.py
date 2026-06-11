"""Integration: GET /cortex/health — the #194 ml/health.py seam, mounted (read-only).

Proves the api half of the Cortex health vertical slice (shapes mirror
test_api_knowledge.py / test_api_agents.py):
  * 401 unauth (the shared current_tenant dependency)
  * the payload IS ml.health.cortex_health over the wired registry + prediction log —
    champion/version-count/drift for THIS tenant
  * honest degradation: default deps (no registry) -> "no_registry"; registry without a
    champion -> "no_champion"; no prediction log -> drift None — never invented model state,
    never a 500
  * THE TRUST RULE: the tenant comes ONLY from the verified claim — a smuggled ?tenant_id=
    neither errors nor changes whose health is read
  * READ-ONLY: only GET is mounted (POST/PUT/PATCH/DELETE -> 405)
  * cortex=None skips mounting entirely (404)
  * IMPORT SAFETY: api.cortex_routes imports no boto3/ml at module import (ml.health is lazy)
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.cortex_routes import CortexDeps
from api.views import SavedViews
from ml.predictions import InMemoryPredictionLog
from ml.registry import InMemoryRegistry, ModelRecord

H = {"Authorization": "Bearer t"}
TENANT = "A"


class FakeVerifier:
    def verify(self, token):
        return {"sub": "uA", "custom:tenant_id": TENANT, "email": "a@x.com"}


def _registry(champion_for: str = TENANT) -> InMemoryRegistry:
    reg = InMemoryRegistry()
    reg._by_tenant[champion_for] = [
        ModelRecord(tenant_id=champion_for, version=1, estimator_name="logreg",
                    metrics={"auc": 0.71}, model=None, is_champion=False),
        ModelRecord(tenant_id=champion_for, version=2, estimator_name="gbm",
                    metrics={"auc": 0.83}, model=None, is_champion=True),
    ]
    return reg


def _client(cortex: CortexDeps | None) -> TestClient:
    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
        executor=lambda a: None, cortex=cortex,
    )
    return TestClient(create_app(deps))


# ---------------- auth ----------------
@pytest.mark.integration
def test_unauth_is_401():
    client = _client(CortexDeps())
    assert client.get("/cortex/health").status_code == 401


# ---------------- the payload is ml.health.cortex_health ----------------
@pytest.mark.integration
def test_serving_payload_champion_and_versions():
    client = _client(CortexDeps(registry=_registry()))
    r = client.get("/cortex/health", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == TENANT
    assert body["status"] == "serving"
    assert body["model_count"] == 2
    assert body["champion"] == {"version": 2, "estimator": "gbm", "metrics": {"auc": 0.83}}
    assert body["drift"] is None  # no prediction log wired -> drift honestly absent


@pytest.mark.integration
def test_drift_leg_reports_insufficient_evidence_not_a_number():
    log = InMemoryPredictionLog()  # empty: zero resolved (score, outcome) pairs
    client = _client(CortexDeps(registry=_registry(), prediction_log=log))
    body = client.get("/cortex/health", headers=H).json()
    assert body["status"] == "serving"
    assert body["drift"]["drift"] is False
    assert body["drift"]["recent_auc"] is None
    assert "insufficient live evidence" in body["drift"]["reason"]


# ---------------- honest degradation ----------------
@pytest.mark.integration
def test_default_deps_mount_the_honest_no_registry_shape():
    client = _client(CortexDeps())  # the ApiDeps default — nothing wired
    r = client.get("/cortex/health", headers=H)
    assert r.status_code == 200
    assert r.json() == {"tenant_id": TENANT, "status": "no_registry", "champion": None,
                        "model_count": 0, "drift": None}


@pytest.mark.integration
def test_no_champion_is_reported_not_invented():
    reg = InMemoryRegistry()
    reg._by_tenant[TENANT] = [
        ModelRecord(tenant_id=TENANT, version=1, estimator_name="logreg",
                    metrics={"auc": 0.6}, model=None, is_champion=False),
    ]
    body = _client(CortexDeps(registry=reg)).get("/cortex/health", headers=H).json()
    assert body["status"] == "no_champion"
    assert body["champion"] is None
    assert body["model_count"] == 1


# ---------------- the trust rule ----------------
@pytest.mark.integration
def test_smuggled_tenant_id_changes_nothing():
    reg = _registry(champion_for="B")  # only tenant B has models
    client = _client(CortexDeps(registry=reg))
    r = client.get("/cortex/health", params={"tenant_id": "B"}, headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == TENANT       # the verified claim, not the query param
    assert body["model_count"] == 0          # tenant A sees nothing of B's registry
    assert body["status"] == "no_champion"


# ---------------- surface shape ----------------
@pytest.mark.integration
def test_read_only_only_get_is_mounted():
    client = _client(CortexDeps())
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/cortex/health", headers=H).status_code == 405


@pytest.mark.integration
def test_cortex_none_skips_mounting():
    client = _client(None)
    assert client.get("/cortex/health", headers=H).status_code == 404


# ---------------- import safety ----------------
@pytest.mark.integration
def test_route_module_import_is_lazy():
    """api.cortex_routes must not import ml (or boto3) at module import — the ml.health import
    happens inside the request handler. Subprocess so the parent run can't mask it."""
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    probe = (
        "import sys\n"
        "import api.cortex_routes\n"
        "bad = [m for m in sys.modules if m == 'boto3' or m.startswith('boto3.')\n"
        "       or m == 'ml' or m.startswith('ml.')]\n"
        "assert not bad, f'eager imports: {bad}'\n"
        "print('CORTEX-ROUTE-IMPORT-OK')\n"
    )
    proc = subprocess.run([sys.executable, "-c", probe], cwd=repo, capture_output=True,
                          text=True, timeout=120,
                          env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(repo)})
    assert proc.returncode == 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    assert "CORTEX-ROUTE-IMPORT-OK" in proc.stdout
