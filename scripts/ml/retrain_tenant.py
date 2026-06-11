#!/usr/bin/env python3
"""Cortex scheduled retrain — the runnable entrypoint the audit found missing.

`ml.retrain.retrain_tenant` existed but was called only by tests; this CLI is the real producer
path: per tenant, load labeled CRM records -> champion/challenger bake-off on a held-out split
-> promote only on AUC improvement (metrics written to the registry) -> sync closed-deal
outcomes into the prediction log -> live drift check. It is the command the EventBridge-
scheduled one-off task (infra/modules/cortex — Lane Nick attaches the target) should run.

Usage:
    # real data plane (env: UPLIFT_DB_URL or DB_* parts, CORTEX_S3_BUCKET or CORTEX_LOCAL_DIR,
    # CORTEX_SIGNING_KEY):
    python scripts/ml/retrain_tenant.py --tenant <tenant-uuid>

    # offline / demo: records from a JSON file (a list of loader-shaped record dicts):
    CORTEX_LOCAL_DIR=/tmp/cortex CORTEX_SIGNING_KEY=dev \\
        python scripts/ml/retrain_tenant.py --tenant t1 --records-json records.json

Honest by design: with no registry configured, no DB and no records file, or thin/single-class
data, it reports WHY and exits non-zero (config errors) or zero (a clean skip) — it never
invents a model. Tenant identity is an explicit operator argument here (the scheduler is a
trusted operator surface, exactly like ingest/run_sync.py); the SET LOCAL pattern scopes every
query to that tenant via RLS.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Runnable as a script from the repo root or via an absolute path (the one-off task does the
# latter); imports resolve against the repo root, same as scripts/seed_demo_tenant.py.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from ml.data_loader import PgTrainingDataLoader, StaticTrainingDataLoader  # noqa: E402
from ml.predictions import PgPredictionLog  # noqa: E402
from ml.registry import SigningKeyError, registry_from_env  # noqa: E402
from ml.retrain import run_scheduled_retrain  # noqa: E402
from shared.config import dsn_from_env  # noqa: E402


def _build_loader(args) -> tuple[object, object | None, str | None]:
    """-> (loader, prediction_log|None, error|None). JSON path wins; else the crm_app DSN."""
    if args.records_json:
        try:
            with open(args.records_json, "r", encoding="utf-8") as f:
                records = json.load(f)
        except (OSError, ValueError) as exc:
            return None, None, f"cannot read --records-json: {exc}"
        if not isinstance(records, list):
            return None, None, "--records-json must contain a JSON list of record objects"
        return StaticTrainingDataLoader(records), None, None
    dsn = dsn_from_env()
    if not dsn:
        return None, None, ("no data source: set UPLIFT_DB_URL / DB_* (crm_app DSN) "
                            "or pass --records-json")
    return PgTrainingDataLoader(dsn), PgPredictionLog(dsn), None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Retrain one tenant's Cortex model.")
    parser.add_argument("--tenant", required=True, help="tenant id (the scheduler's trusted arg)")
    parser.add_argument("--records-json", default=None,
                        help="offline records file instead of the DB loader")
    parser.add_argument("--seed", type=int, default=0, help="deterministic train/split seed")
    args = parser.parse_args(argv)

    registry = registry_from_env()
    if registry is None:
        print("[retrain] FAIL — no model registry configured "
              "(set CORTEX_S3_BUCKET or CORTEX_LOCAL_DIR)")
        return 2

    loader, prediction_log, err = _build_loader(args)
    if err:
        print(f"[retrain] FAIL — {err}")
        return 2

    try:
        result = run_scheduled_retrain(registry, loader, args.tenant,
                                       prediction_log=prediction_log, seed=args.seed)
    except SigningKeyError as exc:
        print(f"[retrain] FAIL — {exc}")
        return 2

    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
