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
