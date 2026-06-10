# CLAUDE.md — Uplift build context

This file orients any agent working in this repo. **On every commit/push, update the living docs
that the change touches — so they never drift from reality — under the two-lane ownership rules
(`CONTRIBUTING.md` § Two-lane contract): `CLAUDE.md` + `README.md` are LANE NICK single-writer;
`TODO.md` — check off only your own lane's sections, never reflow the other lane's lines;
`BUILD_STATUS.md` — write only your own lane's log section. Living-doc edits are the final,
smallest commit of a PR, after a rebase on `origin/main`.**

## What this is
Uplift: a multi-tenant agentic CRM. Hybrid architecture — **agent plane** on Claude Managed
Agents (beta), **everything else** on AWS. See `README.md` for the shape and `BUILD_STATUS.md`
for where the build currently is.

**Status:** all 13 phases (0–12) + the frontend are implemented and green (pytest 193, real
Postgres isolation gate in CI, `terraform validate` + `plan`, web build/typecheck/Playwright, smoke);
a final adversarial audit pass is merged. CI runs on `main`; trunk is **`main`** (branch via
short-lived `feat/…` PRs — see `CONTRIBUTING.md`).

**Live infra (real money) — the product is LIVE end-to-end, including login.** Applied to AWS
(acct 186052668426, us-east-1) under a $200 budget alarm. Live path: **browser → Amplify (Vite SPA,
real mode) → CloudFront → ALB (HTTP) → arm64 Fargate API → Aurora** (FORCE'd RLS) with real Cognito
JWKS auth. **Browser-verified end-to-end:** sign-in gate → Hosted UI (PKCE) → code exchange →
app shell → real RLS-scoped tenant rows. Unauth `/api/*` → 401; **`/chat` is LIVE** — the agent plane provisions a 7-agent roster + coordinator, answers and delegates, with draft-only Greenlight gating proven end-to-end (live verify 2026-06-10).
- ✅ **Login:** Cognito Hosted UI + PKCE in `web/src/auth/`; demo creds in `uplift/demo-user`.
- ✅ **Landing (overhauled 2026-06-10):** an **"Editorial & warm"** marketing page — cream paper,
  one warm-clay accent, **Fraunces** serif display, mono eyebrows with section numerals, hairline
  rules/cards, and a **bespoke product-grounded line-icon set**. Normal vertical scroll (the earlier
  dark cinematic/three.js fly-through was reverted as unusable — three.js removed); mobile hamburger
  portaled to `<body>` + sticky CTA. **Apple-style cinematics** (reduced-motion safe): hero load-in
  assembly, staggered card reveals, gentle hero-plate parallax. Hero + live demos framed as **product
  windows** (live address + LIVE pulse → real-capture feel). Friesen-vs-GoHighLevel radar + comparison
  (warm/light); live ROI calculator; benefit-first copy (no em-dashes/arrows/slop). **Hardened via a
  4-lens audit + live Lighthouse: SEO 100 · Best Practices 100 · Accessibility ~100 · Agentic 100** —
  keyboard-operable controls, dialog roles + Escape, skip-link + `<main>`, AA contrast, heading order,
  touch targets. **Perf: the authed app (Vega/dashboards/panels) is code-split off the landing →
  first-load ~247KB gz (was ~560KB).** SEO: real title/meta/OG/Twitter/canonical/theme-color, brand
  favicon, and a generated 1200×630 **og:image** card emitted by an inline Vite plugin (survives
  `publicDir:false`), served at `/og.png`. Founder photos bundled (7MB→32KB) + Matt's bio corrected
  (currently at ServiceNow). Browser-verified desktop + mobile. SNS alarm email CONFIRMED (alarms page
  the owner). Amplify edge cache: HTML `no-store` + hashed assets `immutable`, so web deploys appear
  instantly.
- ✅ **Live since 2026-06-09 (Lane Nick cycles 1-15):** Aurora hardening (retention 7, deletion
  protection, copy-tags, PI); X-Origin-Verify edge→ALB shared secret (403-default listener);
  cube service 1/1 (`/readyz` 200; memory driver — Cube 1.x dropped redis; sg_api self-rule);
  5 alarms + SNS + billing-alarm action + `uplift-live` dashboard + budget subscriber; CloudTrail
  scoped S3 data events + ALB access logs; IAM tightening (exact-ARN api task secrets, no SFN
  wildcard); provisioning Lambda + pinned SFN (idempotent executions, smoked all-stub); ingest
  scheduler applied DISABLED; prod isolation gate PASSED live as `crm_app`; baseline plan CLEAN.
- ✅ **AI/agent plane LIVE + verified (2026-06-10):** MA env `uplift-prod` (env_012JvqRKUZzUDeH3Gse6TBgZ)
  live; org key + env-id + `SIGNUP_REAL_DEPS=1` on the API task (rev 10); the real
  `signup.agent_plane.AgentPlaneEnsure` is wired (not `_Noop`). `scripts/verify_agent_plane.py` PASSED
  live: provision 7 specialists + coordinator → coordinator answer + delegation → Greenlight
  approve/execute with the **draft-only guarantee held** (no real send). **Worker 2/2 polling; cube
  live.** A RAG-embed IAM gap (`bedrock:InvokeModel` on Titan, missing from the api+worker roles) was
  caught by the verify and FIXED live. Grounding plumbing is green (no-uncited-claim invariant holds);
  a positive citation just needs a seeded tenant corpus (see `TODO.md`).
- 🟙 **Domain:** friesenlabs.com on Route53 — the Squarespace NS cutover is **DONE** and the
  wildcard ACM cert is **ISSUED** (confirmed 2026-06-10). The apex+www Amplify domain association
  initially FAILED (CNAMEAlreadyExists): a stale us-east-2 Amplify app ("friesenlabs", branch
  `prod`, served 404 — same dangling `djvyqxdhlili4` CloudFront target as the deleted rogue zone)
  held the CNAMEs. That app + its association were deleted, the uplift-web association re-created,
  and Route53 apex/www repointed to the new Amplify CloudFront target — association **AVAILABLE**;
  **https://friesenlabs.com is LIVE + verified** (apex+www 200 over the `*.friesenlabs.com` cert,
  correct landing page). The **ALB TLS cutover is DONE too** (sweep-executed, verified in
  Matt's session): ALB 443 serves the real cert with the 403-default origin-verify gate,
  api_cdn origin is `api.friesenlabs.com` https-only:443, :80 is redirect-only (SG-scoped),
  edge + SPA `/api` healthz 200. api_cdn stays (RECOMMEND-AGAINST retiring — Lane Ship note).
- ✅ **Signup/provisioning go-live DONE:** `api_signup_env` + `signup_real_deps` flipped; Stripe/Resend/
  Anthropic-admin/webhook secrets present on the API task; the real provisioning clients are wired (no
  `_Stub`/`_Noop`). Worker deployed (env-key present). (SNS email sub CONFIRMED.)
- ✅ **NS delegation DONE (2026-06-10):** `friesenlabs.com` NS point at the 4 Route53 nameservers
  and the cert is ISSUED — the ALB TLS cutover was auto-run by the hourly Lane-Nick sweep and is
  verified DONE (see the Domain bullet above; `og:image` + canonical repoint to the real domain).
- **Ops:** state in S3 (KMS); machine-local `infra/prod.auto.tfvars` carries the live values +
  go-live flags — full applies allowed only against a re-verified clean plan; targeted applies
  are the norm. One-off tasks run via the `uplift-migrate-oneoff` task-def family. Runbook:
  `infra/RUNBOOK.md`. REQUESTS queue: REQ-001..005 all DONE. Completion sprint (cycles 16-23):
  cube model + Cloud Map live, CI/CD OIDC pipeline, ECS Exec, GuardDuty/Config/SSM, worker image
  staged, rotation executed, TLS-cutover runbook authored, GHL-style landing shipped. Remaining
  work is user-input-gated only. CI/CD pipeline PROVEN end-to-end (prod runs current main). SNS
  alarms confirmed. Security-hardening batch APPLIED + live-verified: CloudFront WAFv2 (managed rules + rate
  limit) + access logging + HSTS + PriceClass_100; Cognito deletion-protection + admin-create-only +
  7-day refresh; AWS provider pin `~> 6.49`; ECS deployment circuit breakers (auto-rollback); ECR
  lifecycle policies; Aurora pg-log retention 30d; CI permissions block; `.stignore` parity. TODO.md
  swept (25 done items checked off + a Lane-Nick completion-status block). **All four owner-gated
  remainders are now satisfied** (env-key → worker live 2/2; Stripe webhook secret + Anthropic
  admin key → signup go-live done; Squarespace NS delegated 2026-06-10 → cert ISSUED → TLS
  cutover executed by the sweep + verified).
**Tooling:** `.claude/settings.json` enables the official-marketplace plugins so collaborators inherit
them on clone+trust. Don't commit secrets to it.

## How we build
- **Dependency order, not feature order.** Phase 0 → 12. Don't start a phase whose inputs
  don't exist. The Build Guide (`docs/`, local-only) is the source of truth for order + commands.
- **Test every step.** A unit isn't done until its applicable levels pass: unit · integration
  (`tests/integration/`) · smoke (`scripts/smoke/`) · Playwright e2e (`web/e2e/`, UI only) ·
  multi-tenant isolation (`scripts/isolation_test.py`, after any data/agent/auth change). Plus
  basics: `terraform validate/fmt`, `python -c import`, `npm run build`.
- **Review every feature** (self + cross) and record the outcome in `BUILD_STATUS.md`.

## Hard constraints (do not violate)
1. **Live cloud mutation is LANE NICK only** (see the two-lane contract in `CONTRIBUTING.md`).
   LANE MATT (app code) never runs `terraform apply` and never creates live AWS resources or
   Anthropic workspaces — author + `terraform validate` only; mark such steps `BLOCKED: Lane Nick`.
   LANE NICK plans freely and applies only from merged `main`, after a reviewed plan that shows no
   unintended change/destroy to live resources.
2. **Draft-only.** No tool that sends a real email/SMS/CRM write may run against real data —
   gate every send behind a Greenlight stub.
3. **Secrets never in the repo.** Secrets Manager / env refs only; respect `.gitignore` + `.stignore`.
   The confidential spec PDFs in `docs/` are gitignored — never publish them.
4. **Managed Agents is beta.** All agent-plane code goes behind `agents/runtime.py` (swappable);
   never assume an MA endpoint works without flagging "verify".
5. **Postgres RLS only works if FORCEd and connected as a non-owner role.** Get this wrong and
   tenant isolation silently fails. (Build Guide red box.)
6. **THE TRUST RULE.** Tenant identity comes ONLY from the verified Cognito JWT `custom:tenant_id`
   claim — never a header or request body. It is what gets pushed into `app.current_tenant`, Cube's
   security context, and the agent session metadata.
7. **Dashboards are spec-not-code.** Agents emit a declarative view-spec validated against
   `shared/schemas/view_spec.schema.json`; the renderer interprets only catalog components — never
   executable code.
8. **Provisioning fires only on the signed Stripe webhook**, and is idempotent + rollback-safe
   (a mid-failure parks the account in `provisioning_failed`). Verify email + phone before pay.

## Tenancy model (decided)
- One Anthropic **workspace per tenant** (vaults are workspace-scoped → isolation boundary).
- AWS side is the **lean pool**: one Aurora cluster, one Cognito pool; isolation via
  `tenant_id` column + RLS + JWT claim + cost tags. Not per-tenant AWS accounts.
- HIPAA tenants are a different runtime (Bedrock/1P fallback via the `runtime.py` seam), not a checkbox.

## Layout & conventions
- Monorepo; see `README.md` "Repo layout". Backend packages: `api/` (control plane + signup routes),
  `agents/`, `ingest/`, `semantic/`, `conv/` (conversational layer), `ml/` (Cortex), `signup/`
  (acquisition/provisioning), `shared/`, `db/`. Python 3.13+ for backend; React + TypeScript for `web/`.
- Side-effecting tools (send_email, update_deal, issue_quote) never execute — they route through the
  `api/control` Greenlight gate (autonomy L0–L3 + compliance + kill switch). Read-only tools auto-run.
- Stores set `app.current_tenant` before any DB access (`PgApprovalStore`/`PgSavedViewStore`/the ingest
  cursor) so RLS applies; the API binds the tenant from the verified claim per request.
- `AWS_REGION=us-east-1`, `PROJECT=uplift`. MA beta header on every Anthropic call:
  `anthropic-beta: managed-agents-2026-04-01`.
- Commit via short-lived lane PRs (`feat/nick-*` / `feat/matt-*`), squash-merge to `main`; on every
  commit keep the living docs current per the lane ownership rules —
  `README.md` + `CLAUDE.md` + `BUILD_STATUS.md` + `TODO.md` (update whichever the change affects;
  e.g. check off / add `TODO.md` items, refresh the live/demo/not-live status).
