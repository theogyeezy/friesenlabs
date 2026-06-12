"""Per-tenant model registry + champion/challenger gate (Build Guide Phase 8, Step 46).

Each tenant has its own registry of versioned models with their held-out metrics. A new model only
promotes to champion if it beats the incumbent on held-out data (by a margin) — champion/challenger.

Two registry families share one protocol (register / champion / versions / challengers /
set_champion / promote), so `evaluate_and_gate` + `ml.retrain.retrain_tenant` accept either:

- `InMemoryRegistry` — the offline/test fake (process-local, nothing durable).
- `PersistentRegistry` — durable + tenant-scoped: `S3Registry` (prod; boto3 imported
  lazily on first blob access) with `LocalFsRegistry` as the dev/tests fallback. A champion
  promoted in one process loads in another. Construction is wired via `registry_from_env()` —
  the seam the worker (`build_clients_from_env`, clients["cortex"]) and the API conversation
  factory inject in the next cycle.

Storage layout (identical for S3 and local fs, rooted at the configured prefix/dir):
    <root>/<tenant_id>/registry.json        — manifest: format_version, champion_version, models[]
    <root>/<tenant_id>/models/<version>.bin — SIGNED joblib blob of the fitted estimator

Serialization safety (ml/artifacts.py): every model blob is an HMAC-SHA256-signed joblib payload
(key = the CORTEX_SIGNING_KEY env secret) behind the `uplift-cortex-model/v<N>` header, and is
REJECTED (`RegistryFormatError`) when the header is missing/malformed, the format version is
unknown or pre-signing (legacy v1 — migrate via scripts/ml/resign_artifacts.py), the signature is
missing or wrong, or the payload fails to load. Verification happens BEFORE any deserialization,
so a writer who can plant a blob in the registry bucket cannot make this process execute it
(the RCE-via-bucket-write fix). No signing key configured = loads AND writes fail closed
(SigningKeyError) — never a silent unsigned fallback.

Concurrency: single-writer per tenant (the scheduled retrain job); `run_model` only reads.
Manifest writes are last-writer-wins, which is safe under that assumption.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from shared.config import ENV_CORTEX_LOCAL_DIR, ENV_CORTEX_S3_BUCKET, ENV_CORTEX_S3_PREFIX

# RegistryFormatError/SigningKeyError are defined in ml.artifacts and re-exported here — this
# module stays the stable import site (agents/tools/run_model.py, tests).
from .artifacts import (  # noqa: F401
    SIGNED_FORMAT_VERSION,
    RegistryFormatError,
    SigningKeyError,
    deserialize_model,
    serialize_model,
)

# A challenger must beat the champion's AUC by at least this margin to promote (avoid churn on noise).
PROMOTION_MARGIN = 0.01

# Bumped on any breaking change to the manifest/blob shape; readers reject other versions loudly
# (a silent mis-parse of a model artifact is far worse than a failed load). v2 = signed artifacts.
FORMAT_VERSION = SIGNED_FORMAT_VERSION
DEFAULT_S3_PREFIX = "cortex/registry"
# Defense in depth on storage key paths. Tenant identity already comes only from the verified
# claim (THE TRUST RULE) — this just guarantees a tenant id can never traverse the key space.
_TENANT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass
class ModelRecord:
    tenant_id: str
    version: int
    estimator_name: str
    metrics: dict
    model: Any                     # the fitted estimator (None on metadata-only listings)
    is_champion: bool = False
    metadata: dict = field(default_factory=dict)


@dataclass
class InMemoryRegistry:
    """Process-local registry — the offline/test fake. Same protocol as PersistentRegistry."""

    _by_tenant: dict[str, list[ModelRecord]] = field(default_factory=dict)

    def _versions(self, tenant_id: str) -> list[ModelRecord]:
        return self._by_tenant.setdefault(tenant_id, [])

    def register(self, tenant_id: str, estimator_name: str, metrics: dict, model: Any, *,
                 metadata: dict | None = None) -> ModelRecord:
        versions = self._versions(tenant_id)
        rec = ModelRecord(tenant_id, len(versions) + 1, estimator_name, metrics, model,
                          metadata=dict(metadata or {}))
        versions.append(rec)
        return rec

    def champion(self, tenant_id: str) -> ModelRecord | None:
        return next((r for r in self._versions(tenant_id) if r.is_champion), None)

    def versions(self, tenant_id: str) -> list[ModelRecord]:
        return list(self._versions(tenant_id))

    def challengers(self, tenant_id: str) -> list[ModelRecord]:
        return [r for r in self._versions(tenant_id) if not r.is_champion]

    def set_champion(self, tenant_id: str, version: int) -> None:
        """Make `version` the tenant's champion (demoting any incumbent)."""
        records = self._versions(tenant_id)
        if not any(r.version == version for r in records):
            raise ValueError(f"unknown model version {version} for tenant {tenant_id!r}")
        for r in records:
            r.is_champion = r.version == version

    def promote(self, tenant_id: str, model: Any, metadata: dict) -> ModelRecord:
        """Register `model` as a new version AND make it the champion (same shape as persistent)."""
        meta = dict(metadata or {})
        name = meta.pop("estimator_name", getattr(model, "name", type(model).__name__))
        rec = self.register(tenant_id, name, meta.pop("metrics", {}), model, metadata=meta)
        self.set_champion(tenant_id, rec.version)
        return rec


