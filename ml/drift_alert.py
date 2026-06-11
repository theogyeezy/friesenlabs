"""Drift alerting — page someone when a tenant's champion has degraded (Build Guide Phase 8, Step 47).

The retrain fan-out (scripts/ml/retrain_all.py) already computes LIVE drift per tenant (recent live
AUC vs the champion's registered AUC, from real logged predictions + resolved outcomes). Until now
that verdict only surfaced in the /cortex/health UI — nothing ALERTED on it. This publishes a drift
verdict to the Cortex drift SNS topic so it actually reaches an operator (email/Slack/PagerDuty
subscription, wired in infra).

Inert by construction (the unconfigured-stub posture used throughout this codebase):
  * No `CORTEX_DRIFT_TOPIC_ARN` set → `from_env` returns None → the fan-out skips alerting entirely.
  * boto3 is imported LAZILY on first publish, so importing this module (and running the offline
    tests) needs no AWS SDK and touches no network.
  * Publishing is BEST-EFFORT: a failure is reported by the caller, never aborts the retrain batch.
The topic ARN comes from the operator's env (the scheduled task injects it) — never from any tenant
input.
"""
from __future__ import annotations

import json
from typing import Any, Mapping


class DriftNotifier:
    """Publishes per-tenant drift verdicts to an SNS topic."""

    def __init__(self, topic_arn: str, *, region: str | None = None, sns: Any = None):
        self._topic_arn = topic_arn
        self._region = region
        self._sns = sns  # injected fake in tests; lazily built via boto3 otherwise

    def _client(self) -> Any:
        if self._sns is None:
            import boto3  # noqa: PLC0415 — lazy: importing this module needs no boto3
            self._sns = boto3.client("sns", region_name=self._region) if self._region else boto3.client("sns")
        return self._sns

    def notify(self, tenant_id: str, drift: Mapping[str, Any]) -> bool:
        """Publish ONLY when the verdict is an actual drift (drift is True). Returns True if a
        message was sent. A no-drift / insufficient-evidence verdict is a no-op (returns False) — we
        never page on "everything's fine"."""
        if not drift or not drift.get("drift"):
            return False
        registered = drift.get("registered_auc")
        recent = drift.get("recent_auc")
        subject = f"Cortex drift: tenant {tenant_id}"[:100]  # SNS subject hard limit is 100 chars
        message = {
            "tenant_id": str(tenant_id),
            "registered_auc": registered,
            "recent_auc": recent,
            "n_outcomes": drift.get("n_outcomes"),
            "reason": drift.get("reason"),
        }
        self._client().publish(
            TopicArn=self._topic_arn,
            Subject=subject,
            Message=json.dumps(message, default=str, sort_keys=True),
        )
        return True


def from_env(env: Mapping[str, str]) -> DriftNotifier | None:
    """Build a notifier ONLY when a drift topic is configured. No `CORTEX_DRIFT_TOPIC_ARN` → None
    (alerting stays inert; the fan-out runs exactly as before)."""
    arn = (env.get("CORTEX_DRIFT_TOPIC_ARN") or "").strip()
    if not arn:
        return None
    return DriftNotifier(arn, region=(env.get("AWS_REGION") or "").strip() or None)
