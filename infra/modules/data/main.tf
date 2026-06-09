# Aurora PostgreSQL Serverless v2 + pgvector (Build Guide Phase 1, Step 9).
# AUTHORED + VALIDATED ONLY — never applied (cost/irreversible).

variable "project" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "db_security_group_id" { type = string }

resource "aws_db_subnet_group" "this" {
  name       = "${var.project}-db-subnets"
  subnet_ids = var.private_subnet_ids
  tags       = { Name = "${var.project}-db-subnets" }
}

resource "aws_rds_cluster" "this" {
  cluster_identifier          = "${var.project}-aurora"
  engine                      = "aurora-postgresql"
  engine_mode                 = "provisioned" # required for Serverless v2
  engine_version              = "16.8"        # ships pgvector 0.8.0
  database_name               = "uplift"
  master_username             = "crmadmin"
  manage_master_user_password = true # master cred -> Secrets Manager (never echoed)

  db_subnet_group_name            = aws_db_subnet_group.this.name
  vpc_security_group_ids          = [var.db_security_group_id]
  storage_encrypted               = true
  enabled_cloudwatch_logs_exports = ["postgresql"]

  serverlessv2_scaling_configuration {
    min_capacity = 1 # NOT 0.5 — a tiny floor starves HNSW index builds
    max_capacity = 16
  }

  skip_final_snapshot = true
}

resource "aws_rds_cluster_instance" "this" {
  identifier         = "${var.project}-aurora-1"
  cluster_identifier = aws_rds_cluster.this.id
  engine             = aws_rds_cluster.this.engine
  engine_version     = aws_rds_cluster.this.engine_version
  instance_class     = "db.serverless"
}

output "cluster_endpoint" { value = aws_rds_cluster.this.endpoint }
output "reader_endpoint" { value = aws_rds_cluster.this.reader_endpoint }
output "master_user_secret_arn" {
  value = try(aws_rds_cluster.this.master_user_secret[0].secret_arn, null)
}
