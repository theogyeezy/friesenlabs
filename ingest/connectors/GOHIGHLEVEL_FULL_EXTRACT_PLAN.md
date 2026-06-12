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

## Grounded API (item 1 — from GHL marketplace docs + a 269-tool community MCP server + a contacts-pagination write-up; sources at bottom)
- **Host / auth:** `https://services.leadconnectorhq.com`; `Authorization: Bearer <token>`; **location-scoped** — every list call takes `locationId` (from the OAuth envelope's `location_id`).
- **Pagination (uniform):** `limit` (max **100**, default 20) + `startAfter` + `startAfterId`; the response returns the next cursors at `meta.startAfter` / `meta.startAfterId` — loop until they're absent. (NOT HubSpot's `after`.)
- **Contacts — GROUNDED:** `GET /contacts/` · `Version: 2021-07-28` · params `locationId,limit,startAfter,startAfterId`.
- **Conversations** `Version: 2021-04-15` (host call recordings/transcripts → media: URL refs only, never bytes).
- **Objects to extract ("everything"):** contacts, opportunities, conversations, calendars/appointments, tasks + notes (under contacts), products, payments, invoices, **custom objects**, + the **Associations** graph. (Community MCP covers ~269 tools across these.)
- **Custom Objects = v3 API:** sub-APIs *Object Schema* (`/docs/ghl/objects/object-schema`), *Records*, *Search Object Records*; Bearer (sub-account or Private Integration Token). Use Object Schema to DISCOVER custom objects + their fields, then Records/Search to pull.
- **Custom Fields:** *Custom Fields V2 API* (fields + folders) covers Custom Objects + Company; contacts/opportunities custom fields ride the object's inline `customFields` array (flatten both into `properties`).
- **Associations API** exists (relationship mapping) → the `crm_records.associations` graph.
- **Rate limits:** 100 req/10s burst + **200k/day per location** → ADD 429/Retry-After backoff.
- **STILL `# VERIFY` on first live run** (SPA docs blocked exact paths): per-Version + exact path for opportunities / calendars / custom-object records+schema / associations; the per-object incremental field (`dateUpdated` contacts vs `updatedAt` opportunities — confirm each); the custom-objects v3 schema/search paths; the 429 body shape. Item 2 codes these as a per-resource map with `# VERIFY` markers (far fewer than the old connector's blanket guess).

## Checklist (the loop works these in order)
- [x] 1. **Ground the GHL v2 API** — DONE. Researched the GHL marketplace docs + a 269-tool community
  MCP server + a contacts-pagination write-up; recorded the **Grounded API** section above (host/auth,
  uniform `startAfter`/`startAfterId` pagination, contacts endpoint+Version, the full object list incl.
  custom objects + associations, custom-fields shapes, rate limits). Remaining unknowns are a SHORT
  `# VERIFY` list (vs the old connector's blanket guess) for item 2 to encode as a per-resource map.
- [x] 2. **`GoHighLevelFullClient`** (`ingest/connectors/gohighlevel_full.py`) — DONE. `_get` pins the
  per-resource `Version` header + does **429/Retry-After backoff** (`_MAX_RETRIES=5`, injectable sleep);
  `discover_object_types()` (standard ∪ custom from the Object-Schema API, tolerant of failure);
  `discover_fields()`; `list_records()` paginates via `startAfter`/`startAfterId` (cursors from
  `meta.*`), seeds incremental with epoch-millis, reuses `hubspot_full.Record`; `_normalize` flattens
  inline `customFields` → `cf_<id>`, flags media values URL-only (`_media_refs`, never fetched),
  pulls `associations`; `search_live()` bounded. Reuses the source-agnostic `Record`. 9 unit tests
  (pagination, epoch-millis seed, 429 retry, Version header, token-required, discovery×2, normalize,
  search); ruff clean. Remaining non-contacts paths/versions stay `# VERIFY`. DONE.
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

## Sources (item 1 research)
- GHL Custom Objects API (v3): https://marketplace.gohighlevel.com/docs/ghl/objects/custom-objects-api/index.html
- GHL Custom Fields V2 API: https://marketplace.gohighlevel.com/docs/ghl/custom-fields/custom-fields-v-2-api/
- Contacts pagination (exact params/cursors): https://medium.com/@tuguidragos/fetch-all-gohighlevel-contacts-with-n8n-api-pagination-explained-25621d6e6976
- Community GHL MCP (269 tools — object coverage): https://github.com/mastanley13/GoHighLevel-MCP
- Official developer docs (SPA — verify paths live): https://developers.gohighlevel.com
