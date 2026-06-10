<!-- Decision brief — produced by the QA+DECISIONS lane (2026-06-10).
     Research: parallel agents over repo code + current Anthropic docs; claims adversarially
     spot-checked by an independent critic agent. Status: DRAFT until ratified by Nick + Matt. -->

# The 100-Workspace Ceiling vs One-Workspace-Per-Tenant

## Context

**What the code does today.** The tenancy model is decided and written down: one Anthropic workspace per tenant, with vaults (workspace-scoped) as the isolation boundary, and the AWS side as a lean pool (one Aurora cluster + RLS, one Cognito pool) — `CLAUDE.md:110-114`. THE TRUST RULE (`CLAUDE.md:101-103`) makes the verified Cognito JWT `custom:tenant_id` claim the only source of tenant identity, flowing into `app.current_tenant` (RLS), Cube's security context, and MA session metadata (`agents/runtime.py:212-216`, the `metadata = {"tenant_id": tenant_id}` line in `create_session`).

The provisioning pipeline implements this per-tenant-workspace model end to end:

- `signup/provisioning.py:255-263` (`_step_workspace`): `ensure_workspace(tenant_id)` → `create_workspace_key(ws_id, tenant_id)` → key into Secrets Manager at `uplift/{tenant_id}/anthropic_key` (never returned again) → `set_limits(ws_id, tenant_id)` for per-workspace spend + rate caps.
- `signup/anthropic_admin.py:85-97`: `ensure_workspace` is idempotent by deterministic name (`uplift-tenant-{tenant_id}`, line 81-83) — check-then-create so a re-delivered Stripe webhook never mints a second workspace.
- `signup/anthropic_admin.py:164-171`: rollback teardown via `POST /v1/organizations/workspaces/{id}/archive` (confirmed: archive is the only deletion; it revokes the workspace's keys, irreversibly).
- `shared/COST.md:19-25`: per-workspace spend caps are the stated blast-radius control ("one tenant can't run away with the bill") and per-workspace Anthropic usage is the stated basis for per-tenant unit economics.

**Three load-bearing facts, verified against live Anthropic docs (fetched 2026-06-10):**

1. **The 100-workspace cap is real and current.** "Maximum 100 workspaces per organization (archived workspaces don't count)" — stated twice in the Workspaces doc (key characteristics + FAQ). No documented raise path exists; the docs and a web sweep show no "contact us to increase" language for this limit (unlike rate limits, which have a documented account-rep path). The Default Workspace has no ID and doesn't appear in lists; the auto-created Claude Code workspace and any internal dev/staging workspaces eat slots, so the practical tenant ceiling is **~95**.

2. **The cap is not your first wall — Console-only key creation is.** The Admin API FAQ says verbatim: "No, new API keys can only be created through the Claude Console for security reasons. The Admin API can only manage existing API keys." `create_workspace_key` (`signup/anthropic_admin.py:119-138`) targets an assumed `POST /v1/organizations/api_keys` create endpoint that, per current docs, **does not exist**. The repo already flags this (`⚠️ NOT CONFIRMED`, BLOCKED: Lane Nick) — the docs check converts it from "unverified" to "documented-impossible today." This breaks pay-then-provision automation at tenant #1, not tenant #100.

3. **Per-workspace limit writes are also Console-only.** Workspace spend/rate limits are set in the Console Limits tab; the Rate Limits API is documented read-only. `set_limits` (`signup/anthropic_admin.py:140-162`) correctly soft-fails for exactly this reason. Meanwhile, the Usage and Cost API natively supports `workspace_ids[]` filtering and `group_by[]=workspace_id` — workspace is the **first-class billing-attribution dimension** Anthropic gives you (Default Workspace usage shows `workspace_id: null`).

One mitigating doc fact: the Admin API **does** support workspace `update` (rename) alongside create/get/list/archive. That makes a pre-minted pool viable (below).

## Options

### Option A — Stay under 100: keep one-workspace-per-tenant, add a pre-minted workspace pool

Keep the decided model. Fix the real blocker (Console-only keys/limits) operationally: Nick batch-creates N spare workspaces in Console (`uplift-pool-001…`), mints one key each, sets the default $200/mo spend cap (matching `DEFAULT_TENANT_LIMITS`, `anthropic_admin.py:47`) in the Limits tab, and loads the keys into Secrets Manager. `_step_workspace` becomes "claim a pool workspace + rename it to `uplift-tenant-{id}` via the confirmed update endpoint + move the key secret to `uplift/{tenant_id}/anthropic_key`." Provisioning stays fully automated; the human step becomes a weekly 10-minute top-up instead of a per-signup click.

- **Target-market cap math:** at the venture's stated mid-market pricing (~$2-8K/mo per done-for-you tenant), ~95 slots = **$190-760K MRR ($2.3-9M ARR)**. For a two-person firm landing tenants via referral (Kyle, Dylan), the ceiling is 2+ years away and arguably past the point where you'd have an Anthropic account rep anyway. The cap only binds early if you go down-market self-serve (GHL price points, $97-497/mo) — then 95 tenants is only ~$110-570K ARR and the wall is real.
- **TRUST RULE:** strengthened. Workspace scoping is an *independent second wall* under JWT+RLS — a tenant-confusion bug in app code still can't cross workspaces because each request path holds only that tenant's key. (Caveat to enforce: the API task currently carries the org key, `shared/config.py:19`; the conversation path must read the per-tenant key for this wall to be real.)
- **Billing attribution:** native — Usage/Cost API grouped by `workspace_id`, spend caps per workspace, exactly what `shared/COST.md` assumes.
- **Cost/effort/risk:** ~zero infra cost; small code change (claim+rename) + a recurring ops chore; risk is the hard wall at ~95 and a stale pool if growth spikes.

### Option B — Multiple organizations

Spill tenants 96+ into a second Anthropic org. Orgs cannot be created via API; each org means separate billing, a separate `sk-ant-admin` key, separate rate-limit tiers, a separate Console, and a separate Usage/Cost API scope. The tenant→workspace registry (`agents/workspace_store.py`) would need an org dimension, and per-tenant unit economics would require aggregating N orgs' reports.

- **TRUST RULE:** unchanged per tenant; adds org-level blast-radius partitioning (a leaked admin key only reaches one org's slice).
- **Billing attribution:** fragmented — every dashboard, alarm, and cost rollup needs a cross-org aggregator.
- **Cost/effort/risk:** low dollars, **high permanent ops tax** for a two-person team; unsupported-by-design pattern (manual org creation, no API); doubles every Console-manual chore from facts #2/#3. This is an escape hatch, not an architecture.

### Option C — Coarser isolation: shared workspace + per-tenant vaults + session metadata

Collapse all standard tenants into one (or a few) pooled workspaces. Vaults are workspace-scoped but plural — one vault per tenant attached per session via `vault_ids` (`agents/runtime.py:220-222`), tenant identity rides session `metadata` per THE TRUST RULE, and RLS does the data-plane work it already does.

- **TRUST RULE:** *weakened from two walls to one.* The JWT→metadata→RLS chain still holds (metadata is set server-side from the verified claim), but the workspace wall disappears: one shared key reaches **every** tenant's sessions, files, vault attachments, and batches. A leaked key or a buggy server-side component goes from one-tenant blast radius to all-tenant. This directly undercuts the "enterprise-grade isolation" wedge against GoHighLevel.
- **Billing attribution:** demoted from native to rebuilt — all usage lands under one `workspace_id`, so per-tenant metering must be reconstructed from `span.model_request_end.model_usage` events keyed by session→tenant, and per-tenant spend caps move from Anthropic-enforced to app-enforced (Greenlight/kill-switch logic). One runaway tenant also burns the shared rate limit — noisy-neighbor risk the per-workspace model was chosen to prevent (`shared/COST.md:19-21`).
- **Cost/effort/risk:** removes the cap entirely and shrinks the Console chore to ~one workspace; medium-high effort (metering pipeline + app-level budget enforcement); the risk is sold-trust, not tech.

### Option D — Hybrid tiering: dedicated workspace = enterprise tier only

Standard/self-serve tenants pool (Option C mechanics); enterprise tenants get a dedicated workspace, native caps, native attribution, and a one-tenant key blast radius — sold as a feature ("dedicated isolation boundary"), which monetizes the constraint. The `runtime.py` seam and `workspace_store` already support per-tenant resolution with a fallback shared environment (`shared/config.py:228`), so the fork is small. This is also structurally identical to the already-decided HIPAA carve-out (`CLAUDE.md:114`: different runtime via the seam, not a checkbox).

- **Cost/effort/risk:** all of C's metering work *plus* two provisioning paths to test; premature while tenant count is single-digit, correct at scale or down-market.

## Recommendation

**Keep one-workspace-per-tenant (Option A) with the pre-minted pool, and pre-commit to Option D as the written >80-tenant plan. Do not build B or C now.** The 100-workspace cap is a ~$2-9M-ARR-away problem at your pricing, while the *actual* provisioning blockers — Console-only key creation and Console-only limit writes — bite at tenant #1 and are solved by the pool pattern this week. Per-tenant workspaces are doing real work for you: they are the native billing-attribution dimension, the Anthropic-enforced spend cap from `COST.md`, and the second isolation wall that backs the enterprise-trust pitch against GHL. Trading that away today to dodge a ceiling you're years from hitting would be solving the cheapest problem you have by giving up the asset your positioning rests on. Concrete tripwires: at **60 tenants**, ask your Anthropic rep for a cap raise (you'll have revenue leverage by then); at **80 without a raise**, implement D — new standard-tier tenants pool, existing tenants grandfathered, dedicated workspaces become the enterprise SKU.

## What would flip this

- **Anthropic ships Admin API key creation + programmatic limit writes and raises/removes the 100 cap** → pure Option A forever; delete the pool chore and the D plan.
- **You go down-market self-serve** (sub-$500/mo plans at GHL price points) → the cap binds at ~$0.5M ARR, not $9M; implement D *before* launch of that tier, since pooled isolation + app-level metering becomes table stakes for the unit economics.
- **An Anthropic rep confirms the cap is soft** (raisable on request like rate limits) → A's wall disappears; D becomes purely a packaging decision.
- **The per-tenant-key request path never ships** (the API task keeps using the org key from `shared/config.py:19` for tenant sessions) → you're already living Option C's blast radius with Option A's chores; either finish wiring per-tenant keys or stop paying for workspaces-per-tenant.
- **A single enterprise/HIPAA deal demands org-level separation** → that one tenant gets its own org (B as a one-off exception, never the default architecture); note HIPAA is already designed to exit via the `runtime.py` seam to Bedrock/1P, where most Admin API endpoints don't exist anyway.

## Sources

- `/Users/nick/dev/friesenlabs/CLAUDE.md:101-114` — THE TRUST RULE + decided tenancy model (workspace per tenant, vaults as isolation boundary, AWS lean pool).
- `/Users/nick/dev/friesenlabs/signup/provisioning.py:255-263, 318-326` — `_step_workspace` pipeline + archive-based rollback.
- `/Users/nick/dev/friesenlabs/signup/anthropic_admin.py:14-26, 85-171` — confirmed vs flagged-unverified Admin API endpoints (workspace create/list/archive confirmed; key-create and limits-write flagged).
- `/Users/nick/dev/friesenlabs/agents/runtime.py:178-235` — vault create (workspace-scoped), session metadata carrying `tenant_id`/`vault_id`.
- `/Users/nick/dev/friesenlabs/agents/workspace_store.py` — per-tenant workspace/environment/coordinator persistence.
- `/Users/nick/dev/friesenlabs/shared/COST.md:19-25`, `/Users/nick/dev/friesenlabs/shared/config.py:19, 156, 228` — per-workspace caps as cost control; org-key vs per-tenant-key wiring.
- Anthropic Workspaces doc (live fetch 2026-06-10): https://platform.claude.com/docs/en/manage-claude/workspaces — "Maximum 100 workspaces per organization (archived workspaces don't count)"; Console-only limit setting; Usage/Cost API `group_by[]=workspace_id`; API keys scoped to a single workspace; Claude Code workspace auto-creation.
- Anthropic Admin API doc (live fetch 2026-06-10): https://platform.claude.com/docs/en/api/administration-api — FAQ: "new API keys can only be created through the Claude Console for security reasons"; workspace create/get/list/**update**/archive endpoints; Rate Limits API read-only; org creation has no API.
- Web sweep for a cap-raise path (2026-06-10): no documented increase process for the 100-workspace limit ([support.anthropic.com workspaces article](https://support.anthropic.com/en/articles/9796807-creating-and-managing-workspaces), [anthropic.com/news/workspaces](https://www.anthropic.com/news/workspaces)).
- claude-api skill (bundled, 2.1.168) — Managed Agents resource scoping (vaults/memory stores workspace-scoped; sessions live under `platform.claude.com/workspaces/{workspace}/sessions`).


## Critic-noted gaps (non-blocking)
- Verified live (platform.claude.com/docs/en/manage-claude/workspaces.md): max 100 workspaces/org, archived don't count, no raise process documented; usage_report supports workspace_ids[] + group_by[]=workspace_id verbatim; workspace limits are set in the Console Limits tab and the Rate Limits API is read-only. Repo claims (anthropic_admin.py:119-138 ASSUMED key-create endpoint, set_limits soft-fail 140-162, archive 164-171, CLAUDE.md tenancy, provisioning _step_workspace) all check out.
- The pre-minted pool does not remove the Console bottleneck — each pooled workspace's API key must still be minted by hand in Console, and spend/rate-limit writes remain Console-only, so per-tenant caps stay manual even with the pool. The pool front-loads the manual work; it doesn't automate it.
- Minor: Anthropic auto-creates a 'Claude Code' workspace when any org member signs into Claude Code via Console billing, consuming one of the 100 slots.
