# Cube semantic layer on ECS Fargate (Build Guide Phase 3, Step 19).
# Runs the cubejs/cube image pointed at Aurora over the private SG, Redis as cache/queue driver.
# Exposed ONLY internally (api + worker call it; never public). AUTHORED + VALIDATED ONLY.

variable "project" { type = string }
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

resource "aws_cloudwatch_log_group" "cube" {
  name              = "/ecs/${var.project}-cube"
  retention_in_days = 30
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
      image        = "cubejs/cube:latest" # pin a digest before apply (verify)
      essential    = true
      portMappings = [{ containerPort = 4000, protocol = "tcp" }]
      environment = [
        { name = "CUBEJS_DB_TYPE", value = "postgres" },
        { name = "CUBEJS_DB_HOST", value = var.aurora_endpoint },
        { name = "CUBEJS_DB_NAME", value = "uplift" },
        { name = "CUBEJS_CACHE_AND_QUEUE_DRIVER", value = "redis" },
        { name = "CUBEJS_REDIS_URL", value = "redis://${var.redis_endpoint}:6379" },
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
    }
  ])
}

resource "aws_ecs_service" "cube" {
  name            = "${var.project}-cube"
  cluster         = var.cluster_id
  task_definition = aws_ecs_task_definition.cube.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids # private only — never public
    security_groups  = [var.security_group_id]
    assign_public_ip = false
  }
}

output "task_definition_arn" { value = aws_ecs_task_definition.cube.arn }
output "service_name" { value = aws_ecs_service.cube.name }
