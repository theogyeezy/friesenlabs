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

# Cortex persistent model registry (ml/registry.py registry_from_env — the worker's run_model /
# retrain tools read it via build_clients_from_env). Plain bucket name, no secret material;
# access rides the worker task role (IAM grant in modules/iam). "" = entry omitted -> no
# persistent registry, run_model degrades cleanly.
variable "cortex_s3_bucket" {
  type    = string
  default = ""
}
# Dev/tests filesystem fallback (CORTEX_S3_BUCKET wins in code); never set in prod.
variable "cortex_local_dir" {
  type    = string
  default = ""
}

# Sec (REQ-012 item 8a): ADOT sidecar image — default is the exact live string (zero-diff);
# pin a digest via tfvars (supply-chain: a mutable :latest is pulled at every task start).
variable "adot_image" {
  type        = string
  default     = "public.ecr.aws/aws-observability/aws-otel-collector:latest"
  description = "ADOT collector sidecar image. SECURITY: pin a digest in tfvars; the :latest default only preserves the current live task def (flip = roll tasks)."
}

# Sec (REQ-012 item 8b): immutable root FS on the worker container; /tmp rides a Fargate
# ephemeral volume (Python tempfile is the only runtime write path; PYTHONDONTWRITEBYTECODE
# is set in worker/Dockerfile). Default false = the live task def unchanged.
variable "readonly_root_filesystem" {
  type        = bool
  default     = false
  description = "Set readonlyRootFilesystem on the worker container and mount /tmp as an ephemeral volume. Default false preserves the live task def; flipping rolls the service."
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

  # /tmp ephemeral volume (Fargate; no tmpfs) — exists only when readonly_root_filesystem
  # flips, keeping the default task def byte-identical to the live one (REQ-012 item 8b).
  dynamic "volume" {
    for_each = var.readonly_root_filesystem ? [1] : []
    content {
      name = "tmp"
    }
  }

  container_definitions = jsonencode([
    merge({
      name      = "worker"
      image     = var.image != "" ? var.image : "${var.project}-worker:latest" # verify: real ECR URI
      essential = true
      environment = concat([
        # REQ-001: worker builds its tool clients from env in run().
        { name = "CLOUDWATCH_METRICS", value = "1" },
        { name = "CUBE_ENDPOINT", value = var.cube_endpoint },
        { name = "DB_HOST", value = var.db_host },
        { name = "DB_NAME", value = var.db_name },
        { name = "DB_PORT", value = var.db_port },
        ],
        # Cortex registry — injected only when set (deploy invariance with the "" defaults).
        var.cortex_s3_bucket != "" ? [{ name = "CORTEX_S3_BUCKET", value = var.cortex_s3_bucket }] : [],
        var.cortex_local_dir != "" ? [{ name = "CORTEX_LOCAL_DIR", value = var.cortex_local_dir }] : []
      )
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
      # Immutable root FS keys appear ONLY when flipped (default = live task def unchanged).
      # JSON round-trip keeps both conditional arms the same HCL type (string).
      jsondecode(var.readonly_root_filesystem ? jsonencode({
        readonlyRootFilesystem = true
        mountPoints            = [{ sourceVolume = "tmp", containerPath = "/tmp", readOnly = false }]
      }) : jsonencode({}))
    ),
    # ADOT collector sidecar (H10, offline IaC leg): receives OTLP spans from the worker container and
    # exports them to X-Ray. The task role needs xray:PutTraceSegments at apply.
    # NOTE: full end-to-end X-Ray trace verification needs apply (BLOCKED: needs Nick).
    {
      name      = "aws-otel-collector"
      image     = var.adot_image
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
