# Uplift

**A multi-tenant agentic CRM with a Moveworks-style conversational front door.**
You talk to it in natural language; a team of AI agents researches leads, drafts outreach,
quotes, follows up, and answers questions grounded in *your* tenant's data — with a human
approving anything that has side effects.

> _"Own your design, let Anthropic run the loop, keep the data in AWS, and make trust the feature."_

## Architecture (hybrid)

- **Agent plane** — Claude Managed Agents (Anthropic-hosted, **beta**): the reasoning loop and
  multi-agent coordination. Wrapped behind `agents/runtime.py` so the runtime is swappable.
- **Everything else** — your AWS account: the conversational front door, tool execution (in-VPC),
  the data plane (Aurora + pgvector + Cube + S3 + Redis), the control plane (autonomy, approvals,
  traces), and per-tenant ML.

Tenant isolation is **defense-in-depth**: microVM/session → credential vault → Postgres **RLS**
→ Cube security context. No single mistake leaks data.

## Repo layout

```
infra/      # Terraform (VPC, Aurora, ECS/Fargate, Cognito, ALB, budgets, Step Functions) — validated, not applied
api/        # FastAPI control plane: trust-rule auth, Greenlight/approvals, view CRUD, the action gate,
            #   signup + Stripe webhook routes; control/ = autonomy L0–L3, compliance, traces, kill switch
agents/     # agent + coordinator definitions AS CODE
  runtime.py  #   swappable agent-runtime adapter (Managed Agents today, behind the seam)
  roster/     #   scout, nadia, margo, ledger, echo, pip, critic
  tools/      #   query_cube, search_rag, read_crm, draft_email, build_view, run_model (+ always_ask sends)
worker/     # self-hosted tool-execution worker (ECS Fargate; env-key only)
ingest/     # connectors + chunk + embed pipeline (Titan → pgvector), incremental cursor
semantic/   # Cube schema (cubes, metrics) + the tenant security context
conv/       # conversational layer: slot resolution, agentic RAG + citations, analytics, session facade
ml/         # Cortex: per-tenant training, registry, champion/challenger gate, retrain
signup/     # acquisition: accounts, Stripe payment, idempotent rollback-safe provisioning, funnel
web/        # React + TypeScript app: chat dock + dashboard renderer + Greenlight UI + signup funnel
shared/     # view-spec JSON schema, config, cost model
db/         # schema.sql (FORCE'd RLS) + roles.sql (crm_app non-owner)
tests/      # unit + integration (pytest)
scripts/    # smoke_all.sh, isolation_test.py, demo.sh, per-feature smokes, briefs/
docs/       # spec PDFs (gitignored — confidential, kept local only)
```

## Build status

**All 13 phases (0–12) + the frontend are implemented and green** — see
**[BUILD_STATUS.md](./BUILD_STATUS.md)** for the per-phase / per-feature map with test + review status.
Everything that can be built and tested offline is done, and a final adversarial audit pass is merged.

### Live deployment status — live / demo / not live

