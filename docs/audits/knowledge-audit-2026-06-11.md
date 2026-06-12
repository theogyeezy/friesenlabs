# Knowledge feature — customer-readiness audit (2026-06-11, Lane Matt)

**Scope:** the full knowledge surface — `api/knowledge_routes.py` + `PgRagClient`, the
`documents` schema/RLS, `web/src/api/KnowledgeView.tsx` + e2e, the ingest pipeline
(`ingest/`: chunk → embed → pgvector, connectors, scheduler), corpus seeding
(`scripts/demo/seed_knowledge.py`, `agents/knowledge_seed/`), and the RAG/grounding path
(`conv/rag.py`, `conv/synthesizer.py`, `conv/session.py`, `agents/tools/readonly.py`,
`worker/worker.py`).

**Method:** 4 parallel read-audits (API+DB · web UI · ingest+seeding · RAG/grounding), every
load-bearing claim spot-checked against source, knowledge test suites run locally.

**Test status (local, this branch):** `test_knowledge_seed_corpus.py` +
`test_api_knowledge.py` + `test_seed_knowledge.py` + `test_conversation_live_citations.py` +
`test_conversation_turn.py` → **31 passed, 1 skipped** (the pgvector seed integration test
needs `UPLIFT_TEST_DB_URL`). An initial all-fail run was a local-venv gap
(`python-multipart` missing), not a product bug — it's in `requirements-api.txt`/CI.

## Verdict

**Architecturally sound, NOT yet customer-ready.** Tenant isolation is correct end-to-end
(per-op `SET LOCAL app.current_tenant`, FORCE'd RLS on `documents`, JWT-only tenant binding,
honest degraded states everywhere). The blockers are product-completeness, not security:
a paying customer has **no path that ever populates their corpus** (read-only API, scheduler
disabled, seeding operator-only) while the empty state promises it "fills in automatically";
and live citations carry **placeholder refs** (`doc:0`) instead of real document ref_ids.

## What's solid (verified against source)

- `GET /knowledge` + `GET /knowledge/search` (`api/knowledge_routes.py:114-156`): JWT-claims-only
  tenancy, q length-capped (500), limit clamped (FastAPI coerces `limit: int` → bad input is a
  422, not a 500), embedder failure degrades to an honest 200 `search_available:false`, never a
  leaked AWS error.
- `documents` schema (`db/schema.sql:14-27`): tenant_id NOT NULL, HNSW cosine index, unique
  (tenant_id, source, ref_id), RLS FORCE'd; ANN-query isolation probed by
  `scripts/isolation_test.py:87-96`.
