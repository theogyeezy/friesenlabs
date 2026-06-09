# ElastiCache (Valkey) for sessions + hot-query cache (Build Guide Phase 1, Step 13).
# Namespace every key t:<tenant_id>:... so the shared cache can't leak across tenants.
# AUTHORED + VALIDATED ONLY — never applied.

variable "project" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "redis_security_group_id" { type = string }

resource "aws_elasticache_subnet_group" "this" {
  name       = "${var.project}-redis-subnets"
  subnet_ids = var.private_subnet_ids
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id = "${var.project}-redis"
  description          = "Uplift sessions + hot-query cache"
  engine               = "valkey"
  engine_version       = "8.0"
  node_type            = "cache.t4g.small"
  num_cache_clusters   = 1
  port                 = 6379

  subnet_group_name          = aws_elasticache_subnet_group.this.name
  security_group_ids         = [var.redis_security_group_id]
  transit_encryption_enabled = true
  at_rest_encryption_enabled = true
}

output "primary_endpoint" { value = aws_elasticache_replication_group.this.primary_endpoint_address }
