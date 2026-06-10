# Provisioning Lambda (REQ-005). Count-gated on var.image_uri — authored + role pre-created;
# the function appears once Lane Nick pushes the image. Secrets arrive as ENV VALUES (Lambda env
# is KMS-encrypted at rest; spec-blessed choice) so the function role needs no SM reads.
# SIGNUP_REAL_DEPS and ALLOW_REAL_SENDS are DELIBERATELY ABSENT — unset = all-stub invocations
# (deploy invariance per REQ-003); setting them is the separate signup go-live act.

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
# uplift/anthropic-admin-key is EMPTY until the # VERIFY'd endpoints are confirmed — a
# secret-version data read on an empty secret fails, so the env entry is gated separately.
variable "admin_key_secret_id" { type = string }
variable "admin_key_available" {
  type    = bool
  default = false
}
variable "posthog_key_secret_id" {
  type    = string
  default = "" # REQ-006: the platform posthog-project-key (has a value; read as env VALUE)
}
variable "posthog_host" {
  type    = string
  default = ""
}

data "aws_secretsmanager_secret_version" "db" {
  secret_id = var.db_secret_arn
}

data "aws_secretsmanager_secret_version" "resend" {
  secret_id = var.resend_key_secret_id
}

data "aws_secretsmanager_secret_version" "admin_key" {
  count     = var.admin_key_available ? 1 : 0
  secret_id = var.admin_key_secret_id
}

data "aws_secretsmanager_secret_version" "posthog" {
  count     = var.posthog_key_secret_id != "" ? 1 : 0
  secret_id = var.posthog_key_secret_id
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
        "cognito-idp:AdminSetUserPassword",
        "cognito-idp:AdminUpdateUserAttributes",
      ]
      Resource = var.cognito_user_pool_arn
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
        DB_USER                = jsondecode(data.aws_secretsmanager_secret_version.db.secret_string)["username"]
        DB_PASS                = jsondecode(data.aws_secretsmanager_secret_version.db.secret_string)["password"]
        DB_HOST                = var.db_host
        DB_NAME                = "uplift"
        DB_PORT                = "5432"
        COGNITO_USER_POOL_ID   = var.cognito_user_pool_id
        RESEND_API_KEY         = data.aws_secretsmanager_secret_version.resend.secret_string
        RESEND_FROM_EMAIL      = var.resend_from_email
        SIGNUP_VERIFY_URL_BASE = var.verify_url_base
      },
      var.admin_key_available ? {
        ANTHROPIC_ADMIN_KEY = data.aws_secretsmanager_secret_version.admin_key[0].secret_string
      } : {},
      var.posthog_key_secret_id != "" ? {
        POSTHOG_PROJECT_KEY_VALUE = data.aws_secretsmanager_secret_version.posthog[0].secret_string
      } : {},
      var.posthog_host != "" ? { POSTHOG_HOST = var.posthog_host } : {}
    )
  }
}

output "function_arn" { value = var.image_uri != "" ? aws_lambda_function.provisioning[0].arn : "" }
