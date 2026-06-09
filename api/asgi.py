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
from api.control.greenlight import Greenlight, PgApprovalStore
from api.views import PgSavedViewStore, SavedViews


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


def _dsn_from_env() -> str | None:
    """Build the crm_app DSN from discrete env (DB_USER/DB_PASS from Secrets Manager + DB_HOST/DB_NAME),
    or use UPLIFT_DB_URL directly. None when no DB is configured (boots for /healthz only)."""
    if os.environ.get("UPLIFT_DB_URL"):
        return os.environ["UPLIFT_DB_URL"]
    user, pw, host = os.environ.get("DB_USER"), os.environ.get("DB_PASS"), os.environ.get("DB_HOST")
    if user and pw and host:
        name = os.environ.get("DB_NAME", "uplift")
        port = os.environ.get("DB_PORT", "5432")
        return f"postgresql://{user}:{pw}@{host}:{port}/{name}"
    return None


def build_app():
    # Aurora-backed stores when a crm_app DSN is configured; else in-memory (boots for /healthz).
    dsn = _dsn_from_env()
    if dsn:
        greenlight = Greenlight(store=PgApprovalStore(dsn))
        saved_views = SavedViews(store=PgSavedViewStore(dsn))
    else:
        greenlight = Greenlight()
        saved_views = SavedViews()
    from api.prod_deps import build_signup_deps
    deps = ApiDeps(
        verifier=_verifier(),
        greenlight=greenlight,
        saved_views=saved_views,
        # /chat returns 503 (not 500) until a real conversation backend (agent runtime) is wired.
        conversation_factory=lambda tenant_id: None,  # TODO(prod): real conv.session.Conversation
        autonomy_config=AutonomyConfig(),
        executor=lambda action: {"status": "noop"},   # TODO(prod): real tool executor via the worker
        signup=build_signup_deps(),                    # mounts /signup, /verify-*, /checkout, /webhooks/stripe
    )
    return create_app(deps)


app = build_app()
