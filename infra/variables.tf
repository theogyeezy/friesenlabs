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

variable "signup_require_phone" {
  type        = bool
  default     = true # phone (SMS OTP) verification required before pay
  description = <<-EOT
    Phone (SMS OTP) verification feature flag. Default true = required. Set FALSE to launch on
    EMAIL-ONLY verification (skip the phone step, mint no OTP) while SMS account-level approval is
    pending (SNS sandbox exit / origination identity). Flip back to true once SMS delivery works.
  EOT
}

variable "allow_real_sends" {
  type        = bool
  default     = false # DRAFT-GATE (CLAUDE.md #2): senders log + drop until this is flipped.
  description = <<-EOT
    Set ALLOW_REAL_SENDS=true on the API task + provisioning Lambda so the email/SMS senders
    actually DELIVER verification mail + phone OTPs (default false = log-and-drop). The deliberate,
    separate go-live act — flip ONLY after: (1) SNS SMS is out of the sandbox with a spend limit +
    an origination identity, and (2) the Resend sending domain is verified. Then a paid signup can
    complete email + phone verification end to end.
  EOT
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

variable "alb_tls" {
  type        = bool
  default     = false # TLS cutover phase (a): 443 listener (validated cert) + api.<domain> alias; 80-forward stays
  description = "Attach the issued ACM cert to the ALB (443) and create api.<domain> -> ALB."
}

variable "alb_retire_http_forward" {
  type        = bool
  default     = false # TLS cutover phase (d): ONLY after CloudFront origin is https (phase b) + verified
  description = "Retire the ALB :80 forward listener (replaced by 80->443 redirect)."
}

variable "api_cdn_origin_domain" {
  type        = string
  default     = "" # TLS cutover phase (b): set to api.<domain> — CloudFront origin goes https-only
  description = "Named https origin for the API CloudFront distribution."
}

variable "web_custom_domain" {
  type        = string
  default     = "" # apex go-live: set to the real domain — Amplify domain association + apex/www records
  description = "Serve the Amplify web app at this domain (apex + www)."
}

variable "api_cube_env" {
  type        = bool
  default     = false # flip to inject the cube signing secret into the API task (query_cube live)
  description = "Inject CUBEJS_API_SECRET_VALUE into the api task from uplift/cube-api-secret."
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

variable "worker_image" {
  type        = string
  default     = "" # the prebuilt uplift-worker ECR image (deploy still gated on env-key + worker module apply)
  description = "ECR image URI for the worker service."
}

variable "enable_crm_db_rotation" {
  type        = bool
  default     = false # TODO 204: flip + apply, then run the RUNBOOK controlled-rotation procedure
  description = "Deploy the crm-app-db rotation Lambda (SAR) and attach the 30-day rotation."
}

variable "posthog_host" {
  type        = string
  default     = "" # REQ-006: only set to override the in-code ingestion default
  description = "PostHog ingestion host override."
}

variable "api_integrations_real" {
  type        = bool
  default     = false # REQ-008 master switch: flip only after the connector-write IAM is applied
  description = "Set INTEGRATIONS_REAL_SECRETS=1 on the API task."
}

# --- Signup-plane plain (non-secret) config (api task env; shared/config.py names). ---
# Stripe Price IDs are public identifiers (price_...), not secret-shaped; URLs/addresses/domain
# lists likewise. "" = the env entry is omitted and the feature stays unconfigured.
variable "stripe_price_id_starter" {
  type        = string
  default     = "" # set the real price_... id in prod.auto.tfvars
  description = "Stripe Price ID for the starter plan (STRIPE_PRICE_ID_STARTER on the api task)."
}

variable "stripe_price_id_team" {
  type        = string
  default     = "" # set the real price_... id in prod.auto.tfvars
  description = "Stripe Price ID for the team plan (STRIPE_PRICE_ID_TEAM on the api task)."
}

variable "stripe_price_id_scale" {
  type        = string
  default     = "" # set the real price_... id in prod.auto.tfvars
  description = "Stripe Price ID for the scale plan (STRIPE_PRICE_ID_SCALE on the api task)."
}

variable "stripe_success_url" {
  type        = string
  default     = ""
  description = "Hosted-Checkout success redirect URL (STRIPE_SUCCESS_URL; UX only — provisioning trusts the signed webhook)."
}

variable "stripe_cancel_url" {
  type        = string
  default     = ""
  description = "Hosted-Checkout cancel redirect URL (STRIPE_CANCEL_URL)."
}

variable "signup_internal_bypass_domains" {
  type        = string
  default     = "" # unset = no bypass (fail closed); code-side read lands in shared/config.py first
  description = "Comma-separated email domains allowed to bypass external signup gating (SIGNUP_INTERNAL_BYPASS_DOMAINS on the api task)."
}

# --- Cortex persistent model registry (api + worker task env; ml/registry.py). ---
variable "cortex_s3_registry" {
  type        = bool
  default     = false # flip to point CORTEX_S3_BUCKET at the datalake bucket + grant task-role S3
  description = "Enable the persistent Cortex model registry on the api+worker tasks (datalake bucket, cortex/* prefix)."
}

variable "cortex_local_dir" {
  type        = string
  default     = "" # dev/tests fallback only (CORTEX_S3_BUCKET wins in code) — never set in prod
  description = "CORTEX_LOCAL_DIR filesystem registry root for the api/worker tasks."
}

variable "cortex_retrain_enabled" {
  type        = bool
  default     = false # the EventBridge retrain fan-out fires only when an owner flips this
  description = "Enable the scheduled Cortex retrain fan-out (scripts/ml/retrain_all.py)."
}

variable "playbook_dispatch_enabled" {
  type        = bool
  default     = false # the EventBridge playbook dispatcher fires only when an owner flips this
  description = "Enable the scheduled playbook trigger dispatcher (agents.playbooks.dispatch --schedule)."
}

variable "playbook_dispatch_tenants" {
  type        = string
  default     = "" # "" => the dispatcher logs 'nothing to do' and exits 0
  description = "Comma-separated tenant ids the playbook dispatcher fans out over (PLAYBOOK_DISPATCH_TENANTS)."
}

# --- Provisioning Lambda AI-plane env (secret ARN references, resolved in-handler). ---
variable "provisioning_anthropic_env" {
  type        = bool
  default     = false # flip ONLY after uplift/anthropic-api-key + uplift/env-id hold values
  description = "Pass the Anthropic org-key + env-id secret ARNs to the provisioning Lambda env (agent_plane step)."
}
