"""Unit: the scheduled-retrain entrypoint (ml.retrain.run_scheduled_retrain +
scripts/ml/retrain_tenant.py) and live drift with REAL inputs.

The audit gap this closes: retrain_tenant was called only by tests — now there is an invokable
producer path (loader -> bake-off -> improvement-gated promotion -> outcome sync -> live drift)
and a CLI for the scheduled one-off task. Offline: InMemory/LocalFs registries, static loaders.
"""
import importlib.util
import json
import os
import random
import sys

import pytest

from ml.data_loader import StaticTrainingDataLoader
from ml.health import cortex_health
from ml.predictions import MIN_LIVE_SAMPLES, InMemoryPredictionLog
from ml.registry import InMemoryRegistry, LocalFsRegistry
from ml.retrain import (
    MIN_TRAINING_RECORDS,
    live_drift_check,
    run_scheduled_retrain,
    sync_outcomes,
)

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


class CliStepModel:
    """Module-level so pickle/joblib round-trips (the resign-CLI test seeds a legacy blob)."""

    name = "step"

    def predict_proba(self, X):
        return [0.5 for _ in X]


def _synthetic(n=300, seed=2, *, separable=True):
    """Loader-shaped records; separable=False randomizes labels (nothing to learn)."""
    rng = random.Random(seed)
    recs = []
    for i in range(n):
        amount = rng.uniform(0, 10000)
        acts = rng.randint(0, 20)
        has_email = rng.random() < 0.7
        signal = amount / 10000 + acts / 20 + (0.3 if has_email else 0)
        booked = (1 if signal + rng.uniform(-0.3, 0.3) > 1.0 else 0) if separable \
            else rng.randint(0, 1)
        recs.append({"deal_id": f"d{i}", "amount": amount, "n_activities": acts,
                     "days_since_created": rng.randint(0, 90),
                     "email": "x@y.com" if has_email else None, "phone": None,
                     "booked": booked})
    return recs


# --------------------------------------------------------------------------- entrypoint

@pytest.mark.unit
def test_scheduled_retrain_trains_promotes_and_writes_metrics():
    reg = InMemoryRegistry()
    out = run_scheduled_retrain(reg, StaticTrainingDataLoader(_synthetic()), "t1", seed=0)
    assert out["status"] == "trained" and out["promoted"] is True
    champ = reg.champion("t1")
    assert champ.version == out["version"]
    assert champ.metrics["auc"] > 0.7            # genuinely learned, metrics in the registry
    assert out["n_records"] == 300


@pytest.mark.unit
def test_scheduled_retrain_improves_or_holds_never_degrades():
    reg = InMemoryRegistry()
    first = run_scheduled_retrain(reg, StaticTrainingDataLoader(_synthetic()), "t1", seed=0)
    champion_auc = reg.champion("t1").metrics["auc"]

    # A later retrain on label noise produces a ~0.5-AUC challenger: registered, NOT promoted.
    second = run_scheduled_retrain(
        reg, StaticTrainingDataLoader(_synthetic(seed=7, separable=False)), "t1", seed=0)
    assert second["status"] == "trained"
    assert second["promoted"] is False
    champ = reg.champion("t1")
    assert champ.version == first["version"]                 # champion held
    assert champ.metrics["auc"] == champion_auc              # never degraded
    assert len(reg.versions("t1")) == 2                      # challenger + metrics still recorded


@pytest.mark.unit
def test_scheduled_retrain_skips_thin_data_without_registering_junk():
    reg = InMemoryRegistry()
    out = run_scheduled_retrain(
        reg, StaticTrainingDataLoader(_synthetic(n=MIN_TRAINING_RECORDS - 1)), "t1")
    assert out["status"] == "skipped" and "labeled records" in out["reason"]
    assert reg.versions("t1") == []


@pytest.mark.unit
def test_scheduled_retrain_skips_single_class_labels():
    records = [dict(r, booked=1) for r in _synthetic(n=50)]
    reg = InMemoryRegistry()
    out = run_scheduled_retrain(reg, StaticTrainingDataLoader(records), "t1")
    assert out["status"] == "skipped" and "single-class" in out["reason"]
    assert reg.versions("t1") == []


