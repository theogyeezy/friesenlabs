# FastAPI control plane on ECS Fargate (Build Guide Phase 9, Step 49).
# Private subnets, SG_API, 2 tasks behind the ALB target group; secrets from Secrets Manager.
# AUTHORED + VALIDATED ONLY.

variable "project" { type = string }
variable "log_retention_days" {
  type    = number
  default = 30 # one knob for every uplift log group (TODO Sec/P3 213)
}
variable "region" { type = string }
variable "cluster_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "security_group_id" { type = string }
variable "target_group_arn" { type = string }
variable "execution_role_arn" { type = string }
variable "task_role_arn" { type = string }
variable "image" {
  type    = string
  default = "" # set to the ECR api image (uplift-api) before apply
}
variable "db_secret_arn" { type = string }
variable "anthropic_api_key_secret_arn" { type = string }
variable "cube_api_secret_arn" {
  type    = string
  default = "" # api_cube_env: inject CUBEJS_API_SECRET_VALUE so cube_client_from_env() goes live
}
variable "env_id_secret_arn" {
  type    = string
  default = ""
}
# REQ-001 part 3, SAFETY-GATED: injecting valueFrom on an EMPTY secret blocks task startup
# (ResourceInitializationError) and would take the live API down. Flip to true in tfvars ONLY
# after `uplift/anthropic-api-key` + `uplift/env-id` hold real values.
variable "api_anthropic_env" {
  type    = bool
  default = false
}

