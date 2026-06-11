# Go-live checklist — owner actions (the code is done; these are flags/secrets/approvals)

Everything here is **owner-only**: AWS console approvals, GitHub secrets, and `terraform apply`s.
The product code is built, tested, and on `main`; nothing below needs new code. Work top-down —
**Section 1 (email-only signup launch) is the highest-value and is unblocked right now.**

## How applies actually work (read this first)

`terraform apply` does **not** run from a local checkout. It runs through the deploy pipeline:
- **Workflow:** `.github/workflows/deploy.yml` — `workflow_dispatch` (run it from the GitHub Actions UI or `gh workflow run deploy.yml`).
- **Config source:** the live tfvars are the **`PROD_AUTO_TFVARS_B64`** GitHub Actions secret (base64), materialized to `prod.auto.tfvars` inside the run. The committed `infra/envs/prod.tfvars` is an **empty template — do NOT apply with it.**
- **Gate:** the apply job is the `production` environment with a **manual-approval** rule (approver: theogyeezy).
- **Editing a flag = edit the canonical `prod.auto.tfvars` (gitignored, on the machine that set the secret) → re-encode → re-set the secret → run the workflow → approve.**
  ```bash
  base64 -i infra/prod.auto.tfvars | gh secret set PROD_AUTO_TFVARS_B64
  gh workflow run deploy.yml      # then approve the apply when it hits the gate
  ```
- ⚠️ **Do NOT `terraform apply` from a laptop** with the empty template or no tfvars — it computes a destructive plan that reverts the whole live deployment. If the canonical `prod.auto.tfvars` is lost, reconstruct it from the live task def first: `aws ecs describe-task-definition --task-definition uplift-api --query 'taskDefinition.containerDefinitions[0].{env:environment,secrets:secrets}'`.

---

## 1. Launch signup on EMAIL-ONLY verification (ready NOW)

Resend domain `friesenlabs.com` is verified; phone is deferred behind a flag. This makes signup work end to end today (email → verify → pay → provision → login), no SMS needed.

