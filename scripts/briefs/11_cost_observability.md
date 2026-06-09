# Brief: Phase 11 — Cost, Guardrails & Observability

## Goal
Inference is the variable that scales; control it with tiering + caching, cap it hard, and watch
everything. Author the guardrails (AWS Budgets + a 90% Deny action, per-workspace caps, cost tags) and
observability (CloudWatch alarms, OTEL/ADOT) as IaC (validate only), plus a small, tested cost-model
utility. Mostly IaC + config — no apply.

## Owner / directory
Orchestrator. IaC in `infra/modules/guardrails` + `infra/modules/observability`; a tested cost helper
in `shared/cost.py`. Do not edit web/ or the logic packages.

## Files
- `shared/cost.py` — a tested unit-economics helper: model tiering 70/25/5 (Haiku/Sonnet/Opus) cost
  mix, prompt-caching discount (~-90% on cached reads), Batch -50% (NOT inside MA sessions), and
  active-session-hour cost ($0.08/hr, parallel specialist threads stack). `estimate(tokens_by_tier,
  session_hours, cached_fraction)` → a cost breakdown. This powers pricing the $2-8K/mo plans.
- `infra/modules/guardrails/main.tf` — `aws_budgets_budget` (monthly cost) + a budget ACTION that
  attaches a Deny IAM policy at 90% (the "stop new spend" lever) + a CloudWatch billing alarm
  (us-east-1). Per-resource cost-allocation tags (tenant/app/env) — note the Anthropic per-workspace
  spend+rate caps are set at provisioning (signup/provisioning set_limits), reference that.
- `infra/modules/observability/main.tf` — CloudWatch alarms: ALB 5xx + target latency, ECS CPU/mem
  (drives autoscaling), Aurora ACU/connections, Redis evictions, and the worker queue depth +
  workers_polling (alarm on 0 = no worker). An SNS topic for alarm notifications. (ADOT/OTEL sidecar +
  Container Insights are noted; Container Insights is already on the cluster.)
- wire both modules into `infra/main.tf` + outputs; `terraform fmt` + `validate` clean.
- `shared/COST.md` (or extend a README) — the inference levers (tiering, prompt caching, batch,
  task-scoped sessions) as the cost playbook.

## Tests
- `tests/unit/test_cost.py` — tiering mix sums right; caching reduces read cost ~90%; Batch discount
  applies to offline only (not sessions); session-hours cost scales with parallel threads;
  a worked example lands in a sane $ range.
- terraform validate clean.

## Constraints
- No apply (budgets/alarms/Deny-action are authored only). Secrets/ARNs via variables, not literals.
- The Deny-at-90% action + per-workspace caps are the hard stops; document that there is no true hard
  cap on AWS (two layers: budget action + billing alarm).

## Done when
`shared/cost.py` is tested; `infra/modules/{guardrails,observability}` validate clean and are wired;
BUILD_STATUS Phase 11 updated; live budget/alarm creation marked BLOCKED: needs Nick.