# --------------------------------------------------------------------------- persistent registry

# Signed (de)serialization lives in ml/artifacts.py; thin aliases keep this module's seam names.
_serialize_model = serialize_model
_deserialize_model = deserialize_model


def _safe_tenant(tenant_id: str) -> str:
    if not tenant_id or not _TENANT_ID_RE.match(tenant_id):
        raise ValueError(f"invalid tenant_id for registry path: {tenant_id!r}")
    return tenant_id


class PersistentRegistry:
    """Durable tenant-scoped registry over a blob backend (subclasses provide `_get`/`_put`).

    Listing calls (`versions`/`challengers`) return metadata-only records (`model=None`) so they
    never pull artifacts; `champion()` loads + deserializes the live artifact for scoring.
    """

    # ---- backend seam (LocalFsRegistry / S3Registry implement these) ----
    def _get(self, key: str) -> bytes | None:
        raise NotImplementedError

    def _put(self, key: str, data: bytes) -> None:
        raise NotImplementedError

    def _list_tenant_ids(self) -> list[str]:
        raise NotImplementedError

    def tenant_ids(self) -> list[str]:
        """All tenant ids with a registry under this root (ops/migration surface — e.g.
        scripts/ml/resign_artifacts.py). Filtered through the same safe-tenant pattern as
        every key builder, so a stray object can never smuggle a traversal segment back in."""
        return sorted(t for t in self._list_tenant_ids() if t and _TENANT_ID_RE.match(t))

    # ---- keys + manifest ----
    @staticmethod
    def _manifest_key(tenant_id: str) -> str:
        return f"{_safe_tenant(tenant_id)}/registry.json"

    @staticmethod
    def _blob_key(tenant_id: str, version: int) -> str:
        return f"{_safe_tenant(tenant_id)}/models/{int(version)}.bin"

    def _load_manifest(self, tenant_id: str) -> dict:
        raw = self._get(self._manifest_key(tenant_id))
        if raw is None:  # tenant has no registry yet
            return {"format_version": FORMAT_VERSION, "champion_version": None, "models": []}
        try:
            manifest = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise RegistryFormatError("registry manifest is corrupt (not valid JSON)") from exc
        if manifest.get("format_version") != FORMAT_VERSION:
            raise RegistryFormatError(
                f"unsupported manifest format version {manifest.get('format_version')!r} "
                f"(this build reads v{FORMAT_VERSION})"
            )
        return manifest

    def _save_manifest(self, tenant_id: str, manifest: dict) -> None:
        self._put(self._manifest_key(tenant_id),
                  json.dumps(manifest, sort_keys=True).encode("utf-8"))

    def _record(self, tenant_id: str, entry: dict, *, champion_version: int | None,
                model: Any = None) -> ModelRecord:
        return ModelRecord(
            tenant_id=tenant_id,
            version=entry["version"],
            estimator_name=entry["estimator_name"],
            metrics=entry.get("metrics", {}),
            model=model,
            is_champion=entry["version"] == champion_version,
            metadata=entry.get("metadata", {}),
        )

    # ---- writes ----
    def register(self, tenant_id: str, estimator_name: str, metrics: dict, model: Any, *,
                 metadata: dict | None = None) -> ModelRecord:
        """Store a new (non-champion) version: blob first, then the manifest entry."""
        manifest = self._load_manifest(tenant_id)
        version = max((m["version"] for m in manifest["models"]), default=0) + 1
        self._put(self._blob_key(tenant_id, version), _serialize_model(model))
        entry = {"version": version, "estimator_name": estimator_name, "metrics": metrics,
                 "metadata": dict(metadata or {})}
        manifest["models"].append(entry)
        self._save_manifest(tenant_id, manifest)
        return self._record(tenant_id, entry,
                            champion_version=manifest.get("champion_version"), model=model)

    def set_champion(self, tenant_id: str, version: int) -> None:
        """Point the tenant's champion at an already-registered version (demotes the incumbent)."""
        manifest = self._load_manifest(tenant_id)
        if not any(m["version"] == version for m in manifest["models"]):
            raise ValueError(f"unknown model version {version} for tenant {tenant_id!r}")
        manifest["champion_version"] = version
        self._save_manifest(tenant_id, manifest)

    def promote(self, tenant_id: str, model: Any, metadata: dict) -> ModelRecord:
        """Register `model` as a new version AND make it the tenant's champion.

        `metadata` carries at minimum `estimator_name` + `metrics`; any extra keys (trained_at,
        n_train, data_window, ...) are stored verbatim on the manifest entry.
        """
        meta = dict(metadata or {})
        name = meta.pop("estimator_name", getattr(model, "name", type(model).__name__))
        rec = self.register(tenant_id, name, meta.pop("metrics", {}), model, metadata=meta)
        self.set_champion(tenant_id, rec.version)
        rec.is_champion = True
        return rec

    # ---- reads ----
    def champion(self, tenant_id: str) -> ModelRecord | None:
        """Load the tenant's champion WITH its artifact (ready for predict_proba), or None."""
        manifest = self._load_manifest(tenant_id)
        champion_version = manifest.get("champion_version")
        if champion_version is None:
            return None
        entry = next((m for m in manifest["models"] if m["version"] == champion_version), None)
        if entry is None:
            raise RegistryFormatError(
                f"manifest names champion v{champion_version} but lists no such model")
        blob = self._get(self._blob_key(tenant_id, champion_version))
        if blob is None:
            raise RegistryFormatError(f"champion artifact v{champion_version} is missing from the store")
        return self._record(tenant_id, entry, champion_version=champion_version,
                            model=_deserialize_model(blob))

    def versions(self, tenant_id: str) -> list[ModelRecord]:
        """Metadata-only listing (model=None). Use champion() to load the live artifact."""
        manifest = self._load_manifest(tenant_id)
        champion_version = manifest.get("champion_version")
        return [self._record(tenant_id, m, champion_version=champion_version)
                for m in manifest["models"]]

    def challengers(self, tenant_id: str) -> list[ModelRecord]:
        """All non-champion versions (metadata-only)."""
        return [r for r in self.versions(tenant_id) if not r.is_champion]


