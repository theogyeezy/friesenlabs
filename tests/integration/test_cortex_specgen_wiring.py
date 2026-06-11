"""Integration: the persistent Cortex registry + the default spec generator ride the tool
contexts (api/asgi.py executor + conversation factory, worker/worker.py clients).

- `run_model` returns a REAL score + model_version through the HTTP /actions path when a champion
  exists in a LocalFsRegistry (durable: a fresh instance over the same root = another process).
- Without a registry, run_model keeps its clean degradation ("no model registry configured").
- The worker builds ToolContext.cortex from CORTEX_LOCAL_DIR (shared/config names) and the
  unconfigured boot stays byte-identical (no stray keys).
- `build_view` runs against the wired AnthropicSpecGenerator (fake client — no network) through
  /actions; with NO generator wired the current explicit-raise behavior is preserved.

No AWS, no Anthropic, no Postgres.
"""
import json

import pytest
from fastapi.testclient import TestClient

import api.asgi as asgi
from agents.runtime import FakeRuntime
from agents.tools.base import ToolContext
from agents.tools.run_model import RunModel
from agents.tools.spec_generator import AnthropicSpecGenerator
from agents.workspace_store import InMemoryWorkspaceStore
from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.control.types import Action
from api.views import SavedViews
from ml.registry import LocalFsRegistry
from worker import worker


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch):
    """The persistent registry writes/reads HMAC-SIGNED artifacts (ml/artifacts.py) — every test
    here gets a key, exactly like the deployed task env."""
    monkeypatch.setenv("CORTEX_SIGNING_KEY", "test-signing-key")


class StubModel:
    """Tiny sklearn-free estimator (module-level so the registry pickle round-trips)."""

    name = "stub"

    def __init__(self, score: float = 0.73):
        self.score = score

    def predict_proba(self, X):
        return [self.score for _ in X]


class _FakeVerifier:
    """Offline verifier — tenant identity still flows ONLY from the 'verified claim'."""

    def verify(self, token):
        return {"sub": "user-A", "custom:tenant_id": "tenant-A", "email": "a@x.com"}


H = {"Authorization": "Bearer t"}


def _app(executor, *, greenlight=None):
    deps = ApiDeps(
        verifier=_FakeVerifier(),
        greenlight=greenlight or Greenlight(),
        saved_views=SavedViews(),
        conversation_factory=lambda tenant_id: None,
        autonomy_config=AutonomyConfig(),
        executor=executor,
    )
    return TestClient(create_app(deps))


