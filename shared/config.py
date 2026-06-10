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

# --- AI-plane env-var NAMES (the single source for names; values come from the task definition /
# --- Secrets Manager — see infra/REQUESTS.md REQ-001). Safe default everywhere is "unset" = stub.
ENV_ANTHROPIC_API_KEY = "ANTHROPIC_API_KEY"   # org key — API task ONLY, never the worker
ENV_UPLIFT_ENV_ID = "UPLIFT_ENV_ID"           # MA self-hosted environment id (secret: uplift/env-id)
ENV_UPLIFT_ENV_KEY = "UPLIFT_ENV_KEY"         # environment-scoped worker key — worker task ONLY
ENV_CLOUDWATCH_METRICS = "CLOUDWATCH_METRICS" # "1" enables the worker heartbeat metric
ENV_CUBE_ENDPOINT = "CUBE_ENDPOINT"           # governed-metrics API base URL (unset = no cube client)
# crm_app DSN: either the single URL, or the discrete parts (user/pass from Secrets Manager).
ENV_UPLIFT_DB_URL = "UPLIFT_DB_URL"
ENV_DB_USER, ENV_DB_PASS = "DB_USER", "DB_PASS"
ENV_DB_HOST, ENV_DB_NAME, ENV_DB_PORT = "DB_HOST", "DB_NAME", "DB_PORT"
# Cortex persistent model registry (ml/registry.py registry_from_env). All-unset = no persistent
# registry -> ToolContext.cortex stays None and run_model degrades cleanly ("no model registry").
ENV_CORTEX_S3_BUCKET = "CORTEX_S3_BUCKET"  # S3 bucket holding serialized tenant models (prod)
ENV_CORTEX_S3_PREFIX = "CORTEX_S3_PREFIX"  # key prefix in that bucket (empty -> cortex/registry)
ENV_CORTEX_LOCAL_DIR = "CORTEX_LOCAL_DIR"  # local-filesystem registry root — dev/tests fallback
# Cube REST auth (agents/tools/cube_client.py). The RESOLVED HS256 signing-secret VALUE — the same
# secret the Cube service itself reads as CUBEJS_API_SECRET (infra/modules/cube, SM
# uplift/cube-api-secret); LANE NICK wires it into the task-def `secrets` block under THIS name.
# A NEW deliberate name on purpose: per-request Cube-JWT minting must never key off env the live
# API task already injects (deploy invariance) — unset = no JWT can be minted, the client degrades
# to its 'unconfigured' result and nothing touches the network. Never the SM reference.
ENV_CUBEJS_API_SECRET_VALUE = "CUBEJS_API_SECRET_VALUE"

# --- Ingestion-plane env-var NAMES (ingest/run_sync.py — the EventBridge→Fargate one-off entry,
# --- infra/REQUESTS.md REQ-004). These are NEW, deliberate names: the live API task already
# --- injects DB_*/COGNITO_*/AWS_REGION for OTHER features, so the scheduler's real adapters key
# --- ONLY off the master switch below (deploy invariance — same rationale as SIGNUP_REAL_DEPS).
# --- Safe default everywhere is "unset" = offline stubs; run_sync stays runnable with no AWS/DB.
ENV_INGEST_REAL_STORES = "INGEST_REAL_STORES"  # exactly "true"/"1" -> real Pg stores + Titan embed
ENV_INGEST_TENANTS = "INGEST_TENANTS"          # comma-separated tenant ids consumed by --all
ENV_INGEST_RAW_BUCKET = "INGEST_RAW_BUCKET"    # S3 raw-lake bucket (unset = in-memory raw sink)
# Titan V2 BATCH embeddings (ingest/embed.py batch_embed) — both must be set or batch_embed
# falls back to synchronous per-text embed (the safe, incremental-sync behavior).
ENV_INGEST_BATCH_S3_BUCKET = "INGEST_BATCH_S3_BUCKET"  # JSONL input/output bucket for batch jobs
ENV_BEDROCK_BATCH_ROLE_ARN = "BEDROCK_BATCH_ROLE_ARN"  # roleArn for create_model_invocation_job


def dsn_from_env() -> str | None:
    """Build the crm_app DSN from `UPLIFT_DB_URL`, or the discrete DB_* parts (DB_USER/DB_PASS from
    Secrets Manager + DB_HOST/DB_NAME). Returns None when no DB is configured — callers must degrade
    to their stub behavior (the deployed API without creds boots for /healthz only)."""
    if os.environ.get(ENV_UPLIFT_DB_URL):
        return os.environ[ENV_UPLIFT_DB_URL]
    user = os.environ.get(ENV_DB_USER)
    pw = os.environ.get(ENV_DB_PASS)
    host = os.environ.get(ENV_DB_HOST)
    if user and pw and host:
        name = os.environ.get(ENV_DB_NAME, "uplift")
        port = os.environ.get(ENV_DB_PORT, "5432")
        return f"postgresql://{user}:{pw}@{host}:{port}/{name}"
    return None


