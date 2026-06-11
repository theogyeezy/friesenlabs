"""Cortex retrain FAN-OUT — retrain every tenant that has a model in the registry.

The EventBridge retrain rule (infra/modules/scheduled_jobs) fires ONCE on its schedule, but each
tenant needs its OWN retrain. This is the per-tenant fan-out driver the schedule's target runs:

    python scripts/ml/retrain_all.py            # every tenant the registry knows
    python scripts/ml/retrain_all.py --tenant t1 --tenant t2   # an explicit subset

It iterates `registry.tenant_ids()` (or the explicit --tenant list), runs the SAME
`run_scheduled_retrain` per tenant that scripts/ml/retrain_tenant.py runs for one, and reports a
per-tenant summary. One tenant's failure NEVER stops the rest (each is contained); the process
exit code is 0 only when every attempted tenant retrained, 1 if any failed (so the schedule's
alarm can page), 2 on a usage/config error (no registry / no data source) before any tenant ran.

Tenant ids come from the registry (or the operator's --tenant args) — NEVER from an event body
(there is no untrusted input here; this is an operator-scheduled batch). Each per-tenant retrain
is RLS-scoped inside the loader/registry exactly as the single-tenant path is.

IMPORT SAFETY: importing this module touches no AWS/boto3/DB; real clients are built only inside
main() (registry_from_env / the DSN loader), mirroring retrain_tenant.py.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Runnable from the repo root or an absolute path (the scheduled task uses the latter).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from ml.data_loader import PgTrainingDataLoader  # noqa: E402
from ml.drift_alert import from_env as drift_notifier_from_env  # noqa: E402
from ml.predictions import PgPredictionLog  # noqa: E402
from ml.registry import SigningKeyError, registry_from_env  # noqa: E402
from ml.retrain import run_scheduled_retrain  # noqa: E402
from shared.config import dsn_from_env  # noqa: E402


def resolve_tenants(registry, explicit: list[str] | None) -> list[str]:
    """Tenant ids to retrain: explicit --tenant flags win; else every tenant in the registry.

    De-duplicated, order-stable. An empty result is not an error — the batch logs "nothing to do"
    and exits clean (a not-yet-populated registry must not page anyone)."""
    if explicit:
        return list(dict.fromkeys(t.strip() for t in explicit if t.strip()))
    return list(dict.fromkeys(registry.tenant_ids()))


def retrain_one(registry, loader, tenant_id: str, *, prediction_log, seed: int,
                drift_notifier=None) -> dict:
    """Retrain ONE tenant; contain any failure into a structured result (never raises).

    When a drift notifier is wired AND this tenant's live drift verdict is positive, publish a
    best-effort SNS alert. A notify failure is recorded on the result but never fails the tenant
    (the retrain itself already succeeded — the alert is downstream)."""
    try:
        result = run_scheduled_retrain(
            registry, loader, tenant_id, prediction_log=prediction_log, seed=seed,
        )
        out = {"tenant": tenant_id, "ok": True, "result": result}
        if drift_notifier is not None:
            try:
                if drift_notifier.notify(tenant_id, result.get("drift") or {}):
                    out["drift_alerted"] = True
            except Exception as exc:  # noqa: BLE001 — alerting is downstream of a successful retrain
                out["drift_alert_error"] = f"{type(exc).__name__}: {exc}"
        return out
    except SigningKeyError as exc:
        # A missing/!invalid CORTEX_SIGNING_KEY is a deployment misconfig — it fails identically
        # for every tenant, so surface it but keep going (the summary makes it obvious).
        return {"tenant": tenant_id, "ok": False, "error": f"signing: {exc}"}
    except Exception as exc:  # noqa: BLE001 — one bad tenant must not stop the fan-out
        return {"tenant": tenant_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python scripts/ml/retrain_all.py",
        description="Retrain every tenant's Cortex model (the EventBridge schedule's fan-out).",
    )
    p.add_argument("--tenant", action="append", metavar="TENANT_ID",
                   help="retrain only this tenant (repeatable); default = every tenant in the registry")
    p.add_argument("--seed", type=int, default=0, help="deterministic train/split seed")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    registry = registry_from_env()
    if registry is None:
        print("[retrain-all] FAIL — no model registry configured "
              "(set CORTEX_S3_BUCKET or CORTEX_LOCAL_DIR)")
        return 2

    dsn = dsn_from_env()
    if not dsn:
        print("[retrain-all] FAIL — no data source: set UPLIFT_DB_URL / DB_* (crm_app DSN)")
        return 2
    loader = PgTrainingDataLoader(dsn)
    prediction_log = PgPredictionLog(dsn)
    # Drift alerting: inert (None) unless CORTEX_DRIFT_TOPIC_ARN is set — then a positive live-drift
    # verdict publishes to the Cortex drift SNS topic so an operator is actually paged.
    drift_notifier = drift_notifier_from_env(os.environ)

    tenants = resolve_tenants(registry, args.tenant)
    if not tenants:
        print("[retrain-all] nothing to do — the registry knows no tenants yet")
        return 0

    print(f"[retrain-all] retraining {len(tenants)} tenant(s)"
          + ("" if drift_notifier else " (drift alerting OFF — no CORTEX_DRIFT_TOPIC_ARN)"))
    results = [
        retrain_one(registry, loader, t, prediction_log=prediction_log, seed=args.seed,
                    drift_notifier=drift_notifier)
        for t in tenants
    ]
    failures = [r for r in results if not r["ok"]]
    alerted = [r["tenant"] for r in results if r.get("drift_alerted")]
    for r in results:
        print(json.dumps(r, sort_keys=True, default=str))

    if alerted:
        print(f"[retrain-all] drift ALERT published for {len(alerted)} tenant(s): {', '.join(map(str, alerted))}")
    if failures:
        print(f"[retrain-all] {len(failures)}/{len(tenants)} tenant(s) FAILED")
        return 1
    print(f"[retrain-all] complete — {len(tenants)} tenant(s) retrained")
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised via main() in tests
    raise SystemExit(main())