- [ ] Confirm Resend: exactly ONE `_dmarc.friesenlabs.com` TXT record (the `p=none` we added is optional — if Google Workspace already had one, delete ours so DMARC isn't double-defined).
- [ ] Confirm the AWS secret `friesenlabs/platform/shared/resend-api-key` holds the active Resend "Onboarding" key (`re_hL4XDYXu…`, Sending access).
- [ ] In the canonical `prod.auto.tfvars`, set:
  ```hcl
  resend_from_email      = "no-reply@friesenlabs.com"
  signup_verify_url_base = "https://friesenlabs.com"   # the live app URL in the email link — confirm the signup page is reachable there
  allow_real_sends       = true
  signup_require_phone   = false
  ```
- [ ] Re-encode + set `PROD_AUTO_TFVARS_B64`, run `deploy.yml`, approve the apply.
- [ ] Verify: one real signup → verification EMAIL arrives, verify, pay, provision, login. No phone step.

## 2. Seed the workspace-key pool (the real-paid-customer blocker)

Until this is done, a real paid signup gets charged then parks `provisioning_failed` (`pool_empty`). The @friesenlabs.com bypass + demo paths work without it.

- [ ] Pre-mint Anthropic workspace keys in the Anthropic Console.
- [ ] Load them: `LOAD_KEYS_REAL_SECRETS=1 python scripts/ops/load_workspace_keys.py < keys.txt` (writes material → Secrets Manager, refs → Postgres).

## 3. Add phone verification back (when AWS approves SMS)

- [ ] SNS SMS: exit the sandbox + set a spend limit + register an origination identity (toll-free verification or 10DLC). Set default message type = Transactional. (AWS console — see the SNS prompt; can take days.)
- [ ] Then flip `signup_require_phone = true` in `prod.auto.tfvars` → re-deploy. Phone step + OTP re-activate; `allow_real_sends` is already on so SMS delivers immediately.

## 4. Turn on the data plane (dashboards show real rows)

- [ ] `api_cube_env = true` → injects `CUBEJS_API_SECRET_VALUE` so the Cube client is real (not degraded). Dashboards/Reports/Balto then render real rows instead of "No data yet".
  - First verify the live task-def doesn't already carry it (doc conflict flagged: `BUILD_STATUS.md` says it does; `asgi.py`/`TODO.md` say degraded).

## 5. Turn on Cortex (per-tenant ML)

- [ ] `cortex_s3_registry = true` (points `CORTEX_S3_BUCKET` at the datalake + grants S3).
- [ ] Set the **`CORTEX_SIGNING_KEY`** secret value (the terraform secret `uplift/cortex-signing-key` now exists — put a real HMAC key in it; the signed registry fails closed without it).
- [ ] `cortex_retrain_enabled = true` (the EventBridge retrain fan-out is wired + disabled) → seed at least one tenant retrain so `/cortex/health` serves a real champion.

## 6. Turn on ingest + connectors

- [ ] `ingest_schedule_enabled = true` + `ingest_tenants = "<tenant-id,...>"` (vault each tenant's per-source secret first).
- [ ] `INTEGRATIONS_REAL_SECRETS` + `INGEST_REAL_STORES` switches for live connector connect/sync + CSV-import landing in the CRM tables.
- [ ] Live per-connector `# VERIFY` pass against the real vendor APIs (HubSpot + Stripe self-confirmed in code; **GoHighLevel still needs a live verify** — no confirmed server-side incremental filter).

## 7. Turn on playbook automation

- [ ] `playbook_dispatch_enabled = true` + `playbook_dispatch_tenants = "<tenant-id,...>"` → the EventBridge dispatcher fires scheduled playbooks (wired + disabled).
- [ ] (Studio live registrar: wired in `asgi.py` — activation/run register a real crew automatically once a tenant is provisioned with an MA environment; no flag.)

## 8. Knowledge corpus + chat citations

- [ ] Seed the demo/first-tenant knowledge corpus (run `scripts/seed_demo_tenant.py` / the seed job with `INGEST_REAL_STORES=1`) so live `/chat` returns grounded citations (the invariant holds; it just has nothing to cite today).

## 9. Settings persistence go-live

- [ ] Run the `tenant_settings` column migrate (`workspace_name` + `notification_prefs` were added via `ADD COLUMN IF NOT EXISTS`) against live Aurora.
- [ ] Then wire `settings=SettingsDeps(store=PgSettingsStore(dsn))` in `asgi.py` so `GET/PUT /account/settings` go live (small code follow-up — ping me; until then the Workspace-settings UI honestly shows "not available").

## 10. Accountability + account deletion (optional)

- [ ] `CONTROL_GLOBAL_OPERATOR_TENANTS` — seed so the **global** kill switch works (tenant-scope works today; global is fail-closed until seeded).
- [ ] Account deletion stays **inert by design** (destructive). To enable, deliberately wire `account_delete=AccountDeleteDeps(deleter=PgAccountDeleter(dsn))` in `asgi.py` (code follow-up — your call).

## 11. Landing-legal (deliberately deferred)

- [ ] The fake 501(c)(3)/EIN/donation, "Real owners" testimonials, fabricated research, "LIVE" demo claims, App Store badge, and missing Terms/Privacy — see `TODO.md` § Landing-legal. Excluded from the build by request; needs counsel + real content.

---

**Quick reference — every tfvars flag added for go-live (all default to the safe/off value):**
`allow_real_sends` · `signup_require_phone` · `api_cube_env` · `cortex_s3_registry` · `cortex_retrain_enabled` · `playbook_dispatch_enabled` · `playbook_dispatch_tenants` · `ingest_schedule_enabled` · `ingest_tenants` · `signup_real_deps` · `api_signup_env`. Each flips via the deploy pipeline (Section 0).

---

## Launch-readiness audit (2026-06-11) — findings

A full-surface launch audit (7 parallel auditors). Security/RLS/trust-rule/Greenlight all PASS (0 blockers). Code blockers below were FIXED in this PR; the rest are tracked here.

### Fixed (this PR)
- ✅ **Verification email was unusable** — the SPA's email-verify step needs a TYPED code but the email only put the long token in a (dead) link. Now the token is rendered as copy-pasteable `<code>`. _(The #1 launch blocker — defeated the email-only launch.)_
- ✅ **Playbook dispatcher crashed on enable** — `dispatch.py` imported `PgWorkspaceStore` from the wrong module (`api.pg_clients` → `agents.workspace_store`); now fixed + a real-mode `_build_runner` test added.
- ✅ **`dow=7` Sunday cron never fired** — the cron matcher rejected `7`; now normalizes `7→0` (+ tests).
- ✅ **Terraform apply would FAIL** — the `scheduled_jobs` retrain rule collided with the legacy `cortex` module's `uplift-cortex-retrain` name; renamed to `-job`.
- ✅ **`CORTEX_SIGNING_KEY` unguarded** — injected from an empty secret → retrain task `ResourceInitializationError` on enable; now gated behind `cortex_signing_key_available`.
- ✅ **`_StubCognito` missing `set_signup_password`** — would 500 on non-real/preview deploys; added a no-op.

### Remaining — frontend launch-polish (code; small follow-ups)
- [ ] **StudioView falsely says "crew is registered"** on a record-only activation — `act()` overwrites the honest record-only notice (`web/src/api/StudioView.tsx:286-312`). Fabricated success state.
- [ ] **Command Center doesn't refresh after "Load sample data"** — `<DashboardView/>` isn't keyed on `sampleReloadKey` (`app.tsx:448`); add the key.
- [ ] **No React error boundary** anywhere — one render throw white-screens the whole authed shell. Add an ErrorBoundary around the authed surfaces.
- [ ] **New-Deal board form has no contact/company picker** (`PipelineBoard.tsx`) and the **Contacts form has no company input** (`ContactsDirectory.tsx`) — primary write actions partially wired (contact↔deal link only via chat).
- [ ] **Marketplace + Integrations-list** treat 404 as a generic red error instead of an honest "rolling out" state (siblings handle it).
- [ ] Minor: BillingManage `formatMoney` guard a null currency; ChatDock keep a "Balto synthesizing" spinner; Reports empty-state "Ask Balto" CTA.

### Remaining — owner actions (added to the sections above)
- [ ] **Captcha is default-OPEN** (`SIGNUP_CAPTCHA_REQUIRED` unset) — at a fully-public self-serve launch this is the one open abuse vector (scripted signup-spam against the email/Cognito budget). Wire a Turnstile/hCaptcha validator + set the flag, OR keep signup invite/bypass-only at first.
- [ ] **No alarms on the two new scheduled jobs** — add a CloudWatch `FailedInvocations` alarm per EventBridge rule (retrain + dispatch), wired to the alarms SNS topic.
- [ ] **Dead `Foundation.html` link** (5 places on the landing) — part of the deferred nonprofit/landing-legal narrative; route `foundation.tsx` or remove the CTAs when that work is done.
- [ ] **`DonateModal` fakes a successful donation** with no payment — relabel/remove until a real donation flow exists (deferred-legal-adjacent).
- [ ] Cleanup: remove the now-superseded `module "cortex"` (its rule has no target; replaced by `scheduled_jobs`).
