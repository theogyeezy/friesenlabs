"""Unit: persistent tenant-scoped Cortex registry (LocalFs + fully-mocked S3).

Proves the TODO AI/P2 contract: a champion promoted in one process loads in another (simulated by
fresh registry instances over the same store), predict_proba is reproducible after the round trip,
the S3 path needs no boto3/network, and corrupt / wrong-version blobs are REJECTED loudly.
"""
import io
import json
import os
import random

import pytest

from agents.tools.base import ToolContext
from agents.tools.run_model import RunModel
from ml.registry import (
    DEFAULT_S3_PREFIX,
    FORMAT_VERSION,
    LocalFsRegistry,
    RegistryFormatError,
    S3Registry,
    evaluate_and_gate,
    registry_from_env,
)
from ml.retrain import retrain_tenant


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch):
    """Persistent registries now write/read HMAC-SIGNED artifacts (ml/artifacts.py) — give every
    test in this file a key, exactly like the deployed task env. The unsigned/invalid/missing-key
    refusal behavior itself is covered in test_ml_artifacts.py."""
    monkeypatch.setenv("CORTEX_SIGNING_KEY", "test-signing-key")


class StepModel:
    """Deterministic picklable estimator stand-in (module-level so pickle round-trips)."""

    name = "step"

    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold

    def predict_proba(self, X):
        return [0.9 if row[0] >= self.threshold else 0.1 for row in X]


def _synthetic(n=300, seed=2):
    rng = random.Random(seed)
    recs = []
    for _ in range(n):
        amount = rng.uniform(0, 10000)
        acts = rng.randint(0, 20)
        has_email = rng.random() < 0.7
        score = amount / 10000 + acts / 20 + (0.3 if has_email else 0)
        recs.append({"amount": amount, "n_activities": acts, "days_since_created": rng.randint(0, 90),
                     "email": "x@y.com" if has_email else None, "phone": None,
                     "booked": 1 if score + rng.uniform(-0.3, 0.3) > 1.0 else 0})
    return recs


# --------------------------------------------------------------------------- LocalFs round trip

@pytest.mark.unit
def test_promote_then_champion_loads_in_a_fresh_instance(tmp_path):
    writer = LocalFsRegistry(tmp_path)
    rec = writer.promote("t1", StepModel(threshold=3.0),
                         {"estimator_name": "step", "metrics": {"auc": 0.8}, "trained_at": "2026-06-09"})
    assert rec.is_champion and rec.version == 1

    # A brand-new instance over the same root = "another process".
    reader = LocalFsRegistry(tmp_path)
    champ = reader.champion("t1")
    assert champ is not None
    assert champ.version == 1
    assert champ.estimator_name == "step"
    assert champ.metrics == {"auc": 0.8}
    assert champ.metadata == {"trained_at": "2026-06-09"}
    assert champ.is_champion is True
    # The artifact genuinely deserialized: it scores, and identically to the original.
    X = [[1.0], [5.0]]
    assert champ.model.predict_proba(X) == StepModel(threshold=3.0).predict_proba(X) == [0.1, 0.9]


@pytest.mark.unit
def test_retrain_promotes_to_persistent_registry_and_scores_reproducibly(tmp_path):
    # The real pipeline (featurize -> bake-off -> gate) through the persistent seam.
    reg = LocalFsRegistry(tmp_path)
    out = retrain_tenant(reg, "t1", _synthetic(), seed=0)
    assert out["promoted"] is True

    in_process = reg.champion("t1")
    cross_process = LocalFsRegistry(tmp_path).champion("t1")
    assert cross_process.version == in_process.version == out["version"]
    # predict_proba reproducible after the (de)serialization round trip.
    from ml import features
    X = features.featurize(_synthetic(n=20, seed=9))
    assert cross_process.model.predict_proba(X) == in_process.model.predict_proba(X)


@pytest.mark.unit
def test_persistent_registry_is_tenant_scoped(tmp_path):
    reg = LocalFsRegistry(tmp_path)
    reg.promote("t1", StepModel(), {"estimator_name": "step", "metrics": {"auc": 0.8}})
    assert reg.champion("t2") is None
    assert reg.versions("t2") == []
    # Tenant ids that could traverse the key space are rejected outright (defense in depth).
    with pytest.raises(ValueError):
        reg.champion("../t1")
    with pytest.raises(ValueError):
        reg.promote("t1/../t2", StepModel(), {})


@pytest.mark.unit
def test_versions_and_challenger_listing_are_metadata_only(tmp_path):
    reg = LocalFsRegistry(tmp_path)
    reg.register("t1", "step", {"auc": 0.6}, StepModel())               # v1 challenger
    reg.promote("t1", StepModel(), {"estimator_name": "step", "metrics": {"auc": 0.8}})  # v2 champion

    fresh = LocalFsRegistry(tmp_path)
    versions = fresh.versions("t1")
    assert [v.version for v in versions] == [1, 2]
    assert all(v.model is None for v in versions)  # listings never pull artifacts
    challengers = fresh.challengers("t1")
    assert [c.version for c in challengers] == [1]
    assert challengers[0].is_champion is False
    assert fresh.champion("t1").version == 2


