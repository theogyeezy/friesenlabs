# HubSpot full-extract connector — build plan / spec

**Goal:** replace the thin MVP HubSpot connector (5–7 hardcoded fields, 4 object
types, fragile ISO cursor) with a **full-fidelity extract**: every property, every
object type (standard + engagements + custom) + associations, into a generic JSONB
store agents can map/reason over. Fixes the current incremental-sync `HTTPError`.
**No binary media stored** (audio/photo/video) — references/URLs only.

This file is the spec a `/loop` executes. Work the **Checklist** top-to-bottom;
each item: implement → run the listed test/validation → commit → check the box →
report. Keep edits small and reversible. Don't start a task whose inputs don't exist.

---

## Decisions (locked)
- **Scope:** EVERYTHING — all standard objects (contacts, companies, deals, tickets,
  products, line_items, quotes), all engagements (calls, emails, meetings, notes,
  tasks), **all custom objects**, + the association graph, + ALL properties per object.
- **Storage:** new generic `crm_records` JSONB table (full bag), alongside the existing
  typed `contacts/companies/deals` tables (still populated, for the current UI views).
- **Media:** NEVER call the Files API / never download blobs. For `file`-type properties
  or media URLs (audio/photo/video by MIME/extension), store the URL/reference as text,
  set `properties._media_refs`, and EXCLUDE it from embedding. Metadata yes, bytes no.

## Architecture
- Keep `HubSpotConnector` (auth/refresh/vault) — reuse its OAuth token handling unchanged.
- Replace `HubSpotRestClient` with a **full-extract client** (`hubspot_full.py` or rework
  in place): object discovery → property discovery → paged record list (incremental) →
  associations. stdlib `urllib` only, lazy import, token via `set_token()`.
- New sink `PgCrmRecordsSink` (in `ingest/sinks.py`) upserts the JSONB bag.
- Pipeline still chunks+embeds text → `documents` vector store (now over the full bag,
  media-refs excluded).

## `crm_records` schema (additive — `db/schema.sql`)
```sql
CREATE TABLE IF NOT EXISTS crm_records (
  id            bigserial PRIMARY KEY,
  tenant_id     text NOT NULL,
  source        text NOT NULL,                 -- 'hubspot'
  object_type   text NOT NULL,                 -- 'contacts','deals','calls','p_customobj', ...
  source_ref_id text NOT NULL,                 -- HubSpot object id
  properties    jsonb NOT NULL DEFAULT '{}',   -- full property bag; media as URL refs only
  associations  jsonb NOT NULL DEFAULT '{}',   -- { toObjectType: [ids] }
  updated_at    timestamptz,                   -- HubSpot hs_lastmodifieddate
  archived_at   timestamptz,                   -- soft-archive parity with the typed tables
  synced_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, source, object_type, source_ref_id)
);
ALTER TABLE crm_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_records FORCE ROW LEVEL SECURITY;
CREATE POLICY crm_records_tenant ON crm_records
  USING (tenant_id = current_setting('app.current_tenant', true));
CREATE INDEX IF NOT EXISTS idx_crm_records_active
  ON crm_records (tenant_id, object_type) WHERE archived_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_crm_records_props ON crm_records USING gin (properties);
-- grants: crm_app gets SELECT/INSERT/UPDATE (no DELETE — soft-archive only), seq usage
```
Run via the `uplift-migrate-oneoff` one-off task (same safe pattern as the `archived_at`
fix): `python -m api.migrate` loads `schema.sql` idempotently. Additive only.

## HubSpot API calls (CRM v3 — VERIFY shapes against current docs on first live run)
- **Object discovery:** `GET /crm/v3/schemas` (custom objects, gives objectTypeId +
  fullyQualifiedName) + a constant list of standard/engagement types.
- **Property discovery:** `GET /crm/v3/properties/{objectType}` → all property `name`s
  (+ `type`/`fieldType` to flag `file`/media properties for the no-blob rule).
- **Records:** `GET /crm/v3/objects/{objectType}?properties=<all>&associations=<linked
  types>&limit=100&after=<cursor>&archived=false`. Paginate via `paging.next.after`.
  Incremental: prefer the Search API with a `hs_lastmodifieddate GTE <epoch-millis>` filter
  (epoch millis — NOT ISO — this is the suspected current-bug fix); full extract uses List.
