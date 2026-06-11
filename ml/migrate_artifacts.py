"""One-shot migration: re-sign legacy (pre-signing) Cortex artifacts as signed v2 blobs.

The serving path REFUSES unsigned/legacy artifacts outright (ml/artifacts.py — the
RCE-via-bucket-write fix). Existing registries written before signing therefore need a single,
explicit, operator-run migration: read each legacy v1 blob (the only place
`deserialize_legacy_v1` may ever be called — an explicit trust decision about blobs the retrain
job itself wrote, ideally after auditing the bucket), re-serialize it signed, and bump the
manifest's format_version. CLI wrapper: scripts/ml/resign_artifacts.py.

Strict by design:
  * a blob that is already a VALID signed v2 artifact is left untouched (idempotent re-runs),
  * a v2 blob with a BAD signature is never "fixed" — re-signing it would launder tampering;
    it is reported and skipped (delete it or retrain the tenant),
  * unreadable/corrupt blobs are reported and skipped.

Classification never deserializes (signature checks only); only a confirmed-legacy v1 payload is
unpickled, and only to round-trip it into the signed format.
"""
from __future__ import annotations

import json
from typing import Any

from . import artifacts
from .registry import FORMAT_VERSION, PersistentRegistry, RegistryFormatError

# Per-blob outcome statuses (also the report vocabulary of the CLI).
RESIGNED = "resigned"                  # legacy v1 -> signed v2, written back
ALREADY_SIGNED = "already_signed"      # valid v2 — untouched
BAD_SIGNATURE = "bad_signature"        # v2 with a wrong/missing MAC — NEVER re-signed
UNREADABLE = "unreadable"              # corrupt header / legacy payload that won't load
MISSING = "missing"                    # manifest names a version with no blob in the store


def _classify_and_convert(blob: bytes, key: bytes) -> tuple[str, bytes | None]:
    """One blob -> (status, replacement-bytes-or-None). Deserializes ONLY confirmed v1."""
    try:
        version, _rest = artifacts._parse_version(blob)  # noqa: SLF001 — package-internal
    except RegistryFormatError:
        return UNREADABLE, None
    if version == artifacts.SIGNED_FORMAT_VERSION:
        # Signature check only — a current-version blob is never deserialized here.
        return (ALREADY_SIGNED if artifacts.is_signed_current(blob, key=key)
                else BAD_SIGNATURE), None
    if version != artifacts.LEGACY_FORMAT_VERSION:
        return UNREADABLE, None
    try:
        model = artifacts.deserialize_legacy_v1(blob)
    except RegistryFormatError:
        return UNREADABLE, None
    return RESIGNED, artifacts.serialize_model(model, key=key)


def resign_tenant(registry: PersistentRegistry, tenant_id: str, *,
                  dry_run: bool = False) -> dict[str, Any]:
    """Re-sign every legacy artifact for one tenant; returns a per-version status report."""
    key = artifacts.signing_key()
    raw_manifest = registry._get(registry._manifest_key(tenant_id))  # noqa: SLF001 — package-internal migration
    if raw_manifest is None:
        return {"tenant_id": tenant_id, "versions": {}, "manifest": "absent"}
    try:
        manifest = json.loads(raw_manifest.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {"tenant_id": tenant_id, "versions": {}, "manifest": UNREADABLE}

    versions: dict[int, str] = {}
    for entry in manifest.get("models", []):
        version = int(entry["version"])
        blob_key = registry._blob_key(tenant_id, version)  # noqa: SLF001
        blob = registry._get(blob_key)  # noqa: SLF001
        if blob is None:
            versions[version] = MISSING
            continue
        status, replacement = _classify_and_convert(blob, key)
        versions[version] = status
        if replacement is not None and not dry_run:
            registry._put(blob_key, replacement)  # noqa: SLF001

    manifest_status = "current"
    if manifest.get("format_version") != FORMAT_VERSION:
        manifest_status = "bumped" if not dry_run else "needs_bump"
        if not dry_run:
            manifest["format_version"] = FORMAT_VERSION
            registry._put(  # noqa: SLF001
                registry._manifest_key(tenant_id),
                json.dumps(manifest, sort_keys=True).encode("utf-8"),
            )
    return {"tenant_id": tenant_id, "versions": versions, "manifest": manifest_status,
            "dry_run": dry_run}


def resign_registry(registry: PersistentRegistry, tenants: list[str] | None = None, *,
                    dry_run: bool = False) -> list[dict[str, Any]]:
    """Re-sign artifacts across tenants (default: every tenant under the registry root)."""
    ids = tenants if tenants is not None else registry.tenant_ids()
    return [resign_tenant(registry, t, dry_run=dry_run) for t in ids]
