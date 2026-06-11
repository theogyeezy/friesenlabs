"""Unit: the Cortex retrain fan-out (scripts/ml/retrain_all.py) — per-tenant iteration,
failure containment, and exit codes. No DB/AWS: the registry/loader/retrain are patched."""
import importlib

import pytest

mod = importlib.import_module("scripts.ml.retrain_all")


class FakeRegistry:
    def __init__(self, tenants):
        self._tenants = list(tenants)

    def tenant_ids(self):
        return list(self._tenants)


# --------------------------------------------------------------------------- resolve_tenants
@pytest.mark.unit
def test_resolve_tenants_explicit_wins_and_dedups():
    reg = FakeRegistry(["a", "b", "c"])
    assert mod.resolve_tenants(reg, ["t1", "t1", " t2 ", ""]) == ["t1", "t2"]


@pytest.mark.unit
def test_resolve_tenants_defaults_to_registry():
    reg = FakeRegistry(["a", "a", "b"])
    assert mod.resolve_tenants(reg, None) == ["a", "b"]


# --------------------------------------------------------------------------- retrain_one
@pytest.mark.unit
def test_retrain_one_success(monkeypatch):
    monkeypatch.setattr(mod, "run_scheduled_retrain",
                        lambda *a, **k: {"promoted": True, "version": 4})
    out = mod.retrain_one(object(), object(), "t1", prediction_log=None, seed=0)
    assert out == {"tenant": "t1", "ok": True, "result": {"promoted": True, "version": 4}}


@pytest.mark.unit
def test_retrain_one_contains_exception(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("loader exploded")
    monkeypatch.setattr(mod, "run_scheduled_retrain", _boom)
    out = mod.retrain_one(object(), object(), "t1", prediction_log=None, seed=0)
    assert out["ok"] is False and "RuntimeError" in out["error"]


@pytest.mark.unit
def test_retrain_one_contains_signing_error(monkeypatch):
    from ml.registry import SigningKeyError

    def _boom(*a, **k):
        raise SigningKeyError("no key")
    monkeypatch.setattr(mod, "run_scheduled_retrain", _boom)
    out = mod.retrain_one(object(), object(), "t1", prediction_log=None, seed=0)
    assert out["ok"] is False and out["error"].startswith("signing:")


# --------------------------------------------------------------------------- main / exit codes
def _wire(monkeypatch, *, registry, dsn="postgresql://crm_app@h/db", retrain=None):
    monkeypatch.setattr(mod, "registry_from_env", lambda: registry)
    monkeypatch.setattr(mod, "dsn_from_env", lambda: dsn)
    monkeypatch.setattr(mod, "PgTrainingDataLoader", lambda d: object())
    monkeypatch.setattr(mod, "PgPredictionLog", lambda d: object())
    if retrain is not None:
        monkeypatch.setattr(mod, "run_scheduled_retrain", retrain)


@pytest.mark.unit
def test_main_no_registry_is_usage_error(monkeypatch):
    _wire(monkeypatch, registry=None)
    assert mod.main([]) == 2


@pytest.mark.unit
def test_main_no_dsn_is_usage_error(monkeypatch):
    _wire(monkeypatch, registry=FakeRegistry(["t1"]), dsn="")
    assert mod.main([]) == 2


@pytest.mark.unit
def test_main_empty_registry_is_clean(monkeypatch):
    _wire(monkeypatch, registry=FakeRegistry([]), retrain=lambda *a, **k: {})
    assert mod.main([]) == 0


@pytest.mark.unit
def test_main_all_success_exits_zero(monkeypatch):
    seen = []
    _wire(monkeypatch, registry=FakeRegistry(["t1", "t2"]),
          retrain=lambda reg, loader, tenant, **k: seen.append(tenant) or {"ok": tenant})
    assert mod.main([]) == 0
    assert seen == ["t1", "t2"]   # every tenant retrained


@pytest.mark.unit
def test_main_any_failure_exits_one(monkeypatch):
    def _retrain(reg, loader, tenant, **k):
        if tenant == "t2":
            raise RuntimeError("boom")
        return {"ok": tenant}
    _wire(monkeypatch, registry=FakeRegistry(["t1", "t2", "t3"]), retrain=_retrain)
    # t2 fails but t1+t3 still run; exit 1 so the schedule alarm can page.
    assert mod.main([]) == 1


@pytest.mark.unit
def test_main_explicit_tenant_subset(monkeypatch):
    seen = []
    _wire(monkeypatch, registry=FakeRegistry(["a", "b", "c"]),
          retrain=lambda reg, loader, tenant, **k: seen.append(tenant) or {})
    assert mod.main(["--tenant", "only1", "--tenant", "only2"]) == 0
    assert seen == ["only1", "only2"]   # registry ignored when explicit
