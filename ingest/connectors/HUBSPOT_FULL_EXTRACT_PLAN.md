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
- [ ] 4. **Full-extract record pull** (`list_records(object_type, since)`): all properties,
  paginated, incremental via epoch-millis Search filter; associations attached. Unit tests:
  pagination (2 pages), incremental filter value is epoch-millis, media props kept as
  URL-only + listed in `_media_refs`, no Files API call.
- [ ] 5. **`PgCrmRecordsSink`** in `ingest/sinks.py`: upsert into `crm_records`
  (ON CONFLICT (tenant_id,source,object_type,source_ref_id) DO UPDATE). Unit test the
  upsert SQL/columns + tenant scoping (SET LOCAL app.current_tenant).
- [ ] 6. **Wire the connector**: `HubSpotConnector` yields full records → `PgCrmRecordsSink`
  + still derives typed contacts/companies/deals rows for the existing UI. Keep the vector
  embedding of text (exclude `_media_refs`). Unit/integration test the end-to-end land.
- [ ] 7. **Registry + run_sync**: ensure `ingest/connectors/registry.py` + `run_sync.py`
  drive the new pull. Backwards-compatible (no breaking the other connectors).
- [ ] 8. **Full test pass**: `pytest tests/unit -q` green; `python -c` imports; no media/Files
  calls anywhere (grep). Update `BUILD_STATUS.md` (own lane only).
- [ ] 9. **PR**: open a PR (branch `feat/hubspot-full-extract`), get CI green (python/web/
  terraform/smoke). DO NOT merge/deploy without owner review — note in the PR that it needs
  the `crm_records` migration run via one-off task before the api rolls.

## Path B — HubSpot MCP (live agent access) — DO BOTH (user, sequenced after the extract core)
The extract above feeds ML/dashboards/grounding (the moat). Path B adds **live** agent access +
write-actions via MCP, so agents can fetch/act on HubSpot in real time without waiting on a sync.
Build after the extract core (items 1–9) is green.
- [ ] 10. **HubSpot MCP server** (`mcp/hubspot/` or vendor HubSpot's official server): expose
  read tools (search/get contacts/companies/deals/engagements, list properties/schemas) backed by
  the SAME per-tenant vaulted OAuth token (reuse `HubSpotConnector` auth/refresh). Read-only first;
  write-actions (create/update) behind Greenlight. NO media blobs (URL refs only). Unit-test the
  tool schemas + token threading with mocked HubSpot responses.
- [ ] 11. **Wire into the agent plane** as a per-tenant tool source (token injected from the vault
  per session; THE TRUST RULE — tenant from the JWT, never the request). Read tools auto-run;
  write tools route through the `api/control` Greenlight gate. Test the per-tenant isolation.
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
