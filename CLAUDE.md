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
  scheduler applied DISABLED **(→ ENABLED 2026-06-12 via REQ-012: nightly rule on,
  `ingest_tenants="auto"` vault-slot discovery, `INGEST_REAL_STORES` on the api task)**;
  prod isolation gate PASSED live as `crm_app`; baseline plan CLEAN.
- ✅ **AI/agent plane LIVE + verified (2026-06-10):** MA env `uplift-prod` (env_012JvqRKUZzUDeH3Gse6TBgZ)
  live; org key + env-id + `SIGNUP_REAL_DEPS=1` on the API task (rev 10); the real
  `signup.agent_plane.AgentPlaneEnsure` is wired (not `_Noop`). `scripts/verify_agent_plane.py` PASSED
  live: provision 7 specialists + coordinator → coordinator answer + delegation → Greenlight
  approve/execute with the **draft-only guarantee held** (no real send). **Worker 2/2 polling; cube
  live.** A RAG-embed IAM gap (`bedrock:InvokeModel` on Titan, missing from the api+worker roles) was
  caught by the verify and FIXED live. Grounding plumbing is green (no-uncited-claim invariant holds);
  **knowledge P0s SHIPPED + DEPLOYED 2026-06-12 (#251 on `uplift-api:414e82c`):** customers
  self-populate the corpus (Knowledge → Add document → `POST /knowledge/documents`, chunk→Titan
  embed→RLS upsert; the `INGEST_REAL_STORES` gate is applied), live citations carry real
  `ref_id`s (the `doc:0` placeholder bug is fixed), and every `/chat` turn reports
  `grounding_status` + `retrieved_count`. The demo tenant's corpus was SEEDED 2026-06-12 (26 kb docs,
  Titan V2, one-off task; retrieval verified in-VPC — top hit `demo:kb:pricing-discount-authority#0`).
