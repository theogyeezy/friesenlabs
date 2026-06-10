<!-- Decision brief — produced by the QA+DECISIONS lane (2026-06-10).
     Research: parallel agents over repo code + current Anthropic docs; claims adversarially
     spot-checked by an independent critic agent. Status: DRAFT until ratified by Nick + Matt. -->

# agent_plane.ensure() — eager (at signup) vs lazy (on first chat)

**Decision needed (TODO.md:192, the single Lane-Matt park, BUILD_STATUS.md:501-502):** when does a tenant's Managed Agents roster (environment + 7 specialists + coordinator) get created? The seam, stub-id guard, and persistence already exist; this decision unblocks ~1h of glue.

## Context (what the code does today)

- **The hook fires at signup, step 3 of 6.** `Provisioner._step_agent_plane` (`signup/provisioning.py:265-278`) calls `self.agent_plane.ensure(tenant_id=..., workspace_id=...)` and persists the returned ids via `workspace_store.upsert(...)` into `tenant_workspaces`. Provisioning runs **only on the verified Stripe webhook** and is idempotent + rollback-safe by contract (`provisioning.py:1-22`): mid-failure parks the account in `provisioning_failed` with an at-most-once refund seam (`provisioning.py:167-198`), rollback deletes the partial workspace (`provisioning.py:318-326`), and there's an operator/tenant `retry` (`provisioning.py:200-223`).
- **Today `ensure()` is a `_Noop`** returning placeholder ids `{"workspace_id": "stub-ws", "environment_id": "stub-env", "coordinator_id": "stub-coord"}` (`api/prod_deps.py:103-117`), wired permanently at `prod_deps.py:318` until this decision lands.
- **The request path refuses to provision.** `make_conversation_factory` (`api/asgi.py:79-108`) looks up the tenant's persisted ids; no row / missing `coordinator_id` or `environment_id` → `None` → `/chat` 503. A row holding `stub-` ids with a real runtime → explicit 503 (`asgi.py:101-108`). The docstring is unambiguous: *"provisioning happens at signup, never in the request path"* and *"never an on-the-fly roster build in the request path"* (`asgi.py:12-15, 83-85`). Lazy provisioning would mean rewriting this contract, not just filling a seam.
- **What "ensure" actually creates: 9 MA objects.** `coordinator.build()` (`agents/coordinator.py:22-33`) = 1 `create_environment` (self-hosted config, `agents/runtime.py:116-135`) + 7 specialist `create_agent` calls (roster at `agents/roster/__init__.py:31-39`: scout/nadia/margo/ledger/echo/pip/critic — Haiku×3, Sonnet×3, Opus×1) + 1 `create_coordinator` (Opus, `multiagent={"type":"coordinator", ...}`, `runtime.py:149-175`). Note `build()` returns only `coordinator_id`; the real `ensure()` must also capture the environment id and be check-then-create (the done-when criterion: a 2nd call is a no-op).
- **Sessions are already lazy and stay lazy either way.** One MA session per conversation, created at chat time (`conv/session.py:77, 145`). This decision is only about the persisted config objects.
- **Live state:** `ManagedAgentsRuntime` SDK shapes verified real; env `uplift-prod` exists; worker blocked on the Console-generated environment key (CLAUDE.md "AI plane half-live").

## The pricing fact that decides this

Per the live Anthropic pricing page (fetched 2026-06-10): **Managed Agents bills on exactly two dimensions — model tokens (standard rates: Opus 4.8 $5/$25 per MTok, Sonnet 4.6 $3/$15, Haiku 4.5 $1/$5) and session runtime at $0.08/session-hour, metered only while the session status is `running`.** Idle/rescheduling/terminated time is free. There is **no per-agent, per-environment, or at-rest storage charge** anywhere in the pricing doc — agents and environments are free persisted config. MA create endpoints are limited to 300 req/min per org (read 600 RPM), so 9 creates/signup caps throughput at ~33 signups/min — orders of magnitude above Uplift's volume.

So the entire "eager wastes money on tenants who never chat" intuition is wrong: **an eagerly provisioned roster that nobody ever chats with costs $0.00.** Cost starts when a session runs — which is lazy in both options.

## Options

### A. Eager — implement `ensure()` inside provisioning step 3 (recommended)
Real `AgentPlane` class: read the tenant's workspace-scoped key from Secrets Manager (`uplift/{tenant}/anthropic_key`, written in step 2 at `provisioning.py:255-263`), build a fresh `ManagedAgentsRuntime`, check the `tenant_workspaces` row first (no-op if live ids present), else run `coordinator.build()`-equivalent with **incremental persistence** (upsert the env id as soon as it exists; resolve agents by name on retry) so an SFN step retry resumes instead of duplicating.
- **Cost:** ~$0/tenant (9 free objects; a few seconds of API calls). No user-perceived latency — provisioning is already async off the webhook (SFN trigger `api/prod_deps.py:179-236`, exactly-once via deterministic execution names; even the in-process path is webhook-side, not browser-side).
- **Risk:** an Anthropic outage during signup parks the account (refund seam fires) — but SFN Retry absorbs transients first, and `retry` exists for the rest. Partial-create orphans are free; name-matched re-resolution cleans them up. Blast radius = one tenant, pre-first-use, with purpose-built machinery already tested around it.
- **Effort:** the advertised ~1h of glue + ~1h for incremental idempotency + tests. All machinery (guards, rollback, retry, persistence, stub-id refusal) already exists.

