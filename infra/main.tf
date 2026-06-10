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
  # TODO Sec/P2 206: the api task role's runtime reads are exactly migrate's two secrets.
  api_task_secret_arns = compact([
    module.secrets.crm_app_db_secret_arn,
    module.data.master_user_secret_arn,
  ])
  provisioning_sfn_arn = module.provisioning.state_machine_arn
  # REQ-003: exact platform-secret ARNs for the execution role (listed, not a wildcard widen).
  extra_execution_secret_arns = [
    data.aws_secretsmanager_secret.platform_stripe.arn,
    data.aws_secretsmanager_secret.platform_resend.arn,
    data.aws_secretsmanager_secret.platform_posthog.arn, # REQ-006
  ]
  cognito_user_pool_arn = local.cognito_pool_arn
}

# REQ-003: org-shared platform secrets created out-of-band (Lane Nick console/CLI) — referenced,
# never managed here.
data "aws_secretsmanager_secret" "platform_stripe" {
  name = "friesenlabs/platform/shared/stripe-secret-key"
}
data "aws_secretsmanager_secret" "platform_resend" {
  name = "friesenlabs/platform/shared/resend-api-key"
}
data "aws_secretsmanager_secret" "platform_posthog" {
  name = "friesenlabs/platform/shared/posthog-project-key"
}

module "secrets" {
  source                     = "./modules/secrets"
  project                    = var.project
  enable_origin_verify       = var.enable_origin_verify
  enable_crm_db_rotation     = var.enable_crm_db_rotation
  rotation_subnet_ids        = module.vpc.private_subnet_ids
  rotation_security_group_id = module.security.sg_api
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
  log_retention_days  = var.log_retention_days
  image               = var.cube_image
  namespace_id        = module.vpc.service_discovery_namespace_id
}

# --- Phase 9: auth + ALB + api service ---
module "auth" {
  source        = "./modules/auth"
  project       = var.project
  callback_urls = var.web_callback_urls
  logout_urls   = var.web_logout_urls
}

locals {
  cognito_pool_arn = module.auth.user_pool_arn
}

module "alb" {
  source                = "./modules/alb"
  project               = var.project
  vpc_id                = module.vpc.vpc_id
  public_subnet_ids     = module.vpc.public_subnet_ids
  alb_security_group_id = module.security.sg_alb
  origin_verify_secret  = module.secrets.origin_verify_value
  enforce_origin_verify = var.alb_enforce_origin_verify
  # The VALIDATED arn (empty until delegated + ISSUED) — issuance is a hard ordering gate.
  certificate_arn     = var.alb_tls ? try(module.dns[0].validated_certificate_arn, "") : ""
  retire_http_forward = var.alb_retire_http_forward
}

module "api_service" {
  source                         = "./modules/api_service"
  project                        = var.project
  region                         = var.aws_region
  cluster_id                     = module.ecs.cluster_id
  private_subnet_ids             = module.vpc.private_subnet_ids
  security_group_id              = module.security.sg_api
  target_group_arn               = module.alb.target_group_arn
  execution_role_arn             = module.iam.ecs_task_execution_role_arn
  task_role_arn                  = module.iam.task_role_arns["api"]
  db_secret_arn                  = module.secrets.crm_app_db_secret_arn
  anthropic_api_key_secret_arn   = module.secrets.anthropic_api_key_secret_arn
  env_id_secret_arn              = module.secrets.env_id_secret_arn
  api_anthropic_env              = var.api_anthropic_env
  cube_api_secret_arn            = var.api_cube_env ? module.secrets.cube_api_secret_arn : ""
  api_signup_env                 = var.api_signup_env
  signup_real_deps               = var.signup_real_deps
  stripe_key_arn                 = data.aws_secretsmanager_secret.platform_stripe.arn
  resend_key_arn                 = data.aws_secretsmanager_secret.platform_resend.arn
  stripe_webhook_secret_arn      = module.secrets.stripe_webhook_secret_arn
  signup_token_secret_arn        = module.secrets.signup_token_secret_arn
  anthropic_admin_key_secret_arn = module.secrets.anthropic_admin_key_secret_arn
  provisioning_sfn_arn           = var.api_provisioning_sfn ? module.provisioning.state_machine_arn : ""
  cube_endpoint                  = var.cube_endpoint
  posthog_key_arn                = data.aws_secretsmanager_secret.platform_posthog.arn
  posthog_host                   = var.posthog_host
  integrations_real              = var.api_integrations_real
  cognito_user_pool_id           = module.auth.user_pool_id
  cognito_client_id              = module.auth.user_pool_client_id
  image                          = var.api_image
  aurora_endpoint                = module.data.cluster_endpoint
  aurora_master_secret_arn       = module.data.master_user_secret_arn
  desired_count                  = var.api_desired_count
  log_retention_days             = var.log_retention_days
}

