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
- [ ] `cortex_signing_key_available = true` (only AFTER the secret has a value — injects `CORTEX_SIGNING_KEY` into the retrain task; a `valueFrom` on the empty secret blocks startup).
- [ ] `cortex_retrain_enabled = true` (the EventBridge retrain fan-out is wired + disabled) → seed at least one tenant retrain so `/cortex/health` serves a real champion.
- [ ] **Drift alerting (optional but recommended):** set `cortex_drift_alert_email = "you@friesenlabs.com"` to subscribe to the `uplift-cortex-drift` SNS topic (confirm the AWS subscription email), OR subscribe Slack/PagerDuty to the topic yourself. The retrain fan-out auto-publishes a positive live-drift verdict (`CORTEX_DRIFT_TOPIC_ARN` is already injected into the task). No email set = topic exists, no notifications.
- [ ] _Model note (no action):_ the bake-off now trains a **gradient-boosted-trees** learner alongside logistic regression over a 9-feature vector and keeps the higher held-out-AUC model per tenant. No flag — it's automatic once retrain runs.

## 6. Turn on ingest + connectors (= release Switchboard — the full ordered runbook is REQ-012)

- [ ] **Migrate first:** one-off `api.migrate` with a fresh image — adds the `integration_sync_runs` table + grants (async "Sync now", the single-runner guard, sync history / last-synced). Isolation gate after.
- [ ] **IAM deltas:** api task `secretsmanager:DeleteSecret` on `uplift/*/{hubspot,stripe,gohighlevel}*` (disconnect + account-delete vault purge); ingest task `secretsmanager:ListSecrets` (the `auto` tenant discovery). Verify #235's connector-write widening is APPLIED.
- [ ] `ingest_schedule_enabled = true` + `ingest_tenants = "auto"` — `auto` discovers the tenant set from the vaulted slots, so a customer who connects in the panel is auto-enrolled (no hand-list; a comma list still works). The rule syncs `--source hubspot`; add per-source runs when stripe/gohighlevel tenants exist.
- [ ] `INTEGRATIONS_REAL_SECRETS` (flag `api_integrations_real`) + `INGEST_REAL_STORES` on the api task for live connector connect/disconnect/sync + CSV-import landing in the CRM tables. (In-request sync risk is gone — API kicks are 202 + background + guarded.)
- [ ] Live per-connector `# VERIFY` pass against the real vendor APIs on the first connect (HubSpot + Stripe self-confirmed in code; **GoHighLevel still needs a live verify** — incremental filter AND the new connect-probe endpoint). Also verify put/create/describe/delete_secret shapes + the REQ-008 ARN-suffix match.
- [ ] **Bill the module:** minted DONE (test mode): `stripe_module_price_ids = { STRIPE_PRICE_ID_MODULE_INTEGRATION = "price_1ThHLBRCMItYjxIJAUlF0E1q" }` — staged in the machine-local `prod.auto.tfvars` with the other Switchboard flips; rides the next deploy. (Re-mint in LIVE mode at the live-keys cutover.)

## 7. Turn on playbook automation

- [ ] `playbook_dispatch_enabled = true` + `playbook_dispatch_tenants = "<tenant-id,...>"` → the EventBridge dispatcher fires scheduled playbooks (wired + disabled). **Same act: stamp `PLAYBOOK_DISPATCH_ENABLED=1` on the api task env** so the Studio stops bannering schedule playbooks as "trigger not enabled yet" (`GET /studio/playbooks` dispatch honesty — `feat/matt-agents-studio-p0s`).
- [ ] (Studio live registrar: wired in `asgi.py` — activation/run register a real crew automatically once a tenant is provisioned with an MA environment; no flag.)
- [x] Event triggers (`lead.created` from `POST /contacts`, `deal.created` from `POST /deals`) are live in-process once the agent plane is configured — no flag; fire-and-forget, draft-only, run history in `playbook_runs`. _(Needs the one-off DB migrate below first.)_
- [ ] **One-off DB migrate** for `playbook_runs` + the `playbooks.ma_*` columns (`uplift-migrate-oneoff` task-def family, schema.sql + roles.sql are idempotent) — until then run history answers 503 and crew-reuse re-registers per run.

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

## 12. Stripe webhook endpoint (the silent paid-signup blocker)

