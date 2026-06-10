<!-- Decision brief — produced by the QA+DECISIONS lane (2026-06-10).
     Research: parallel agents over repo code + current Anthropic docs; claims adversarially
     spot-checked by an independent critic agent. Status: DRAFT until ratified by Nick + Matt. -->

# Decision Briefs — Index

Drafted 2026-06-10 by the QA+DECISIONS lane. Each needs a ratify/decline from Nick + Matt; the recommendation is the lane's opinion, not a decision.

| Brief | Recommendation (one line) | Critic |
|---|---|---|
| [hipaa-fallback-runtime-bedrock-decision](hipaa-fallback-runtime-bedrock-decision.md) | Run HIPAA tenants on SelfHostedToolUseRuntime with an AnthropicBedrock client injected via the existing client | ✓ checked |
| [workspace-ceiling-vs-per-tenant](workspace-ceiling-vs-per-tenant.md) | Keep one-workspace-per-tenant with a Console pre-minted workspace pool (fixes the real blocker: Admin API cann | ✓ checked |
| [custom-tool-execution-path](custom-tool-execution-path.md) | Kill the EnvironmentWorker path for Uplift's registry tools — execute them client-side in the API orchestrator | ⚠️ corrected |
| [self-hosted-sandbox-resource-limits](self-hosted-sandbox-resource-limits.md) | No flow needs to move — Uplift never used Anthropic-side mounting or memory stores; close the TODO and spend a | ✓ checked |
| [workers-polling-heartbeat-assumption](workers-polling-heartbeat-assumption.md) | Replace the tools-callable piggyback with an explicit asyncio heartbeat task in run() that emits workers_polli | ✓ checked |
| [ma-env-key-generation-rotation-runbook](ma-env-key-generation-rotation-runbook.md) | Adopt a 90-day manual rotation runbook (Console "Generate environment key" → CLI put into uplift/env-key → ecs | ⚠️ corrected |
| [agent-plane-ensure-eager-vs-lazy](agent-plane-ensure-eager-vs-lazy.md) | Go eager: implement agent_plane.ensure() inside the existing idempotent signup provisioning step (Option A) —  | ✓ checked |
| [uplift-launch-pricing](uplift-launch-pricing.md) | Launch at $99 Starter / $299 Team / $799 Scale monthly (annual = 2 months free: $990/$2,990/$7,990), gated by  | ✓ checked |
| [demo-tenant-synthetic-dataset](demo-tenant-synthetic-dataset.md) | Build Option B: a seeded stdlib-only generator with ~8 hand-authored hero arcs emitting a committed demo_tenan | ✓ checked |
