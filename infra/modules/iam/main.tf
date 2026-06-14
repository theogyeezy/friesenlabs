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
        # RBAC (REQ-012 item 10): provisioning assigns the first user of a tenant to the
        # "admin" group (best-effort, app code in flight) — same single-pool scope.
        "cognito-idp:AdminAddUserToGroup",
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
        # REQ-012: DeleteSecret powers DELETE /integrations/{name}/credentials (disconnect,
        # ForceDeleteWithoutRecovery) + the account-delete connector-vault purge; GetSecretValue
        # powers in-process API-kicked syncs (the connector's authenticate() reads the TENANT'S
        # OWN slot; async 202 runs, single-runner-guarded — api/integrations_routes.py).
        "secretsmanager:DeleteSecret",
        "secretsmanager:GetSecretValue",
      ]
      # The connector vault slot is uplift/{tenant_id}/{source}; cover ALL supported sync
      # connectors (google/microsoft/salesforce/pipedrive were AccessDenied on the OAuth callback's
      # token store before this — surfaced live connecting Google 2026-06-13).
      Resource = [
        for src in ["hubspot", "stripe", "gohighlevel", "google", "microsoft", "salesforce", "pipedrive"] :
        "arn:aws:secretsmanager:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:secret:uplift/*/${src}*"
      ]
    }]
  })
}

# OAuth APP credentials (client_id/client_secret) live under uplift/oauth/{provider}/* — a DIFFERENT
# namespace from the per-tenant connector slots above. The /oauth/start + /oauth/callback routes
# READ these to build the authorize URL + exchange the code. HubSpot/GHL happened to be covered by
# the connector-write ARN pattern (uplift/*/hubspot* matches uplift/oauth/hubspot/*); google et al.
# do NOT, so the Google connect 502'd "oauth credential read failed" until this (live 2026-06-13).
resource "aws_iam_role_policy" "api_task_oauth_creds_read" {
  name = "oauth-app-creds-read"
  role = aws_iam_role.task["api"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
      Resource = "arn:aws:secretsmanager:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:secret:uplift/oauth/*"
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

# ECS Exec session audit (REQ-012 item 8c): the cluster's execute_command_configuration now
# OVERRIDEs logging into a KMS-encrypted log group — the TASK role must be able to write the
# session transcript there and use the session-encryption key, or `aws ecs execute-command`
# fails to start. The count rides the STATIC flag (default false = policy not created, module
# stays standalone-validate clean) — it must NOT test the ARN values: they come from resources
# in module.ecs and can be unknown at plan time, which makes `count` un-plannable ("Invalid
# count argument", broke deploy run 27401329030). Unknown values are fine INSIDE the policy.
variable "ecs_exec_audit_enabled" {
  type    = bool
  default = false
}
variable "ecs_exec_kms_key_arn" {
  type    = string
  default = ""
}
variable "ecs_exec_log_group_arn" {
  type    = string
  default = ""
}

resource "aws_iam_role_policy" "api_task_exec_audit" {
  count = var.ecs_exec_audit_enabled ? 1 : 0
  name  = "ecs-exec-audit"
  role  = aws_iam_role.task["api"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = var.ecs_exec_kms_key_arn
      },
      {
        # DescribeLogGroups takes no resource scoping narrower than log-group:*; the write
        # actions are pinned to exactly the exec audit group's streams.
        Effect   = "Allow"
        Action   = ["logs:DescribeLogGroups"]
        Resource = "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:DescribeLogStreams",
          "logs:PutLogEvents",
        ]
        Resource = "${var.ecs_exec_log_group_arn}:*"
      }
    ]
  })
}

# CI/CD (TODO Sec/P1): GitHub Actions OIDC — deploy.yml assumes this role; no static keys.
# Trust is pinned to this repo (build jobs on main + the protected 'production' environment).
# Sec/P0: the role now carries a SCOPED customer-managed deploy policy (attached unconditionally,
# below) enumerating exactly the services this repo's terraform + deploy.yml/build-images.yml
# touch. AdministratorAccess remains attached ONLY behind var.deploy_role_admin_fallback
# (default true = current live state; the union of both policies changes nothing) — the
# migration path is: run one full successful deploy on the scoped policy, then flip the var
# to false to detach admin. See infra/REQUESTS.md (REQ-012 item 1).
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

