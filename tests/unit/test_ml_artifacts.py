"""Unit: signed model artifacts (ml/artifacts.py) + the legacy re-sign migration.

The RCE-via-bucket-write contract: nothing is ever deserialized unless its HMAC (under the
out-of-band CORTEX_SIGNING_KEY) verifies first — unsigned (legacy v1), tampered, truncated,
wrong-key, and missing-key blobs are all REFUSED. The migration shim re-signs exactly the
legacy blobs and never "fixes" a bad signature.
"""
import io
import json
import os
import pickle

import pytest

from agents.tools.base import ToolContext
from agents.tools.run_model import RunModel
from ml import artifacts
from ml.migrate_artifacts import (
    ALREADY_SIGNED,
    BAD_SIGNATURE,
    RESIGNED,
    UNREADABLE,
    resign_registry,
    resign_tenant,
)
from ml.registry import (
    FORMAT_VERSION,
    LocalFsRegistry,
    RegistryFormatError,
    S3Registry,
    SigningKeyError,
)

KEY = b"unit-test-signing-key"


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch):
    monkeypatch.setenv("CORTEX_SIGNING_KEY", KEY.decode())


class StepModel:
    """Deterministic estimator stand-in (module-level so serialization round-trips)."""

    name = "step"

    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold

    def predict_proba(self, X):
        return [0.9 if row[0] >= self.threshold else 0.1 for row in X]


def _legacy_v1_blob(model) -> bytes:
    """A pre-signing blob exactly as the old registry wrote it (header + raw pickle)."""
    return b"uplift-cortex-model/v1\n" + pickle.dumps(model)


# --------------------------------------------------------------------------- envelope behavior

@pytest.mark.unit
def test_format_version_is_bumped_for_signed_artifacts():
    assert FORMAT_VERSION == artifacts.SIGNED_FORMAT_VERSION == 2


@pytest.mark.unit
def test_sign_then_load_round_trip():
    blob = artifacts.serialize_model(StepModel(threshold=3.0))
    model = artifacts.deserialize_model(blob)
    assert model.predict_proba([[1.0], [5.0]]) == [0.1, 0.9]
    assert artifacts.is_signed_current(blob)


@pytest.mark.unit
def test_unsigned_legacy_v1_blob_is_refused():
    blob = _legacy_v1_blob(StepModel())
    with pytest.raises(RegistryFormatError, match="unsigned legacy v1"):
        artifacts.deserialize_model(blob)
    assert not artifacts.is_signed_current(blob)


@pytest.mark.unit
def test_tampered_payload_is_refused():
    blob = bytearray(artifacts.serialize_model(StepModel()))
    blob[-1] ^= 0xFF  # flip one payload byte — the MAC must catch it
    with pytest.raises(RegistryFormatError, match="signature verification"):
        artifacts.deserialize_model(bytes(blob))


@pytest.mark.unit
def test_signature_swap_under_a_different_key_is_refused():
    blob = artifacts.serialize_model(StepModel(), key=b"some-other-key")
    with pytest.raises(RegistryFormatError, match="signature verification"):
        artifacts.deserialize_model(blob)  # verifies under CORTEX_SIGNING_KEY -> mismatch


@pytest.mark.unit
def test_missing_signature_line_is_refused():
    blob = b"uplift-cortex-model/v2\nnot-a-hex-mac"
    with pytest.raises(RegistryFormatError, match="signature"):
        artifacts.deserialize_model(blob)


@pytest.mark.unit
def test_missing_key_refuses_both_read_and_write(monkeypatch):
    blob = artifacts.serialize_model(StepModel())
    monkeypatch.delenv("CORTEX_SIGNING_KEY", raising=False)
    with pytest.raises(SigningKeyError, match="CORTEX_SIGNING_KEY"):
        artifacts.deserialize_model(blob)
    with pytest.raises(SigningKeyError, match="CORTEX_SIGNING_KEY"):
        artifacts.serialize_model(StepModel())
    # SigningKeyError IS a RegistryFormatError, so every existing degrade path catches it.
    assert issubclass(SigningKeyError, RegistryFormatError)


@pytest.mark.unit
def test_run_model_degrades_cleanly_when_key_is_missing(tmp_path, monkeypatch):
    reg = LocalFsRegistry(tmp_path)
    reg.promote("t1", StepModel(), {"estimator_name": "step", "metrics": {"auc": 0.8}})
    monkeypatch.delenv("CORTEX_SIGNING_KEY", raising=False)
    out = RunModel().invoke(ToolContext(tenant_id="t1", cortex=LocalFsRegistry(tmp_path)),
                            record={"amount": 1})
    assert out["status"] == "ok"
    assert out["result"]["score"] is None
    assert "unreadable" in out["result"]["reason"]


@pytest.mark.unit
def test_legacy_loader_reads_only_v1():
    legacy = _legacy_v1_blob(StepModel(threshold=2.0))
    model = artifacts.deserialize_legacy_v1(legacy)
    assert model.predict_proba([[3.0]]) == [0.9]
    with pytest.raises(RegistryFormatError, match="only v1"):
        artifacts.deserialize_legacy_v1(artifacts.serialize_model(StepModel()))


# --------------------------------------------------------------------------- migration shim

def _seed_legacy_registry(root, tenant="t1", *, champion_version=1):
    """Hand-craft a pre-signing registry: v1 manifest + raw-pickle blob (the old on-disk state)."""
    tdir = os.path.join(str(root), tenant)
    os.makedirs(os.path.join(tdir, "models"), exist_ok=True)
    manifest = {"format_version": 1, "champion_version": champion_version,
                "models": [{"version": 1, "estimator_name": "step",
                            "metrics": {"auc": 0.8}, "metadata": {}}]}
    with open(os.path.join(tdir, "registry.json"), "w") as f:
        json.dump(manifest, f)
    with open(os.path.join(tdir, "models", "1.bin"), "wb") as f:
        f.write(_legacy_v1_blob(StepModel(threshold=3.0)))