# --------------------------------------------------------------------------- drift with real inputs

@pytest.mark.unit
def test_outcome_sync_resolves_logged_predictions():
    log = InMemoryPredictionLog()
    records = _synthetic(n=40)
    for rec in records[:25]:                       # scored while the deals were open
        log.log("t1", deal_id=rec["deal_id"], model_version=1, score=0.5)
    resolved = sync_outcomes(log, "t1", records)
    assert resolved == 25
    assert len(log.scored_outcomes("t1")) == 25


@pytest.mark.unit
def test_live_drift_check_reports_insufficient_evidence_honestly():
    reg = InMemoryRegistry()
    run_scheduled_retrain(reg, StaticTrainingDataLoader(_synthetic()), "t1", seed=0)
    out = live_drift_check(reg, "t1", InMemoryPredictionLog())
    assert out["drift"] is False
    assert out["recent_auc"] is None and "insufficient live evidence" in out["reason"]


@pytest.mark.unit
def test_live_drift_check_flags_real_degradation():
    reg = InMemoryRegistry()
    run_scheduled_retrain(reg, StaticTrainingDataLoader(_synthetic()), "t1", seed=0)
    log = InMemoryPredictionLog()
    for i in range(MIN_LIVE_SAMPLES * 2):          # live scores anti-correlated with outcomes
        outcome = i % 2
        log.log("t1", deal_id=f"d{i}", model_version=1, score=0.9 - 0.8 * outcome)
        log.record_outcome("t1", f"d{i}", outcome)
    out = live_drift_check(reg, "t1", log)
    assert out["drift"] is True
    assert out["recent_auc"] == 0.0 and out["n_outcomes"] == MIN_LIVE_SAMPLES * 2


@pytest.mark.unit
def test_scheduled_retrain_with_prediction_log_syncs_and_checks_drift():
    reg = InMemoryRegistry()
    log = InMemoryPredictionLog()
    records = _synthetic(n=60)
    for rec in records:                            # the champion scored these while open
        log.log("t1", deal_id=rec["deal_id"], model_version=1,
                score=min(rec["amount"] / 10000 + rec["n_activities"] / 20, 1.0))
    out = run_scheduled_retrain(reg, StaticTrainingDataLoader(records), "t1",
                                prediction_log=log, seed=0)
    assert out["status"] == "trained"
    assert out["outcomes_synced"] == 60
    assert out["drift"]["n_outcomes"] == 60        # drift computed from REAL pairs
    assert out["drift"]["recent_auc"] is not None


# --------------------------------------------------------------------------- health surface

@pytest.mark.unit
def test_cortex_health_shapes():
    assert cortex_health(None, "t1")["status"] == "no_registry"
    reg = InMemoryRegistry()
    assert cortex_health(reg, "t1")["status"] == "no_champion"

    run_scheduled_retrain(reg, StaticTrainingDataLoader(_synthetic()), "t1", seed=0)
    out = cortex_health(reg, "t1", InMemoryPredictionLog())
    assert out["status"] == "serving"
    assert out["champion"]["version"] == 1 and out["champion"]["metrics"]["auc"] > 0.7
    assert out["drift"]["drift"] is False and out["drift"]["recent_auc"] is None

    log = InMemoryPredictionLog()                  # anti-correlated live evidence -> drifting
    for i in range(MIN_LIVE_SAMPLES * 2):
        outcome = i % 2
        log.log("t1", deal_id=f"d{i}", model_version=1, score=0.9 - 0.8 * outcome)
        log.record_outcome("t1", f"d{i}", outcome)
    drifting = cortex_health(reg, "t1", log)
    assert drifting["status"] == "drifting" and drifting["drift"]["drift"] is True


