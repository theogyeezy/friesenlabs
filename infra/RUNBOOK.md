# infra/RUNBOOK.md — live-ops runbook (Lane Nick)

Operational log + procedures for the live stack (acct 186052668426, us-east-1).
Raw plan dumps stay local; this file records the reviewed conclusions.

## Baseline plan triage — 2026-06-09

`terraform init -backend-config=backend.hcl && terraform plan` from `main` @ c5103b0
(state serial: S3 backend, full refresh): **14 to add, 1 to change, 2 to destroy.**

**Verdict: NOT a clean baseline — do not full-apply.** Until both surprises below are
resolved, any apply must be `-target`ed at pure-add modules only.

### Intentional adds (14) — the known authored-but-unapplied modules
- `module.cube` (4): log groups ×2, ECS task def, ECS service
- `module.worker` (4): log groups ×2, ECS task def, ECS service
- `module.observability` (6): 5 alarms (alb_5xx, alb_latency, aurora_acu,
  redis_evictions, worker_absent) + SNS topic

### SURPRISE 1 — plan destroys the live Amplify frontend (2 destroys)
- `module.web_hosting[0].aws_amplify_app.web` + `aws_amplify_branch.this`
  "because module.web_hosting[0] is not in configuration".
- **Root cause:** `infra/main.tf:140` gates the module on
  `var.github_access_token != ""`, and the machine-local `prod.auto.tfvars` does not
  set `github_access_token` (the value used at original apply time was never persisted
  to this machine). count drops to 0 → terraform plans to destroy the live app
  (`main.d224yxym1ehrim.amplifyapp.com`).
- **Remediation:** restore `github_access_token = "<the Amplify GitHub PAT>"` in
  `prod.auto.tfvars` (gitignored) and re-plan; expect the 2 destroys to disappear.
  Structural hardening (follow-up): gate web_hosting on an explicit
  `enable_web_hosting` bool instead of a secret's emptiness, so a missing local
  secret can never plan a frontend teardown.
- **Status: PARKED — needs the GitHub PAT (Matt).**

### SURPRISE 2 — budget notification block removal (1 change)
- `module.guardrails.aws_budgets_budget.monthly` updated in-place: removes the live
  80%-ACTUAL notification block (which has an **empty** subscriber list, so it pages
  nobody today).
- **Root cause:** `notify_email` is unset in local tfvars, so the config renders no
  notification block while the live budget has one.
- **Remediation:** set `notify_email` in tfvars and fix the $200-vs-$500 limit +
  subscriber as one intentional change (TODO "Fix the billing alarm/budget
  notification"). Low-risk either way — the live block alerts no one.
- **Status: PARKED — needs Matt (budget owner) to pick the email + limit.**

### Live verification at triage time (2026-06-09, post-plan, no apply performed)
- `https://d1vw20lc120dpa.cloudfront.net/healthz` → **200** (edge → ALB → Fargate → uvicorn).
  Note: the health route is `/healthz`, not `/api/healthz` (404) — older docs/TODO lines that say
  `/api/healthz` are drifted.
- `ecs describe-services uplift-cluster/uplift-api` → running 1 / desired 1, ACTIVE.
- Direct `http://<alb-dns>/healthz` from the internet → timeout (SG admits only the CloudFront
  prefix list) — intended.
- `amplify list-apps` → `uplift-web` @ `d224yxym1ehrim.amplifyapp.com` exists and is live — this is
  the resource the un-clean plan would destroy.

## Aurora hardening — 2026-06-09 (feat/nick-aurora-hardening)

- Live verification showed the TODO premise was stale: the cluster ALREADY has
  `backup_retention_period=7` and `deletion_protection=true` (verified via
  `describe-db-clusters`). Remaining real gaps: `copy_tags_to_snapshot=false`,
  `PerformanceInsightsEnabled=false`.
- Authored exactly those two attributes. `plan -target=module.data`:
  **0 add / 2 change / 0 destroy**, attribute diff is precisely
  `copy_tags_to_snapshot false→true` + `performance_insights_enabled false→true`
  (in-place, no downtime; PI 7-day retention = free tier).
- **Intended-change apply** (explicitly named by TODO items 123/136): apply
  `-target=module.data` from merged main, then re-verify with
  `describe-db-clusters` / `describe-db-instances`.
- **APPLIED 2026-06-09 from main @866328b** (re-planned first: still exactly the 2 attrs).
  Live-verified: `copyTags=true`, `PerformanceInsightsEnabled=true`, cluster + instance `available`.

### Apply discipline (until the baseline is clean)
1. No full `terraform apply`.
2. Pure-add module deploys go via `terraform apply -target=module.<cube|worker|observability> baseline-style plan first`.
3. Re-run this triage after every tfvars/state change and update this section.
