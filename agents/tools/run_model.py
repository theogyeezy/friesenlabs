"""run_model tool (Build Guide Phase 8, Step 46): agents call the tenant's champion model.

Read-only (AUTO): e.g. Scout scoring a lead's propensity to convert. Loads the tenant's CHAMPION model
from the injected registry and returns a score in [0,1]. Tenant-scoped — a tenant only ever sees its
own model.
"""
from __future__ import annotations

from ml import features

from .base import Policy, Tool, ToolContext


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
        registry = ctx.cortex  # the injected per-tenant model registry
        if registry is None:
            return {"score": None, "reason": "no model registry configured"}
        champ = registry.champion(ctx.tenant_id)
        if champ is None:
            return {"score": None, "reason": "no champion model for tenant"}
        x = features.featurize([record])
        score = champ.model.predict_proba(x)[0]
        return {"score": score, "model_version": champ.version, "estimator": champ.estimator_name}
