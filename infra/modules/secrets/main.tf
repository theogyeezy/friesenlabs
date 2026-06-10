# Secrets (Build Guide §Step 8): containers reference these by name from task defs — never plaintext env.
# Values are NOT set here (no secrets in code/state). Create the container, then put the value in via
# the console / CLI / provisioning, or let the producing resource manage it
# (e.g. Aurora master via RDS --manage-master-user-password in Phase 1).

variable "project" { type = string }

resource "aws_secretsmanager_secret" "anthropic_api_key" {
  name        = "${var.project}/anthropic-api-key"
  description = "Anthropic API key for the agent plane (never on the worker host)."
}

resource "aws_secretsmanager_secret" "connectors" {
  name        = "${var.project}/connector-secrets"
  description = "Per-connector credentials (HubSpot, Stripe, etc.) — populated at provisioning."
}

resource "aws_secretsmanager_secret" "crm_app_db" {
  name        = "${var.project}/crm-app-db"
  description = "crm_app (non-owner) Postgres credentials the app/cube/worker connect with."
}

resource "aws_secretsmanager_secret" "cube_api_secret" {
  name        = "${var.project}/cube-api-secret"
  description = "CUBEJS_API_SECRET — JWT signing secret for the Cube API."
}

resource "aws_secretsmanager_secret" "env_key" {
  name        = "${var.project}/env-key"
  description = "Managed Agents ENVIRONMENT key — authenticates the worker to the queue (NOT the org API key)."
}

# REQ-001: Managed Agents self-hosted ENVIRONMENT ID (not a credential, but task defs read it
# via valueFrom alongside the env key). Value written after the live create_environment run.
resource "aws_secretsmanager_secret" "env_id" {
  name        = "${var.project}/env-id"
  description = "Managed Agents environment id — single-tenant fallback; per-tenant rows take precedence."
}

# Sec/P0: shared secret CloudFront injects as X-Origin-Verify so the ALB can reject requests that
# bypass our edge (any stranger's CloudFront distro passes the SG prefix-list check). The value is
# generated only when var.enable_origin_verify is set (phase-1 of the two-phase rollout) and lives
# in SM for rotation; it also lands in the (KMS-encrypted S3) state — acceptable, never in git.
variable "enable_origin_verify" {
  type    = bool
  default = false
}

resource "aws_secretsmanager_secret" "origin_verify" {
  name        = "${var.project}/origin-verify"
  description = "X-Origin-Verify shared secret: CloudFront custom origin header, enforced at the ALB listener."
}

resource "random_password" "origin_verify" {
  count   = var.enable_origin_verify ? 1 : 0
  length  = 48
  special = false # header-safe
}

resource "aws_secretsmanager_secret_version" "origin_verify" {
  count         = var.enable_origin_verify ? 1 : 0
  secret_id     = aws_secretsmanager_secret.origin_verify.id
  secret_string = random_password.origin_verify[0].result
}

# REQ-003: signup/provisioning plane secrets (API task ONLY — never the worker).
# Values arrive out-of-band: webhook secret from the Stripe dashboard after endpoint registration;
# token-signer minted by Lane Nick (openssl rand -hex 32, CLI put — never in git or TF state);
# admin key (sk-ant-admin…, distinct from the inference key) after the # VERIFY'd endpoints in
# signup/anthropic_admin.py are confirmed.
resource "aws_secretsmanager_secret" "stripe_webhook_secret" {
  name        = "${var.project}/stripe-webhook-secret"
  description = "Stripe webhook signing secret — construct_event refuses all webhooks while empty."
}

resource "aws_secretsmanager_secret" "signup_token_secret" {
  name        = "${var.project}/signup-token-secret"
  description = "HMAC key for signup email/phone verification tokens (REQ-003)."
}

resource "aws_secretsmanager_secret" "anthropic_admin_key" {
  name        = "${var.project}/anthropic-admin-key"
  description = "Anthropic ADMIN key (workspace provisioning) — NOT the inference key; API task only."
}

output "anthropic_api_key_secret_arn" { value = aws_secretsmanager_secret.anthropic_api_key.arn }
output "connectors_secret_arn" { value = aws_secretsmanager_secret.connectors.arn }
output "crm_app_db_secret_arn" { value = aws_secretsmanager_secret.crm_app_db.arn }
output "cube_api_secret_arn" { value = aws_secretsmanager_secret.cube_api_secret.arn }
output "env_key_secret_arn" { value = aws_secretsmanager_secret.env_key.arn }
output "env_id_secret_arn" { value = aws_secretsmanager_secret.env_id.arn }
output "stripe_webhook_secret_arn" { value = aws_secretsmanager_secret.stripe_webhook_secret.arn }
output "signup_token_secret_arn" { value = aws_secretsmanager_secret.signup_token_secret.arn }
output "anthropic_admin_key_secret_arn" { value = aws_secretsmanager_secret.anthropic_admin_key.arn }
output "origin_verify_secret_arn" { value = aws_secretsmanager_secret.origin_verify.arn }
output "origin_verify_value" {
  value     = var.enable_origin_verify ? random_password.origin_verify[0].result : ""
  sensitive = true
}