# REQ-003, same safety gate rationale: flip ONLY after stripe-webhook-secret + signup-token-secret
# + anthropic-admin-key hold values (the two platform secrets already do).
variable "api_signup_env" {
  type    = bool
  default = false
}
# The deliberate signup go-live act (REQ-003 step 0) — separate from the wiring flag above.
variable "signup_real_deps" {
  type    = bool
  default = false
}
# The draft-gate (CLAUDE.md #2): when true, ALLOW_REAL_SENDS=true is set on the API task and the
# email/SMS senders actually deliver. Default false = senders log + drop (the safe default).
variable "allow_real_sends" {
  type    = bool
  default = false
}
# Feature flag: phone (SMS OTP) verification. Default true = phone required (no env set). Set false
# to inject SIGNUP_REQUIRE_PHONE=false → email-only signup while SMS account approval is pending.
variable "signup_require_phone" {
  type    = bool
  default = true
}
variable "stripe_key_arn" {
  type    = string
  default = ""
}
variable "resend_key_arn" {
  type    = string
  default = ""
}
variable "stripe_webhook_secret_arn" {
  type    = string
  default = ""
}
variable "signup_token_secret_arn" {
  type    = string
  default = ""
}
variable "anthropic_admin_key_secret_arn" {
  type    = string
  default = ""
}
# REQ-005: the deliberate decouple switch — unset keeps the in-process provision path.
variable "provisioning_sfn_arn" {
  type    = string
  default = ""
}
variable "cube_endpoint" {
  type    = string
  default = "" # http://cube.uplift.local:4000 once Cloud Map is live
}
variable "posthog_key_arn" {
  type    = string
  default = "" # REQ-006: platform posthog-project-key ARN (rides the api_signup_env gate)
}
variable "posthog_host" {
  type    = string
  default = "" # only set to override the in-code default ingestion host
}
# REQ-008: the integrations master switch — unset = honest 503 stubs.
variable "integrations_real" {
  type    = bool
  default = false
}
# Connector OAuth ("Connect with login"): the HMAC state-signing secret ARN. Injected as
# OAUTH_STATE_SECRET; with OAUTH_REDIRECT_BASE below it satisfies OAuthConfig.configured(), so
# /integrations/{name}/oauth/start signs state + returns the authorize_url instead of a 503.
variable "oauth_state_secret_arn" {
  type    = string
  default = ""
}
# REQ-012 step 6: the ingest-plane master switch ON THE API TASK — powers in-process
# API-kicked syncs ("Sync now": async 202 + integration_sync_runs guard) and CSV-import
# landing. REQ-004's old "no INGEST_* on the api task" stance was about IN-REQUEST syncs,
# which no longer exist. Unset = honest 503 stubs (deploy invariance).
variable "ingest_real" {
  type    = bool
  default = false
}
# Playbook schedule-leg honesty flag (GO_LIVE_CHECKLIST §7): stamps PLAYBOOK_DISPATCH_ENABLED=1
# on the api task so GET /studio/playbooks reports scheduling_enabled and the Studio stops
# bannering schedule playbooks as "trigger not enabled yet". Flip it in the SAME apply that
# enables the EventBridge dispatcher (playbook_dispatch_enabled at root) — it is display
# honesty only; the dispatcher itself runs as the scheduled one-off task, not in the API.
variable "playbook_dispatch_enabled" {
  type    = bool
  default = false
}
# Signup-plane PLAIN (non-secret) config: Stripe Hosted-Checkout price ids (price_..., public
# identifiers, not secret-shaped) + redirect URLs, the Resend from-address, the verification-link
# base, and the internal-bypass domain list. All read by shared/config.py at call time; safe ""
# defaults = the entry is omitted and the feature stays unconfigured (deploy invariance). Real
# values land in the machine-local prod.auto.tfvars, never here.
variable "stripe_price_id_starter" {
  type    = string
  default = ""
}
variable "stripe_price_id_team" {
  type    = string
  default = ""
}
variable "stripe_price_id_scale" {
  type    = string
  default = ""
}
# Phase-2 "selection sets the price": per-module recurring Stripe Price ids, keyed by the EXACT env
# var name shared/modules.py reads (STRIPE_PRICE_ID_MODULE_<ID>, e.g. STRIPE_PRICE_ID_MODULE_CORTEX).
# Empty map (default) => no per-module billing wired => the PUT /account/modules sync stays inert
# (the toggle still persists + re-gates the UI). Owner mints the Prices in Stripe, then sets this map.
variable "stripe_module_price_ids" {
  type    = map(string)
  default = {}
}
variable "stripe_success_url" {
  type    = string
  default = ""
}
variable "stripe_cancel_url" {
  type    = string
  default = ""
}
variable "resend_from_email" {
  type    = string
  default = ""
}
variable "signup_verify_url_base" {
  type    = string
  default = ""
}
variable "signup_internal_bypass_domains" {
  type    = string
  default = ""
}
# Cortex persistent model registry (ml/registry.py registry_from_env -> S3Registry): a plain
# bucket name, no secret material — access rides the task role (IAM grant in modules/iam).
variable "cortex_s3_bucket" {
  type    = string
  default = ""
}
# Dev/tests filesystem fallback (CORTEX_S3_BUCKET wins in code); never set in prod.
variable "cortex_local_dir" {
  type    = string
  default = ""
}
variable "cognito_user_pool_id" { type = string }
variable "cognito_client_id" { type = string }
variable "aurora_endpoint" {
  type    = string
  default = ""
}
variable "aurora_master_secret_arn" {
  type    = string
  default = ""
}
variable "desired_count" {
  type    = number
  default = 2
}

# Sec/P0 (REQ-012 item 3): UPLIFT_ENVIRONMENT — arms shared/config.py is_prod(), whose
# refuse-to-boot guard (Stripe internal bypass set in prod => crash, fail closed) is dead code
# until this is present on the task. "prod" on the live task; "" omits the entry (module-level
# escape hatch only — the root default is "prod").
variable "uplift_environment" {
  type        = string
  default     = "prod"
  description = "Value for the UPLIFT_ENVIRONMENT task env var; shared/config.is_prod() gates prod-only safety refusals on it."
}

# Sec (REQ-013): RBAC strict mode. false (default) keeps the empty-groups=admin back-compat so
# pre-RBAC users are unaffected; true sets RBAC_STRICT=1 so a group-less user is no longer auto-admin.
# Flip ONLY after every functional user is in a group (provisioning bootstraps new first-users to admin).
variable "rbac_strict" {
  type        = bool
  default     = false
  description = "When true, sets RBAC_STRICT=1 on the API task — api.auth removes the empty-cognito:groups admin allowance."
}

