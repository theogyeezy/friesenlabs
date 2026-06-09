# Brief: Phase 6 — The Conversational Layer (slot resolution, agentic RAG + citations, analytics)

## Goal
The Moveworks-style front door: one NL entry point where a user asks, acts, and resolves in one thread
— grounded answers with citations, multi-step work, approvals inline. Most of this is wiring Parts
VI–VII together; the **two genuinely new builds** are **slot resolution** and **citation assembly** —
budget for those. The HTTP front door (SSE, auth) is Phase 9; build the reusable logic now, offline.

## Owner / directory
Background agent owns a new top-level **`conv/`** package (+ tests under `tests/`). Do NOT edit
`infra/`, `web/`, `db/`, `api/`, `agents/`, `semantic/`, `ingest/`. Do not run git. Offline only — no
real Anthropic/AWS; inject all clients (cube, db, rag, an LLM "disambiguator"/"synthesizer") and use
fakes in tests.

## Files (in `conv/`)
- `conv/__init__.py`
- `conv/slots.py` — the NL-to-governed-call bridge (Build Guide Step 36). `resolve_slots(text, ctx)`
  turns human references into system IDs/values:
  - "Acme account" → `company_id` (CRM lookup), a contact name → `contact_id`,
  - "last quarter"/"this month" → a concrete date range (deterministic date math; pass `today` in via
    ctx so it's testable — do NOT call the clock directly),
  - "Riverside" → a Cube dimension value (e.g. region) via the cube client's dimension catalog.
  - On multiple matches, return a `Disambiguation` (candidates + a prompt) instead of guessing; an
    injected `disambiguator` may pick when confidence is high. Never silently choose.
  - Tenant-scoped: every lookup goes through the injected tenant-bound clients (RLS/Cube context).
- `conv/rag.py` — agentic RAG with citations (Build Guide Step 37):
  - `answer(question, ctx)`: hybrid retrieval (injected `rag.search` over pgvector + injected
    `crm.read`) IN PARALLEL conceptually (just call both), then `synthesize` (injected LLM fake)
    returns claims, then **citation assembly**: map each claim → the retrieved chunk(s) that backed it
    and return `{answer, citations:[{claim, source_ref, snippet}]}`. Permission-awareness is automatic
    via the tenant-scoped clients (don't re-filter by hand).
  - Make citation assembly the tested centerpiece: every claim in the answer must carry ≥1 source_ref
    that exists in the retrieved set; an uncited claim is dropped or flagged (your choice — test it).
- `conv/analytics.py` — `record(event)` persists interaction events (utterance, tool_call, approval,
  click) to an injected analytics store (in-memory fake); tenant-scoped. Sourced from the same event
  stream as traces.
- `conv/session.py` — a thin `Conversation` facade: opens one session via `agents.runtime.get_runtime`
  (FakeRuntime offline), forwards a user message, returns the structured turn (answer + citations +
  any pending approvals). This is the seam the Phase 9 HTTP front door will call. One MA session per
  conversation; thin client.
- `conv/README.md`.

## Tests (offline, no AWS/Anthropic)
- `tests/unit/test_slots.py` — date phrases → correct ranges (with injected `today`); "Acme" →
  company_id via fake CRM; multiple matches → Disambiguation (no silent guess); unknown ref → empty/None.
- `tests/unit/test_rag_citations.py` — every claim in the answer carries a valid source_ref from the
  retrieved set; an answer with an unsupported claim drops/flags it; tenant-scoped clients are used.
- `tests/unit/test_analytics.py` — events persist tenant-scoped; types round-trip.
- `tests/integration/test_conversation_turn.py` — `Conversation` over FakeRuntime: a knowledge question
  returns a cited answer for the right tenant; an action question surfaces a pending approval (reuse the
  Phase 4 tools + Phase 5 Greenlight via injection — import them, don't reimplement).

## Constraints
- No real Anthropic/AWS; everything injected; no secrets; no git.
- Slot resolution NEVER silently guesses across ambiguous matches. Citation assembly NEVER returns an
  uncited claim as grounded. Both are tenant-scoped through injected clients.
- Reuse, don't duplicate: import `agents.runtime`, `agents.tools.*`, `api.control.greenlight` rather
  than re-implementing them.

## Done when
`conv/` implements slot resolution + agentic RAG with citation assembly + analytics + the conversation
facade; all new tests pass offline (`/Users/yee/Desktop/friesenlabs/.venv/bin/pytest -q` from repo
root); `python -c "import conv.session"` needs no AWS/Anthropic. Report files, verbatim pytest tail,
and anything flagged "verify".
