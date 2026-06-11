"""run_model tool (Build Guide Phase 8, Step 46): agents call the tenant's champion model.

Read-only (AUTO): e.g. Scout scoring a lead's propensity to convert. Loads the tenant's CHAMPION model
from the injected registry and returns a score in [0,1]. Tenant-scoped — a tenant only ever sees its
own model.

Prediction logging: after a successful score, the prediction is durably logged via the
`prediction_log` injected through `ctx.extra['prediction_log']` (an ml.predictions protocol
object). Logging is best-effort — a failure never fails the score. When no registry/model
is present (score=None paths) or no prediction_log is injected, the log step is a no-op.
"""
from __future__ import annotations

import logging

from ml import features
from ml.registry import RegistryFormatError

from .base import Policy, Tool, ToolContext

logger = logging.getLogger(__name__)


class RunModel(Tool):
    name = "run_model"
    description = "Score a record (e.g. lead conversion propensity) with the tenant's champion model."
    input_schema = {
        "type": "object",
        "properties": {"record": {"type": "object"}},
        "required": ["record"],
    }
    policy = Policy.AUTO

    def _execute(self, ctx: ToolContext, *, record: dict) -> dict:
        registry = ctx.cortex  # the injected per-tenant model registry (ml.registry)
        if registry is None:
            return {"score": None, "reason": "no model registry configured"}
        try:
            champ = registry.champion(ctx.tenant_id)
        except RegistryFormatError as exc:
            # A corrupt/foreign-format champion artifact degrades to a clean tool result instead
            # of crashing the worker's tool call; the retrain job re-promotes a fresh artifact.
            return {"score": None, "reason": f"champion model unreadable: {exc}"}
        if champ is None:
            return {"score": None, "reason": "no champion model for tenant"}
        x = features.featurize([record])
        score = champ.model.predict_proba(x)[0]

        # Best-effort prediction log: feeds the drift flywheel (live AUC in ml/health.py).
        # A logging failure MUST NOT propagate — the score is the primary contract.
        prediction_log = ctx.extra.get("prediction_log")
        if prediction_log is not None:
            try:
                prediction_log.log(
                    ctx.tenant_id,
                    deal_id=record.get("deal_id") or record.get("id"),
                    model_version=champ.version,
                    score=score,
                    features=record,
                )
            except Exception:  # noqa: BLE001
                logger.warning("run_model: prediction log failed (best-effort, score unaffected)",
                               exc_info=True)

        return {"score": score, "model_version": champ.version, "estimator": champ.estimator_name}