- **Associations:** request inline via `associations=` param where supported, else
  `GET /crm/v3/objects/{type}/{id}/associations/{toType}`.

## Media exclusion (concrete rule)
- From property discovery, mark property names whose `type=='file'` (or value is a URL with
  audio/photo/video MIME or extension `.mp3/.wav/.m4a/.png/.jpg/.jpeg/.gif/.webp/.mp4/.mov/
  .avi/.webm`) as media. Store their **value (URL/id) as a string** under
  `properties` and list them in `properties._media_refs`. NEVER fetch the bytes; NEVER call
  the Files API. Embedding step skips `_media_refs` + binary-looking values.

## Checklist (the loop works these in order)
- [x] 1. **Migration first.** Added the `crm_records` table (uuid tenant_id, composite natural
  PK, FORCE RLS via the DO-block array + belt-and-suspenders, gin/active indexes, soft-archive)
  to `db/schema.sql` + crm_app `SELECT/INSERT/UPDATE` (no DELETE) to `db/roles.sql`. Validated:
  `pytest tests/unit/test_sql_schema.py` green (static pglast SQL/RLS-contract gate). Applied via
  one-off task at deploy time (NOT CI). DONE.
- [x] 2. **Property discovery** — `HubSpotFullClient.discover_properties()` in
  `ingest/connectors/hubspot_full.py` returns a `PropertySet(names, media)` from
  `GET /crm/v3/properties/{type}`, flagging `fieldType/type == 'file'` as media (URL-ref-only).
  4 unit tests in `tests/unit/test_hubspot_full.py` (lists all, flags media only, empty, token
  required); ruff clean. DONE.
- [x] 3. **Object discovery** — `discover_object_types()`: standard objects + engagements
  (constants) ∪ custom objects from `GET /crm/v3/schemas` (by `fullyQualifiedName`); tolerant of
  a schemas-call failure (falls back to standard set). 2 unit tests (union + failure tolerance);
  ruff clean. DONE.
- [x] 4. **Full-extract record pull** — `list_records(object_type, prop_set, since, associated_types)`
  yields normalized `Record`s with ALL properties, paginated via `paging.next.after`. Full pull =
  List API (associations inline); incremental = Search API filtered on lastmod `GTE` **epoch-millis**
  (`_to_millis`, the sync-bug fix vs the old ISO filter). `_normalize` keeps media values as URL
  refs + flags `_media_refs`, flattens associations to `{toType:[ids]}`. 4 unit tests (pagination,
  epoch-millis filter value, media-ref-only + no Files call, association flatten); ruff clean. DONE.
- [x] 5. **`PgCrmRecordsSink`** in `ingest/sinks.py`: `upsert_records(tenant_id, records)` UPSERTs
  the full bag into `crm_records` (ON CONFLICT … DO UPDATE, un-archives), tenant_id from the GUC
  (`current_setting('app.current_tenant')`, SET LOCAL — never hand-written), properties/associations
  `::jsonb`, per-row SAVEPOINT isolation, crm_app non-owner. Accepts Record dataclass OR dict.
  6 unit tests (SET LOCAL first, upsert shape/jsonb/GUC/on-conflict, both input types, empty,
  missing-keys reported, savepoint rollback); ruff clean. DONE.
- [x] 6. **Wire the connector** — `HubSpotFullConnector.sync(tenant_id, since, object_types)`
  orchestrates discover-objects → per-type discover-properties → paged `list_records` →
  `PgCrmRecordsSink.upsert_records`, with per-object-type try/except (one bad type logged by
  TYPE only — no PII — and SKIPPED so "pull everything" never dies on one type). ADDITIVE: the
  existing typed contacts/companies/deals + vector-embedding path is untouched; this lands
  full-fidelity `crm_records` alongside it. 4 unit tests (lands per type, skips failing type,
  honors override, forwards since+associations); full `pytest tests/unit` green; ruff clean. DONE.
