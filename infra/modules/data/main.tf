# Aurora PostgreSQL Serverless v2 + pgvector (Build Guide Phase 1, Step 9).
# AUTHORED + VALIDATED ONLY — never applied (cost/irreversible).

variable "project" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "db_security_group_id" { type = string }

# --- Aurora CMK (Sec, REQ-012 item 9). Today the cluster encrypts with the default aws/rds key
# and Performance Insights with the default aws/pi key — a CMK gives key-rotation control, an
# auditable key policy, and revocation. ---
variable "aurora_kms_key_arn" {
  type        = string
  default     = ""
  description = <<-EOT
    ⚠️⚠️ DANGER — READ BEFORE SETTING ⚠️⚠️ KMS CMK ARN for Aurora storage + Performance
    Insights encryption. Changing kms_key_id on an EXISTING cluster FORCES FULL CLUSTER
    REPLACEMENT (terraform destroy+create => the live tenant database is DESTROYED unless the
    snapshot-restore migration in infra/REQUESTS.md REQ-012 item 9 is followed: snapshot →
    copy-snapshot with the CMK → restore → cut over → only then retarget state). NEVER set
    this and apply blind. Default "" = the live default-key state, zero diff. The PI key has
    the same hazard at the instance level.
  EOT
}

variable "create_aurora_cmk" {
  type        = bool
  default     = false
  description = <<-EOT
    Create a rotating customer-managed KMS key (alias/<project>-aurora) and use it as the
    cluster + Performance Insights key. SAME REPLACEMENT HAZARD as aurora_kms_key_arn — the
    key creation itself is additive and safe, but WIRING it into the existing cluster
    replaces the cluster. Follow the REQ-012 item 9 snapshot-restore runbook; consider a
    first apply that only creates the key (-target the kms resources) so the key exists
    before the migration window.
  EOT
}

resource "aws_kms_key" "aurora" {
  count                   = var.create_aurora_cmk ? 1 : 0
  description             = "${var.project} Aurora storage + Performance Insights CMK"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

resource "aws_kms_alias" "aurora" {
  count         = var.create_aurora_cmk ? 1 : 0
  name          = "alias/${var.project}-aurora"
  target_key_id = aws_kms_key.aurora[0].key_id
}

locals {
  # Precedence: the created CMK wins; else the supplied ARN; else "" = default AWS keys (live).
  aurora_kms_arn = var.create_aurora_cmk ? aws_kms_key.aurora[0].arn : var.aurora_kms_key_arn
}

resource "aws_db_subnet_group" "this" {
  name       = "${var.project}-db-subnets"
  subnet_ids = var.private_subnet_ids
  tags       = { Name = "${var.project}-db-subnets" }
}

resource "aws_rds_cluster" "this" {
  cluster_identifier          = "${var.project}-aurora"
  engine                      = "aurora-postgresql"
  engine_mode                 = "provisioned" # required for Serverless v2
  engine_version              = "16.11"       # ships pgvector 0.8.0; AWS auto-minor-upgrades — see lifecycle
  database_name               = "uplift"
  master_username             = "crmadmin"
  manage_master_user_password = true # master cred -> Secrets Manager (never echoed)

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [var.db_security_group_id]
  storage_encrypted      = true
  # ⚠️ REPLACEMENT: null (live default aws/rds key) until the REQ-012 item 9 snapshot-restore
  # migration window — see the screaming variable descriptions above.
  kms_key_id                      = local.aurora_kms_arn != "" ? local.aurora_kms_arn : null
  enabled_cloudwatch_logs_exports = ["postgresql"]

  serverlessv2_scaling_configuration {
    min_capacity = 1 # NOT 0.5 — a tiny floor starves HNSW index builds
    max_capacity = 16
  }

  # Durability: holds real tenant data, so protect against accidental loss.
  backup_retention_period   = 7
  deletion_protection       = true
  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.project}-aurora-final"
  copy_tags_to_snapshot     = true

  # AWS auto-minor-upgrades the cluster out-of-band (caught live: 16.8 -> 16.11); without this,
  # every plan after an upgrade proposes a dangerous DOWNGRADE back to the pinned minor.
  lifecycle {
    ignore_changes = [engine_version]
  }
}

resource "aws_rds_cluster_instance" "this" {
  identifier         = "${var.project}-aurora-1"
  cluster_identifier = aws_rds_cluster.this.id
  engine             = aws_rds_cluster.this.engine
  engine_version     = aws_rds_cluster.this.engine_version
  instance_class     = "db.serverless"

  # Query-level visibility into the only datastore holding tenant data (7-day
  # retention = the free tier).
  performance_insights_enabled = true
  # ⚠️ The PI KMS key cannot be changed while PI is enabled on a live instance (forces
  # disable/re-enable or instance replacement) — wire it only inside the REQ-012 item 9
  # migration window, together with the cluster key.
  performance_insights_kms_key_id = local.aurora_kms_arn != "" ? local.aurora_kms_arn : null
}

output "cluster_endpoint" { value = aws_rds_cluster.this.endpoint }
output "reader_endpoint" { value = aws_rds_cluster.this.reader_endpoint }
output "master_user_secret_arn" {
  value = try(aws_rds_cluster.this.master_user_secret[0].secret_arn, null)
}