- `PgRagClient` (`api/pg_clients.py:292-359`): per-op `SET LOCAL` in a single txn; inventory is
  a plain aggregate (works even when the embedder isn't wired).
- Citation invariant (`conv/rag.py:111-142` + `conv/synthesizer.py:155-163`): enforced in code
  twice (synthesizer drops refs not in the retrieved set; assembly re-filters); malformed model
  output degrades to extractive. The live coordinator path runs the same invariant
  (`conv/session.py:403-407`).
- Web: real-mode `KnowledgeView` honest states (inventory, search, warming-up, rollout-404,
  error-500) with 7 e2e cases (`web/e2e/knowledge.spec.ts`); module-entitlement route gating
  works (`web/src/app.tsx:159`); mock prototype `screens/knowledge.tsx` is mock-build-only.
- Seeding implemented + tested: `agents/knowledge_seed/` corpus (25+ docs) through the
  production chunker/embedder seam, idempotent upserts (`scripts/demo/seed_knowledge.py`).
- `default_structured_sink()` (`ingest/connectors/__init__.py:22`): real `PgCrmStructuredSink`
  in real mode, fail-loud when the switch is on but no DSN.

## Findings

### P0 — release blockers

1. **No path populates a customer's corpus.** The knowledge API is read-only; the ingest
   scheduler is applied DISABLED (`infra/modules/ingest/main.tf`, `ingest_tenants` empty);
   seeding is an operator script. Meanwhile the empty state
   (`web/src/api/KnowledgeView.tsx:347-350`) tells the customer their knowledge base "fills in
   automatically as your connected sources … are ingested — there's nothing for you to do
   here." Until the scheduler is enabled for their tenant (or an upload path exists), that copy
   is false and the tab is permanently empty. Fix = ship a tenant-scoped document add path
   (upload/paste → existing chunk→embed→upsert) **or** make the auto-ingest promise real and
   point the empty state at connecting sources.
2. **Live citations carry placeholder refs.** `conv/rag.py:106` normalizes hits via
   `hit.get("ref") or hit.get("id")` — but live `PgRagClient.search` returns **`ref_id`**
   (`api/pg_clients.py:330-337`), so every live vector hit falls back to the positional
   `doc:{i}` default, and `_normalize` hardcodes `source="rag"` (discards the real
   upload/hubspot source). Internally consistent → the invariant holds and live verify passed
   grounded=True, but customers see `doc:0` instead of a traceable document ref. Tests miss it
   because FakeRag fixtures return `ref` keys. One-line fix + a live-shape regression test.
3. **Grounding is invisible when the corpus is empty.** Empty retrieval yields "No supporting
   material found." with zero citations (`conv/rag.py:162-167,193`) — indistinguishable from a
   generic refusal. `Turn.as_dict()` (`conv/session.py:87-99`) drops the `dropped` claims and
   carries no retrieval evidence. Add `grounding_status` + `retrieved_count` to the `/chat`
   response and render it in `ChatDock`.

### P1

4. **One-bucket degrade reasons / silent degraded modes.** Every embedder failure reads
   "search model not configured" (`api/knowledge_routes.py:139-146`, type-only WARNING log);
   the UI says "warming up" (`KnowledgeView.tsx:318-329`) even for a permanent config gap;
   `AnthropicSynthesizer` swallows client-build failures with no log
   (`conv/synthesizer.py:136-139`); the worker starts silently with `rag=None`
   (`worker/worker.py:170-177` → `readonly.py:19` returns `[]`). Differentiate
   transient/config reasons, log them, and surface degraded mode at worker startup.
5. **Embedding cost/rate-limit controls.** Sync path embeds per-text with no
   backoff/circuit-breaker/cost accounting (`ingest/embed.py:71-90`); `batch_embed` silently
   falls back to sync when its env is unset (`ingest/embed.py:253-257`) and its Bedrock job
   shapes are explicitly `# VERIFY`-flagged, never live-run. A large first backfill =
   throttling + surprise spend. (Cost attribution exists for Anthropic calls — extend the
   pattern to embeddings.)
6. **Partial corpus on mid-sync embed failure.** `sync_tenant` continues past failed chunks
   with no partial status / failed-chunk report (`ingest/pipeline.py:96-149`,
   `run_sync.py:256-262`). Idempotent re-runs do heal it, but operators can't see it.
7. **Answer/citation coherence on the live path.** On knowledge turns the displayed prose is
   the coordinator's, while citations come from an independent `_grounded_answer(message)` RAG
   synthesis (`conv/session.py:403-407`) — claims may not match the prose shown. Decide:
   merge/dedupe, or display the grounded claims as their own block.
8. **Onboarding never touches knowledge.** `STEP_IDS = (load_data, try_chat, invite_team)`
   (`api/onboarding_routes.py:51`); `/onboarding/load-sample` seeds CRM only; the knowledge
   empty state has no CTA. Seed a small sample corpus with load-sample (reuse
   `agents/knowledge_seed`) and/or link the empty state to integrations.
9. **404 reason ambiguity.** Unwired DSN → bare 404 → UI shows "rolling out" + refresh forever
   (`knowledge_routes.py:78-81`, `KnowledgeView.tsx:136-143`). Carry a reason code so
   unprovisioned ≠ rolling-out.

### P2

10. Search pagination/total (`MAX_SEARCH_LIMIT=25`, no offset/total_hits).
11. Embedding-dim assert at search time (upsert validates 1024; search assumes it —
    `api/pg_clients.py:317-337`).
12. Stale docstring: `ingest/run_sync.py:~172` still says the structured sink "stays IN-MEMORY
    in both modes" — false since #231 (`default_structured_sink` is gated-real).
13. Test gaps: real-DB empty-tenant `/knowledge` integration test; `PgCrmStructuredSink`
    cross-tenant isolation integration test; cursor advancement/tenant-scoping tests; CSV
    import unit tests; knowledge module-gating e2e; positive-row assertion in the
    isolation-test ANN probe.

## Claims investigated and DISPROVEN (do not re-file)

- ~~"`limit=abc` 500s"~~ — FastAPI coerces `limit: int`; bad input is a 422.
- ~~"Chat UI never renders citations"~~ — `web/src/api/ChatDock.tsx:320-336` renders them
  (with `data-testid`s); only the mock prototype `screens/chat.tsx` lacks them.
- ~~"run_sync hardcodes InMemoryStructuredSink — CRM rows never land"~~ —
  `default_structured_sink()` returns the real Pg sink in real mode, fail-loud without a DSN.
  Only the run_sync docstring is stale (P2 #12).

## Existing TODO items this audit confirms (already tracked, not duplicated)

Seed the live corpus (P1 + lines 91-92, 349) · live-citation integration test through
ManagedAgentsRuntime (lines 40-42) · enable the ingest scheduler (lines 29, 97-99) · connector
live VERIFY pass (lines 33, 95) · connector-secret IAM broadening (lines 84, 96) · batch-embed
live run (line 355).
