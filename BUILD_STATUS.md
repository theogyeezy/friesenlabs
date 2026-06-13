# Uplift — Build Status

> ## ✅ BUILD COMPLETE + AUDITED — all 13 phases (0–12) + frontend, plus Sections A/D and a final audit pass.
> Everything buildable offline is green: **pytest 193 passed / 2 skipped** (the 2 skips run for real in
> CI against a live Postgres+pgvector service), **smoke_all** pass, **terraform validate** clean
> (19 modules) **and `terraform plan` against the live AWS account is clean (92 to add, 0 change/destroy)**,
> **web** typecheck + build + Playwright (7) pass, isolation gate real (fails in CI without a DB).
> Every step that needs live cloud / Anthropic / Stripe is explicitly **`BLOCKED: needs Nick`** below —
> nothing was applied, spent, or sent. No secrets or the confidential spec are tracked in this public repo.
>
> **As-shipped caveats (honest):** the production ASGI app mounts the signup/webhook routes but its
> chat + tool-executor backends and the Stripe/Cognito/Resend clients are clearly-stubbed pending live
> creds (BLOCKED: needs Nick) — they fail loudly (503 / "not configured"), they do not fake success.
> An 8-agent adversarial audit (Cycle 17) found + fixed a client-trusted-flag auth bypass on `/actions`,
> a Step Functions ARN bug (caught by `terraform plan`), a 3× cost-model price error, and ~20 more;
> remaining items are live-cloud (needs Nick).

Multi-tenant agentic CRM with a Moveworks-style conversational front door.
Hybrid architecture: **agent plane** = Claude Managed Agents (beta, behind a swappable
adapter); **everything else** = AWS (data plane, control plane, app, ML).

