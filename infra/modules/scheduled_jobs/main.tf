# Scheduled jobs — the two EventBridge → Fargate RunTask schedules that were the missing
# "firing mechanism" for code that already existed (the Cortex retrain fan-out + the playbook
# trigger dispatcher). Mirrors infra/modules/ingest (the proven scheduler pattern): a task-def
# per job + a dedicated task role + an EventBridge rule (DISABLED until its flag flips — the
# go-live act) + an invoke role + a target. Validate-clean; nothing fires until an owner enables.
#
# Also CREATES the CORTEX_SIGNING_KEY secret (was absent from all terraform — the signed model
# registry fails closed without it). The VALUE is set out-of-band by an owner; this just makes the
# secret + its task wiring exist.

variable "project" { type = string }
variable "region" { type = string }
variable "cluster_arn" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "security_group_id" { type = string }
variable "execution_role_arn" { type = string }
variable "image" { type = string }
variable "db_secret_arn" { type = string }
variable "db_host" { type = string }
variable "anthropic_key_secret_arn" {
  type        = string
  description = "Platform org Anthropic API key secret (the playbook dispatcher builds the MA runtime with it)."
}
variable "cortex_s3_bucket" {
  type    = string
  default = "" # "" => retrain exits 'no registry' honestly
}
variable "retrain_enabled" {
  type    = bool
  default = false
}
# Inject CORTEX_SIGNING_KEY into the retrain task only when the secret holds a value (flip with the
# put-secret-value). Default false avoids a startup-blocking valueFrom on the empty secret.
variable "cortex_signing_key_available" {
  type    = bool
  default = false
}
variable "retrain_schedule" {
  type    = string
  default = "rate(7 days)"
}
variable "dispatch_enabled" {
  type    = bool
  default = false
}
variable "dispatch_schedule" {
  type = string
  # ALIGNED quarter-hour ticks (:00/:15/:30/:45), NOT rate(15 minutes): rate() ticks at an
  # arbitrary offset set by enable time (live ticks landed at :12/:27/:42/:57). The dispatcher
  # WINDOW-matches — each tick owns (tick-15m, tick], so ANY cron minute fires exactly once —
  # but the window length (PlaybookDispatcher.WINDOW_MINUTES) must equal this cadence and the
  # ticks must stay boundary-aligned (main() floors container-start jitter to the quarter-hour).
  # Change cadence here and WINDOW_MINUTES together, or windows gap/overlap.
  default = "cron(0/15 * * * ? *)"
}
variable "playbook_dispatch_tenants" {
  type    = string
  default = "" # "" => dispatch logs 'nothing to do' and exits 0
}
variable "drift_alert_email" {
  type    = string
  default = "" # "" => the drift topic exists but no email subscription is created (owner subscribes)
}
variable "log_retention_days" {
  type    = number
  default = 30
}

data "aws_caller_identity" "current" {}

# --- CORTEX_SIGNING_KEY — the signed-registry HMAC secret (value set by an owner) -------------
resource "aws_secretsmanager_secret" "cortex_signing" {
  name        = "${var.project}/cortex-signing-key"
  description = "HMAC key for Cortex signed model artifacts (CORTEX_SIGNING_KEY). Value set out-of-band."
}

# --- Cortex drift alarm SNS topic — the retrain fan-out publishes a positive live-drift verdict
# here so an operator is actually paged (the verdict was previously surfaced only in the UI). Moved
# in from the (now-deleted) legacy module "cortex". A subscription is created only when an email is
# provided; otherwise the topic exists for the owner to subscribe to (email/Slack/PagerDuty).
resource "aws_sns_topic" "drift" {
  name = "${var.project}-cortex-drift"
}

resource "aws_sns_topic_subscription" "drift_email" {
  count     = var.drift_alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.drift.arn
  protocol  = "email"
  endpoint  = var.drift_alert_email
}

# --- shared assume-role docs -----------------------------------------------------------------
data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
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

