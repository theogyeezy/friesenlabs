# Brief: Phase 2 — Ingestion & Embeddings (connectors → chunk → embed → pgvector)

## Goal
Get a tenant's world into the data plane and keep it fresh incrementally: pull from a source,
land raw → S3 + structured → Aurora, chunk for retrieval, embed with Titan V2 (1024 dims), upsert
into `documents` (pgvector). Build the **pattern** with HubSpot as the first connector. Everything
runs offline/mocked here — **no real external API calls, no real AWS** (author + test only).

## Owner / directory
A background worker owns **`ingest/`** exclusively (+ its tests in `tests/`). Do NOT touch
`infra/`, `web/`, `db/`, `api/`, `agents/`, or `semantic/`. Do not run `git`.

## Files to create (in `ingest/`)
- `ingest/__init__.py`
- `ingest/connectors/base.py` — `Connector` ABC: `authenticate()`, `pull(since_cursor)`,
  `land(records)`. A connector does three things: auth (via vaulted creds — here, an injected
  fake secret provider), pull records since the last cursor, land raw JSON to S3 +
  normalized rows to Aurora.
- `ingest/connectors/hubspot.py` — `HubSpotConnector` implementing the base. **Do NOT call the real
  HubSpot API.** Take an injected `client` (interface) so tests pass a fake returning fixture
  contacts/companies/deals. Normalize to the `contacts`/`companies`/`deals`/`activities` shapes
  from `db/schema.sql` (carry `tenant_id`, `source='hubspot'`, `ref_id`).
- `ingest/chunk.py` — `chunk_text(text, target_tokens=400, overlap=40)` → list of chunks
  (~300–500 tokens, light overlap). Plus record-type strategies: CRM record → one chunk per contact
  summary + separate chunks for notes/activities; transcripts → by speaker turn; Stripe →
  customer/invoice-level text. Every chunk carries `tenant_id`, `source`, `ref_id`.
- `ingest/embed.py` — `embed(text, client=None)`: shape the Bedrock Titan V2 call exactly
  (`modelId="amazon.titan-embed-text-v2:0"`, body `{"inputText", "dimensions":1024, "normalize":true}`)
  but accept an injected client so tests use a **fake** returning a deterministic 1024-float vector.
  Default client construction is lazy (only when actually invoked) so importing never needs AWS.
  Add a `batch_embed` stub that documents the S3-JSONL Bedrock Batch path (no real job).
- `ingest/pipeline.py` — `sync_tenant(tenant_id, connector, embedder, store, cursor_store)`:
  pull → land → chunk → embed → **upsert by `ref_id`** into `documents` (new/changed embed;
  unchanged skipped). Persist a per-tenant, per-source high-water cursor so a second run embeds
  almost nothing. `store`/`cursor_store` are injected interfaces (a fake in-memory impl for tests;
  a psycopg2-backed impl guarded so it only imports/connects when a DSN is provided).
- `ingest/README.md` — the pattern, the Sidecar-vs-Full note (Sidecar: client keeps HubSpot as
  system-of-record, writes flow back via tools; Full: Aurora is system-of-record; ingestion code is
  the same, only the write path differs), and the EventBridge→StepFunctions→SQS incremental design
  (documented; not built here).

## Constraints (hard)
- **No real network / no real AWS / no secrets.** All external clients are injected; tests use fakes.
- **Draft-only**: ingestion only READS from sources + writes to your own data plane. No writes back to
  any real CRM.
- Lock embedding at Titan V2 / **1024 dims** to match `db/schema.sql` (changing later forces re-embed).
- `tenant_id` on every landed row and every chunk — no cross-tenant mixing.

## Tests (the gate — must run with no DB and no AWS)
- `tests/unit/test_chunk.py` — chunk sizing/overlap; tenant_id/source/ref_id carried on every chunk.
- `tests/unit/test_embed.py` — with a fake client, `embed()` returns a 1024-length vector and shapes
  the Titan request body correctly (assert modelId + dimensions=1024 + normalize=True).
- `tests/unit/test_pipeline_incremental.py` — run `sync_tenant` twice over the same fixture data with
  in-memory fakes: first run embeds N chunks, **second run embeds ~0** (incremental via ref_id cursor).
  Assert all stored docs carry the right tenant_id.
- (Optional) `tests/integration/test_ingest_pgvector.py` — real upsert into `documents` when
  `UPLIFT_TEST_DB_URL` is set; **skip cleanly** otherwise.
- Everything must pass under `.venv/bin/pytest -q` from the repo root with no services running.

## Done when
`ingest/` implements connector→chunk→embed→upsert with injected clients; the incremental test proves
the second sync embeds ~nothing; all new unit tests pass offline; `python -c "import ingest.pipeline"`
works without AWS/DB; `ingest/README.md` documents the connector pattern + incremental design.
Report: files created, test results (verbatim tail), and anything you stubbed/flagged "verify".
