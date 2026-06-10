# Cube semantic layer on ECS Fargate (Build Guide Phase 3, Step 19).
# Runs the cubejs/cube image pointed at Aurora over the private SG, Redis as cache/queue driver.
# Exposed ONLY internally (api + worker call it; never public). AUTHORED + VALIDATED ONLY.

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
variable "aurora_endpoint" { type = string }
variable "redis_endpoint" { type = string }
variable "db_secret_arn" { type = string }
variable "cube_api_secret_arn" { type = string }
variable "namespace_id" {
  type    = string
  default = "" # Cloud Map namespace; "" = no registry (pre-discovery behavior)
}
variable "image" {
  type    = string
  default = "" # custom uplift-cube image (semantic/ baked in); "" = the pinned public image
}

resource "aws_cloudwatch_log_group" "cube" {
  name              = "/ecs/${var.project}-cube"
  retention_in_days = var.log_retention_days
}

# ADOT (AWS Distro for OpenTelemetry) collector sidecar log group — see the sidecar container below.
resource "aws_cloudwatch_log_group" "cube_otel" {
  name              = "/ecs/${var.project}-cube-otel"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_task_definition" "cube" {
  family                   = "${var.project}-cube"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name         = "cube"
      image        = var.image != "" ? var.image : "cubejs/cube:latest@sha256:3e3715ccad21ba7914203c5a0e1c011f829200738d77ed9cb4012f67caa05ee4" # custom model image, or the pinned public amd64 fallback
      essential    = true
      portMappings = [{ containerPort = 4000, protocol = "tcp" }]
      environment = [
        { name = "CUBEJS_DB_TYPE", value = "postgres" },
        { name = "CUBEJS_DB_HOST", value = var.aurora_endpoint },
        { name = "CUBEJS_DB_NAME", value = "uplift" },
        # Cube 1.x removed the redis driver (live log: "Only 'cubestore' or 'memory' are
        # supported ... passed: redis" -> /readyz 500). memory = correct for a single task;
        # move to a Cube Store sidecar when pre-aggregations land.
        { name = "CUBEJS_CACHE_AND_QUEUE_DRIVER", value = "memory" },
      ]
      secrets = [
        # crm_app DB credentials (non-owner role so Postgres RLS applies) + the JWT signing secret.
        { name = "CUBEJS_DB_USER", valueFrom = "${var.db_secret_arn}:username::" },
        { name = "CUBEJS_DB_PASS", valueFrom = "${var.db_secret_arn}:password::" },
        { name = "CUBEJS_API_SECRET", valueFrom = var.cube_api_secret_arn },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.cube.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "cube"
        }
      }
    },
    # ADOT collector sidecar (H10, offline IaC leg): receives OTLP spans from the cube container and
    # exports them to X-Ray. The task role needs xray:PutTraceSegments at apply.
    # NOTE: full end-to-end X-Ray trace verification needs apply (BLOCKED: needs Nick).
    {
      name      = "aws-otel-collector"
      image     = "public.ecr.aws/aws-observability/aws-otel-collector:latest"
      essential = false
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.cube_otel.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "otel"
        }
      }
    }
  ])
}

# Cloud Map registry: cube.uplift.local → task IPs. NOTE adding service_registries REPLACES the
# ECS service (brief outage; nothing consumes cube yet) — intended, one-time.
resource "aws_service_discovery_service" "cube" {
  count = var.namespace_id != "" ? 1 : 0
  name  = "cube"

  dns_config {
    namespace_id   = var.namespace_id
    routing_policy = "MULTIVALUE"
    dns_records {
      type = "A"
      ttl  = 10
    }
  }
}

resource "aws_ecs_service" "cube" {
  name            = "${var.project}-cube"
  cluster         = var.cluster_id
  task_definition = aws_ecs_task_definition.cube.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  dynamic "service_registries" {
    for_each = var.namespace_id != "" ? [1] : []
    content {
      registry_arn = aws_service_discovery_service.cube[0].arn
    }
  }

  network_configuration {
    subnets          = var.private_subnet_ids # private only — never public
    security_groups  = [var.security_group_id]
    assign_public_ip = false
  }
}

output "task_definition_arn" { value = aws_ecs_task_definition.cube.arn }
output "service_name" { value = aws_ecs_service.cube.name }
