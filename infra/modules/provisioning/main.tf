# Provisioning orchestration (Build Guide Phase 10, Step 55) as a Step Functions state machine.
# Triggered by the verified Stripe webhook. Each step invokes the provisioning Lambda (which runs the
# idempotent, rollback-safe `signup.provisioning.Provisioner` logic). Retries are safe because every
# step is idempotent; any failure is caught and the account is PARKED in provisioning_failed for retry.
# AUTHORED + VALIDATED ONLY (no Lambda is deployed here; pass its ARN at apply).

variable "project" { type = string }
variable "provisioning_lambda_arn" {
  type    = string
  default = "" # the Lambda wrapping signup.provisioning.Provisioner; set at apply
}

data "aws_iam_policy_document" "sfn_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name               = "${var.project}-provisioning-sfn"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json
}

resource "aws_iam_role_policy" "sfn_invoke" {
  name = "invoke-provisioning-lambda"
  role = aws_iam_role.sfn.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = var.provisioning_lambda_arn != "" ? var.provisioning_lambda_arn : "*"
    }]
  })
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  # Use the real Lambda ARN when supplied; otherwise a syntactically-valid placeholder ARN so the
  # Step Functions definition passes the provider's schema validation at plan time (the real ARN is
  # set at apply, once the provisioning Lambda is deployed).
  lambda = var.provisioning_lambda_arn != "" ? var.provisioning_lambda_arn : "arn:aws:lambda:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:function:${var.project}-provisioning"

  # One idempotent step per provisioning stage; a failure in any stage is caught and the account is
  # parked (the Provisioner also rolls back partial resources like the half-created workspace).
  steps = ["tenant_record", "workspace", "agent_plane", "cognito_tenant", "tenant_context", "welcome"]

  states = merge(
    {
      for i, s in local.steps : "Step_${s}" => {
        Type     = "Task"
        Resource = local.lambda
        # Pass the account + which step to run; the Lambda is idempotent (check-then-create).
        Parameters = { "account_id.$" = "$.account_id", step = s }
        ResultPath = "$.last"
        Retry = [{
          ErrorEquals     = ["States.TaskFailed"]
          IntervalSeconds = 2
          MaxAttempts     = 3
          BackoffRate     = 2.0
        }]
        Catch = [{
          ErrorEquals = ["States.ALL"]
          ResultPath  = "$.error"
          Next        = "ParkProvisioningFailed"
        }]
        Next = i + 1 < length(local.steps) ? "Step_${local.steps[i + 1]}" : "Activate"
      }
    },
    {
      Activate = {
        Type       = "Task"
        Resource   = local.lambda
        Parameters = { "account_id.$" = "$.account_id", step = "activate" }
        End        = true
      }
      ParkProvisioningFailed = {
        Type       = "Task"
        Resource   = local.lambda
        Parameters = { "account_id.$" = "$.account_id", step = "park_failed" }
        End        = true
      }
    }
  )
}

resource "aws_sfn_state_machine" "provisioning" {
  name     = "${var.project}-provisioning"
  role_arn = aws_iam_role.sfn.arn

  definition = jsonencode({
    Comment = "Idempotent, rollback-safe per-tenant provisioning (runs on the verified Stripe webhook)."
    StartAt = "Step_${local.steps[0]}"
    States  = local.states
  })
}

output "state_machine_arn" { value = aws_sfn_state_machine.provisioning.arn }
