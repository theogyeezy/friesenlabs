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

# --- Phase 9: auth + ALB + api service ---
module "auth" {
  source  = "./modules/auth"
  project = var.project
}

module "alb" {
  source                = "./modules/alb"
  project               = var.project
  vpc_id                = module.vpc.vpc_id
  public_subnet_ids     = module.vpc.public_subnet_ids
  alb_security_group_id = module.security.sg_alb
}

module "api_service" {
  source                       = "./modules/api_service"
  project                      = var.project
  region                       = var.aws_region
  cluster_id                   = module.ecs.cluster_id
  private_subnet_ids           = module.vpc.private_subnet_ids
  security_group_id            = module.security.sg_api
  target_group_arn             = module.alb.target_group_arn
  execution_role_arn           = module.iam.ecs_task_execution_role_arn
  task_role_arn                = module.iam.task_role_arns["api"]
  db_secret_arn                = module.secrets.crm_app_db_secret_arn
  anthropic_api_key_secret_arn = module.secrets.anthropic_api_key_secret_arn
  cognito_user_pool_id         = module.auth.user_pool_id
  cognito_client_id            = module.auth.user_pool_client_id
}

# --- Phase 8: Cortex scheduled retrain ---
module "cortex" {
  source  = "./modules/cortex"
  project = var.project
}

# --- Phase 10: provisioning orchestration (Step Functions) ---
module "provisioning" {
  source  = "./modules/provisioning"
  project = var.project
}

# --- Web hosting: Amplify (Vite SPA). Only created when a GitHub token is supplied. ---
module "web_hosting" {
  count               = var.github_access_token != "" ? 1 : 0
  source              = "./modules/web_hosting"
  project             = var.project
  github_access_token = var.github_access_token
  api_base_url        = var.web_api_base_url
}

# --- Phase 11: cost guardrails + observability ---
module "guardrails" {
  source       = "./modules/guardrails"
  project      = var.project
  notify_email = var.notify_email
  # Empty default leaves the Deny-at-90% budget action un-created (validate-clean); set at apply.
  budgets_action_execution_role_arn = var.budgets_action_execution_role_arn
}

module "observability" {
  source         = "./modules/observability"
  project        = var.project
  notify_email   = var.notify_email
  alb_arn_suffix = module.alb.arn_suffix
}

# --- Phase 4: self-hosted tool-execution worker ---
module "worker" {
  source             = "./modules/worker"
  project            = var.project
  region             = var.aws_region
  cluster_id         = module.ecs.cluster_id
  private_subnet_ids = module.vpc.private_subnet_ids
  security_group_id  = module.security.sg_api
  execution_role_arn = module.iam.ecs_task_execution_role_arn
  task_role_arn      = module.iam.task_role_arns["worker"]
  env_key_secret_arn = module.secrets.env_key_secret_arn
}
