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

    # --- Outbound senders + Anthropic Admin (signup/provisioning) ---
    # DRAFT-GATE (CLAUDE.md hard constraint #2): real outbound delivery (email/SMS) stays OFF
    # unless this is explicitly the string "true". Default is draft-only — senders log and drop.
    allow_real_sends: bool = os.environ.get("ALLOW_REAL_SENDS", "false").strip().lower() == "true"
    # Resend (transactional email). Key is the VALUE injected at runtime (task-def secret),
    # never committed; empty default means "unconfigured — stub cleanly".
    resend_api_key: str = os.environ.get("RESEND_API_KEY", "")
    resend_from_email: str = os.environ.get("RESEND_FROM_EMAIL", "")
    # Base URL the signed email-verification token is appended to (SPA click-through route).
    signup_verify_url_base: str = os.environ.get("SIGNUP_VERIFY_URL_BASE", "")
    # Anthropic ADMIN key (sk-ant-admin..., distinct from the inference key) — Secrets Manager
    # *reference name*, resolved at runtime like the refs above. Never the value.
    anthropic_admin_key_secret: str = os.environ.get(
        "ANTHROPIC_ADMIN_KEY_SECRET", "uplift/anthropic-admin-key"
    )


def load() -> Config:
    """Return the active configuration."""
    return Config()
