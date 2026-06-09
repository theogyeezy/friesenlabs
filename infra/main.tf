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