# Sec (REQ-012 item 8a): the ADOT sidecar image. Default = the exact string the live task defs
# carry today (zero-diff). Pin to a digest (public.ecr.aws/...@sha256:...) via tfvars — a
# mutable :latest sidecar pulled at every task start is a supply-chain hole.
variable "adot_image" {
  type        = string
  default     = "public.ecr.aws/aws-observability/aws-otel-collector:latest"
  description = "ADOT collector sidecar image. SECURITY: pin a digest in tfvars; the :latest default only preserves the current live task def (flip = roll tasks)."
}

# Sec (REQ-012 item 8b): readonlyRootFilesystem on the app container. Default false = current
# live task def (zero-diff). When true the container's root FS is immutable and /tmp becomes a
# Fargate ephemeral volume (the only runtime write path: Python tempfile; PYTHONDONTWRITEBYTECODE
# is set in the Dockerfile so no .pyc writes). Flip = new task-def revision = service roll
# (circuit breaker auto-rolls back a broken def).
variable "readonly_root_filesystem" {
  type        = bool
  default     = false
  description = "Set readonlyRootFilesystem on the app container and mount /tmp as an ephemeral volume. Default false preserves the live task def; flipping rolls the service."
}

# Sec (REQ-012 item 8c): ECS Exec on the api service. Default true = the current live state
# (break-glass shells stay available, now with session audit logging via the cluster's
# execute_command_configuration). Set false to close the interactive-shell surface entirely.
variable "enable_ecs_exec" {
  type        = bool
  default     = true
  description = "enable_execute_command on the api service. Default true = current live state; sessions are KMS-encrypted + audit-logged via the cluster exec configuration."
}

# Plain env entries injected only when set, so an apply with the "" defaults changes nothing on
# the live task (deploy invariance). Names mirror shared/config.py — never invented here.
locals {
  plain_env = merge(
    { for k, v in {
      STRIPE_PRICE_ID_STARTER        = var.stripe_price_id_starter
      STRIPE_PRICE_ID_TEAM           = var.stripe_price_id_team
      STRIPE_PRICE_ID_SCALE          = var.stripe_price_id_scale
      STRIPE_SUCCESS_URL             = var.stripe_success_url
      STRIPE_CANCEL_URL              = var.stripe_cancel_url
      RESEND_FROM_EMAIL              = var.resend_from_email
      SIGNUP_VERIFY_URL_BASE         = var.signup_verify_url_base
      SIGNUP_INTERNAL_BYPASS_DOMAINS = var.signup_internal_bypass_domains
      CORTEX_S3_BUCKET               = var.cortex_s3_bucket
      CORTEX_LOCAL_DIR               = var.cortex_local_dir
    } : k => v if v != "" },
    # Per-module Stripe Price ids (Phase-2 module billing). Same inject-only-when-set discipline:
    # an empty map adds nothing, so the live task is unchanged until the owner populates it.
    { for k, v in var.stripe_module_price_ids : k => v if v != "" },
  )
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${var.project}-api"
  retention_in_days = var.log_retention_days
}

