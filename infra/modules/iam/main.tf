# IAM (Build Guide §Step 5): roles, not long-lived keys.
# - one ecsTaskExecutionRole (pull from ECR, write logs)
# - a distinct task role per service (api, cube, worker) — least privilege, filled in per phase.
# Human access is via IAM Identity Center (SSO) — configured in the console / a separate SSO stack,
# not here (no long-lived IAM users).

variable "project" { type = string }

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_task_execution" {
  name               = "${var.project}-ecsTaskExecutionRole"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "ecs_execution_managed" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Secrets the tasks read: project secrets (uplift/*) + the RDS-managed Aurora master (rds!*).
variable "extra_execution_secret_arns" {
  type    = list(string)
  default = [] # REQ-003: exact platform-secret ARNs (listed, NOT a widened wildcard)
}

locals {
  secret_arns = [
    "arn:aws:secretsmanager:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:secret:uplift/*",
    "arn:aws:secretsmanager:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:secret:rds!*",
  ]
}

# Execution role needs GetSecretValue to inject DB_USER/DB_PASS into the container at launch.
resource "aws_iam_role_policy" "ecs_execution_secrets" {
  name = "read-secrets"
  role = aws_iam_role.ecs_task_execution.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = concat(local.secret_arns, var.extra_execution_secret_arns) }]
  })
}

# Per-service task roles (permissions attached as each service's phase lands).
resource "aws_iam_role" "task" {
  for_each           = toset(["api", "cube", "worker"])
  name               = "${var.project}-${each.key}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = { Service = each.key }
}

# The api task role reads secrets at runtime — ONLY migrate's boto3 reads (master + crm-app-db);
# everything else is execution-role valueFrom injection. Scope to the exact ARNs when supplied
# (TODO Sec/P2: no uplift/* runtime read surface); empty list falls back to the broad pattern so
# validate/plan stay green in contexts that don't pass ARNs.
variable "api_task_secret_arns" {
  type    = list(string)
  default = []
}

resource "aws_iam_role_policy" "api_task_secrets" {
  name = "read-secrets"
  role = aws_iam_role.task["api"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = length(var.api_task_secret_arns) > 0 ? var.api_task_secret_arns : local.secret_arns
    }]
  })
}

# The api task runs an aws-otel-collector sidecar (essential=false) that exports OTLP spans to
# X-Ray — without these the sidecar fails silently and tracing is dead (TODO Sec/P2). X-Ray write
# actions do not support resource-level scoping; ssm:GetParameters covers ADOT config pulls.
resource "aws_iam_role_policy" "api_task_xray" {
  name = "xray-export"
  role = aws_iam_role.task["api"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameters"]
        Resource = "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter/uplift/*"
      }
    ]
  })
}

# The worker's workers_polling heartbeat (worker/worker.py emit_polling_metric) — the datapoint
# the worker_absent alarm watches. PutMetricData takes no resource ARNs; the namespace condition
# is the only scoping CloudWatch supports, pinned to exactly the alarm's namespace.
resource "aws_iam_role_policy" "worker_task_metrics" {
  name = "put-heartbeat-metric"
  role = aws_iam_role.task["worker"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = ["cloudwatch:PutMetricData"]
      Resource  = "*"
      Condition = { StringEquals = { "cloudwatch:namespace" = "Uplift/Agents" } }
    }]
  })
}

# RAG/grounding query-embedding calls Bedrock Titan to embed the query before pgvector retrieval.
# The api task (conv-layer grounding path) and the worker task (the search_rag tool it executes)
# both need bedrock:InvokeModel on the embed model — mirrors the ingest role's sync-embed grant
# (infra/modules/ingest/main.tf). Surfaced live by scripts/verify_agent_plane.py step [3]
# (AccessDenied on amazon.titan-embed-text-v2:0), 2026-06-10.
resource "aws_iam_role_policy" "api_task_bedrock_embed" {
  name = "bedrock-embed"
  role = aws_iam_role.task["api"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel"]
      Resource = "arn:aws:bedrock:${data.aws_region.current.region}::foundation-model/amazon.titan-embed-text-v2:0"
    }]
  })
}

resource "aws_iam_role_policy" "worker_task_bedrock_embed" {
  name = "bedrock-embed"
  role = aws_iam_role.task["worker"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel"]
      Resource = "arn:aws:bedrock:${data.aws_region.current.region}::foundation-model/amazon.titan-embed-text-v2:0"
    }]
  })
}

# Cortex persistent model registry (ml/registry.py S3Registry): the api task (conversation
# factory) and the worker (the run_model/retrain tools) read+write serialized tenant models
# under cortex/* in the datalake bucket. ListBucket (prefix-conditioned) is required so a
# missing key surfaces as NoSuchKey (the registry's "no champion yet" path) instead of
# AccessDenied. Empty name = no policy (the registry env is gated by the same root flag).
variable "cortex_bucket_name" {
  type    = string
  default = ""
}

resource "aws_iam_role_policy" "cortex_registry_s3" {
  for_each = var.cortex_bucket_name != "" ? toset(["api", "worker"]) : toset([])
  name     = "cortex-registry-s3"
  role     = aws_iam_role.task[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "arn:aws:s3:::${var.cortex_bucket_name}/cortex/*"
      },
      {
        Effect    = "Allow"
        Action    = ["s3:ListBucket"]
        Resource  = "arn:aws:s3:::${var.cortex_bucket_name}"
        Condition = { StringLike = { "s3:prefix" = "cortex/*" } }
      }
    ]
  })
}

# The signup plane's Cognito admin ops (signup/cognito_admin.py: create-unconfirmed user,
# verify-confirm, set password, stamp the tenant claim) — EXACTLY the five calls the module
# makes, scoped to the ONE pool. Live 500 on POST /signup without this (AdminCreateUser
# AccessDenied, 2026-06-10).
variable "cognito_user_pool_arn" {
  type    = string
  default = ""
}

resource "aws_iam_role_policy" "api_task_cognito_signup" {
  count = var.cognito_user_pool_arn != "" ? 1 : 0
  name  = "cognito-signup-admin"
  role  = aws_iam_role.task["api"].id
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

# REQ-005: the api task starts provisioning executions — scoped to exactly ONE machine ARN.
variable "provisioning_sfn_arn" {
  type    = string
  default = ""
}

resource "aws_iam_role_policy" "api_task_sfn" {
  count = var.provisioning_sfn_arn != "" ? 1 : 0
  name  = "start-provisioning-sfn"
  role  = aws_iam_role.task["api"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["states:StartExecution"]
      Resource = var.provisioning_sfn_arn
    }]
  })
}

# REQ-008: the api task vaults per-tenant HubSpot tokens — write/existence-check on EXACTLY
# the connector slots (never uplift/* broadly). Trailing wildcard = the SM random ARN suffix.
# VERIFY on first live connect: CreateSecret resource-scoping matches the name pattern.
resource "aws_iam_role_policy" "api_task_connector_write" {
  name = "connector-write"
  role = aws_iam_role.task["api"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "secretsmanager:PutSecretValue",
        "secretsmanager:CreateSecret",
        "secretsmanager:DescribeSecret",
      ]
      Resource = "arn:aws:secretsmanager:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:secret:uplift/*/hubspot*"
    }]
  })
}

