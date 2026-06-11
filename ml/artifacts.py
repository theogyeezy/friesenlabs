"""Signed model artifacts — HMAC-authenticated joblib blobs (Cortex artifact safety).

THE FINDING THIS CLOSES (RCE-via-bucket-write): the registry used to round-trip estimators as
raw header-prefixed pickles. Anyone who could write to the Cortex S3 bucket (or the local dir)
could plant a malicious pickle that the worker would happily `pickle.loads` — arbitrary code
execution in the task. Deserializing ANY untrusted pickle stream is game over; a magic header
is provenance theater, not authentication.

THE FIX: every artifact is now a joblib payload wrapped in an HMAC-SHA256 envelope keyed by the
`CORTEX_SIGNING_KEY` env secret (name owned by `shared/config.py`; value wired by LANE NICK from
Secrets Manager — never in the repo). The loader verifies the MAC over header+payload BEFORE any
byte reaches the deserializer and REFUSES:
  * legacy v1 (unsigned) blobs — migrate via `scripts/ml/resign_artifacts.py`,
  * blobs whose signature is missing, malformed, or wrong (corrupt or tampered),
  * everything, when no signing key is configured (fail closed, never unsigned fallback).

Blob layout (format v2)::

    uplift-cortex-model/v2\n
    <hex hmac-sha256 over "uplift-cortex-model/v2\\n" + payload>\n
    <joblib payload bytes>

The MAC covers the version header so a v2 blob cannot be replayed under a future version, and
`hmac.compare_digest` keeps the comparison constant-time.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import os
import pickle

from shared.config import ENV_CORTEX_SIGNING_KEY

# Bumped on any breaking change to the blob shape; readers reject other versions loudly
# (a silent mis-parse of a model artifact is far worse than a failed load).
SIGNED_FORMAT_VERSION = 2
LEGACY_FORMAT_VERSION = 1          # pre-signing pickle blobs — read ONLY by the migration shim
_BLOB_HEADER_PREFIX = b"uplift-cortex-model/v"


class RegistryFormatError(ValueError):
    """A stored blob/manifest is corrupt, truncated, unsigned, or from an unknown format."""


class SigningKeyError(RegistryFormatError):
    """No CORTEX_SIGNING_KEY configured — artifacts can be neither written nor verified.

    Subclasses RegistryFormatError so every existing degrade path (e.g. run_model's clean
    "champion unreadable" result) treats a missing key as an unreadable artifact, never a crash —
    and never an unsigned fallback.
    """


def signing_key() -> bytes:
    """The HMAC key from the CORTEX_SIGNING_KEY env var. Raises SigningKeyError when unset."""
    raw = os.environ.get(ENV_CORTEX_SIGNING_KEY, "")
    if not raw:
        raise SigningKeyError(
            f"{ENV_CORTEX_SIGNING_KEY} is not set — refusing to read/write model artifacts "
            "(signed-artifact contract; see ml/artifacts.py)"
        )
    return raw.encode("utf-8")


def _header(version: int) -> bytes:
    return _BLOB_HEADER_PREFIX + str(version).encode("ascii") + b"\n"


def _mac(key: bytes, version: int, payload: bytes) -> bytes:
    return hmac.new(key, _header(version) + payload, hashlib.sha256).hexdigest().encode("ascii")


def _joblib():
    import joblib  # noqa: PLC0415 — lazy so importing ml.artifacts never needs the dep at import

    return joblib


def serialize_model(model: object, *, key: bytes | None = None) -> bytes:
    """Model -> signed v2 blob: header line, hex-HMAC line, joblib payload."""
    key = key if key is not None else signing_key()
    buf = io.BytesIO()
    _joblib().dump(model, buf)
    payload = buf.getvalue()
    return _header(SIGNED_FORMAT_VERSION) + _mac(key, SIGNED_FORMAT_VERSION, payload) + b"\n" + payload


def _parse_version(blob: bytes) -> tuple[int, bytes]:
    """Split off + validate the header line -> (format_version, rest-after-header)."""
    head, sep, rest = blob.partition(b"\n")
    if not sep or not head.startswith(_BLOB_HEADER_PREFIX):
        raise RegistryFormatError("model blob is missing the uplift-cortex-model header")
    try:
        version = int(head[len(_BLOB_HEADER_PREFIX):])
    except ValueError as exc:
        raise RegistryFormatError("model blob has a malformed format-version header") from exc
    return version, rest


def deserialize_model(blob: bytes, *, key: bytes | None = None) -> object:
    """Signed v2 blob -> model. Verification happens BEFORE any deserialization.

    Refuses (RegistryFormatError) legacy/unknown versions, missing/invalid/mismatched
    signatures, and torn payloads. Raises SigningKeyError when no key is configured.
    SAFE BY CONSTRUCTION: joblib.load only ever runs on a payload whose HMAC (under the
    out-of-band CORTEX_SIGNING_KEY secret) just verified — i.e. bytes the retrain job itself
    signed, never attacker-supplied content.
    """
    version, rest = _parse_version(blob)
    if version == LEGACY_FORMAT_VERSION:
        raise RegistryFormatError(
            "unsigned legacy v1 model blob — refused (signed-artifact contract); "
            "re-sign it with scripts/ml/resign_artifacts.py"
        )
    verify_signature(blob, key=key)
    _sig, _sep, payload = rest.partition(b"\n")
    try:
        return _joblib().load(io.BytesIO(payload))
    except Exception as exc:  # signature passed but payload won't load — surface loudly
        raise RegistryFormatError("model blob payload is corrupt (deserialize failed)") from exc


def deserialize_legacy_v1(blob: bytes) -> object:
    """MIGRATION-ONLY: unpickle a legacy (pre-signing) v1 blob.

    This is the explicit, operator-invoked trust decision behind
    `scripts/ml/resign_artifacts.py` — it runs once, against blobs the retrain job itself wrote
    before signing existed, ideally after an audit of the bucket. NOTHING on the serving path
    may ever call this.
    """
    version, payload = _parse_version(blob)
    if version != LEGACY_FORMAT_VERSION:
        raise RegistryFormatError(
            f"not a legacy v1 blob (format v{version}) — deserialize_legacy_v1 reads only v1"
        )
    try:
        return pickle.loads(payload)
    except Exception as exc:
        raise RegistryFormatError("legacy model blob payload is corrupt (unpickle failed)") from exc


def verify_signature(blob: bytes, *, key: bytes | None = None) -> None:
    """Check header + signature of a v2 blob WITHOUT deserializing anything.

    Raises the same RegistryFormatError family as `deserialize_model` on any problem. The
    migration shim uses this to classify blobs — classification must never execute a payload.
    """
    version, rest = _parse_version(blob)
    if version == LEGACY_FORMAT_VERSION:
        raise RegistryFormatError("unsigned legacy v1 model blob")
    if version != SIGNED_FORMAT_VERSION:
        raise RegistryFormatError(
            f"unsupported model blob format version {version} "
            f"(this build reads v{SIGNED_FORMAT_VERSION})"
        )
    key = key if key is not None else signing_key()
    sig, sep, payload = rest.partition(b"\n")
    if not sep or len(sig) != 64:
        raise RegistryFormatError("model blob is missing its signature line")
    if not hmac.compare_digest(sig, _mac(key, version, payload)):
        raise RegistryFormatError(
            "model blob failed signature verification — corrupt or tampered; refusing to load"
        )


def is_signed_current(blob: bytes, *, key: bytes | None = None) -> bool:
    """True iff `blob` is a well-formed, signature-valid CURRENT-version artifact.

    Signature check only — never deserializes (no pickle/joblib execution risk here)."""
    try:
        verify_signature(blob, key=key)
    except RegistryFormatError:
        return False
    return True
