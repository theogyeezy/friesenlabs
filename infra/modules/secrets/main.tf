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

output "anthropic_api_key_secret_arn" { value = aws_secretsmanager_secret.anthropic_api_key.arn }
output "connectors_secret_arn" { value = aws_secretsmanager_secret.connectors.arn }
output "crm_app_db_secret_arn" { value = aws_secretsmanager_secret.crm_app_db.arn }
output "cube_api_secret_arn" { value = aws_secretsmanager_secret.cube_api_secret.arn }
output "env_key_secret_arn" { value = aws_secretsmanager_secret.env_key.arn }