# ECS Exec (TODO Sec/P3 212): the api task opens SSM sessions for break-glass shells.
resource "aws_iam_role_policy" "api_task_ssm_exec" {
  name = "ecs-exec-ssm"
  role = aws_iam_role.task["api"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "ssmmessages:CreateControlChannel",
        "ssmmessages:CreateDataChannel",
        "ssmmessages:OpenControlChannel",
        "ssmmessages:OpenDataChannel",
      ]
      Resource = "*"
    }]
  })
}

# CI/CD (TODO Sec/P1): GitHub Actions OIDC — deploy.yml assumes this role; no static keys.
# Trust is pinned to this repo (build jobs on main + the protected 'production' environment).
# Policy is AdministratorAccess for now (terraform apply spans every service) — tightening to a
# scoped deploy policy is a recorded follow-up.
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"] # ignored by AWS for GitHub since 2023 (trust via root CA) but required by the API
}

data "aws_iam_policy_document" "github_deploy_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:theogyeezy/friesenlabs:ref:refs/heads/main",
        "repo:theogyeezy/friesenlabs:environment:production",
      ]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name               = "${var.project}-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_deploy_assume.json
}

resource "aws_iam_role_policy_attachment" "github_deploy_admin" {
  role       = aws_iam_role.github_deploy.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

output "deploy_role_arn" { value = aws_iam_role.github_deploy.arn }

output "ecs_task_execution_role_arn" { value = aws_iam_role.ecs_task_execution.arn }
output "task_role_arns" { value = { for k, r in aws_iam_role.task : k => r.arn } }
