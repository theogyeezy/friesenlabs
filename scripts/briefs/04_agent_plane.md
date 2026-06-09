# Brief: Phase 4 — The Agent Plane (runtime adapter, roster, tools, worker)

## Goal
Wire Claude Managed Agents (the reasoning loop) to in-VPC tool execution — but entirely as **code
behind a swappable adapter**, with **nothing created against real Anthropic** (hard gate: STOP before
live Anthropic workspaces/agents/environments). Everything is testable offline with a fake runtime.

## Standing constraints (hard)
- **No live Anthropic / no live AWS.** Author the definitions + adapter + worker; do not create real
  environments, agents, vaults, or sessions. Flag every real MA endpoint "verify" (beta).
- Managed Agents is beta → ALL agent-plane code sits behind `agents/runtime.py`. The real impl is one
  `AgentRuntime` subclass; a `FakeRuntime` drives tests. A factory picks the impl from config so a
  Bedrock/1P fallback (HIPAA tenants) can drop in without touching callers.
- MA beta header on every real call: `anthropic-beta: managed-agents-2026-04-01` (use
  `shared.config.MA_BETA_HEADER`).
- **Two credentials, never confused:** the *environment key* lives on the worker and authenticates it
  to the queue; the *org API key* creates sessions/agents and must NEVER be on the worker host.
- **Draft-only / Greenlight:** read-only tools = `auto` policy; side-effecting tools
  (send_email, update_deal, issue_quote) = `always_ask` → routed through a Greenlight stub. No real
  email/CRM writes execute.
- **RLS during tool exec:** every tool sets `app.current_tenant` from the session metadata before any
  DB/Cube call (tenant isolation must hold inside tool execution too).

## Files
- `agents/runtime.py` — `AgentRuntime` ABC (`create_environment`, `create_agent`, `create_coordinator`,
  `create_vault`, `create_session`, `send_message`); `ManagedAgentsRuntime` (real shape, lazy
  `anthropic` client, every call flagged verify, never invoked in tests); `FakeRuntime` (in-memory,
  records calls, returns canned delegations/tool-calls); `get_runtime(config)` factory.
- `agents/roster/*.py` — one module per specialist returning an `AgentSpec` (name, model tier, system
  prompt, tool names): scout (haiku), nadia (sonnet), margo (sonnet), ledger (sonnet), echo (haiku),
  pip (haiku), critic (opus). `agents/coordinator.py` (opus) lists the roster (flat topology).
  Encode the hard limits as asserts/constants: delegation depth = 1, ≤20 agents/roster,
  ≤25 concurrent threads/session.
- `agents/tools/base.py` — `Tool` (name, description, input_schema, policy: `auto`|`always_ask`,
  `run(ctx, **kwargs)`); `ToolContext` carries tenant_id + injected db/cube/rag/cortex clients and
  sets `app.current_tenant`. `always_ask` tools return a Greenlight *proposal* (never execute the side
  effect) via an injected `greenlight.propose(...)` stub.
- `agents/tools/{search_rag,query_cube,read_crm,build_view,draft_email,send_email,update_deal,issue_quote,run_model}.py`
  — real VPC logic shape with injected clients; sends/mutations gated `always_ask`. Model IDs:
  see `claude-api` skill; use current ids (opus `claude-opus-4-8`, sonnet `claude-sonnet-4-6`,
  haiku `claude-haiku-4-5`).
- `worker/worker.py` — self-hosted `EnvironmentWorker` scaffold (env-key auth, polls queue, registers
  the tool list, sets tenant from session metadata). Author only; do not run against real Anthropic.
- `infra/modules/worker` — ECS Fargate service for the worker (private subnets, SG_API, outbound 443
  to api.anthropic.com; env key from Secrets Manager). validate only.
- READMEs in `agents/` and `worker/`.

## Tests (offline, no real Anthropic/AWS)
- `tests/unit/test_runtime_adapter.py` — `FakeRuntime` satisfies the ABC; factory returns it under a
  `runtime=fake` config; the real `ManagedAgentsRuntime` constructs without touching the network and
  raises/needs creds only when a method is actually called.
- `tests/unit/test_roster.py` — every spec has a valid model tier + tool list; roster ≤20; coordinator
  lists all 7; delegation-depth constant = 1.
- `tests/unit/test_tool_policy.py` — read-only tools are `auto`; send_email/update_deal/issue_quote are
  `always_ask` and return a Greenlight proposal WITHOUT performing the side effect (assert the injected
  sender/CRM fake was never called). Each tool sets `app.current_tenant` on its (fake) db ctx.
- `tests/integration/test_agent_session.py` — drive a fake coordinator session end-to-end with
  `FakeRuntime`: send a message → see delegation to specialists → a read-only tool runs against the
  right tenant's fake data → synthesized answer. (No real services.)

## Done when
The adapter + roster + tools + worker scaffold exist; `get_runtime` is swappable; side-effecting tools
are provably gated (never execute, always propose to Greenlight); tenant is set before every tool DB
call; all new tests pass offline; `terraform validate` clean with the worker module; BUILD_STATUS
Phase 4 row + the live-Anthropic items marked BLOCKED: needs Nick.