# Sec/P0 (REQ-012 item 1): keep AdministratorAccess ONLY while the scoped policy below is being
# proven. Default true = the CURRENT LIVE state (zero behavior change: scoped ∪ admin = admin).
# Flip procedure (infra/REQUESTS.md): one full successful deploy.yml run with this still true,
# then set false in tfvars + targeted apply — the deploy role drops to the scoped policy only.
variable "deploy_role_admin_fallback" {
  type        = bool
  default     = true
  description = <<-EOT
    Keep arn:aws:iam::aws:policy/AdministratorAccess attached to the GitHub OIDC deploy role
    alongside the scoped uplift-deploy-scoped policy. SECURITY: admin on an internet-assumable
    (OIDC) role is the single largest blast radius in the account — the goal state is FALSE.
    Flip to false ONLY after one full successful deploy (plan+apply+service roll) has run with
    the scoped policy attached, so a missing permission surfaces as a diff-able AccessDenied
    in CI rather than a broken half-applied prod. Rollback: set true + targeted apply.
  EOT
}

# Address continuity: the attachment used to be unconditional (no count). Without this move,
# flipping nothing would still plan a destroy+create of the live attachment.
moved {
  from = aws_iam_role_policy_attachment.github_deploy_admin
  to   = aws_iam_role_policy_attachment.github_deploy_admin[0]
}

