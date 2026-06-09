# Least-privilege security-group chain (Build Guide §Step 7):
# ALB (public 443) → API/Cube/Worker (8000) → Aurora (5432) + Redis (6379).
# Each tier only accepts from the tier in front of it.

variable "project" { type = string }
variable "vpc_id" { type = string }

resource "aws_security_group" "alb" {
  name        = "${var.project}-alb"
  description = "ALB, the only public tier"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.project}-alb" }
}

resource "aws_security_group" "api" {
  name        = "${var.project}-api"
  description = "api / cube / worker tasks"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.project}-api" }
}

resource "aws_security_group" "db" {
  name        = "${var.project}-db"
  description = "Aurora PostgreSQL"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.project}-db" }
}

resource "aws_security_group" "redis" {
  name        = "${var.project}-redis"
  description = "ElastiCache Redis"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.project}-redis" }
}

# --- Ingress chain ---
resource "aws_security_group_rule" "alb_https_in" {
  type              = "ingress"
  security_group_id = aws_security_group.alb.id
  protocol          = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_blocks       = ["0.0.0.0/0"]
  description       = "Public HTTPS"
}

resource "aws_security_group_rule" "api_from_alb" {
  type                     = "ingress"
  security_group_id        = aws_security_group.api.id
  protocol                 = "tcp"
  from_port                = 8000
  to_port                  = 8000
  source_security_group_id = aws_security_group.alb.id
  description              = "App port from ALB only"
}

resource "aws_security_group_rule" "db_from_api" {
  type                     = "ingress"
  security_group_id        = aws_security_group.db.id
  protocol                 = "tcp"
  from_port                = 5432
  to_port                  = 5432
  source_security_group_id = aws_security_group.api.id
  description              = "Postgres from app tier only"
}

resource "aws_security_group_rule" "redis_from_api" {
  type                     = "ingress"
  security_group_id        = aws_security_group.redis.id
  protocol                 = "tcp"
  from_port                = 6379
  to_port                  = 6379
  source_security_group_id = aws_security_group.api.id
  description              = "Redis from app tier only"
}

# --- Egress: allow all outbound (private tasks reach Bedrock/Anthropic via NAT) ---
resource "aws_security_group_rule" "alb_egress" {
  type              = "egress"
  security_group_id = aws_security_group.alb.id
  protocol          = "-1"
  from_port         = 0
  to_port           = 0
  cidr_blocks       = ["0.0.0.0/0"]
}

resource "aws_security_group_rule" "api_egress" {
  type              = "egress"
  security_group_id = aws_security_group.api.id
  protocol          = "-1"
  from_port         = 0
  to_port           = 0
  cidr_blocks       = ["0.0.0.0/0"]
}

output "sg_alb" { value = aws_security_group.alb.id }
output "sg_api" { value = aws_security_group.api.id }
output "sg_db" { value = aws_security_group.db.id }
output "sg_redis" { value = aws_security_group.redis.id }
