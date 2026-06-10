<!-- Decision brief — produced by the QA+DECISIONS lane (2026-06-10).
     Research: parallel agents over repo code + current Anthropic docs; claims adversarially
     spot-checked by an independent critic agent. Status: DRAFT until ratified by Nick + Matt. -->

# Self-hosted sandbox resource limits — audit + decision

**TL;DR: The feared exposure does not exist.** Nothing in the repo assumes Anthropic-side file/repo mounting or memory stores. Uplift's agent plane is pointer-native by construction — the only things that cross to Anthropic are prompts, tool schemas, and a 2-key session-metadata dict. The real work is small: codify the metadata contract (it's currently implicit and has one dangling key), and stop depending on an unverified metadata-delivery hook for the worker's tenant binding.

## Context — what the code does today

**The limitation list, verified against MA docs (claude-api skill, 2026-06).** For `config: {"type": "self_hosted"}` environments: (1) `memory_store` session resources are **"Not yet supported"**; (2) `file` / `github_repository` resources are **not mounted Anthropic-side** — the documented pattern is "pass pointers via `sessions.create(metadata={...})` and have your orchestrator fetch/clone before dispatch"; (3) the `networking`/`packages` env sub-fields don't apply (bare `{"type": "self_hosted"}` config); (4) self_hosted is **not available on Claude Platform on AWS**. The TODO's premise (`/Users/nick/dev/friesenlabs/TODO.md:313`) is accurate.

**Repo audit — zero Anthropic-side resource usage:**