@pytest.mark.unit
def test_cortex_health_never_loads_artifacts(tmp_path, monkeypatch):
    """Health is metadata-only: it must work even when the artifact would be UNREADABLE."""
    monkeypatch.setenv("CORTEX_SIGNING_KEY", "k")
    reg = LocalFsRegistry(tmp_path)
    reg.promote("t1", CliStepModel(), {"estimator_name": "step", "metrics": {"auc": 0.8}})
    monkeypatch.delenv("CORTEX_SIGNING_KEY")       # loads would now fail...
    out = cortex_health(LocalFsRegistry(tmp_path), "t1")
    assert out["status"] == "serving"              # ...but health never deserializes
    assert out["champion"]["metrics"] == {"auc": 0.8}


# --------------------------------------------------------------------------- the CLIs

def _load_script(name):
    path = os.path.join(ROOT, "scripts", "ml", f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.unit
def test_retrain_cli_trains_from_records_json_into_local_registry(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CORTEX_S3_BUCKET", raising=False)
    monkeypatch.setenv("CORTEX_LOCAL_DIR", str(tmp_path / "registry"))
    monkeypatch.setenv("CORTEX_SIGNING_KEY", "cli-test-key")
    records_path = tmp_path / "records.json"
    records_path.write_text(json.dumps(_synthetic()))

    mod = _load_script("retrain_tenant")
    rc = mod.main(["--tenant", "t1", "--records-json", str(records_path)])
    assert rc == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["status"] == "trained" and result["promoted"] is True
    # Durable: the champion is loadable by "another process" over the same root.
    champ = LocalFsRegistry(str(tmp_path / "registry")).champion("t1")
    assert champ is not None and champ.version == result["version"]


@pytest.mark.unit
def test_retrain_cli_fails_clean_when_unconfigured(tmp_path, monkeypatch, capsys):
    for var in ("CORTEX_S3_BUCKET", "CORTEX_LOCAL_DIR"):
        monkeypatch.delenv(var, raising=False)
    mod = _load_script("retrain_tenant")
    assert mod.main(["--tenant", "t1"]) == 2
    assert "no model registry configured" in capsys.readouterr().out

    # Registry but no data source -> clean config failure too (never a fabricated run).
    monkeypatch.setenv("CORTEX_LOCAL_DIR", str(tmp_path))
    for var in ("UPLIFT_DB_URL", "DB_USER", "DB_PASS", "DB_HOST"):
        monkeypatch.delenv(var, raising=False)
    assert mod.main(["--tenant", "t1"]) == 2
    assert "no data source" in capsys.readouterr().out


@pytest.mark.unit
def test_retrain_cli_fails_clean_without_signing_key(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CORTEX_S3_BUCKET", raising=False)
    monkeypatch.delenv("CORTEX_SIGNING_KEY", raising=False)
    monkeypatch.setenv("CORTEX_LOCAL_DIR", str(tmp_path / "registry"))
    records_path = tmp_path / "records.json"
    records_path.write_text(json.dumps(_synthetic()))
    mod = _load_script("retrain_tenant")
    rc = mod.main(["--tenant", "t1", "--records-json", str(records_path)])
    assert rc == 2
    assert "CORTEX_SIGNING_KEY" in capsys.readouterr().out


@pytest.mark.unit
def test_resign_cli_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CORTEX_S3_BUCKET", raising=False)
    monkeypatch.setenv("CORTEX_LOCAL_DIR", str(tmp_path))
    monkeypatch.setenv("CORTEX_SIGNING_KEY", "cli-test-key")
    # Seed a legacy tenant (v1 manifest + raw-pickle blob) the way the old registry wrote it.
    import pickle

    tdir = tmp_path / "t1"
    (tdir / "models").mkdir(parents=True)
    (tdir / "registry.json").write_text(json.dumps(
        {"format_version": 1, "champion_version": 1,
         "models": [{"version": 1, "estimator_name": "step", "metrics": {}, "metadata": {}}]}))
    (tdir / "models" / "1.bin").write_bytes(
        b"uplift-cortex-model/v1\n" + pickle.dumps(CliStepModel()))

    mod = _load_script("resign_artifacts")
    assert mod.main([]) == 0
    out = capsys.readouterr().out
    assert "resigned" in out and "clean" in out
    assert LocalFsRegistry(str(tmp_path)).champion("t1") is not None
    # Second run: idempotent, still clean.
    assert mod.main([]) == 0
