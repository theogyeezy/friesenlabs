"""Pre-minted Anthropic workspace-key pool (issue #152 — the ratified Console-pool flow).

The live Admin API VERIFY (issue #152) proved key CREATION is Console-only (POST
/v1/organizations/api_keys 405s), so provisioning can no longer mint a per-tenant workspace key
programmatically. Instead an OWNER pre-mints keys in the Anthropic Console and loads them via
``scripts/ops/load_workspace_keys.py``; provisioning step 2 CONSUMES one per tenant.

SECRET STORAGE (the security fix — read this before touching the SQL): the pool table holds NO key
material. The loader writes each key's material to AWS Secrets Manager and stores only a NON-SECRET
**reference** (the Secrets Manager name) plus a sha256 fingerprint, a last-4 hint, and the Console
workspace id in Postgres. So a SQLi dump or an app-role compromise of the RLS-exempt
``workspace_keys`` table leaks only references and fingerprints — never a usable key. Provisioning
resolves the reference to material via the Secrets Manager seam (``Provisioner.secrets.get``) at
consume time, then writes it to the per-tenant ``uplift/{tenant}/anthropic_key`` secret. The DB is
NOT the secret store.

NOTE ON THE COLUMN NAME: the ``workspace_keys.key_material`` column (db/schema.sql — Lane Nick) now
stores the reference string, not material. A rename to ``secret_ref`` is requested of Lane Nick in
the PR body; until then the column name is an alias for the reference (``_REF_COLUMN`` below). A
prod guard (:meth:`PgWorkspaceKeyPool.assert_no_inline_material`) REFUSES to start if any row still
carries inline key material (a row whose ref looks like ``sk-ant-…``), so a legacy plaintext pool
cannot silently keep leaking.

Guarantees:
  * atomic claim — ONE statement (``UPDATE .. WHERE id = (SELECT .. FOR UPDATE SKIP LOCKED
    LIMIT 1) RETURNING``): of N concurrent provisions, each gets a DIFFERENT row, none blocks;
  * idempotent per tenant — the claim is preceded by a lookup of an already-consumed row for
    this tenant (and backed by the partial-unique ``consumed_by_tenant`` index), so a retried
    provisioning step re-reads the SAME reference instead of burning a second one;
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

# The Postgres column that stores the Secrets Manager REFERENCE (NOT material). Aliased here so a
# Lane-Nick rename to `secret_ref` is a one-line change (PR body requests it). Selected/inserted
# with an explicit AS so the row dicts key off the stable Python name `secret_ref`.
_REF_COLUMN = "key_material"

# Anthropic key prefix — used ONLY to detect a legacy row that still holds inline material so the
# prod startup guard can refuse. A reference never starts with this.
_KEY_MATERIAL_PREFIX = "sk-ant-"


class WorkspaceKeyPoolEmpty(RuntimeError):
    """No pre-minted workspace key is available — the signup must park as pool_empty."""

    reason = "pool_empty"


class InlineKeyMaterialError(RuntimeError):
    """A pool row still carries inline key material — the prod guard refuses to start/serve.

    Means a legacy plaintext pool (pre-fix) was loaded: key material is sitting in Postgres
    instead of Secrets Manager. Re-load the pool via scripts/ops/load_workspace_keys.py (which
    writes material to Secrets Manager and stores only a reference) before serving traffic.
    """


@dataclass(frozen=True)
class PoolKey:
    secret_ref: str               # Secrets Manager reference to the key material (NOT the key)
    workspace_id: str | None      # the Console workspace the key is scoped to (may be unknown)
    key_hint: str | None          # non-secret ops hint (e.g. last 4 chars)


def _low_watermark() -> int:
    try:
        value = int(os.environ.get(_ENV_LOW_WATERMARK, DEFAULT_LOW_WATERMARK))
    except (TypeError, ValueError):
        return DEFAULT_LOW_WATERMARK
    return value if value >= 0 else DEFAULT_LOW_WATERMARK


def _looks_like_inline_material(value: str | None) -> bool:
    """True if a stored reference is actually inline key material (legacy plaintext row)."""
    return bool(value) and str(value).startswith(_KEY_MATERIAL_PREFIX)


class PgWorkspaceKeyPool(_PgBase):
    """The pool consumer + loader over the ``workspace_keys`` table (as crm_app).

    The table stores only a Secrets Manager REFERENCE per key (see the module docstring); no key
    material is ever read from or written to Postgres here.
    """

    def consume(self, tenant_id: str) -> PoolKey:
        """Claim one pre-minted key reference for this tenant (idempotent: a retry returns the
        SAME reference).

        Returns a :class:`PoolKey` carrying the Secrets Manager REFERENCE (``secret_ref``) — the
        caller (Provisioner._step_workspace) resolves it to key material via its secrets seam.
        Raises :class:`WorkspaceKeyPoolEmpty` when no row is available — the caller lets that park
        the signup with reason ``pool_empty``. Raises :class:`InlineKeyMaterialError` if the
        claimed row still holds inline material (a legacy plaintext pool — must be re-loaded).
        """
        with self._tx() as cur:
            # Idempotent retry: this tenant already holds a key -> hand the same reference back.
            cur.execute(
                f"SELECT {_REF_COLUMN} AS secret_ref, workspace_id, key_hint FROM workspace_keys "
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
                    f"RETURNING {_REF_COLUMN} AS secret_ref, workspace_id, key_hint",
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
        secret_ref = row["secret_ref"]
        if _looks_like_inline_material(secret_ref):
            # Defense in depth: even past the startup guard, never hand inline material onward as
            # if it were a reference (it would get re-stored under the per-tenant path verbatim).
            raise InlineKeyMaterialError(
                "workspace_keys row holds inline key material instead of a Secrets Manager "
                "reference — re-load the pool via scripts/ops/load_workspace_keys.py"
            )
        return PoolKey(
            secret_ref=secret_ref,
            workspace_id=row.get("workspace_id"),
            key_hint=row.get("key_hint"),
        )

    def assert_no_inline_material(self) -> None:
        """Prod startup guard: refuse to serve if ANY pool row still carries inline key material.

        A reference never starts with ``sk-ant-``; a row that does is a legacy plaintext pool that
        must be re-loaded (loader writes material to Secrets Manager, stores only a reference).
        Raises :class:`InlineKeyMaterialError` if such a row exists.
        """
        with self._tx() as cur:
            cur.execute(
                f"SELECT count(*) AS n FROM workspace_keys WHERE {_REF_COLUMN} LIKE %s",
                (_KEY_MATERIAL_PREFIX + "%",),
            )
            row = cur.fetchone() or {}
        offending = int(dict(row).get("n") or 0)
        if offending:
            raise InlineKeyMaterialError(
                f"{offending} workspace_keys row(s) still hold inline key material in Postgres "
                "(the DB must never be the secret store) — re-load the pool via "
                "scripts/ops/load_workspace_keys.py so material lives in Secrets Manager and "
                "only a reference remains in the table"
            )

    def available_count(self) -> int:
        with self._tx() as cur:
            cur.execute("SELECT count(*) AS n FROM workspace_keys WHERE status = 'available'")
            row = cur.fetchone() or {}
        return int(dict(row).get("n") or 0)

    def load(self, entries: list[dict]) -> int:
        """Insert pre-minted key REFERENCES (the ops loader path). Idempotent via key_hash
        ON CONFLICT.

        Each entry: ``{"secret_ref": <SM reference>, "key_hash": <sha256 hex>, "key_hint": ...,
        "workspace_id": ...}`` — NO key material (the loader has already written material to
        Secrets Manager). Returns how many rows were actually inserted (re-loading the same file
        is a no-op, never a duplicate pool entry).
        """
        inserted = 0
        with self._tx() as cur:
            for entry in entries:
                ref = entry["secret_ref"]
                if _looks_like_inline_material(ref):
                    # Hard refusal: never let the table become a secret store again.
                    raise InlineKeyMaterialError(
                        "refusing to insert inline key material into workspace_keys — pass a "
                        "Secrets Manager reference (the loader writes material to SM first)"
                    )
                cur.execute(
                    f"INSERT INTO workspace_keys ({_REF_COLUMN}, key_hash, key_hint, workspace_id) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (key_hash) DO NOTHING",
                    (ref, entry["key_hash"], entry.get("key_hint"),
                     entry.get("workspace_id")),
                )
                inserted += int(cur.rowcount or 0)
        return inserted
