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
- [x] 3. **`GoHighLevelFullConnector`** — DONE. `sync(tenant_id, *, location_id, since, object_types)`
  orchestrates discover → per-type `list_records` → `PgCrmRecordsSink.upsert_records`, reusing the
  shared `FullSyncResult`; per-object-type try/except (a bad type logged by exception TYPE only — no
  PII — and SKIPPED). Lands the source-agnostic `crm_records` (`source='gohighlevel'`) alongside the
  existing path. 4 unit tests (lands per type + forwards location, skips failing type, honors
  override, forwards since); ruff clean. DONE.
- [x] 4. **Registry + run_sync** — DONE. `registry.build_gohighlevel_full_connector(tenant_id, *,
  secrets, dsn|conn_factory, client, secret_writer, token, location_id)` mirrors the HubSpot factory:
  REUSES `GoHighLevelConnector.authenticate()` (same vault read + OAuth refresh + write-back) which
  set_token's AND set_location's the full client (both duck-typed; added `set_location` to the full
  client); `token`(+optional `location_id`) skips auth for the pasted-key/test path; lands the
  source-agnostic `crm_records` via `PgCrmRecordsSink`. `run_sync.run_full_extract_ghl(tenant_id, *,
  since)` is the SIBLING driver (real-mode-gated, Boto3SecretProvider + Aurora DSN) — additive, the
  default `--all` typed/vector sync untouched. 3 wiring tests (token+location bypass, vault-auth reuse
  resolves token+location from legacy JSON, real-mode gate); `pytest tests/unit` green, ruff clean.
- [x] 5. **Live GHL agent tools** — DONE. `agents/tools/ghl_live.py`: `ghl_object_types` /
  `ghl_fields` / `ghl_search` — `Policy.AUTO` read-only, backed by `ctx.ghl` (added `ghl: Any = None`
  to `ToolContext`; values incl. recording/attachment URLs returned as text refs, never fetched).
  `registry.tenant_ghl_client()` resolver REUSES `GoHighLevelConnector.authenticate` (vault token +
  location_id, honest `None` when not connected); registered in `_TOOL_CLASSES`; a lazy `ghl_resolver`
  injected into `make_executor` (additive — `_ghl_resolver` wired in `api/asgi.py` alongside the
  HubSpot one); granted to the **Scout** specialist (prompt + tool list). 12 tests (specs/AUTO,
  arg-threading, not-connected degradation, lazy-resolve-once, registry, resolver token+location,
  executor e2e for the bound tenant + degrade-when-unwired, roster grant); `pytest tests/unit` green,
  ruff clean.
- [x] 6. **Full test pass** — DONE. `pytest tests/unit` green (exit 0; 3 DB-gated skips); all touched
  modules import (`ingest.connectors.gohighlevel_full`/`registry`/`run_sync`, `agents.tools.ghl_live`/
  `registry`, `agents.roster`, `api.asgi`); ruff clean across `ingest/`+`agents/`+`api/asgi.py`+tests;
  no-media verified (the only `.read()` is the JSON API response in `_get`; media values are URL-ref
  flagged, never fetched). `BUILD_STATUS.md` entry added (own GHL section, stacked-on-#340 note).
- [x] 7. **PR** — DONE. Opened **PR #344** (branch `feat/gohighlevel-full-extract`, base
  `feat/hubspot-full-extract` so the diff is only the GHL work — stacked on #340). Additive summary +
  owner-gated note: merge #340 first then rebase on main; register the **GHL marketplace app** → seed
  `uplift/oauth/gohighlevel/client_id`+`client_secret`; the `crm_records` migration is shared with #340
  (no second migration). NOT merged/deployed. Loop complete.

## Follow-on (NOT this loop)
- Agent field-mapping across BOTH sources (HubSpot + GHL `crm_records`) → Cortex features.
- Multi-location agencies (loop locations per tenant).

## Sources (item 1 research)
- GHL Custom Objects API (v3): https://marketplace.gohighlevel.com/docs/ghl/objects/custom-objects-api/index.html
- GHL Custom Fields V2 API: https://marketplace.gohighlevel.com/docs/ghl/custom-fields/custom-fields-v-2-api/
- Contacts pagination (exact params/cursors): https://medium.com/@tuguidragos/fetch-all-gohighlevel-contacts-with-n8n-api-pagination-explained-25621d6e6976
- Community GHL MCP (269 tools — object coverage): https://github.com/mastanley13/GoHighLevel-MCP
- Official developer docs (SPA — verify paths live): https://developers.gohighlevel.com
