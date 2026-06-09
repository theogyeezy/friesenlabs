"""Production ASGI entrypoint for the control-plane API (the container runs this).

Builds the FastAPI app from environment config. Boots with the in-memory stores by default so the
container starts and `/healthz` passes the ALB health check; **production swaps in the Aurora-backed
stores** (the DB-backed ApprovalStore / SavedViewStore / TraceStore are the remaining integration —
see BUILD_STATUS "needs Nick / connective tissue"). The real Cognito verifier is wired from env.
"""
from __future__ import annotations

import os

from api.app import ApiDeps, create_app
from api.auth import CognitoJwtVerifier, JwtVerifier
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.views import SavedViews


def _verifier() -> JwtVerifier:
    pool = os.environ.get("COGNITO_USER_POOL_ID")
    client = os.environ.get("COGNITO_CLIENT_ID")
    region = os.environ.get("AWS_REGION", "us-east-1")
    if pool and client:
        # VERIFY: real JWKS verification against the pool (BLOCKED until creds).
        return CognitoJwtVerifier(pool_id=pool, client_id=client, region=region)
    # No pool configured (local/dev): a verifier that rejects everything but lets /healthz serve.

    class _RejectAll:
        def verify(self, token):  # noqa: D401
            raise RuntimeError("auth not configured")

    return _RejectAll()


def build_app():
    deps = ApiDeps(
        verifier=_verifier(),
        greenlight=Greenlight(),          # TODO(prod): Aurora-backed ApprovalStore over `approvals`
        saved_views=SavedViews(),         # TODO(prod): Aurora-backed SavedViewStore over `saved_views`
        conversation_factory=lambda tenant_id: None,  # TODO(prod): real conv.session.Conversation
        autonomy_config=AutonomyConfig(),
        executor=lambda action: {"status": "noop"},   # TODO(prod): real tool executor via the worker
    )
    return create_app(deps)


app = build_app()
