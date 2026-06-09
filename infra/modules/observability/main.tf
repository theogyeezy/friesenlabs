# Observability (Build Guide Phase 11, Step 60).
# CloudWatch alarms across the tiers + an SNS topic for notifications. ADOT/OTEL tracing -> X-Ray and
# Container Insights (already enabled on the cluster) cover request + cluster traces.
# AUTHORED + VALIDATED ONLY.

variable "project" { type = string }
variable "alb_arn_suffix" {
  type    = string
  default = "" # app/uplift-alb/xxxx — from the ALB
}
variable "notify_email" {
  type    = string
  default = ""
}

resource "aws_sns_topic" "alarms" {
  name = "${var.project}-alarms"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.notify_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.notify_email
}

# ALB 5xx (target errors).
resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name          = "${var.project}-alb-5xx"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Sum"
  threshold           = 5
  alarm_actions       = [aws_sns_topic.alarms.arn]
  treat_missing_data  = "notBreaching"
  dimensions          = var.alb_arn_suffix != "" ? { LoadBalancer = var.alb_arn_suffix } : {}
}

# ALB target latency p95.
resource "aws_cloudwatch_metric_alarm" "alb_latency" {
  alarm_name          = "${var.project}-alb-latency"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "TargetResponseTime"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  extended_statistic  = "p95"
  threshold           = 2.0
  alarm_actions       = [aws_sns_topic.alarms.arn]
  treat_missing_data  = "notBreaching"
  dimensions          = var.alb_arn_suffix != "" ? { LoadBalancer = var.alb_arn_suffix } : {}
}

# Aurora ACU utilization (Serverless v2 capacity).
resource "aws_cloudwatch_metric_alarm" "aurora_acu" {
  alarm_name          = "${var.project}-aurora-acu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "ServerlessDatabaseCapacity"
  namespace           = "AWS/RDS"
  period              = 60
  statistic           = "Average"
  threshold           = 14 # of MaxCapacity 16
  alarm_actions       = [aws_sns_topic.alarms.arn]
  treat_missing_data  = "notBreaching"
}

# Redis evictions.
resource "aws_cloudwatch_metric_alarm" "redis_evictions" {
  alarm_name          = "${var.project}-redis-evictions"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "Evictions"
  namespace           = "AWS/ElastiCache"
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  alarm_actions       = [aws_sns_topic.alarms.arn]
  treat_missing_data  = "notBreaching"
}

# Worker: no worker polling the queue (workers_polling == 0) — a custom metric the API emits.
resource "aws_cloudwatch_metric_alarm" "worker_absent" {
  alarm_name          = "${var.project}-workers-polling-zero"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "workers_polling"
  namespace           = "Uplift/Agents"
  period              = 60
  statistic           = "Maximum"
  threshold           = 1
  alarm_actions       = [aws_sns_topic.alarms.arn]
  treat_missing_data  = "breaching" # missing data here means no worker => alarm
}

output "alarms_topic_arn" { value = aws_sns_topic.alarms.arn }
