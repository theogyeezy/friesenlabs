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

  # ADMIN_USER_PASSWORD_AUTH is for server-side smoke tests only (requires AWS IAM creds to call
  # admin-initiate-auth); the browser uses the Hosted UI code+PKCE flow, never password auth.
  explicit_auth_flows = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH", "ALLOW_ADMIN_USER_PASSWORD_AUTH"]

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

output "user_pool_id" { value = aws_cognito_user_pool.this.id }
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