def _int_env(name: str, default: int) -> int:
    """Parse an int env var, falling back to the default on junk (import must never crash)."""
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _switch_env(name: str) -> bool:
    """A DELIBERATE boolean switch: True only when the env var is exactly 'true' or '1'.

    No strip/lower/yes/on leniency — a master switch guarding real side-effecting adapters must
    fail CLOSED on anything that isn't the documented value (typos, 'True', stray whitespace)."""
    return os.environ.get(name, "") in ("true", "1")


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
    # The Managed Agents self-hosted environment id (REQ-001 — the worker + API task read the
    # resolved value from the UPLIFT_ENV_ID env var; this is the Secrets Manager reference name).
    env_id_secret: str = os.environ.get("UPLIFT_ENV_ID_SECRET", "uplift/env-id")
    # Non-owner role used by the app so Postgres RLS actually applies (Build Guide red box).
    db_app_role: str = os.environ.get("DB_APP_ROLE", "uplift_app")
    # --- Signup verification (Phase 10, signup/tokens.py) ---
    # Secrets Manager REFERENCE name for the HMAC signing secret (never the value itself); the
    # caller resolves it and INJECTS the bytes into EmailTokenService / OtpService.
    signup_token_secret: str = os.environ.get(
        "SIGNUP_TOKEN_SECRET", "uplift/signup-token-secret"
    )
    # The RESOLVED signing-secret VALUE (LANE NICK wires the secret above into the API task-def
    # `secrets` block under this env name). Empty = verification stays OFF (api/prod_deps.py
    # keeps email_token_ok/sms_code_ok hardcoded False, the safe pre-wire behavior).
    signup_token_secret_value: str = os.environ.get("SIGNUP_TOKEN_SECRET_VALUE", "")
    # Plain tunables (safe defaults; no secrets).
    signup_email_token_ttl_s: int = _int_env("SIGNUP_EMAIL_TOKEN_TTL_S", 900)   # 15 min
    signup_otp_ttl_s: int = _int_env("SIGNUP_OTP_TTL_S", 600)                   # 10 min
    signup_otp_max_attempts: int = _int_env("SIGNUP_OTP_MAX_ATTEMPTS", 5)
    signup_otp_max_sends: int = _int_env("SIGNUP_OTP_MAX_SENDS", 5)
    signup_otp_send_window_s: int = _int_env("SIGNUP_OTP_SEND_WINDOW_S", 3600)  # 1 h

    # --- Signup / payment / identity plane (Build Guide Phase 10) -------------------------
    # MASTER SWITCH for the real signup/provisioning adapters (api/prod_deps.build_signup_deps).
    # DEPLOY INVARIANCE (adversarial finding, HIGH): the live API task ALREADY injects
    # COGNITO_USER_POOL_ID (for JWKS) and DB_HOST/DB_NAME/DB_USER/DB_PASS (for the request-path
    # stores) for OTHER features — so the per-adapter guards below, alone, would flip real
    # Cognito admin calls + live-Aurora signup state on a mere image deploy (with REQ-002 grants
    # still OPEN). Every real adapter therefore ALSO requires this deliberate flag — exactly
    # "true" or "1", wired as plain env on the API task (infra/REQUESTS.md REQ-003). Unset (or
    # anything else) = all stubs, byte-identical boot, regardless of what other env is present.
    signup_real_deps: bool = _switch_env("SIGNUP_REAL_DEPS")
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
    # The RESOLVED admin-key VALUE (task-def `secrets` valueFrom the reference above; API task
    # ONLY). Empty = unconfigured — api/prod_deps.py keeps the provisioning _Noop stub.
    anthropic_admin_key: str = os.environ.get("ANTHROPIC_ADMIN_KEY", "")

    # --- Cortex persistent model registry (ml/registry.py) ---
    # Safe '' defaults: unconfigured = no persistent registry, nothing touches AWS. The bucket is
    # plain config (no secret material); access rides the task role, never embedded credentials.
    cortex_s3_bucket: str = os.environ.get("CORTEX_S3_BUCKET", "")
    cortex_s3_prefix: str = os.environ.get("CORTEX_S3_PREFIX", "")  # '' -> ml.registry default
    cortex_local_dir: str = os.environ.get("CORTEX_LOCAL_DIR", "")  # dev/tests fallback root

    # --- Provisioning Step Functions trigger (api/prod_deps.SfnProvisioningTrigger; REQ-005) ---
    # The uplift-provisioning state machine ARN. A NEW, deliberate env name (never keyed off env
    # the live API task already injects): empty (default) keeps the in-process provision path —
    # and even when set, the trigger is selected only UNDER the SIGNUP_REAL_DEPS master switch.
    provisioning_sfn_arn: str = os.environ.get("PROVISIONING_SFN_ARN", "")


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


# --- Integrations-plane env-var NAMES (api/integrations_routes.py — the api half of TODO INT/P2
# --- "Build the real integrations/connect UI + backend"; infra/REQUESTS.md REQ-006). A NEW,
# --- deliberate name on purpose: the live API task ALREADY injects DB_*/COGNITO_*/
# --- ANTHROPIC_API_KEY for OTHER features, so the per-tenant secret WRITE path keys ONLY off
# --- this master switch (deploy invariance — same rationale as SIGNUP_REAL_DEPS /
# --- INGEST_REAL_STORES). Exactly "true"/"1" (the _switch_env semantics — fail CLOSED on
# --- anything else) selects the real boto3 Secrets Manager writer; unset = no writer, and the
# --- credentials/status endpoints answer an honest 503 "not configured" / status "unknown" —
# --- never a fake success.
ENV_INTEGRATIONS_REAL_SECRETS = "INTEGRATIONS_REAL_SECRETS"
