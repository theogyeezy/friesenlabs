# Provisioning Lambda (REQ-005). Count-gated on var.image_uri — authored + role pre-created;
# the function appears once Lane Nick pushes the image.
#
# SECRETS BY ARN, NEVER BY VALUE: earlier revisions resolved Secrets Manager VALUES at plan time
# (data.aws_secretsmanager_secret_version) into the Lambda env — which copies every secret into
# Terraform state and breaks rotation (a rotated value needs a re-apply to reach the function).
# The env now carries *_SECRET_ARN references only; the function role is granted
# secretsmanager:GetSecretValue on exactly those ARNs and the handler resolves them at cold
# start (code-side contract: signup/lambda_handler — owned by the signup lane).
#
# SIGNUP_REAL_DEPS rides var.signup_real_deps — the SAME deliberate go-live act as the API task
# (REQ-003 step 0); default false = all-stub invocations (deploy invariance per REQ-003).
# ALLOW_REAL_SENDS remains DELIBERATELY ABSENT — draft-only is a hard constraint (CLAUDE.md #2).

variable "project" { type = string }
variable "image_uri" {
  type    = string
  default = "" # set to the pushed uplift-provisioning ECR image to create the function
}
variable "private_subnet_ids" { type = list(string) }
variable "security_group_id" { type = string }
variable "db_secret_arn" { type = string }
variable "db_host" {
  type    = string
  default = ""
}
variable "cognito_user_pool_id" { type = string }
variable "resend_key_secret_id" { type = string }
variable "resend_from_email" {
  type    = string
  default = ""
}
variable "verify_url_base" {
  type    = string
  default = ""
}
# uplift/anthropic-admin-key may be EMPTY (no version) — a GetSecretValue on an empty secret
# fails at cold start, so the env entry stays gated even though nothing reads it at plan time.
variable "admin_key_secret_id" { type = string }
variable "admin_key_available" {
  type    = bool
  default = false
}
variable "posthog_key_secret_id" {
  type    = string
  default = "" # REQ-006: the platform posthog-project-key (ARN reference; resolved in-handler)
}
variable "posthog_host" {
  type    = string
  default = ""
}
# REQ-003 step 0 on the LAMBDA: without it build_provisioner() boots all-stub no matter what
# other env is present. Wire it to the SAME root flag as the API task so one deliberate flip
# moves the whole signup plane together.
variable "signup_real_deps" {
  type    = bool
  default = false
}
# The AI-plane pair the agent_plane provisioning step needs (org key + MA environment id) —
# mirrors the api task's api_anthropic_env gate: flip ONLY after uplift/anthropic-api-key +
# uplift/env-id hold values.
variable "anthropic_api_key_secret_arn" {
  type    = string
  default = ""
}
variable "env_id_secret_arn" {
  type    = string
  default = ""
}
variable "anthropic_env_available" {
  type    = bool
  default = false
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${var.project}-provisioning-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

variable "cognito_user_pool_arn" {
  type    = string
  default = ""
}

# Provisioning stamps custom:tenant_id (and the parked-account retry path re-reads the user) —
# the same five admin ops as the api task, same single-pool scope.
resource "aws_iam_role_policy" "cognito_signup" {
  count = var.cognito_user_pool_arn != "" ? 1 : 0
  name  = "cognito-signup-admin"
  role  = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "cognito-idp:AdminCreateUser",
        "cognito-idp:AdminGetUser",
        "cognito-idp:AdminConfirmSignUp",
        "cognito-idp:AdminUpdateUserAttributes",
        "cognito-idp:AdminSetUserPassword",
      ]
      Resource = var.cognito_user_pool_arn
    }]
  })
}

# Exactly the secret ARNs the handler may resolve at cold start — listed, never a wildcard.
locals {
  secret_read_arns = compact([
    var.db_secret_arn,
    var.resend_key_secret_id,
    var.admin_key_secret_id,
    var.posthog_key_secret_id,
    var.anthropic_api_key_secret_arn,
    var.env_id_secret_arn,
  ])
}

resource "aws_iam_role_policy" "secrets_read" {
  name = "read-provisioning-secrets"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = local.secret_read_arns
    }]
  })
}

resource "aws_iam_role_policy_attachment" "vpc_access" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_lambda_function" "provisioning" {
  count         = var.image_uri != "" ? 1 : 0
  function_name = "${var.project}-provisioning"
  package_type  = "Image"
  image_uri     = var.image_uri
  role          = aws_iam_role.lambda.arn
  architectures = ["arm64"]
  timeout       = 60 # one step per invocation; SFN owns retries (3, backoff)
  memory_size   = 512

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [var.security_group_id] # sg_api already holds Aurora 5432 ingress pairing
  }

  environment {
    variables = merge(
      {
        # Plain config + Secrets Manager ARN references — never resolved values (state safety +
        # rotation). CRM_APP_SECRET_ARN carries the crm_app username/password JSON under the SAME
        # env name api/migrate.py already resolves via boto3.
        CRM_APP_SECRET_ARN        = var.db_secret_arn
        DB_HOST                   = var.db_host
        DB_NAME                   = "uplift"
        DB_PORT                   = "5432"
        COGNITO_USER_POOL_ID      = var.cognito_user_pool_id
        RESEND_API_KEY_SECRET_ARN = var.resend_key_secret_id
        RESEND_FROM_EMAIL         = var.resend_from_email
        SIGNUP_VERIFY_URL_BASE    = var.verify_url_base
      },
      # The deliberate signup go-live act (same flag as the API task — REQ-003 step 0).
      var.signup_real_deps ? { SIGNUP_REAL_DEPS = "1" } : {},
      var.admin_key_available ? {
        ANTHROPIC_ADMIN_KEY_SECRET_ARN = var.admin_key_secret_id
      } : {},
      # AI-plane pair for the agent_plane step (org key — Lambda/API posture, NEVER the worker).
      var.anthropic_env_available ? {
        ANTHROPIC_API_KEY_SECRET_ARN = var.anthropic_api_key_secret_arn
        UPLIFT_ENV_ID_SECRET_ARN     = var.env_id_secret_arn
      } : {},
      var.posthog_key_secret_id != "" ? {
        POSTHOG_PROJECT_KEY_SECRET_ARN = var.posthog_key_secret_id
      } : {},
      var.posthog_host != "" ? { POSTHOG_HOST = var.posthog_host } : {}
    )
  }
}

output "function_arn" { value = var.image_uri != "" ? aws_lambda_function.provisioning[0].arn : "" }
