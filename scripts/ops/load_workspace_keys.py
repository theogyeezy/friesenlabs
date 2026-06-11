#!/usr/bin/env python3
"""Load Console-pre-minted Anthropic workspace keys into the workspace_keys pool (issue #152).

The Admin API cannot mint workspace keys (POST /v1/organizations/api_keys 405s — Console-only),
so provisioning consumes from a pre-minted pool (signup/key_pool.py). This loader is the OWNER
act that fills it: pre-mint keys in the Anthropic Console (one per future tenant, ideally each
in its own pre-created workspace), then feed them here.

Input — one entry per line on STDIN (never argv: keys must not land in shell history), either:
    sk-ant-api03-...                          (key only)
    wrkspc_ABC123<TAB>sk-ant-api03-...        (workspace_id<TAB>key — preferred: records which
                                               Console workspace the key is scoped to)
Blank lines and '#' comments are skipped.

Idempotent: each key is hashed (sha256) and inserted ON CONFLICT (key_hash) DO NOTHING —
re-running the same input file never duplicates pool entries. Only a non-secret hint (last 4
chars) is ever printed or stored alongside the hash.

DB target: the crm_app DSN from the standard env (UPLIFT_DB_URL or DB_* — shared/config.py
dsn_from_env). Run by the operator wherever those are available (e.g. the migrate one-off task);
this script makes NO AWS/Anthropic calls — it only writes pool rows.

Usage:
    python scripts/ops/load_workspace_keys.py < keys.txt
    python scripts/ops/load_workspace_keys.py --dry-run < keys.txt
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root for shared/signup


def parse_lines(lines) -> list[dict]:
    """Parse loader input into pool entries (pure — unit-tested without a DB)."""
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
        entries.append({
            "key": key,
            "key_hash": hashlib.sha256(key.encode("utf-8")).hexdigest(),
            "key_hint": key[-4:],
            "workspace_id": workspace_id,
        })
    return entries


def main(argv: list[str] | None = None) -> int:
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
        print("--dry-run: no rows written")
        return 0

    from shared.config import dsn_from_env  # noqa: PLC0415 — after sys.path bootstrap
    from signup.key_pool import PgWorkspaceKeyPool  # noqa: PLC0415

    dsn = dsn_from_env()
    if not dsn:
        print("no DB configured (set UPLIFT_DB_URL or DB_USER/DB_PASS/DB_HOST) — refusing",
              file=sys.stderr)
        return 1
    pool = PgWorkspaceKeyPool(dsn)
    inserted = pool.load(entries)
    print(f"inserted {inserted} new pool row(s) "
          f"({len(entries) - inserted} duplicate(s) skipped); "
          f"available now: {pool.available_count()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
