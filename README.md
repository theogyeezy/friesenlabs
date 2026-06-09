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
| ✅ **Live & working** | Amplify → CloudFront → ALB → arm64 Fargate API → Aurora (FORCE'd RLS); Cognito JWKS auth | deployed + **verified** (`/api/healthz` 200, `/api/approvals` 401) | — |
| ✅ **Live & working** | Web UI with real login (Hosted-UI PKCE, `web/src/auth/`) | deployed real-mode build, **browser-verified end-to-end**: sign-in gate → Hosted UI → code exchange → app shell → real RLS-scoped rows from Aurora | — |
| ⛔ **Not live** | AI / agent plane (chat, tools) | `runtime.py` stub, `/chat` 503, noop executor | Anthropic Managed Agents creds |
| ⛔ **Not live** | cube/worker, observability, provisioning (Stripe/Resend), cortex | authored, not applied / stubbed | deploy + Stripe/Resend/Admin creds |
| ✅ **Reconciled** | Terraform state | out-of-band SG rules imported, ECR back to IMMUTABLE; full plan = 0 change/destroy to live resources (only the unapplied modules show as adds) | — |

Applied to AWS account 186052668426 (us-east-1) under a $200 budget alarm; Terraform state in S3 (KMS).
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
