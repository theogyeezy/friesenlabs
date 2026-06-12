# GoHighLevel full-extract connector — build plan / spec (loop-driven)

**Goal:** do for GoHighLevel what `HUBSPOT_FULL_EXTRACT_PLAN.md` did for HubSpot — a **full-fidelity
extract** of EVERY object + field (standard objects, custom objects, custom fields) + relationships
into the SAME source-agnostic **`crm_records`** JSONB table (`source='gohighlevel'`), PLUS **live GHL
agent tools**. The existing experimental `ingest/connectors/gohighlevel.py` (contacts+opportunities
MVP, all endpoints `# VERIFY`) is the OAuth + normalization STARTING point, not the target.

This file is the spec a `/loop` executes. Work the **Checklist** top-to-bottom; each item:
implement → run the listed test/validation → commit → check the box → report. Additive only; no
media/Files blobs (URL refs only); RLS FORCE + crm_app non-owner; never log tokens/PII; DO NOT
deploy or merge. Local test runner: `/Users/macpro24gb/dev/.friesen-loop-venv/bin/python -m pytest`.

## Why this is cheaper than HubSpot was
The `crm_records` table, `PgCrmRecordsSink`, the embedding path, and the agent-tool framework
(`agents/tools/`, `ToolContext`, `make_executor` injection) ALREADY EXIST and are source-agnostic.
The genuinely new work is ONE client file (`gohighlevel_full.py`) + GHL live tools. This branch is
STACKED on `feat/hubspot-full-extract` (#340) so `crm_records` is present; rebase on main once #340
merges (the crm_records migration is shared — no second migration).

## GHL-specific wrinkles (these drive the work — confirm in item 1)
- **V2 only** (`services.leadconnectorhq.com`); V1 dead since 2025-12-31.
- **Location-scoped**: OAuth returns a `location_id`; pulls are per-location (the existing connector
  already carries it through). Agencies = many locations/tokens (out of scope for v1 — one location).
- **Per-resource `Version` header** (Contacts `2021-07-28`, Conversations `2021-04-15`, …) — pin per
  endpoint, not one global value.
- **Pagination** = `startAfter` / `startAfterId` cursors + `limit=100` (NOT HubSpot's `after`).
- **No unified Search** — per-resource list endpoints; incremental via each object's last-updated
  field (`dateUpdated` on contacts, `updatedAt` on opportunities — VERIFY per object).
- **Custom fields**: v2 Custom-Fields API covers Custom Objects + Company; contacts/opps custom
  fields ride the object's own `customFields` array (different shape) — handle both.
- **Rate limits**: 100 req/10s burst, 200k/day per location → ADD reactive 429/Retry-After backoff
  (mirror google/microsoft/pipedrive connectors' `_MAX_RETRIES` pattern; HubSpot's didn't have it).
- **Media** (call recordings / conversation attachments): URL refs only, never the bytes.

## Checklist (the loop works these in order)
- [ ] 1. **Ground the GHL v2 API (research — NO code).** Via `WebFetch` on developers.gohighlevel.com
  + marketplace.gohighlevel.com/docs, enumerate and RECORD IN THIS FILE (a "Grounded API" section):
  the standard object list + each list/search endpoint + its `Version` header; custom-object discovery
  (`GET /objects/` or the schemas endpoint) + how to list a custom object's records; custom-field
  shapes (v2 Custom-Fields API vs the inline `customFields` array); pagination params; the
  per-object last-updated field for incremental; rate-limit + 429 shape. This REPLACES the
  experimental connector's `# VERIFY` guesses — everything below builds on it.
- [ ] 2. **`GoHighLevelFullClient`** (`ingest/connectors/gohighlevel_full.py`): `_get` with the
  per-resource `Version` header + **429/Retry-After backoff**; `discover_object_types()` (standard
  list ∪ custom objects); `discover_fields(object_type)` (ALL fields incl. custom, flag file/media);
  `list_records(object_type, *, since, location_id)` — `startAfter` pagination, incremental on the
  per-object updated field, media-as-refs, normalize to the `crm_records` Record shape (object_type,
  source_ref_id, properties incl. flattened customFields, associations, updated_at);
  `search_live(object_type, *, q, limit)` (bounded, for the live tools). Unit tests w/ mocked
  responses (pagination, version header, 429 retry, media-ref-only, customFields flatten).
- [ ] 3. **`GoHighLevelFullConnector`** + sink wiring: reuse `PgCrmRecordsSink(source='gohighlevel')`;
  `sync(tenant_id, *, since, location_id, object_types)` per-object-type ROBUST (skip a bad type,
  log by exception TYPE only — no PII). Unit tests (lands per type, skips failing type).
- [ ] 4. **Registry + run_sync**: `registry.build_gohighlevel_full_connector(...)` reusing the
  existing GHL vault auth (`GoHighLevelConnector.authenticate` resolves token + location_id); extend
  `run_full_extract` (or add a sibling) to drive GHL. Backwards-compatible. Unit tests.
- [ ] 5. **Live GHL agent tools** (`agents/tools/ghl_live.py`): `ghl_object_types` / `ghl_fields` /
  `ghl_search` — `Policy.AUTO` read-only, backed by `ctx.ghl` (add `ghl` to `ToolContext`; lazy
  per-tenant client). `registry.tenant_ghl_client()` resolver; register in `_TOOL_CLASSES`; inject a
  lazy `ghl_resolver` into `make_executor` (additive); grant to the **Scout** specialist. Unit +
  e2e-through-executor tests.
- [ ] 6. **Full test pass**: `pytest tests/unit` green; imports OK; ruff clean; no-media grep (only
  JSON-response reads, never a blob/recording fetch). `BUILD_STATUS.md` entry (own lane).
- [ ] 7. **PR**: open a PR (branch `feat/gohighlevel-full-extract`, stacked on #340) — additive
  summary + owner-gated note: register the **GHL marketplace app** → seed
  `uplift/oauth/gohighlevel/client_id`+`client_secret`; `crm_records` migration is shared with #340.
  DO NOT merge/deploy. Then stop.

## Follow-on (NOT this loop)
- Agent field-mapping across BOTH sources (HubSpot + GHL `crm_records`) → Cortex features.
- Multi-location agencies (loop locations per tenant).