# ADOT (AWS Distro for OpenTelemetry) collector sidecar log group — see the sidecar container below.
resource "aws_cloudwatch_log_group" "api_otel" {
  name              = "/ecs/${var.project}-api-otel"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${var.project}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  # Image is arm64 (Graviton, cheaper); built on Apple Silicon.
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }

  # /tmp as a Fargate EPHEMERAL volume (no host_path / no tmpfs — tmpfs is EC2-launch-type
  # only): exists ONLY when readonly_root_filesystem flips, so the default task def stays
  # byte-identical to the live one.
  dynamic "volume" {
    for_each = var.readonly_root_filesystem ? [1] : []
    content {
      name = "tmp"
    }
  }

  container_definitions = jsonencode([
    merge({
      name         = "api"
      image        = var.image != "" ? var.image : "${var.project}-api:latest"
      essential    = true
      portMappings = [{ containerPort = 8000, protocol = "tcp" }]
      environment = concat([
        { name = "AWS_REGION", value = var.region },
        { name = "COGNITO_USER_POOL_ID", value = var.cognito_user_pool_id },
        { name = "COGNITO_CLIENT_ID", value = var.cognito_client_id },
        { name = "DB_HOST", value = var.aurora_endpoint },
        { name = "DB_NAME", value = "uplift" },
        # For `python -m api.migrate` (one-off task): master + crm secret ARNs (read via boto3).
        { name = "AURORA_MASTER_SECRET_ARN", value = var.aurora_master_secret_arn },
        { name = "CRM_APP_SECRET_ARN", value = var.db_secret_arn },
        # Sec/P0 (REQ-012 item 3): arms shared/config.is_prod() — the refuse-to-boot guard for
        # SIGNUP_INTERNAL_BYPASS_DOMAINS-in-prod reads exactly this.
        { name = "UPLIFT_ENVIRONMENT", value = var.uplift_environment },
        ],
        # REQ-003 step 0: the master switch appears ONLY at the deliberate go-live act —
        # without it build_signup_deps() boots all-stub even with every secret present.
        var.signup_real_deps ? [{ name = "SIGNUP_REAL_DEPS", value = "1" }] : [],
        # DRAFT-GATE (CLAUDE.md hard-constraint #2): the email/SMS senders only deliver when this is
        # exactly "true". Absent => senders log + drop (no verification email/OTP reaches users).
        # Deliberate, separate go-live act — flip ONLY after SNS SMS is out of sandbox (spend limit +
        # origination identity) and the Resend sending domain is verified.
        var.allow_real_sends ? [{ name = "ALLOW_REAL_SENDS", value = "true" }] : [],
        # FEATURE FLAG: phone (SMS OTP) verification. Default (true) = phone required, no env set.
        # Set signup_require_phone=false → SIGNUP_REQUIRE_PHONE=false → email-only signup (skip the
        # phone step) while SMS account-level approval is pending.
        var.signup_require_phone ? [] : [{ name = "SIGNUP_REQUIRE_PHONE", value = "false" }],
        var.provisioning_sfn_arn != "" ? [{ name = "PROVISIONING_SFN_ARN", value = var.provisioning_sfn_arn }] : [],
        var.cube_endpoint != "" ? [{ name = "CUBE_ENDPOINT", value = var.cube_endpoint }] : [],
        var.posthog_host != "" ? [{ name = "POSTHOG_HOST", value = var.posthog_host }] : [],
        var.integrations_real ? [{ name = "INTEGRATIONS_REAL_SECRETS", value = "1" }] : [],
        # Sec (REQ-013): RBAC strict mode. Default OFF = the back-compat allowance (a user with
        # NO cognito:groups is treated as tenant-admin, so pre-RBAC users keep working). Flip to
        # 1 ONLY after every functional user has been assigned a group — then a group-less user is
        # NO LONGER auto-admin. api.auth.is_tenant_admin reads exactly this.
        var.rbac_strict ? [{ name = "RBAC_STRICT", value = "1" }] : [],
        # Connector OAuth flow config (non-secret): the public API base the provider redirect_uri is
        # built from (must route through the edge that stamps X-Origin-Verify — the SPA's /api path,
        # NOT the bare ALB), and where the callback returns the browser. Gated on the state-secret ARN
        # being wired so OAUTH_STATE_SECRET (below) and these always appear together.
        var.oauth_state_secret_arn != "" ? [
          { name = "OAUTH_REDIRECT_BASE", value = "https://friesenlabs.com/api" },
          { name = "OAUTH_APP_RETURN_URL", value = "https://friesenlabs.com/?view=integrations" },
        ] : [],
        # REQ-012 step 6: real sync-runner + csv-importer deps in the API process (the routes'
        # async/202 path; in-request syncs are gone). Deliberate, separate flip from the
        # secrets switch above.
        var.ingest_real ? [{ name = "INGEST_REAL_STORES", value = "1" }] : [],
        # Playbook schedule-leg honesty (GO_LIVE §7): tells api/routes_studio the EventBridge
        # dispatcher is live so the Studio stops bannering schedule playbooks as inert.
        var.playbook_dispatch_enabled ? [{ name = "PLAYBOOK_DISPATCH_ENABLED", value = "1" }] : [],
        # Signup-plane plain config + Cortex registry (see local.plain_env above) — sorted-by-name
        # map iteration keeps the rendered task def deterministic.
        [for k, v in local.plain_env : { name = k, value = v }]
      )
      secrets = concat(
        [
          # crm_app (non-owner) DB credentials so RLS applies.
          { name = "DB_USER", valueFrom = "${var.db_secret_arn}:username::" },
          { name = "DB_PASS", valueFrom = "${var.db_secret_arn}:password::" },
        ],
        # Connector OAuth: HMAC state-signing secret (whole-secret ARN). Gated so an unset ARN
        # never injects a valueFrom on an empty secret (which would block task startup).
        var.oauth_state_secret_arn != "" ? [
          { name = "OAUTH_STATE_SECRET", valueFrom = var.oauth_state_secret_arn },
        ] : [],
        # REQ-001: org Anthropic key + env-id fallback — API task ONLY (never the worker), and
        # only once the secrets hold values (see var.api_anthropic_env). UPLIFT_ENV_KEY must
        # never appear here.
        var.api_anthropic_env ? [
          { name = "ANTHROPIC_API_KEY", valueFrom = var.anthropic_api_key_secret_arn },
          { name = "UPLIFT_ENV_ID", valueFrom = var.env_id_secret_arn },
        ] : [],
        # Cube REST signing secret (the SAME value the cube service reads as CUBEJS_API_SECRET) —
        # turns cube_client_from_env() live in the API (agents' query_cube + future views data).
        var.cube_api_secret_arn != "" ? [
          { name = "CUBEJS_API_SECRET_VALUE", valueFrom = var.cube_api_secret_arn },
        ] : [],
        # REQ-003: signup/provisioning plane — API task ONLY; never the worker.
        var.api_signup_env && var.posthog_key_arn != "" ? [
          { name = "POSTHOG_PROJECT_KEY_VALUE", valueFrom = var.posthog_key_arn },
        ] : [],
        var.api_signup_env ? [
          { name = "STRIPE_API_KEY", valueFrom = var.stripe_key_arn },
          { name = "RESEND_API_KEY", valueFrom = var.resend_key_arn },
          { name = "STRIPE_WEBHOOK_SECRET", valueFrom = var.stripe_webhook_secret_arn },
          { name = "SIGNUP_TOKEN_SECRET_VALUE", valueFrom = var.signup_token_secret_arn },
          { name = "ANTHROPIC_ADMIN_KEY", valueFrom = var.anthropic_admin_key_secret_arn },
        ] : []
      )
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.api.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "api"
        }
      }
      },
      # Immutable root FS (REQ-012 item 8b) — keys appear ONLY when flipped so the rendered
      # JSON (and therefore the task-def revision) is unchanged at the default. The JSON
      # round-trip keeps both conditional arms the same HCL type (string).
      jsondecode(var.readonly_root_filesystem ? jsonencode({
        readonlyRootFilesystem = true
        mountPoints            = [{ sourceVolume = "tmp", containerPath = "/tmp", readOnly = false }]
      }) : jsonencode({}))
    ),
    # ADOT collector sidecar (H10, offline IaC leg): receives OTLP spans from the api container and
    # exports them to X-Ray. The task role needs xray:PutTraceSegments at apply.
    # NOTE: full end-to-end X-Ray trace verification needs apply (BLOCKED: needs Nick).
    {
      name      = "aws-otel-collector"
      image     = var.adot_image
      essential = false
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.api_otel.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "otel"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "api" {
  name            = "${var.project}-api"
  cluster         = var.cluster_id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  # A broken task def auto-rolls back instead of draining the service to zero.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  # Break-glass debugging (TODO Sec/P3 212): live shell into a task via SSM — no inbound ports.
  # Sessions are KMS-encrypted + audit-logged via the cluster execute_command_configuration
  # (REQ-012 item 8c); var default true = current live state.
  enable_execute_command = var.enable_ecs_exec

  # First deploy: tasks need time to pull the image + pass health checks before the LB drains them.
  health_check_grace_period_seconds = 120

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.security_group_id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.target_group_arn
    container_name   = "api"
    container_port   = 8000
  }
}

output "service_name" { value = aws_ecs_service.api.name }
