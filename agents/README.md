# agents/ — the agent plane (as code)

"Own your design, rent the runtime." Agent definitions, prompts, tool schemas, and control policies
live here as code; Managed Agents only executes them.

## Pieces
- `runtime.py` — the **swappable adapter**. `AgentRuntime` ABC; `ManagedAgentsRuntime` (real shape,
  lazy client, every live endpoint flagged "verify" and `NotImplementedError` until Nick wires creds);
  `FakeRuntime` (drives tests/dev offline); `get_runtime(config)` factory. Hard limits encoded:
  delegation depth = 1, ≤20 agents/roster, ≤25 concurrent threads/session.
- `roster/` — the 7 specialists as `AgentSpec`s: scout (haiku), nadia (sonnet), margo (sonnet),
  ledger (sonnet), echo (haiku), pip (haiku), critic (opus). Native model tiering.
- `coordinator.py` — opus coordinator; flat topology; `build(runtime)` creates env + agents + coordinator
  (works on FakeRuntime; the per-tenant provisioning sequence against the real runtime is BLOCKED on
  live Anthropic).
- `tools/` — custom tools the agents call (in-VPC).
  - `base.py` — `Tool` + `Policy` (`auto` vs `always_ask`) + `ToolContext` (binds `app.current_tenant`
    for RLS) + `InMemoryGreenlight`. The base class **guarantees** an `always_ask` tool's side effect
    cannot auto-run — it builds a proposal and routes it to Greenlight.
  - `readonly.py` — search_rag, query_cube, read_crm (`auto`).
  - `sideeffecting.py` — draft_email (`auto`), send_email / update_deal / issue_quote (`always_ask`).

## Trust is the feature
Side-effecting tools never execute directly. `send_email`, `update_deal`, `issue_quote` return a
Greenlight proposal (`status: pending_approval`) for a human to approve — proven by
`tests/unit/test_tool_policy.py`. Every tool sets the tenant before any DB/Cube call so RLS applies
during tool execution too.

## Standing constraints
- Managed Agents is **beta** — everything is behind `runtime.py` so a Bedrock/1P fallback (HIPAA
  tenants) swaps in without touching callers.
- The **org API key** creates sessions/agents and must never reach the worker; the worker gets the
  **environment key** only.
- Nothing here creates real Anthropic resources — those steps are BLOCKED: needs Nick.

## Test
```bash
pytest tests/unit/test_runtime_adapter.py tests/unit/test_roster.py tests/unit/test_tool_policy.py \
       tests/integration/test_agent_session.py -q
```
