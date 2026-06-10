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

## One-off task runs — 2026-06-09 (cycle 4: migrate + live isolation gate)

- Image: `uplift-api:dc7a352` (arm64, digest sha256:6b13e10209…) — pushed as a NEW immutable tag;
  built from main @dc7a352 (bundles the REQ-002 roles.sql + scripts/).
- One-off task def: family `uplift-migrate-oneoff:1` — a CLONE of the live `uplift-api` def with
  only the image swapped; the live service's family/revision untouched.
- `python -m api.migrate` (task 5165fb0731974a7f828814de8df5a13d): exit 0,
  log "migrate: schema + roles loaded; crm_app password set…" → REQ-002 grants live.
- `scripts/isolation_test.py` as crm_app (task f3ed4a5cc478437f8205366091cbede8): exit 0,
  log "[isolation] PASS — RLS enforced; no cross-tenant read/write." → TODO Sec/P0 188 done.
- REQ-002 live probe (rolled back): crm_app rolsuper=f/rolbypassrls=f; INSERT/SELECT/UPDATE ok,
  DELETE → InsufficientPrivilege on accounts + stripe_events.
- Post-run sanity: edge /healthz 200; uplift-api 1/1.

## X-Origin-Verify rollout procedure — authored 2026-06-09 (Sec/P0)

Two flags in the gitignored `prod.auto.tfvars`; NEVER flip both in one apply:
1. **Phase 1** `enable_origin_verify = true` → `apply -target=module.secrets -target=module.api_cdn`
   (creates the uplift/origin-verify value + stamps the header at CloudFront; ALB untouched).
   Wait `get-distribution` Status=Deployed, verify edge `/healthz` 200.
2. **Phase 2** `alb_enforce_origin_verify = true` → `apply -target=module.alb`
   (listener default → 403; priority-10 rule forwards only on header match). Verify edge 200.
   Negative-path: config-level only (`describe-rules` shows 403 default + header rule) — a true
   stranger's-distro probe would need a second CloudFront distro; the SG prefix-list already
   blocks non-CloudFront sources.
Rotation: taint `random_password.origin_verify` → phase-1 apply → wait Deployed → phase-2 apply.
ROLLBACK: flip `alb_enforce_origin_verify=false`, `apply -target=module.alb` (instant).

### Apply discipline (until the baseline is clean)
1. No full `terraform apply`.
2. Pure-add module deploys go via `terraform apply -target=module.<cube|worker|observability> baseline-style plan first`.
3. Re-run this triage after every tfvars/state change and update this section.
