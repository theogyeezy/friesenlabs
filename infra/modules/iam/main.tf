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

# The api task role reads secrets at runtime (migrate reads the master + crm secrets via boto3).
resource "aws_iam_role_policy" "api_task_secrets" {
  name = "read-secrets"
  role = aws_iam_role.task["api"].id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = local.secret_arns }]
  })
}

output "ecs_task_execution_role_arn" { value = aws_iam_role.ecs_task_execution.arn }
output "task_role_arns" { value = { for k, r in aws_iam_role.task : k => r.arn } }
