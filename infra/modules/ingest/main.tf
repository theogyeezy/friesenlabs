# Ingestion scheduler (REQ-004): a dedicated one-off Fargate task (same arm64 api image,
# command-overridden to `python -m ingest.run_sync --all`) fired by an EventBridge rule that is
# DISABLED by default — flipping var.schedule_enabled is the go-live act. INGEST_REAL_STORES=1
# lives ONLY on this task definition; the API/worker defs never carry INGEST_* names.

variable "project" { type = string }
variable "region" { type = string }
variable "cluster_arn" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "security_group_id" { type = string }
variable "execution_role_arn" { type = string }
variable "image" { type = string }
variable "db_secret_arn" { type = string }
variable "db_host" {
  type    = string
  default = ""
}
variable "ingest_tenants" {
  type    = string
  default = "" # "" => run_sync logs 'nothing to do' and exits 0
}
variable "ingest_raw_bucket" {
  type    = string
  default = "" # "" => raw landing skipped with a warning
}
variable "schedule_enabled" {
  type    = bool
  default = false # REQ-004 go-live act
}
variable "log_retention_days" {
  type    = number
  default = 30
}

data "aws_caller_identity" "current" {}

resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/ecs/${var.project}-ingest"
  retention_in_days = var.log_retention_days
}

# Dedicated TASK ROLE — per-tenant HubSpot secret reads + Titan V2 sync embeds + optional raw lake.
# (Batch-backfill IAM — CreateModelInvocationJob/PassRole/batch bucket — ships as its own REQ.)
data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ingest_task" {
  name               = "${var.project}-ingest-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "ingest_task" {
  name = "ingest-runtime"
  role = aws_iam_role.ingest_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Effect = "Allow"
          Action = ["secretsmanager:GetSecretValue"]
          Resource = concat(
            # per-tenant vault slot uplift/{tenant_id}/{source} for EVERY supported sync
            # connector (ingest/connectors/base.py tenant_secret_ref). `{src}*` matches the
            # bare slot; Stripe + GoHighLevel were unreadable before this (HubSpot-only).
            [
              for src in ["hubspot", "stripe", "gohighlevel"] :
              "arn:aws:secretsmanager:${var.region}:${data.aws_caller_identity.current.account_id}:secret:${var.project}/*/${src}*"
            ],
            [
              # DEPRECATED shared token — until every tenant is migrated
              "arn:aws:secretsmanager:${var.region}:${data.aws_caller_identity.current.account_id}:secret:${var.project}/hubspot-private-app-token*",
            ]
          )
        },
        {
          Effect   = "Allow"
          Action   = ["bedrock:InvokeModel"]
          Resource = "arn:aws:bedrock:${var.region}::foundation-model/amazon.titan-embed-text-v2:0"
        },
        {
          # REQ-012: INGEST_TENANTS="auto" derives the nightly sync set from the vaulted
          # uplift/{tenant}/{source} slots (ingest/run_sync.py discover_tenants), so a tenant
          # who connects via the API is auto-enrolled. ListSecrets is metadata-only (names,
          # never values) and not resource-scopable — Resource must be "*"; the value reads
          # above stay exactly slot-scoped.
          Effect   = "Allow"
          Action   = ["secretsmanager:ListSecrets"]
          Resource = "*"
        }
      ],
      var.ingest_raw_bucket != "" ? [
        {
          Effect   = "Allow"
          Action   = ["s3:PutObject"]
          Resource = "arn:aws:s3:::${var.ingest_raw_bucket}/raw/*"
        }
      ] : []
    )
  })
}

resource "aws_ecs_task_definition" "ingest" {
  family                   = "${var.project}-ingest"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = aws_iam_role.ingest_task.arn

  # Same arm64 api image (bundles ingest/ + db/).
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([
    {
      name      = "ingest"
      image     = var.image
      essential = true
      command   = ["python", "-m", "ingest.run_sync", "--all"]
      environment = [
        # The deliberate act: REAL stores on THIS task only. Unset elsewhere = offline stub.
        { name = "INGEST_REAL_STORES", value = "1" },
        { name = "INGEST_TENANTS", value = var.ingest_tenants },
        { name = "INGEST_RAW_BUCKET", value = var.ingest_raw_bucket },
        { name = "AWS_REGION", value = var.region },
        { name = "DB_HOST", value = var.db_host },
        { name = "DB_NAME", value = "uplift" },
        { name = "DB_PORT", value = "5432" },
      ]
      secrets = [
        { name = "DB_USER", valueFrom = "${var.db_secret_arn}:username::" },
        { name = "DB_PASS", valueFrom = "${var.db_secret_arn}:password::" },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ingest.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "ingest"
        }
      }
    }
  ])
}

# EventBridge → RunTask. DISABLED until var.schedule_enabled flips (the go-live act).
resource "aws_cloudwatch_event_rule" "nightly" {
  name                = "${var.project}-ingest-nightly"
  schedule_expression = "rate(1 day)"
  state               = var.schedule_enabled ? "ENABLED" : "DISABLED"
}

data "aws_iam_policy_document" "events_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "events" {
  name               = "${var.project}-ingest-events"
  assume_role_policy = data.aws_iam_policy_document.events_assume.json
}

resource "aws_iam_role_policy" "events" {
  name = "run-ingest-task"
  role = aws_iam_role.events.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecs:RunTask"]
        Resource = "arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:task-definition/${aws_ecs_task_definition.ingest.family}:*"
        Condition = {
          ArnEquals = { "ecs:cluster" = var.cluster_arn }
        }
      },
      {
        Effect    = "Allow"
        Action    = ["iam:PassRole"]
        Resource  = [aws_iam_role.ingest_task.arn, var.execution_role_arn]
        Condition = { StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } }
      }
    ]
  })
}

resource "aws_cloudwatch_event_target" "ingest" {
  rule     = aws_cloudwatch_event_rule.nightly.name
  arn      = var.cluster_arn
  role_arn = aws_iam_role.events.arn

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.ingest.arn_without_revision # track LATEST
    task_count          = 1
    launch_type         = "FARGATE"

    network_configuration {
      subnets          = var.private_subnet_ids
      security_groups  = [var.security_group_id]
      assign_public_ip = false
    }
  }
}

output "task_definition_family" { value = aws_ecs_task_definition.ingest.family }
output "task_role_arn" { value = aws_iam_role.ingest_task.arn }
