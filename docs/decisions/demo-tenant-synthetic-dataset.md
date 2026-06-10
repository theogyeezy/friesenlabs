<!-- Decision brief — produced by the QA+DECISIONS lane (2026-06-10).
     Research: parallel agents over repo code + current Anthropic docs; claims adversarially
     spot-checked by an independent critic agent. Status: DRAFT until ratified by Nick + Matt. -->

# Synthetic demo tenant — dataset spec + generator + demo script

Decision brief for Nick (infra) and Matt (GTM). Repo read-only; everything below is a spec for a lane to implement (target home: `docs/decisions/DD-0XX-demo-tenant.md` — `docs/` is local-only/gitignored per `CLAUDE.md` but Syncthing-shared between both machines, so both lanes see it; a private gist is the fallback if you want it out of the working tree entirely).

## Context (what the code does today)

**The seed is a smoke test, not a demo.** `scripts/seed_demo_tenant.py` inserts 4 companies (:33–38), 6 contacts (:46–53), 6 deals across stages `new/qualified/proposal/negotiation/closed_won` (:62–69), 5 one-line activities (:78–84), and 3 pending approvals (:90–101). It runs as the RLS-bound `crm_app` role under `SET app.current_tenant` (:23–28) and is idempotent — it deletes this tenant's rows first (:30–31). That isolation-exercising, delete-then-insert pattern is exactly right and should be kept.

Five gaps that matter for a sales demo:

1. **The RAG corpus is empty.** The seed never touches `documents` (the pgvector store, `db/schema.sql:14–26`, Titan V2 1024-dim). `conv/rag.py` retrieves from `rag.search` over pgvector **plus** `crm.read` (:87–101), and its citation invariant drops any uncited claim (:111–142) — so on a freshly seeded tenant, chat has no vector corpus to cite from. The chat demo can't sing against an empty `documents` table.
2. **`saved_views` is wiped but never seeded** (seed :30–31 deletes it; no inserts) — first login shows an empty dashboard. Schema: `db/schema.sql:77–87` (`view_id`, `spec_json`, `source_prompt`).
3. **All activities timestamp at seed time.** `activities.occurred_at` defaults to `now()` (`db/schema.sql:71`) and the seed's INSERT omits it (:85–88) — every "conversation" looks like it happened in the same second. Demo data needs explicit backdated `occurred_at`.
4. **The fabrication discipline is violated.** Seed contacts use plausibly-real domains — `dana@birchwoodcap.com`, `priya@halcyonlogistics.io` (:46–52). Draft-only gating (CLAUDE.md hard constraint 2) means nothing sends today, but the moment a real email tool is wired and someone clicks Approve in a demo, mail could route to a real mailbox. The ghl-test-data discipline (project memory, verified 2026-06-01) is the standard: reserved-by-RFC undeliverable domains + the `555-01xx` fictitious phone block, zero deliverability by construction.
5. **Approvals don't exercise the autonomy dial.** L2 thresholds are `max_auto_value=$1,000` and `max_discount=10%` (`api/control/autonomy.py:14–17`; decision logic :56–69). The 3 seeded approvals are all five-to-six figures — none straddles the thresholds, so you can't show the dial changing behavior.

What the demo surfaces actually support: `GET /approvals` + `POST /approvals/{id}/decide` and `POST /chat`, `GET/POST /views` (`api/app.py:97–158`); the wired web components are `web/src/api/GreenlightQueue.tsx` (approve/edit/deny against the live API) and `web/src/api/ChatDock.tsx` (renders answer + citations; shows the graceful 503 while AI is parked). Side-effecting tools are exactly `send_email`, `update_deal`, `issue_quote` (+ `draft_email`) in `agents/tools/sideeffecting.py`; read-only are `search_rag`, `query_cube`, `read_crm`. Greenlight supports approve / **edit** / deny-with-message (`api/control/greenlight.py:170–190`) — the edit path is a great demo beat. Note: `web/src/screens/greenlight.tsx` is the *marketing mock* (hardcoded "86% auto-approved"); the live demo must drive the wired `GreenlightQueue.tsx`.

## Options

**A. Fatten the existing inline seed** — hand-edit `seed_demo_tenant.py` to ~40/120/60 as Python literals. *Effort:* ~0.5–1 day. *Cost:* $0. *Risk:* a 1,500-line literal file nobody reviews; still no `documents`/`saved_views` unless added; narrative quality decays as you grind out 480 activity strings by hand; every demo-narrative tweak is a code diff.

