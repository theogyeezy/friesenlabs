"""Authed per-tenant Cortex health — the api half of PR #194's `ml/health.py` seam.

One endpoint, READ-ONLY and bound to the VERIFIED JWT claims (THE TRUST RULE — tenant never
from a header or the request body):

  GET /cortex/health    the tenant's model health: champion (version + estimator + registered
                        metrics) + version count + the live-AUC drift verdict when resolved
                        (score, outcome) evidence exists. The payload is built ENTIRELY by
                        `ml.health.cortex_health` — this route only binds the tenant from the
                        verified claim and returns the dict, exactly the wiring #194 specified.

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

from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI

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
