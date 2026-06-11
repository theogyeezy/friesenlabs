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

## X-Origin-Verify APPLIED — 2026-06-09 (from main @d211c38)

- Phase 1 `-target=module.secrets -target=module.api_cdn`: 3 add / 1 change (exactly as planned);
  distro ETZLYZ2VC4KBI modification took 7m09s, Status=Deployed, CustomHeaders.Quantity=1.
  Edge /healthz 200.
- Phase 2 `-target=module.alb`: 1 add / 1 change (listener default → fixed-response 403; rule
  prio-10 X-Origin-Verify → forward). Edge /healthz 200 ×3 immediately after; unauth API routes
  unchanged (404 — same pre-existing behavior as the cycle-1 baseline note).
- Negative path: SG (CloudFront-prefix-only) + 403 default means a stranger's CloudFront distro
  now gets 403 instead of reaching FastAPI. Direct internet curl can't reach the listener at all.
- Flags now true in `prod.auto.tfvars`: enable_origin_verify, alb_enforce_origin_verify.
  ROLLBACK: flip alb_enforce_origin_verify=false, `apply -target=module.alb` (instant).

## X-Origin-Verify ROTATION procedure (REQ-012 item 11)

The shared secret lives in three places that must agree: `uplift/origin-verify` in Secrets
Manager (+ TF state — generated by `module.secrets.random_password.origin_verify[0]`), the
CloudFront custom origin header (`module.api_cdn`, distro ETZLYZ2VC4KBI), and the ALB listener
rule values (`module.alb` — post-TLS-cutover that is `aws_lb_listener_rule.origin_verify_https`
on the 443 listener; the :80 listener is redirect-only). CloudFront config changes take ~5-7
minutes to reach Deployed while the ALB rule updates instantly — rotating both in one apply
opens a 403 window at not-yet-propagated POPs. Two procedures:

**SAFE path (no 403 window — recommended):**
1. `alb_enforce_origin_verify = false` in `prod.auto.tfvars` → `terraform apply -target=module.alb`.
   The 443 default returns to forward (the SG + WAF still front the ALB; the relaxation window
   should be minutes). Verify edge `/healthz` 200.
2. Rotate the value + restamp CloudFront in one apply:
   `terraform apply -replace='module.secrets.random_password.origin_verify[0]' -target=module.secrets -target=module.api_cdn`
   (new random value → SM secret version + the CF custom header).
   Wait `aws cloudfront get-distribution --id ETZLYZ2VC4KBI` → Status=Deployed. Verify edge 200.
3. `alb_enforce_origin_verify = true` → `terraform apply -target=module.alb` (403 default returns;
   the priority-10 rule now matches the NEW value, read from state).
4. Verify: edge `/healthz` 200 ×3; direct `curl -s -o /dev/null -w '%{http_code}' https://api.friesenlabs.com/healthz`
   (no header) → **403** (the 443 default); `aws elbv2 describe-rules` on the 443 listener shows
   the prio-10 header rule + fixed-response 403 default.

**FAST path (single window, accepts ~5-7 min of partial 403s while CF propagates):**
`terraform apply -replace='module.secrets.random_password.origin_verify[0]' -target=module.secrets -target=module.api_cdn -target=module.alb`
then wait Deployed and run step-4 verification. Use only if a brief edge degradation is acceptable.

Never put the value anywhere but SM/state (it is already kept out of git; `prod.auto.tfvars`
does not carry it — the modules read it from the random_password resource via outputs).

## REQ-003 APPLIED — 2026-06-09 (from main @7c94e4c)

- `-target=module.secrets -target=module.iam`: 3 add / 1 change, exactly as planned.
- Containers live: uplift/stripe-webhook-secret, uplift/signup-token-secret,
  uplift/anthropic-admin-key. Token-signer value minted (`openssl rand -hex 32` → CLI
  put-secret-value; 1 version; never echoed/committed/in-state).
