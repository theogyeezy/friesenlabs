# Cognito multi-tenant auth (Build Guide Phase 9, Step 48).
# One user pool; tenancy rides as an IMMUTABLE custom attribute in the JWT. The client cannot write it.
# AUTHORED + VALIDATED ONLY.

variable "project" { type = string }

resource "aws_cognito_user_pool" "this" {
  name                     = "${var.project}-users"
  auto_verified_attributes = ["email"]

  username_attributes = ["email"]

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = true
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

resource "aws_cognito_user_pool_client" "web" {
  name            = "${var.project}-web"
  user_pool_id    = aws_cognito_user_pool.this.id
  generate_secret = false

  explicit_auth_flows = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]

  # custom:tenant_id is READ-only to the client (not in write_attributes) so it can never be self-set.
  read_attributes  = ["email", "custom:tenant_id"]
  write_attributes = ["email"]
}

output "user_pool_id" { value = aws_cognito_user_pool.this.id }
output "user_pool_client_id" { value = aws_cognito_user_pool_client.web.id }
output "jwks_uri" {
  value = "https://cognito-idp.${data.aws_region.current.region}.amazonaws.com/${aws_cognito_user_pool.this.id}/.well-known/jwks.json"
}

data "aws_region" "current" {}