resource "aws_iam_role_policy_attachment" "github_deploy_admin" {
  count      = var.deploy_role_admin_fallback ? 1 : 0
  role       = aws_iam_role.github_deploy.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

# The SCOPED deploy policy (attached unconditionally): exactly the services this repo's
# terraform manages (enumerated module-by-module) + what deploy.yml/build-images.yml call
# directly (ecr login/push, ecs update-service/wait, terraform S3+KMS state backend).
# iam:* is RESTRICTED to the project's own role/policy ARNs; iam:PassRole is conditioned on
# the exact services tasks/functions/rules pass roles to. NO organizations:*, NO account:*,
# NO aws-portal/billing-write anywhere in this policy (pure allowlist — safe to union with
# the admin fallback; no Deny statements that could narrow the live role).
resource "aws_iam_policy" "github_deploy_scoped" {
  name        = "${var.project}-deploy-scoped"
  description = "Scoped CI/CD deploy policy for the ${var.project}-deploy OIDC role (replaces AdministratorAccess once deploy_role_admin_fallback flips false)."
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Service planes this terraform manages: ecs/ecr (api, cube, worker, ingest, scheduled
        # jobs), elbv2 (alb), cloudfront+wafv2 (api_cdn), cognito-idp (auth), rds (data),
        # elasticache (redis), s3+kms (buckets, state backend, log/exec encryption),
        # secretsmanager (secrets, rotation), lambda+states+events (provisioning, schedules),
        # logs+cloudwatch+sns (observability, alarms, dashboards), budgets (guardrails),
        # cloudtrail+config+guardduty (baseline), amplify+route53+acm (web_hosting, dns),
        # servicediscovery (Cloud Map), application-autoscaling (service scaling),
        # serverlessrepo+cloudformation (the SAR crm-db rotation stack).
        Sid    = "DeployInfraServices"
        Effect = "Allow"
        Action = [
          "acm:*",
          "amplify:*",
          "application-autoscaling:*",
          "budgets:*",
          "cloudformation:*",
          "cloudfront:*",
          "cloudtrail:*",
          "cloudwatch:*",
          "cognito-idp:*",
          "config:*",
          "ecr:*",
          "ecs:*",
          "elasticache:*",
          "elasticloadbalancing:*",
          "events:*",
          "guardduty:*",
          "kms:*",
          "lambda:*",
          "logs:*",
          "rds:*",
          "route53:*",
          "s3:*",
          "secretsmanager:*",
          "serverlessrepo:*",
          "servicediscovery:*",
          "sns:*",
          # SSM Parameter Store: terraform manages /uplift/live/* params (outputs.tf) and the
          # deploy pipeline's tfvars clobber guard reads /uplift/live/tfvars-keys — without
          # this, every plan dies on ssm:GetParameter the moment the admin fallback detaches
          # (exactly the 2026-06-12 16:48/16:53 failures).
          "ssm:*",
          "states:*",
          "wafv2:*",
        ]
        Resource = "*"
      },
      {
        # EC2 is restricted to the NETWORKING surface the vpc/security modules manage — no
        # RunInstances/SpotFleet/AMI plane. Describe* covers every read + data source
        # (managed prefix list reads additionally need Get*).
        Sid    = "DeployEc2Networking"
        Effect = "Allow"
        Action = [
          "ec2:Describe*",
          "ec2:GetManagedPrefixList*",
          "ec2:CreateTags",
          "ec2:DeleteTags",
          "ec2:*Vpc*",
          "ec2:*Subnet*",
          "ec2:*RouteTable*",
          "ec2:CreateRoute",
          "ec2:DeleteRoute",
          "ec2:ReplaceRoute",
          "ec2:*InternetGateway*",
          "ec2:*NatGateway*",
          "ec2:AllocateAddress",
          "ec2:ReleaseAddress",
          "ec2:AssociateAddress",
          "ec2:DisassociateAddress",
          "ec2:*SecurityGroup*",
          "ec2:*FlowLogs",
        ]
        Resource = "*"
      },
      {
        # iam:* ONLY on the project's own roles/policies (every role this repo creates is
        # ${project}-* — verified module-by-module), the SAR rotation stack's generated roles
        # (serverlessrepo-${project}-*), and the GitHub OIDC provider resource itself.
        # PassRole is deliberately NOT granted here — see the conditioned statement below.
        Sid    = "IamScopedToProject"
        Effect = "Allow"
        Action = [
          "iam:Get*",
          "iam:List*",
          "iam:CreateRole",
          "iam:DeleteRole",
          "iam:UpdateRole",
          "iam:UpdateRoleDescription",
          "iam:UpdateAssumeRolePolicy",
          "iam:PutRolePolicy",
          "iam:DeleteRolePolicy",
          "iam:AttachRolePolicy",
          "iam:DetachRolePolicy",
          "iam:TagRole",
          "iam:UntagRole",
          "iam:CreatePolicy",
          "iam:DeletePolicy",
          "iam:CreatePolicyVersion",
          "iam:DeletePolicyVersion",
          "iam:SetDefaultPolicyVersion",
          "iam:TagPolicy",
          "iam:UntagPolicy",
          "iam:CreateOpenIDConnectProvider",
          "iam:DeleteOpenIDConnectProvider",
          "iam:UpdateOpenIDConnectProviderThumbprint",
          "iam:AddClientIDToOpenIDConnectProvider",
          "iam:RemoveClientIDFromOpenIDConnectProvider",
          "iam:TagOpenIDConnectProvider",
          "iam:UntagOpenIDConnectProvider",
        ]
        Resource = [
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.project}-*",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/${var.project}-*",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/serverlessrepo-${var.project}-*",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com",
        ]
      },
      {
        # PassRole locked to the exact service principals this stack hands roles to:
        # ecs-tasks (task/execution roles), lambda (provisioning fn), states (SFN role),
        # events (EventBridge->ECS target roles), budgets (deny-at-90 action role),
        # config (recorder role), vpc-flow-logs (flow-log delivery role).
        Sid    = "PassRoleToKnownServicesOnly"
        Effect = "Allow"
        Action = ["iam:PassRole"]
        Resource = [
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.project}-*",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/serverlessrepo-${var.project}-*",
        ]
        Condition = {
          StringEquals = {
            "iam:PassedToService" = [
              "ecs-tasks.amazonaws.com",
              "lambda.amazonaws.com",
              "states.amazonaws.com",
              "events.amazonaws.com",
              "budgets.amazonaws.com",
              "config.amazonaws.com",
              "vpc-flow-logs.amazonaws.com",
            ]
          }
        }
      },
      {
        # First-create of RDS/ECS/ElastiCache/GuardDuty/Config/autoscaling silently needs the
        # service-linked role to exist; scoped to the SLR namespace only.
        Sid      = "ServiceLinkedRoles"
        Effect   = "Allow"
        Action   = ["iam:CreateServiceLinkedRole"]
        Resource = "arn:aws:iam::*:role/aws-service-role/*"
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "github_deploy_scoped" {
  role       = aws_iam_role.github_deploy.name
  policy_arn = aws_iam_policy.github_deploy_scoped.arn
}

output "deploy_role_arn" { value = aws_iam_role.github_deploy.arn }

output "ecs_task_execution_role_arn" { value = aws_iam_role.ecs_task_execution.arn }
output "task_role_arns" { value = { for k, r in aws_iam_role.task : k => r.arn } }
