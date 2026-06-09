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

## Validate (no creds needed)
```bash
cd infra
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
```

## Apply (BLOCKED: needs Nick)
Configure the S3+DynamoDB backend, supply a real `*.tfvars`, `terraform plan`, review, then apply
with live AWS creds. Not done by the autonomous build.
