# AWS Amplify Hosting for the Vite SPA in web/ (Build Guide Phase 9 front end, as-built with Vite).
# Git-connected CI/CD: push to the branch -> Amplify auto-builds + deploys, served via CloudFront with
# free managed TLS and SPA rewrites. This is Amplify HOSTING (not Amplify Gen2) — the backend is the
# separate FastAPI/AWS stack.
#
# Connecting to GitHub needs a Personal Access Token (repo scope) -> pass var.github_access_token at
# apply. Without it this module creates nothing (count gate in root main.tf), so the default plan stays
# clean. AUTHORED + VALIDATED; apply needs the token (BLOCKED: needs Nick's GitHub PAT).

variable "project" { type = string }
variable "repository" {
  type    = string
  default = "https://github.com/theogyeezy/friesenlabs"
}
variable "github_access_token" {
  type      = string
  sensitive = true
}
variable "branch" {
  type    = string
  default = "main"
}
variable "api_base_url" {
  type    = string
  default = "" # set to the deployed API URL to take the site out of mock mode
}
variable "api_cdn_domain" {
  type    = string
  default = "" # CloudFront HTTPS domain in front of the ALB; when set, Amplify proxies /api/* to it
}
variable "custom_domain" {
  type    = string
  default = "" # e.g. friesenlabs.com — creates the Amplify domain association (apex + www)
}
variable "zone_id" {
  type    = string
  default = "" # Route53 zone for the verification CNAME + apex/www records
}

variable "cognito_domain" {
  type    = string
  default = "" # Hosted UI host (no scheme); empty disables the login flow in the build
}
variable "cognito_client_id" {
  type    = string
  default = ""
}
variable "cognito_region" {
  type    = string
  default = "us-east-1"
}

resource "aws_amplify_app" "web" {
  name         = "${var.project}-web"
  repository   = var.repository
  access_token = var.github_access_token

  # Vite build; the app lives in web/. Cache node_modules between builds.
  build_spec = <<-YAML
    version: 1
    applications:
      - appRoot: web
        frontend:
          phases:
            preBuild:
              commands:
                - npm ci
            build:
              commands:
                - npm run build
          artifacts:
            baseDirectory: dist
            files:
              - '**/*'
          cache:
            paths:
              - node_modules/**/*
  YAML

  # Mock mode ON by default so the hosted site is a working demo before the backend API is live.
  # Flip VITE_API_MOCK=0 + set VITE_API_BASE_URL once the API (ALB/Fargate) is deployed.
  # VITE_COGNITO_*: Hosted UI config for the SPA login flow (public identifiers, not secrets).
  environment_variables = {
    VITE_API_MOCK          = var.api_base_url == "" ? "1" : "0"
    VITE_API_BASE_URL      = var.api_base_url
    VITE_COGNITO_DOMAIN    = var.cognito_domain
    VITE_COGNITO_CLIENT_ID = var.cognito_client_id
    VITE_COGNITO_REGION    = var.cognito_region
  }

  # Proxy /api/* to the CloudFront HTTPS edge in front of the ALB — must come BEFORE the SPA catch-all.
  # The browser hits the trusted Amplify domain; Amplify forwards to CloudFront (HTTPS, required), which
  # origins to the ALB. So the API needs no domain/cert and there's no CORS (same-origin to the browser).
  dynamic "custom_rule" {
    for_each = var.api_cdn_domain != "" ? [1] : []
    content {
      source = "/api/<*>"
      target = "https://${var.api_cdn_domain}/<*>"
      status = "200"
    }
  }

  # SPA rewrite: serve index.html (200) for any path that 404s, so deep links + refresh work.
  custom_rule {
    source = "/<*>"
    target = "/index.html"
    status = "404-200"
  }

  platform = "WEB"
}

resource "aws_amplify_branch" "this" {
  app_id            = aws_amplify_app.web.id
  branch_name       = var.branch
  enable_auto_build = true
  stage             = "PRODUCTION"
}

# Custom domain: apex + www -> the main branch. Amplify mints its own managed cert; the
# verification CNAME + the apex ALIAS/www CNAME land in OUR Route53 zone below.
resource "aws_amplify_domain_association" "this" {
  count                 = var.custom_domain != "" ? 1 : 0
  app_id                = aws_amplify_app.web.id
  domain_name           = var.custom_domain
  wait_for_verification = false

  sub_domain {
    branch_name = aws_amplify_branch.this.branch_name
    prefix      = ""
  }

  sub_domain {
    branch_name = aws_amplify_branch.this.branch_name
    prefix      = "www"
  }
}

locals {
  # "name CNAME value" -> record pieces (computed; safe in record VALUES, never in count).
  amplify_cert_parts = var.custom_domain != "" ? split(" ", aws_amplify_domain_association.this[0].certificate_verification_dns_record) : []
  # Per-sub_domain dns_record is "<prefix> CNAME <target>.cloudfront.net".
  amplify_apex_target = var.custom_domain != "" ? trimspace(element(split("CNAME", [for sd in aws_amplify_domain_association.this[0].sub_domain : sd.dns_record if sd.prefix == ""][0]), 1)) : ""
  amplify_www_target  = var.custom_domain != "" ? trimspace(element(split("CNAME", [for sd in aws_amplify_domain_association.this[0].sub_domain : sd.dns_record if sd.prefix == "www"][0]), 1)) : ""
  cloudfront_zone_id  = "Z2FDTNDATAQYW2" # the fixed hosted-zone id for ALL *.cloudfront.net aliases
}

resource "aws_route53_record" "amplify_cert_verification" {
  count   = (var.custom_domain != "" && var.zone_id != "") ? 1 : 0
  zone_id = var.zone_id
  name    = local.amplify_cert_parts[0]
  type    = "CNAME"
  ttl     = 300
  records = [local.amplify_cert_parts[2]]
}

resource "aws_route53_record" "apex" {
  count   = (var.custom_domain != "" && var.zone_id != "") ? 1 : 0
  zone_id = var.zone_id
  name    = var.custom_domain
  type    = "A"

  alias {
    name                   = local.amplify_apex_target
    zone_id                = local.cloudfront_zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "www" {
  count   = (var.custom_domain != "" && var.zone_id != "") ? 1 : 0
  zone_id = var.zone_id
  name    = "www.${var.custom_domain}"
  type    = "CNAME"
  ttl     = 300
  records = [local.amplify_www_target]
}

output "app_id" { value = aws_amplify_app.web.id }
output "default_domain" { value = aws_amplify_app.web.default_domain }
output "branch_url" { value = "https://${aws_amplify_branch.this.branch_name}.${aws_amplify_app.web.default_domain}" }
