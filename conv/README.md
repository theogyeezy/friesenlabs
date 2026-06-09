# conv — the conversational layer (Phase 6)

The Moveworks-style front door: one NL entry point where a user asks, acts, and resolves in one
thread — grounded answers with citations, multi-step work, approvals inline. This package is the
**reusable, offline logic**. The HTTP front door (SSE, auth) is Phase 9 and will call `conv.session`.

Everything is **offline and injected**: no real Anthropic/AWS, no secrets. `import conv.session`
needs no cloud. The agent plane is reached only through `agents.runtime.get_runtime` (FakeRuntime by
default); every client (cube, db/crm, rag, the LLM disambiguator/synthesizer, greenlight, analytics)
is passed in; tests use fakes.

## Modules

| Module | What it does |
|---|---|
| `slots.py` | `resolve_slots(text, ctx)` — NL references → governed IDs/values. |
| `rag.py` | `answer(question, ctx)` — agentic RAG + **citation assembly**. |
| `analytics.py` | `Analytics.record(event)` — interaction events to a tenant-scoped store. |
| `session.py` | `Conversation` — the thin facade Phase 9 will call (one MA session per conversation). |

### Slot resolution (`slots.py`) — Step 36

Turns human references into the values a governed call needs, **never silently guessing**:

- `"Acme account"` → `company_id` (injected tenant-scoped CRM lookup)
- a contact name → `contact_id`
- `"last quarter"` / `"this month"` → a concrete `{start, end}` range — **deterministic**: `today`
  is passed in via `SlotContext`; the clock is never read.
- `"Riverside"` → a Cube dimension value (e.g. `region`) via the cube client's dimension catalog.

On **>1 match** it returns a `Disambiguation` (candidates + prompt) instead of choosing. An injected
`disambiguator` may pick **only** when it returns confidence ≥ threshold; otherwise the human chooses.
Unknown references come back `unresolved` (never invented). Every lookup is tenant-scoped through the
injected, tenant-bound clients.

### Agentic RAG + citations (`rag.py`) — Step 37

`answer(question, ctx)`: hybrid retrieval (`rag.search` over pgvector **and** `crm.read`, both
tenant-scoped) → `synthesize` (injected LLM fake) returns claims → **citation assembly** maps each
claim to the retrieved chunk(s) that back it.

**Invariant (tested centerpiece):** every claim that survives into the grounded answer carries ≥1
`source_ref` that **exists in the retrieved set**. A claim with no valid ref is **dropped** (default,
recorded in `dropped`) or **flagged ungrounded** (`flag_uncited=True`). An uncited claim is never
returned as grounded, and never leaks into the prose. Permission-awareness is automatic via the
tenant-scoped clients — results are not re-filtered by hand.

### Analytics (`analytics.py`) — Step 38

`Analytics.record(Event(...))` persists interaction events — `utterance`, `tool_call`, `approval`,
`click` — to an injected store (in-memory fake offline; Aurora + RLS in prod). Sourced from the same
event stream as agent traces. Reads are strictly tenant-scoped.

### Conversation facade (`session.py`) — Step 38

`Conversation` opens one session via `get_runtime` (FakeRuntime offline) and forwards a user message,
returning a structured `Turn`: `answer`, `citations`, `pending_approvals`, `slots`, `delegations`.

It **reuses, never reimplements**:
- Phase 4 tools (`agents.tools.sideeffecting.*`) — an action utterance invokes a side-effecting tool
  which, by the base-class guarantee, routes a **proposal to Greenlight without performing the side
  effect** → a pending approval surfaces in the turn.
- Phase 5 Greenlight (`api.control.greenlight.Greenlight`) — injected as the approval queue.

## Tests

- `tests/unit/test_slots.py` — date phrases → ranges (injected `today`); name → id; ambiguity →
  `Disambiguation` (no silent guess); unknown → unresolved.
- `tests/unit/test_rag_citations.py` — every claim cites a real retrieved ref; unsupported claims are
  dropped/flagged; tenant-scoped clients are used.
- `tests/unit/test_analytics.py` — events persist tenant-scoped; types round-trip.
- `tests/integration/test_conversation_turn.py` — `Conversation` over FakeRuntime: a knowledge
  question returns a cited answer for the right tenant; an action question surfaces a pending approval
  (reusing Phase 4 tools + Phase 5 Greenlight by injection).

Run: `/.venv/bin/pytest -q` from the repo root.
