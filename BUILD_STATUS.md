# Uplift — Build Status

Multi-tenant agentic CRM with a Moveworks-style conversational front door.
Hybrid architecture: **agent plane** = Claude Managed Agents (beta, behind a swappable
adapter); **everything else** = AWS (data plane, control plane, app, ML).

Source of truth: `docs/uplift-build-guide.pdf` (Build Guide, Phases 0–12) and the
Architecture Design doc. Build in **dependency order**, not feature order.

> **Environment note:** This build runs **solo** on one machine (no SSH fleet / Syncthing
> tree — those parts of the original brief don't exist here). Parallel fan-out is done with
> local subagents / Workflow. Repo: `friesenlabs` (public GitHub).
>
> **Hard safety gates (in force):**
> - **No `terraform apply` / no live cloud creation** — IaC is authored + validated only.
>   Steps that need live AWS are marked `BLOCKED: needs Nick (creds/cost)`.
> - **Draft-only** — no tool that sends real email/SMS/CRM writes runs against real data;
>   all sends gated behind Greenlight stubs.
> - **Secrets** via Secrets Manager / env refs — never committed (`.gitignore` + `.stignore`).
> - Managed Agents is **beta** — agent-plane code lives behind `agents/runtime.py`.

## Legend
status: ✅ done · 🟡 in-progress · ⛔ blocked · ⬜ not-started
tests: U=unit · I=integration · S=smoke · E=e2e(Playwright) · X=isolation — (✓ pass / · n/a / ✗ fail / ? pending)

## Phase map

| # | Phase | Status | Owner | U | I | S | E | X | Review |
|---|-------|--------|-------|---|---|---|---|---|--------|
| — | Foundation (scaffold, harness, BUILD_STATUS) | ✅ | orchestrator | ✓ | · | ✓ | · | · | self ✓ |
| 0 | AWS Foundation (IAM, VPC, SGs, secrets, ECR, baseline) | ✅* | orchestrator | · | · | · | · | · | self ✓ |
| 1 | Data Plane (Aurora+pgvector, RLS, schema, S3, Redis) | ⬜ | — | · | · | · | · | · | — |
| 2 | Ingestion & Embeddings (connectors, chunk, Titan, pipeline) | ⬜ | — | · | · | · | · | · | — |
| 3 | Semantic Layer (Cube deploy, metrics, tenant security ctx) | ⬜ | — | · | · | · | · | · | — |
| 4 | Agent Plane (Managed Agents, roster, vaults, worker) | ⬜ | — | · | · | · | · | · | — |
| 5 | Control Plane (autonomy, Greenlight, traces, kill switch) | ⬜ | — | · | · | · | · | · | — |
| 6 | Conversational Layer (front door, slots, agentic RAG+cites) | ⬜ | — | · | · | · | · | · | — |
| 7 | Dashboard Engine (view-spec, generate, render, save/edit) | ⬜ | — | · | · | · | · | · | — |
| 8 | Cortex / ML (per-tenant models, train, registry, retrain) | ⬜ | — | · | · | · | · | · | — |
| 9 | App, Auth & API (Cognito, FastAPI/Fargate, ALB, web) | ⬜ | — | · | · | · | · | · | — |
| 10 | Acquisition, Signup & Provisioning (landing, Stripe, auto-provision) | ⬜ | — | · | · | · | · | · | — |
| 11 | Cost, Guardrails & Observability (budgets, caps, CloudWatch, OTEL) | ⬜ | — | · | · | · | · | · | — |
| 12 | IaC, CI/CD & Launch (Terraform/CDK, pipelines, smoke+isolation) | ⬜ | — | · | · | · | · | · | — |
| FE | Frontend: convert ~50 JSX → React+TS app in `web/` | ⬜ | — | · | · | · | · | · | — |

`✅*` = code complete + `terraform validate`-clean; **apply BLOCKED: needs Nick** (cost/irreversible).

## Blocked — needs Nick (creds / cost / external accounts)
*(populated as we hit live-cloud steps; nothing executed against real AWS by design)*
- `terraform apply` for all of `infra/` — authored + `validate`-clean, but never applied (cost/irreversible).
- **Org-level Phase 0 items** authored-as-notes only (need an AWS Org context): AWS Config recorder +
  delivery channel, and the SCP denying CloudTrail/Config disablement. Account-level baseline
  (CloudTrail + S3 block-public-access) IS authored in `infra/modules/baseline`.
- IAM Identity Center (SSO) Admins permission set — console/SSO-stack step, not in this Terraform.

## Cycle log
- **Cycle 1** — repo scaffold (monorepo layout per Build Guide §Step 4), Python venv +
  pytest harness, Playwright config, `scripts/` (smoke_all, isolation_test stub), root
  README + CLAUDE.md, `.gitignore`/`.stignore` (secrets + confidential PDFs excluded).
  Began Phase 0 Terraform authoring. Frontend identified (~50 JSX in `Friesen Labs/`),
  queued for React+TS conversion.