- ✅ **Self-upgrading rosters + orphan GC LIVE (2026-06-14, #360/#361/#362/#363, verified live):** MA
  agents are created once with the code's specs frozen in, so a spec change never reached existing
  tenants (the live `draft_email` bug). `tenant_workspaces.roster_version` now stamps the spec hash
  (`agents/provisioning.current_roster_version`); the conversation factory transparently re-provisions
  + starts a fresh session whenever the stamp is stale — each tenant self-heals on its next chat after
  a deploy (per-tenant locked, failure backoff, B1 session-invalidation). A **cross-process upgrade
  claim** (compare-and-set `upsert_coordinator_if_version`, NULL-safe) makes two api tasks upgrading at
  deploy time exactly-once (no coordinator flip-flop; the loser serves the winner + records its
  orphan). Superseded rosters are logged to the RLS-EXEMPT `retired_rosters` ledger and reaped by
  `scripts/ops/reap_orphan_agents.py` + the weekly **`reap.yml`** (archive after a grace window; MA has
  no hard delete — agents are **archived**, which frees the active slot). Safe by construction (only
  system-recorded superseded coordinators; unique specialist ids; grace window). **Live-verified:** the
  pre-existing demo orphan (coordinator + 7 specialists) reaped, demo's current coordinator untouched,
  active agents 36→28; live-caught two beta SDK shapes (`agents.list` returns reference objects;
  archive-not-delete). See `agent-provisioning-create-once` memory.
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
- ✅ **Security release-readiness audit + remediation LIVE (2026-06-11 audit → 2026-06-12 applied;
  `docs/audits/security-audit-2026-06-11.md`, REQ-013):** 5 parallel deep-dives + Semgrep verified
  the core invariants hold; the P0/P1/P2 fixes merged (#290) + deployed. Now live: **intra-tenant
  RBAC** — a `cognito:groups` admin gate over the 8 privileged writes (kill switch, autonomy, billing
  portal, modules, GDPR export/delete, settings, approval-decide; reads stay open), `RBAC_STRICT=1`
  removes the empty-groups=admin back-compat, global kill-switch operators are user-granular, and
  provisioning bootstraps a tenant's first user into `admin` (RBAC verified end-to-end on the live
  API — admin→200, no-group→403). **Compliance floor moved into `Greenlight.propose`** (TCPA/CAN-SPAM
  on every propose path + post-edit re-validation); **prompt-injection delimiters** around RAG/CRM/lead
  content; **Vega chart-fragment allow-list** (3 mirrors). Edge/infra: the GitHub OIDC deploy role is
  scoped (**`AdministratorAccess` detached**); `ALLOW_ADMIN_USER_PASSWORD_AUTH` removed; Cognito
  advanced-security ENFORCED; VPC flow logs + WAF logging + ECS-exec session logging; cube SG split;
  SPA CSP/security headers (`customHttp.yml`); Pg single-use email tokens; worker org-key guard;
  PII-masked logs. **Still owner-gated/deferred:** Aurora CMK (window), ADOT digest pin + read-only
  rootfs, Turnstile CAPTCHA (validators wired, site/secret pending), broader-user RBAC assignment.
**Tooling:** `.claude/settings.json` enables the official-marketplace plugins so collaborators inherit
them on clone+trust. Don't commit secrets to it.

## FLEETAGENT session — 2026-06-10/11 (revenue, accountability, data-plane, MVP)
A multi-agent fleet (3 Claude accounts + Codex over Tailscale/SSH; see `scripts/fleet/`) ran an
adversarial audit then a 4-wave build. **22 PRs squash-merged to `main` (green), tip `20384e9`.**
- **Revenue path made real:** checkout returns the Stripe `checkout_url` (SPA no longer fakes
  payment), `invoice.paid` resolves via subscription metadata, atomic per-account settlement +
  signed-webhook field verification (amount/price/livemode), pre-minted **workspace-key pool**
  (material in Secrets Manager, only a ref in PG), Cognito `FORCE_CHANGE_PASSWORD` login fix,
  `POST /public/leads`, and an env-gated **@friesenlabs.com Stripe bypass** (off by default;
  `SIGNUP_INTERNAL_BYPASS_DOMAINS` + prod escape-hatch; settles via `internal_comp` through the
  idempotent ledger). Stripe test-mode Prices created: starter `price_1Tgnl3…` $99, team
  `price_1Tgnl4…` $299, scale `price_1Tgnl5…` $799 (monthly).
- **Accountability is real, not theater:** persisted kill switch + `GET/PUT /control/killswitch`,
  `PgTraceStore` over the FORCE-RLS traces table + `GET /control/traces`, persisted autonomy dial the
  gate actually reads. Web wired with 404-degrade.
- **Data plane un-severed:** Cube RLS GUC fix (issue #177 — needs the cube image rolled to go live),
  real worker Cube client, 12/12 tools served, live-runtime citations.
- **Tenancy hygiene:** fresh-load grants for `workspace_keys`/`leads`, composite same-tenant FKs,
  append-only audit trail (REVOKE DELETE), schema-derived RLS gate, AdminSetUserPassword IAM parity,
  ingest shared-token fallback removed.
- **MVP features (branches `feat/mvp-*` PRESERVED on origin for further dev):** **Balto** NL view
  creation in chat ("Our synthesizing agent Balto is mushing away…", data-not-exists refusal, view
  button + X overlay, save option, saved-views dropdown; spec-not-code over Cube), **Agent Studio**
  + 5 starter playbooks, **connectors** (CSV/GoHighLevel/Stripe-read), **dashboards v2** (funnel/
  leaderboard/sparkline/cohort/grid), **Cortex depth** (training loader, retrain entrypoint, signed
  artifacts, live drift), demo-tenant golden path + knowledge-corpus seeding.
- **Live applies — DONE + DEPLOYED (2026-06-11):** DB migrate ran live against Aurora via
  `uplift-migrate-oneoff:2` (new schema + roles/grants) — **exit 0**; live isolation test **PASS**.
  Then the full Deploy **succeeded** (after two fix-forwards): api `:11→:12`, **cube rolled to the
  #177 RLS-fix image** (steady state), worker on the data-plane image, provisioning Lambda on the
  ARN-fetch image; edge `/healthz` 200. Two deploy-time bugs were found + fixed: (1) the non-api
  images weren't built by `deploy.yml` → added `.github/workflows/build-images.yml` (cube/
  provisioning/worker); (2) those builds were wrong — **cube needed amd64** (Fargate) not arm64, and
  the **provisioning Lambda needs a Docker-v2 manifest** (`--provenance=false`), not buildx's OCI
  index. Stripe TEST price IDs + corrected image tags were written into the `PROD_AUTO_TFVARS_B64`
  secret. The whole FLEETAGENT backend (revenue/accountability/data-plane/tenancy) is now running live.
- **Customer-readiness wave (2026-06-11, all merged):** **auth recovery** (Cognito Hosted-UI
  forgot/change-password, trust-rule upheld), **Stripe billing portal** (change card/cancel/invoices
  + cancellation webhook), **support surface** (`/public/support` + in-app help + `/status` page),
  **signup abuse controls** (disposable-email block, per-IP velocity, captcha seam), **per-tenant
  rate limits + plan-tier usage quotas + Anthropic cost attribution**, **first-run onboarding**
  (empty states, guided checklist, one-click load-sample), and a **landing "Provision my instance"
  fix** (was a fake animation that threw — now starts the real signup). Plus a latent-bug fix:
  **lazy DB connection pools** (`minconn=1`) — the stores were eagerly opening the full 10-conn pool
  each, hoarding ~180 idle Aurora connections (and exhausting CI Postgres).
- **Customer-readiness DEPLOYED (2026-06-11):** a second Deploy rolled the api `:12→:13` from
  `f9b2df2` — all customer-readiness + MVP backend is live; the 4 new tables (`support_requests`,
  `onboarding_state`, `usage_counters`, `cost_events`) + grants migrated live (isolation PASS),
  `/healthz` 200; web UI ships via Amplify. Plus follow-up fixes: lazy DB pools `minconn=1` (#213)
  and the `support_requests` grant (#215).
- **First `/fleet` agent-skill run (#217):** the v2 fleet skill (model-tiers-per-lane) added 77 unit
  tests across billing/support/limits/leads (4/4 confirmed by the 3-skeptic panel, boss-merged green).
- **Still owner-gated (last mile to real paid customers):** **seed the workspace-key pool**
  (Anthropic Console pre-mint → `scripts/ops/load_workspace_keys.py`) — until then real provisioning
  parks `pool_empty` (the @friesenlabs.com test-bypass + demo path work without it). (Cortex) live S3
  registry + a real retrain + seeded knowledge corpus. Legal/Terms/Privacy pages + the
  placeholder-501(c)(3) landing copy are still open (deliberately deferred).

## Build principle — ship complete, never partial (Matt, 2026-06-13)
**Anytime you build, the product must be fully fleshed out, wired, integrated, and implemented
end to end — backend, frontend, and cloud where necessary — so it works with no human intervention
or manual promotion needed. Write clean, scalable, enterprise-grade code.**

A feature is NOT done if any layer is stubbed, a wire is left dangling, a TODO defers the real
implementation, or a manual step is required to make it function. "Works on my branch with a mock"
is not works. Carry every change through all the layers it touches (DB/migration → API → agent/tool
wiring → frontend → infra/config) and verify it runs, not just compiles. The ONLY acceptable
remaining manual steps are things genuinely external to the code (registering a third-party app,
an owner-gated production deploy, seeding a real secret) — and those must be called out explicitly,
not left implicit.

> Matt's words: "Make sure anytime you build, the product is fully fleshed out, wired, integrated
> and implemented with backend, frontend and cloud if necessary. No human intervention or promotion
> should be needed. Write clean scalable enterprise code."

## How we build
- **Dependency order, not feature order.** Phase 0 → 12. Don't start a phase whose inputs
  don't exist. The Build Guide (`docs/`, local-only) is the source of truth for order + commands.
- **Test every step — the trophy. `TESTING.md` is the source of truth for layers, commands, and
  WHICH to run for a given change.** A unit isn't done until its applicable levels pass:
  - **static** — `web` ESLint (`npm run lint`) + `tsc` (`npm run typecheck`); `python -c import`.
  - **unit + component** — Vitest + Testing Library for `web/src` (`npm run test:unit`, the bulk of
    UI logic); pytest for the backend.
  - **integration** — `tests/integration/` + the real-Postgres multi-tenant isolation gate
    (`scripts/isolation_test.py`, after any data/agent/auth change).
  - **e2e + a11y** — Playwright real-Chromium flows (`web/e2e/`) including the axe accessibility
    spec; both run in CI.
  - **opt-in** — visual regression (`npm run test:visual`, CI-generated baselines) and the
    post-deploy production smoke (`PROD_SMOKE_URL=… npm run test:smoke`, the live-site check
    mock e2e can't do).
  - basics: `terraform validate/fmt`, `npm run build`.
  Don't claim a UI works off backend tests alone — run the component test and the screen's e2e spec.
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
