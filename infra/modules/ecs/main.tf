# Shared ECS cluster (Build Guide: Cube runs as a Fargate service in the same cluster as api/worker).
# The cluster lives here so Phase 3 (Cube) and Phase 9 (api/worker) share it. AUTHORED + VALIDATED ONLY.

variable "project" { type = string }
variable "log_retention_days" {
  type    = number
  default = 30 # one knob for every uplift log group (TODO Sec/P3 213)
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# ECS Exec session audit (Sec, REQ-012 item 8c): break-glass shells were enabled with NO session
# logging — an exec'd shell on the prod api task left no transcript. The cluster now OVERRIDEs
# exec logging into a CMK-encrypted log group; sessions themselves are encrypted with the same
# key. Additive (in-place cluster update). The api task role gets kms:Decrypt + log-write in
# modules/iam (ecs-exec-audit); HUMAN callers (Nick's admin session) ride the key policy's
# root-account delegation.
resource "aws_kms_key" "ecs_exec" {
  description             = "${var.project} ECS Exec session encryption + exec audit log group key"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Root-account delegation: IAM policies (task role, admin sessions) govern use.
        Sid       = "EnableIAM"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        # CloudWatch Logs encrypts the exec audit group with this key — scoped to that one group.
        Sid       = "CloudWatchLogsUse"
        Effect    = "Allow"
        Principal = { Service = "logs.${data.aws_region.current.region}.amazonaws.com" }
        Action = [
          "kms:Encrypt*",
          "kms:Decrypt*",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:Describe*",
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:/ecs/${var.project}-exec"
          }
        }
      }
    ]
  })
}

resource "aws_kms_alias" "ecs_exec" {
  name          = "alias/${var.project}-ecs-exec"
  target_key_id = aws_kms_key.ecs_exec.key_id
}

resource "aws_cloudwatch_log_group" "exec" {
  name              = "/ecs/${var.project}-exec"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.ecs_exec.arn
}

resource "aws_ecs_cluster" "this" {
  name = "${var.project}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  configuration {
    execute_command_configuration {
      kms_key_id = aws_kms_key.ecs_exec.arn
      logging    = "OVERRIDE"
      log_configuration {
        cloud_watch_log_group_name     = aws_cloudwatch_log_group.exec.name
        cloud_watch_encryption_enabled = true
      }
    }
  }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]
}

output "cluster_id" { value = aws_ecs_cluster.this.id }
output "cluster_name" { value = aws_ecs_cluster.this.name }
output "ecs_exec_kms_key_arn" { value = aws_kms_key.ecs_exec.arn }
output "ecs_exec_log_group_arn" { value = aws_cloudwatch_log_group.exec.arn }
