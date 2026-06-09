# Brief: Phase 5 — The Control Plane (the moat)

## Goal
One uniform pipeline every agent action flows through, so control is uniform, auditable, and
human-governed: **propose → validate (compliance + policy) → autonomy check → [Greenlight if needed]
→ execute → trace**. Read-only actions skip Greenlight but are still validated + traced. This is the
thing GoHighLevel structurally can't do. Build it in `api/` (the control plane); Phase 4's
side-effecting tools call into it rather than acting directly.

## Owner / directory
Orchestrator. New code in `api/` (control-plane logic) + `shared/` (event/trace types if needed).
Builds on `agents/tools/base.py` (the always_ask → proposal seam already exists). Do not edit
`web/`, `ingest/`, `semantic/`. Stays offline/mocked (no live AWS/Anthropic).

## Files
- `api/control/gate.py` — `ActionGate.run(action, ctx)` implementing the pipeline in order:
  1. **compliance.validate** — a hard fail never reaches Greenlight (raises/returns blocked).
  2. **autonomy check** — resolve the (agent, tenant) level L0–L3 and decide auto vs approve.
  3. **Greenlight** — if approval needed, persist a proposal and return `pending_approval`.
  4. **execute** — only when allowed (calls the injected executor; never real sends in tests).
  5. **trace** — always append a decision trace (even for read-only + blocked).
- `api/control/autonomy.py` — L0–L3 (Build Guide Step 29):
  - L0 suggest-only (everything → Greenlight); L1 ask-first (side-effecting → Greenlight);
  - L2 act-within-limits (auto under thresholds e.g. discount < 10%, value < $X; above → approval);
  - L3 fully autonomous (acts + reports; only flagged/exception cases pause).
  A `resolve(agent, tenant)` reads config; `decide(level, action, value_at_stake) -> AUTO|APPROVE`.
- `api/control/greenlight.py` — the HITL queue over the `approvals` table: `propose(...)`,
  `list_pending(tenant)`, `decide(id, "approve"|"edit"|"deny", edits=…, decided_by=…)`. Conforms to
  the `Greenlight` protocol in `agents/tools/base.py`. Maps to the MA tool-confirmation reply
  (`user.tool_confirmation` allow/deny) — author that mapping, flag "verify" (beta), don't call live.
- `api/control/compliance.py` — deterministic checks (TCPA quiet-hours/consent for SMS, CAN-SPAM
  unsubscribe) + a hook for an LLM critic pass (injected, mocked in tests). Hard fail → blocked.
- `api/control/traces.py` — append per-step records (agent, tool, minimized inputs, summarized
  outputs, reasoning, ts, tokens) to the `traces` table (injected store; in-memory fake for tests).
- `api/control/killswitch.py` — per-tenant + global pause flag the gate checks before any execute;
  flipping it blocks new actions (and, live, interrupts sessions via `user.interrupt` — flag verify).
- `api/__init__.py`, `api/control/__init__.py`, `api/README.md`.

## Tests (offline, no AWS/Anthropic)
- `tests/unit/test_autonomy.py` — L0 always approves; L1 side-effecting approves, read-only autos;
  L2 autos under threshold and approves above; L3 autos except flagged.
- `tests/unit/test_gate.py` — full pipeline ordering: a compliance hard-fail never reaches Greenlight;
  an approved action executes once; a denied action never executes; every path writes exactly one trace.
- `tests/unit/test_greenlight_queue.py` — propose → list_pending → approve/edit/deny transitions;
  edited draft is what would be executed; denied carries the deny message.
- `tests/unit/test_killswitch.py` — when paused (tenant or global), the gate blocks execute even for an
  otherwise-auto action; unpausing restores flow.
- `tests/unit/test_compliance.py` — TCPA quiet-hours blocks an SMS; CAN-SPAM missing-unsubscribe blocks
  an email; compliant actions pass.
- Re-run `scripts/isolation_test.py` semantics: approvals/traces are tenant-scoped (they already carry
  tenant_id + RLS in db/schema.sql).

## Constraints
- No live sends/mutations ever execute in tests (injected executor/sender fakes; assert never called on
  deny/block). MA confirmation reply + session interrupt are authored + flagged "verify", not called.
- Keep the gate the SINGLE path; Phase 4 tools should propose through it (wire `send_email` et al. to
  call the gate's Greenlight, or document the seam if left for Phase 6 wiring).

## Done when
The gate enforces propose→validate→autonomy→Greenlight→execute→trace; L0–L3 behave per spec; a draft
pauses at Greenlight with reasoning + value-at-stake; an L2 action autos under a threshold and pauses
above; the kill switch halts execution; every step writes a trace; all new unit tests pass offline;
BUILD_STATUS Phase 5 updated.
