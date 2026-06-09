# Shared ECS cluster (Build Guide: Cube runs as a Fargate service in the same cluster as api/worker).
# The cluster lives here so Phase 3 (Cube) and Phase 9 (api/worker) share it. AUTHORED + VALIDATED ONLY.

variable "project" { type = string }

resource "aws_ecs_cluster" "this" {
  name = "${var.project}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]
}

output "cluster_id" { value = aws_ecs_cluster.this.id }
output "cluster_name" { value = aws_ecs_cluster.this.name }