@pytest.mark.unit
def test_evaluate_and_gate_persists_promotion_across_instances(tmp_path):
    reg = LocalFsRegistry(tmp_path)
    first = reg.register("t1", "step", {"auc": 0.80}, StepModel())
    assert evaluate_and_gate(reg, "t1", first) is True
    # Within-noise challenger does NOT flip the champion...
    noise = reg.register("t1", "step", {"auc": 0.805}, StepModel())
    assert evaluate_and_gate(reg, "t1", noise) is False
    assert LocalFsRegistry(tmp_path).champion("t1").version == first.version
    # ...a clearly-better one does, durably.
    better = reg.register("t1", "step", {"auc": 0.90}, StepModel())
    assert evaluate_and_gate(reg, "t1", better) is True
    assert LocalFsRegistry(tmp_path).champion("t1").version == better.version


@pytest.mark.unit
def test_set_champion_rejects_unknown_version(tmp_path):
    reg = LocalFsRegistry(tmp_path)
    reg.register("t1", "step", {"auc": 0.8}, StepModel())
    with pytest.raises(ValueError):
        reg.set_champion("t1", 99)


# --------------------------------------------------------------------------- format rejection

def _champion_blob_path(tmp_path, tenant="t1", version=1):
    return os.path.join(str(tmp_path), tenant, "models", f"{version}.bin")


@pytest.mark.unit
def test_headerless_blob_is_rejected(tmp_path):
    reg = LocalFsRegistry(tmp_path)
    reg.promote("t1", StepModel(), {"estimator_name": "step", "metrics": {"auc": 0.8}})
    with open(_champion_blob_path(tmp_path), "wb") as f:
        f.write(b"\x80\x05 definitely not our header\n123")
    with pytest.raises(RegistryFormatError, match="header"):
        LocalFsRegistry(tmp_path).champion("t1")


@pytest.mark.unit
def test_unknown_format_version_is_rejected(tmp_path):
    reg = LocalFsRegistry(tmp_path)
    reg.promote("t1", StepModel(), {"estimator_name": "step", "metrics": {"auc": 0.8}})
    with open(_champion_blob_path(tmp_path), "wb") as f:
        f.write(f"uplift-cortex-model/v{FORMAT_VERSION + 99}\n".encode() + b"payload")
    with pytest.raises(RegistryFormatError, match="format version"):
        LocalFsRegistry(tmp_path).champion("t1")


@pytest.mark.unit
def test_truncated_payload_is_rejected(tmp_path):
    reg = LocalFsRegistry(tmp_path)
    reg.promote("t1", StepModel(), {"estimator_name": "step", "metrics": {"auc": 0.8}})
    path = _champion_blob_path(tmp_path)
    blob = open(path, "rb").read()
    with open(path, "wb") as f:
        f.write(blob[: len(blob) - 7])  # valid header, torn pickle payload
    with pytest.raises(RegistryFormatError, match="corrupt"):
        LocalFsRegistry(tmp_path).champion("t1")


@pytest.mark.unit
def test_corrupt_and_wrong_version_manifest_rejected(tmp_path):
    reg = LocalFsRegistry(tmp_path)
    reg.promote("t1", StepModel(), {"estimator_name": "step", "metrics": {"auc": 0.8}})
    manifest_path = os.path.join(str(tmp_path), "t1", "registry.json")
    with open(manifest_path, "w") as f:
        json.dump({"format_version": FORMAT_VERSION + 1, "champion_version": 1, "models": []}, f)
    with pytest.raises(RegistryFormatError, match="manifest format version"):
        LocalFsRegistry(tmp_path).champion("t1")
    with open(manifest_path, "w") as f:
        f.write("{ not json")
    with pytest.raises(RegistryFormatError, match="not valid JSON"):
        LocalFsRegistry(tmp_path).champion("t1")


# --------------------------------------------------------------------------- S3 path (mocked)

class _NoSuchKey(Exception):
    pass


