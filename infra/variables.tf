variable "aws_region" {
  description = "AWS region. us-east-1 or us-west-2 (Titan embeddings + us-east-1 billing alarms)."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Project/name prefix for all resources."
  type        = string
  default     = "uplift"
}

variable "vpc_cidr" {
  description = "VPC CIDR. /16 split into two /20 public + two /20 private subnets."
  type        = string
  default     = "10.0.0.0/16"
}

variable "azs" {
  description = "Two availability zones (suffixes appended to region)."
  type        = list(string)
  default     = ["a", "b"]
}

variable "ecr_repos" {
  description = "ECR repositories to create (one image home per service)."
  type        = list(string)
  default     = ["api", "cube", "worker", "provisioning"] # provisioning = the REQ-005 Lambda image
}

variable "notify_email" {
  description = "Email for budget + alarm SNS notifications. Empty = no subscription wired (validate-clean)."
  type        = string
  default     = ""
}

variable "budgets_action_execution_role_arn" {
  description = "IAM role AWS Budgets assumes to apply the Deny-at-90% policy. Empty = budget action not created (BLOCKED: needs Nick)."
  type        = string
  default     = ""
}

variable "github_access_token" {
  description = "GitHub PAT (repo scope) to connect Amplify Hosting to the repo. Empty = no web hosting created."
  type        = string
  sensitive   = true
  default     = ""
}

variable "web_api_base_url" {
  description = "Deployed API base URL for the hosted web app. Empty = the site runs in mock mode."
  type        = string
  default     = ""
}

variable "api_image" {
  description = "ECR image URI for the API service (uplift-api). Empty = the local-tag placeholder."
  type        = string
  default     = ""
}

variable "api_desired_count" {
  description = "Number of API Fargate tasks."
  type        = number
  default     = 2
}

variable "web_callback_urls" {
  description = "OAuth redirect URIs for the SPA (Hosted UI -> app). Public URLs, not secrets."
  type        = list(string)
  default = [
    "https://main.d224yxym1ehrim.amplifyapp.com/auth/callback",
    "http://localhost:5173/auth/callback",
  ]
}

variable "web_logout_urls" {
  description = "Allowed sign-out redirect URIs for the SPA."
  type        = list(string)
  default = [
    "https://main.d224yxym1ehrim.amplifyapp.com/",
    "http://localhost:5173/",
  ]
}

variable "cube_endpoint" {
  type        = string
  default     = "" # REQ-001: set after the cube service is deployed (internal :4000 endpoint)
  description = "Internal Cube API endpoint for the worker's query tool client."
}

variable "api_anthropic_env" {
  type        = bool
  default     = false # REQ-001 safety gate: flip ONLY after uplift/anthropic-api-key + uplift/env-id hold values
  description = "Inject ANTHROPIC_API_KEY + UPLIFT_ENV_ID into the API task def (never the worker)."
}

variable "enable_origin_verify" {
  type        = bool
  default     = false # Sec/P0 phase 1: generate the X-Origin-Verify secret + stamp it at CloudFront
  description = "Create the uplift/origin-verify secret value and send the header from the API edge."
}

variable "alb_enforce_origin_verify" {
  type        = bool
  default     = false # Sec/P0 phase 2: flip ONLY after the distro shows Deployed with the header
  description = "ALB :80 default becomes 403; only requests with the matching X-Origin-Verify forward."
}

variable "api_signup_env" {
  type        = bool
  default     = false # REQ-003: flip ONLY after stripe-webhook + signup-token + admin-key secrets hold values
  description = "Inject the signup/provisioning secrets into the API task def (never the worker)."
}

variable "signup_real_deps" {
  type        = bool
  default     = false # REQ-003 step 0: the deliberate signup go-live act — see infra/REQUESTS.md
  description = "Set SIGNUP_REAL_DEPS=1 on the API task (build_signup_deps selects real adapters)."
}

variable "log_retention_days" {
  type        = number
  default     = 30 # the single retention knob for all uplift log groups (TODO 213)
  description = "CloudWatch retention for every uplift service log group."
}

variable "worker_deployed" {
  type        = bool
  default     = false # flip with the worker service deploy (gates the worker_absent alarm)
  description = "True once uplift-worker runs; enables the workers_polling-zero alarm."
}

variable "domain_name" {
  type        = string
  default     = "" # set in prod.auto.tfvars (friesenlabs.com)
  description = "Apex domain for the Route53 zone + ACM cert."
}

variable "dns_delegated" {
  type        = bool
  default     = false # flip AFTER Squarespace nameservers point at the Route53 zone
  description = "Registrar NS records point at the zone; unblocks cert validation waits."
}

variable "ingest_tenants" {
  type        = string
  default     = "" # REQ-004: comma-separated tenant ids; "" = run_sync no-ops
  description = "Tenants the nightly ingest run syncs."
}

variable "ingest_raw_bucket" {
  type        = string
  default     = "" # REQ-004: raw landing skipped (with a warning) while empty
  description = "S3 bucket for the raw ingest landing zone."
}

variable "ingest_schedule_enabled" {
  type        = bool
  default     = false # REQ-004 go-live act: flips the EventBridge rule ENABLED
  description = "Enable the nightly ingest schedule."
}

variable "bedrock_batch_role_arn" {
  type        = string
  default     = "" # REQ-004 (later): Bedrock batch-embed backfill ships as its own REQ
  description = "Reserved for the Titan batch-embed backfill role."
}

variable "ingest_batch_s3_bucket" {
  type        = string
  default     = "" # REQ-004 (later): batch-embed JSONL I/O bucket
  description = "Reserved for the Titan batch-embed backfill bucket."
}

variable "provisioning_lambda_image" {
  type        = string
  default     = "" # REQ-005: set to the pushed uplift-provisioning image URI to create the Lambda
  description = "ECR image URI for the provisioning Lambda (empty = function not created)."
}

variable "api_provisioning_sfn" {
  type        = bool
  default     = false # REQ-005 decouple switch: injects PROVISIONING_SFN_ARN into the API task
  description = "Expose the provisioning state-machine ARN to the API task (SfnProvisioningTrigger)."
}

variable "resend_from_email" {
  type        = string
  default     = ""
  description = "From-address for signup verification emails (provisioning Lambda env)."
}

variable "signup_verify_url_base" {
  type        = string
  default     = ""
  description = "Base URL for signup verification links (provisioning Lambda env)."
}

variable "provisioning_admin_key_available" {
  type        = bool
  default     = false # flip once uplift/anthropic-admin-key holds a value (VERIFY endpoints first)
  description = "Inject ANTHROPIC_ADMIN_KEY into the provisioning Lambda env."
}

variable "cube_image" {
  type        = string
  default     = "" # the custom uplift-cube image (semantic/ model + security context baked in)
  description = "ECR image URI for the cube service; empty = pinned public cubejs/cube."
}
