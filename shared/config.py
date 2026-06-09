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


def _int_env(name: str, default: int) -> int:
    """Parse an int env var, falling back to the default on junk (import must never crash)."""
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


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
    # --- Signup verification (Phase 10, signup/tokens.py) ---
    # Secrets Manager REFERENCE name for the HMAC signing secret (never the value itself); the
    # caller resolves it and INJECTS the bytes into EmailTokenService / OtpService.
    signup_token_secret: str = os.environ.get(
        "SIGNUP_TOKEN_SECRET", "uplift/signup-token-secret"
    )
    # Plain tunables (safe defaults; no secrets).
    signup_email_token_ttl_s: int = _int_env("SIGNUP_EMAIL_TOKEN_TTL_S", 900)   # 15 min
    signup_otp_ttl_s: int = _int_env("SIGNUP_OTP_TTL_S", 600)                   # 10 min
    signup_otp_max_attempts: int = _int_env("SIGNUP_OTP_MAX_ATTEMPTS", 5)
    signup_otp_max_sends: int = _int_env("SIGNUP_OTP_MAX_SENDS", 5)
    signup_otp_send_window_s: int = _int_env("SIGNUP_OTP_SEND_WINDOW_S", 3600)  # 1 h


def load() -> Config:
    """Return the active configuration."""
    return Config()