class FakeS3Client:
    """boto3 s3-client stand-in: get_object/put_object over a dict + exceptions.NoSuchKey."""

    class exceptions:
        NoSuchKey = _NoSuchKey

    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket, Key, Body):
        self.objects[(Bucket, Key)] = bytes(Body)

    def get_object(self, *, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise _NoSuchKey(Key)
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


@pytest.mark.unit
def test_s3_registry_round_trip_cross_instance_no_boto3():
    fake = FakeS3Client()
    writer = S3Registry("models-bucket", "cortex/registry", client=fake)
    rec = writer.promote("t1", StepModel(threshold=2.0),
                         {"estimator_name": "step", "metrics": {"auc": 0.77}})
    assert rec.version == 1 and rec.is_champion

    # Fresh instance over the same (fake) bucket = another process / the worker.
    reader = S3Registry("models-bucket", "cortex/registry", client=fake)
    champ = reader.champion("t1")
    assert champ.version == 1
    assert champ.metrics == {"auc": 0.77}
    assert champ.model.predict_proba([[1.0], [3.0]]) == [0.1, 0.9]
    # Tenant-scoped, prefix-rooted keys; nothing outside the prefix.
    keys = sorted(k for _, k in fake.objects)
    assert keys == ["cortex/registry/t1/models/1.bin", "cortex/registry/t1/registry.json"]
    # Unknown tenant: NoSuchKey -> empty manifest -> clean None (no exception).
    assert reader.champion("t9") is None


@pytest.mark.unit
def test_s3_registry_requires_bucket_and_defaults_prefix():
    with pytest.raises(ValueError):
        S3Registry("")
    reg = S3Registry("b", "", client=FakeS3Client())
    assert reg.prefix == DEFAULT_S3_PREFIX


# --------------------------------------------------------------------------- env factory

@pytest.mark.unit
def test_registry_from_env_unconfigured_returns_none(monkeypatch):
    for var in ("CORTEX_S3_BUCKET", "CORTEX_S3_PREFIX", "CORTEX_LOCAL_DIR"):
        monkeypatch.delenv(var, raising=False)
    assert registry_from_env() is None


@pytest.mark.unit
def test_registry_from_env_localfs_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("CORTEX_S3_BUCKET", raising=False)
    monkeypatch.setenv("CORTEX_LOCAL_DIR", str(tmp_path))
    reg = registry_from_env()
    assert isinstance(reg, LocalFsRegistry)
    assert reg.root == str(tmp_path)


@pytest.mark.unit
def test_registry_from_env_s3_wins_and_stays_lazy(monkeypatch, tmp_path):
    monkeypatch.setenv("CORTEX_S3_BUCKET", "models-bucket")
    monkeypatch.setenv("CORTEX_LOCAL_DIR", str(tmp_path))  # S3 outranks the local fallback
    monkeypatch.delenv("CORTEX_S3_PREFIX", raising=False)
    reg = registry_from_env()
    assert isinstance(reg, S3Registry)
    assert reg.bucket == "models-bucket"
    assert reg.prefix == DEFAULT_S3_PREFIX
    assert reg._client is None  # boto3 untouched at construction — lazy until first blob access
    monkeypatch.setenv("CORTEX_S3_PREFIX", "custom/prefix/")
    assert registry_from_env().prefix == "custom/prefix"


@pytest.mark.unit
def test_cortex_env_names_live_in_shared_config():
    from shared import config
    assert config.ENV_CORTEX_S3_BUCKET == "CORTEX_S3_BUCKET"
    assert config.ENV_CORTEX_S3_PREFIX == "CORTEX_S3_PREFIX"
    assert config.ENV_CORTEX_LOCAL_DIR == "CORTEX_LOCAL_DIR"
    # Safe '' defaults on the Config fields (unset = stub cleanly, never touch AWS).
    fields = config.Config.__dataclass_fields__
    for name in ("cortex_s3_bucket", "cortex_s3_prefix", "cortex_local_dir"):
        assert name in fields


# --------------------------------------------------------------------------- run_model over it

@pytest.mark.unit
def test_run_model_scores_via_persistent_champion(tmp_path):
    reg = LocalFsRegistry(tmp_path)
    retrain_tenant(reg, "t1", _synthetic(), seed=0)

    # A different instance, as the worker process would build via registry_from_env().
    ctx = ToolContext(tenant_id="t1", cortex=LocalFsRegistry(tmp_path))
    out = RunModel().invoke(ctx, record={"amount": 9000, "n_activities": 18, "email": "a@b.com"})
    assert out["status"] == "ok"
    assert out["result"]["score"] is not None and 0.0 <= out["result"]["score"] <= 1.0
    assert out["result"]["model_version"] == 1
    assert out["result"]["estimator"]
    # No champion for another tenant -> clean no-champion result, never another tenant's model.
    out2 = RunModel().invoke(ToolContext(tenant_id="t2", cortex=LocalFsRegistry(tmp_path)),
                             record={"amount": 1})
    assert out2["result"] == {"score": None, "reason": "no champion model for tenant"}


@pytest.mark.unit
def test_run_model_degrades_cleanly_on_unreadable_champion(tmp_path):
    reg = LocalFsRegistry(tmp_path)
    reg.promote("t1", StepModel(), {"estimator_name": "step", "metrics": {"auc": 0.8}})
    with open(_champion_blob_path(tmp_path), "wb") as f:
        f.write(b"garbage")
    out = RunModel().invoke(ToolContext(tenant_id="t1", cortex=LocalFsRegistry(tmp_path)),
                            record={"amount": 1})
    assert out["status"] == "ok"
    assert out["result"]["score"] is None
    assert "unreadable" in out["result"]["reason"]
