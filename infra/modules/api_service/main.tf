# FastAPI control plane on ECS Fargate (Build Guide Phase 9, Step 49).
# Private subnets, SG_API, 2 tasks behind the ALB target group; secrets from Secrets Manager.
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
variable "target_group_arn" { type = string }
variable "execution_role_arn" { type = string }
variable "task_role_arn" { type = string }
variable "image" {
  type    = string
  default = "" # set to the ECR api image (uplift-api) before apply
}
variable "db_secret_arn" { type = string }
variable "anthropic_api_key_secret_arn" { type = string }
variable "env_id_secret_arn" {
  type    = string
  default = ""
}
# REQ-001 part 3, SAFETY-GATED: injecting valueFrom on an EMPTY secret blocks task startup
# (ResourceInitializationError) and would take the live API down. Flip to true in tfvars ONLY
# after `uplift/anthropic-api-key` + `uplift/env-id` hold real values.
variable "api_anthropic_env" {
  type    = bool
  default = false
}

# REQ-003, same safety gate rationale: flip ONLY after stripe-webhook-secret + signup-token-secret
# + anthropic-admin-key hold values (the two platform secrets already do).
variable "api_signup_env" {
  type    = bool
  default = false
}
# The deliberate signup go-live act (REQ-003 step 0) — separate from the wiring flag above.
variable "signup_real_deps" {
  type    = bool
  default = false
}
variable "stripe_key_arn" {
  type    = string
  default = ""
}
variable "resend_key_arn" {
  type    = string
  default = ""
}
variable "stripe_webhook_secret_arn" {
  type    = string
  default = ""
}
variable "signup_token_secret_arn" {
  type    = string
  default = ""
}
variable "anthropic_admin_key_secret_arn" {
  type    = string
  default = ""
}
# REQ-005: the deliberate decouple switch — unset keeps the in-process provision path.
variable "provisioning_sfn_arn" {
  type    = string
  default = ""
}
variable "cube_endpoint" {
  type    = string
  default = "" # http://cube.uplift.local:4000 once Cloud Map is live
}
variable "cognito_user_pool_id" { type = string }
variable "cognito_client_id" { type = string }
variable "aurora_endpoint" {
  type    = string
  default = ""
}
variable "aurora_master_secret_arn" {
  type    = string
  default = ""
}
variable "desired_count" {
  type    = number
  default = 2
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${var.project}-api"
  retention_in_days = var.log_retention_days
}

# ADOT (AWS Distro for OpenTelemetry) collector sidecar log group — see the sidecar container below.
resource "aws_cloudwatch_log_group" "api_otel" {
  name              = "/ecs/${var.project}-api-otel"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${var.project}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  # Image is arm64 (Graviton, cheaper); built on Apple Silicon.
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([
    {
      name         = "api"
      image        = var.image != "" ? var.image : "${var.project}-api:latest"
      essential    = true
      portMappings = [{ containerPort = 8000, protocol = "tcp" }]
      environment = concat([
        { name = "AWS_REGION", value = var.region },
        { name = "COGNITO_USER_POOL_ID", value = var.cognito_user_pool_id },
        { name = "COGNITO_CLIENT_ID", value = var.cognito_client_id },
        { name = "DB_HOST", value = var.aurora_endpoint },
        { name = "DB_NAME", value = "uplift" },
        # For `python -m api.migrate` (one-off task): master + crm secret ARNs (read via boto3).
        { name = "AURORA_MASTER_SECRET_ARN", value = var.aurora_master_secret_arn },
        { name = "CRM_APP_SECRET_ARN", value = var.db_secret_arn },
        ],
        # REQ-003 step 0: the master switch appears ONLY at the deliberate go-live act —
        # without it build_signup_deps() boots all-stub even with every secret present.
        var.signup_real_deps ? [{ name = "SIGNUP_REAL_DEPS", value = "1" }] : [],
        var.provisioning_sfn_arn != "" ? [{ name = "PROVISIONING_SFN_ARN", value = var.provisioning_sfn_arn }] : [],
        var.cube_endpoint != "" ? [{ name = "CUBE_ENDPOINT", value = var.cube_endpoint }] : []
      )
      secrets = concat(
        [
          # crm_app (non-owner) DB credentials so RLS applies.
          { name = "DB_USER", valueFrom = "${var.db_secret_arn}:username::" },
          { name = "DB_PASS", valueFrom = "${var.db_secret_arn}:password::" },
        ],
        # REQ-001: org Anthropic key + env-id fallback — API task ONLY (never the worker), and
        # only once the secrets hold values (see var.api_anthropic_env). UPLIFT_ENV_KEY must
        # never appear here.
        var.api_anthropic_env ? [
          { name = "ANTHROPIC_API_KEY", valueFrom = var.anthropic_api_key_secret_arn },
          { name = "UPLIFT_ENV_ID", valueFrom = var.env_id_secret_arn },
        ] : [],
        # REQ-003: signup/provisioning plane — API task ONLY; never the worker.
        var.api_signup_env ? [
          { name = "STRIPE_API_KEY", valueFrom = var.stripe_key_arn },
          { name = "RESEND_API_KEY", valueFrom = var.resend_key_arn },
          { name = "STRIPE_WEBHOOK_SECRET", valueFrom = var.stripe_webhook_secret_arn },
          { name = "SIGNUP_TOKEN_SECRET_VALUE", valueFrom = var.signup_token_secret_arn },
          { name = "ANTHROPIC_ADMIN_KEY", valueFrom = var.anthropic_admin_key_secret_arn },
        ] : []
      )
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.api.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "api"
        }
      }
    },
    # ADOT collector sidecar (H10, offline IaC leg): receives OTLP spans from the api container and
    # exports them to X-Ray. The task role needs xray:PutTraceSegments at apply.
    # NOTE: full end-to-end X-Ray trace verification needs apply (BLOCKED: needs Nick).
    {
      name      = "aws-otel-collector"
      image     = "public.ecr.aws/aws-observability/aws-otel-collector:latest"
      essential = false
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.api_otel.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "otel"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "api" {
  name            = "${var.project}-api"
  cluster         = var.cluster_id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  # Break-glass debugging (TODO Sec/P3 212): live shell into a task via SSM — no inbound ports.
  enable_execute_command = true

  # First deploy: tasks need time to pull the image + pass health checks before the LB drains them.
  health_check_grace_period_seconds = 120

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.security_group_id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.target_group_arn
    container_name   = "api"
    container_port   = 8000
  }
}

output "service_name" { value = aws_ecs_service.api.name }
