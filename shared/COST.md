# Cost playbook (Phase 11)

Storage, embeddings, and DB I/O are nearly free; **inference is the variable that scales.** Control it
with tiering + caching, cap it hard, and watch everything. `shared/cost.py` models the bill (tested).

## The inference levers (Step 57)
1. **Tier models 70/25/5** (Haiku/Sonnet/Opus) — already encoded in the roster (`agents/roster`).
2. **Prompt caching** — the stable system/skill context caches at ~-90% on cached reads; the biggest
   saver in an agentic loop.
3. **Batch for offline work** — embeddings, bulk scoring at -50%. NOTE: the Batch discount does NOT
   apply inside Managed Agents sessions — run bulk work through the standard API/Batch, not a session.
4. **Keep sessions task-scoped** — the $0.08/active-session-hour meters only while running; don't leave
   sessions idle-open, and remember parallel specialist threads stack active-hours.

## The hard caps (Step 58) — there is no true hard cap on AWS, so use two layers
- **AWS Budget action**: at 90% actual, attach `AWSDenyAll` to the target roles (the "stop new spend"
  lever) — `infra/modules/guardrails`.
- **CloudWatch billing alarm** in us-east-1 (notify; lags ~6h).
- **Anthropic per-workspace caps**: each tenant's workspace gets its own spend + rate limit, set at
  provisioning (`signup/provisioning` `set_limits`), so one tenant can't run away with the bill.

## Per-tenant economics (Step 59)
Tag every resource (tenant/app/env; ECS via managed tags + propagation), activate the tenant
cost-allocation key, and slice spend in Cost Explorer. Combine with per-tenant token logging +
per-workspace Anthropic usage for real unit economics — the basis for pricing the plans and the
$2-8K/mo retainers. `shared/cost.py` `estimate(...)` turns a workload into a $ breakdown.

## Observability (Step 60)
`infra/modules/observability`: alarms for ALB 5xx + p95 latency, Aurora ACU, Redis evictions, and the
worker (`workers_polling < 1` = no worker) → an SNS topic. Container Insights is on the cluster; ADOT
(OTEL) → X-Ray instruments FastAPI; the Managed Agents event stream (persisted to `traces`) is
agent-level observability; PostHog (Phase 10) is the business funnel.

## Live setup — BLOCKED: needs Nick
Budgets/alarms/Deny-action creation, the budgets-action execution role, the SNS email subscription,
and the ADOT sidecar all need apply + an email + ARNs.
