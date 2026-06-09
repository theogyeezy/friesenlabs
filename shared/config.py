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


def load() -> Config:
    """Return the active configuration."""
    return Config()