### B. Lazy — provision on first chat
`make_conversation_factory` provisions when the row is missing/stubbed, then proceeds.
- **Cost:** saves $0 (see pricing fact). First chat goes from ~seconds to **tens of seconds before the first token** (1 env + 8 agent creates + session create + first turn) — a terrible first-run impression for a product whose demo *is* the chat.
- **Risk:** moves Anthropic-API failure into the user-facing request path with none of the park/retry machinery. Needs new distributed locking: `PgWorkspaceStore.upsert` is last-write-wins `ON CONFLICT DO UPDATE` (`agents/workspace_store.py:112-124`), so two concurrent first messages each build a roster and orphan one. Needs the API task to read per-tenant workspace keys from Secrets Manager in the hot path (IAM surface widening — today the API task holds only the org key, `asgi.py:182-192, 225-227`). And it contradicts the factory contract the codebase states three times.
- **Effort:** realistically 2-4 days (locking, partial-state recovery, IAM, UX for the slow first chat, rewritten 503 semantics, tests) — to save nothing.

### C. Eager with soft-fail (variant of A)
Same as A, but a step-3 failure doesn't park the whole account: mark `ai_plane_pending`, activate anyway (`/chat` stays a graceful 503 — already the behavior for a missing row), backfill via the existing retry route/sweeper.
- **Cost/risk:** as A, minus the "paid customer parked because Anthropic had a bad hour" scenario; adds one in-between state to monitor and a divergence from the uniform park semantics.
- **Effort:** A + ~1h.

## Recommendation

**Option A — eager, now.** The economics are settled by Anthropic's own pricing model: MA config objects are free at rest, so lazy buys zero cost savings while adding latency, concurrency bugs, IAM surface, and an architecture reversal the codebase explicitly forbids. Eager drops into a provisioning pipeline that was *built* for exactly this — idempotent steps, SFN retries, park/refund, operator retry, and a `tenant_workspaces` row the conversation factory already reads. The one genuine eager risk (transient Anthropic failure parking a paid account) is already mitigated by SFN Retry; if MA beta proves flaky in practice, graduate to C with one extra hour — it's a refinement of A, not an alternative architecture. Lazy is a one-way door into request-path provisioning; eager is reversible by swapping the seam back.

Implementation notes for the ~1h of glue: make `ensure()` resolve-by-name/check-then-create per resource (not all-or-nothing), upsert ids incrementally, and keep `MAX_AGENTS_PER_ROSTER`/depth guards as-is (`runtime.py:19-22`). Flag for Lane Nick: the per-tenant **self-hosted environment key is Console-generated** (no API yet — same blocker as `uplift-prod` today), so each eager env still needs a Console click (or a switch to `cloud` envs, or an env-key API if one ships) before that tenant's worker can execute tools. That gap exists under lazy too — it's an ops item, not a tiebreaker.

## What would flip this

1. **Anthropic introduces per-agent/per-environment storage pricing or hard per-org object caps** — then idle-tenant rosters have a carrying cost and lazy (or eager-with-reaping) wins. Re-check the pricing page before GA.
2. **A free-trial funnel with huge signup volume and <1% chat activation** plus any MA object quota — thousands of dead rosters could hit undocumented org limits.
3. **The Console-only environment-key step proves permanent and unautomatable** — then per-tenant envs can't scale under either option, and the architecture question becomes "shared environment with per-session tenant binding," which moots this brief.
4. **MA beta reliability is bad enough that step-3 park rates are material** — don't flip to lazy; flip to Option C.

## Sources

- Repo (read directly): `signup/provisioning.py`, `api/prod_deps.py`, `api/asgi.py`, `agents/coordinator.py`, `agents/roster/__init__.py`, `agents/runtime.py`, `agents/workspace_store.py`, `conv/session.py`, `TODO.md:192`, `BUILD_STATUS.md:501-502`, `CLAUDE.md`.
- Anthropic pricing (live fetch 2026-06-10): https://platform.claude.com/docs/en/about-claude/pricing — "Claude Managed Agents pricing": tokens + $0.08/session-hour while `running`; no Batch/fast-mode/data-residency modifiers on sessions; no agent/environment line item.
- Managed Agents reference (live fetch 2026-06-10): https://platform.claude.com/docs/en/managed-agents/reference.md — create endpoints 300 RPM/org, read 600 RPM/org; beta header `managed-agents-2026-04-01`.
- Managed Agents overview (live fetch 2026-06-10): https://platform.claude.com/docs/en/managed-agents/overview.md — agents are persisted config created once and referenced by ID; beta enabled by default; sessions stateful (no ZDR/HIPAA BAA — consistent with the repo's `SelfHostedToolUseRuntime` HIPAA seam).
- claude-api skill (bundled docs, 2026): managed-agents core/api-reference/self-hosted-sandboxes — agent-create-once anti-pattern, Console-generated `ANTHROPIC_ENVIRONMENT_KEY` for self-hosted environments.


## Critic-noted gaps (non-blocking)
- Claim 5's rate-limit mechanics are slightly off: per the MA rate-limit table, the 300 RPM bucket covers Agents/Sessions/Vaults creates, but Environments have their own limit (60 RPM, max 5 concurrent operations). The binding eager cap is therefore ~37 signups/min (8 agent-creates each) bounded further by env-create concurrency of 5 — same ballpark as the stated ~33/min, and it does not change eager-vs-lazy.
- All other claims verified: pricing page (fetched live) confirms $0.08/session-hour metered only while status is running and lists no per-agent/per-environment/at-rest charge; coordinator.build (coordinator.py:22-33) = 1 environment + 7 roster specialists (roster/__init__.py:31-39) + 1 coordinator = 9 creates; SfnProvisioningTrigger at prod_deps.py:179-236 with idempotent/park/refund/retry in provisioning.py; /chat None→503 and stub-id 503 at asgi.py:90-108; PgWorkspaceStore.upsert last-write-wins at workspace_store.py:112-124.