# --------------------------------------------------------------------------- #
# Job 1: Cortex retrain fan-out — python scripts/ml/retrain_all.py
# --------------------------------------------------------------------------- #
resource "aws_cloudwatch_log_group" "retrain" {
  name              = "/ecs/${var.project}-cortex-retrain"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "retrain_task" {
  name               = "${var.project}-cortex-retrain-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "retrain_task" {
  name = "retrain-runtime"
  role = aws_iam_role.retrain_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    # A harmless base statement guarantees >=1 statement; the S3 grant is ADDED only when the
    # registry bucket is wired (concat with []), mirroring ingest's raw-bucket conditional.
    Statement = concat(
      [{ Effect = "Allow", Action = ["sts:GetCallerIdentity"], Resource = "*" }],
      # Publish drift alerts to the Cortex drift topic (best-effort; the fan-out degrades cleanly
      # if this is ever denied, and stays inert unless CORTEX_DRIFT_TOPIC_ARN is injected below).
      [{ Effect = "Allow", Action = ["sns:Publish"], Resource = aws_sns_topic.drift.arn }],
      var.cortex_s3_bucket != "" ? [
        {
          Effect = "Allow"
          Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
          Resource = [
            "arn:aws:s3:::${var.cortex_s3_bucket}",
            "arn:aws:s3:::${var.cortex_s3_bucket}/cortex/*",
          ]
        }
      ] : []
    )
  })
}

resource "aws_ecs_task_definition" "retrain" {
  family                   = "${var.project}-cortex-retrain"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = aws_iam_role.retrain_task.arn

  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([
    {
      name      = "retrain"
      image     = var.image
      essential = true
      command   = ["python", "scripts/ml/retrain_all.py"]
      environment = [
        { name = "CORTEX_S3_BUCKET", value = var.cortex_s3_bucket },
        { name = "AWS_REGION", value = var.region },
        { name = "DB_HOST", value = var.db_host },
        { name = "DB_NAME", value = "uplift" },
        { name = "DB_PORT", value = "5432" },
        # When set, the fan-out publishes positive live-drift verdicts here (else alerting is inert).
        { name = "CORTEX_DRIFT_TOPIC_ARN", value = aws_sns_topic.drift.arn },
      ]
      # CORTEX_SIGNING_KEY is injected ONLY when the owner has put a value in the secret and
      # flipped cortex_signing_key_available — a valueFrom on an EMPTY secret blocks task startup
      # (ResourceInitializationError). Without it the retrain code fails closed cleanly (a clear
      # SigningKeyError, contained per-tenant), never an opaque init crash.
      secrets = concat([
        { name = "DB_USER", valueFrom = "${var.db_secret_arn}:username::" },
        { name = "DB_PASS", valueFrom = "${var.db_secret_arn}:password::" },
        ],
        var.cortex_signing_key_available ? [
          { name = "CORTEX_SIGNING_KEY", valueFrom = aws_secretsmanager_secret.cortex_signing.arn }
        ] : []
      )
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.retrain.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "retrain"
        }
      }
    }
  ])
}

resource "aws_cloudwatch_event_rule" "retrain" {
  # Keeps the "-job" suffix: the legacy module "cortex" (now deleted) once owned the
  # "${var.project}-cortex-retrain" rule name. If that rule was ever applied live, renaming back
  # would force a destroy/create; the suffix is harmless, so it stays. (EventBridge rule names are
  # unique per acct/region.)
  name                = "${var.project}-cortex-retrain-job"
  description         = "Per-tenant Cortex model retrain fan-out (the flywheel)."
  schedule_expression = var.retrain_schedule
  state               = var.retrain_enabled ? "ENABLED" : "DISABLED"
}

resource "aws_iam_role" "retrain_events" {
  name               = "${var.project}-cortex-retrain-events"
  assume_role_policy = data.aws_iam_policy_document.events_assume.json
}

resource "aws_iam_role_policy" "retrain_events" {
  name = "run-retrain-task"
  role = aws_iam_role.retrain_events.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = ["ecs:RunTask"]
        Resource  = "arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:task-definition/${aws_ecs_task_definition.retrain.family}:*"
        Condition = { ArnEquals = { "ecs:cluster" = var.cluster_arn } }
      },
      {
        Effect    = "Allow"
        Action    = ["iam:PassRole"]
        Resource  = [aws_iam_role.retrain_task.arn, var.execution_role_arn]
        Condition = { StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } }
      }
    ]
  })
}

