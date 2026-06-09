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
  environment_variables = {
    VITE_API_MOCK     = var.api_base_url == "" ? "1" : "0"
    VITE_API_BASE_URL = var.api_base_url
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

output "app_id" { value = aws_amplify_app.web.id }
output "default_domain" { value = aws_amplify_app.web.default_domain }
output "branch_url" { value = "https://${aws_amplify_branch.this.branch_name}.${aws_amplify_app.web.default_domain}" }
