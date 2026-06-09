# infra/ — Uplift IaC (Terraform)

Phase 0 (AWS Foundation) is authored here. **Authored + validated only — never applied** in this
build (cost/irreversible; that's Nick's call with live creds).

## Phase 0 contents
- `modules/vpc` — VPC across 2 AZs: 2 public + 2 private subnets, IGW, single NAT, route tables,
  free S3 gateway endpoint.
- `modules/security` — least-privilege SG chain: ALB(443) → API(8000) → DB(5432) + Redis(6379).
- `modules/iam` — `ecsTaskExecutionRole` + per-service task roles (api/cube/worker). Human access
  is via IAM Identity Center (SSO), configured outside this stack.
- `modules/secrets` — Secrets Manager containers for the Anthropic key + connector creds (values
  set out-of-band, never in code/state).
- `modules/ecr` — `uplift-api`, `uplift-cube`, `uplift-worker` repos (scan-on-push, immutable tags).

Later phases add Aurora, Redis, ECS services, Cognito, ALB, budgets.

## Remote state (S3 backend)
State lives in S3 (`uplift-tfstate-*`, versioned + KMS-encrypted, S3-native locking). The bucket/key
are in `infra/backend.hcl` (gitignored — keeps the account-id bucket name out of this public repo).
Init against it with:
```bash
cd infra
terraform init -backend-config=backend.hcl
```
A new clone needs its own `backend.hcl` (ask Nick) — or run `init -backend=false` for validate-only.

## Validate (no creds needed)
```bash
cd infra
terraform fmt -check -recursive
terraform init -backend=false    # skips the S3 backend
terraform validate
```

## Web hosting (Amplify, Vite SPA)
`modules/web_hosting` is an Amplify Hosting app for `web/` (git-connected: push to `main` → auto
build + deploy via CloudFront + TLS). It is **only created when a GitHub token is supplied** —
`terraform apply -var="github_access_token=<PAT with repo scope>"`. Without it, no web hosting is
created. The site builds with `VITE_API_MOCK=1` (working demo) until you set `web_api_base_url` to the
deployed API. Output `web_app_url` is the live URL.

## Apply (BLOCKED: needs Nick)
Configure the S3+DynamoDB backend, supply a real `*.tfvars`, `terraform plan`, review, then apply
with live AWS creds. Not done by the autonomous build.