@pytest.mark.unit
def test_resign_migrates_legacy_registry_so_champion_loads(tmp_path):
    _seed_legacy_registry(tmp_path)
    reg = LocalFsRegistry(tmp_path)
    # Before migration the serving path refuses both the manifest and the blob.
    with pytest.raises(RegistryFormatError):
        reg.champion("t1")

    report = resign_tenant(reg, "t1")
    assert report["versions"] == {1: RESIGNED}
    assert report["manifest"] == "bumped"

    champ = LocalFsRegistry(tmp_path).champion("t1")  # fresh instance = another process
    assert champ.version == 1
    assert champ.model.predict_proba([[1.0], [5.0]]) == [0.1, 0.9]

    # Idempotent: a second run touches nothing.
    report2 = resign_tenant(LocalFsRegistry(tmp_path), "t1")
    assert report2["versions"] == {1: ALREADY_SIGNED}
    assert report2["manifest"] == "current"


@pytest.mark.unit
def test_resign_dry_run_writes_nothing(tmp_path):
    _seed_legacy_registry(tmp_path)
    reg = LocalFsRegistry(tmp_path)
    report = resign_tenant(reg, "t1", dry_run=True)
    assert report["versions"] == {1: RESIGNED} and report["manifest"] == "needs_bump"
    with pytest.raises(RegistryFormatError):  # still legacy on disk
        LocalFsRegistry(tmp_path).champion("t1")


@pytest.mark.unit
def test_resign_never_blesses_a_bad_signature(tmp_path):
    reg = LocalFsRegistry(tmp_path)
    reg.promote("t1", StepModel(), {"estimator_name": "step", "metrics": {"auc": 0.8}})
    blob_path = os.path.join(str(tmp_path), "t1", "models", "1.bin")
    tampered = bytearray(open(blob_path, "rb").read())
    tampered[-1] ^= 0xFF
    with open(blob_path, "wb") as f:
        f.write(bytes(tampered))

    report = resign_tenant(LocalFsRegistry(tmp_path), "t1")
    assert report["versions"] == {1: BAD_SIGNATURE}
    with pytest.raises(RegistryFormatError):  # still refused — tampering is never laundered
        LocalFsRegistry(tmp_path).champion("t1")


@pytest.mark.unit
def test_resign_reports_unreadable_blobs(tmp_path):
    _seed_legacy_registry(tmp_path)
    blob_path = os.path.join(str(tmp_path), "t1", "models", "1.bin")
    with open(blob_path, "wb") as f:
        f.write(b"complete garbage, no header")
    report = resign_tenant(LocalFsRegistry(tmp_path), "t1")
    assert report["versions"] == {1: UNREADABLE}


@pytest.mark.unit
def test_resign_registry_walks_all_tenants_localfs(tmp_path):
    _seed_legacy_registry(tmp_path, "t1")
    _seed_legacy_registry(tmp_path, "t2")
    reg = LocalFsRegistry(tmp_path)
    assert reg.tenant_ids() == ["t1", "t2"]
    reports = resign_registry(reg)
    assert sorted(r["tenant_id"] for r in reports) == ["t1", "t2"]
    assert all(r["versions"] == {1: RESIGNED} for r in reports)


# --------------------------------------------------------------------------- S3 tenant listing

class _NoSuchKey(Exception):
    pass


class FakeS3Client:
    """get/put/list_objects_v2 over a dict (CommonPrefixes emulated for Delimiter='/')."""

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

    def list_objects_v2(self, *, Bucket, Prefix, Delimiter, ContinuationToken=None):
        assert Delimiter == "/"
        prefixes = sorted({
            key[: len(Prefix)] + key[len(Prefix):].split("/", 1)[0] + "/"
            for (bucket, key) in self.objects
            if bucket == Bucket and key.startswith(Prefix) and "/" in key[len(Prefix):]
        })
        return {"CommonPrefixes": [{"Prefix": p} for p in prefixes], "IsTruncated": False}


@pytest.mark.unit
def test_s3_registry_lists_tenants_and_resigns(tmp_path):
    fake = FakeS3Client()
    reg = S3Registry("models-bucket", "cortex/registry", client=fake)
    reg.promote("t-signed", StepModel(), {"estimator_name": "step", "metrics": {"auc": 0.8}})
    # Plant a legacy tenant directly in the (fake) bucket.
    fake.put_object(Bucket="models-bucket", Key="cortex/registry/t-legacy/registry.json",
                    Body=json.dumps({"format_version": 1, "champion_version": 1,
                                     "models": [{"version": 1, "estimator_name": "step",
                                                 "metrics": {}, "metadata": {}}]}).encode())
    fake.put_object(Bucket="models-bucket", Key="cortex/registry/t-legacy/models/1.bin",
                    Body=_legacy_v1_blob(StepModel(threshold=3.0)))

    assert reg.tenant_ids() == ["t-legacy", "t-signed"]
    reports = {r["tenant_id"]: r for r in resign_registry(reg)}
    assert reports["t-legacy"]["versions"] == {1: RESIGNED}
    assert reports["t-signed"]["versions"] == {1: ALREADY_SIGNED}
    champ = S3Registry("models-bucket", "cortex/registry", client=fake).champion("t-legacy")
    assert champ.model.predict_proba([[5.0]]) == [0.9]
