<!-- Decision brief — produced by the QA+DECISIONS lane (2026-06-10).
     Research: parallel agents over repo code + current Anthropic docs; claims adversarially
     spot-checked by an independent critic agent. Status: DRAFT until ratified by Nick + Matt. -->

# HIPAA Fallback Runtime: What the "Bedrock/1P fallback" Actually Runs

## Context (what the code does today)

**The tenancy promise.** `CLAUDE.md:114` says: *"HIPAA tenants are a different runtime (Bedrock/1P fallback via the `runtime.py` seam), not a checkbox."* `TODO.md:310` flags the open question this brief settles: Managed Agents (MA) is not available on Bedrock, so the HIPAA path must be either a Claude-API tool-use loop or MA on Claude Platform on AWS.

**The seam.** `agents/runtime.py:372-402` (`get_runtime`) knows three kinds: `fake`, `managed` (`ManagedAgentsRuntime`, the standard tenancy path with the `managed-agents-2026-04-01` beta header), and `self_hosted` — which lazily builds `SelfHostedToolUseRuntime` from `agents/runtime_selfhosted.py` (merged in PR #68, "self-hosted HIPAA runtime seam", 24 new tests, full suite 530 passed). A `'bedrock'` kind deliberately raises `ValueError` (`tests/unit/test_runtime_selfhosted.py:74`) — Bedrock is not a fourth runtime; it is a client choice *inside* the self-hosted one.

**What SelfHostedToolUseRuntime is.** A bounded, client-side Messages-API tool-use loop (`runtime_selfhosted.py:345-394`) over the same trusted tool registry, with the same Greenlight ALWAYS_ASK routing (proposal, never execution — asserted at `test_runtime_selfhosted.py:211-247`), the same trust rule (tenant_id only via `create_session`), synthetic `selfhosted-…` ids persisted to the same `tenant_workspaces` row, no subagent threads, no Anthropic-side state, no MA beta header (`test_runtime_selfhosted.py:193-207` asserts plain `{name, description, input_schema}` tool shapes and no `extra_headers`). Tools execute in-process in our VPC — there is no environment worker and no environment key on this path.

**The load-bearing VERIFY.** `runtime_selfhosted.py:144-162` (`_c()`): the default client is first-party `anthropic.Anthropic` (api.anthropic.com), and the comment says a tenant whose compliance posture requires AWS-side inference runs the SAME loop over `anthropic.AnthropicBedrock` via the `client_factory` injection seam, with prefixed model ids. The module was shipped with the endpoint choice deliberately unresolved. This brief resolves it.

**What is NOT wired yet.** `api/asgi.py:182-192` builds only the managed runtime in prod (`_managed_runtime_factory`). There is no per-tenant runtime selection — no `tenants.runtime_kind` column, no asgi branch that routes a HIPAA tenant to `self_hosted`. The seam exists and is tested; the routing is future work either way.

## The facts that decide this (verified 2026-06-10)

1. **MA does not exist on Bedrock — officially.** The Bedrock docs page (platform.claude.com, fetched) lists "Claude Managed Agents" under *Features not supported*, alongside server-side tools, Files API, and Batches. Supported on Bedrock: Messages API, prompt caching, extended thinking, **client-side tool use** (incl. bash/text-editor/memory tool shapes), structured outputs, citations. The self-hosted loop uses exactly and only the supported subset.
2. **Claude Platform on AWS does not support self_hosted sandboxes.** Per the claude-api skill (`shared/claude-platform-on-aws.md` and the self-hosted-sandboxes comparison table): MA works there, but `config:{type:"self_hosted"}` does not — `cloud` only. Uplift's entire tool plane is the self-hosted VPC worker, so MA-on-CPoAWS would force tool execution out of our VPC.
3. **Anthropic's 1P-API BAA covers a specific feature list — and MA is not on it.** Per Anthropic's Privacy Center BAA article (fetched): HIPAA-ready API orgs get the **Messages API** plus prompt caching, structured outputs, memory, web search, bash tool, text-editor tool, token counting, models/org-management/compliance APIs. Explicitly excluded: Batch API, Files API, Skills API, code execution, computer use, web fetch, and **beta features**. MA is beta and absent from the covered list. Getting turned on requires the org admin to sign the BAA and contact sales; ZDR is limited to "qualified accounts only." Separately, Anthropic's HIPAA-ready program does not extend to Claude Platform on AWS (Anthropic operates inference there; the BAA surfaces are the 1P API and sales-assisted Enterprise).
4. **Bedrock is HIPAA-eligible under the standard AWS BAA.** The AWS BAA is self-service (AWS Artifact, click-through, free), Bedrock is in scope, and Claude-on-Bedrock keeps prompts/outputs inside the AWS service boundary — Anthropic never sees the data. One BAA, no Anthropic contracting cycle, no subprocessor disclosure for Anthropic in the tenant's compliance story.
5. **The SDK seam is real.** `pip install "anthropic[bedrock]"` → `from anthropic import AnthropicBedrock` (SigV4 / standard AWS credential chain, no `api_key`) — matching the repo's VERIFY note. One correction to the comment: current Bedrock model ids are not bare `anthropic.claude-opus-4-8`; they carry routing prefixes and sometimes version suffixes — e.g. `global.anthropic.claude-opus-4-6-v1` (global, no premium) or `us.anthropic.claude-…` (US-regional CRIS, +10%, guarantees US data routing — likely the right default for a PHI tenant). The newest Opus models route through the newer Messages-API Bedrock endpoint; verify exact ids at integration time.

## Options

### (a) SelfHostedToolUseRuntime over the 1P Claude API, under Anthropic's BAA
The loop's default client today. **Viable** — the Messages API with client-side tools is squarely inside Anthropic's BAA coverage. But: requires sales-assisted HIPAA-ready enrollment + signed BAA + config requirements; ZDR is gated to "qualified accounts" (a two-person startup with one HIPAA tenant may not qualify); the feature exclusions must be policed forever (no Batch, no Files, no code execution on that org); HIPAA-ready constraints apply at the **org** level, so Uplift would want a second Anthropic org just for HIPAA tenants to keep the MA org unconstrained — more org/key/billing plumbing. And the tenant's compliance story now includes Anthropic as a subcontractor BA. **Cost:** standard 1P token pricing. **Effort:** zero code, weeks of contracting. **Risk:** contracting friction + permanent feature-exclusion discipline.

### (b) The same loop over Amazon Bedrock via `client_factory`, under the AWS BAA — RECOMMENDED
Inject `AnthropicBedrock` through the existing `client_factory`/`model` seam (`runtime_selfhosted.py:120,144-162`); map roster model ids to Bedrock ids (`us.anthropic.…` for PHI data-residency). Everything else — registry tools, Greenlight, trust rule, digest shape — is byte-identical and already tested with a mocked client. **Compliance:** single self-service AWS BAA; PHI never leaves AWS; the strongest possible answer to a healthcare buyer's "who touches our data?" (answer: only AWS, in our own account, behind the same Aurora/RLS/KMS/CloudTrail controls we already run). **Effort:** small and inside Nick's existing competence — Bedrock model access in acct 186052668426, `bedrock-runtime:InvokeModel` on the API task role, and un-park the `bedrock-runtime` VPC interface endpoint (`TODO.md:174`); the account already uses Bedrock for Titan embeddings (`ingest/embed.py`, TODO.md:139). **Cost:** Bedrock Claude token pricing ≈ 1P (+10% only if regional CRIS endpoints are chosen). **Risk:** partner-operated release lag (newest models can trail 1P; lifecycle dates set by AWS) and a second model-id namespace to maintain — both acceptable for a compliance-only path.

### (c) Managed Agents on Claude Platform on AWS
**Dead, three times over:** (1) CPoAWS has no `self_hosted` environments, so Uplift's VPC tool worker can't run there — tool execution would move into Anthropic-hosted containers, the opposite of what a HIPAA tenant is buying; (2) Anthropic's HIPAA-ready program doesn't extend to CPoAWS (Anthropic is the inference data processor; AWS only handles billing/identity); (3) MA is beta and excluded from Anthropic's BAA coverage anyway — so MA cannot serve PHI **even on the 1P API** today. Do not build toward this.

## Recommendation

**Default the HIPAA path to (b): SelfHostedToolUseRuntime + `AnthropicBedrock` injected via `client_factory`, under the AWS BAA, with US-regional (CRIS) model ids.** Keep (a) as a per-tenant, contract-driven alternative — the seam already supports per-tenant client injection — used only when a tenant explicitly demands Anthropic-1P and the Anthropic BAA + HIPAA-ready enrollment is actually secured first (the module docstring already mandates confirming the endpoint per tenant before onboarding).

Concrete follow-ups (no code written by this brief; repo is Lane-owned):
1. Update `CLAUDE.md:114` and `agents/README.md:30` from "Bedrock/1P fallback" to "self-hosted tool-use loop, **Bedrock by default** (AWS BAA), 1P only with a signed Anthropic BAA".
2. Tighten the `runtime_selfhosted.py:149-158` VERIFY comment: client name `AnthropicBedrock` is confirmed; model-id example should be `us.anthropic.…`/`global.anthropic.…` shaped, not bare `anthropic.claude-opus-4-8`.
3. When wiring asgi routing, add a `runtime_kind` to the tenant record and a Bedrock branch in the runtime factory next to `_managed_runtime_factory` (`api/asgi.py:182`); never default a tenant into `managed` silently (the `ValueError` on unknown kinds already protects this).
4. Before the first HIPAA tenant: execute the AWS BAA in AWS Artifact, request Bedrock Anthropic model access, and run the existing mocked-loop tests plus one live Bedrock smoke through `client_factory`.

## What would flip this

- **MA exits beta and lands on Anthropic's BAA-covered list** (or an Anthropic HIPAA-ready tier covering MA + self-hosted environments ships) → revisit running HIPAA tenants on the managed plane; the runtime seam makes that a config change.
- **Claude Platform on AWS gains `self_hosted` environment support and BAA coverage** → (c) becomes the minimal-divergence path (same MA code, AWS billing); watch the CPoAWS docs page.
- **A flagship HIPAA tenant already holds (or insists on) an Anthropic BAA** and won't accept Bedrock → use (a) for that tenant via per-tenant `client_factory`; isolate in a dedicated Anthropic org.
- **Bedrock model lag bites** — if the roster model a HIPAA tenant needs isn't on Bedrock in-region for months, the 1P BAA path's contracting cost may beat the capability gap.

## Sources

- Repo (read directly): `/Users/nick/dev/friesenlabs/CLAUDE.md:110-114`, `agents/runtime.py:372-402`, `agents/runtime_selfhosted.py` (esp. 1-46, 105-162, 345-394), `tests/unit/test_runtime_selfhosted.py` (esp. 56-76, 193-247, 267-296), `tests/integration/test_conversation_selfhosted.py`, `api/asgi.py:150-219`, `TODO.md:139,144,174,310-313`, `agents/README.md:30`; PR #68 via `gh pr view 68` (MERGED).
- claude-api skill (loaded this session): MA availability matrix ("Managed Agents is available on the first-party API and Claude Platform on AWS… **not** available on Amazon Bedrock"), `shared/claude-platform-on-aws.md` ("except self-hosted sandboxes — `config:{type:"self_hosted"}` is not available here"), `shared/managed-agents-self-hosted-sandboxes.md` comparison table ("Claude Platform on AWS | Not available").
- platform.claude.com — Claude on Amazon Bedrock docs (WebFetch 2026-06-10): `AnthropicBedrock` client, `anthropic[bedrock]`, SigV4, `global.`/`us.` model-id prefixes, "Features not supported" incl. Claude Managed Agents; supported incl. Messages API + tool use + structured outputs.
- privacy.claude.com — "Business Associate Agreements (BAA) for Commercial Customers" (WebFetch 2026-06-10): 1P-API BAA covered-feature list; Batch/Files/code-exec/web-fetch/beta exclusions; sales-assisted enablement; ZDR qualified-accounts-only.
- Web search (2026-06-10): support.claude.com HIPAA-ready Enterprise article; AWS re:Post + aws.amazon.com on Bedrock HIPAA eligibility under the AWS BAA; strac.io / aptible.com 2026 Claude-HIPAA guides (Bedrock keeps data inside AWS, no Anthropic access; Anthropic HIPAA-ready program not available via Claude Platform on AWS).


## Critic-noted gaps (non-blocking)
- Claim 5's Bedrock specifics need verification before coding: the claude-api skill's current migration guide shows the Bedrock client as AnthropicBedrockMantle with bare 'anthropic.'-prefixed ids (anthropic.claude-opus-4-8); the brief's 'AnthropicBedrock' + 'global.anthropic.claude-opus-4-6-v1' / 'us.anthropic.…' shapes look like the legacy integration. A wrong id 400s at runtime but does not change the Bedrock-vs-MA decision. The regional +10% premium is confirmed on the live pricing page.
- Claim 3 (the exact 1P-API BAA covered-feature list) could not be independently verified from the skill or fetched docs — confirm with Anthropic before contracting the 1P-BAA per-tenant alternative.
- All repo claims verified exactly: runtime_selfhosted.py uses plain {name,description,input_schema} tools (not type:custom), no MA beta header, Greenlight ALWAYS_ASK preserved, client_factory seam at _c() lines 144-162, and api/asgi.py wires only _managed_runtime_factory (asgi.py:182-188, 228) with no per-tenant runtime routing.
