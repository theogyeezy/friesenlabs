#!/usr/bin/env python3
"""Migration shim: re-sign legacy (pre-signing) Cortex model artifacts as signed v2 blobs.

The serving path now REFUSES unsigned artifacts (ml/artifacts.py — the RCE-via-bucket-write
fix), so registries written before signing need this one-shot, operator-run migration. Running
it is the explicit trust decision to round-trip the legacy pickles the retrain job itself wrote
— audit the bucket/dir first. Strictness lives in ml/migrate_artifacts.py:

  * valid signed v2 blobs  -> untouched (idempotent re-runs),
  * v2 with a BAD signature -> reported, NEVER re-signed (that would launder tampering),
  * unreadable blobs        -> reported, skipped,
  * legacy v1               -> re-signed in place; the manifest format_version is bumped.

Usage (registry + key from env, same names as the serving path):
    CORTEX_LOCAL_DIR=... CORTEX_SIGNING_KEY=...  python scripts/ml/resign_artifacts.py [--dry-run]
    CORTEX_S3_BUCKET=... CORTEX_SIGNING_KEY=...  python scripts/ml/resign_artifacts.py \\
        [--tenant <id> ...] [--dry-run]

Exit codes: 0 = clean (everything signed/migrated), 1 = problem blobs were reported,
2 = configuration error. S3 writes ride the caller's task-role/operator credentials.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from ml.migrate_artifacts import BAD_SIGNATURE, MISSING, UNREADABLE, resign_registry  # noqa: E402
from ml.registry import SigningKeyError, registry_from_env  # noqa: E402

_PROBLEMS = (BAD_SIGNATURE, UNREADABLE, MISSING)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-sign legacy Cortex model artifacts.")
    parser.add_argument("--tenant", action="append", default=None,
                        help="tenant id to migrate (repeatable; default: all under the root)")
    parser.add_argument("--dry-run", action="store_true",
                        help="classify + report only; write nothing")
    args = parser.parse_args(argv)

    registry = registry_from_env()
    if registry is None:
        print("[resign] FAIL — no persistent registry configured "
              "(set CORTEX_S3_BUCKET or CORTEX_LOCAL_DIR)")
        return 2

    try:
        reports = resign_registry(registry, args.tenant, dry_run=args.dry_run)
    except SigningKeyError as exc:
        print(f"[resign] FAIL — {exc}")
        return 2

    problems = 0
    for report in reports:
        print(json.dumps(report, sort_keys=True))
        problems += sum(1 for status in report["versions"].values() if status in _PROBLEMS)
        if report["manifest"] == UNREADABLE:
            problems += 1
    if problems:
        print(f"[resign] DONE with {problems} problem blob(s) reported above — "
              "bad-signature/unreadable artifacts are never re-signed; delete or retrain.")
        return 1
    print(f"[resign] DONE — {len(reports)} tenant(s) clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