resource "aws_cloudwatch_event_target" "retrain" {
  rule     = aws_cloudwatch_event_rule.retrain.name
  arn      = var.cluster_arn
  role_arn = aws_iam_role.retrain_events.arn

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.retrain.arn_without_revision
    task_count          = 1
    launch_type         = "FARGATE"
    network_configuration {
      subnets          = var.private_subnet_ids
      security_groups  = [var.security_group_id]
      assign_public_ip = false
    }
  }
}

# --------------------------------------------------------------------------- #
# Job 2: Playbook trigger dispatcher — python -m agents.playbooks.dispatch --schedule
# --------------------------------------------------------------------------- #
resource "aws_cloudwatch_log_group" "dispatch" {
  name              = "/ecs/${var.project}-playbook-dispatch"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "dispatch_task" {
  name               = "${var.project}-playbook-dispatch-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

# The dispatcher drives playbooks through Managed Agents (org key, injected as a secret via the
# execution role) — no extra task-role grants beyond the default are required.
resource "aws_iam_role_policy" "dispatch_task" {
  name = "dispatch-runtime"
  role = aws_iam_role.dispatch_task.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["sts:GetCallerIdentity"], Resource = "*" }]
  })
}

resource "aws_ecs_task_definition" "dispatch" {
  family                   = "${var.project}-playbook-dispatch"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = aws_iam_role.dispatch_task.arn

  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([
    {
      name      = "dispatch"
      image     = var.image
      essential = true
      command   = ["python", "-m", "agents.playbooks.dispatch", "--schedule"]
      environment = [
        { name = "PLAYBOOK_DISPATCH_TENANTS", value = var.playbook_dispatch_tenants },
        { name = "AWS_REGION", value = var.region },
        { name = "DB_HOST", value = var.db_host },
        { name = "DB_NAME", value = "uplift" },
        { name = "DB_PORT", value = "5432" },
      ]
      secrets = [
        { name = "DB_USER", valueFrom = "${var.db_secret_arn}:username::" },
        { name = "DB_PASS", valueFrom = "${var.db_secret_arn}:password::" },
        { name = "ANTHROPIC_API_KEY", valueFrom = var.anthropic_key_secret_arn },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.dispatch.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "dispatch"
        }
      }
    }
  ])
}

resource "aws_cloudwatch_event_rule" "dispatch" {
  name                = "${var.project}-playbook-dispatch"
  description         = "Fire activated playbooks whose cron is due (the trigger dispatcher)."
  schedule_expression = var.dispatch_schedule
  state               = var.dispatch_enabled ? "ENABLED" : "DISABLED"
}

resource "aws_iam_role" "dispatch_events" {
  name               = "${var.project}-playbook-dispatch-events"
  assume_role_policy = data.aws_iam_policy_document.events_assume.json
}

resource "aws_iam_role_policy" "dispatch_events" {
  name = "run-dispatch-task"
  role = aws_iam_role.dispatch_events.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = ["ecs:RunTask"]
        Resource  = "arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:task-definition/${aws_ecs_task_definition.dispatch.family}:*"
        Condition = { ArnEquals = { "ecs:cluster" = var.cluster_arn } }
      },
      {
        Effect    = "Allow"
        Action    = ["iam:PassRole"]
        Resource  = [aws_iam_role.dispatch_task.arn, var.execution_role_arn]
        Condition = { StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } }
      }
    ]
  })
}

resource "aws_cloudwatch_event_target" "dispatch" {
  rule     = aws_cloudwatch_event_rule.dispatch.name
  arn      = var.cluster_arn
  role_arn = aws_iam_role.dispatch_events.arn

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.dispatch.arn_without_revision
    task_count          = 1
    launch_type         = "FARGATE"
    network_configuration {
      subnets          = var.private_subnet_ids
      security_groups  = [var.security_group_id]
      assign_public_ip = false
    }
  }
}

output "cortex_signing_key_secret_arn" { value = aws_secretsmanager_secret.cortex_signing.arn }
output "retrain_rule_name" { value = aws_cloudwatch_event_rule.retrain.name }
output "dispatch_rule_name" { value = aws_cloudwatch_event_rule.dispatch.name }
output "drift_topic_arn" { value = aws_sns_topic.drift.arn }