**B. Spec-driven generator + hand-authored hero arcs (recommended)** — a standalone, stdlib-only, seeded generator emits `seed_data/demo_tenant.json` (committed: small, zero PII, byte-identical for both lanes + CI); the loader is a data-driven evolution of `seed_demo_tenant.py` that keeps the `crm_app`/`SET app.current_tenant`/wipe-then-insert pattern and **adds** `documents` (embedded via the ingest embedder seam, `ingest/run_sync.py build_embedder`) and `saved_views`. ~8 hero arcs are hand-authored literals inside the generator; the ~30-company tail is template-generated. *Effort:* ~1.5–2 days for Lane Matt (app code), one one-off ECS run for Lane Nick (`uplift-migrate-oneoff` family, `infra/RUNBOOK.md:76`; live mutation is Lane Nick only per CLAUDE.md constraint 1). *Cost:* Titan V2 embedding of ~600 short docs ≈ well under $0.01 (verify pricing at run time). *Risk:* low — deterministic, idempotent, re-runnable to reset between demos.

**C. Demo data through the real ingest pipeline** — build fixture connectors behind the seam in `ingest/run_sync.py` and let `sync_tenant` (`ingest/pipeline.py:96`) populate `documents` with cursors, dedupe, the works. Most honest end-to-end story ("this is literally the HubSpot path"). *Effort:* 3–4 days. *Risk:* medium — couples demo narrative to connector record shapes, slower iteration on story content, and you still need the CRM-row seed anyway.

## Recommendation

**Option B.** At 40/120/60 scale, narrative quality is the product being demoed — generated filler makes RAG answers mush, and pure hand-authoring doesn't scale past the hero arcs. Hybrid gets both: 8 arcs the demo script actually touches are written by a human (or one good LLM pass, reviewed), the tail exists to make dashboards and pipeline counts look like a real business.

### The fabricated company

**Demo tenant: "Meridian Mechanical Group"** — a fabricated Austin commercial HVAC/plumbing services firm. Why this vertical: it *is* the ICP Matt is selling into (mid-market service business, the Kyle wedge), every prospect intuitively understands it, and it naturally produces all three side-effecting tools (quotes, deal moves, follow-up emails), renewals, emergencies, and site-visit narratives. The spec parameterizes the segment table so a real-estate variant (the Dylan POC) is one config-block swap.

### Fabrication discipline (non-negotiable, per the ghl-test-data standard)

