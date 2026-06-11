# Demo-tenant knowledge corpus

Hand-authored internal knowledge-base documents for the **Meridian Mechanical Group** demo
tenant — the fabricated Austin commercial HVAC/plumbing services firm whose CRM universe is
produced by `scripts/generate_demo_dataset.py` and loaded by `scripts/demo/load_demo_tenant.py`.

These give the chat/RAG demo a *knowledge* corpus to cite from — pricing policy, sales
playbooks, onboarding guides, FAQs, and battlecards — distinct from the CRM activity/deal
narratives in the fixture's `documents`. Together they let the agent answer questions like
*"what's our discount policy?"* or *"how do we handle a competitive renewal?"* with grounded,
clickable citations (`conv/rag.py` drops any uncited claim).

## Discipline (matches the dataset fabrication standard)

- **No real names, brands, or PII.** Meridian, its competitor "Apex Air Systems", and all
  equipment (NorthCool, Kestrel, TruFlow, ...) are fabricated. Content is believable generic
  commercial-services knowledge — nothing derived from any real customer corpus.
- **Coherent with the hero arcs.** The discount floor (10% without VP sign-off), payment terms
  (net-30 vs net-45), COI requirements, and compliance documentation referenced here match the
  fixture's deals and Greenlight approvals, so the demo's RAG answers and queue line up.

## How it loads

`scripts/demo/seed_knowledge.py` chunks each `.md` (via `ingest.chunk.chunk_text`), embeds the
chunks through the **same** ingest embedder seam the production pipeline uses
(`ingest.run_sync.build_embedder` — offline stub by default, Titan V2 when
`INGEST_REAL_STORES=1`), and upserts into `documents` as the RLS-bound `crm_app` role with
`source='upload'` and `ref_id = demo:kb:<slug>#<seq>`. Idempotent: re-running re-embeds in place
(`ON CONFLICT (tenant_id, source, ref_id)`), and the `demo:kb:` namespace is left untouched by
the CRM loader's `demo:doc:%` wipe, so the two seeders compose in any order.