> ### 🟢 LIVE end-to-end — applied to AWS account 186052668426 (us-east-1)
> The backend is **live and verified**: `browser → Amplify (Vite SPA) → CloudFront → ALB (HTTP) →
> arm64 Fargate API → Aurora` (FORCE'd RLS) with **real Cognito JWKS auth** enforced. Checks:
> `/api/healthz` → 200, `/api/approvals` (no token) → 401. Live pieces: VPC/NAT/SGs, IAM, Secrets, ECR,
> S3, **Aurora** (`uplift-aurora`, schema migrated), **Redis**, Cognito, CloudTrail, Step Functions,
> ECS cluster, **ALB + arm64 Fargate API service (1 task)**, **CloudFront API edge**
> (`d1vw20lc120dpa.cloudfront.net`), **Amplify Hosting** (`main.d224yxym1ehrim.amplifyapp.com`, `/api`
> proxy). **$200 budget alarm** armed. **State in S3** (`uplift-tfstate-*`, KMS).
>
> **The login flow is LIVE and browser-verified end-to-end** (real-mode build deployed): sign-in
> gate → Cognito Hosted UI (`web/src/auth/`: hand-rolled authorization-code + PKCE, no auth SDK) →
> code exchange at `/auth/callback` → app shell with **real RLS-scoped tenant rows from Aurora**
> (seeded via `scripts/seed_demo_tenant.py`; demo creds in Secrets Manager `uplift/demo-user`).
> Unauth API → 401; `/chat` → graceful 503. State drift reconciled (SG rules imported, ECR
> IMMUTABLE; plan = 0 change/destroy to live resources).
>
> **Not yet real:** the **AI/agent plane** (no Anthropic Managed Agents creds — `runtime.py` stub,
> noop executor) and the **provisioning integrations** (Stripe/Resend/Admin stubs). The cube/worker/
> observability/provisioning-Lambda/cortex modules are authored but unapplied.
> **Security (2026-06-09):** a 37-agent adversarial audit produced 27 findings (in TODO.md). A
> **critical cross-tenant leak was FIXED** (shared DB connection + session-level tenant GUC raced
> across the threadpool → pooled per-request conns + `SET LOCAL`; proven on live Aurora + CI).
> Aurora durability (deletion protection + 7-day backups) applied.
>
> To tear down: `cd infra && terraform destroy`.

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
| 1 | Data Plane (Aurora+pgvector, RLS, schema, S3, Redis) | ✅* | orchestrator | ✓ | ✓skip | ✓ | · | ✓skip | self ✓ |
| 2 | Ingestion & Embeddings (connectors, chunk, Titan, pipeline) | ✅ | bg-agent | ✓ | ✓skip | · | · | · | cross ✓ |
| 3 | Semantic Layer (Cube deploy, metrics, tenant security ctx) | ✅* | orchestrator | ✓ | · | ✓ | · | ✓ | self ✓ |
| 4 | Agent Plane (Managed Agents, roster, vaults, worker) | ✅* | orchestrator | ✓ | ✓ | · | · | · | self ✓ |
| 5 | Control Plane (autonomy, Greenlight, traces, kill switch) | ✅ | orchestrator | ✓ | ✓ | · | · | · | self ✓ |
| 6 | Conversational Layer (front door, slots, agentic RAG+cites) | ✅ | bg-agent | ✓ | ✓ | · | · | · | cross ✓ |
| 7 | Dashboard Engine (view-spec, generate, render, save/edit) | ✅ | orch+agent | ✓ | · | ✓ | ✓ | · | cross ✓ |
| 8 | Cortex / ML (per-tenant models, train, registry, retrain) | ✅* | orchestrator | ✓ | · | · | · | · | self ✓ |
| 9 | App, Auth & API (Cognito, FastAPI/Fargate, ALB, web) | ✅ | orch+agent | ✓ | ✓ | · | ✓ | ✓ | cross ✓ |
| 10 | Acquisition, Signup & Provisioning (landing, Stripe, auto-provision) | ✅* | orchestrator | ✓ | · | · | · | ✓ | self ✓ |
| 11 | Cost, Guardrails & Observability (budgets, caps, CloudWatch, OTEL) | ✅* | orchestrator | ✓ | · | · | · | · | self ✓ |
| 12 | IaC, CI/CD & Launch (Terraform/CDK, pipelines, smoke+isolation) | ✅ | orchestrator | · | · | ✓ | · | ✓ | self ✓ |
| FE | Frontend: convert ~45 JSX → React+TS app in `web/` | ✅ | bg-agent | · | · | ✓ | ✓ | · | cross ✓ (fixed) |

`✅*` = code complete + `terraform validate`-clean; **apply BLOCKED: needs Nick** (cost/irreversible).

## Blocked — needs Nick (creds / cost / external accounts)
*(populated as we hit live-cloud steps; nothing executed against real AWS by design)*
- `terraform apply` for all of `infra/` — authored + `validate`-clean, but never applied (cost/irreversible).
  Now includes Phase 1: Aurora Serverless v2 (`modules/data`), ElastiCache Valkey (`modules/redis`),
  S3 datalake+uploads (`modules/s3`).
- **Apply `db/schema.sql` + `db/roles.sql`** to the live cluster, then set `crm_app` password from
  Secrets Manager — needs the cluster (Nick). Until then the live RLS integration test skips.
- **Org-level Phase 0 items** authored-as-notes only (need an AWS Org context): AWS Config recorder +
  delivery channel, and the SCP denying CloudTrail/Config disablement. Account-level baseline
  (CloudTrail + S3 block-public-access) IS authored in `infra/modules/baseline`.
- IAM Identity Center (SSO) Admins permission set — console/SSO-stack step, not in this Terraform.
- **Live Anthropic (Phase 4)** — create environment / agents / coordinator / vaults / sessions, run the
  worker against the real queue. All authored behind `agents/runtime.py` + flagged "verify" (MA beta);
  `ManagedAgentsRuntime` methods raise until creds+verify. BLOCKED: needs Nick (org key, env key, beta).
- **Live Cognito JWKS verification (Phase 9)** — `CognitoJwtVerifier.verify` authored + flagged verify;
  raises until wired. BLOCKED: needs Nick.
- **Live signup integrations (Phase 10)** — Stripe (keys + webhook secret), Cognito, the Anthropic
  **Admin API** (workspace/key endpoints — verify against current docs), Resend domain (SPF/DKIM/DMARC),
  SNS/Twilio. All injected + tested with fakes; live calls BLOCKED: needs Nick.

## Follow-ups (non-blocking cleanups)
- ✅ **`ingest_cursor` RLS** — DONE (Section D2): folded into `db/schema.sql` under FORCE'd RLS.
- ✅ **SECURITY: prototype feed XSS** — DONE (Section D1): all feed HTML routed through
  `web/src/lib/SafeHtml.tsx` (DOMPurify); no raw `dangerouslySetInnerHTML` sink remains; Playwright proof.
- **`documents` content-hash** — ingest derives `sha256(content)` at read time for skip-if-unchanged
  since the schema has no hash column; consider adding `content_hash` to `documents` for efficiency.
  (Minor optimization, not a correctness/security issue.)
- Tighten the 42 `// @ts-nocheck` files in `web/` (see `web/CONVERSION_NOTES.md`). (Quality, not blocking.)

## Cycle log
- **Cycle 1** — repo scaffold (monorepo layout per Build Guide §Step 4), Python venv +
  pytest harness, `scripts/` (smoke_all, isolation_test), root README + CLAUDE.md,
  `.gitignore`/`.stignore` (secrets + confidential PDFs excluded). **Phase 0 complete**:
  `infra/` Terraform (baseline + vpc + security + iam + secrets + ecr), `terraform validate`
  clean, `pytest` 3 passed, smoke_all pass. Committed + pushed to `prod`.
  Dispatched **background agent** to convert the prototype → Vite React+TS in `web/`
  (brief: `scripts/briefs/FE_01_react_ts.md`). Queued **Phase 1** data-plane brief
  (`scripts/briefs/01_data_plane.md`).
- **Cycle 2 (FE integration)** — background agent converted the ~45-file Babel prototype →
  Vite + React 18 + TypeScript in `web/` (43 screens, globals→module wiring, simulated
  `window.claude` stub, styles/fonts/images preserved). Independent review: `npm run build`
  exit 0, Playwright smoke 1 passed — but the agent's "typecheck clean" claim was **wrong**
  (`playwright.config.ts` used `process` without `@types/node`). Fixed by adding `@types/node`;
  `tsc --noEmit` now clean. All 42 prototype files carry `// @ts-nocheck` (see
  `web/CONVERSION_NOTES.md`) — type-tightening is a tracked follow-up. Committed + pushed.
- **Cycle 3 (Phase 1 data plane)** — `db/schema.sql` (documents+pgvector HNSW, contacts, companies,
  deals, activities, saved_views, approvals, traces) with `ENABLE`+`FORCE` RLS + `tenant_isolation`
  policy on all 8 tables; `db/roles.sql` (`crm_app` NOSUPERUSER/NOBYPASSRLS login). Terraform
  `modules/{data,redis,s3}` wired + `validate` clean. Tests: 13 static SQL tests (libpg_query parse +
  FORCE-RLS assertions) pass; two-tenant RLS integration test (row + vector ANN + update) written,
  skips cleanly with no local DB; `isolation_test.py` reconciled to `app.current_tenant` GUC + vector
  query. `pytest` 16 passed / 1 skipped; smoke_all pass. Committed + pushed.
- **Cycle 4 (Phase 2 ingestion)** — background agent built `ingest/`: `Connector` ABC +
  `HubSpotConnector` (injected client, no real API), `chunk.py` (record/transcript/stripe strategies,
  tenant_id/source/ref_id on every chunk), `embed.py` (Titan V2 1024, lazy boto3 — import-safe),
  `pipeline.sync_tenant` (pull→land→chunk→embed→upsert-by-ref_id + content-hash skip + per-tenant
  cursor). Independent review: import-safe confirmed (no eager boto3/network), 22 ingest unit tests +
  incremental proof (2nd sync embeds ~0) pass; full suite 38 passed / 2 skipped. Committed + pushed.
  Two follow-ups recorded (ingest_cursor RLS, content_hash column).
- **Cycle 5 (Phase 3 semantic layer)** — `semantic/security.js` (tenant security context: force a
  `tenant_id` filter onto every referenced cube; throw `no tenant` on missing/forged context),
  `cube.js`, cube models for Deals/Contacts/Companies/Activities (tenant_id `shown:false`),
  6 Node tests green. IaC: shared ECS cluster (`modules/ecs`) + Cube Fargate service (`modules/cube`,
  crm_app creds via Secrets Manager, internal-only), `terraform validate` clean. smoke_all green.
  Committed + pushed.
- **Cycle 6 (Phase 4 agent plane)** — `agents/runtime.py` swappable adapter (FakeRuntime drives tests;
  ManagedAgentsRuntime real-shape but blocked until verify; `get_runtime` factory; hard limits encoded).
  Roster of 7 specialists + opus coordinator as code (native model tiering). Tools: `base.py` Policy
  seam (auto vs always_ask) + ToolContext binding `app.current_tenant`; read-only (search_rag/query_cube/
  read_crm) + side-effecting (draft_email auto; send_email/update_deal/issue_quote always_ask →
  Greenlight proposal, never executed). `worker/worker.py` scaffold (env-key only, lazy anthropic,
  import-safe). IaC: `modules/worker` Fargate + env-key/cube/db secrets. Tests: 15 new (adapter/roster/
  tool-policy/session), full suite 53 passed / 2 skipped; smoke_all green; terraform validate clean.
  Committed + pushed. Live Anthropic provisioning BLOCKED: needs Nick.
- **Cycle 7 (Phase 5 control plane)** — `api/control/`: `gate.py` (single path:
  propose→validate→autonomy→Greenlight→execute→trace, exactly one trace per run, executor never
  called on block/deny), `autonomy.py` (L0-L3 + L2 thresholds), `greenlight.py` (HITL queue over
  `approvals`, approve/edit/deny, conforms to the Phase 4 tool Greenlight protocol + MA confirmation
  mapping flagged verify), `compliance.py` (TCPA/CAN-SPAM deterministic + injected critic; hard fail
  never reaches the queue), `traces.py` (minimized per-step records), `killswitch.py` (per-tenant +
  global). Tests: 27 unit (autonomy/gate/greenlight/killswitch/compliance) + integration proving a
  Phase 4 send_email tool routes into the control-plane queue without sending. Full suite 81 passed /
  2 skipped; smoke_all green. Committed + pushed.
- **Cycle 8 (Phase 6 conversational layer)** — background agent built `conv/`: `slots.py` (NL→governed
  IDs; date phrases via injected `today`; >1 match → Disambiguation, auto-pick only at confidence
  ≥0.85 — never silently guesses), `rag.py` (hybrid retrieval → synthesize → `assemble_citations`:
  every grounded claim carries a source_ref that exists in the retrieved set; uncited claims dropped/
  flagged, never grounded), `analytics.py`, `session.py` (Conversation facade over FakeRuntime; action
  utterances route to Phase 4 tools → Phase 5 Greenlight without sending). Independent review: import-
  safe, no network/secrets, both invariants verified in source + tests. 33 new tests; full suite 114
  passed / 2 skipped. Committed + pushed. Flagged: `session.py` action-routing regexes are an offline
  stand-in to be superseded by the coordinator's tool selection in Phase 9.
- **Cycle 9 (Phase 7 core)** — `shared/schemas/view_spec.schema.json` (strict spec-not-code: catalog
  types kpi/chart/table, Vega-Lite only, Cube-member pattern, additionalProperties:false) +
  `shared/view_spec.py` (schema + real-member validation), `agents/tools/build_view.py`
  (generate→validate→reject-and-retry, never returns unvalidated), `api/views.py` (SavedViews
  save/version/refine-NL/edit, never persists invalid). 13 tests; full suite 127 passed / 2 skipped.
  Committed + pushed. Dispatched **background agent** for the trusted Vega-Lite renderer in `web/`
  (`scripts/briefs/07_dashboard_renderer.md`).
- **Cycle 10 (Phase 7 renderer)** — background agent built the trusted renderer in `web/`:
  `SpecRenderer.tsx` (re-validates the spec first → SafeFallback on error; renders only catalog
  components: KPI card / Vega-Lite chart / table; no dangerouslySetInnerHTML / eval; vega-embed with
  `actions:false` + loaders disabled so a spec can't reach the network), `viewSpec.ts` client
  validator mirroring the JSON schema, sample spec + demo mount. Independent review: build exit 0,
  typecheck clean, Playwright 3 passed incl. an XSS spec that yields the fallback (`window.__pwned`
  undefined, payload never in DOM). Committed + pushed. (Logged a separate pre-existing prototype-feed
  XSS follow-up.)
- **Cycle 11 (Phase 8 Cortex/ML)** — `ml/`: `features.py` (lead→booked feature build), `estimator.py`
  (Estimator protocol + real pure-Python LogisticRegression + MajorityBaseline floor; LightGBM/XGBoost
  drop in for prod), `train.py` (split→bake-off→held-out AUC, deterministic), `metrics.py` (AUC/acc),
  `registry.py` (per-tenant versioned registry + champion/challenger gate with promotion margin),
  `retrain.py` (retrain orchestration + drift check), `agents/tools/run_model.py` (AUTO tool serving
  the tenant champion, tenant-scoped). IaC: `modules/cortex` EventBridge retrain schedule (validate
  only). 11 tests (learner beats random AUC>0.7, deterministic, gate promotes only on margin, run_model
  tenant-scoped, drift flags degradation). Full suite 138 passed / 2 skipped; terraform validate +
  smoke_all green. Committed + pushed. Live SageMaker/Modal training + EventBridge target BLOCKED: needs Nick.
- **Cycle 12 (Phase 9 backend)** — `api/auth.py` (THE TRUST RULE: `current_tenant` reads tenant ONLY
  from the verified Cognito JWT `custom:tenant_id`; injected verifier; real CognitoJwtVerifier flagged
  verify), `api/app.py` FastAPI (`create_app(deps)`: healthz, approvals list/decide, views CRUD, chat
  via conv.session, actions via control/gate) — every route tenant-scoped from the claim, never the
  body. IaC: `modules/auth` (Cognito pool, tenant_id immutable + client-read-only), `modules/alb`
  (public ALB 443→8000, HTTP→HTTPS redirect, /healthz health check), `modules/api_service` (api
  Fargate ×2, behind TG, secrets from SM; org API key on API never worker). 12 API tests incl. the
  trust rule + two-tenant HTTP isolation; full suite 150 passed / 2 skipped; terraform validate + smoke
  green. Committed + pushed. Dispatched **background agent** for frontend wiring
  (`scripts/briefs/09b_frontend_wiring.md`). Live Cognito/ALB/Fargate apply BLOCKED: needs Nick.
- **Cycle 13 (Phase 9 frontend wiring)** — background agent built `web/src/api/client.ts` (typed,
  injectable baseURL+token, mock-mode default for offline e2e) + wired GreenlightQueue (reasoning +
  value-at-stake + editable draft; approve/edit/deny), ChatDock (answer + inline citations), and a
  DashboardView (getView/saveView → SpecRenderer). Independent review: build exit 0, typecheck clean,
  Playwright 5 passed (smoke + 2 dashboard + 2 greenlight); confirmed the client NEVER sends tenant_id
  (only Bearer from config) — the trust rule holds client-side. Committed + pushed. **Phase 9 done.**
- **Cycle 14 (Phase 10 acquisition/signup/provisioning)** — `signup/`: `accounts.py` (verify email+phone
  BEFORE pay; Cognito unconfirmed, no tenant_id yet; idempotent create), `payment.py` (Stripe; checkout
  refused until verified + idempotency key; `handle_webhook` is the ONLY provisioning trigger,
  signature-verified + idempotent), `provisioning.py` (the 6-step idempotent rollback-safe pipeline:
  tenant→workspace+key→agent plane→Cognito tenant→Cube/defaults→welcome; mint tenant_id at provisioning;
  mid-failure parks provisioning_failed + tears down the orphan workspace), `funnel.py` (PostHog,
  server-side revenue). 7 tests proving every anti-accidental-charge guarantee. Full suite 157 passed /
  2 skipped; smoke green. Committed + pushed. Live Stripe/Cognito/Anthropic-Admin/Resend BLOCKED: needs Nick.
- **Cycle 15 (Phase 11 cost/guardrails/observability)** — `shared/cost.py` (unit-economics model:
  70/25/5 tiering, prompt-caching -90%, Batch -50% offline-only, $0.08/active-session-hour stacking on
  parallel threads) + `shared/COST.md` playbook. IaC: `modules/guardrails` (AWS Budget + 90% Deny
  action + us-east-1 billing alarm + cost tags), `modules/observability` (CloudWatch alarms for ALB
  5xx/p95 latency, Aurora ACU, Redis evictions, worker workers_polling<1, + SNS topic). 6 cost tests;
  full suite 163 passed / 2 skipped; terraform validate + smoke green. Committed + pushed. Live
  budgets/alarms BLOCKED: needs Nick.
- **Cycle 16 (Phase 12 IaC/CI-CD/launch)** — `.github/workflows/ci.yml` (python: pytest + isolation
  gate; terraform: fmt-check + validate; web: typecheck + build + Playwright), `infra/envs/{dev,staging,
  prod}.tfvars` (environments = deploys of the trunk; secrets stay in SM), `CONTRIBUTING.md`
  (trunk-based on `prod`, branch model, the isolation gate), `scripts/demo.sh` (offline end-to-end
  dry-run). Fixed a `.gitignore` trailing-comment bug so env tfvars are tracked while secret tfvars stay
  ignored. Committed + pushed.
- **All 13 phases (0-12) + frontend complete.** Final Definition-of-Done verification pass done.
- **Section A (connective tissue between units + the outside world)** — closed the gaps that stood
  between "phases tested" and "runnable product":
  - **A1** — exposed signup over HTTP: `api/signup_routes.py` (POST /signup, /verify-email,
    /verify-phone, /checkout, GET /signup/{id}, **POST /webhooks/stripe** = the only provisioning
    trigger) + wired `/views/{id}/refine`. Tests prove verify-before-pay, webhook-only provisioning,
    bad-sig rejected, re-delivery idempotent.
  - **A2** — `api/asgi.py` production entrypoint (boots, /healthz 200) + Dockerfiles for api / worker /
    cube (+ `requirements-api.txt`). Images authored; `docker build` itself is a CI/Nick step.
  - **A3** — `infra/modules/provisioning` Step Functions state machine (idempotent step-per-stage +
    Retry + Catch→ParkProvisioningFailed). validate clean (19 modules).
  - **A4** — signup funnel UI (`web/src/signup/SignupFlow.tsx`, ?view=signup) + PostHog client
    (`web/src/analytics/posthog.ts`, env-only key, no-op in tests, masked replay, /ph proxy). Playwright
    6 passed. (background agent, cross-reviewed)
  - Full suite **166 passed / 2 skipped**; terraform validate + smoke + web build/typecheck/e2e green.
    Pushed to `prod`.
- **Section D (security + production-persistence follow-ups) — DONE:**
  - **D2** — `ingest_cursor` folded into `db/schema.sql` under ENABLE+FORCE RLS (9 tenant tables now);
    `PgCursorStore` SETs `app.current_tenant` and no longer self-creates the table.
  - **D3** — Aurora-backed `PgApprovalStore` + `PgSavedViewStore` (connect as crm_app, `bind_tenant` →
    `SET app.current_tenant` before every read/write so RLS applies); `api/asgi.py` uses them when
    `UPLIFT_DB_URL` is set; decide route binds tenant + uuid-string ids; `approvals` gained
    `decided_by`/`deny_message` columns. 3 fake-connection tests prove tenant-bind-before-query.
  - **D1** — prototype-feed **XSS fixed**: all feed HTML routes through `web/src/lib/SafeHtml.tsx`
    (DOMPurify); no raw `dangerouslySetInnerHTML` sink remains; Playwright proves a malicious payload
    is inert. The `api/asgi.py` store TODOs are closed.
  - Full suite **170 passed / 2 skipped**; web build + typecheck + **Playwright 7 passed**; terraform
    validate + smoke green.
- **Remaining = needs Nick only** (creds/cost/apply + verify the 3 beta APIs). Nothing else is
  buildable offline.
  (Aurora/Redis/S3 IaC + `db/schema.sql` with FORCE'd RLS + the two-tenant isolation proof
  incl. a vector query).
- **Cycle 17 (final audit, AWS logged in)** — ran `terraform plan` against the LIVE AWS account
  (read-only; **not** applied): clean **92 to add / 0 change / 0 destroy**, after fixing a Step
  Functions ARN bug `validate` couldn't catch. An 8-agent adversarial audit (`uplift-final-audit`
  workflow) swept every phase vs the Build Guide; fixes landed (3 parallel agents + orchestrator):
  - **H1/H2 (security):** `/actions` trusted a client `side_effecting` flag → a forged flag bypassed
    Greenlight + compliance. Now derived from a **trusted server-side tool registry**
    (`agents/tools/registry.py`); body cannot set it. Unknown tool → 400.
  - **H3/H5/M-reg:** `run_model` + `build_view` were orphaned → added to the registry + the scout roster.
  - **H6:** prod ASGI now **mounts** the signup/Stripe-webhook routes (`api/prod_deps.py`, stub clients
    flagged needs-Nick); **M1:** `/chat` returns 503 (not 500) when unconfigured.
  - **H8/M5:** CI isolation gate was a no-op → CI now runs a real **Postgres+pgvector service**, loads
    the schema, and runs the two DB integration tests for real; `UPLIFT_REQUIRE_DB=1` makes the gate
    fail without a DB; added a smoke job.
  - **H9:** cost model Opus price was 3× the spec → corrected (+ absolute-price test).
  - **H4:** saved-view validation can now resolve per-tenant Cube members (no silent skip).
  - **H10 (IaC leg):** ADOT/OTEL sidecars added to api/worker/cube task defs (trace verify = needs Nick).
  - **signup hardening:** input validation, webhook unknown-account no-op (M6), verify-ordering (L4),
    provision re-asserts verify-before-pay (L2), server-side PostHog funnel wired (H7).
  - **guardrails wiring** (M2/M3/M4): ALB `arn_suffix` output, notify-email/Deny-action vars, worker
    `workers_polling` PutMetricData; **L1:** L2 won't auto-execute a value-less side effect; demo.sh init.
  - **Doc drift (D1):** counts/trunk/claims corrected here. Full suite **193 passed / 2 skipped**;
    terraform validate + plan + smoke + web all green.
  - **Flagged, not blocking:** Redis AUTH token (L10), Pg-store SET LOCAL/pooling (L3), cross-tenant FK
    nuance (L11), batch_embed real job (L6), LightGBM/XGBoost candidates (L7) — tracked, mostly needs-Nick.

---

# Two-lane sprint logs (2026-06-09 →)

Per the two-lane contract in `CONTRIBUTING.md`: each lane appends ONLY to its own section below.

## Lane Nick (infra / live-ops) — log
- 2026-06-09 — Lane contract landed: ownership map in CONTRIBUTING.md, `infra/REQUESTS.md` handoff
  queue created, TODO chunk prompts patched (infra steps → REQUESTS.md), CLAUDE.md constraint #1
  lane-scoped. Machine prep: venv green (195 passed / 4 skipped), push verified via diffusion23.
- 2026-06-09 — Baseline plan captured + triaged (`infra/RUNBOOK.md`): 14 adds = exactly the
  unapplied cube/worker/observability modules ✓; alb/api_service/api_cdn/web_hosting confirmed in
  state (TODO "reconcile ALB/API/CloudFront" checked off — premise was stale). **NOT clean:** the
  plan would destroy the live Amplify app (`github_access_token` absent from machine-local
  `prod.auto.tfvars`) and strip the budget notification (`notify_email` unset). Both parked —
  needs Matt (PAT + budget email/limit). Apply discipline: no full apply; pure-add `-target` only.
- 2026-06-09 — Aurora hardening authored (feat/nick-aurora-hardening): TODO premise stale — live
  cluster already at retention=7 + deletion_protection=true (verified). Authored the 2 real gaps
  (copy_tags_to_snapshot, performance_insights_enabled); `plan -target=module.data` = 0 add /
  2 in-place change / 0 destroy, exactly those attrs. Apply follows merge (intended-change rule).
- 2026-06-09 — Aurora hardening APPLIED from main @866328b (#23): re-planned (still exactly 2
  in-place attrs), applied `-target=module.data`, live-verified copyTags=true + PI=true, both
  `available`. TODO 123/136/197 checked off. Also: 3-lens adversarial verify REFUTED the PR-20
  RLS-blocker claim (fail-closed + unreachable; empirical repro) and 3-lens review of PR-18 came
  back MERGE/no-blockers — both recorded as PR comments post-merge (merged by diffusion23).
- 2026-06-09 — REQ-001 + REQ-002 authored (feat/nick-req-001-002): `uplift/env-id` secret, full
  worker env/secret wiring, API org-key injection SAFETY-GATED behind `api_anthropic_env`
  (default false — valueFrom on an empty secret kills task startup). roles.sql: GRANT no-DELETE
  DML on accounts/stripe_events + explicit REVOKE (default-privileges hazard reconciled).
  3-agent verification: asymmetry proven (flag-on plan = exactly 2 api_service actions, flag-off
  = zero; worker has no org key), grants proven empirically on pgvector/pg16 (CI's image; DELETE
  denied, REVOKE wins, idempotent), env-name contract matches shared/config.py + worker.run().
- 2026-06-09 — Cycle 4: REQ-002 grants LIVE + the isolation gate finally run against PROD Aurora.
  Built+pushed `uplift-api:dc7a352` (immutable tag), one-off task-def clone, ran `api.migrate`
  (exit 0) then `scripts/isolation_test.py` as crm_app → '[isolation] PASS — RLS enforced'.
  crm_app live: rolsuper=f, rolbypassrls=f; DELETE denied on accounts/stripe_events. REQ-001+002
  both DONE in REQUESTS.md. Edge /healthz 200 after. TODO Sec/P0 188 checked.
- 2026-06-09 — Cycle 5 authoring: X-Origin-Verify edge→ALB shared secret (Sec/P0 187) behind a
  two-flag, two-phase rollout (no 403 window; nonsensitive() bool so the live listener shows zero
  spurious diff — flags-off plan = baseline +1 pure add). Multi-platform .terraform.lock.hcl now
  TRACKED (aws + random, 2×h1 each) — TODO 135 done; random ~>3.6 added for the secret value.
- 2026-06-09 — Sec/P0 187 DONE: X-Origin-Verify applied two-phase from main @d211c38 with zero
  downtime (edge 200 throughout). ALB :80 default is now fixed-response 403; only requests carrying
  the CloudFront-stamped header forward. Secret in uplift/origin-verify (rotation = taint the
  random_password → phased re-apply). TODO 135 lock-file also merged (#41, Linux CI proof).
- 2026-06-09 — REQ-003 authored (feat/nick-req-003): 3 new secret containers (stripe-webhook,
  signup-token, anthropic-admin-key — the last did NOT pre-exist despite the spec) + API-task
  injection gated behind api_signup_env=false; SIGNUP_REAL_DEPS go-live act has its own flag.
  Execution role gains the 2 exact platform-secret ARNs (no wildcard widening). 2-agent verify
  PASS: worker references none of the 7 names; flag-on render = exactly the 5 secrets +
  SIGNUP_REAL_DEPS=1; config.py/_switch_env/build_signup_deps contract confirmed fail-closed;
  ALLOW_REAL_SENDS untouched. Flags-off plan = baseline + 3 pure adds + intended iam change.
- 2026-06-09 — REQ-003 DONE @7c94e4c (#44): 3 secret containers applied + verified, execution
  role lists the 2 exact platform ARNs, token-signer value minted + stored (CLI, never in
  git/state). Signup go-live sequence documented in RUNBOOK (webhook-secret + admin-key values
  parked on Stripe dashboard / Anthropic admin key; api_signup_env → signup_real_deps are the
  two later deliberate flips). Edge /healthz 200.
- 2026-06-09 — Cycle 7 authoring: CloudWatch `uplift-live` dashboard (6 widgets) + api-task-role
  X-Ray export policy (the ADOT sidecar has been failing silently). Targeted plan = exactly 2
  pure adds; applies post-merge.
- 2026-06-09 — Cycle 7 APPLIED @835d1c0: `uplift-live` dashboard live (list-dashboards ✓) +
  api-task xray-export policy attached (get-role-policy ✓). X-Ray still shows 0 segments —
  the api app emits no OTLP to the sidecar; handed to Lane Matt in TODO (202 PARTIAL). Edge 200.
- 2026-06-09 — Cycle 8 authoring (cube/worker deploys PARKED: run-rate ~$185/mo vs the $200
  ceiling — adding Fargate services needs Matt's cost call): IAM tightening P2 206 — api task
  role scoped to the exact 2 ARNs migrate reads; SFN invoke de-wildcarded to the placeholder
  ARN. Targeted plan = exactly 2 intended in-place changes.
- 2026-06-09 — Cycle 8 APPLIED @c8f2f10: IAM tightening live (api task role = exactly 2 ARNs;
  SFN invoke = placeholder ARN, no wildcard) — get-role-policy verified, edge 200. TODO 206 done.
  QUEUE STATUS: remaining Lane Nick P0/P1s are all PARKED on external inputs — baseline plan
  (Amplify PAT + notify_email, Matt), budget fix (email/limit, Matt), observability alarms
  (notify_email, Matt), cube/worker deploys ($200-ceiling cost call, Matt), domain/ACM (purchase),
  signup go-live values (Stripe dashboard + Anthropic admin key), AI plane (Anthropic creds).
  Unblocked remainder is P2/P3 hardening (205 CloudTrail/ALB logs, 204 rotation, 143 NAT
  endpoints, 212 ECS Exec, 213 log retention, 214 GuardDuty/Config).
- 2026-06-09 — Cycle 9 authoring: CloudTrail scoped data events (uplift buckets + uplift/*
  secrets, management re-stated) + ALB access logging to a new encrypted bucket (SSE-S3, PAB,
  90d expiry, ELB 127311923021 delivery policy). Plan = 5 adds + 2 intended changes (trail, ALB).
- 2026-06-09 — Cycle 9 DONE (TODO 205): ALB access logs live (test file delivered to the
  encrypted bucket) + CloudTrail scoped uplift-* S3 data events + management re-stated.
  Premise correction: Secrets Manager has no data events (GetSecretValue = management event,
  already captured) — first apply's rejection documented, selector dropped in #53.
- 2026-06-09 — Cycle 10 (TODO 213 DONE): single `log_retention_days` root knob across all 6
  uplift log groups (zero-diff refactor at default 30; live groups verified consistent). KMS
  deliberately deferred (cost/complexity vs single-account posture; revisit with HIPAA runtime).
  QUEUE NOW: every remaining Lane Nick item is checked or parked — P0/P1s on Matt inputs
  (Amplify PAT, notify_email, budget, cost calls, Stripe/Anthropic values, domain), P2/P3
  remainder either cost-gated (143 NAT endpoints ~$35/mo, 214 GuardDuty ~cost), task-rolling
  (212 ECS Exec — defer to a maintenance window), or authored-pending-window (204 rotation).
  Lane loop dropping to long-sleep cadence: fetch/REQUESTS/PR checks only until new inputs land.
- 2026-06-09 — BIG UNBLOCK (user supplied: Amplify PAT via gh token, notify_email, cube/worker
  cost go-ahead, domain friesenlabs.com @ Squarespace): baseline plan now CLEAN (0 destroys —
  Amplify hazard gone); budget notification live (subscriber confirmed via direct CLI after a
  terraform/Budgets-API consistency quirk); 4 alarms + SNS live (subscription PendingConfirmation
  — user must click); cube-api-secret minted. Cycle-11 PR: cube digest pin (amd64), worker_absent
  gated on worker_deployed, billing alarm wired to the alarms topic, new dns module (Route53 zone
  + wildcard ACM cert for friesenlabs.com; validation waits gated on dns_delegated).
- 2026-06-09 — Cycle 11 applies: friesenlabs.com Route53 zone + wildcard ACM live (PENDING_VALIDATION
  until the Squarespace NS cutover — 4 NS handed to user); billing alarm wired to the alarms topic
  (verified); cube service deployed 1/1 STEADY STATE (digest-pinned, minted secret). Live :4000 probe
  caught the missing sg_api self-ingress rule (timeout) — fix PR'd. Cloud Map service discovery noted
  as the remaining cube gap. SNS email sub still PendingConfirmation (user).
- 2026-06-09 — Cube VERIFIED: /readyz 200 from in-VPC probe (SG self-rule #58 + memory driver #59
  — Cube 1.x removed redis, caught from live logs). TODO 127 DONE. Cycle-11 batch check-off:
  baseline 122/190 DONE (clean plan), budget 195 DONE, alarms 194/129 DONE (sub pending user
  click), domain 131 IN PROGRESS (cert pending NS cutover). Next: cycle 12 MA env creation
  (org key in SM).
- 2026-06-09 — CYCLE 12, AI-plane unlock: MA shapes VERIFIED (claude-api skill — runtime.py's
  assumptions all real, incl. managed-agents-2026-04-01 + multiagent coordinator); live
  create_environment → env_012JvqRKUZzUDeH3Gse6TBgZ in uplift/env-id; api_anthropic_env flipped
  → task-def rev 4 (zero downtime; ANTHROPIC_API_KEY + UPLIFT_ENV_ID live on the API task, env-key
  provably absent). /chat 503→401-unauth. PARKED: uplift/env-key = Console-only click (user).
- 2026-06-09 — REQ-004 authored (feat/nick-req-004): new modules/ingest — dedicated task def
  (arm64 api image, run_sync --all override, INGEST_REAL_STORES=1 isolated to this def), scoped
  task role (per-tenant hubspot secrets + Titan V2 InvokeModel + conditional raw-bucket write),
  EventBridge rate(1d) DISABLED by default with cluster-conditioned RunTask + service-conditioned
  PassRole. Plan = 8 pure adds at safe defaults; API/worker defs carry zero INGEST_* names.
- 2026-06-09 — REQ-004 DONE @e67ca87 (#62): modules/ingest applied (8 pure adds), live-verified
  — rule DISABLED rate(1d), task def command/env exact, role scoped (hubspot patterns + Titan V2
  only). Go-live = ingest_tenants + ingest_schedule_enabled flip. Edge 200.
- 2026-06-09 — REQ-005 authored (feat/nick-req-005): Lambda Dockerfile + provisioning_lambda
  module (count-gated, env-value secrets, admin-key gated), ECR repo #4, exact-ARN StartExecution
  policy, gated PROVISIONING_SFN_ARN. Validate green. ⚠ AWS SESSION CREDS EXPIRED mid-cycle —
  plan/apply/image-build parked until the user re-authenticates; all AWS verification blocked.
- 2026-06-09 — REQ-005 DONE @e55dcc4: Lambda live (arm64 image), SFN pinned, StartExecution
  smoked (clean invoke + retries + Catch park on a nonexistent account; duplicate name →
  ExecutionAlreadyExists). Cycle 15: README + CLAUDE.md status sections refreshed to reality
  (user granted standing merge approval; autonomous completion mode).
- 2026-06-10 — AUTONOMOUS COMPLETION SPRINT (cycles 16-23, user master-approval): cube semantic
  model LIVE (custom image, no-model warning gone) + Cloud Map (cube.uplift.local verified end-
  to-end); CI/CD pipeline live (OIDC role, protected production env, build job proven; plan-job
  hang under investigation); ECS Exec + CUBE_ENDPOINT (api rev 5); GuardDuty + Config recorder +
  SSM endpoint mirror; worker ARM64 image prebuilt (uplift-worker:3010bfe — one env-key from
  deploy); crm-app-db ROTATION EXECUTED (SAR Lambda, controlled window, rolled + verified);
  ALB TLS cutover sequence authored (RUNBOOK, 301-loop hazard documented); GHL-energy landing +
  interactive vs-GoHighLevel comparison shipped (user-directed lane override; Amplify deploying).
  REMAINING = USER INPUTS ONLY: env-key Console click → worker deploy; NS cutover → TLS cutover;
  SNS confirm; stripe-webhook + admin-key values → signup go-live; deploy-run approval click.
- 2026-06-10 — REQ-006/007/008 DONE (#85): PostHog env (Lambda live + staged in the signup
  gate), connector-write IAM verified exact-scope, live-e2e CI job (nightly, self-skipping).
  LIVE CATCH: AWS auto-minor-upgraded Aurora 16.8→16.11 — the stale pin would have planned a
  DOWNGRADE; re-pinned + ignore_changes(engine_version), module.data plan clean.
- 2026-06-10 — Landing overhaul (user-directed, GHL-energy) shipped over v1-v4 + a fix:
  interactive agent-roster hero, animated Friesen-vs-GoHighLevel capability RADAR + lens-toggle
  comparison table, live ROI calculator (sliders → savings count-up + bar race), magnetic CTAs,
  scroll-progress bar, closing CTA band. Live-verified via browser screenshots (caught + fixed a
  CountUp $NaN — prop is value= not to=). All additive; every existing section/demo/modal intact.
  Also: SNS alarm subscription CONFIRMED by the user — 5 CloudWatch alarms now page
  theogyeezy@gmail.com (sub ARN active, no longer PendingConfirmation).
- 2026-06-10 — "FINISH IT": drove the CI/CD pipeline END-TO-END (master approval) — first full
  build→plan→approved-apply→roll→health-gate, all green. Prod API upgraded from the weeks-old
  e0794bc to current main `uplift-api:14524b0` (all merged backend work now live; rev 6, 1/1,
  /healthz 200, unauth 401). Pre-req: gated the worker module on `worker_deployed` so a full apply
  can't crash-loop a worker on the empty env-key.
  >>> COMPLETION STATE: every Lane-Nick item that can be finished WITHOUT a third-party
  credential/console action is DONE and live. The four irreducible remainders each need something
  only the account owner can produce:
    1. uplift/env-key — Anthropic Console "Generate environment key" (SDK has no mint method;
       verified). Unblocks: worker deploy (image + ARM64 task-def + module all staged; flip
       worker_deployed=true + apply -target=module.worker).
    2. Squarespace NS → the 4 Route53 nameservers — unblocks ACM validation → ALB TLS cutover
       (sequence authored in RUNBOOK).
    3. uplift/stripe-webhook-secret — Stripe Dashboard endpoint registration — unblocks signup
       webhook go-live (api_signup_env → signup_real_deps flips, all wired).
    4. uplift/anthropic-admin-key — Anthropic Console admin key — unblocks workspace provisioning
       (provisioning_admin_key_available flip).
  Hand me any one of those values/clicks and the corresponding go-live runs same-session.
- 2026-06-10 — Landing prod fixes (user-reported): (1) SCROLL-LOCK fixed — the landing never
  applied `body.lp-body`, so the global `body{overflow:hidden}` locked it in real builds; Landing
  now owns that class (verified live: real scroll to y=4037). (2) STALE-CACHE fixed at the Amplify
  edge — set custom headers: `index.html`/`/` → `Cache-Control: no-store, must-revalidate`,
  `/assets/**` (hashed) → `immutable` — so future web deploys are seen immediately without a hard
  refresh (AWS-side `amplify update-app --custom-headers`, not in git). Existing users should
  hard-refresh once to clear the previously-cached bundle.
- 2026-06-10 — Landing mobile-first UX redesign (user: "bad UX, can't see everything easily on
  mobile"). Researched SaaS landing best practices (mobile-first / one primary CTA / wayfinding /
  scannable), diagnosed the real gaps on the live phone view, then shipped: HAMBURGER nav +
  slide-in menu (the 15k-px page had NO mobile navigation — links just display:none'd at 860px);
  sticky bottom CTA bar (Build your suite always one tap away); back-to-top; hero/final CTAs
  stack full-width to a single dominant action; tighter mobile section rhythm + safe-area insets.
  Live-verified on a 390px viewport (hero, open menu, radar, pricing) — incl. fixing the radar
  axis labels clipping (widened SVG viewBox). Desktop unchanged; e2e anchors intact.
- 2026-06-10 — "finish everything" pass: security-hardening batch APPLIED + live-verified —
  CloudFront WAFv2 (managed rules + 2000/5min/IP rate limit) + access logging + HSTS/security-
  headers policy + PriceClass_100; Cognito deletion-protection + admin-create-only + 7-day refresh;
  AWS provider pinned ~>6.49; ECS deployment circuit breakers (auto-rollback) on api/cube; CI
  permissions block; .stignore parity. Then an honest TODO.md sweep: 22 already-done items checked
  off, a Lane-Nick completion-status block added categorizing the 52 remaining as owner-gated /
  cost-parked / maintenance-window / Lane-Matt. Caught + fixed a latent tfvars drift (api_image
  still e0794bc would have reverted the live 14524b0) and refreshed the deploy secret. Edge 200
  throughout.
- 2026-06-10 — Landing EDITORIAL overhaul + audit hardening (user-directed; the GHL/cinematic
  experiments logged above were reverted as unusable — three.js removed). Shipped an "Editorial &
  warm" system (cream paper, Fraunces serif, warm-clay accent, hairline rules/cards, a bespoke
  product-grounded line-icon set), benefit-first dash/arrow/slop-free copy, Apple-style cinematics
  (hero load-in assembly, staggered card reveals, hero-plate parallax — reduced-motion safe), and
  product-WINDOW framing of the live demos across all four surfaces (hero + "see it in action" +
  product-detail + roadmap, with honest LIVE vs clay PREVIEW badges). Then a 4-lens audit
  (theme/copy/a11y/SEO via a 4-agent workflow, 46 findings) folded in and verified on the LIVE site:
  • SEO: real `<title>`/meta/OG+Twitter/canonical/theme-color, brand favicon, and a generated
    1200×630 og:image card emitted by an inline Vite plugin (survives `publicDir:false`), served at
    `/og.png`. og:image + canonical point at the Amplify URL FOR NOW — repoint to
    `https://friesenlabs.com/` at the TLS cutover.
  • a11y: WCAG-AA contrast tokens, keyboard-operable pricing rows/BYO toggle/product pills, dialog
    roles + global Escape-to-close, skip-link + `role=main`, completed vs-table ARIA, heading order,
    44px touch targets, reduced-motion guards → **Lighthouse ~100 (a11y/SEO/best-practices/agentic)
    DESKTOP + MOBILE**.
  • Perf: lazy-loaded the authed app (App + DashboardView→vega + every gated panel) off the landing
    path → first-load **560KB → 247KB gz**.
  • Theme: removed the cinematic-era cool leftovers (indigo WebGL field, synthwave grid, vignette,
    grain, glow-blobs); dropped the unused three.js dep; warmed `--accent-press` + the vs/finalcta
    sections. Brand: swept 4 platform-level "Uplift" refs → "Friesen Labs" (kept every "Uplift CRM").
  Verified end-to-end: web unit **28/0**, Playwright e2e **7/7** (incl. zero-dead-anchors +
  lazy-load conversion paths + focus-visible), browser QA (all 13 demo tabs render, mobile hamburger
  ok, zero console errors). Living docs (CLAUDE/README/TODO) de-drifted from the removed cinematic
  prose. DOMAIN staged to the single user step: Route53 zone + ACM validation CNAME are already in
  place; cert is PENDING only because `friesenlabs.com` NS aren't yet delegated to the 4 Route53
  nameservers — scheduled an hourly sweep to catch ISSUED and auto-run the TLS cutover.

- 2026-06-10 — AGENT-PLANE LIVE VERIFY (Lane Nick, on `feat/nick-agentplane`). Confirmed the live
  wiring (api task `uplift-api:10` has the full gate: `SIGNUP_REAL_DEPS=1` + `ANTHROPIC_API_KEY` +
  `UPLIFT_ENV_ID` → the real `signup.agent_plane.AgentPlaneEnsure` is selected, NOT `_Noop`; worker
  2/2, cube up), then ran `scripts/verify_agent_plane.py` LIVE (`UPLIFT_LIVE_VERIFY=1`, org key +
  env-id from SM, throwaway test tenant, in-memory stores). RESULT `{"ok": true}`:
  • workspace PASS — provisioned 7 specialists + coordinator (`agent_01QTgpwQ…`) in env
    `env_012JvqRKUZ…`;
  • chat PASS — live coordinator round-trip, 293-char answer, delegated to the `ledger` specialist;
  • greenlight/approve/execute PASS — a `send_email` lands PENDING (executor calls=0), approve flips
    it, execute runs the executor exactly once AND the **draft-only guarantee held** (the executed
    send only produced a fresh Greenlight proposal — no email left the building);
  • grounding SKIP (no crm_app DSN — needs the in-VPC Aurora; a Fargate one-off is the full RAG leg).
  ⇒ The agent plane is PROVEN usable + safe end-to-end. TODO 193's description ("ensure() is `_Noop`")
  is stale and its "done-when" (ensure creates env+roster+coordinator idempotently) is now met —
  ready for Lane Matt to close. NOTE: this run created an orphaned test-tenant roster in the live MA
  env (in-memory store ⇒ no Aurora pointer); harmless + tenant-scoped, deletable via the Admin API on
  request.

- 2026-06-10 — AGENT-PLANE grounding leg + the REAL prod IAM bug it caught (Lane Nick). Ran
  `scripts/verify_agent_plane.py` as a Fargate one-off IN THE VPC (api task def + command override,
  demo tenant `f0930caa…`, DB-backed). It surfaced `AccessDenied` on `bedrock:InvokeModel` for
  `amazon.titan-embed-text-v2:0` — NEITHER the api nor worker task role could embed RAG queries, so
  knowledge/grounding lookups were broken in PRODUCTION, not just the verify. FIX APPLIED LIVE: added
  a scoped `bedrock-embed` inline policy to both `uplift-api-task` + `uplift-worker-task` (mirrors the
  ingest role's sync-embed grant, `infra/modules/iam/main.tf`); `terraform apply` = **2 added, 0
  change/destroy**. Re-ran the verify: AccessDenied GONE — grounding embeds + searches cleanly; chat
  PASS (273-char answer, delegated to `ledger`); draft-only held through approve+execute. Grounding's
  only remaining miss is `citations=0` because the demo tenant's knowledge corpus is EMPTY (needs an
  ingest sync) — and `grounded=True, dropped=0` proves the no-uncited-claim invariant HELD (produced
  nothing-to-cite rather than hallucinating). CLEANUP: archived the 8 orphaned agents from the earlier
  LOCAL verify run (timestamp-cluster matched; the demo's real roster preserved). ⇒ Agent plane proven
  usable + safe; RAG-embed IAM gap closed live.

## Lane Matt (app code) — log
- 2026-06-12 — **Drag-to-nest in the Knowledge rail (#346):** Notion's signature gesture as
  pure sugar over the #342 location PATCH — drag onto a page nests, the rail header is the
  top-level dropzone (dashed cue / accent ring / dimmed source), the dragged page's own
  subtree never accepts (dragOver withholds preventDefault -> browser not-allowed, ZERO
  requests), draggable only under organize_available, keyboard parity via the Move panel.
  doMove + drag unified on one movePage primitive + shared per-class failure copy. +2 e2e
  (PATCH-body-asserted nest/un-nest; refused drop fires nothing); knowledge spec 33/33;
  full chromium-real 184/184. Client-only.
- 2026-06-12 — **Knowledge tree finishers (#345):** collapsible nodes (chevron swallows the
  click; collapsed subtrees stay hidden even through the cycle-defensive flat tail;
  session-local state) + "+ Sub-page" on the open page (create -> auto-nest via the SAME
  location PATCH as Move, one flow; nest failure degrades calmly to top-level; pending
  parent clears on cancel so it can't go stale). Client-only. +2 e2e; knowledge spec 31/31;
  full chromium-real 182/182.
- 2026-06-12 — **Knowledge page hierarchy, slice 2/2: tree UI (#343):** the rail renders
  sub-pages indented in manual order (cycle-safe walk; deleted parents degrade children to
  top level; filter flattens), the open page gets clickable breadcrumbs, and a Move panel
  (up/down + nest-under excluding the page's own subtree + top level) with honest per-class
  notes (503 organizing-rolling-out / 422 cycle). EVERY affordance gated on
  organize_available — un-migrated DB renders exactly the #332 flat rail. Mock gains a
  stateful tree. +4 e2e; knowledge spec 29/29; full chromium-real 180/180. Rollout after
  merge: Deploy build tag -> Migrate with it (knowledge_pages additive) -> approve apply ->
  Amplify.
- 2026-06-12 — **Knowledge page hierarchy, slice 1/2: backend (#342):** `knowledge_pages`
  (tenant+ref_prefix PK, parent_ref NULL=top, float sort_order; RLS FORCE; full DML to
  crm_app; absent row = top-level default so NO backfill) + four tolerant PgRagClient meta
  methods (42P01 -> reads None / typed PageOrganizeUnavailable on the write — psycopg2-free
  import preserved) + PATCH /knowledge/documents/{ref}/location (one op per call: re-parent
  with cycle-walk refusal, or move up/down with first-use integer materialization + honest
  edge no-ops; un-migrated DB -> pinned 503). Edits CARRY the location row + children to the
  new ref namespace in one tx; deletes re-parent children to the grandparent. +7 integration
  tests + a real-PG meta-lifecycle proof (env-gated); full pytest exit 0. Slice 2 = the tree
  rail/move UI; rollout = build tag -> Migrate with it -> approve Deploy -> Amplify.
- 2026-06-12 — **Knowledge search paging + real-path dim assert (#339 — the last two knowledge
  P2s):** /knowledge/search gains a clamped offset (depth cap 200) + offset/next_offset on the
  wire (null at the honest end; degrade shapes carry the same keys — wire-compat with older
  images); the web appends "Show more results" in place. The embed dim assert lands on the
  REAL lazy-Titan path only (wrong width -> typed EmbedderUnavailable, the calm warming-up
  story, never a Postgres operator error); the injected embedder seam stays dim-unchecked —
  unit fakes deliberately use tiny vectors (their failures still wrap; the #334 boundary
  holds). Full pytest exit 0; knowledge e2e 25/25.
- 2026-06-12 — **Citations → knowledge pages + URL-addressable pages (#338):** the chat loop
  closes — a citation whose source_ref is a page chunk (upload:…#n / demo:kb:…#n; the SAME
  chunked-family rule as the rail, shape-decided client-side) gets "Open page". In-shell it
  soft-switches the route and opens in place (no reload, chat thread survives; handoff is
  consumed-and-cleared so re-clicks work). Standalone chat falls back to a real
  /?view=knowledge&doc=<ref> deep link — every knowledge page is now URL-addressable
  (KnowledgeView reads ?doc= once on mount; non-page refs 404 honestly). CRM refs and
  single-row corpus shadows get NO link. +2 e2e; knowledge spec 24/24; FULL chromium-real
  project 175/175; no API changes.
- 2026-06-12 — **Pages-rail integrity + onboarding seeds knowledge (#337):** (1) LATENT BUG —
  the demo fixture lands 169 single-row activity shadows under source='upload'
  (demo:doc:act:N); post-#332 they'd flood the pages rail as junk read-only "pages" titled by
  their trailing digit. list_uploaded_documents now requires a CHUNKED family (#0..#n / #raw
  member) — proven against real Postgres in the new env-gated test_knowledge_pages_sql.py
  (list shape, raw head bound, inventory #raw exclusion, RLS-scoped namespace delete).
  (2) The audit's "onboarding never touches knowledge": load-sample seeds 3 clearly-labelled
  EDITABLE sample pages (pricing/refunds/FAQ) through the SAME build_doc_ingestor seam the
  Knowledge tab rides; idempotent; honest degrades (no plane -> pages_seeded:0 + pinned
  reason; mid-seed failure reports what landed, never fails the CRM load). +3 unit tests,
  full pytest exit 0; backend-only (the response gains the `knowledge` key).
- 2026-06-12 — **Knowledge degrade reasons differentiated (#334 — audit P1, knowledge slice):**
  `PgRagClient._embed` raises a TYPED `EmbedderUnavailable` (RuntimeError subclass — broad
  callers unchanged; a Bedrock outage never touches the pool), `/knowledge/search` classifies
  on the type (never string-sniffing): embedder down -> pinned "search model not configured" +
  `reason_code embedder_unavailable` (calm warming-up), post-embed failure -> "search failed" +
  `search_error` with RETRY copy (no more "warming up" forever over a Postgres outage). And
  unprovisioned ≠ rolling-out: 503 from GET /knowledge gets its own calm "isn't switched on
  for this workspace yet" panel vs the 404 deploy-lag story. Wire only GAINS `reason_code`
  (older API image -> web defaults to the embedder story). +3 integration tests (typed
  boundary fires pre-pool), e2e 22/22, full pytest exit 0. Synthesizer/worker halves of the
  P1 item remain (tracked in TODO).
- 2026-06-12 — **Knowledge editor ergonomics + rail filter (#333, on top of #332):** Enter
  continues markdown lists ("- "/"* "/"3. "→"4. "; empty item exits — caret restore is a
  LAYOUT effect so fast typing can't race it), the editor textarea auto-grows (page scrolls,
  not the box), and a client-side filter narrows the pages rail past 4 pages (semantic search
  stays the corpus-wide tool; honest no-match state). Pure KnowledgeView + spec — no API
  change. knowledge e2e 21/21 ×2 (--repeat-each=2); tsc + real build clean.
- 2026-06-12 — **Knowledge → a Notion-style pages workspace (#332):** the sixth real tab grows
  full document CRUD with NO schema change. Ingest seam now lands a `<ref>#raw` row per upload
  (the exact original, embedding NULL — invisible to search; chunks land FIRST so a mid-write
  failure means "indexed, not yet editable", never the reverse; titles normalize to one line so
  the raw row's first paragraph break splits title/body unambiguously). PgRagClient grows
  list/get/delete for the upload namespace (bounded raw heads in LIST — never full dumps;
  LIKE-escaped prefixes; inventory now excludes the #raw mirrors) and the routes grow
  GET /knowledge/documents + GET/PUT/DELETE /knowledge/documents/{ref}: PUT re-ingests through
  the SAME seam as POST and removes the old namespace only AFTER the new one fully lands (a
  cleanup failure answers `previous_removed:false` — duplicate, never lost); refs are
  charset-validated pre-reader (both real shapes: `upload:<slug>-<hash8>` AND the seeded
  `demo:kb:<slug>` — the demo corpus lists/reads/deletes as honest read-only pages, edit→409).
  KnowledgeView rebuilt: pages rail · safe-markdown reader (the spec-not-code subset renderer)
  · write/preview editor (⌘S, dirty-guard, two-step delete) · search hit → Open page only when
  the chunk family IS a listed page; new calm pages-rolling-out state covers the web deploying
  ahead of the API. Mock API gains a stateful pages map (offline demo fully drivable). Tests:
  8 unit + 31 integration + 19 Playwright knowledge e2e; full pytest exit 0; tsc + all three
  builds clean; FULL Playwright suite green. Crew RBAC/Greenlight untouched — pages are the
  tenant's own corpus, the same openness tier POST /knowledge/documents has had since #251.
- 2026-06-12 — **CREW LANE LIVE-VERIFIED + the worker-discovery bug found (the Vada Fenwick
  test):** the contact-lookup router fix deployed and the question correctly took the crew
  lane — transport flawless (1 send + 10 auto-continues, all 200, zero 504s, zero nudges,
  honest interim narration) — but the turn stalled: the WORKER's session discovery is
  STARTUP-BOUND (the SDK EnvironmentWorker was streaming two morning-era sessions and never
  picked up newer ones; deploy.yml rolls only the api service, so the worker had run for
  hours). A `--force-new-deployment` on uplift-worker re-discovered immediately — it attached
  to the persisted session, served the still-open `read_crm` calls, and the follow-up turn
  answered with the real contact record (+1-737-555-0115, Round Rock Utility District) WITH
  full context across the stall — the session-persistence feature proven live. Filed: worker
  re-discovery (or roll the worker in deploy.yml); suppress the "documents couldn't verify"
  note on turns answered from served CRM tool_results.
- 2026-06-12 — **Playbook settle 120s -> 480s (option 1, owner-directed):** the 19:45Z live
  tick proved 120s covers a direct tool round-trip (Run-now at 45s served query_cube+read_crm)
  but NOT a delegation cycle — the coordinator delegated to scout and the sub-turn's calls were
  still open at budget end. The scheduled/event legs have no http edge; 8 minutes clears the
  15-minute cadence with margin. Run-now stays 45s (edge-bounded; a delegating run surfaces
  `incomplete` + a settle_budget sentinel honestly — 202-async Run-now remains the durable
  follow-up).
- 2026-06-12 — **MA session-id persistence (deploy-roll survival):** the in-memory Conversation
  (and the tenant's MA session id with it) died on every api task roll — in-flight CREW turns
  were unrecoverable and history reset. Now: `tenant_workspaces.session_id` (idempotent ALTER),
  `WorkspaceStore.set_session_id`, `ManagedAgentsRuntime.resume_session` (offline handle) with
  LAZY LEDGER PRIMING (a new send marks all prior events seen so reconnect-replays never fold
  history into a digest; a continue marks through the last `user.message` so the in-flight tail
  is recovered exactly), Conversation resume-or-create + `persist_session` seam +
  `forget_session`, cache rebuild-on-terminated clears the dead id first, asgi factory wired.
  TDD (7 new integration tests across store/conv/cache/runtime); full pytest exit 0. Needs the
  migrate workflow before the deploy (the new column).
- 2026-06-12 — **Worker drain-window latency ROOT-CAUSED + FIXED (settle budgets per leg):**
  both live "incomplete" sightings surfaced at exactly +25s — the chat-tuned
  `DEFAULT_TURN_SETTLE_SECONDS` (edge-bounded, with chat's async continue-leg as its safety
  net) was starving playbook turns, which have no continue-leg. `get_runtime` now passes
  `settle_budget_s` through (the plumbing gap), and each leg gets a fit-for-purpose budget:
  scheduled/event = 120s (no http edge; `UPLIFT_PLAYBOOK_SETTLE_SECONDS`), HTTP-bound Run-now
  = 45s (under the 60s CloudFront/ALB ceilings; `UPLIFT_RUNNOW_SETTLE_SECONDS`); chat
  untouched. TDD: get_runtime pass-through, env resolvers, and a spy asserting the scheduled
  leg builds runtimes with the 120s budget. 202-async Run-now filed as the durable follow-up
  if 45s still clips.
- 2026-06-12 — **CHAT LIVE-VERIFIED end-to-end (browser, demo tenant) — the "clanky chat"
  program closes:** "What is our discount policy?" answered in SECONDS via the Tier-0 fast
  lane: ONE 200 request, `grounding_status=grounded`, `retrieved_count=8`, `settled=true`,
  **11 citations across 4 real corpus docs** (`demo:kb:pricing-discount-authority#0`,
  `pricing-policy-overview#0`, `pricing-service-agreement-rates#0`,
  `playbook-objection-handling#0`) rendered in the dock with claims + snippets;
  `pending=[]`, `delegations=[]` — zero MA round-trips, zero human nudges. Crew-lane transport
  (settle rounds 1-6 + /chat/continue + ChatDock auto-continue + 504 recovery) all deployed
  behind it. Remaining polish filed in TODO: persist the MA session id per tenant (a deploy
  roll still kills an in-flight CREW turn), bound fast-lane citation snippets (~320 chars like
  the API does — today they carry the full chunk), optional LLM router upgrade, SSE streaming.
- 2026-06-12 — **Tier-0 knowledge fast lane (the Moveworks front door, owner-directed):**
  knowledge-shaped asks now answer DIRECTLY from the grounded RAG path in seconds — no MA
  session round-trip at all (the live "discount policy" turn took 3+ minutes through
  coordinator→margo→scout→critic). New `conv/router.py` (HeuristicRouter — deterministic,
  offline, CREW-BIASED: action/research/CRM-state asks and anything ambiguous still go to the
  coordinator; an LLM classifier can swap in behind the same seam). Wired in
  `make_conversation_factory`; `router=None` keeps the status quo (FakeRuntime facade
  untouched). Trade-off by design: fast-lane turns skip the MA session history. TDD: 14 router
  + 4 fast-path tests; full pytest exit 0.
- 2026-06-12 — **Settle round 6 — budget on EVERY event (steady-window 504 root cause):** the
  budget was only checked at requires_action idles + stream drops; a BUSY session emitting
  ordinary events for minutes hits neither, so the drain rode the whole turn past the 60s edge
  (504) while the held tenant turn lock starved /chat/continue into a 504 too (proven in a
  no-deploy-churn window: /chat 504 + /chat/continue 504, no POST ever completing in the api
  log). handle() now checks the per-request budget BEFORE the dedupe ledger records each event —
  spent → surface unsettled ({reason: settle_budget}); the un-consumed event replays on the
  continue. 3 older budget-shape tests updated to the earlier-exit contract. Full pytest exit 0.
- 2026-06-12 — **Security-audit remediation APPLIED LIVE (owner-directed, REQ-013 + RBAC strict).**
  PR #290 (P0/P1/P2 code) merged + deployed. REQ-013 infra applied as targeted, plan-reviewed
  steps: scoped deploy policy + **`AdministratorAccess` DETACHED** from `uplift-deploy` (after one
  proven scoped deploy), `ALLOW_ADMIN_USER_PASSWORD_AUTH` removed, `UPLIFT_ENVIRONMENT=prod`,
  Cognito threat-protection ENFORCED + `admin`/`member` groups, VPC flow logs + WAF logging +
  ECS-exec session logging, **cube SG split** (worker/lambda on dedicated SGs, sg_api self-:4000
  rule removed — worker re-rolled healthy). **RBAC strict LIVE:** `RBAC_STRICT=1` plumbed (PR #315)
  + flipped on `uplift-api`; the owner account assigned to the `admin` Cognito group. **Verified
  end-to-end on the real account against the live API** (fresh SRP login): `admin` group →
  `GET /account/export` **200**; admin removed → **403** with the honest "requires a workspace
  admin" message; membership restored + reconfirmed. All four flips are in the 39-key
  `/uplift/live/tfvars-keys` manifest, so the #318 clobber-guard prevents a deploy from reverting
  them. Status record: `infra/REQUESTS.md` REQ-013 (PRs #310/#317). Deferred (owner/window): Aurora
  CMK, `adot_image` digest pin, `readonly_root_filesystem`, CAPTCHA Turnstile, broader-user RBAC.
  _(Note for Lane Nick: README.md §security + CLAUDE.md status are stale — they still cite only the
  2026-06-09 audit / "25 findings"; they're your single-writer files, flagging for your next pass.)_
- 2026-06-12 — **Runner digest split SHIPPED (the "pending" honesty fix):** `RunRecord` now
  separates routed Greenlight drafts (`actions_proposed` — a human is genuinely awaited) from
  `calls_unserved` (open calls no executor served before the drain + wedged-session sentinels,
  surfaced verbatim); new status `incomplete` for unserved-only runs (`pending` wins when a
  draft exists; `ok` stays clean-only). The live shape that motivated it (the 15:15Z scheduled
  run's unserved `read_crm`/`query_cube` reading as "awaiting approval") is pinned in unit
  tests; Studio renders the split ("tools unserved" chip + honest Run-now notice; e2e asserts
  the incomplete row NEVER reads "awaiting approval"). Backend 2147 passed; web
  typecheck + 6/6 studio e2e. **LIVE-VERIFIED post-deploy (18:59Z, api on the #322 image):
  a Run-now reproduced the exact motivating case — the worker left `query_cube`+`read_crm`
  unserved in the drain window — and the record now reads `status: incomplete`,
  `actions_proposed: []`, `calls_unserved: [query_cube, read_crm]`, trace
  `call_unserved ×2`; registration reused; history row carries the split. NOTE: that's the
  SECOND live sighting of worker drain-window latency on read-only calls (the open P1) —
  the new digest now makes it visible instead of masking it as "pending".**
- 2026-06-12 — **Deploy-pipeline hardening SHIPPED (the incident pair):** deploy.yml gets a
  `concurrency: deploy-production` group (queued, never cancelled — four concurrent runs
  trampled the state lock + raced the immutable-tag gate tonight), and the tfvars CLOBBER
  GUARD is live: key-name manifest in SSM `/uplift/live/tfvars-keys`,
  `scripts/ops/set_tfvars_secret.py` as the blessed setter (refuses missing manifest keys;
  `--allow-remove` for deliberate removals; updates the manifest on success), plus a
  pre-plan check in deploy.yml on the MATERIALIZED tfvars so an out-of-band secret write
  still fails before the state lock. TDD'd (incident shape pinned); bootstrapped live
  (39-key manifest; armed-check clean; clobbered-file probe BLOCKED, exit 1). `ssm:*`
  added to the scoped deploy policy (the /uplift/live/* params + the guard both need it
  once the admin fallback detaches — tonight's 16:48/16:53 plan failures). NOTE: use the
  script for ALL future secret updates — a raw `gh secret set` bypasses manifest upkeep
  (the workflow check still catches removals, but additions won't be protected until the
  next scripted set).
- 2026-06-12 — **Dispatcher window-matching SHIPPED + live-verified (#314); deploy-pipeline
  incident handled (owner session):** each tick now owns the 15-min window ending at its floored
  boundary — windows partition time, so ANY cron minute fires exactly once (TDD: off-quarter,
  boundary-no-double-fire, once-per-tick for `* * * * *`, midnight/DOW crossing). LIVE PROOF:
  an active `7,22,37,52 * * * *` playbook (impossible under exact-minute match; old-image ticks
  logged "0 runs" as the control) fired on the first new-image tick — 18:00Z → MA session →
  "1 playbook run(s)" → run history `schedule · 7,22,37,52 · pending`, registration reused;
  test playbook deactivated after. **Incident en route (cross-lane):** my 16:40Z tfvars-secret
  write CLOBBERED the security lane's REQ-013 dedicated-SG flags (my local file predated them)
  → four subsequent deploys unknowingly tried to REVERT the live SG migration and hung/failed
  on the 45-min Lambda-ENI wait (runs 27429875909/27430132575/27430354813/27432930814). The
  lane restored the flags in the canonical file; I re-encoded the secret, rejected my
  stale-plan run, and the gate caught the revert before approval. NOTE for the security lane:
  run 27432930814's apply carried the revert plan — the next deploy on the repaired secret
  re-applies the dedicated SGs in the fast direction (create+repoint, no deletes); verify the
  Lambda/worker SG state after it. ALSO FILED: deploy.yml has no `concurrency:` group — four
  concurrent runs trampled the state lock + raced the immutable-tag build gate tonight.
- 2026-06-12 — **Settle round 5 — the cache proxy hid continue_turn (live 501):** with rounds
  1-4 deployed, the live turn finally came back 200/unsettled in time — and `POST /chat/continue`
  501'd: prod's `conversation_factory` returns the `CachedConversation` proxy (conv/cache.py),
  which only exposed `send()`, so the route's capability guard fired. The proxy now passes
  `continue_turn()` through under the SAME per-tenant turn lock (terminated session → rebuild →
  settled-empty continue, the client stops). TDD (+1 proxy test); full pytest exit 0.
- 2026-06-12 — **Seed shapes FIXED + the full Greenlight approve path PROVEN live (#309/#311):**
  `build_demo_approvals` now emits APPLIER-shaped drafts (real deal uuids + `changes`; full email
  body WITH an unsubscribe line; issue_quote mirrors the tool shape), module import-safe, shapes
  pinned in CI against the real compliance choke point + appliers; the approvals wipe was removed
  (crm_app has DELETE revoked — a re-seed would have crashed). LIVE repair on the demo tenant:
  3 stuck seeded sends DENIED via the API; 3 fresh drafts inserted via a one-off shim generated
  FROM `build_demo_approvals` (resolved to REAL live deals — the live CRM's 60 deals don't match
  the script's titles, so the shim falls back to live rows). Approve-verified end-to-end:
  **send_email APPROVED (CAN-SPAM passes with the unsubscribe line) → `performed: false` —
  record-only; issue_quote same; update_deal → `performed: true` and the live deal (Saltgrass
  Hotel Collective) actually moved to `negotiation`; Resend send log still 0 emails ever.**
- 2026-06-12 — **Greenlight approve-verification LIVE (owner-directed, Matt's session):** decided
  the demo tenant's 6 pending drafts against the live API. **Record-only PROVEN:** approving
  `issue_quote` → `performed: false — "draft-only until provider go-live"`; `send_email` maps to
  the SAME `record_only` applier, and **Resend's send log is EMPTY (0 emails ever — checked with
  the platform key)**, so nothing has ever left the building. Compliance proved STRICTER than
  record-only: the 3 seeded `send_email` drafts are un-approvable at decide time (422 `CAN-SPAM:
  missing unsubscribe mechanism`) — and un-fixable by edit, because the seeds carry
  `body_preview` not `body` and the edit guard correctly refuses novel keys. The 2 seeded
  `update_deal` drafts approved but the applier KeyError'd (seeds carry `deal`/`field`, not
  `deal_id`/`changes`) — CONTAINED, recorded `performed: false`, no corruption. All three
  seed-shape issues filed in TODO. **Correction to the earlier scheduled-run note:** the 15:15Z
  run's two "pending" entries were UNSERVED READ-ONLY calls (`read_crm`/`query_cube`,
  `approval: None`) surfaced in the run digest — NOT Greenlight drafts (all 6 queue items were
  June 6-8 seeds); the draft-only invariant held regardless (nothing executed). Runner-digest
  pending-overstatement + worker drain-window latency filed as follow-ups.
- 2026-06-12 — **Settle round 4 — bounded stream reads + 504 recovery (live re-test 504'd
  again):** the settle budget is only checkable when EVENTS arrive — a 40s+ silent inference
  round blocked the stream wait past the 60s edge ceiling. Fixes: the MA client is built with a
  bounded SSE read timeout (`UPLIFT_STREAM_READ_SECONDS`, default 20s; httpx timeouts classified
  as stream drops), a reconnect-exhausted/budget-spent drop SURFACES the turn unsettled
  (`stream_interrupted`) instead of raising — /chat/continue re-attaches — and ChatDock treats
  an edge 502/504 as "turn still settling server-side" and recovers through the continue leg
  (never the error wall). One deliberate contract update: the legacy second-drop raise is now
  the surfaced-unsettled shape. TDD (+4 tests); full pytest exit 0; realmode Playwright 14/14.
- 2026-06-12 — **Async turn contract (settle round 3 — the live 504):** holding one request
  can't clear the 60s CloudFront/ALB ceilings (a delegation-heavy live turn 504'd mid-settle).
  Shipped the durable shape: `ManagedAgentsRuntime.continue_drain` re-attaches to the in-flight
  session observe-only (events.list replay + per-session dedupe ledger — the existing
  consolidation machinery), per-REQUEST settle budget (default now 25s), `Turn.settled`,
  `POST /chat/continue` (same auth/kill-switch posture), and ChatDock auto-continues unsettled
  turns with progressive narration — ONE user action, zero nudges, any turn length. Honesty
  upgrade: an UNSERVED side-effecting call no longer renders the false "Prepared an action for
  your approval." copy (it claimed a draft that hadn't landed) — unsettled turns continue
  instead; 3 legacy tests updated deliberately. TDD throughout (runtime continue/dedupe, conv
  settled+continue_turn grounding against the original question, API leg, e2e auto-continue).
  Full pytest exit 0 · typecheck/builds/node units · realmode+knowledge Playwright 23/23.
- 2026-06-12 — **First SCHEDULED playbook run LIVE-VERIFIED end-to-end + Cognito threat
  protection ON (owner-approved, Matt's session):** activated a starter playbook
  (pipeline-hygiene-scout instantiated via the live Studio API as the demo tenant, cron
  `*/15`) — and the first tick produced "0 playbook run(s)": a SECOND firing bug, found live.
  The Fargate dispatch container starts ~30-90s after the EventBridge tick, so the exact-minute
  cron match against `datetime.now()` (15:01:10 for the 15:00 tick) missed on every run. Fixed
  by #299 (TDD): `main()` floors now() to the quarter-hour tick (`_tick_floor`); the live
  failure shape is pinned in unit tests. After the deploy, the 15:15Z tick FIRED the playbook:
  real MA session → "dispatch complete: 1 playbook run(s) across 1 tenant(s)" → run persisted
  (`schedule · */15 · pending`, P0-2 history live for a scheduled run) → 2 draft actions in
  Greenlight with model-authored reasoning, 0 approved (draft-only HELD) →
  `reused_registration: true` (P0-3, no crew leak). Playbook deactivated post-proof. Same
  apply: **Cognito `PLUS` tier + threat protection `ENFORCED`** (owner-approved billing change,
  REQ-012 items 2+4 — verified live via describe-user-pool). Ops note: a local-resolver DNS
  staleness made friesenlabs.com serve Squarespace from THIS Mac only (world DNS verified
  correct via 8.8.8.8/whois/pinned-IP probe — flush the local cache if it recurs).
- 2026-06-12 — **Settle loop ROUND 2 (post-deploy live re-test):** the first deploy still
  clipped — `requires_action` can fire for a DELEGATED THREAD's upcoming work with ZERO
  open calls on the stream (captured: `pending=[{reason: requires_action}]`,
  paragraph-folding confirmed live). Settle v2 waits through requires_action regardless of
  open calls (budget-bounded); a routed Greenlight proposal remains the one immediate stop;
  stream-drop at exhausted budget surfaces fail-closed instead of raising; the wedged-
  session placeholder signal is preserved at stream end. +2 live-sequence tests; full
  pytest exit 0.
- 2026-06-12 — **Agentic chat settle loop (the "clanky chat" fix, owner-reported):** live
  browser test as the demo user proved the diagnosis — `/chat` returned at the FIRST
  `requires_action` idle with the coordinator's `search_rag` calls still unserved (worker race),
  so the customer got "I've asked Scout — I'll report back" as the final answer, needed a human
  nudge to harvest the result, and grounding/citations were skipped (unserved reads in
  `pending_approvals`; captured response: `pending=[search_rag×3], grounding_status=null`).
  Fix (TDD, 5 new unit tests): `ManagedAgentsRuntime.send_message` now keeps draining through
  `requires_action` while open calls are the worker's to serve, bounded by a wall-clock settle
  budget (`UPLIFT_TURN_SETTLE_SECONDS`, default 45s — under the 60s CloudFront-origin/ALB
  ceilings, both verified); on exhaustion / worker-down / stream-end the fail-closed surface is
  byte-identical to before; routed Greenlight proposals never wait (approval is a legitimate
  stop). Narration now paragraph-folds (was jammed `"".join`). Settled knowledge turns leave
  `pending` empty → the grounding/citation pass runs again. Full pytest exit 0. Follow-up filed:
  SSE/async turns for the >60s long tail.
- 2026-06-12 — **Playbook scheduler FLIPPED ON + live-verified (owner-approved, Matt's session):**
  GO_LIVE §7 executed end-to-end via the deploy pipeline. One flag drives both legs since #289
  (`playbook_dispatch_enabled` → the EventBridge rule AND `PLAYBOOK_DISPATCH_ENABLED=1` on the
  api task, so Studio honesty can't drift); tfvars staged `true` + the demo tenant
  (`f0930caa…`, the one provisioned `custom:tenant_id` in Cognito) → secret → deploy → reviewed
  plans → owner-approved applies. **Verified live: rule ENABLED on `cron(0/15 * * * ? *)`, api
  rev 17 carries the env, edge healthz 200, and the 08:15Z aligned tick logged "dispatch
  complete: 0 playbook run(s) across 1 tenant(s)".** Three blockers found + fixed en route:
  (1) #294 — #290's ECS-exec audit policy keyed `count` off apply-time ARNs → every plan died
  "Invalid count argument", and its first failed apply DEREGISTERED the api task def mid-run
  (service kept serving; next apply restored rev 16) — count now rides the static
  `enable_ecs_exec` flag; (2) #295 — deploy retries on an unchanged SHA died on the immutable
  ECR tag → build now skips when the image exists (retries idempotent); (3) #296 — `rate(15
  minutes)` ticks (:12/:27/:42/:57) NEVER match the dispatcher's exact-minute cron check, so
  the minute-0 starter templates could never fire → quarter-hour-aligned cron schedule
  (dispatcher window-matching filed as the durable P1). Also pinned
  `cognito_threat_protection_mode = "OFF"` in tfvars: #290's ENFORCED default 400s on the live
  ESSENTIALS pool tier (enabling it = a billing decision, PLUS tier + mode in one apply).
- 2026-06-12 — **Demo knowledge corpus SEEDED live (owner-run):** `seed_knowledge.py` executed
  as a `uplift-migrate-oneoff` Fargate task on the live api image (`414e82c`) for the demo
  tenant — **26 docs / 26 chunks embedded (Titan V2), exit 0**; retrieval verified by a second
  in-VPC one-off through the production `PgRagClient` (query "what is the discount policy?" →
  top hit `demo:kb:pricing-discount-authority#0` @0.487, RLS-scoped; demo inventory now 195
  upload / 148 call / 132 email docs). Live `/chat` grounding now has a corpus to cite.
  TWO bugs found by the run: (1) `scripts/demo/seed_knowledge.py` + `load_demo_tenant.py` had
  no repo-root `sys.path` bootstrap (script execution puts `scripts/demo` on the path →
  `ModuleNotFoundError: ingest` in the image; worked around with `PYTHONPATH=/app`, FIXED this
  PR + parametrized subprocess regression tests). (2) ~~OBSERVED + UNRESOLVED~~ **ROOT-CAUSED
  + FIXED (follow-up PR):** the task def carries an `aws-otel-collector` sidecar and
  `describe-tasks` returns containers in ARBITRARY order — `containers[0].exitCode` read the
  sidecar's 0 while the `api` container exited 1 (proven by re-describing the failed task:
  otel=0 listed first, api=1). `migrate.yml` had the SAME read in both gates → false-green
  risk on a failed migrate/isolation run; both now query by container name
  (`containers[?name=='$CONTAINER']`). The seed + retrieval-verify runs were re-checked
  by-name: api exited 0 for real on both. Docs de-staled in the same PR (TODO seed items
  checked with evidence; CLAUDE/README demo-seed notes updated).
- 2026-06-11 — **Security-audit remediation batch (P0/P1/P2 from the release-readiness audit):**
  compliance floor moved INTO `Greenlight.propose` (worker/sidecar/playbook paths covered;
  unknown-action fail-closed; violations stored denied) + post-edit re-validation before the CAS
  flip; intra-tenant RBAC (`cognito:groups` → one admin policy, 8 privileged writes gated incl.
  approval-decide, `RBAC_STRICT` migration flag, user-granular global-killswitch operators,
  provisioning bootstraps first user → "admin"); Pg-backed email-token single-use store (replay
  closed); worker org-key fail-loud guard; PII-masked send logs; prompt-injection fences around
  RAG/playbook content; Vega chart-fragment allow-list across all three mirrors + renderer strip;
  landing innerHTML sinks removed; Turnstile widget seam (env-gated, x-captcha-token); SPA
  customHttp.yml (HSTS/CSP/XFO; 'unsafe-eval' only for vega, follow-up noted); 422/400 detail
  hygiene; token_use strict. Infra AUTHORED (REQ-013, zero-diff-at-default): scoped deploy policy
  (+admin-fallback detach path), ADMIN_USER_PASSWORD_AUTH removed, UPLIFT_ENVIRONMENT=prod,
  Cognito threat-protection + admin/member groups, VPC flow logs, WAF logging, cube SG split,
  ECS hardening vars + exec session logging, Aurora-CMK gated vars + runbook, X-Origin-Verify
  rotation runbook. **Verified: pytest 2104 passed/0 failed; web typecheck+build green;
  `terraform validate` green.** Review: 5-way spec compliance ✅; quality review → 1 accepted fix
  (decide admin-gate), 3 findings rejected with evidence (prune `>=` is correct; vega-embed never
  fetches $schema; span/v2 mirror is pre-existing). Audit: `docs/audits/security-audit-2026-06-11.md`.
- 2026-06-12 — **Switchboard RELEASED (REQ-012 executed end-to-end):** the $29/mo `integration`
  module is live for customers. Owner-approved deploy 27394841845 applied the #253 IAM deltas
  (connector Delete/Get on the api role, ListSecrets on ingest) + all four flips
  (INTEGRATIONS_REAL_SECRETS, INGEST_REAL_STORES, nightly rule ENABLED, `ingest_tenants="auto"`)
  + STRIPE_PRICE_ID_MODULE_INTEGRATION (test-mode Price `price_1ThHLBR…`, minted to match the
  deployed test-mode plan prices) — api rolled to `uplift-api:414e82c`, healthz 200. NEW
  **Migrate workflow** (`.github/workflows/migrate.yml`, #276 — one-off DB migrate + isolation
  gate via the OIDC deploy role; no more laptop-AWS-session dependency) ran live:
  `uplift-migrate-oneoff:5` → `api.migrate` exit 0 ("schema + roles loaded";
  `integration_sync_runs` + grants) → isolation gate exit 0 ("[isolation] PASS — RLS enforced").
  Live-verified: unauthed `/api/integrations` and `/api/integrations/{name}/syncs` answer 401
  (mounted + gated). Remaining: REQ-012 step 5 — the first-connect live # VERIFY with a real
  HubSpot/Stripe token (user), then the first nightly `auto` discovery run.
- 2026-06-11 — **Agents & Studio audit P0s IMPLEMENTED (`feat/matt-agents-studio-p0s`):** all
  four release blockers from the morning's audit, TDD throughout (red→green per chunk).
  (1) `draft_email` now REQUIRES a model-authored `body` stored verbatim — the placeholder
  `(draft) Re: <goal>` is dead; generation lives in the calling agent, not a nested model call
  (the worker carries no Anthropic key by design). (2) Run history is real: append-only
  `playbook_runs` (RLS-FORCEd, SELECT+INSERT only) + `PgPlaybookRunStore`; the runner persists
  every terminal `RunRecord` (contained); `GET /studio/playbooks/{id}/runs`; StudioView gets
  **Run now** + a runs panel with draft-only honesty copy ("N drafts wait in Greenlight.
  Nothing was sent."). (3) The MA orphan leak is closed: `ma_coordinator_id`/`ma_agent_ids`/
  `ma_registered_version` persisted at activate/first-run; runner + re-activate REUSE the crew
  while the definition version matches (edits invalidate by construction); full ids never on
  the wire (tails only); trace carries tails. (4) Starter playbooks are fireable: `POST
  /contacts` emits `lead.created` (the #248 producer seam), and asgi now ACTUALLY wires the
  dispatcher to deals+contacts (deal.created was wired-but-inert) behind a fire-and-forget
  `BackgroundDispatcher` (a create never blocks on an agent run); `GET /studio/playbooks`
  reports dispatch state and the Studio banners inert schedule/event playbooks. Hardened for
  schema skew: pre-migrate deploys degrade (activate 200 unpersisted, runs route honest 503),
  never 500. Tests: backend **1996 passed / 33 skipped** (new: draft_email, run store, runner
  persistence+reuse, runs route, contacts producer, BackgroundDispatcher, schema-skew, and a
  `playbook_runs` RLS+append-only proof for CI); web typecheck/build green + a new 6-test
  `studio.spec.ts` (chromium-real) covering Run-now/runs/banner. **Live DB migrate for the new
  table/columns: BLOCKED: Lane Nick** (GO_LIVE_CHECKLIST §7 updated — the schedule-leg flip now
  includes `PLAYBOOK_DISPATCH_ENABLED=1` on the api task).
- 2026-06-11 — **Knowledge audit P0 fixes (`feat/matt-knowledge-p0`, TDD):** the three release
  blockers from the knowledge audit (PR #247) implemented. (1) **Customer corpus-add path:**
  `POST /knowledge/documents` (claims-only tenancy, pydantic body, 422 bounds, honest 503 when
  the ingest plane is unswitched, LOUD 503 on ingest failure — never a quiet no-op) over a new
  `ingest/upload.py` seam (production chunker + embedder, `upload:<slug>-<hash8>#<seq>` refs,
  ALL chunks embed before the first upsert so a mid-doc failure lands nothing); wired in asgi
  behind `INGEST_REAL_STORES` (the CSV-importer posture); KnowledgeView add-document form +
  honest empty-state rewrite (the false "fills in automatically" promise is gone) + 503 degrade
  copy. (2) **Live citation refs fixed:** `conv/rag.py _normalize` now reads the live
  PgRagClient `ref_id` key (was falling back to positional `doc:0` placeholders) and keeps the
  hit's real source; live-shape regression tests pin it. (3) **Grounding observability:**
  `Answer.status`/`retrieved_count` → `Turn.grounding_status`/`retrieved_count` on every /chat
  turn (`grounded` / `no_sources_found` / `ungrounded` / `unavailable` / null-skipped), dropped
  claims logged refs-only (never claim text), ChatDock renders honest notes for non-grounded
  turns. Verified: full pytest exit 0 (29 DSN-gated skips) · web typecheck + mock/real builds ·
  node units · knowledge.spec 9/9 (2 new) · realmode.spec 10/10 (1 new). Caveat learned: shared
  Playwright ports (4173-5) can attach to ANOTHER session's stale preview server
  (`reuseExistingServer`) — a foreign-bundle run produced false failures; rerun with free ports.
- 2026-06-11 — **Agents & Studio customer-readiness audit (`feat/matt-agents-studio-audit`):**
  4-pass read-audit (backend agent plane · web UI · tests/CI · data-layer+infra wiring), claims
  cross-checked between passes; 252 tests green locally (202 unit + 50 integration, RLS-proof
  skips run in CI). Verdict: safety sound (draft-only structural in `Tool.invoke`, trust rule
  uniform, `playbooks` RLS/grants per house convention, real-mode web views genuinely API-wired,
  live registrar real since #236) — automation NOT honest yet: `DraftEmail` returns a literal
  `(draft) Re: <goal>` placeholder (Policy.AUTO → customer-visible), Studio has no Run-now
  button or run history (`RunRecord` persisted nowhere), `PlaybookRunner.run()` re-creates the
  MA crew every invocation (O(runs × roster) orphan leak, ids never persisted), and none of the
  5 starter templates can ever fire (schedule leg owner-gated OFF, event leg unbuilt — zero
  `dispatch_event` callers). 4 P0 / 8 P1 / 3 P2 filed in `TODO.md`; 4 stale Agent-Studio
  site-audit bullets corrected (registrar/marketplace done by #236/#233). Full report:
  `docs/audits/agents-studio-audit-2026-06-11.md`.
- 2026-06-11 — **Switchboard release readiness (audit → build, `feat/matt-switchboard-audit`):**
  audited the $29/mo `integration` module end-to-end (`docs/audits/switchboard-audit-2026-06-11.md`
  — real code, NOT a Sidecar-style empty SKU; blockers were go-live wiring, marketing honesty, and
  the connect→sync loop) then shipped the code side of every gap. **Backend:** new RLS-FORCEd
  `integration_sync_runs` table (partial-unique single-runner guard) + `PgSyncRunStore`; POST sync
  is now ASYNC (202 + background task, concurrent kick 409, 30-min stale-runner reap, exception
  CLASS names only) with `GET /integrations/{name}/syncs` history + `last_sync` in the listing;
  `DELETE /integrations/{name}/credentials` (idempotent disconnect; ForceDeleteWithoutRecovery so
  reconnects never block; DeletedDate = not-connected); verify-on-connect probes (definitive 401/403
  → 422 + nothing stored, inconclusive → stored `verified:null`); account-delete now purges the
  `uplift/{tenant}/{source}` vault slots (honest `skipped_unconfigured` until the asgi wiring);
  `INGEST_TENANTS=auto` — run_sync discovers the tenant set from vaulted slots (ListSecrets
  names-only) so connecting auto-enrolls the nightly sync. **Web:** panel disconnect (inline
  confirm), last-synced line, 202-sync polling; client+mock methods/types. **Honesty:** landing
  "18+ tools"/fake-vendor carousel/"two-way sync & write-back"/"Keep Salesforce or Pipedrive"
  rewritten to the real read-only 4-connector catalog (+ tour/onboarding/constellation + the mock
  screen); stale HOTFIX comment reframed as the boot invariant; `shared/modules.py` docstring fixed
  (no phantom modules.ts). **Verification:** pytest **1994 passed, 0 failed** (+27 new tests across
  routes-v2/secret-writer/account-delete/run-sync-discovery), web typecheck + build green,
  **16/16 integrations e2e** vs the real bundle (3 new: disconnect, last-synced, 202-poll). All
  inert-safe behind the existing switches — the live release is exactly **REQ-012**
  (migrate + DeleteSecret/ListSecrets IAM + flips + the module Price), runbook also folded into
  `GO_LIVE_CHECKLIST.md` § 6.
- 2026-06-11 — **Greenlight customer-readiness audit + hardening (one branch):** 4-pass audit
  (backend core · agent plane · web UI · persistence/tests; spot-check-verified, one finder
  false-positive refuted) — verdict: core sound (structural draft-only, race-free decide,
  FORCE'd RLS), operational shell incomplete. Then implemented ALL filed TODOs, TDD: approval
  expiry (lazy `expires_at`, `GREENLIGHT_TTL_HOURS` default 7d, decide() flips expired + refuses) ·
  `GET /approvals` keyset pagination + `total_pending` + partial pending index (schema appended;
  live apply rides the next migrate — Lane Nick) · isolation-gate approvals probe · `/chat` 409
  while the kill switch is engaged (API boundary, both runtimes; posture documented in
  `conv/session.py`) · record-only approvals logged + honest UI toast ("recorded as a draft",
  never "sent") · status-named decide errors + already-decided UX (specific notice + quiet
  resync) · queue 404 → "not yet enabled" parity · real-mode nav badge (polled `total_pending`)
  + 45s quiet queue polling that never clobbers in-progress edits + refresh · structured "What
  this will do" payload panel (recipient/deal/changes visible pre-approve; also fixed the
  novel-key 422 when editing draft-less payloads) · optional deny reasons · worker TOOLS now
  DERIVED from roster grants (parity by construction) · tz-aware TCPA quiet hours (server-side
  hour from IANA `timezone`, fail-closed on junk) + compliance blocks logged · applier audit
  linkage (`approval_id`/`decided_by` stamped onto `apply_result`) · pool-retry + dial-cache
  read-your-own-write tests. Two deliberate documented postures (v1 all-members-admin,
  append-forever retention) in the audit doc. Verified: full pytest green (1969 passed), web
  typecheck/build green, full Playwright 134/134 ×2 (3 new specs). Also fixed a latent race this
  branch's timing shift exposed in `signup-real.spec.ts`: the spec asserted the TRANSIENT
  "provisioning" step with no synchronization (passes standalone, flaked under suite-parallel
  load — seen failing in both directions); the second stubbed poll answer is now gated on the
  test observing provisioning. Report: `docs/audits/greenlight-audit-2026-06-11.md`.
- 2026-06-11 — **Neural constellation hero (landing):** the hero is now a live, dependency-free
  canvas render of the real 11-product suite — Command Center at the heart, any-to-any transient
  signal routes, product-true activity cards, and a ~9s Security guardrail interception (shield +
  green card + relay to Greenlight) animating the draft-only guarantee. Previous hero (product
  window + roster + trust) moved to the section below; blended H1 "Your AI workforce, working.
  Watched by you." Perf/a11y held: rAF only on-screen + tab-visible, reduced-motion static frame,
  DPR cap, mobile lite density, cards measure-fenced off the text block, canvas layer aria-hidden,
  single `.lp-hero-cta` preserved for e2e. typecheck/build green; 111 Playwright + 30 unit pass.
  Spec: `docs/superpowers/specs/2026-06-11-constellation-hero-design.md`. (Design brainstormed
  against youtiva.com — verified their "globe" is a stock MP4 on a Webflow template; ours is real.)
- 2026-06-10 — **LANE PRODUCT (real-mode tab build-out) — Pipeline · Contacts · Agents · Workflows · Reports:**
  converted five sidebar surfaces from FLStore prototypes to honest, API-wired real-mode views, each
  with loading/empty/error states, no fabricated data, and offline Playwright vs the REAL bundle.
  #146 Pipeline (`PipelineBoard`: RLS `GET /deals` board + Greenlight-gated stage moves) · #148
  Contacts (`ContactsDirectory`: RLS `GET /contacts`+`/companies`, searchable, deal-linked, read) ·
  #151 Agents (`AgentsRoster`: owned MA crew from `GET /agents` — roster + trusted tool policies +
  truncated provisioned ids, never full ARNs) · #155 Workflows (`WorkflowsView`: the OWNED 5-step
  provisioning diagram from `GET /workflows` + IAM-degradable recent-executions feed; REQ-009 the
  states:read grant) · **#157 Reports (`ReportsView`: saved-views gallery from `GET /views`, each
  rendered through the SAME trusted dashboard `SpecRenderer` — spec-not-code, re-validated, zero-rows
  loader in real mode so blocks honestly say "No data yet"; "ask for a chart" rides the EXISTING
  `POST /views/{id}/refine` NL route, degrading to an honest "not live yet" state on 501 when the
  agent runtime/view_patcher isn't wired — same posture as chat's 503. No new backend routes; client
  gained `refineView` + a deterministic mock).** Each PR: draft → adversarial review → squash-merge
  on green CI; #157 review caught a latent cross-view stale-state risk (fixed with `key={selected}`).
  Combined main after the batch: **pytest 819 passed / 5 skipped**; web typecheck + mock/real builds
  (`sample`/`mockData` fold out of `dist-real`) + node units 28/28 + Playwright 66/66 (reports 6 new).
  **(4) Knowledge — SKIPPED this cycle, gated on Lane Ship:** there is NO knowledge/documents/search
  HTTP route in `main`; the RAG chain (`documents` pgvector table + `ingest/` pipeline + `PgRagClient`)
  is reachable ONLY as the agent-side `search_rag` tool through `/chat` (parked 503). Embedding needs
  live Bedrock Titan V2 and the ingest worker is BLOCKED on the Console env-key, so no docs are
  ingested into a live tenant's `documents` table. Building a Knowledge tab over the RAG chain has no
  honest live surface yet. **Follow-up when Lane Ship lands the worker (env-key) + ingests docs:**
  author `POST /search` (or `GET /knowledge/documents`) over `PgRagClient.search()` bound to the
  verified-claim tenant under RLS, then a `KnowledgeView.tsx` (ingested-sources list + grounded
  semantic search with citations, reusing the ChatDock citation components). Remaining stub tabs for a
  future cycle: Billing · Calendar · Email · Templates · Reputation · Sell · Frontline (render the
  honest ComingSoon panel in real mode). (Marketplace, Cortex, Security, Settings, and **Sidecar**
  have since been built into real API-backed surfaces — see later entries.)
- 2026-06-09 — **Cycles 5-6 (lane tail) + LANE MATT COMPLETE:** #67(+hotfix #73: the prod image
  bundles no ingest/ — top-level import would have crash-looped the deployed API; caught by
  adversarial review AFTER an early merge → draft-PR discipline adopted; also closed the shared-
  token cross-tenant sync risk with a 409 connect-first guard) · #68 SelfHostedToolUseRuntime
  (HIPAA seam) + scripts/verify_agent_plane.py (offline-PLAN default, UPLIFT_LIVE_VERIFY-gated) ·
  #70 PostHog funnel + tenant_settings defaults + retry-provision route · #78 typed Integrations
  panel (honest states, token hygiene, 11 offline e2e vs the real bundle) · #80 gated live signup
  e2e (skips w/o STRIPE_TEST_*) + cube dimension_values + synthesizer ref normalization.
  Suite 601→607 passed / 5 skipped (new skip = the gated e2e, by design).
  **Lane Matt queue: every item checked or parked.** Single park: agent_plane.ensure() real impl
  awaits the eager-vs-lazy per-tenant agent-provisioning decision (seam + stub-guard in place).
  Everything else awaiting-live belongs to Lane Nick: REQ-001..007 applies, live verify script run,
  Cube secret wiring, Resend domain, MA env-key rotation.
- 2026-06-09 — **Cycle 4 (deep wiring + remaining provisioning, PRs #42/#47/#50/#56):** #42 cortex+
  spec-gen wired into asgi/worker, coordinator-driven routing (regex gated to FakeRuntime), /api/me ·
  #47 per-request Cube JWT minted from the verified claim (TRUST RULE Cube leg) · #50 ingest
  run_sync entrypoint, per-tenant SecretProvider, ingest_cursor stores migrated to SET LOCAL
  (closes the CLAUDE.md follow-up), Titan batch (VERIFY) · #56 provisioning Lambda handler +
  deterministic claim-ordered SFN trigger. REQ-004/REQ-005 filed. Suite 419→506 / 4 skip.
  TODO true-up: 22 Lane-Matt boxes checked (code-complete; live halves = Lane Nick).
  Two ENOSPC incidents mid-cycle (disk at 100%%) — survived via cache purges; Lexar offload needed.
- 2026-06-09 — **Cycle 3 (real wiring + frontend honesty, 5 PRs + 1 fix-PR):** #34 real provisioning
  deps end-to-end behind a NEW `SIGNUP_REAL_DEPS` master switch (adversarial review caught 2 HIGHs:
  the real-adapter guards rode env vars the live task already injects (COGNITO_USER_POOL_ID, DB_*) —
  a bare image deploy would have flipped live Cognito/Aurora signup on; now fail-closed + regression-
  tested; atomic webhook claim w/ release-on-failure) · #33+#38 web real-mode shell, honest
  loading/empty/error states, ?apimock seam deleted from prod bundles · #32 persistent tenant-scoped
  Cortex registry (S3+LocalFs) · #31 Anthropic view-spec generator (validate+retry). REQ-003 filed
  (Stripe/Resend/webhook-secret/admin-key/SIGNUP_REAL_DEPS task wiring). Suite 344→399 / 4 skip.
  Cycle-4 queue: cortex+spec-gen asgi/worker wiring, Cube JWT mint, ingestion scheduler entrypoint +
  per-tenant connector creds, lambda_handler + SFN trigger, requirements lock + refresh rotation +
  demo-affordance strip.
- 2026-06-09 — **Cycle 2 (asgi integration + provisioning foundations, 4 PRs + 1 fix, all reviewed PASS):**
  #28 real `conversation_factory` + tool executor (/chat end-to-end on the runtime seam; tenant↔env
  binding fixed; REQ-001 filed) · #27 signup tokens + Pg account/event/OTP stores (+`accounts`/
  `stripe_events` RLS-EXEMPT tables; REQ-002 filed) · #26 Stripe + Cognito admin adapters · #25
  draft-gated Resend/SNS senders + Anthropic Admin client · #29 fix: cross-PR auto-merge orphaned
  Config fields (combined-main check caught it; per-branch suites were green). Suite 249→344 / 4 skip.
  Cycle-3 queue: prod_deps real wiring + verification flow, stub-id guard, cognito confirm()
  tightening, Frontend P1, Cortex registry, build_view generator.
- 2026-06-09 — **Cycle 1 (AI plane, 4 parallel module PRs, all adversarially reviewed PASS):**
  #22 `tenant_workspaces` + `WorkspaceStore` (RLS FORCE'd, PgApprovalStore pattern) · #21
  `ManagedAgentsRuntime` implemented (env/agent/coordinator/session/vault + event-stream
  `send_message`, 12 VERIFY flags, hard limits enforced) · #20 `api/pg_clients.py` (PgRag/PgCrm,
  allow-listed, SET LOCAL proven in tests) · #18 `conv/synthesizer.py` (citation invariant
  enforced, graceful extractive fallback). Suite 195→249 passed / 4 skipped. Cycle-2 follow-ups:
  tenant↔environment binding via store lookup in the conversation factory (review medium), asgi
  factory+executor wiring, provisioning upsert, worker client wiring.

## Lane Ship (deploy / go-live flips) — log
- 2026-06-10 — **Cycle 1 (step 1/10):** `api/Dockerfile` ships `ingest/` (`COPY ingest/ ./ingest/`).
  Verified the crash premise: `api/integrations_routes.py` + `api/pg_clients.py` lazy-import
  `ingest.run_sync`/`ingest.embed` at call time — without the package the uplift-ingest sync and
  the RAG embed leg ImportError in the live image. `ingest/` is stdlib-only at import time; its
  lazy deps (boto3/psycopg2) are already in `requirements-api.txt`. arm64 image build verified
  locally. Next: roll the api image to current main via deploy.yml (live is 44 commits stale).
- 2026-06-10 — **Cycle 5 (step 5/10, worker go-live):** uplift-worker image built (arm64, immutable
  sha tags) + `module.worker` applied targeted (5 pure adds: service/task-def/log-groups +
  `worker_absent` alarm). First task crash-looped: `worker.py run()` lazy-imports
  `api.control.greenlight` + `api.pg_clients` (→ `ingest.embed`) which the worker image omitted —
  fixed with `COPY api/ ./api/` + `COPY ingest/ ./ingest/` (local container run reaches the real
  Anthropic env poll loop). tfvars synced: machine + worktree + GH secret `PROD_AUTO_TFVARS_B64`
  (also fixed stale `api_image` e0794bc→682b2ea there). Verify: workers_polling + alarm pending
  the fixed-image roll.
- 2026-06-10 — **Cycles 6-8 (steps 6-8):** Signup go-live: `api_signup_env` flip (rev-8, 6 additive
  secret injections) then `signup_real_deps` as its own apply (rev-9, SIGNUP_REAL_DEPS=1,
  ALLOW_REAL_SENDS unset); live probes: /signup 422-validates, /webhooks/stripe 400s unsigned.
  **tfvars state-reconciliation:** machine copy was missing 10 applied vars (full plan = 15 destroys
  incl. Amplify/DNS-zone/Lambda); all recovered from state, GH secret re-synced — full plan clean.
  Admin-key VERIFY (live): workspace create/list/archive ✅; assumed key-create **405** + limits-write
  **404** (issue filed — Console pre-minted pool per the ratified brief); flag applied, Lambda env
  verified. **Route53 recon:** rogue zone Z0599822DN7S53EA8VCJ DELETED (dangling djvyqxdhlili4
  CloudFront alias = takeover risk, validation CNAME for a nonexistent cert, zero public references —
  records backed up); TF zone NS set documented for the Squarespace cutover (user act); TLS sequence
  parked on cutover + cert ISSUED.
- 2026-06-10 — **Cycle 9 (step 9):** live-signup-e2e red-on-main root-caused: NOT the skip guard —
  secrets are wired and the tests RUN; `signup/payment.py` reused ONE idempotency key across two
  Stripe endpoints (customers + checkout.sessions) → idempotency_error on every run. Fixed with
  per-endpoint suffixes (`:customer`/`:checkout`); offline suite green; live proof = next main push.
- 2026-06-10 — **Cycle 10 (step 10):** Cognito MFA → OPTIONAL + software-token TOTP (ON would force
  enrollment on the demo user's next Hosted UI login — enforcement flip stays a deliberate later
  act). Billing alerts CONFIRMED: budgets uplift-200-ceiling + uplift-monthly ($500) with ACTUAL
  50/80% + FORECASTED 100% notifications to the restored notify_email, all OK. The legacy
  "Receive CloudWatch billing alerts" account preference is OFF and console-only — optional 1-click
  for Matt; budgets do not need it. Step 9 live proof landed: post-merge main push ran
  live-signup-e2e against Stripe TEST mode → SUCCESS (main fully green).
- 2026-06-10 — **Cycle 11 (step 3 root-cause, #147 — user-approved cross-lane):** live MA session
  forensics (per-thread events) proved the worker DOES resolve delegated read_crm calls with REAL
  Aurora data (the critic's bash input literally contained the Meridian negotiation amounts,
  284000 first). TWO defects wedged the surface: (1) `agents/runtime.py` granted every agent the
  built-in `agent_toolset_20260401` that NOTHING serves → first native call (critic's bash) blocks
  the session at requires_action forever — toolset grant removed (also keeps model-driven bash out
  of the creds-laden worker env); (2) `api/asgi.py` built a NEW Conversation → NEW MA session per
  /chat request, orphaning worker-resolved reports — per-tenant Conversation cache added (per-tenant
  send lock, rebuild-once on terminated). Suite green. Live remediation after roll: agents.update
  to strip the toolset from the existing live agents.
- 2026-06-10 — **Cycle 12 (STEP 3 VERIFIED — chat returns real data end-to-end):** turn-2 in the SAME
  MA session answered with the live Meridian pipeline: **8 deals in Negotiation, $438,550** (Westlake
  Galleria chiller retrofit $284K top). Four stacked defects found+fixed: unserved native toolset
  grant (#156) · session-per-request (#156 cache) · sequential work-queue starvation by 9 dead
  bash-wedged sessions (deleted; worker x2 + bounce) · coordinator multiagent pinned specialist v1
  (repinned v2, coordinator v3; dangling native bash answered with is_error tool_result). #147
  CLOSED with evidence; hardening follow-ups filed (#161: worker logging, native-tool refusal,
  queue hygiene, ensure() roster repin, codify worker desired_count). LANE TALLY: 9/10 steps
  landed + verified; step 8 blocked SOLELY on the owner's Squarespace NS change (TF zone NS set
  documented; TLS sequence ready to execute on cutover).
- 2026-06-10 — **Cycle 13 (STEP 8 COMPLETE — 10/10): full TLS chain live.** NS cutover (user,
  Squarespace → the TF zone's awsdns set) propagated in minutes; `dns_delegated=true` →
  ACM cert ISSUED (friesenlabs.com + wildcard). Cutover IaC authored as 3 one-flag phases
  (#164, review caught a real :80 destroy-before-create collision — depends_on added):
  (a) ALB :443 (validated-cert gate) + 443 origin-verify twin + api.friesenlabs.com alias —
  3 pure adds, :80 untouched; (b) CloudFront origin → https-only via api.friesenlabs.com
  (RUNBOOK amended: raw ELB hostname can't validate TLS — named origin required); Deployed +
  edge 200×3 + signup 422 through the https chain; (d) :80 forward retired → 301 redirect
  (reachable only from the CloudFront prefix list, by SG design); direct :443 no-header → 403 ✓.
  Live path now: browser → Amplify → CloudFront → **HTTPS** → ALB(443, real cert) → API.
  Remaining (not steps): apex/www records decision (owner), api_cdn retirement (TODO 210/211),
  #161 hardening, Node-20 actions bump.
- 2026-06-10 — **Node-24 actions bump COMPLETE (closes the cycle-13 remainder):** the #161
  hardening PR (#167) had bumped `configure-aws-credentials` v4→v5, but a Codex P2 review comment
  caught that v5 still declares `runs.using: node20` — the node24-native major is v6 (verified
  against the action manifests; v6.0.0's only breaking change is the runtime, needs runner
  ≥ v2.327.1, satisfied by `ubuntu-latest`). Fix authored on `feat/nick-creds-node24` (#169),
  squashed into the #167 branch, then #167 (closed unmerged after its content was pushed to main
  by the parallel lane — reopened as the one-commit v6 diff) merged to main @3f68c0c with CI
  green. All three credential steps in `deploy.yml` now `@v6`; the next prod deploy no longer
  depends on the `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24` runner override.
- 2026-06-10 — **Cycle 14 (post-run follow-up batch):** (1) **Live signup 500 FIXED** — the signup
  plane had ZERO cognito-idp grants (AdminCreateUser AccessDenied); the exact four admin ops now
  granted to the api task + provisioning Lambda, single-pool-scoped (#168, review dropped the
  call-site-less AdminSetUserPassword); live re-probe: /signup 200 {account_id, state:created}.
  FORCE_CHANGE_PASSWORD confirm path still unimplemented (app lane — new users can't password-login
  yet). (2) **Apex authored+applied** (#166, review-hardened: ' CNAME ' parse, trimsuffix,
  allow_overwrite, try() count-0): Cognito callbacks extended, records created — association
  **FAILED on a FOREIGN CloudFront distro (djvyqxdhlili4, not this account) still claiming
  friesenlabs.com** (the deleted rogue zone's target — likely Nick's personal AWS); apex+www blocked
  until that alias is released, then re-run via -replace on the domain association. (3) **#161
  hardening landed+rolled** (#167 + the user's v6 credscredentials fix): worker INFO logging LIVE
  (poller trail in CloudWatch), desired_count=2 codified, ensure() pinning constraint documented,
  Node-24 actions forced ahead of the 6/16 flip. (4) api_cdn retirement: RECOMMEND AGAINST (TODO
  note) — it stamps X-Origin-Verify + carries the WAF; Amplify proxy can't add headers.
- 2026-06-10 — **Domain root-cause + fix (user-directed, Matt's session):** "site cannot be
  reached" on friesenlabs.com diagnosed live. Findings: the Squarespace **NS cutover is DONE**
  (whois + SOA show the 4 awsdns servers) and the wildcard ACM cert is **ISSUED** — the
  PENDING_VALIDATION status in the docs was stale. The real blocker: **CORRECTION to Cycle 14** —
  the "foreign CloudFront distro" claiming friesenlabs.com (djvyqxdhlili4) was NOT external; it
  was this account's own **stale us-east-2 Amplify app** `friesenlabs` (d1zq690gmmatpq, repo
  theogyeezy/friesenlabs branch `prod`, created 2026-05-31, serving 404) holding an AVAILABLE
  domain association — invisible to us-east-1-only scans. Actions: deleted that app's domain
  association, then (user-directed) the app itself; deleted the FAILED uplift-web association and
  re-created it (apex+www → `main`); Route53 apex A-alias + www CNAME repointed to the newly
  minted Amplify CloudFront target (dz0mzuwjm2p3n). Association in AWAITING_APP_CNAME →
  propagating at write time; a retry loop watches it to AVAILABLE. With the cert ISSUED, the ALB
  TLS cutover (RUNBOOK) is now executable — nothing remains owner-gated for the domain.
- 2026-06-10 — **Domain LIVE (follow-up to the root-cause entry, #172):** the re-created uplift-web
  domain association reached **AVAILABLE** on attempt 1; **https://friesenlabs.com + www verified
  live** (200 over the `*.friesenlabs.com` cert, correct landing title). (NB the "claim released"
  in Cycle 15 below = the deliberate eviction of the us-east-2 app per the root-cause entry.)
- 2026-06-10 — **Cycle 15: friesenlabs.com IS LIVE.** The conflicting foreign distro
  (djvyqxdhlili4) stopped resolving — claim released; domain-association recreate (-replace)
  went AWAITING_APP_CNAME → PENDING_DEPLOYMENT → AVAILABLE. Verified: https://friesenlabs.com
  200 (real title) + www 200, wildcard cert served, and a FULL browser login on the apex domain
  (Hosted UI accepted the new redirect_uri → code exchange → Command Center signed in). The
  product — marketing site, app shell, login, API (via /api proxy → CloudFront → HTTPS ALB),
  agents+worker, signup — is end-to-end live on the real domain.
- 2026-06-10 — **ALB TLS cutover CONFIRMED DONE + verified (Matt's session):** the hourly sweep
  had already executed the RUNBOOK sequence once the cert went ISSUED. Live verification: ALB 443
  serves the real `friesenlabs.com` cert (CN match, exp 2026-12-24) with the 403-default
  origin-verify gate (direct no-header curl → 403); api_cdn origin = `api.friesenlabs.com`
  https-only:443 (edge /healthz 200; SPA /api/healthz 200 via friesenlabs.com); :80 is a redirect
  listener, SG-scoped off the public internet. No 301 loop. api_cdn retained per the Lane Ship
  RECOMMEND-AGAINST. Follow-on hardening still open: drop the :80 SG rule (#211), CF min-TLS (#257).
- 2026-06-10 — **Cycle 16: cube data plane wired to the API (#175) + edge timeout fix (#176).**
  api rev-11 carries CUBEJS_API_SECRET_VALUE; cube_client_from_env() live in executor + /chat
  ToolContext. CloudFront origin_read_timeout 30→60s (in-request multi-tool turns 504'd at the
  default — hit live). RESULT: complex tool turns now complete IN-REQUEST (200 in ~15s, no
  deferral): the agent ran query_cube via two specialists, REFUSED to fabricate when it returned
  zero rows, and reported the real CRM pipeline ($2,596,850 / 60 deals; negotiation $438,550 —
  matches the step-3 verification). Remaining cube defect ROOT-CAUSED + filed (#177): Cube
  connects as crm_app but never sets app.current_tenant, so FORCE'd RLS blanks every query —
  fix design (TenantBoundPostgresDriver via driverFactory) in the issue. Dashboards' /views data
  route + web loader remains the separate product slice.

## 2026-06-11 — FLEETAGENT customer-readiness + live deploy (Lane Nick/boss)
- **Deployed to prod (success):** api `:11→:12`, **cube rolled to the #177 RLS-fix image** (steady
  state — Cube now sets `app.current_tenant`, governed queries return tenant rows), worker on the
  data-plane image, provisioning Lambda on the #197 ARN-fetch image; edge `/healthz` 200; live
  migrate + isolation gate PASS. Two deploy-time bugs found+fixed: added `build-images.yml`
  (deploy.yml only built api); cube must be **amd64** (Fargate) and the provisioning Lambda needs a
  **Docker-v2 manifest** (`--provenance=false`) — buildx's default arm64/OCI-index failed ECS pull
  (`CannotPull … platform linux/amd64`) and Lambda (`InvalidParameterValue media type`).
- **Customer-readiness wave merged:** auth recovery (#206), Stripe billing portal (#209), support +
  status page (#210), signup abuse controls (#207), per-tenant rate limits + usage quotas + cost
  attribution (#211), first-run onboarding (#212), landing provision-CTA fix (#205).
- **Bug fix:** lazy DB connection pools (`minconn=1`, #213) — stores were eagerly opening the full
  10-conn pool (≈180 idle Aurora conns; exhausted CI Postgres → recurring python-CI failures).
- **Secret:** `PROD_AUTO_TFVARS_B64` now carries the corrected cube/provisioning image tags + the
  Stripe TEST price IDs ($99/$299/$799).
- **Not yet rolled:** the customer-readiness + MVP code is on `main` but needs the next Deploy +
  Amplify web build to go live. **Owner-gated:** seed the workspace-key pool (Console).

## 2026-06-11 — customer-readiness DEPLOYED + unit-test wave + /fleet tooling (boss)
- **Second deploy SUCCEEDED:** api rolled `:12 → :13` from `f9b2df2` (all customer-readiness +
  MVP backend). The 4 new tables (`support_requests`, `onboarding_state`, `usage_counters`,
  `cost_events`) + grants migrated live (exit 0) + isolation gate PASS before the roll; `/healthz`
  200. Web UI ships via Amplify on main push. So customer-readiness + MVP backends are now LIVE
  (were "merged, awaiting roll" in the prior README — corrected).
- **Follow-up fixes merged:** lazy DB pools `minconn=1` (#213, was hoarding ~180 idle Aurora conns
  + exhausting CI Postgres); `support_requests` crm_app grant (#215, the support endpoint was dead
  code without it).
- **First `/fleet` agent-skill run (#217):** the v2 fleet skill (model-tiers-in-each-lane) ran its
  first real wave on this repo — 77 new unit tests across 4 file-disjoint modules (billing/support/
  limits/leads routes) that had integration-only coverage; 4/4 confirmed by the 3-skeptic panel,
  boss-verified (pytest), merged green. Repo fleet hygiene: `.claude/worktrees/` gitignored +
  `.claude/fleet-lessons.md` seeded (#216).
- **Still owner-gated:** seed the workspace-key pool (Anthropic Console) for real paid provisioning;
  (Cortex) live S3 registry + a real retrain + seeded knowledge corpus. Legal/Terms/Privacy pages
  + placeholder-501(c)(3) landing copy still deferred (#119/#121).

### Site-completeness backlog build — `/fleet` waves (2026-06-11)
After a 10-assessor site-completeness audit (78 features → `TODO.md`), the buildable backlog (excluding
landing-legal + owner-gated infra flips) ran as serial `/fleet` build waves — each: file-disjoint
tiered builders in isolated worktrees → 3-haiku refute-by-default panel → boss squash-merge → CI gate.
- **Wave 1 (#222):** abuse controls→prod_deps; Cube data endpoint (`POST /views/{id}/data`); CRM
  structured sink; Cortex prediction logging; status rollup fix. **3 confirmed, 0 rejected.**
- **Wave 2 (#223):** web→Cube live-data loader; `PlaybookRunner`; `GET /account/export`. **3/0.**
- **Wave 3 (#224):** `view_patcher` NL refine; connectors VERIFY hardening; Cognito password fix.
  **3 confirmed, 1 rejected** (contacts-deals-crud — skeptic caught a REAL silent-`contact_id`-drop
  data-loss bug in `POST /deals` + a misleading error; boss-fixed → #225).
- **CRUD (#225):** create/edit contacts & deals + the `contact_id` boss-fix. _(The fix rippled into a
  SQL-pin unit test + a "contacts read-only → 405" integration guard — both caught by CI, updated.)_
- **Wave 4 (#226):** `POST /studio/playbooks/{id}/run` (manual playbook trigger, draft-only);
  `POST /account/delete` (GDPR teardown, confirm-gated, append-only-aware, inert-by-default);
  `GET /billing/invoices`. **3 confirmed** (1 was a verifier FALSE-NEGATIVE — diffed the wrong git
  base inside the worktree and claimed "no code"; boss hand-verified: 11 tests pass, endpoint mounted).
- **Metrics across waves:** 12 build tasks, 11 merged, 1 genuine rejection (real bug, then fixed), 1
  false-rejection (recovered). Main stayed green throughout; every feature CI-gated + adversarially
  reviewed.
- **Web-UI wave (#228 + #229):** the backlog's Cortex/CSV/billing UI items pointed at the demo-only
  `web/src/screens/*` mock prototype; the REAL authed app had no such tabs. Built net-new real-mode
  views in `web/src/api/*` consuming pre-wired client methods (#228): **CortexView** (`GET /cortex/health`,
  honest no_registry/no_champion/404-rollout states, NO number simulation — the honesty fix done in the
  *real* app), **CSV upload** (IntegrationsPanel), **invoice display** (BillingManage), **account
  export/delete** (Settings). The wave's 4 builders each rewrote the shared client.ts differently and the
  verifier panel false-rejected all 4 (same git-state confusion as Wave 4 — main checkout left on a
  feature branch broke their origin/main diffs); boss hand-verified, reconciled client.ts against the
  BACKEND (corrected CortexDrift.registered_auc, Integration.kind/csv_import_configured, the real
  CsvImportReport shape), fixed an account-view field bug + the cortex e2e nav. Verified: typecheck +
  mock/real builds + **126 Playwright e2e** + 30 node tests green (#229).
- **Depth wave (#231 + #232 + #233):** the four remaining depth items, all confirmed + verified:
  **CRM-table landing** (CSV imports land in Pipeline/Contacts via `default_structured_sink`; the
  ref→uuid `PgCrmStructuredSink` already existed — #231); **status probes** (`GET /public/status`,
  unknown-never-degrades rollup + a commit-review security fix that sanitized the public probe-error
  detail — #232) wired into the web `fetchStatus`; **settings persistence** (`GET/PUT /account/settings`
  over new `tenant_settings` columns + a real-mode Workspace settings UI that saves — #232/#233);
  **agent-marketplace** (the `/studio/templates` backend already existed — built the real-mode
  `MarketplaceView` browse+hire — #233). The web half of #233 was built directly (not a fleet wave) to
  avoid another shared-`client.ts` reconciliation; verified the same way: typecheck + mock/real builds +
  131 Playwright e2e (+5 new) + 30 node tests. account-delete deliberately kept INERT (owner decision).
- **Remaining:** owner-gated infra flips/seeding (workspace-key pool, cortex_s3/ingest/integrations
  tfvars, knowledge corpus, EventBridge legs, the settings column-migrate + real status probes in asgi,
  the ingest-env flip for live CRM-table landing) and the deferred landing/demo-honesty + legal pages.
  **Every buildable item (ex-landing-legal) is shipped; what's left needs owner action, not code.**

## Module entitlements ("provision/show only what the user selects") — 2026-06-11
- **Feature:** per-tenant module catalog (`shared/modules.py`, 10 modules + required Command Center
  spine, each carrying a route set + price + Stripe `price_env`). The app shows ONLY enabled modules:
  `GET /account/modules` returns the catalog + enabled route-ids + à-la-carte monthly total; the SPA
  gates its nav sections + route render against it (fail-OPEN — show-all on 503/404/error so the gate
  can never strand a tenant). Settings → **"Your suite"** (`web/src/api/ModulesView.tsx`) toggles
  modules, shows the live monthly total, and re-gates the app on save. Default for an un-tailored
  tenant = **full suite (opt-out)** so no existing tenant loses a surface on deploy.
- **Storage:** `tenant_settings.enabled_modules jsonb` (schema.sql) via `PgSettingsStore.get_modules/
  set_modules`; route in `api/modules_routes.py` (THE TRUST RULE: tenant from the verified claim;
  required modules forced on; unknown ids dropped; resilient GET → default catalog pre-migrate).
- **Phase-2 billing ("selection sets the price"):** `StripeAdapter.sync_subscription_modules`
  reconciles the tenant's subscription items to the enabled set (only ever touches MODULE items, never
  the plan-tier line); orchestrated by `api/module_billing.ModuleBillingSync` (tenant → account →
  `stripe_customer_id`). **Inert until the owner mints per-module Prices** — `from_env` returns None
  with no `STRIPE_PRICE_ID_MODULE_*` set, so the PUT just persists + re-gates (best-effort sync,
  non-fatal on Stripe error, reported in the response + an honest UI note). Infra carries a new
  `stripe_module_price_ids` map var (inject-only-when-set; `terraform validate` clean). Activation
  steps recorded in `GO_LIVE_CHECKLIST.md`.
- **Tests:** `test_modules_catalog.py` + `test_modules_routes.py` + `test_modules_billing.py`
  (catalog normalization/routes/totals/price-resolution, GET/PUT incl. trust-rule + billing-wired
  PUT + non-fatal billing error, adapter add/remove/no-op/no-sub). Web typecheck + mock/real build
  green.

## Cortex depth — estimator + features + drift alerting + infra cleanup — 2026-06-11
- **Estimator bake-off upgraded:** added a real pure-Python **GradientBoostedTrees** (logistic-loss
  GBDT over shallow CART trees) alongside the existing logistic regression, floored by the majority
  baseline (`ml/estimator.py`, `ml/train.py`). The held-out-AUC bake-off keeps the winner per tenant;
  the GBT captures feature interactions logreg can't (proven: out-separates logreg on an interaction
  pattern). No new deps (stays GPU/heavy-dep-free + offline-testable).
- **Feature set enriched 5→9:** added derived signal (log-amount, engagement velocity, recency flag,
  contact-completeness) built only from fields both the training loader and `run_model` inference
  already produce, so train/serve parity holds by construction (`ml/features.py`). APPEND-ONLY contract.
- **Drift alerting wired:** `ml/drift_alert.py` publishes a positive live-drift verdict to the Cortex
  drift SNS topic; the retrain fan-out (`scripts/ml/retrain_all.py`) calls it best-effort (alert
  failure never fails the retrain). Inert without `CORTEX_DRIFT_TOPIC_ARN`. Infra: the drift topic +
  an optional email subscription + `sns:Publish` grant + env injection now live in
  `infra/modules/scheduled_jobs`; new `cortex_drift_alert_email` tfvar.
- **Legacy infra removed:** deleted the dead, target-less `module "cortex"` (its drift topic moved to
  `scheduled_jobs`, now wired to a real publisher). `terraform validate` clean.
- **Tests:** `test_ml_estimator.py` (GBT learns/interaction/deterministic/proba-range + feature
  contract), `test_ml_drift_alert.py` (publish-on-drift, no-page-when-fine, inert-without-arn),
  extended `test_retrain_all.py` (alert path + non-fatal alert failure), updated `test_ml_train.py`.
  Full ML suite green. **Still owner-gated:** S3 registry + signing-key value + retrain enable + a
  drift subscription + one seeded retrain (GO_LIVE_CHECKLIST §5).

## Sidecar — built into a real product (was a SKU with no backend) — 2026-06-11
- The audit found Sidecar was vaporware: a $35/mo module with an empty routes tuple, a static mock
  screen, landing copy, and **zero backend** (no route/agent/tool/table/tests). Built a real, honest v1.
- **What it is now:** the agentic layer over the tenant's CRM. `api/sidecar.py` is a PURE suggestion
  engine that turns already-read deals + contacts into grounded next-actions (aging open deal →
  follow-up; unreachable contact → enrich; unlinked deal → attach a contact; stale contact →
  reconnect). Every suggestion references a REAL row — nothing fabricated.
- **Backend:** `GET /sidecar/suggestions` (RLS reads via the SAME PgCrmClient as /deals + /contacts)
  and `POST /sidecar/act` — accept enqueues a **Greenlight DRAFT** via the existing gate + appliers
  (`create_activity`/`update_*`), so Sidecar never writes the CRM directly (the draft-only constraint).
  Security: accept takes a suggestion **id**, the server recomputes + resolves the action server-side
  (a client can't inject an arbitrary Greenlight action); THE TRUST RULE (tenant from the claim);
  defense-in-depth tenant-isolation check on every row; honest 503 (unconfigured) / 409 (stale).
- **Frontend:** real-mode route `sidecar` → `web/src/api/SidecarView.tsx` (suggestion cards + "Send to
  Greenlight" → links into the approvals queue; honest empty/503/409/truncation states). The module
  catalog now gates the `sidecar` route (was empty) so a tenant who enables Sidecar actually gets a
  surface — closing the "pay $35 for nothing" gap the entitlements/billing work exposed. Mock-mode
  FLStore Sidecar prototype is unchanged (walled off by realMode).
- **Wiring:** `SidecarDeps(crm=...)` (inert-None default → honest 503) mounted in api/app.py + wired
  live in api/asgi.py. **Tests:** `test_sidecar.py` (engine: each kind, closed-skip, determinism,
  truncation) + `test_sidecar_routes.py` (503/401/grounded items/isolation-500/act-enqueues-draft/
  tenant-from-claim/409/unconfigured). Web typecheck + mock/real build green.

## CRM-depth (#312) DEPLOYED LIVE — migrate-before-apply ordering — 2026-06-12
- **Merged + deployed:** #312 (9 CRM-depth features: tasks, contact/company dedupe-merge, archived
  views, deal search, won/lost close reasons) squash-merged as `353e57e` (branch updated + CI re-ran
  green first — branch protection requires up-to-date). Deployed via the no-breakage ordering:
  deploy.yml `build` pushed `uplift-api:353e57e` → **migrate.yml ran on the NEW image while the old
  code still served** (one-off task def `uplift-migrate-oneoff:7`; **migrate exit 0, isolation exit 0**
  — "[isolation] PASS — RLS enforced") → only then the apply gate was approved → api task def **rev 29
  on `:353e57e`**, services-stable + edge healthz green (run 27432226222 attempt 5).
- **Incident found + fixed en route — `PROD_AUTO_TFVARS_B64` drift:** the CI secret lacked the REQ-013
  `worker_dedicated_sg`/`provisioning_lambda_dedicated_sg` flags (flipped live via local targeted
  applies), so CI deploys planned a silent REVERT of the SG split. An earlier approved apply (a224425)
  deleted the 5 worker/lambda egress+db SG rules at 16:59Z (worker + provisioning Lambda lost
  Aurora/cube/egress reach ~75 min) then deadlocked 45 min destroying SGs whose releasing updates were
  graph-ordered behind the destroys; a second identical run (294f22c, stale plan) was cancelled and its
  orphaned S3 state lock removed. Fix: secret re-synced from the machine-local `infra/prod.auto.tfvars`
  (the canonical carrier) → re-plan recreated the 5 rules (connectivity restored by the green apply),
  zero SG destroys, and the REQ-013 `github_deploy_admin` detach re-converged. `rbac_strict=true`
  (the parallel lane's flip) rode along on rev 29 — not reverted. `api_image` bumped to `:353e57e` in
  BOTH the local tfvars and the secret post-deploy.
- **Gotcha recorded:** `gh run rerun --failed` does NOT regenerate the plan — apply re-uses the saved
  `deploy.tfplan` artifact → "Saved plan is stale" whenever state moved. Cancel + rerun ALL jobs to
  re-plan at the SAME headSha (build skips re-push via the immutable-tag guard), keeping
  migrate-SHA == apply-SHA.
- **Live verification (through the real edge, PKCE demo JWT):** CloudFront `/healthz` 200 + SPA
  `/api/healthz` 200; unauth `/tasks` → 401. `GET /tasks` 200 → `POST /tasks` 201 → `complete` set
  `done_at` (RLS-scoped on Aurora). Close-reason e2e: `move-stage closed_won + reason` → **queued in
  Greenlight** (gated `update_deal`, no error) → approved via `/approvals/{id}/decide` → deal re-read
  shows `stage: closed_won` + the exact `close_reason` persisted. Worker 2/2, cube 1/1, rollouts
  COMPLETED. SPA with the Tasks tab shipped via Amplify (jobs 309–311 SUCCEED). Direct
  `api.friesenlabs.com` 403 = the origin-verify gate working as designed (not an outage).