- [x] 7. **Registry + run_sync** — `registry.build_hubspot_full_connector(tenant_id, secrets, dsn/
  conn_factory, token)` wires HubSpotFullClient + PgCrmRecordsSink + HubSpotFullConnector, reusing
  `HubSpotConnector.authenticate()` for the vault token (or `token=` to bypass). `run_sync.run_full_extract(
  tenant_id, since)` is a real-mode-only driver. ADDITIVE — separate from `build_sync_connector`/the
  `--all` typed-vector path (untouched). 3 unit tests (token bypass, vault-auth reuse, real-mode gate);
  no regressions; ruff clean. DONE.
- [x] 8. **Full test pass** — `pytest tests/unit` = **2187 passed, 3 skipped** (DB-gated, run in CI);
  imports OK; ruff clean across all new/touched files; no-media grep confirms the only `.read()` is
  the JSON response body in `_get`/`_post` (never a file blob, never the Files API). `BUILD_STATUS.md`
  build-log entry added (own lane). DONE.
- [x] 9. **PR** — opened **#340** (feat/hubspot-full-extract → main) with the additive-extract
  summary + the deploy-ordering note (run the `crm_records` migration via `uplift-migrate-oneoff`
  BEFORE the api rolls). NOT merged/deployed (owner-gated). CI verified before the loop's final stop.
  Path A (extract) DONE. Path B (MCP, items 10–12) builds next on its OWN branch (separate feature).

## Path B — HubSpot MCP (live agent access) — DO BOTH (user, sequenced after the extract core)
The extract above feeds ML/dashboards/grounding (the moat). Path B adds **live** agent access +
write-actions via MCP, so agents can fetch/act on HubSpot in real time without waiting on a sync.
Build after the extract core (items 1–9) is green.
- [x] 10. **HubSpot live agent tools** (the "MCP" surface — repo uses native agent tools, not the
  MCP wire protocol; no new dep) in `agents/tools/hubspot_live.py`: `hubspot_object_types`,
  `hubspot_properties`, `hubspot_search` — all `Policy.AUTO` (read-only, auto-run) backed by
  `ctx.hubspot` (a per-tenant token-set `HubSpotFullClient`; added `hubspot` to `ToolContext`).
  Added `HubSpotFullClient.search_live()` (bounded one-page Search, no whole-CRM walk). NO media
  blobs (URL refs only). Write actions deferred (ALWAYS_ASK/Greenlight). 5 unit tests (specs/AUTO,
  object types, media flag, search arg-threading, not-connected); 204 agent/tool tests still green;
  ruff clean. DONE.
- [x] 11. **Wired into the agent plane** — the 3 live tools registered in `agents/tools/registry.py`
  `_TOOL_CLASSES`; `tenant_hubspot_client(tenant_id, secrets)` resolves the per-tenant vault token
  (reusing connector auth; None when not connected). `api/asgi.make_executor` gained an additive
  `hubspot_resolver` and injects a LAZY per-tenant client into the `ToolContext` (tenant from the
  verified binding — THE TRUST RULE; no vault read unless a HubSpot tool runs); `build_app` wires it
  in real mode. Tools stay `Policy.AUTO` (read-only auto-run); writes remain ALWAYS_ASK/Greenlight.
  4 wiring tests (lazy-resolve-once, registry membership, resolver none/connected); full
  `pytest tests/unit` = **2196 passed**; ruff clean. DONE.
- [ ] 12. **Expose in chat/Studio**: agents can call "ask HubSpot live"; surface results with
  citations. Honest empty/error states. Test the end-to-end tool-call path (mocked).

## Follow-on (separate, NOT this loop)
- Phase 4 — agent field-mapping: an agent reads discovered schema + sample `crm_records` →
  maps HubSpot fields to the Uplift model + proposes Cortex features (uses BOTH the resident
  extract and the live MCP introspection).

## Guardrails
- Additive only; no destructive SQL. RLS FORCE + crm_app non-owner (isolation must hold).
- Token/PII never logged. Media bytes never fetched.
- Don't deploy from the loop — land code + CI; the migration + api deploy are owner-gated
  and must be coordinated (deploy pipeline currently carries others' unapplied infra).
