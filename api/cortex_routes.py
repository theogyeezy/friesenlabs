"""Authed per-tenant Cortex health + score — the api half of PR #194's `ml/health.py` seam.

READ-ONLY endpoints, bound to the VERIFIED JWT claims (THE TRUST RULE — tenant never from a
header or the request body):

  GET /cortex/health    the tenant's model health: champion (version + estimator + registered
                        metrics) + version count + the live-AUC drift verdict when resolved
                        (score, outcome) evidence exists. The payload is built ENTIRELY by
                        `ml.health.cortex_health` — this route only binds the tenant from the
                        verified claim and returns the dict, exactly the wiring #194 specified.

  GET /cortex/score?deal_id=<uuid>   the champion's score for one deal: `{score, model_version}`
                        for the tenant's champion. `model_version` comes from the registry
                        champion METADATA (a metadata-only `versions()` listing — never a blob
                        deserialize, mirroring /cortex/health). `score` is the champion's most
                        recent logged prediction for that deal, read back from the SAME
                        `prediction_log` dep (the drift flywheel) — a REAL score-time number, never
                        fabricated. When the champion never scored the deal, score is honestly
                        null (status "no_prediction"); 503 when there is no model (no_registry /
                        no_champion). A LIVE per-request score from freshly-derived features
                        (ml/features.py) would need a CRM client on CortexDeps — that wiring is
                        NOT present today and editing api/asgi.py is out of scope here (see PR).

Honest degradation, never a 500:
  * no registry wired (CORTEX_S3_BUCKET / CORTEX_LOCAL_DIR unset)  -> status "no_registry"
  * registry wired, tenant has no champion                          -> status "no_champion"
  * no prediction log (or thin/single-class evidence)               -> drift carries the
    "insufficient live evidence" reason — a number is never fabricated (#194 drift honesty).

METADATA-ONLY by construction: `cortex_health` reads the manifest listing and NEVER
deserializes a model artifact (a health probe must not unpickle blobs or slow with model
size); the signed-artifact gate (ml/artifacts.py) is therefore never in this path.

IMPORT SAFETY: importing this module touches no AWS/boto3/DB — `ml.health` is imported
lazily inside the request handler (the same discipline as api/knowledge_routes.py), so the
image-fileset boot guarantee holds unchanged.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from api.auth import TenantClaims


@dataclass
class CortexDeps:
    """Wiring for /cortex/health. The all-None default is the honest inert stub: the route
    mounts and answers the metadata-only "no_registry" shape (never 404, never invented
    model state). api/asgi.py is the ONLY real wiring — the SAME env-built registry instance
    `run_model` scores with (one registry, one truth) plus a PgPredictionLog over the SAME
    crm_app DSN every live surface rides (per-op SET LOCAL — RLS scopes every row)."""

    registry: Any | None = None        # ml.registry protocol (versions/champion)
    prediction_log: Any | None = None  # ml.predictions protocol (scored_outcomes)


def mount_cortex(app: FastAPI, deps: CortexDeps, current_tenant) -> None:
    """Mount GET /cortex/health on `app`, claims-bound via the SAME current_tenant dependency
    every authed route rides (unauth/invalid tokens 401 before any work)."""

    @app.get("/cortex/health")
    def cortex_health_route(claims: TenantClaims = Depends(current_tenant)):
        from ml.health import cortex_health  # noqa: PLC0415 — lazy (import-safety, see module doc)

        # Tenant from the VERIFIED claim only — a smuggled ?tenant_id= / body field changes
        # nothing (there is no other tenant input to this route).
        return cortex_health(deps.registry, claims.tenant_id, deps.prediction_log)

    @app.get("/cortex/score")
    def cortex_score_route(deal_id: str, claims: TenantClaims = Depends(current_tenant)):
        # Tenant from the VERIFIED claim only (THE TRUST RULE) — deal_id is the only request input.
        tenant_id = claims.tenant_id

        # A malformed deal_id is a client error, not a 500 (and never an existence oracle).
        try:
            deal_id = str(uuid.UUID(str(deal_id)))
        except (ValueError, AttributeError, TypeError):
            return JSONResponse(
                {"status": "bad_request", "detail": "deal_id must be a uuid"}, status_code=400)

        # Honest degradation, same vocabulary as /cortex/health — never a fabricated model state.
        if deps.registry is None:
            return JSONResponse(
                {"deal_id": deal_id, "tenant_id": str(tenant_id), "status": "no_registry",
                 "score": None, "model_version": None}, status_code=503)

        # Champion via the METADATA-ONLY listing (versions()), exactly like cortex_health — no blob
        # deserialize on a hot read path.
        champ = next((r for r in deps.registry.versions(tenant_id) if r.is_champion), None)
        if champ is None:
            return JSONResponse(
                {"deal_id": deal_id, "tenant_id": str(tenant_id), "status": "no_champion",
                 "score": None, "model_version": None}, status_code=503)

        # Score from the SAME prediction_log dep (the drift flywheel) — the champion's most recent
        # logged score for THIS deal. A fresh per-request score from derived features would need a
        # CRM client on CortexDeps (api/asgi.py wiring, out of scope) — see the PR body.
        score: float | None = None
        status = "no_prediction"
        if deps.prediction_log is not None:
            rec = deps.prediction_log.latest_for_deal(tenant_id, deal_id)
            if rec is not None:
                score = rec["score"]
                status = "scored"
        return {"deal_id": deal_id, "tenant_id": str(tenant_id), "status": status,
                "score": score, "model_version": champ.version,
                "champion_metrics": dict(champ.metrics)}
