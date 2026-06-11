#!/usr/bin/env python3
"""Load Console-pre-minted Anthropic workspace keys into the workspace_keys pool (issue #152).

The Admin API cannot mint workspace keys (POST /v1/organizations/api_keys 405s — Console-only),
so provisioning consumes from a pre-minted pool (signup/key_pool.py). This loader is the OWNER
act that fills it: pre-mint keys in the Anthropic Console (one per future tenant, ideally each
in its own pre-created workspace), then feed them here.

SECRET STORAGE (the security model — read before changing): key MATERIAL never lands in Postgres.
For each input key this loader:
  1. writes the key material to AWS Secrets Manager under a deterministic reference derived from
     the key's sha256 (``uplift/pool/anthropic_key/<hash16>``) — idempotent (re-running the same
     file overwrites the same secret with the same value), and
  2. inserts only the NON-SECRET reference + sha256 + last-4 hint + workspace id into
     ``workspace_keys`` (ON CONFLICT (key_hash) DO NOTHING).
So a dump of the pool table leaks references and fingerprints, never usable keys. Provisioning
resolves the reference back to material from Secrets Manager at consume time.

Input — one entry per line on STDIN (never argv: keys must not land in shell history), either:
    sk-ant-api03-...                          (key only)
    wrkspc_ABC123<TAB>sk-ant-api03-...        (workspace_id<TAB>key — preferred: records which
                                               Console workspace the key is scoped to)
Blank lines and '#' comments are skipped.

AWS WRITE GATE: the loader writes to Secrets Manager only when ``LOAD_KEYS_REAL_SECRETS`` is
exactly ``true``/``1`` (a deliberate operator act, mirroring the INTEGRATIONS_REAL_SECRETS posture)
and ``--dry-run`` is not set. Without it the loader REFUSES to write pool rows (it will not store a
reference to a secret it never wrote) — so a misconfigured run can never seed a dangling pool.

DB target: the crm_app DSN from the standard env (UPLIFT_DB_URL or DB_* — shared/config.py
dsn_from_env). Run by the operator wherever those + AWS creds are available (e.g. the migrate
one-off task). IAM (request to Lane Nick in the PR body): secretsmanager:CreateSecret +
PutSecretValue on ``uplift/pool/anthropic_key/*`` for the loader principal.

Usage:
    LOAD_KEYS_REAL_SECRETS=1 python scripts/ops/load_workspace_keys.py < keys.txt
    python scripts/ops/load_workspace_keys.py --dry-run < keys.txt   # parse only; no AWS, no DB
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root for shared/signup

# Deterministic Secrets Manager reference for a pooled key (derived from its hash so re-loading
# the same key is idempotent end-to-end). Pool secrets live under their own prefix so the IAM
# grant is scoped (uplift/pool/anthropic_key/*) and never overlaps the per-tenant secrets.
_POOL_SECRET_PREFIX = "uplift/pool/anthropic_key/"
_ENV_REAL_SECRETS = "LOAD_KEYS_REAL_SECRETS"


def _pool_secret_ref(key_hash: str) -> str:
    return f"{_POOL_SECRET_PREFIX}{key_hash[:16]}"


def parse_lines(lines) -> list[dict]:
    """Parse loader input into pool entries (pure — unit-tested without a DB or AWS).

    Each entry carries the raw ``key`` material (used ONLY to write Secrets Manager, never stored
    in the DB), its ``key_hash``, the derived ``secret_ref``, a non-secret ``key_hint``, and the
    optional ``workspace_id``.
    """
    entries: list[dict] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        workspace_id = None
        key = line
        if "\t" in line:
            workspace_id, key = (part.strip() for part in line.split("\t", 1))
            workspace_id = workspace_id or None
        if not key or any(c.isspace() for c in key):
            raise ValueError(f"malformed key line (hint: ...{line[-4:]})")
        key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        entries.append({
            "key": key,
            "key_hash": key_hash,
            "secret_ref": _pool_secret_ref(key_hash),
            "key_hint": key[-4:],
            "workspace_id": workspace_id,
        })
    return entries


def _real_secrets_mode() -> bool:
    """True only when LOAD_KEYS_REAL_SECRETS is exactly 'true'/'1' (deliberate operator act)."""
    return os.environ.get(_ENV_REAL_SECRETS, "") in ("true", "1")


def _build_secret_writer():
    """The real Secrets Manager writer (lazy boto3). Isolated so tests inject a fake."""
    from api.integrations_routes import Boto3SecretWriter  # noqa: PLC0415 — after sys.path bootstrap
    return Boto3SecretWriter()


def write_secrets(entries: list[dict], writer) -> None:
    """Write each key's material to Secrets Manager at its derived reference (idempotent).

    The DB row stores only ``secret_ref`` — material flows ONLY to Secrets Manager here.
    """
    for entry in entries:
        writer.put_secret(entry["secret_ref"], entry["key"])


def _db_entries(entries: list[dict]) -> list[dict]:
    """Strip the raw key material before anything touches the DB (defense in depth)."""
    return [{"secret_ref": e["secret_ref"], "key_hash": e["key_hash"],
             "key_hint": e["key_hint"], "workspace_id": e["workspace_id"]}
            for e in entries]


def main(argv: list[str] | None = None, *, writer=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    dry_run = "--dry-run" in argv

    entries = parse_lines(sys.stdin)
    if not entries:
        print("no keys on stdin — nothing to load", file=sys.stderr)
        return 1
    print(f"parsed {len(entries)} key(s): "
          + ", ".join(f"...{e['key_hint']} ({e['workspace_id'] or 'no workspace'})"
                      for e in entries))
    if dry_run:
        print("--dry-run: no secrets written, no rows written")
        return 0

    if writer is None and not _real_secrets_mode():
        # Refuse to store a reference to a secret we never wrote: a pool row whose Secrets Manager
        # secret is missing would park every signup that claims it. Fail closed.
        print(f"refusing to load: set {_ENV_REAL_SECRETS}=1 to write key material to Secrets "
              "Manager (the DB only ever stores the reference). Use --dry-run to parse only.",
              file=sys.stderr)
        return 1

    from shared.config import dsn_from_env  # noqa: PLC0415 — after sys.path bootstrap
    from signup.key_pool import PgWorkspaceKeyPool  # noqa: PLC0415

    dsn = dsn_from_env()
    if not dsn:
        print("no DB configured (set UPLIFT_DB_URL or DB_USER/DB_PASS/DB_HOST) — refusing",
              file=sys.stderr)
        return 1

    # 1. Material -> Secrets Manager FIRST (so a later DB insert never references a missing secret).
    writer = writer if writer is not None else _build_secret_writer()
    write_secrets(entries, writer)
    print(f"wrote {len(entries)} key(s) to Secrets Manager under {_POOL_SECRET_PREFIX}*")

    # 2. Only the non-secret reference -> Postgres.
    pool = PgWorkspaceKeyPool(dsn)
    inserted = pool.load(_db_entries(entries))
    print(f"inserted {inserted} new pool row(s) "
          f"({len(entries) - inserted} duplicate(s) skipped); "
          f"available now: {pool.available_count()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
