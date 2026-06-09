"""Shared configuration for Uplift.

Reads from environment / Secrets Manager refs. NEVER hardcode secrets here.
Phases pull what they need from this single source so the moving parts stay coherent.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# The Managed Agents beta header required on every Anthropic call (Build Guide §Step 4).
MA_BETA_HEADER = "managed-agents-2026-04-01"

# Titan Text Embeddings V2 dimensionality (Build Guide §Step 10 — documents.embedding vector(1024)).
EMBEDDING_DIM = 1024


@dataclass(frozen=True)
class Config:
    aws_region: str = os.environ.get("AWS_REGION", "us-east-1")
    project: str = os.environ.get("PROJECT", "uplift")
    # Reference names, not values — resolved from Secrets Manager at runtime.
    anthropic_api_key_secret: str = os.environ.get(
        "ANTHROPIC_API_KEY_SECRET", "uplift/anthropic-api-key"
    )
    aurora_master_secret: str = os.environ.get(
        "AURORA_MASTER_SECRET", "uplift/aurora-master"
    )
    # Non-owner role used by the app so Postgres RLS actually applies (Build Guide red box).
    db_app_role: str = os.environ.get("DB_APP_ROLE", "uplift_app")

    # --- Signup / payment / identity plane (Build Guide Phase 10) -------------------------
    # Key MATERIAL arrives via the task environment (LANE NICK wires Secrets Manager
    # `friesenlabs/platform/shared/stripe-*` into the task-def `secrets` block); adapters read it
    # injected/from here and NEVER fetch Secrets Manager themselves. Safe default: empty string =
    # unconfigured, and the adapters stub cleanly (clear *NotConfiguredError, no network).
    stripe_api_key: str = os.environ.get("STRIPE_API_KEY", "")
    stripe_webhook_secret: str = os.environ.get("STRIPE_WEBHOOK_SECRET", "")  # api/asgi.py reads the same name
    # Hosted-Checkout redirect URLs (UX only — provisioning trusts the signed webhook, never the
    # browser redirect).
    stripe_success_url: str = os.environ.get("STRIPE_SUCCESS_URL", "")
    stripe_cancel_url: str = os.environ.get("STRIPE_CANCEL_URL", "")
    # Cognito admin ops (signup/cognito_admin.py) — same name api/asgi.py already uses for JWKS.
    cognito_user_pool_id: str = os.environ.get("COGNITO_USER_POOL_ID", "")


# plan id -> env var that carries its Stripe Price ID (values land via task secrets, never here).
STRIPE_PRICE_ID_ENV = {
    "starter": "STRIPE_PRICE_ID_STARTER",
    "team": "STRIPE_PRICE_ID_TEAM",
    "scale": "STRIPE_PRICE_ID_SCALE",
}


def stripe_price_ids() -> dict[str, str]:
    """plan -> Stripe Price ID map, read from env at call time (unset plans are omitted)."""
    return {plan: os.environ[var] for plan, var in STRIPE_PRICE_ID_ENV.items() if os.environ.get(var)}


def load() -> Config:
    """Return the active configuration."""
    return Config()
