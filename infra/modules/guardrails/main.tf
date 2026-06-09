# Cost guardrails (Build Guide Phase 11, Step 58-59).
# There's no true hard cap on AWS — use two layers: a Budget ACTION that attaches a Deny policy at 90%
# (the "stop new spend" lever) + a CloudWatch billing alarm (us-east-1). Plus per-tenant cost tags.
# The Anthropic side is capped per-workspace at provisioning (signup/provisioning set_limits).
# AUTHORED + VALIDATED ONLY.

variable "project" { type = string }
variable "monthly_budget_usd" {
  type    = string
  default = "500"
}
variable "notify_email" {
  type    = string
  default = "" # set before apply
}
variable "deny_target_role_arns" {
  type    = list(string)
  default = []
}
variable "budgets_action_execution_role_arn" {
  type    = string
  default = "" # role AWS Budgets assumes to apply the Deny policy
}

resource "aws_budgets_budget" "monthly" {
  name         = "${var.project}-monthly"
  budget_type  = "COST"
  limit_amount = var.monthly_budget_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.notify_email != "" ? [var.notify_email] : []
  }
}

# Budget ACTION: at 90% actual, attach the AWS-managed Deny-all policy to the target roles.
resource "aws_budgets_budget_action" "deny_at_90" {
  count              = var.budgets_action_execution_role_arn != "" ? 1 : 0
  budget_name        = aws_budgets_budget.monthly.name
  action_type        = "APPLY_IAM_POLICY"
  approval_model     = "AUTOMATIC"
  notification_type  = "ACTUAL"
  execution_role_arn = var.budgets_action_execution_role_arn

  action_threshold {
    action_threshold_type  = "PERCENTAGE"
    action_threshold_value = 90
  }

  definition {
    iam_action_definition {
      policy_arn = "arn:aws:iam::aws:policy/AWSDenyAll"
      roles      = var.deny_target_role_arns
    }
  }

  subscriber {
    subscription_type = "EMAIL"
    address           = var.notify_email
  }
}

# CloudWatch billing alarm — MUST live in us-east-1 (lags ~6h; notify only).
resource "aws_cloudwatch_metric_alarm" "billing" {
  alarm_name          = "${var.project}-billing"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "EstimatedCharges"
  namespace           = "AWS/Billing"
  period              = 21600
  statistic           = "Maximum"
  threshold           = var.monthly_budget_usd
  dimensions          = { Currency = "USD" }
}

output "budget_name" { value = aws_budgets_budget.monthly.name }