- Execution-role policy verified: uplift/* + rds!* + the 2 exact platform-secret ARNs.
- Signup go-live sequence (deliberate, in order): (1) Stripe dashboard → register
  /webhooks/stripe → put webhook-secret value; (2) put admin-key value after the
  signup/anthropic_admin.py VERIFY items pass; (3) flip `api_signup_env=true` (tfvars) →
  targeted api_service apply (task-def replace + service update ONLY); (4) separately flip
  `signup_real_deps=true` — the master switch. ALLOW_REAL_SENDS stays false throughout.
- Edge /healthz 200 after apply.

## AI-plane gate flipped — 2026-06-09 (cycle 12)

- MA SDK shapes verified against the claude-api skill docs (header + all client.beta namespaces
  match agents/runtime.py's VERIFY-flagged assumptions; coordinator = top-level multiagent field).
- Live: `client.beta.environments.create(name="uplift-prod", config={"type":"self_hosted"})` →
  **env_012JvqRKUZzUDeH3Gse6TBgZ**, stored in uplift/env-id (idempotent: lists+reuses by name).
- `api_anthropic_env=true` (tfvars) → api_service targeted apply: task-def rev 4, zero-downtime
  roll (healthz 200 throughout), secrets = [DB_USER, DB_PASS, ANTHROPIC_API_KEY, UPLIFT_ENV_ID].
- /chat: was bare 503, now 401 for unauth (reaches the auth layer); authed behavior needs a JWT
  probe + the conversation_factory wiring (Lane Matt).
- **PARKED — uplift/env-key:** the MA environment key is generated in the CONSOLE only
  (platform.claude.com → environment uplift-prod → "Generate environment key"; sk-ant-oat01-…).
  User click required; value then goes into uplift/env-key (CLI put). Worker deploy stays blocked
  on it (+ the cost note).

## crm-app-db rotation procedure (TODO 204)

Enable: `enable_crm_db_rotation=true` (tfvars) → targeted apply (SAR stack + rotation config,
rotate_immediately=false). The secret VALUE must carry host/port/dbname/engine keys (the AWS
rotation template requires them; added via CLI put — valueFrom :username::/:password:: unaffected).
Controlled rotation window:
1. `aws secretsmanager rotate-secret --secret-id uplift/crm-app-db`
2. Wait `describe-secret` shows the new version AWSCURRENT (rotation steps ~30-60s).
3. IMMEDIATELY `aws ecs update-service --cluster uplift-cluster --service uplift-api
   --force-new-deployment` (+ cube the same way) — old tasks' pooled conns survive but their NEW
   conns would fail auth; fresh tasks read the new AWSCURRENT.
4. Verify: edge /healthz 200; a DB-backed route (401-auth path) healthy; cube /readyz 200.
ROLLBACK: `update-secret-version-stage --move-to-version-id <AWSPREVIOUS id> --version-stage
AWSCURRENT` then ALTER ROLE crm_app back via a one-off migrate task.

## ALB TLS cutover sequence (execute when the friesenlabs.com cert is ISSUED)

Pre-req: Squarespace NS → Route53 (user), `dns_delegated=true` applied, cert ISSUED.
1. `certificate_arn = module.dns[0].certificate_arn` into module.alb (tfvars/wiring) → plan:
   the module swaps http_forward → https(443, forward) + http_redirect(80→443). NOTE this
   REPLACES the :80 listener (origin-verify rule rides on it — re-created by the same apply;
   verify the rule lands on the new 443 listener config or re-author for 443).
2. BEFORE applying: flip api_cdn origin to https-only port 443 CANNOT happen first (ALB has no
   443 yet) — sequence: apply ALB listeners FIRST (CloudFront keeps talking :80 → redirect 301
   loop risk! The 80-listener becomes redirect → CloudFront origin-protocol http would follow…
   CloudFront does NOT follow origin redirects → 301s surface to clients = OUTAGE).
   => SAFE ORDER: (a) add the 443 listener KEEPING 80-forward (temporary both-listeners state —
   needs a small module tweak: has_cert branch must not destroy http_forward yet), (b) flip
   api_cdn origin to https-only :443 + origin-verify header still sent, (c) wait Deployed +
   verify, (d) remove the 80-forward (or convert to redirect), (e) point Route53 A/AAAA alias
   api.friesenlabs.com → ALB, re-point Amplify /api proxy to https://api.friesenlabs.com,
   (f) retire module.api_cdn + the CloudFront-prefix SG rule (TODO 210/211) once nothing hits it.
   Each step its own targeted apply + edge health check. Author the module tweak when executing.
### Apply discipline (until the baseline is clean)
1. No full `terraform apply`.
2. Pure-add module deploys go via `terraform apply -target=module.<cube|worker|observability> baseline-style plan first`.
3. Re-run this triage after every tfvars/state change and update this section.

## Enable real sends — signup email + SMS verification (the `allow_real_sends` go-live act)

Verification email (Resend) + phone OTP (SNS SMS) are draft-gated by `ALLOW_REAL_SENDS` (CLAUDE.md
hard-constraint #2). The senders are the REAL clients under `signup_real_deps`, but they log + drop
the actual delivery until this flips — so signup email/phone verification (and, downstream, first
login) do not work until ALL of the prerequisites below are done. The flag is wired (default
`false`) onto the API task + provisioning Lambda; flipping it is the deliberate, last act.

**Prereq A — Resend (email): a VERIFIED sending domain.**
- Add `friesenlabs.com` in Resend → it emits DKIM (`resend._domainkey` TXT), a return-path on
  `send.friesenlabs.com` (MX `feedback-smtp.us-east-1.amazonses.com` pri 10 + SPF TXT
  `v=spf1 include:amazonses.com ~all`), and an optional `_dmarc` TXT.
- Add those to the Route53 `friesenlabs.com` zone (ADD only). The apex Google-Workspace SPF
  (`v=spf1 include:_spf.google.com ~all`) does NOT need merging — Resend's SPF lives on `send.`.
  CAUTION: ensure exactly ONE `_dmarc.friesenlabs.com` TXT (a second = DMARC invalid); the
  `_dmarc` record is optional for Resend verification.
- Wait for Resend status = **Verified** (SES-backed, usually minutes–1h). From-address is then
  `no-reply@friesenlabs.com` (apex; DMARC-aligned via Resend's DKIM `d=friesenlabs.com`).
- tfvars: `resend_from_email = "no-reply@friesenlabs.com"`; confirm `signup_verify_url_base` is the
  live app URL (the link in the email). Confirm the secret
  `friesenlabs/platform/shared/resend-api-key` holds an active Sending-access key.

**Prereq B — SNS SMS (phone OTP):** out of the SMS sandbox + a registered origination identity
(toll-free verification or 10DLC) + default message type Transactional + a monthly spend limit.
These are AWS account-level approvals (console, not terraform) and can take days. See the # VERIFY
in `signup/sms_sender.py` (`BLOCKED: Lane Nick — SNS SMS spend limit / origination identity`).

**The flip (only after A + B are both DONE):**
1. `allow_real_sends = true` in `prod.auto.tfvars`.
2. Targeted apply on `module.api_service` (+ `module.provisioning_lambda`) → the task def / Lambda
   env gains `ALLOW_REAL_SENDS=true`; roll the service.
3. Run ONE real signup end-to-end (real email + phone) and confirm the verification email + OTP SMS
   arrive and the account provisions + logs in. Roll back by setting the flag false + apply.

### Email-only launch (ship NOW while SMS approval is pending)

Prereq B (SNS SMS) takes days of AWS approval. Prereq A (Resend domain) is **DONE** (friesenlabs.com
verified). So you can launch on EMAIL-ONLY verification today and add phone later, via the
`signup_require_phone` feature flag (SIGNUP_REQUIRE_PHONE; default true). When false: the SPA skips
the phone step, no OTP is minted/sent, and an email-verified account is ready to pay.

`prod.auto.tfvars`:
```hcl
resend_from_email    = "no-reply@friesenlabs.com"   # apex domain is Verified in Resend
allow_real_sends     = true                          # email now delivers (SMS would fail — not used)
signup_require_phone = false                          # skip phone until SMS is approved
```
Apply + roll:
```bash
cd infra
terraform plan  -target=module.api_service -target=module.provisioning_lambda   # confirm only env adds
terraform apply -target=module.api_service -target=module.provisioning_lambda
aws ecs update-service --cluster uplift --service uplift-api --force-new-deployment --region us-east-1
```
Verify: ONE real signup → verification EMAIL arrives, verify, pay, provision, login. No phone step.
Also confirm `signup_verify_url_base` is the live app URL (the link inside the email) and the secret
`friesenlabs/platform/shared/resend-api-key` holds the active Resend "Onboarding" key.

**Adding phone later (when SNS SMS is approved):** finish Prereq B, then flip
`signup_require_phone = true` (+ re-apply `module.api_service`). The phone step + OTP re-activate;
`allow_real_sends` is already on, so SMS starts delivering immediately.
