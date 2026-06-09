# Phase 0 — AWS Foundation.
# A lean, secure base: one VPC across two AZs, a least-privilege security-group chain,
# IAM task roles, secrets, and ECR. Everything in later phases lands inside this.
#
# AUTHORED + VALIDATED ONLY — never `terraform apply`-ed in this build (cost/irreversible).

module "baseline" {
  source  = "./modules/baseline"
  project = var.project
}

module "vpc" {
  source   = "./modules/vpc"
  project  = var.project
  vpc_cidr = var.vpc_cidr
  azs      = var.azs
  region   = var.aws_region
}

module "security" {
  source  = "./modules/security"
  project = var.project
  vpc_id  = module.vpc.vpc_id
}

module "iam" {
  source  = "./modules/iam"
  project = var.project
}

module "secrets" {
  source  = "./modules/secrets"
  project = var.project
}

module "ecr" {
  source  = "./modules/ecr"
  project = var.project
  repos   = var.ecr_repos
}

# --- Phase 1: data plane ---
module "data" {
  source               = "./modules/data"
  project              = var.project
  private_subnet_ids   = module.vpc.private_subnet_ids
  db_security_group_id = module.security.sg_db
}

module "redis" {
  source                  = "./modules/redis"
  project                 = var.project
  private_subnet_ids      = module.vpc.private_subnet_ids
  redis_security_group_id = module.security.sg_redis
}

module "s3" {
  source  = "./modules/s3"
  project = var.project
}

# --- Phase 3: semantic layer (Cube on Fargate) ---
module "ecs" {
  source  = "./modules/ecs"
  project = var.project
}

module "cube" {
  source              = "./modules/cube"
  project             = var.project
  region              = var.aws_region
  cluster_id          = module.ecs.cluster_id
  private_subnet_ids  = module.vpc.private_subnet_ids
  security_group_id   = module.security.sg_api
  execution_role_arn  = module.iam.ecs_task_execution_role_arn
  task_role_arn       = module.iam.task_role_arns["cube"]
  aurora_endpoint     = module.data.cluster_endpoint
  redis_endpoint      = module.redis.primary_endpoint
  db_secret_arn       = module.secrets.crm_app_db_secret_arn
  cube_api_secret_arn = module.secrets.cube_api_secret_arn
}