# REQ-004: ingestion scheduler — one-off Fargate task on an EventBridge rule (DISABLED by
# default; var.ingest_schedule_enabled is the go-live act).
module "ingest" {
  source             = "./modules/ingest"
  project            = var.project
  region             = var.aws_region
  cluster_arn        = module.ecs.cluster_id
  private_subnet_ids = module.vpc.private_subnet_ids
  security_group_id  = module.security.sg_api
  execution_role_arn = module.iam.ecs_task_execution_role_arn
  image              = var.api_image
  db_secret_arn      = module.secrets.crm_app_db_secret_arn
  db_host            = module.data.cluster_endpoint
  ingest_tenants     = var.ingest_tenants
  ingest_raw_bucket  = var.ingest_raw_bucket
  schedule_enabled   = var.ingest_schedule_enabled
  log_retention_days = var.log_retention_days
}

# --- Phase 8: Cortex scheduled retrain ---
module "cortex" {
  source  = "./modules/cortex"
  project = var.project
}

# --- Phase 10: provisioning orchestration (Step Functions) ---
# REQ-005: the Lambda the SFN invokes (count-gated on the pushed image).
module "provisioning_lambda" {
  source                = "./modules/provisioning_lambda"
  project               = var.project
  image_uri             = var.provisioning_lambda_image
  private_subnet_ids    = module.vpc.private_subnet_ids
  security_group_id     = module.security.sg_api
  db_secret_arn         = module.secrets.crm_app_db_secret_arn
  db_host               = module.data.cluster_endpoint
  cognito_user_pool_id  = module.auth.user_pool_id
  cognito_user_pool_arn = local.cognito_pool_arn
  resend_key_secret_id  = data.aws_secretsmanager_secret.platform_resend.id
  resend_from_email     = var.resend_from_email
  verify_url_base       = var.signup_verify_url_base
  admin_key_secret_id   = module.secrets.anthropic_admin_key_secret_arn
  admin_key_available   = var.provisioning_admin_key_available
  posthog_key_secret_id = data.aws_secretsmanager_secret.platform_posthog.id
  posthog_host          = var.posthog_host
}

module "provisioning" {
  provisioning_lambda_arn = module.provisioning_lambda.function_arn
  source                  = "./modules/provisioning"
  project                 = var.project
}

# --- Web hosting: Amplify (Vite SPA). Only created when a GitHub token is supplied. ---
# CloudFront HTTPS edge in front of the API ALB (so Amplify can proxy /api/* to a valid HTTPS target).
module "api_cdn" {
  source               = "./modules/api_cdn"
  api_origin_domain    = var.api_cdn_origin_domain
  project              = var.project
  alb_dns              = module.alb.alb_dns_name
  origin_verify_secret = module.secrets.origin_verify_value
}

module "web_hosting" {
  count               = var.github_access_token != "" ? 1 : 0
  source              = "./modules/web_hosting"
  project             = var.project
  github_access_token = var.github_access_token
  custom_domain       = var.web_custom_domain
  zone_id             = try(module.dns[0].zone_id, "")
  api_base_url        = var.web_api_base_url
  api_cdn_domain      = module.api_cdn.domain
  cognito_domain      = module.auth.hosted_ui_domain
  cognito_client_id   = module.auth.user_pool_client_id
  cognito_region      = var.aws_region
}

# --- Phase 11: cost guardrails + observability ---
module "guardrails" {
  source       = "./modules/guardrails"
  project      = var.project
  notify_email = var.notify_email
  # Empty default leaves the Deny-at-90% budget action un-created (validate-clean); set at apply.
  budgets_action_execution_role_arn = var.budgets_action_execution_role_arn
  alarms_topic_arn                  = module.observability.alarms_topic_arn
}

module "observability" {
  source          = "./modules/observability"
  project         = var.project
  notify_email    = var.notify_email
  alb_arn_suffix  = module.alb.arn_suffix
  worker_deployed = var.worker_deployed
}

# Real domain (friesenlabs.com on Squarespace registrar): zone + cert; ALB TLS cutover follows
# once var.dns_delegated is flipped and the cert is ISSUED.
module "dns" {
  count        = var.domain_name != "" ? 1 : 0
  source       = "./modules/dns"
  domain_name  = var.domain_name
  delegated    = var.dns_delegated
  alb_dns_name = var.alb_tls ? module.alb.alb_dns_name : ""
  alb_zone_id  = var.alb_tls ? module.alb.alb_zone_id : ""
}

# --- Phase 4: self-hosted tool-execution worker ---
module "worker" {
  count              = var.worker_deployed ? 1 : 0
  source             = "./modules/worker"
  project            = var.project
  region             = var.aws_region
  cluster_id         = module.ecs.cluster_id
  private_subnet_ids = module.vpc.private_subnet_ids
  security_group_id  = module.security.sg_api
  execution_role_arn = module.iam.ecs_task_execution_role_arn
  task_role_arn      = module.iam.task_role_arns["worker"]
  env_key_secret_arn = module.secrets.env_key_secret_arn
  env_id_secret_arn  = module.secrets.env_id_secret_arn
  db_secret_arn      = module.secrets.crm_app_db_secret_arn
  db_host            = module.data.cluster_endpoint
  cube_endpoint      = var.cube_endpoint
  log_retention_days = var.log_retention_days
  image              = var.worker_image
}