Provisioning fires **only** on the signed Stripe webhook (hard constraint #8) — if the endpoint isn't registered, a paid signup charges the card and then **never provisions**. (`infra/RUNBOOK.md` §"Signup go-live sequence" step 1. Per `CLAUDE.md` this may already be done during signup go-live — verify before launch.)

- [ ] Stripe dashboard → **register the `/webhooks/stripe` endpoint** at the live API URL (`https://api.friesenlabs.com/webhooks/stripe`), subscribed to `checkout.session.completed` + `invoice.paid` (+ `customer.subscription.deleted` for cancellation).
- [ ] Put the endpoint's signing secret (`whsec_…`) into the webhook-secret in Secrets Manager (the `construct_event` verify refuses all webhooks against an empty secret).
- [ ] Verify: a real test-mode checkout → the webhook arrives, signature verifies, the account provisions (not `provisioning_failed`).

## 13. Hard cost cap — AWS Budgets Deny-at-90% (owner)

The budget **alarm** is live, but the **auto-Deny action** (an IAM Deny policy AWS Budgets applies at 90% of budget) is **not created** — `budget_action_role_arn` is empty by default (`infra/variables.tf:38`, `infra/modules/guardrails`). Without it, a runaway spend only emails; it isn't *capped*.

- [ ] Create/choose the IAM role AWS Budgets assumes to apply the Deny policy, set `budget_action_role_arn` in `prod.auto.tfvars`, deploy. (Leave empty to keep alarm-only — a deliberate choice, not an oversight.)

## 14. Post-apply verifies (owner, after the next deploy)

- [ ] **End-to-end X-Ray trace** across api → cube → worker (the ADOT sidecars are wired but full trace verification needs a live apply — `infra/modules/{api_service,cube,worker}/main.tf`).
- [ ] **Scheduled-job alarms** (from the audit): a CloudWatch `FailedInvocations` alarm per EventBridge rule (Cortex retrain + playbook dispatch), wired to the alarms SNS topic.

---

**Quick reference — every tfvars flag added for go-live (all default to the safe/off value):**
`allow_real_sends` · `signup_require_phone` · `api_cube_env` · `cortex_s3_registry` · `cortex_retrain_enabled` · `cortex_signing_key_available` · `cortex_drift_alert_email` · `stripe_module_price_ids` (Phase-2 module billing, §"Module entitlements") · `budget_action_role_arn` (§13 hard cost cap) · `playbook_dispatch_enabled` · `playbook_dispatch_tenants` · `ingest_schedule_enabled` · `ingest_tenants` · `signup_real_deps` · `api_signup_env`. Each flips via the deploy pipeline (Section 0).

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
- [x] Cleanup: removed the dead `module "cortex"` (target-less rule + unsubscribed topic); the drift SNS topic moved into `scheduled_jobs` and is now wired to a real publisher (the retrain fan-out).

---

## Module entitlements — Phase 2 billing activation (owner: mint Prices + set one tfvar)

The "Your suite" feature (Settings → toggle modules on/off; the app shows only enabled modules) is
**built and live-able as-is** — toggling persists per-tenant and re-gates the nav/routes. The
**billing sync is built but inert** until you mint per-module Stripe Prices. Code path:
`PUT /account/modules` → persists the set → (when configured) `api/module_billing.ModuleBillingSync`
reconciles the tenant's Stripe **subscription items** to the selection ("selection sets the price").
It only ever adds/removes the module items — the plan-tier line item is never touched.

To turn billing ON (nothing else in code changes; it auto-activates when the env is set):

1. **Mint 10 recurring monthly Prices in Stripe** (one per non-spine module — Command Center is the
   required spine and can stay bundled or get its own Price). Catalog + amounts: `shared/modules.py`
   (`uplift` $49, `agents` $39, `workflows` $39, `greenlight` $25, `frontline` $39, `knowledge` $25,
   `cortex` $45, `integration` $29, `sidecar` $35, `command` $49). Use the **same livemode** as the
   plan-tier Prices already in use.
2. **Set the `stripe_module_price_ids` map** in the canonical `prod.auto.tfvars`, keyed by the EXACT
   env-var names `shared/modules.py` reads:
   ```hcl
   stripe_module_price_ids = {
     STRIPE_PRICE_ID_MODULE_CORTEX      = "price_..."
     STRIPE_PRICE_ID_MODULE_UPLIFT      = "price_..."
     STRIPE_PRICE_ID_MODULE_AGENTS      = "price_..."
     # ...one per module you want billed; omit a module to leave it visibility-only
   }
   ```
   (Wired through `infra/main.tf` → `module.api_service` → `plain_env`, inject-only-when-set, so a
   partial map is fine and an empty map keeps billing inert.)
3. **Deploy** via the pipeline (Section 0 / "How applies work"): re-encode the tfvars secret →
   `gh workflow run deploy.yml` → approve. On the next task rev the `STRIPE_PRICE_ID_MODULE_*` env
   vars are present → `module_billing.from_env` returns a live sync → toggles start moving the bill.
4. **Verify:** as a paid test tenant, toggle a module in Settings → "Your suite"; confirm the
   subscription gains/loses the matching item in Stripe and the next invoice reflects it. (A sync
   failure is non-fatal — the toggle still saves and the UI shows an honest "billing didn't go
   through, we'll retry" note; re-saving re-syncs.)

Notes / decisions baked in:
- **Default suite = full (opt-out).** A tenant with no saved selection (incl. all existing live
  tenants, pre-migrate) sees **everything**; the new power is turning modules OFF. This is also the
  fail-open for a store/Stripe error — no one is ever stranded out of a surface.
- Provisioning still seeds the row from the purchased plan in a later step if you want signup to
  pre-select modules by tier; today a new tenant simply starts on the full suite.