| State | Component | Why | Unblocked by |
|-------|-----------|-----|--------------|
| ✅ **Live & working** | Amplify → CloudFront → ALB → arm64 Fargate API → Aurora (FORCE'd RLS); Cognito JWKS auth | deployed + **verified** (`/healthz` 200, unauth API 401) | — |
| ✅ **Live & working** | Web UI with real login (Hosted-UI PKCE, `web/src/auth/`) | browser-verified end-to-end | — |
| ✅ **Live & working** | GHL-energy marketing landing, **mobile-first**: hamburger nav, sticky CTA, interactive hero roster, vs-GoHighLevel radar, live ROI calculator | browser-verified desktop + 390px mobile | — |
| ✅ **Live & working** | Edge hardening: X-Origin-Verify shared secret (CloudFront → ALB 403-default) | applied two-phase, zero downtime | — |
| ✅ **Live & working** | Cube semantic service (1/1, `/readyz` 200 internally) | digest-pinned, memory driver (Cube 1.x), SG self-rule | data model image (semantic/ bake) next |
| ✅ **Live & working** | Observability: 4 CloudWatch alarms + SNS + billing-alarm action + `uplift-live` dashboard; budget notification subscribed | applied | email sub PendingConfirmation (click) |
| ✅ **Live & working** | Audit: CloudTrail scoped S3 data events; ALB access logs (encrypted bucket, delivering) | applied + verified | — |
| ✅ **Live & working** | crm-app-db secrets rotation (30-day, controlled-window procedure) | rotation executed + services rolled + verified | — |
| ✅ **Live & working** | CI/CD: OIDC deploy pipeline (build→plan→approved apply→roll) | **proven end-to-end**; prod runs current `main` | — |
| ✅ **Live & working** | Cloud Map (`cube.uplift.local`) + cube semantic model + ECS Exec | verified end-to-end | — |
| ✅ **Live & working** | Provisioning Lambda + Step Functions (pinned ARNs, idempotent executions) | applied + smoked all-stub | signup go-live values (Stripe/Resend/admin key) |
| 🟙 **Half-live** | AI / agent plane: MA environment live, org key + env-id on the API task, `/chat` reaches auth (401) | MA SDK shapes verified; env `uplift-prod` created | env-key Console click (worker), conversation wiring (app) |
| 🟙 **Authored, gated** | Ingest scheduler (nightly EventBridge → Fargate `run_sync`) | applied, rule DISABLED | `ingest_tenants` + enable flip |
| ⛔ **Not live** | Worker service; signup real-deps (Stripe/Resend/Cognito admin) | gated flags off | env-key + webhook-secret + admin-key values |
| 🟙 **Pending** | friesenlabs.com TLS (Route53 zone + wildcard ACM applied) | cert PENDING_VALIDATION | Squarespace NS cutover, then ALB TLS cutover |

Applied to AWS account 186052668426 (us-east-1) under a $200 budget alarm; Terraform state in S3 (KMS).
**Security:** a 37-agent adversarial audit (2026-06-09) found + we **fixed a critical cross-tenant data leak** (the request-path stores shared one DB connection + a session-level tenant GUC, racing across the threadpool) — now pooled per-request connections + `SET LOCAL`, proven on live Aurora under concurrency. Aurora durability (deletion protection + 7-day backups) on. The remaining 25 findings (2 high, 7 medium, 17 low) are tracked in TODO.md.

The full granular, prioritized work list (119 items, P0→P3) and the critical path to a fully-real
product (login flow first) live in **[TODO.md](./TODO.md)**. Tear down with `cd infra && terraform destroy`.

CI (`.github/workflows/ci.yml`) runs on every push/PR to **`main`** (the trunk): pytest + a real
Postgres+pgvector isolation gate, `terraform fmt`/`validate`, and the web build + typecheck +
Playwright. See [CONTRIBUTING.md](./CONTRIBUTING.md) for the branching model.

## Claude Code tooling
This repo ships **`.claude/settings.json`** with `enabledPlugins` for the official-marketplace plugins
used in development (code-review, superpowers, feature-dev, and the AWS toolkits). When you clone and
**trust** the repo in Claude Code, you'll be prompted to install that same set; the skills bundled in
those plugins come along. Repo-local skills (if any) live in `.claude/skills/` and load automatically.

## Safety constraints (in force)

- **No live cloud creation.** All IaC is authored and `terraform validate`-clean; nothing is
  `apply`-ed. Steps needing live AWS are marked `BLOCKED: needs Nick` in BUILD_STATUS.md.
- **Draft-only.** No real email/SMS/CRM write executes against real data — all sends are gated
  behind Greenlight stubs.
- **Secrets** live in AWS Secrets Manager / env refs, never in the repo.

## Developing

```bash
# Python (api / agents / ingest / ml / tests)
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q

# Infra
cd infra && terraform fmt -check && terraform validate

# Web
cd web && npm install && npm run build
npx playwright test            # e2e (headless)

# Roll-ups
bash scripts/smoke_all.sh
python scripts/isolation_test.py
```
