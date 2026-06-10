# Self-hosted tool-execution worker on ECS Fargate (Build Guide Phase 4, Step 27).
# Private subnets, SG_API (reaches Aurora/Cube/Redis), outbound 443 to api.anthropic.com.
# Authenticated by the ENVIRONMENT KEY from Secrets Manager — never the org API key.
# AUTHORED + VALIDATED ONLY.

variable "project" { type = string }
variable "log_retention_days" {
  type    = number
  default = 30 # one knob for every uplift log group (TODO Sec/P3 213)
}
variable "region" { type = string }
variable "cluster_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "security_group_id" { type = string }
variable "execution_role_arn" { type = string }
variable "task_role_arn" { type = string }
variable "env_key_secret_arn" { type = string }
variable "env_id_secret_arn" { type = string }
variable "db_secret_arn" { type = string }
variable "db_host" {
  type    = string
  default = ""
}
variable "db_name" {
  type    = string
  default = "uplift"
}
variable "db_port" {
  type    = string
  default = "5432"
}
variable "cube_endpoint" {
  type    = string
  default = "" # REQ-001: safe "" default so validate/plan stay green before cube is deployed
}
variable "desired_count" {
  type    = number
  default = 2 # >=2: the SDK worker serves work items SEQUENTIALLY — one wedged/slow session
  # starves the queue at 1 (#161, proven live 2026-06-10); 2 gives minimal head-of-line relief.
}

variable "image" {
  type    = string
  default = "" # set to the ECR worker image (uplift-worker) before apply
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${var.project}-worker"
  retention_in_days = var.log_retention_days
}

# ADOT (AWS Distro for OpenTelemetry) collector sidecar log group — see the sidecar container below.
resource "aws_cloudwatch_log_group" "worker_otel" {
  name              = "/ecs/${var.project}-worker-otel"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.project}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  # arm64 (Graviton) — matches the api image toolchain; the worker image builds natively on
  # Apple Silicon and runs cheaper.
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([
    {
      name      = "worker"
      image     = var.image != "" ? var.image : "${var.project}-worker:latest" # verify: real ECR URI
      essential = true
      environment = [
        # REQ-001: worker builds its tool clients from env in run().
        { name = "CLOUDWATCH_METRICS", value = "1" },
        { name = "CUBE_ENDPOINT", value = var.cube_endpoint },
        { name = "DB_HOST", value = var.db_host },
        { name = "DB_NAME", value = var.db_name },
        { name = "DB_PORT", value = var.db_port },
      ]
      secrets = [
        # ONLY the environment key + id reach the worker. The org ANTHROPIC_API_KEY must never
        # be here — this api/worker asymmetry IS the security boundary (REQ-001).
        { name = "UPLIFT_ENV_KEY", valueFrom = var.env_key_secret_arn },
        { name = "UPLIFT_ENV_ID", valueFrom = var.env_id_secret_arn },
        { name = "DB_USER", valueFrom = "${var.db_secret_arn}:username::" },
        { name = "DB_PASS", valueFrom = "${var.db_secret_arn}:password::" },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.worker.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "worker"
        }
      }
    },
    # ADOT collector sidecar (H10, offline IaC leg): receives OTLP spans from the worker container and
    # exports them to X-Ray. The task role needs xray:PutTraceSegments at apply.
    # NOTE: full end-to-end X-Ray trace verification needs apply (BLOCKED: needs Nick).
    {
      name      = "aws-otel-collector"
      image     = "public.ecr.aws/aws-observability/aws-otel-collector:latest"
      essential = false
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.worker_otel.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "otel"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "worker" {
  name            = "${var.project}-worker"
  cluster         = var.cluster_id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  # A broken task def auto-rolls back instead of draining the service to zero.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.security_group_id]
    assign_public_ip = false
  }
}

output "service_name" { value = aws_ecs_service.worker.name }
