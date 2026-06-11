aws_region = "us-east-1"
project    = "uplift"
vpc_cidr   = "10.0.0.0/16"

# --- Signup-plane plain config (empty defaults; real values belong in the machine-local
# --- <env>.auto.tfvars, which Terraform loads automatically — do NOT also pass this file with
# --- -var-file at apply, command-line var-files override auto.tfvars).
stripe_price_id_starter        = ""
stripe_price_id_team           = ""
stripe_price_id_scale          = ""
stripe_success_url             = ""
stripe_cancel_url              = ""
resend_from_email              = ""
signup_verify_url_base         = ""
signup_internal_bypass_domains = ""

# --- Cortex persistent registry + provisioning-Lambda AI-plane env (deliberate go-live flips).
cortex_s3_registry         = false
cortex_local_dir           = ""
provisioning_anthropic_env = false
