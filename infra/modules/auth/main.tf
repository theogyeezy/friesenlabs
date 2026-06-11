# Cognito multi-tenant auth (Build Guide Phase 9, Step 48).
# One user pool; tenancy rides as an IMMUTABLE custom attribute in the JWT. The client cannot write it.
# Login is the Hosted UI + OAuth authorization-code flow with PKCE (public SPA client, no secret).

variable "project" { type = string }

variable "callback_urls" {
  description = "OAuth redirect URIs for the SPA (e.g. https://<amplify>/auth/callback + localhost for dev)."
  type        = list(string)
  default     = []
}

variable "logout_urls" {
  description = "Allowed sign-out redirect URIs for the SPA."
  type        = list(string)
  default     = []
}

# Sec (REQ-012 item 4): Cognito threat protection (the feature AWS formerly called "advanced
# security"). ENFORCED = adaptive auth + compromised-credentials blocking on the pool that holds
# every tenant identity. "AUDIT" is the observe-only rollback (events logged, nothing blocked);
# "OFF" disables. NOTE: AUDIT/ENFORCED require the pool's Plus feature plan — see
# var.cognito_user_pool_tier and REQUESTS.md (REQ-012) for the apply-order note.
variable "cognito_threat_protection_mode" {
  type        = string
  default     = "ENFORCED"
  description = "Cognito threat protection (user_pool_add_ons.advanced_security_mode): OFF | AUDIT | ENFORCED. AUDIT is the observe-only rollback."
  validation {
    condition     = contains(["OFF", "AUDIT", "ENFORCED"], var.cognito_threat_protection_mode)
    error_message = "cognito_threat_protection_mode must be OFF, AUDIT, or ENFORCED."
  }
}

# "" = the user_pool_tier attribute is omitted entirely (terraform leaves the live pool's
# feature plan untouched — today's state). Threat protection AUDIT/ENFORCED requires PLUS;
# setting this to "PLUS" is a billing change (~per-MAU pricing) and must ride the same
# reviewed apply as the threat-protection flip.
variable "cognito_user_pool_tier" {
  type        = string
  default     = ""
  description = "Cognito feature plan: \"\" (leave unmanaged), LITE, ESSENTIALS, or PLUS. PLUS is required for threat protection AUDIT/ENFORCED."
  validation {
    condition     = contains(["", "LITE", "ESSENTIALS", "PLUS"], var.cognito_user_pool_tier)
    error_message = "cognito_user_pool_tier must be \"\", LITE, ESSENTIALS, or PLUS."
  }
}

resource "aws_cognito_user_pool" "this" {
  name                     = "${var.project}-users"
  auto_verified_attributes = ["email"]
  deletion_protection      = "ACTIVE" # holds tenant identities — guard against accidental delete

  username_attributes = ["email"]

  # Provisioning-only pool: users are created by the signup provisioner via AdminCreateUser, never
  # self-registered through the Hosted UI (which would bypass verify-before-pay + the tenant claim).
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = true
  }

  # OPTIONAL (not ON): ON forces TOTP enrollment at next Hosted UI login, which would break the
  # demo user + every automated login flow; OPTIONAL enables per-user TOTP enrollment now and
  # leaves the enforcement flip (ON) as a deliberate later act once admin users carry MFA.
  mfa_configuration = "OPTIONAL"
  software_token_mfa_configuration {
    enabled = true
  }

  # Threat protection (REQ-012 item 4). Provider ~>6.49 still models this as
  # user_pool_add_ons.advanced_security_mode (verified against the provider schema at
  # validate time) — the console-side rename to "threat protection" did not rename the API
  # field. ENFORCED requires the Plus feature plan (var.cognito_user_pool_tier).
  user_pool_add_ons {
    advanced_security_mode = var.cognito_threat_protection_mode
  }

  # "" = omit (null): the live pool's feature plan stays unmanaged/untouched by terraform.
  user_pool_tier = var.cognito_user_pool_tier != "" ? var.cognito_user_pool_tier : null

  schema {
    name                     = "tenant_id"
    attribute_data_type      = "String"
    mutable                  = false # immutable: users can't change their tenant
    developer_only_attribute = false
    string_attribute_constraints {
      min_length = 1
      max_length = 64
    }
  }
}

# Hosted UI domain (Cognito prefix domain — needs no DNS/cert; swap for a custom domain later).
# Prefix must be globally unique per region; suffix with the account id.
resource "aws_cognito_user_pool_domain" "this" {
  domain       = "${var.project}-${data.aws_caller_identity.current.account_id}"
  user_pool_id = aws_cognito_user_pool.this.id
}

