#!/usr/bin/env python3
"""Reap orphaned Managed-Agents rosters — delete the agents of SUPERSEDED rosters (2026-06-14).

The self-upgrading roster (agents/provisioning.py) mints a fresh coordinator + specialists whenever
the code's specs change and repoints the tenant at them, leaving the old roster as dead weight in the
shared MA environment. Each supersession is recorded in the RLS-EXEMPT `retired_rosters` ledger; this
ops sweep deletes the recorded agents from MA after a grace window and marks the ledger row reaped.
The reap logic lives in agents/retirement.py (unit-tested); this is the thin env/CLI wiring.

SAFE BY CONSTRUCTION (see agents/retirement.py): only ever targets coordinators the system recorded
as superseded; every provision mints unique specialists so a retired roster's agents can never be
pinned by a current coordinator; a grace window protects a just-retired roster.

TWO gates before anything is deleted (a destructive, live-cloud op):
  * --apply on the command line, AND
  * REAP_REAL_DELETES=1 (or =true) in the environment (mirrors the repo's *_REAL_* write-gate posture).
Without BOTH it is a DRY RUN — it prints exactly what it would delete and changes nothing.

Env:
    ANTHROPIC_API_KEY   org key that owns the agents (Secrets Manager: uplift/anthropic-api-key)
    UPLIFT_DB_URL       crm_app DSN to the retired_rosters ledger (or DB_* via shared.config)
    REAP_REAL_DELETES   '1'/'true' to arm real deletes (with --apply)

Usage (run where those + the VPC are reachable, e.g. the migrate one-off task):
    python scripts/ops/reap_orphan_agents.py                       # dry run (default)
    REAP_REAL_DELETES=1 python scripts/ops/reap_orphan_agents.py --apply
    python scripts/ops/reap_orphan_agents.py --grace-seconds 0     # ignore the grace window (dry run)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root for agents/shared

from agents.retirement import DEFAULT_GRACE_SECONDS, PgRetirementSource, reap_orphans  # noqa: E402
from agents.runtime import get_runtime  # noqa: E402

_ENV_REAL_DELETES = "REAP_REAL_DELETES"


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes")


def _dsn() -> str:
    dsn = os.environ.get("UPLIFT_DB_URL")
    if dsn:
        return dsn
    try:
        from shared.config import dsn_from_env  # noqa: PLC0415
        composed = dsn_from_env()
        if composed:
            return composed
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit("no DB connection configured: set UPLIFT_DB_URL (or DB_* for shared.config).")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Reap orphaned (superseded) Managed-Agents rosters.")
    ap.add_argument("--apply", action="store_true",
                    help=f"actually delete (also needs {_ENV_REAL_DELETES}=1); otherwise dry run")
    ap.add_argument("--grace-seconds", type=int, default=DEFAULT_GRACE_SECONDS,
                    help="leave a roster retired less than this long untouched "
                         f"(default {DEFAULT_GRACE_SECONDS})")
    args = ap.parse_args(argv)

    armed = args.apply and _truthy(os.environ.get(_ENV_REAL_DELETES))
    if args.apply and not armed:
        print(f"--apply given but {_ENV_REAL_DELETES} is not set to 1/true → DRY RUN (no deletes).",
              file=sys.stderr)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY (the org key) is required.")
    runtime = get_runtime({"runtime": "managed", "api_key": api_key})

    source = PgRetirementSource(_dsn())
    try:
        report = reap_orphans(runtime, source, now=datetime.now(timezone.utc),
                              grace_seconds=args.grace_seconds, apply=armed)
    finally:
        source.close()

    print(json.dumps(report, indent=2, default=str))
    reaped = sum(1 for e in report["rosters"] if e["reaped"])
    failed = sum(len(e["failed"]) for e in report["rosters"])
    print(f"\n[reaper] mode={'APPLY' if armed else 'DRY-RUN'} considered={report['considered']} "
          f"due={report['due']} reaped_rosters={reaped} failed_deletes={failed}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