- `agents/runtime.py:213-222` — `ManagedAgentsRuntime.create_session` sends exactly four things: `agent`, `environment_id`, `metadata`, and (optionally) `vault_ids`. **No `resources=[...]` anywhere.** Grep across `agents/`, `conv/`, `worker/`, `api/` finds no `client.beta.files`, no `github_repository`, no `mount_path`, no `container_upload`.
- `agents/runtime.py:126-133` — environment created with the bare `config={"type": "self_hosted"}`, matching the doc-verified shape (and already live-verified per `TODO.md:112`: env_012JvqRKUZzUDeH3Gse6TBgZ).
- **Memory stores:** the only two mentions in the codebase are comments — `conv/synthesizer.py:22` (listing the SDK's beta-header namespaces) and `agents/runtime.py:178` (a naming-convention aside). No `client.beta.memory_stores.*` call exists. Conversation continuity is MA's own per-session thread state (one session per `Conversation`, `conv/session.py:145-148`); cross-session durable state is Aurora (workspace store, Greenlight approvals, analytics, saved views) + per-tenant pgvector. Losing MA memory stores on self_hosted costs Uplift literally nothing today.
- **Knowledge/data access is already in-VPC pointer-passing:** `search_rag` hits tenant-scoped pgvector (`agents/tools/readonly.py:12-20`), `read_crm` hits Aurora via `PgCrmClient`, `query_cube` hits the Cube service — all built from env in `worker/worker.py:55-97` and executed under RLS via `build_context` (`worker/worker.py:100-124`). The roster (`agents/roster/__init__.py:31-39`) gives specialists only these custom tools plus the implied built-in toolset. The HIPAA fallback (`agents/runtime_selfhosted.py`) is even cleaner — no Anthropic-side resources at all, synthetic local ids only.
- **Vaults are declared but dead on the live path:** `create_vault` (`agents/runtime.py:177-187`) is only exercised by tests; provisioning's `agent_plane.ensure` builds env+agents+coordinator only (`signup/provisioning.py:265-271`). Docs confirm vaults are MCP-credential-only and injected by Anthropic-side proxies — Uplift uses no MCP servers; tenant integration creds live in Secrets Manager (`api/integrations_routes.py`). No conflict with self_hosted, no change needed.

**The session-metadata contract as it exists (implicit, slightly frayed):**

- Stamped at create: `{"tenant_id": <verified Cognito claim>}` + `"vault_id"` if present (`agents/runtime.py:212-221`). THE TRUST RULE is respected — tenant arrives from the caller, never env/header.
- Read by the worker: `session_metadata["tenant_id"]` (required) and `session_metadata.get("agent")` (`worker/worker.py:116-117`) — **but nothing ever stamps `agent`**. Dangling key: either stamp it or delete the read.
- Doc constraint nobody has written down: session `metadata` is capped at **8 key-value pairs**. We use 2 of 8 — fine, but the budget should be documented before anyone spends it casually.
- **The delivery mechanism is the weak link.** `worker/worker.py:151-161` passes `tools=` and `context_factory=` kwargs to `EnvironmentWorker` — the docs show `EnvironmentWorker(client, environment_id=, environment_key=, workdir=)` with no such kwargs (custom tools go through `AgentToolContext` + `beta_agent_toolset(env)` + the lower-level `tool_runner()`). The repo already VERIFY-flags this. Worse: if metadata is *not* delivered with the dispatched work item, the worker cannot fetch it — `sessions.retrieve` is a control-plane call needing the org key, which by design never reaches the worker (`agents/README.md:32-33`). The worker holds only the environment key.

**Adjacent flag (owned by the separate "custom-tool path" question, `TODO.md:31`):** the docs route `{"type": "custom"}` tool calls to the **SSE stream** (`agent.custom_tool_use` → client answers with `user.custom_tool_result`), not the environment work queue. `conv/session.py:224-226` assumes "the self-hosted worker executes read tools in the VPC" and passes read-tool events through untouched — if custom read tools actually surface stream-side, an unanswered event leaves the session blocked at `requires_action`. Doesn't change this brief's verdict, but it must be resolved in the live agent/session smoke before the worker ships.

## Options

**Option A — Close the TODO as "no exposure"; codify the metadata contract (recommended).**
Author a `SESSION_METADATA` contract (module-level constants + docstring in `agents/runtime.py`, mirrored in `worker/worker.py`): reserved keys `tenant_id` (required, verified-claim only), `vault_id` (optional), `agent` (stamp it at create or delete the worker read), documented 8-key cap, and a reserved-for-future `resource_manifest` key (an S3 URI pointer, never inline data, never tokens — metadata is readable via the API for the session's life). Additionally: persist `session_id → tenant_id` into the Aurora workspace/sessions table at `create_session` time, and make that row the worker's **authoritative** tenant binding, with Anthropic-delivered metadata as a cross-check. This removes the dependency on the unverified metadata-delivery hook entirely — the worker resolves tenant from its own RLS-protected DB using only the session id from the work item.
*Cost:* ~$0. *Effort:* hours (one PR, unit tests, doc update). *Risk:* near zero; strictly tightens an existing seam.

**Option B — Build the pointer-passing resource layer now.**
Implement the full orchestrator-fetch pattern: tenant uploads land in S3, API writes a manifest, stamps `resource_manifest` in session metadata, worker materializes files into `workdir` before serving tools. *Cost:* S3 + plumbing, days of work. *Risk:* meaningful — you're building file-materialization and path-sandboxing security surface for a capability **no current agent flow uses**. Every tool today wants rows and chunks, not raw files. Classic premature build.

**Option C — Do nothing.**
*Effort:* zero. *Risk:* the TODO stays ambiguously open; the `agent` key stays dangling; the worker's tenant binding stays coupled to an undocumented SDK hook that may not exist in the shipped shape; and the next person to touch `create_session` has no written contract stopping them from adding `resources=[...]` expecting cloud-style mounting.

## Recommendation

**Option A.** The audit answer to the TODO is a clean "no" — nothing must move to pointer-passing because nothing ever left the VPC. Spend the small effort where the actual risk is: write the metadata contract down, fix the `agent` key, and anchor the worker's tenant binding in Aurora (keyed by session id) instead of in an SDK hook that the docs don't show. That last move also future-proofs the pointer pattern: when a `resource_manifest` is eventually needed, the worker already has a trusted per-session lookup path that doesn't depend on what Anthropic chooses to deliver in the work payload. Lane Matt can author all of it offline; the only live dependency is the already-planned agent/session smoke (which should also settle the EnvironmentWorker kwarg shape and the custom-tool routing question).

## What would flip this

- **A real document-grounded flow appears** — e.g. a tenant uploads a contract and an agent must read/edit the raw file (not RAG chunks). That's the trigger to build Option B's manifest + materializer.
- **Anthropic ships memory-store support for self_hosted** — re-evaluate agent long-term memory as MA-native vs the build-it-on-Aurora-as-a-custom-tool default (the Aurora path still wins on data-residency for the multi-tenant story, so this flip is weak).
- **The live smoke shows work items carry full session metadata reliably and EnvironmentWorker exposes a supported per-invocation context hook** — then the Aurora-binding belt-and-suspenders can be demoted from authoritative to cross-check, simplifying the worker.
- **The custom-tool routing question resolves as "all custom tools are stream-side"** — then the worker's TOOLS registration (`worker/worker.py:33`) shrinks toward the built-in toolset and the metadata contract matters more on the API/stream side than the worker side; the contract itself is unchanged.

## Sources

- **Repo (read directly):** `agents/runtime.py` (create_environment :116-135, create_session :189-229, send_message :231-306), `agents/runtime_selfhosted.py`, `agents/roster/__init__.py`, `agents/coordinator.py`, `agents/tools/readonly.py`, `worker/worker.py`, `conv/session.py`, `conv/synthesizer.py`, `signup/provisioning.py:265-271`, `api/integrations_routes.py`, `TODO.md:31,310,313`, `agents/README.md`.
- **Anthropic facts:** claude-api skill (invoked this session, 2026-06) — `shared/managed-agents-self-hosted-sandboxes.md` ("What changes vs cloud" table: memory_store "Not yet supported"; file/github mounting "You — pass pointers via sessions.create(metadata={...})"; bare self_hosted config; not on Claude Platform on AWS; EnvironmentWorker signature; control-plane calls use x-api-key, not the env key), `shared/managed-agents-core.md` (session `metadata` max 8 keys; vault_ids session-create-only), `shared/managed-agents-tools.md` (custom tools are client-handled via `agent.custom_tool_use`; vaults are MCP-credential-only, proxy-injected), `shared/managed-agents-memory.md` (memory stores attach via `resources[]` at session create).


## Critic-noted gaps (non-blocking)
- Claim 6 understates the installed SDK's real signature: tools= IS a documented EnvironmentWorker kwarg (._worker.py:259, docstring 225-229; plus unrestricted_paths/max_file_bytes/max_idle/worker_id) — only context_factory= is nonexistent. Immaterial to the recommendation, and actually helpful: the tools callable receives an AgentToolContext carrying session_id, which directly enables the recommended Aurora session_id→tenant_id binding.
- The assertion that the worker cannot call sessions.retrieve with the environment key is plausible (docs scope the env key to events stream/list/send + work queue) but was not verified — test it once live before hard-coding the Aurora-row design as the only option.
- All other claims verified exactly: no resources=/client.beta.files/memory_stores calls anywhere (memory_stores appears only in docstrings at conv/synthesizer.py and agents/runtime.py:178-181); create_session sends only agent/environment_id/metadata/vault_ids (runtime.py:212-222); metadata = {tenant_id required, vault_id optional}; worker.py reads an 'agent' metadata key nothing stamps; 8-key session-metadata cap confirmed in the MA Session Object docs.