# --------------------------------------------------------------------------- run_model + registry
@pytest.mark.integration
def test_run_model_returns_real_score_from_localfs_champion_via_actions(tmp_path):
    # Champion promoted by "the retrain job" (writer instance) ...
    writer = LocalFsRegistry(tmp_path)
    writer.promote("tenant-A", StubModel(score=0.73),
                   {"estimator_name": "stub", "metrics": {"auc": 0.81}})

    # ... loads in "another process" (a FRESH registry instance over the same root) wired into the
    # executor's ToolContext — through the real HTTP /actions path.
    executor = asgi.make_executor(greenlight=Greenlight(), cortex=LocalFsRegistry(tmp_path))
    r = _app(executor).post(
        "/actions",
        json={"name": "run_model", "payload": {"record": {"amount": 5000, "email": "x@y.com"}}},
        headers=H,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["decision"] == "auto"
    scored = body["result"]["result"]
    assert scored["score"] == 0.73          # the REAL champion scored — not a degraded None
    assert scored["model_version"] == 1
    assert scored["estimator"] == "stub"


@pytest.mark.integration
def test_run_model_degrades_cleanly_without_a_registry():
    executor = asgi.make_executor(greenlight=Greenlight())  # no cortex wired
    r = _app(executor).post(
        "/actions", json={"name": "run_model", "payload": {"record": {"amount": 1}}}, headers=H
    )
    assert r.status_code == 200
    scored = r.json()["result"]["result"]
    assert scored["score"] is None
    assert "no model registry" in scored["reason"]


@pytest.mark.integration
def test_run_model_scores_tenant_scoped_champion_only(tmp_path):
    # Tenant isolation at the registry layer: tenant-B has no champion even when tenant-A does.
    LocalFsRegistry(tmp_path).promote("tenant-A", StubModel(score=0.9),
                                      {"estimator_name": "stub", "metrics": {"auc": 0.8}})
    reg = LocalFsRegistry(tmp_path)
    out_b = RunModel().invoke(ToolContext(tenant_id="tenant-B", cortex=reg), record={"amount": 1})
    assert out_b["result"]["score"] is None
    assert "no champion model" in out_b["result"]["reason"]
    out_a = RunModel().invoke(ToolContext(tenant_id="tenant-A", cortex=reg), record={"amount": 1})
    assert out_a["result"]["score"] == 0.9 and out_a["result"]["model_version"] == 1


# --------------------------------------------------------------------------- worker wiring
@pytest.mark.integration
def test_worker_builds_cortex_from_env_and_threads_it_into_tool_context(tmp_path, monkeypatch):
    for var in ("UPLIFT_DB_URL", "DB_USER", "DB_PASS", "DB_HOST", "CUBE_ENDPOINT",
                "CORTEX_S3_BUCKET", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CORTEX_LOCAL_DIR", str(tmp_path))

    # Promote a champion as the retrain job would, into the SAME configured root.
    LocalFsRegistry(tmp_path).promote("tenant-A", StubModel(score=0.42),
                                      {"estimator_name": "stub", "metrics": {"auc": 0.8}})

    clients = worker.build_clients_from_env()
    assert isinstance(clients["cortex"], LocalFsRegistry)
    assert "spec_generator" not in clients  # no key in env -> no generator on the worker

    # Per-call context: tenant from session metadata (stamped from the verified claim upstream).
    ctx = worker.build_context({"tenant_id": "tenant-A"}, clients)
    out = RunModel().invoke(ctx, record={"amount": 100})
    assert out["result"]["score"] == 0.42
    assert out["result"]["model_version"] == 1


@pytest.mark.integration
def test_worker_unconfigured_boot_is_byte_identical(monkeypatch):
    # All-unset: no cortex/spec_generator keys appear — the pinned unconfigured shape holds.
    for var in ("UPLIFT_DB_URL", "DB_USER", "DB_PASS", "DB_HOST", "CUBE_ENDPOINT",
                "CORTEX_S3_BUCKET", "CORTEX_LOCAL_DIR", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert worker.build_clients_from_env() == {
        "db": None, "rag": None, "cube": None, "greenlight": None
    }


@pytest.mark.integration
def test_worker_spec_generator_is_env_guarded(monkeypatch, tmp_path):
    # Dev parity only — the org key must never be on the worker in prod (shared/config.py).
    for var in ("UPLIFT_DB_URL", "DB_USER", "DB_PASS", "DB_HOST", "CUBE_ENDPOINT",
                "CORTEX_S3_BUCKET", "CORTEX_LOCAL_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    clients = worker.build_clients_from_env()
    assert isinstance(clients["spec_generator"], AnthropicSpecGenerator)
    ctx = worker.build_context({"tenant_id": "tenant-A"}, clients)
    assert ctx.extra["generate_spec"] is clients["spec_generator"]


# --------------------------------------------------------------------------- spec generator seam
ALLOWED = ["Deals.pipeline_value", "Deals.count", "Deals.stage"]


class _FakeCube:
    def members(self, tenant_id):
        return list(ALLOWED)


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


class _FakeMessages:
    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp(self._payloads.pop(0))


class _FakeAnthropic:
    def __init__(self, payloads):
        self.messages = _FakeMessages(payloads)


def _good_spec():
    return {
        "view_id": "pipeline", "title": "Pipeline", "semantic_refs": ["Deals.pipeline_value"],
        "layout": [{"type": "kpi", "metric": "Deals.pipeline_value"}],
    }


@pytest.mark.integration
def test_build_view_uses_wired_default_generator_via_actions(tmp_path):
    gen = AnthropicSpecGenerator(client=_FakeAnthropic([json.dumps(_good_spec())]))
    executor = asgi.make_executor(greenlight=Greenlight(), cube=_FakeCube(), spec_generator=gen)
    r = _app(executor).post(
        "/actions", json={"name": "build_view", "payload": {"request": "show pipeline"}}, headers=H
    )
    assert r.status_code == 200
    out = r.json()["result"]["result"]
    assert out["status"] == "valid"
    assert out["spec"] == _good_spec()
    # The generator really was called with the tenant's Cube catalog.
    assert gen._client.messages.calls, "the wired generator should have been invoked"


@pytest.mark.integration
def test_build_view_without_generator_preserves_explicit_raise():
    # Env-guarded default: with no key/generator wired, ctx.extra carries no 'generate_spec' and
    # build_view keeps the current programming-error raise (never a silent degraded mode).
    executor = asgi.make_executor(greenlight=Greenlight(), cube=_FakeCube())
    action = Action(name="build_view", tenant_id="tenant-A", payload={"request": "show pipeline"})
    with pytest.raises(RuntimeError, match="generate_spec"):
        executor(action)


# --------------------------------------------------------------------------- conversation factory
@pytest.mark.integration
def test_conversation_factory_threads_cortex_and_spec_generator(tmp_path):
    store = InMemoryWorkspaceStore()
    store.upsert("tenant-A", "ws-A", "env-A", "coord-A")
    reg = LocalFsRegistry(tmp_path)
    gen = AnthropicSpecGenerator(client=_FakeAnthropic([]))
    factory = asgi.make_conversation_factory(
        workspace_store=store, runtime_factory=lambda row: FakeRuntime(),
        cortex=reg, spec_generator=gen,
    )
    convo = factory("tenant-A")
    assert convo.cortex is reg
    assert convo.spec_generator is gen
    ctx = convo._tool_ctx()
    assert ctx.cortex is reg
    assert ctx.extra["generate_spec"] is gen
