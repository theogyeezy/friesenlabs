terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.49" # bounded: lock resolved 6.49.x; prevents init -upgrade jumping majors
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Remote state in S3 with native S3 locking (use_lockfile, no DynamoDB needed). Partial config:
  # the bucket/key/region live in infra/backend.hcl (gitignored — keeps the account-id bucket name
  # out of this public repo). Init with: terraform init -backend-config=backend.hcl
  # CI uses `terraform init -backend=false` so it skips the backend entirely.
  backend "s3" {}
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
    }
  }
}
