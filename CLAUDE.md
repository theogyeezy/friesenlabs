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
app shell → real RLS-scoped tenant rows. Unauth `/api/*` → 401; `/chat` → graceful 503 (AI parked).
- ✅ **Login:** Cognito Hosted UI (`uplift-<acct>.auth.us-east-1.amazoncognito.com`) + hand-rolled
  PKCE in `web/src/auth/` (no auth SDK). Demo user creds live in Secrets Manager `uplift/demo-user`
  (tenant seeded via `scripts/seed_demo_tenant.py` as a one-off Fargate task, run as `crm_app`).
  Note: Amplify 301s `/auth/callback` → `/auth/callback/` — the path match tolerates the slash.
- ⛔ **Not live (parked):** the AI/agent plane (`agents/runtime.py` stub, `/chat` 503, noop executor —
  needs Anthropic creds); provisioning clients (`api/prod_deps.py` `_Stub`/`_Noop`, verify hardcoded off
  — needs Stripe/Resend/Admin creds); the cube/worker/observability/provisioning-Lambda/cortex modules
  (authored, unapplied).
- ✅ **State reconciled:** out-of-band SG rules imported, budget untainted, ECR `uplift-api` back to
  **IMMUTABLE** (push new image versions as new tags, not `:latest` overwrites). Full plan: 0
  change/destroy to live resources; only the unapplied modules show as adds. Live var values load from
  gitignored `infra/prod.auto.tfvars`.
- **Ops:** state in **S3** (`uplift-tfstate-*`, KMS) — init `terraform init -backend-config=backend.hcl`
  (gitignored). API image (arm64): `docker build --platform linux/arm64`. DB migrate
  `python -m api.migrate`; tenant seed `scripts/seed_demo_tenant.py` — both as one-off Fargate tasks.
- **Security:** a 37-agent adversarial audit (2026-06-09) is folded into `TODO.md` (27 findings). A
  **critical cross-tenant leak is FIXED** — the request-path stores (`PgApprovalStore`/`PgSavedViewStore`)
  now use a per-request pooled connection + `SET LOCAL app.current_tenant` in a transaction (NOT a
  shared connection + session-level SET, which raced across the anyio threadpool). Tenant is threaded
  explicitly; proven on live Aurora (16 threads) + CI real-Postgres. **Follow-up:** the `ingest_cursor`
  stores (`ingest/pipeline.py`) share the old single-conn pattern but run off the request path.
- **The full granular, prioritized work list is in [`TODO.md`](./TODO.md)**. Next big chunks: AI plane
  (needs Anthropic creds), provisioning (needs Stripe/Resend), cube/worker/observability deploys.

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
