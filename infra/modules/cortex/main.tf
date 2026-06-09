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

# Target (SageMaker pipeline / Fargate task) is attached at apply time; left out so validate needs no
# live ARNs. VERIFY: point this at the retrain job and add the drift-alarm SNS topic before apply.

output "retrain_rule_name" { value = aws_cloudwatch_event_rule.retrain.name }
