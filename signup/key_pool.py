"""Pre-minted Anthropic workspace-key pool (issue #152 — the ratified Console-pool flow).

The live Admin API VERIFY (issue #152) proved key CREATION is Console-only (POST
/v1/organizations/api_keys 405s), so provisioning can no longer mint a per-tenant workspace key
programmatically. Instead an OWNER pre-mints keys in the Anthropic Console and loads them into
the `workspace_keys` pool table (db/schema.sql — RLS-EXEMPT pre-tenant infrastructure) via
``scripts/ops/load_workspace_keys.py``; provisioning step 2 CONSUMES one per tenant.

Guarantees:
  * atomic claim — ONE statement (``UPDATE .. WHERE id = (SELECT .. FOR UPDATE SKIP LOCKED
    LIMIT 1) RETURNING``): of N concurrent provisions, each gets a DIFFERENT row, none blocks;
  * idempotent per tenant — the claim is preceded by a lookup of an already-consumed row for
    this tenant (and backed by the partial-unique ``consumed_by_tenant`` index), so a retried
    provisioning step re-reads the SAME key instead of burning a second one;
  * empty pool fails LOUDLY — :class:`WorkspaceKeyPoolEmpty` (message prefixed ``pool_empty``)
    parks the signup in ``provisioning_failed`` for an idempotent retry once keys are loaded;
  * alarms-friendly low-water logging — after every consume, an availability count at or below
    the ``WORKSPACE_KEY_POOL_LOW_WATERMARK`` (default 3) emits a structured
    ``workspace_key_pool_low`` warning (CloudWatch metric-filter ready).

Connection discipline mirrors signup/store_pg.py (the shared ``_PgBase``): non-owner crm_app
role, pooled per-op connections, ONE transaction per operation, and — deliberately — NO
``SET LOCAL app.current_tenant``: the pool is pre-tenant infrastructure (see the schema comment).
Import-safe: psycopg2 is imported lazily on construction.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from .store_pg import _PgBase

log = logging.getLogger(__name__)

DEFAULT_LOW_WATERMARK = 3
_ENV_LOW_WATERMARK = "WORKSPACE_KEY_POOL_LOW_WATERMARK"  # name declared in shared/config.py


class WorkspaceKeyPoolEmpty(RuntimeError):
    """No pre-minted workspace key is available — the signup must park as pool_empty."""

    reason = "pool_empty"


@dataclass(frozen=True)
class PoolKey:
    key: str                      # the pre-minted workspace-scoped API key (secret material)
    workspace_id: str | None      # the Console workspace the key is scoped to (may be unknown)
    key_hint: str | None          # non-secret ops hint (e.g. last 4 chars)


def _low_watermark() -> int:
    try:
        value = int(os.environ.get(_ENV_LOW_WATERMARK, DEFAULT_LOW_WATERMARK))
    except (TypeError, ValueError):
        return DEFAULT_LOW_WATERMARK
    return value if value >= 0 else DEFAULT_LOW_WATERMARK


class PgWorkspaceKeyPool(_PgBase):
    """The pool consumer + loader over the ``workspace_keys`` table (as crm_app)."""

    def consume(self, tenant_id: str) -> PoolKey:
        """Claim one pre-minted key for this tenant (idempotent: a retry returns the SAME key).

        Raises :class:`WorkspaceKeyPoolEmpty` when no row is available — the caller
        (Provisioner._step_workspace) lets that park the signup with reason ``pool_empty``.
        """
        with self._tx() as cur:
            # Idempotent retry: this tenant already holds a key -> hand the same one back.
            cur.execute(
                "SELECT key_material, workspace_id, key_hint FROM workspace_keys "
                "WHERE consumed_by_tenant = %s",
                (str(tenant_id),),
            )
            row = cur.fetchone()
            if row is None:
                # THE CLAIM — one atomic statement; SKIP LOCKED keeps concurrent provisions on
                # different rows without blocking each other.
                cur.execute(
                    "UPDATE workspace_keys SET status = 'consumed', "
                    "consumed_by_tenant = %s, consumed_at = now() "
                    "WHERE id = (SELECT id FROM workspace_keys "
                    "            WHERE status = 'available' AND consumed_by_tenant IS NULL "
                    "            ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) "
                    "RETURNING key_material, workspace_id, key_hint",
                    (str(tenant_id),),
                )
                row = cur.fetchone()
            if row is None:
                raise WorkspaceKeyPoolEmpty(
                    "pool_empty: no pre-minted workspace keys available — pre-mint keys in the "
                    "Anthropic Console and load them via scripts/ops/load_workspace_keys.py, "
                    "then retry the parked signup"
                )
            cur.execute(
                "SELECT count(*) AS n FROM workspace_keys WHERE status = 'available'"
            )
            count_row = cur.fetchone() or {}
            available = int(count_row.get("n") or 0)
        watermark = _low_watermark()
        if available <= watermark:
            # Alarms-friendly structured line (stable token + key=value pairs for a CloudWatch
            # metric filter): the owner must pre-mint more keys in Console before this hits 0.
            log.warning(
                "workspace_key_pool_low available=%d low_watermark=%d tenant=%s",
                available, watermark, tenant_id,
            )
        row = dict(row)
        return PoolKey(
            key=row["key_material"],
            workspace_id=row.get("workspace_id"),
            key_hint=row.get("key_hint"),
        )

    def available_count(self) -> int:
        with self._tx() as cur:
            cur.execute("SELECT count(*) AS n FROM workspace_keys WHERE status = 'available'")
            row = cur.fetchone() or {}
        return int(dict(row).get("n") or 0)

    def load(self, entries: list[dict]) -> int:
        """Insert pre-minted keys (the ops loader path). Idempotent via key_hash ON CONFLICT.

        Each entry: ``{"key": <material>, "key_hash": <sha256 hex>, "key_hint": ...,
        "workspace_id": ...}``. Returns how many rows were actually inserted (re-loading the
        same file is a no-op, never a duplicate pool entry).
        """
        inserted = 0
        with self._tx() as cur:
            for entry in entries:
                cur.execute(
                    "INSERT INTO workspace_keys (key_material, key_hash, key_hint, workspace_id) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (key_hash) DO NOTHING",
                    (entry["key"], entry["key_hash"], entry.get("key_hint"),
                     entry.get("workspace_id")),
                )
                inserted += int(cur.rowcount or 0)
        return inserted
