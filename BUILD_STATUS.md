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
| 1 | Data Plane (Aurora+pgvector, RLS, schema, S3, Redis) | ✅* | orchestrator | ✓ | ✓skip | ✓ | · | ✓skip | self ✓ |
| 2 | Ingestion & Embeddings (connectors, chunk, Titan, pipeline) | ✅ | bg-agent | ✓ | ✓skip | · | · | · | cross ✓ |
| 3 | Semantic Layer (Cube deploy, metrics, tenant security ctx) | ✅* | orchestrator | ✓ | · | ✓ | · | ✓ | self ✓ |
| 4 | Agent Plane (Managed Agents, roster, vaults, worker) | ✅* | orchestrator | ✓ | ✓ | · | · | · | self ✓ |
| 5 | Control Plane (autonomy, Greenlight, traces, kill switch) | ✅ | orchestrator | ✓ | ✓ | · | · | · | self ✓ |
| 6 | Conversational Layer (front door, slots, agentic RAG+cites) | ✅ | bg-agent | ✓ | ✓ | · | · | · | cross ✓ |
| 7 | Dashboard Engine (view-spec, generate, render, save/edit) | ✅ | orch+agent | ✓ | · | ✓ | ✓ | · | cross ✓ |
| 8 | Cortex / ML (per-tenant models, train, registry, retrain) | ✅* | orchestrator | ✓ | · | · | · | · | self ✓ |
| 9 | App, Auth & API (Cognito, FastAPI/Fargate, ALB, web) | ✅ | orch+agent | ✓ | ✓ | · | ✓ | ✓ | cross ✓ |
| 10 | Acquisition, Signup & Provisioning (landing, Stripe, auto-provision) | ⬜ | — | · | · | · | · | · | — |
| 11 | Cost, Guardrails & Observability (budgets, caps, CloudWatch, OTEL) | ⬜ | — | · | · | · | · | · | — |
| 12 | IaC, CI/CD & Launch (Terraform/CDK, pipelines, smoke+isolation) | ⬜ | — | · | · | · | · | · | — |
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

## Follow-ups (non-blocking cleanups)
- **`ingest_cursor` table** — `ingest.pipeline.PgCursorStore` creates a per-tenant cursor side-table
  on construction without RLS. Fold it into `db/schema.sql` with `tenant_id` + FORCE'd RLS before
  any live DB use (currently only touched when a real DSN is supplied; offline tests use fakes).
- **`documents` content-hash** — ingest derives `sha256(content)` at read time for skip-if-unchanged
  since the schema has no hash column; consider adding `content_hash` to `documents` for efficiency.
- Tighten the 42 `// @ts-nocheck` files in `web/` (see `web/CONVERSION_NOTES.md`).
- **SECURITY: prototype feed XSS** — `web/src/app.tsx`, `screens/dashboard.tsx`, `screens/security.tsx`
  render activity-feed `f.html` via `dangerouslySetInnerHTML` (inherited from the original prototype).
  Sanitize (DOMPurify) or convert to structured text before any real/user-derived content flows in.
  Not introduced by the spec-renderer (which is injection-safe), but must be fixed before launch.

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
- **Next** — Phase 10 (acquisition/signup/provisioning): signup + email/phone verify + Stripe payment,
  and idempotent rollback-safe per-tenant provisioning gated on the signed payment webhook.
  (Aurora/Redis/S3 IaC + `db/schema.sql` with FORCE'd RLS + the two-tenant isolation proof
  incl. a vector query).
