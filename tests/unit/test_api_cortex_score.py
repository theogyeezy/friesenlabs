"""GET /cortex/score?deal_id=<uuid> — the champion's per-deal score, claims-bound.

Mirrors test_api_cortex_health.py's harness (in-memory registry + prediction log, TestClient):
  * 401 unauth (the shared current_tenant dependency)
  * {score, model_version} for a deal the champion has scored (real logged number, not invented)
  * honest null score + "no_prediction" when the champion never scored the deal
  * honest 503 no_registry / no_champion when no model exists (never a fabricated score)
  * a malformed deal_id is a 400, never a 500
  * THE TRUST RULE: a smuggled ?tenant_id= changes nothing — only the verified claim decides
"""
from __future__ import annotations

import uuid

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
DEAL = str(uuid.uuid4())


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
@pytest.mark.unit
def test_unauth_is_401():
    client = _client(CortexDeps(registry=_registry()))
    assert client.get("/cortex/score", params={"deal_id": DEAL}).status_code == 401


# ---------------- a real logged score ----------------
@pytest.mark.unit
def test_scored_deal_returns_score_and_model_version():
    log = InMemoryPredictionLog()
    log.log(TENANT, deal_id=DEAL, model_version=2, score=0.77)
    client = _client(CortexDeps(registry=_registry(), prediction_log=log))
    r = client.get("/cortex/score", params={"deal_id": DEAL}, headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "scored"
    assert body["score"] == 0.77
    assert body["model_version"] == 2
    assert body["deal_id"] == DEAL
    assert body["champion_metrics"] == {"auc": 0.83}


@pytest.mark.unit
def test_score_is_the_most_recent_logged_prediction():
    log = InMemoryPredictionLog()
    log.log(TENANT, deal_id=DEAL, model_version=1, score=0.40)
    log.log(TENANT, deal_id=DEAL, model_version=2, score=0.90)  # newer wins
    client = _client(CortexDeps(registry=_registry(), prediction_log=log))
    body = client.get("/cortex/score", params={"deal_id": DEAL}, headers=H).json()
    assert body["score"] == 0.90
    assert body["model_version"] == 2


# ---------------- honest absence, never a fabricated number ----------------
@pytest.mark.unit
def test_champion_but_no_prediction_is_null_score_not_invented():
    client = _client(CortexDeps(registry=_registry(), prediction_log=InMemoryPredictionLog()))
    r = client.get("/cortex/score", params={"deal_id": DEAL}, headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "no_prediction"
    assert body["score"] is None
    assert body["model_version"] == 2  # the champion is real even without a per-deal score


@pytest.mark.unit
def test_no_prediction_log_dep_is_null_score():
    body = _client(CortexDeps(registry=_registry())).get(
        "/cortex/score", params={"deal_id": DEAL}, headers=H).json()
    assert body["status"] == "no_prediction"
    assert body["score"] is None
    assert body["model_version"] == 2


@pytest.mark.unit
def test_no_registry_is_503_no_registry():
    r = _client(CortexDeps()).get("/cortex/score", params={"deal_id": DEAL}, headers=H)
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "no_registry"
    assert body["score"] is None and body["model_version"] is None


@pytest.mark.unit
def test_no_champion_is_503_no_champion():
    reg = InMemoryRegistry()
    reg._by_tenant[TENANT] = [
        ModelRecord(tenant_id=TENANT, version=1, estimator_name="logreg",
                    metrics={"auc": 0.6}, model=None, is_champion=False),
    ]
    r = _client(CortexDeps(registry=reg)).get("/cortex/score", params={"deal_id": DEAL}, headers=H)
    assert r.status_code == 503
    assert r.json()["status"] == "no_champion"


# ---------------- input hygiene ----------------
@pytest.mark.unit
def test_malformed_deal_id_is_400_not_500():
    r = _client(CortexDeps(registry=_registry())).get(
        "/cortex/score", params={"deal_id": "not-a-uuid"}, headers=H)
    assert r.status_code == 400
    assert r.json()["status"] == "bad_request"


@pytest.mark.unit
def test_missing_deal_id_is_422():
    # deal_id is a required query param — FastAPI rejects its absence before any work.
    assert _client(CortexDeps(registry=_registry())).get(
        "/cortex/score", headers=H).status_code == 422


# ---------------- the trust rule ----------------
@pytest.mark.unit
def test_smuggled_tenant_id_changes_nothing():
    # Only tenant B has a champion + a logged score; the caller is tenant A.
    reg = _registry(champion_for="B")
    log = InMemoryPredictionLog()
    log.log("B", deal_id=DEAL, model_version=2, score=0.95)
    client = _client(CortexDeps(registry=reg, prediction_log=log))
    r = client.get("/cortex/score", params={"deal_id": DEAL, "tenant_id": "B"}, headers=H)
    # Tenant A has no champion -> 503, and never sees B's 0.95 score.
    assert r.status_code == 503
    body = r.json()
    assert body["tenant_id"] == TENANT
    assert body["status"] == "no_champion"
    assert body["score"] is None