class LocalFsRegistry(PersistentRegistry):
    """Filesystem-backed registry — the dev/tests fallback. Same layout + (de)serialization as
    S3Registry, so a cross-instance round trip proven here proves the persistence contract."""

    def __init__(self, root: str | os.PathLike):
        self.root = str(root)

    def _path(self, key: str) -> str:
        return os.path.join(self.root, key)

    def _get(self, key: str) -> bytes | None:
        try:
            with open(self._path(key), "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def _put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)  # atomic publish — a reader never sees a torn file

    def _list_tenant_ids(self) -> list[str]:
        try:
            entries = os.listdir(self.root)
        except FileNotFoundError:
            return []
        return [e for e in entries if os.path.isdir(os.path.join(self.root, e))]


class S3Registry(PersistentRegistry):
    """S3-backed registry (prod). boto3 is imported LAZILY on first blob access — importing this
    module / constructing this class (e.g. via `registry_from_env`) needs no AWS deps and creates
    no live resources. Tests inject a fake `client`; nothing here touches the network then.
    Bucket access rides the task-role credentials (no embedded secrets)."""

    def __init__(self, bucket: str, prefix: str = DEFAULT_S3_PREFIX, *, client: Any = None):
        if not bucket:
            raise ValueError("S3Registry requires a bucket name")
        self.bucket = bucket
        self.prefix = (prefix or DEFAULT_S3_PREFIX).strip("/")
        self._client = client  # injected in tests; lazily built from boto3 in prod

    def _s3(self) -> Any:
        if self._client is None:  # pragma: no cover — live AWS path; tests always inject
            import boto3  # noqa: PLC0415 — lazy so importing ml.registry needs no boto3/AWS

            self._client = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        return self._client

    def _key(self, key: str) -> str:
        return f"{self.prefix}/{key}"

    def _get(self, key: str) -> bytes | None:
        s3 = self._s3()
        try:
            resp = s3.get_object(Bucket=self.bucket, Key=self._key(key))
        except s3.exceptions.NoSuchKey:
            return None
        return resp["Body"].read()

    def _put(self, key: str, data: bytes) -> None:
        self._s3().put_object(Bucket=self.bucket, Key=self._key(key), Body=data)

    def _list_tenant_ids(self) -> list[str]:
        """Tenant ids = the first key segment under the prefix (delimiter listing, paginated)."""
        s3 = self._s3()
        root = f"{self.prefix}/"
        tenants: list[str] = []
        token: str | None = None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": root, "Delimiter": "/"}
            if token:
                kwargs["ContinuationToken"] = token
            resp = s3.list_objects_v2(**kwargs)
            for cp in resp.get("CommonPrefixes", []) or []:
                seg = cp.get("Prefix", "")[len(root):].strip("/")
                if seg:
                    tenants.append(seg)
            if not resp.get("IsTruncated"):
                return tenants
            token = resp.get("NextContinuationToken")


# Anything implementing the shared protocol; both families work in evaluate_and_gate / retrain.
Registry = InMemoryRegistry | PersistentRegistry


def registry_from_env() -> PersistentRegistry | None:
    """Build the persistent Cortex registry from env (shared/config.py names) — the factory the
    worker (`build_clients_from_env` -> clients["cortex"]) and the API conversation factory wire
    into the tool context next cycle (api/* is owned by the prod-deps agent this cycle).

    CORTEX_S3_BUCKET (prod) wins over CORTEX_LOCAL_DIR (dev/tests); all-unset returns None so
    `ToolContext.cortex` stays None and run_model degrades cleanly. Import-safe: constructing the
    S3Registry defers boto3 to the first blob access.
    """
    bucket = os.environ.get(ENV_CORTEX_S3_BUCKET, "")
    if bucket:
        return S3Registry(bucket, os.environ.get(ENV_CORTEX_S3_PREFIX, "") or DEFAULT_S3_PREFIX)
    local_dir = os.environ.get(ENV_CORTEX_LOCAL_DIR, "")
    if local_dir:
        return LocalFsRegistry(local_dir)
    return None


def evaluate_and_gate(registry: Registry, tenant_id: str, challenger: ModelRecord,
                      metric: str = "auc", margin: float = PROMOTION_MARGIN) -> bool:
    """Promote `challenger` to champion iff it beats the incumbent by `margin`. Returns True if promoted.

    With no incumbent, the first model that beats random (auc > 0.5) becomes champion. Promotion
    goes through `registry.set_champion`, so on a persistent registry the flip is durable (visible
    to `run_model` in other processes), while InMemoryRegistry keeps its offline semantics.
    """
    champ = registry.champion(tenant_id)
    if champ is None:
        if challenger.metrics.get(metric, 0.0) > 0.5:
            registry.set_champion(tenant_id, challenger.version)
            challenger.is_champion = True
            return True
        return False
    if challenger.metrics.get(metric, 0.0) >= champ.metrics.get(metric, 0.0) + margin:
        registry.set_champion(tenant_id, challenger.version)
        champ.is_champion = False
        challenger.is_champion = True
        return True
    return False