- **Emails:** `firstname@<companyslug>.example` — the `.example` TLD is RFC 2606-reserved and can never resolve; undeliverable by construction. (This also *fixes* the current seed's `birchwoodcap.com`-class problem.)
- **Phones:** `+1 {512,737,210,830,254,361} 555 01XX` — 555-0100–0199 is the NANP fictitious block; 6 area codes × 100 numbers covers 120 contacts + 40 company mains with zero reuse.
- **Names:** seeded sampling from curated pools; common-combination synthetic. The deliverability boundary is domains/phones, same as the ghl dataset.
- **Brands/competitors fabricated:** competitor "Apex Air Systems"; equipment like "NorthCool C-450 chiller", "Kestrel KX-90 RTU". No real vendors.
- **`ref_id` = `demo:<entity>:<n>`** on every row — idempotent upsert key and an unmissable synthetic marker.
- Free text is templated/authored only; nothing derived from any real customer corpus.

### Dataset spec

| Table | Count | Shape |
|---|---|---|
| companies | 40 | 7 segments: property mgmt 9, industrial/logistics 7, healthcare 6, hospitality/restaurant 5, office/REIT 5, education 4, municipal/nonprofit 4. `name`, `domain=<slug>.example` |
| contacts | 120 | 1–6 per company (hero companies get 4–6). Roles: Facilities Director, Chief Engineer, Director of Ops, Property Manager, CFO/Controller, Procurement |
| deals | 60 | Funnel: new 14 · qualified 12 · proposal 11 · negotiation 8 · closed_won 9 · closed_lost 6 (keep the seed's stage strings; add `closed_lost`). Amounts: repairs $4.5–18K, service agreements $18–72K, installs/retrofits $80–480K; open pipeline ≈ $2.1M |
| activities | ~480 | Per-deal by stage: new 2–3, qualified 4–6, proposal 6–9, negotiation 8–12, closed 10–14. Kinds `call/email/note/meeting`. **Explicit `occurred_at`** backdated over the trailing 150 days, US-Central business hours |
| approvals | 6 pending + 8 decided | Pending set below; 8 backdated decided rows (6 approved, 2 denied with `deny_message`, `decided_by`/`decided_at` set) so the queue's history isn't empty |
| saved_views | 2 | `pipeline-health` (KPI: open pipeline $; chart: deals by stage; table: top 10 open) and `renewals-next-90d`. Specs must validate against `shared/schemas/view_spec.schema.json` AND the live Cube member catalog — implementer must pull member names from the deployed Cube model, not invent them |
| documents | ~600 | Every activity body mirrored (kind `call`→source `call`, `email`→`email`, `note/meeting`→`upload` — stays inside the schema's commented vocabulary, `db/schema.sql:17`) + 8–10 longer authored docs (300–600-word site-visit reports, proposal summaries, QBR notes) — these are what make RAG answers rich. Embedded at load time via the ingest embedder seam |

**The 8 hero arcs** (hand-authored, each with retrievable story beats — names, numbers, objections, dates):

1. **Westlake Galleria chiller retrofit** — Pinnacle Property Partners, Rosa Camarillo (Facilities Dir). $284K, negotiation. Chiller #2 failure → emergency repair → retrofit proposal → board approval pending → net-45 vs net-30 → COI request outstanding.
2. **Hill Country ISD service-agreement renewal** — $48K/yr, at risk; agent proposes 8% loyalty discount.
3. **Cedar Park Surgical Center install** — $132K; compliance-documentation subplot; counter on scope.
4. **Brazos Logistics PM contract** — $36K; ops manager demands 12% discount.
5. **Lantana Hospitality Group** — small repair upsell; $850 follow-up email.
6. **Mueller Commons REIT churn risk** — 3 missed QBRs, Apex Air quoted 15% under; win-back with $1,200 service credit.
7. **Travis Heights Medical Plaza** — closed_won renewal + kickoff (the reference-customer answer).
8. **Coppell Distribution Center** — closed_lost to Apex on price (the honest "why did we lose X" answer).

**The 6 pending approvals — designed against the L2 thresholds ($1,000 / 10%):**

| # | Tool | Tied to | value_at_stake | Why it's there |
|---|---|---|---|---|
| 1 | `send_email` | Lantana follow-up | **$850** | Under $1,000 → would flip to AUTO at L2: *the dial demo* |
| 2 | `send_email` | Hill Country ISD renewal, 8% discount | $48,000 | Over value ceiling → still queues at L2 |
| 3 | `update_deal` | Cedar Park proposal→negotiation | $132,000 | Big-value stage move with agent reasoning |
| 4 | `issue_quote` | Brazos PM contract, **12% discount** | $36,000 | Trips the discount guard independently of value |
| 5 | `send_email` | Mueller win-back + $1,200 credit | **$1,200** | Just over the $1,000 boundary — shows the line is real |
| 6 | `update_deal` | Westlake negotiation→closed_won | $284,000 | The one you EDIT or DENY live |

Every `reasoning` field cites its arc's activity narrative, so the queue reads as traceable agent judgment, not lorem ipsum.

### Generation approach

- **Generator:** one stdlib-only Python script (`random.seed(47)`, no faker dependency — keeps the repo's zero-new-deps discipline), hero arcs as literal data, tail from templates with slot-filled specifics. Emits `seed_data/demo_tenant.json` (committed).
- **Loader:** evolve `scripts/seed_demo_tenant.py` to read the JSON (env `DEMO_DATA_PATH`), preserving the `crm_app` + `SET app.current_tenant` + wipe-then-insert idempotency, adding `documents` (embed via `ingest/run_sync.py build_embedder` in real mode; stub embedder offline so tests stay $0/offline) and `saved_views`, and setting `occurred_at`/`created_at` explicitly. Wipe list gains `documents` scoped to `ref_id LIKE 'demo:%'`.
- **Ownership/run:** Lane Matt authors (app code); Lane Nick runs as the one-off ECS task with `TENANT_ID` (RUNBOOK pattern). Re-run before every demo to reset state.

### The live demo script (~12 minutes)

*Pre-flight:* re-run the seed (resets approvals to 6 pending); confirm `/chat` isn't 503 (MA worker live — currently blocked on the Console env key per CLAUDE.md, see flip conditions); sign in via Cognito Hosted UI with `uplift/demo-user` creds.

1. **Dashboard** — open the seeded *Pipeline health* view. Talking point: agents emit a declarative spec validated against a schema + Cube catalog — spec-not-code, no injected UI (`shared/view_spec.py`).
2. **Chat, knowledge:** *"What's blocking the Westlake Galleria retrofit?"* → grounded answer citing the COI request and net-30 negotiation, with clickable citations. Talking point: claims cite or die — uncited claims are dropped, by construction (`conv/rag.py`).
3. **Chat, action:** *"Draft a follow-up to Rosa Camarillo recapping the site visit and asking for the COI."* → a pending approval appears; nothing sent. Talking point: side-effecting tools structurally cannot execute without a human (draft-only).
4. **Greenlight queue** — walk the 6 items: reasoning, value at stake. **Edit** the Westlake draft inline, approve the edited version; **deny** the 12% Brazos quote with a message ("max 10% without VP sign-off") — the deny reason flows back to the agent.
5. **The autonomy dial** — tenant sits at L1 (everything asks). Show L2: the $850 Lantana follow-up would auto-execute; the $48K renewal and the 12% discount still queue — value and discount guards are independent. This is the slide GHL doesn't have.
6. **The honesty close:** *"What did Coppell say about Apex's pricing at the depot walkthrough?"* (not in corpus) → *"I don't have grounded sources to answer that"* (`conv/rag.py:193`). Contrast with the confident-hallucination failure mode of competitor AI.

## What would flip this

- **If the first design-partner demos are "show me MY data"** (HubSpot import-led), cut the deep synthetic narrative and spend the effort on Option C's fixture connectors instead — the synthetic tenant then only needs ~10 deals of dressing.
- **If the MA worker stays blocked** (Console env key; `/chat` 503 per CLAUDE.md), beats 2–3 and 6 can't run live — Greenlight + dashboard still demo, but the `documents` corpus is parked value. If that blocker looks like lasting past the first scheduled demo, ship the CRM/Greenlight slice of the spec first and hold the corpus work.
- **If demo-tenant provisioning becomes product surface** (a "load sample data" button in onboarding), the loader should move from a one-off script into `signup/provisioning.py` — different design review.

## Sources

- Repo (read directly): `scripts/seed_demo_tenant.py` (:23–105), `db/schema.sql` (:14–26, :31–104, :71), `conv/rag.py` (:87–142, :193), `api/control/autonomy.py` (:14–17, :56–69), `api/control/greenlight.py` (:170–190), `api/app.py` (:97–158), `agents/tools/sideeffecting.py` + `readonly.py`, `agents/runtime.py`, `conv/session.py`, `ingest/pipeline.py` + `ingest/run_sync.py`, `web/src/api/{GreenlightQueue,ChatDock,client}.ts(x)`, `web/src/screens/greenlight.tsx` (mock), `infra/RUNBOOK.md:76`, `CLAUDE.md`, `BUILD_STATUS.md:34`.
- Fabrication discipline: project memory `ghl_test_dataset_and_compare.md` (2026-06-01 — RFC-reserved domains, `555-01xx` fiction block, zero-collision verification method).
- RFC 2606 (reserved TLDs incl. `.example`); NANP 555-0100–0199 fictitious number range.
- No external Anthropic-API claims are made in this brief; all agent-plane facts are grounded in repo files (`shared/config.py:12` for the `managed-agents-2026-04-01` beta header). The claude-api skill was therefore not needed.



## Critic-noted gaps (non-blocking)
- All six claims verified against the repo: seed counts (4/6/6/5/3) and saved_views delete-with-no-insert (seed lines 30-31, 90-105); documents never seeded while conv/rag.py drops uncited claims; autonomy thresholds $1,000 / 10% at autonomy.py:16-17; activities.occurred_at DEFAULT now() at schema.sql:71 with the seed omitting it; birchwoodcap.com / halcyonlogistics.io domains; side-effecting tools send_email/update_deal/issue_quote (+draft_email) and Greenlight approve/edit/deny at greenlight.py:170-190.
- Unconsidered cost: documents is a pgvector store locked to Titan V2 1024-dim embeddings (schema.sql:12-26), so a 'seeded stdlib-only generator' cannot by itself produce a searchable RAG corpus — seeding documents requires either a Titan embed step at load time (Bedrock call, in-VPC) or a deterministic stub embedder, and the brief's plan should budget for that.
- Minor claim imprecision (immaterial): the current seed's phones already follow the 555-01xx fictitious pattern — only the domains violate the fabrication discipline.