resource "aws_cognito_user_pool_client" "web" {
  name            = "${var.project}-web"
  user_pool_id    = aws_cognito_user_pool.this.id
  generate_secret = false

  # Sec/P0 (REQ-012 item 2): ALLOW_ADMIN_USER_PASSWORD_AUTH REMOVED from the PUBLIC SPA client —
  # a public (no-secret) client with the admin password flow lets anyone holding IAM
  # admin-initiate-auth perms mint tokens by raw password against the prod pool, and widens the
  # credential-stuffing surface. The browser uses Hosted UI code+PKCE only. Smoke tests that
  # need the password flow get their own NON-public client (var.create_smoke_test_client below).
  explicit_auth_flows = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]

  # Hosted UI OAuth: authorization-code flow with PKCE (public client). No implicit flow.
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = var.callback_urls
  logout_urls                          = var.logout_urls

  # Short ID/access tokens; refresh via the refresh token.
  token_validity_units {
    id_token      = "minutes"
    access_token  = "minutes"
    refresh_token = "days"
  }
  id_token_validity      = 60
  access_token_validity  = 60
  refresh_token_validity = 7 # short-lived for a browser SPA (was 30); revoke-on-signout still applies

  # custom:tenant_id is READ-only to the client (not in write_attributes) so it can never be self-set.
  read_attributes  = ["email", "custom:tenant_id"]
  write_attributes = ["email"]
}

# Sec (REQ-012 item 2): a SEPARATE, NON-public (secret-bearing, no Hosted UI/OAuth) client for
# server-side smoke tests that genuinely need ADMIN_USER_PASSWORD_AUTH (admin-initiate-auth
# already requires IAM creds; the client secret adds the second factor). Default OFF — create
# it only if/when a smoke flow actually needs the password grant.
variable "create_smoke_test_client" {
  type        = bool
  default     = false
  description = "Create a non-public (confidential) app client allowing ADMIN_USER_PASSWORD_AUTH for server-side smoke tests. Default false: no such client exists; the public SPA client never carries the admin password flow."
}

resource "aws_cognito_user_pool_client" "smoke_test" {
  count           = var.create_smoke_test_client ? 1 : 0
  name            = "${var.project}-smoke-test"
  user_pool_id    = aws_cognito_user_pool.this.id
  generate_secret = true # confidential client — never shipped to a browser

  explicit_auth_flows = ["ALLOW_ADMIN_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]

  # No Hosted UI / OAuth surface at all on this client.
  token_validity_units {
    id_token      = "minutes"
    access_token  = "minutes"
    refresh_token = "days"
  }
  id_token_validity      = 60
  access_token_validity  = 60
  refresh_token_validity = 1 # smoke sessions are throwaway

  read_attributes  = ["email", "custom:tenant_id"]
  write_attributes = ["email"]
}

output "smoke_test_client_id" {
  value = var.create_smoke_test_client ? aws_cognito_user_pool_client.smoke_test[0].id : ""
}

# RBAC groups (REQ-012 item 10): coarse in-pool roles the app reads from the JWT's
# cognito:groups claim. Additive — no user is auto-assigned here; provisioning app code
# (in flight) puts the first provisioned user of a tenant into "admin" best-effort.
resource "aws_cognito_user_group" "admin" {
  name         = "admin"
  user_pool_id = aws_cognito_user_pool.this.id
  description  = "Tenant administrators (full in-app control surface)."
  precedence   = 1
}

resource "aws_cognito_user_group" "member" {
  name         = "member"
  user_pool_id = aws_cognito_user_pool.this.id
  description  = "Standard tenant members."
  precedence   = 10
}

output "user_pool_id" { value = aws_cognito_user_pool.this.id }
output "user_pool_arn" { value = aws_cognito_user_pool.this.arn }
output "user_pool_client_id" { value = aws_cognito_user_pool_client.web.id }
output "hosted_ui_domain" {
  description = "Hosted UI base host (no scheme), e.g. uplift-<acct>.auth.us-east-1.amazoncognito.com"
  value       = "${aws_cognito_user_pool_domain.this.domain}.auth.${data.aws_region.current.region}.amazoncognito.com"
}
output "jwks_uri" {
  value = "https://cognito-idp.${data.aws_region.current.region}.amazonaws.com/${aws_cognito_user_pool.this.id}/.well-known/jwks.json"
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}
