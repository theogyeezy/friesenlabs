# ingest/ — Ingestion & Embeddings (Phase 2)

Pull a tenant's world from a source, land it in the data plane, and keep it fresh
incrementally:

```
connector.pull(since) → connector.land(records) → chunk → embed (Titan V2) → upsert into documents (pgvector)
                                   │                                                    ▲
                              raw → S3                                            keyed by (tenant_id, source, ref_id)
                              rows → Aurora
```

HubSpot is the first connector and establishes the **pattern**. Everything here is
offline/mocked: **no real external API calls, no real AWS, no secrets.** Every
external client (HubSpot, Bedrock, Postgres, S3) is an **injected interface**; tests
pass fakes. Importing any module in this package needs neither AWS nor a DB.

## Modules

| File | Responsibility |
|---|---|
| `connectors/base.py` | `Connector` ABC + injected `SecretProvider` / `RawSink` / `StructuredSink` Protocols. `land()` is shared (raw→S3, rows→Aurora, enforces single-tenant). |
| `connectors/hubspot.py` | `HubSpotConnector`. Injected `HubSpotClient`; normalizes to `companies/contacts/deals/activities` (carrying `tenant_id`, `source='hubspot'`, `ref_id`). No real HubSpot calls. |
| `chunk.py` | `chunk_text(text, target_tokens=400, overlap=40)` + record-type strategies (`chunk_record` CRM, `chunk_transcript` by speaker turn, `chunk_stripe` customer/invoice). Every `Chunk` carries `tenant_id/source/ref_id`. |
| `embed.py` | `embed(text, client=None)` — exact Titan V2 call (`amazon.titan-embed-text-v2:0`, body `{"inputText","dimensions":1024,"normalize":true}`). Lazy real client. `batch_embed` documents the S3-JSONL Bedrock Batch path. |
| `pipeline.py` | `sync_tenant(tenant_id, connector, embedder, store, cursor_store)` — orchestration + incremental cursor. In-memory fakes + guarded psycopg2 impls. |

## Embedding lock

Locked to **Titan Text Embeddings V2 / 1024 dims** to match
`db/schema.sql` `documents.embedding vector(1024)`. Constants live in
`ingest/__init__.py` (`EMBEDDING_MODEL_ID`, `EMBEDDING_DIM`). **Changing either
forces a full re-embed of every tenant** — do not change without a migration plan.

Request body shape (asserted in `tests/unit/test_embed.py`):

```json
{ "inputText": "<text>", "dimensions": 1024, "normalize": true }
```

## Incremental sync (how a second run embeds ~nothing)

1. `cursor_store.get(tenant_id, source)` → the per-tenant/per-source high-water mark
   (max `updated_at` seen last run).
2. `connector.pull(since)` only returns records changed since that cursor (a real
   HubSpot impl filters on `hs_lastmodifieddate`).
3. For each chunk we compute `sha256(content)` and compare to what's stored
   (`store.get_content_hash`). **Unchanged → skipped (no embed call).** New/changed →
   embed once and `UPSERT` by `(tenant_id, source, ref_id)`.
4. Advance the cursor to the new max `updated_at`.

So the **first** run embeds N chunks; the **second** run over identical fixture data
pulls them again but embeds **0** (all hashes match), and with a real
change-filtered connector it pulls 0. `tests/unit/test_pipeline_incremental.py` is
the gate that proves this.

`documents` is unique on `(tenant_id, source, ref_id)`. A record that yields multiple
chunks gets distinct ref_ids via `Chunk.doc_ref_id` (`"<ref>#<seq>"`).

## Tenancy

`tenant_id` is stamped on **every landed row** (by the connector) and **every chunk**
(by the chunker), and re-asserted at land time and at upsert time. `land()` raises on
any cross-tenant record. The `PgDocumentStore` sets `app.current_tenant` before every
`documents` access so Postgres RLS applies (it connects as the non-owner `crm_app`
role — see `db/roles.sql`).

## Sidecar vs. Full (the write path is the only difference)

- **Sidecar** — the client keeps **HubSpot as system-of-record**. We ingest read-only
  for retrieval/agents; any writes the agents propose flow **back to HubSpot via
  tools** (behind Greenlight). Aurora is a read-optimized projection.
- **Full** — **Aurora is system-of-record**. The client has migrated off HubSpot;
  writes land in Aurora directly.

**The ingestion code is identical in both modes** — same connector→chunk→embed→upsert
pipeline. Only the *write-back path* differs (a different tool target), and that lives
in `agents/`, not here. Ingestion is always **draft-only / read-only against the
source**; it never writes back to a real CRM.

## Incremental architecture (AWS — documented, not built here)

Production incremental design (`infra/` owns the IaC; not built in this phase):

```
EventBridge (schedule / source webhook)
      → Step Functions (per-tenant sync state machine: auth → pull → land → chunk → embed → upsert)
      → SQS (per-tenant work queue; smooths bursts, isolates noisy tenants, enables retries/DLQ)
      → ingest worker (this package) runs sync_tenant per message
```

- **EventBridge** kicks scheduled syncs and routes source webhooks (e.g. HubSpot
  change events) to trigger near-real-time incremental pulls.
- **Step Functions** sequences the pipeline with retries/backoff and per-step traces.
- **SQS** is the per-tenant work buffer (DLQ for poison records); keeps one tenant's
  backlog from starving others.
- Initial full backfills use the **Bedrock Batch** embedding path (`embed.batch_embed`
  docstring: S3-JSONL in/out, `create_model_invocation_job`); incremental syncs use
  the synchronous `embed()`.

## Running the tests

From the repo root (no DB, no AWS, no network required):

```
.venv/bin/pytest -q
```

The optional `tests/integration/test_ingest_pgvector.py` performs a real upsert into
`documents` only when `UPLIFT_TEST_DB_URL` is set; otherwise it **skips cleanly**.
