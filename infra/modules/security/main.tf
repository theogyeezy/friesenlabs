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

# Until a real domain + ACM cert exist, the ALB serves HTTP:80 to CloudFront ONLY (the CF edge
# terminates TLS with its trusted *.cloudfront.net cert; the Amplify site proxies /api/* through it).
data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_security_group_rule" "alb_http_from_cloudfront" {
  type              = "ingress"
  security_group_id = aws_security_group.alb.id
  protocol          = "tcp"
  from_port         = 80
  to_port           = 80
  prefix_list_ids   = [data.aws_ec2_managed_prefix_list.cloudfront.id]
  description       = "HTTP from CloudFront origins only (TLS terminated at the CF edge)"
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

# --- Cube SG split (Sec, REQ-012 item 7) ---
# Cube used to ride sg_api with a self-referencing :4000 rule — which meant EVERY task in
# sg_api (api, worker, ingest, scheduled jobs, provisioning/rotation Lambdas) both REACHED
# cube and ACCEPTED :4000 from its peers. Cube now has its own SG that admits :4000 from the
# app-tier SG only, and the self rule is gone (no app-tier task accepts :4000 any more).
# WORKER DECISION (documented per the audit): the worker LEGITIMATELY queries cube — its task
# env carries CUBE_ENDPOINT and worker/worker.py builds a real CubeClient via
# agents.tools.cube_client.cube_client_from_env for the query_cube tool — so worker→cube reach
# is intentional. Today the worker shares sg_api, so the cube_from_api rule covers it; when
# var.create_worker_sg flips (below), the EXPLICIT cube_from_worker rule takes over.
# NOTE (apply impact): moving the cube service to this SG rolls cube tasks — see REQUESTS.md.
resource "aws_security_group" "cube" {
  name        = "${var.project}-cube"
  description = "cube semantic-layer tasks (ingress :4000 from the app tier only)"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.project}-cube" }
}

resource "aws_security_group_rule" "cube_from_api" {
  type                     = "ingress"
  security_group_id        = aws_security_group.cube.id
  protocol                 = "tcp"
  from_port                = 4000
  to_port                  = 4000
  source_security_group_id = aws_security_group.api.id
  description              = "cube :4000 from the app tier (api; worker rides sg_api until create_worker_sg flips)"
}

# Cube needs Aurora (5432, via sg_db's existing pairing — added below) + egress to reach it.
resource "aws_security_group_rule" "cube_egress" {
  type              = "egress"
  security_group_id = aws_security_group.cube.id
  protocol          = "-1"
  from_port         = 0
  to_port           = 0
  cidr_blocks       = ["0.0.0.0/0"]
}

resource "aws_security_group_rule" "db_from_cube" {
  type                     = "ingress"
  security_group_id        = aws_security_group.db.id
  protocol                 = "tcp"
  from_port                = 5432
  to_port                  = 5432
  source_security_group_id = aws_security_group.cube.id
  description              = "Postgres from the cube tier"
}

# --- Gated per-service SG split for the cube free-riders (REQ-012 item 7, flip vars) ---
# The point of the cube split is that worker/provisioning-Lambda stop reaching cube "for free"
# by virtue of sharing sg_api. Severing them requires moving each onto its OWN SG — which
# rolls worker tasks / updates the Lambda's VPC config — so each move is gated behind a
# variable whose default (false) preserves the CURRENT LIVE wiring exactly.
variable "create_worker_sg" {
  type        = bool
  default     = false
  description = "Create a dedicated worker SG (no inbound; explicit worker->cube :4000 + worker->db :5432). Flip together with worker_dedicated_sg at the root — rolls worker tasks."
}

resource "aws_security_group" "worker" {
  count       = var.create_worker_sg ? 1 : 0
  name        = "${var.project}-worker"
  description = "worker tasks (no inbound; explicit egress pairings only)"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.project}-worker" }
}

resource "aws_security_group_rule" "worker_egress" {
  count             = var.create_worker_sg ? 1 : 0
  type              = "egress"
  security_group_id = aws_security_group.worker[0].id
  protocol          = "-1"
  from_port         = 0
  to_port           = 0
  cidr_blocks       = ["0.0.0.0/0"]
}

# The EXPLICIT worker→cube rule (worker legitimately queries cube — see the decision note above).
resource "aws_security_group_rule" "cube_from_worker" {
  count                    = var.create_worker_sg ? 1 : 0
  type                     = "ingress"
  security_group_id        = aws_security_group.cube.id
  protocol                 = "tcp"
  from_port                = 4000
  to_port                  = 4000
  source_security_group_id = aws_security_group.worker[0].id
  description              = "cube :4000 from the worker (query_cube tool: explicit, not for-free)"
}

resource "aws_security_group_rule" "db_from_worker" {
  count                    = var.create_worker_sg ? 1 : 0
  type                     = "ingress"
  security_group_id        = aws_security_group.db.id
  protocol                 = "tcp"
  from_port                = 5432
  to_port                  = 5432
  source_security_group_id = aws_security_group.worker[0].id
  description              = "Postgres from the worker tier"
}

variable "create_lambda_sg" {
  type        = bool
  default     = false
  description = "Create a dedicated provisioning-Lambda SG (db :5432 only — NO cube reach: the provisioning handler touches DB/Cognito/Resend/Anthropic, never cube). Flip together with provisioning_lambda_dedicated_sg at the root."
}

resource "aws_security_group" "provisioning_lambda" {
  count       = var.create_lambda_sg ? 1 : 0
  name        = "${var.project}-provisioning-lambda"
  description = "provisioning Lambda (no inbound; db egress pairing only; deliberately NO cube reach)"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.project}-provisioning-lambda" }
}

resource "aws_security_group_rule" "lambda_egress" {
  count             = var.create_lambda_sg ? 1 : 0
  type              = "egress"
  security_group_id = aws_security_group.provisioning_lambda[0].id
  protocol          = "-1"
  from_port         = 0
  to_port           = 0
  cidr_blocks       = ["0.0.0.0/0"]
}

resource "aws_security_group_rule" "db_from_lambda" {
  count                    = var.create_lambda_sg ? 1 : 0
  type                     = "ingress"
  security_group_id        = aws_security_group.db.id
  protocol                 = "tcp"
  from_port                = 5432
  to_port                  = 5432
  source_security_group_id = aws_security_group.provisioning_lambda[0].id
  description              = "Postgres from the provisioning Lambda"
}

output "sg_api" { value = aws_security_group.api.id }
output "sg_cube" { value = aws_security_group.cube.id }
output "sg_worker" { value = var.create_worker_sg ? aws_security_group.worker[0].id : "" }
output "sg_lambda" { value = var.create_lambda_sg ? aws_security_group.provisioning_lambda[0].id : "" }
output "sg_db" { value = aws_security_group.db.id }
output "sg_redis" { value = aws_security_group.redis.id }
