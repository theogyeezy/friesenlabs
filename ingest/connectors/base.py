"""Connector ABC — the shape every source connector implements.

A connector does three things:
  1. authenticate()      — resolve vaulted creds via an INJECTED secret provider
                           (here a fake; in prod a Secrets Manager-backed impl).
  2. pull(since_cursor)  — read records changed since the last high-water cursor.
  3. land(records)       — write raw JSON to S3 (the raw lake) + normalized rows
                           to Aurora (companies/contacts/deals/activities).

All external dependencies (the source API client, the S3/raw sink, the structured
sink) are injected. Nothing here imports boto3 or psycopg2, so importing a
connector never needs AWS or a database. Tests pass in-memory fakes.

Draft-only: connectors READ from sources and WRITE only to our own data plane.
They never write back to a real CRM.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol, runtime_checkable


# --------------------------------------------------------------------------- #
# Injected interfaces (Protocols so a fake or a real impl both satisfy them).
# --------------------------------------------------------------------------- #
@runtime_checkable
class SecretProvider(Protocol):
    """Resolves a vaulted credential by reference name (NOT a raw secret value)."""

    def get_secret(self, ref: str) -> str: ...


# --------------------------------------------------------------------------- #
# Per-tenant vaulted credentials (TODO INT/P1 "Per-tenant connector credential
# capture + storage in Secrets Manager").
# --------------------------------------------------------------------------- #
# The per-tenant secret naming convention: one Secrets Manager secret per
# (tenant, source) pair. Connectors resolve THIS name and ONLY this name —
# there is no shared-token fallback: a shared credential would let one
# customer's source portal land under another tenant's rows. Missing
# credential = hard error (MissingTenantCredentialError), never a fallback.
PER_TENANT_SECRET_TEMPLATE = "uplift/{tenant_id}/{source}"


def tenant_secret_ref(tenant_id: str, source: str) -> str:
    """The per-tenant Secrets Manager name for a connector credential.

    e.g. tenant_secret_ref("1111-...", "hubspot") -> "uplift/1111-.../hubspot".
    `tenant_id` arrives from the caller that already verified it (THE TRUST
    RULE) — this is a pure name formatter, not an authorization seam.
    """
    return PER_TENANT_SECRET_TEMPLATE.format(tenant_id=tenant_id, source=source)


class SecretNotFoundError(KeyError):
    """The named secret does not exist in the vault (distinct from access errors).

    Connectors use this to distinguish "no per-tenant secret provisioned yet"
    (a hard MissingTenantCredentialError — the tenant's sync must not run)
    from a real provider failure (access/throttle errors propagate untouched).
    """


class MissingTenantCredentialError(RuntimeError):
    """No usable per-tenant credential for a (tenant, source) pair.

    Raised by `Connector.authenticate()` when the per-tenant vaulted secret is
    absent or empty. This is a HARD error by design: ingesting with anything
    other than the tenant's own credential (e.g. a shared token) could land
    another customer's source data under this tenant's rows. The fix is to
    provision the per-tenant secret (PER_TENANT_SECRET_TEMPLATE), never to
    fall back.
    """

    def __init__(self, tenant_id: str, source: str, ref: str, reason: str) -> None:
        self.tenant_id = tenant_id
        self.source = source
        self.ref = ref
        self.reason = reason
        super().__init__(
            f"{source}: no per-tenant credential for tenant {tenant_id} "
            f"(ref {ref!r}: {reason}) — provision the per-tenant secret; "
            "there is no shared-token fallback"
        )


class Boto3SecretProvider:
    """Real Secrets Manager-backed SecretProvider.

    Lazy: boto3 is imported only on the first `get_secret` call when no client
    was injected — importing this module (or constructing the provider) never
    needs AWS. Tests inject a fake `client` with `get_secret_value`.

    A missing secret raises :class:`SecretNotFoundError`; every other client
    error propagates untouched (an access/throttle error must NOT be silently
    treated as "not provisioned").
    """

    def __init__(self, *, region: str | None = None, client: Any = None) -> None:
        self._region = region
        self._client = client  # injected fake in tests; lazily built otherwise

    def _sm(self) -> Any:
        if self._client is None:
            import os  # noqa: PLC0415 — lazy with boto3 below

            import boto3  # noqa: PLC0415 — lazy: import-safe module (no AWS at import)

            region = self._region or os.environ.get("AWS_REGION", "us-east-1")
            self._client = boto3.client("secretsmanager", region_name=region)
        return self._client

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        # botocore ClientError carries the code in .response; injected fakes may
        # just name their exception class after the AWS error code.
        code = ""
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            code = (response.get("Error") or {}).get("Code", "")
        return code == "ResourceNotFoundException" or (
            exc.__class__.__name__ == "ResourceNotFoundException"
        )

    def get_secret(self, ref: str) -> str:
        client = self._sm()
        try:
            resp = client.get_secret_value(SecretId=ref)
        except Exception as exc:  # noqa: BLE001 — narrowed immediately below
            if self._is_not_found(exc):
                raise SecretNotFoundError(ref) from exc
            raise
        value = resp.get("SecretString")
        if value is None:
            blob = resp.get("SecretBinary") or b""
            value = blob.decode("utf-8") if isinstance(blob, (bytes, bytearray)) else str(blob)
        return value


@runtime_checkable
class RawSink(Protocol):
    """The raw lake (S3 in prod). Stores untouched source JSON keyed for replay."""

    def put_raw(self, tenant_id: str, source: str, ref_id: str, record: dict) -> str:
        """Persist one raw record; return the storage key/uri."""
        ...


@runtime_checkable
class StructuredSink(Protocol):
    """The structured store (Aurora in prod). Upserts normalized rows by ref_id."""

    def upsert_rows(self, table: str, rows: list[dict]) -> int:
        """Upsert `rows` into `table` (keyed by tenant_id+ref_id); return count."""
        ...


# --------------------------------------------------------------------------- #
# Normalized record — what a connector emits per source object.
# --------------------------------------------------------------------------- #
@dataclass
class NormalizedRecord:
    """One normalized source object, ready to land + chunk.

    `table` is the Aurora table the structured row targets. `row` matches that
    table's schema (carries tenant_id, ref_id, source). `raw` is the original
    source JSON (landed to S3 untouched). `updated_at` drives the incremental
    cursor. `kind`/`text_blocks` feed the chunker (e.g. notes, speaker turns).
    """

    tenant_id: str
    source: str
    ref_id: str
    table: str
    row: dict
    raw: dict
    updated_at: str = ""
    kind: str = "record"
    text_blocks: list[dict] = field(default_factory=list)


@dataclass
class LandResult:
    raw_keys: list[str] = field(default_factory=list)
    rows_upserted: int = 0


class Connector(ABC):
    """Base connector. Subclasses provide `pull`; `land` is shared boilerplate."""

    #: source tag stamped on every landed row + chunk (e.g. "hubspot").
    source: str = "base"

    def __init__(
        self,
        tenant_id: str,
        *,
        secrets: SecretProvider,
        raw_sink: RawSink,
        structured_sink: StructuredSink,
    ) -> None:
        self.tenant_id = tenant_id
        self._secrets = secrets
        self._raw_sink = raw_sink
        self._structured_sink = structured_sink
        self._authed = False

    # -- 1. auth --------------------------------------------------------- #
    @abstractmethod
    def authenticate(self) -> None:
        """Resolve creds via the injected secret provider. Set self._authed."""

    # -- 2. pull --------------------------------------------------------- #
    @abstractmethod
    def pull(self, since_cursor: str | None) -> Iterable[NormalizedRecord]:
        """Yield NormalizedRecords changed since `since_cursor` (None = full)."""

    # -- 3. land --------------------------------------------------------- #
    def land(self, records: Iterable[NormalizedRecord]) -> LandResult:
        """Land raw JSON to the raw sink + normalized rows to the structured sink.

        Groups rows by target table so the structured sink can batch-upsert. Every
        row already carries tenant_id (enforced below — no cross-tenant mixing).
        """
        result = LandResult()
        by_table: dict[str, list[dict]] = {}
        for rec in records:
            if rec.tenant_id != self.tenant_id:
                raise ValueError(
                    f"cross-tenant record: connector tenant {self.tenant_id} "
                    f"!= record tenant {rec.tenant_id}"
                )
            key = self._raw_sink.put_raw(rec.tenant_id, rec.source, rec.ref_id, rec.raw)
            result.raw_keys.append(key)
            by_table.setdefault(rec.table, []).append(rec.row)
        for table, rows in by_table.items():
            result.rows_upserted += self._structured_sink.upsert_rows(table, rows)
        return result

    def _require_auth(self) -> None:
        if not self._authed:
            raise RuntimeError(f"{type(self).__name__}: call authenticate() before pull()")
