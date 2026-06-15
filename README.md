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
  fleet/      #   fleet dispatch kit (boss-on-Studio → Codex/MBP/mini workers) — see docs/fleet/
docs/       # spec PDFs (gitignored — confidential, kept local only)
  fleet/      #   fleet design: README + system diagram (committed)
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
| ✅ **Live & working** | **"Editorial & warm"** marketing landing (Fraunces serif, warm-clay accent, hairline cards, bespoke product-grounded icons; Apple-style hero load-in + staggered reveals + parallax, product-window demos, vs-GoHighLevel radar, ROI calculator, founder photos). Code-split first-load **~247KB gz** (was ~560); generated **og:image** card | **Lighthouse ~100** (a11y/SEO/best-practices/agentic) browser-verified desktop + mobile | — |
| ✅ **Live & working** | Edge hardening: X-Origin-Verify shared secret (CloudFront → ALB 403-default) | applied two-phase, zero downtime | — |
| ✅ **Live & working** | Cube semantic service (1/1, `/readyz` 200 internally) | digest-pinned, memory driver (Cube 1.x), SG self-rule | data model image (semantic/ bake) next |
| ✅ **Live & working** | Observability: 5 CloudWatch alarms + SNS + billing-alarm action + `uplift-live` dashboard; GuardDuty + Config | applied; **SNS email CONFIRMED** (alarms page the owner) | — |
| ✅ **Live & working** | Audit: CloudTrail scoped S3 data events; ALB access logs (encrypted bucket, delivering) | applied + verified | — |
| ✅ **Live & working** | Security hardening: CloudFront WAFv2 (managed rules + rate limit) + access logging + HSTS + PriceClass_100; ECS circuit breakers; ECR lifecycle; AWS provider pin | applied + verified | — |
| ✅ **Live & working** | Security-audit remediation (REQ-013, 2026-06-12): intra-tenant **RBAC** (`cognito:groups` admin gate, `RBAC_STRICT=1`); scoped CI/CD deploy role (**`AdministratorAccess` detached**); Cognito advanced-security ENFORCED; VPC flow logs + WAF logging + ECS-exec session logging; cube SG split; SPA CSP/security headers | applied + **RBAC verified end-to-end on the live API** (admin→200, no-group→403) | Aurora CMK, ADOT digest pin, read-only rootfs, Turnstile CAPTCHA deferred |
| ✅ **Live & working** | crm-app-db secrets rotation (30-day, controlled-window procedure) | rotation executed + services rolled + verified | — |
| ✅ **Live & working** | CI/CD: OIDC deploy pipeline (build→plan→approved apply→roll) | **proven end-to-end**; prod runs current `main` | — |
| ✅ **Live & working** | Cloud Map (`cube.uplift.local`) + cube semantic model + ECS Exec | verified end-to-end | — |
| ✅ **Live & working** | Provisioning Lambda + Step Functions; **signup real-deps flipped** (Stripe/Resend/Anthropic-admin/webhook secrets on the API task, real clients wired — no `_Stub`/`_Noop`) | `signup_real_deps` live | — |
| ✅ **Live & working** | AI / agent plane: provisions a 7-agent roster + coordinator, `/chat` answers + delegates, draft-only Greenlight held; worker **2/2 polling** | `scripts/verify_agent_plane.py` PASSED live 2026-06-10; caught + fixed a `bedrock:InvokeModel` (Titan embed) IAM gap | demo corpus seeded 2026-06-12 (retrieval verified); since #251 (deployed 2026-06-12) customers self-populate via Knowledge → Add document, citations carry real `ref_id`s, and `/chat` reports `grounding_status` |
| ✅ **Live & working** | Self-upgrading rosters + orphan GC (#360–#363): a `roster_version` stamp re-provisions stale tenants on their next chat (per-tenant locked, **cross-process compare-and-set** claim so concurrent api tasks are exactly-once); superseded rosters land in the RLS-exempt `retired_rosters` ledger and are **archived** by `scripts/ops/reap_orphan_agents.py` + the weekly `reap.yml` (safe-by-construction, grace-windowed) | verified live 2026-06-14: the pre-existing demo orphan (coordinator + 7 specialists) reaped, demo's live coordinator untouched (active agents 36→28); live-caught 2 beta SDK shapes | — |
| ✅ **Live & working** | FLEETAGENT backend deployed (2026-06-11): revenue path (real checkout URL, atomic settlement + webhook field verification, workspace-key pool→Secrets Manager, Cognito login fix, `/public/leads`, @friesenlabs.com test-bypass); accountability (kill switch / traces / autonomy persisted + routed); data-plane (**Cube RLS GUC fix #177 LIVE**, worker cube client, citations); tenancy hygiene | Deploy succeeded — api `:12`, cube on the #177 image (steady state), worker + provisioning Lambda rolled; `/healthz` 200; live migrate + isolation PASS | — |
| ✅ **Live (backend deployed)** | MVP features: **Balto** NL view-creation in chat, **Agent Studio** + 5 playbooks, **connectors** (CSV/GoHighLevel/Stripe-read), **dashboards v2**, **Cortex depth** (loader/retrain/signed artifacts/drift), demo-tenant + knowledge-corpus seed | backend on api `:13`; branches `feat/mvp-*` preserved for further dev | (Cortex) live S3 registry + seeded corpus; web UI via Amplify |
| ✅ **Live (deployed)** | Customer-readiness: **auth recovery** (Cognito forgot/change-password), **Stripe billing portal** (change card/cancel/invoices + webhook), **support** (`/public/support` + help + `/status`), **signup abuse controls**, **per-tenant rate limits + usage quotas + cost attribution**, **first-run onboarding** (empty states + load-sample), landing provision-CTA fix; plus lazy DB pools (`minconn=1`) | Deploy succeeded — api `:13`, the 4 new tables migrated + isolation PASS, `/healthz` 200; web UI via Amplify | seed the workspace-key pool (Console) for real provisioning |
| 🟙 **Authored, gated** | Ingest scheduler (nightly EventBridge → Fargate `run_sync`) | applied, rule DISABLED | `ingest_tenants` + enable flip |
| ⛔ **Not live** | Cortex retrain pipeline; API → 2-task HA + autoscaling | authored `validate`-clean / cost-parked vs the $200 ceiling | cortex job unapplied; API-scale flip when the ceiling moves |
| ✅ **Live & working** | **https://friesenlabs.com** (apex + www) on the uplift-web Amplify app — NS cutover DONE, wildcard ACM ISSUED, domain association AVAILABLE (after evicting a stale us-east-2 Amplify app off the CNAMEs) | verified live: 200 over the `*.friesenlabs.com` cert, correct landing page | — |
| ✅ **Live & working** | `api.friesenlabs.com` TLS at the ALB (sweep-executed RUNBOOK cutover): 443 + real ACM cert + 403-default origin-verify; api_cdn origin https-only; :80 redirect-only | verified: direct-ALB 403, edge + SPA `/api` healthz 200 | — |

Applied to AWS account 186052668426 (us-east-1) under a $200 budget alarm; Terraform state in S3 (KMS). Edge hardened with WAFv2 (managed rules + rate limit), HSTS, and access logging; Cognito is provisioning-only with deletion protection.
**Security:** a 37-agent adversarial audit (2026-06-09) found + we **fixed a critical cross-tenant data leak** (the request-path stores shared one DB connection + a session-level tenant GUC, racing across the threadpool) — now pooled per-request connections + `SET LOCAL`, proven on live Aurora under concurrency. Aurora durability (deletion protection + 7-day backups) on.

A second **release-readiness audit (2026-06-11)** ran 5 parallel deep-dives (auth/tenancy/RLS · signup/billing · agent plane/Greenlight · web/public/ingest · Terraform) + Semgrep and verified the core invariants hold in code (Trust Rule, FORCEd-RLS-`SET LOCAL`, draft-only Greenlight, TOCTOU-safe approvals, the Stripe trust model, spec-not-code, key-pool/secrets hygiene). Its P0/P1/P2 remediation is **merged + live (2026-06-12)** — full report: `docs/audits/security-audit-2026-06-11.md`:
- **Intra-tenant RBAC** — tenant identity already comes only from the JWT, but admin authority now does too: a `cognito:groups` admin gate over the 8 privileged writes (kill switch, autonomy, billing portal, module entitlements, GDPR export/delete, settings, approval-decide), reads stay open; `RBAC_STRICT=1` is live (group-less users are no longer auto-admin); global kill-switch operators are user-granular. Provisioning bootstraps a tenant's first user into `admin`.
- **Compliance floor inside `Greenlight.propose`** (TCPA/CAN-SPAM on every propose path — worker/Sidecar/playbooks, not just the gate) + post-edit re-validation; **prompt-injection delimiters** around RAG/CRM/lead content fed to agents; **Vega chart-fragment allow-list** (the one spec-not-code gap closed) across all three schema mirrors.
- **Edge/infra (REQ-013, applied live):** the GitHub OIDC deploy role is scoped (**`AdministratorAccess` detached**); `ALLOW_ADMIN_USER_PASSWORD_AUTH` removed from the SPA client; Cognito advanced-security **ENFORCED**; VPC flow logs, WAF logging, and ECS-exec session logging on; the cube tier split off the shared SG; **SPA CSP + security headers** (`customHttp.yml`). Plus a Pg-backed single-use email-token store, a worker org-key guard, and PII-masked send logs.
- **Still owner-gated / deferred** (tracked in TODO.md): Aurora customer-managed KMS (maintenance window), ADOT-image digest pin + read-only root FS, Turnstile CAPTCHA (validators wired, site/secret pending), and broader-user RBAC group assignment.

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

- **Live cloud mutation is Lane Nick only.** The stack IS applied + live (real money, acct
  186052668426, us-east-1). Lane Nick applies from merged `main` after a reviewed plan showing no
  unintended change/destroy; Lane Matt (app code) authors + `terraform validate` only and marks
  live steps `BLOCKED: Lane Nick`.
- **Draft-only.** No real email/SMS/CRM write executes against real data — every send routes through
  the Greenlight gate (proven by the live agent-plane verify: an approved + executed `send_email`
  produced only a proposal, no real send left the building).
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
