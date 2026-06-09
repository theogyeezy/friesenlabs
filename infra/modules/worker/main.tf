# Self-hosted tool-execution worker on ECS Fargate (Build Guide Phase 4, Step 27).
# Private subnets, SG_API (reaches Aurora/Cube/Redis), outbound 443 to api.anthropic.com.
# Authenticated by the ENVIRONMENT KEY from Secrets Manager — never the org API key.
# AUTHORED + VALIDATED ONLY.

variable "project" { type = string }
variable "region" { type = string }
variable "cluster_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "security_group_id" { type = string }
variable "execution_role_arn" { type = string }
variable "task_role_arn" { type = string }
variable "env_key_secret_arn" { type = string }
variable "image" {
  type    = string
  default = "" # set to the ECR worker image (uplift-worker) before apply
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${var.project}-worker"
  retention_in_days = 30
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.project}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name      = "worker"
      image     = var.image != "" ? var.image : "${var.project}-worker:latest" # verify: real ECR URI
      essential = true
      secrets = [
        # ONLY the environment key reaches the worker. The org API key must never be here.
        { name = "UPLIFT_ENV_KEY", valueFrom = var.env_key_secret_arn },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.worker.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "worker"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "worker" {
  name            = "${var.project}-worker"
  cluster         = var.cluster_id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.security_group_id]
    assign_public_ip = false
  }
}

output "service_name" { value = aws_ecs_service.worker.name }
