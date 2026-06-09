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

# Per-service task roles (permissions attached as each service's phase lands).
resource "aws_iam_role" "task" {
  for_each           = toset(["api", "cube", "worker"])
  name               = "${var.project}-${each.key}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = { Service = each.key }
}

output "ecs_task_execution_role_arn" { value = aws_iam_role.ecs_task_execution.arn }
output "task_role_arns" { value = { for k, r in aws_iam_role.task : k => r.arn } }
