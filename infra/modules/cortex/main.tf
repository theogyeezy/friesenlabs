# Cortex scheduled retrain (Build Guide Phase 8, Step 47).
# EventBridge triggers periodic per-tenant retrains as new outcomes accumulate. AUTHORED + VALIDATED
# ONLY — the target compute (SageMaker/Modal training job, or a Fargate task) is wired at apply time.

variable "project" { type = string }
variable "retrain_schedule" {
  type    = string
  default = "rate(7 days)"
}

resource "aws_cloudwatch_event_rule" "retrain" {
  name                = "${var.project}-cortex-retrain"
  description         = "Periodic per-tenant model retrain (the flywheel)."
  schedule_expression = var.retrain_schedule
}

# NOTE — the EventBridge rule above has NO target attached here on purpose.
# The retrain target (a SageMaker pipeline / training-job Lambda, or a Fargate task) requires live
# ARNs that don't exist until apply, so attaching an `aws_cloudwatch_event_target` now would break
# `terraform validate`. BLOCKED: needs Nick — at apply, add an `aws_cloudwatch_event_target` whose
# `arn` points at the retrain compute and grant EventBridge `iam:PassRole` / invoke permission.

# Drift-alarm SNS topic: authored now (validate-clean) so the retrain job + Cortex drift detector
# have a stable topic to publish to. Subscriptions/alarm wiring land at apply (needs Nick).
resource "aws_sns_topic" "drift" {
  name = "${var.project}-cortex-drift"
}

output "retrain_rule_name" { value = aws_cloudwatch_event_rule.retrain.name }
output "drift_topic_arn" { value = aws_sns_topic.drift.arn }
