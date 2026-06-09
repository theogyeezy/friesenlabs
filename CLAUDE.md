# CLAUDE.md — Uplift build context

This file orients any agent working in this repo. **On every commit/push, update all of the living
docs that the change touches — `README.md`, `CLAUDE.md`, `BUILD_STATUS.md`, and `TODO.md` — so they
never drift from reality.**

## What this is
Uplift: a multi-tenant agentic CRM. Hybrid architecture — **agent plane** on Claude Managed
Agents (beta), **everything else** on AWS. See `README.md` for the shape and `BUILD_STATUS.md`
for where the build currently is.

**Status:** all 13 phases (0–12) + the frontend are implemented and green (pytest 193, real
Postgres isolation gate in CI, `terraform validate` + `plan`, web build/typecheck/Playwright, smoke);
a final adversarial audit pass is merged. CI runs on `main`; trunk is **`main`** (branch via
short-lived `feat/…` PRs — see `CONTRIBUTING.md`).

**Live infra (real money) — the backend is LIVE end-to-end.** Applied to AWS (acct 186052668426,
us-east-1) under a $200 budget alarm. Live path: **browser → Amplify (Vite SPA) → CloudFront → ALB
(HTTP) → arm64 Fargate API → Aurora** (FORCE'd RLS) with real Cognito JWKS auth enforced. Verified:
`/api/healthz` 200, `/api/approvals` 401 (unauth rejected).
- 🟡 **Demo/mock:** the web UI runs in mock mode (`VITE_API_MOCK=1`) until a Cognito login flow exists
  to obtain a JWT; the real API is live at `/api`.
- ⛔ **Not live (parked):** the AI/agent plane (`agents/runtime.py` stub, `/chat` 503, noop executor —
  needs Anthropic creds); provisioning clients (`api/prod_deps.py` `_Stub`/`_Noop`, verify hardcoded off
  — needs Stripe/Resend/Admin creds); the cube/worker/observability/provisioning-Lambda/cortex modules
  (authored, unapplied).
- ⚠️ **Drift:** ALB / api_service / api_cdn / IAM secret policies / 4 SG rules were created out-of-band
  (CLI) and aren't in TF state; ECR `uplift-api` is MUTABLE. `terraform import` + reconcile before the
  next `apply`.
- **Ops:** state in **S3** (`uplift-tfstate-*`, KMS) — init `terraform init -backend-config=backend.hcl`
  (gitignored). API image `uplift-api:latest` (arm64); rebuild `docker build --platform linux/arm64`.
  DB migrate `python -m api.migrate` as a one-off Fargate task.
- **The full granular, prioritized work list (119 items, P0→P3) is in [`TODO.md`](./TODO.md)** — start
  at the login-flow critical path. Anything still un-applied / the AI plane is `BLOCKED: needs Nick`.

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
1. **No live cloud creation.** Author + `terraform validate` IaC; never `terraform apply`, never
   create live AWS resources or Anthropic workspaces. Mark such steps `BLOCKED: needs Nick`.
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
- Commit + push to `main`; on every commit keep the living docs current —
  `README.md` + `CLAUDE.md` + `BUILD_STATUS.md` + `TODO.md` (update whichever the change affects;
  e.g. check off / add `TODO.md` items, refresh the live/demo/not-live status).
