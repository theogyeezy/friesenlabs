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
