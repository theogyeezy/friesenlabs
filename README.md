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
infra/      # Terraform (VPC, Aurora, ECS, Cognito, budgets) — authored + validated, not applied
api/        # FastAPI control plane (auth, Greenlight, view CRUD)
agents/     # agent + coordinator definitions AS CODE
  runtime.py  #   swappable agent-runtime adapter (Managed Agents today)
  roster/     #   scout, nadia, margo, ledger, echo, pip, critic
  tools/      #   query_cube, search_rag, read_crm, draft_email, run_model
worker/     # self-hosted tool-execution worker (ECS Fargate)
ingest/     # connectors + chunk + embed pipeline (Titan → pgvector)
semantic/   # Cube schema (cubes, metrics, security context)
ml/         # Cortex: per-tenant training, registry, retrain
web/        # React + TypeScript app: chat dock + dashboard renderer + Greenlight UI
shared/     # schemas (view-spec JSON schema, event types), config
tests/      # unit + integration (pytest)
scripts/    # smoke_all.sh, isolation_test.py, per-feature smokes
docs/       # spec PDFs (gitignored — confidential, kept local only)
```

## Build status

See **[BUILD_STATUS.md](./BUILD_STATUS.md)** for the per-phase / per-feature map (done /
in-progress / blocked) with test + review status. Build proceeds in dependency order
(Phase 0 → 12). The full ordered manual is the Build Guide (kept local in `docs/`, not published).

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
