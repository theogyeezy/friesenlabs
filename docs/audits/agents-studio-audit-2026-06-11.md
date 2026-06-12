# Agents & Agent Studio — customer-readiness audit (2026-06-11)

**Method:** 4 parallel read-only passes (backend agent plane · web UI · tests/CI · data-layer +
infra wiring), claims cross-checked between passes and spot-checked against source. 252 tests
green locally across the surface (202 unit + 50 integration; the 2 skips are the real-Postgres
RLS proof, which runs in CI's pgvector job). No code was modified during the audit.

**Scope:** `api/routes_studio.py`, `api/agents_routes.py`, `agents/playbooks/*` (store /
activation / runner / dispatch / templates), `agents/roster`, `agents/runtime.py` +
`runtime_selfhosted.py`, `agents/tools/*`, `worker/worker.py`, web `AgentsRoster` / `StudioView`
/ `MarketplaceView` + the mock-mode `screens/{studio,agents,agent-market}.tsx`, `db/schema.sql`
+ `db/roles.sql`, `infra/modules/scheduled_jobs`, module entitlements (`shared/modules.py`).

---

## What is genuinely solid

- **Safety invariants hold at every layer.** Draft-only is structural (`Tool.invoke` base
  class), not conventional; the trust rule (tenant from the verified JWT claim only) is uniform
  with no body/header override path; tenant smuggling is explicitly tested on every studio
  route. Greenlight gating of side-effecting tools is proven at six layers (tool policy → gate
  → runtime → playbook runner → run route → approval-side record-only).
- **Data layer follows every house convention.** `playbooks` has mandatory `tenant_id`,
  DO-block + explicit ENABLE/FORCE RLS, the `tenant_isolation` GUC policy, an explicit
  fresh-load GRANT (`db/roles.sql:86-91`), per-op `SET LOCAL app.current_tenant` in
  `PgPlaybookStore`, and is auto-discovered by the schema-derived gate
  (`tests/unit/test_sql_schema.py`). Migrated live.
- **Wiring is real as of #236.** Both routers mount unconditionally with honest-503
  degradation; `api/asgi.py:344-364` wires a live `StudioDeps.registrar_factory` that resolves
  the tenant's persisted MA environment — activate/run drive a real Managed Agents crew.
  (TODO.md's "registrar is hardcoded None in prod" bullets were stale; corrected this pass.)
- **The real-mode web views are honestly API-wired.** `AgentsRoster` (GET /agents),
  `StudioView` (full playbook CRUD + activate/deactivate + template instantiate, client-side
  pre-flight + server 422 surfacing), `MarketplaceView` (browse + hire over /studio/templates).
  No FLStore/mock data leaks into real mode; loading/empty/rollout/error states present.
- **CI runs all of it** — unit + integration (with a real non-owner-role Postgres) + the
  Playwright projects. Nothing on this surface is excluded.

**User journeys:** view roster — REAL · create playbook — REAL · activate — REAL ·
manually run — **FAKE (no UI entry point)** · browse/hire from marketplace — REAL.

---

## Findings

### P0 — blocks an honest customer release

1. **Every customer-visible email draft is a hardcoded placeholder.**
   `DraftEmail._execute` returns `{"body": f"(draft) Re: {goal}"}`
   (`agents/tools/sideeffecting.py:24`). It is `Policy.AUTO`, so nadia/echo (live roster) and
   the `lead_followup_drafter` template surface the literal canned string as the draft, with
   no Greenlight review in between. The feature reads as broken, not missing.

2. **Activate is a dead end in the UI: no "Run now", no run history.**
   The backend route exists and is draft-only-correct (`POST /studio/playbooks/{id}/run`,
   `api/routes_studio.py:273-320`, shipped in #226), but `StudioView.tsx` exposes no Run
   button and no runs surface. Compounding it, `RunRecord` — whose own docstring promises "the
   audit digest a scheduler/worker persists" (`agents/playbooks/runner.py:66`) — is persisted
   nowhere: there is no `playbook_runs` table; `dispatch.main()` only logs to CloudWatch and
   the manual route only returns it in the HTTP response. A customer can never see whether a
   playbook ran or what it proposed.

3. **Every run re-creates the MA crew — an unbounded resource leak.**
   `PlaybookRunner.run()` calls `activate_playbook(...)` unconditionally
   (`agents/playbooks/runner.py:221`), creating fresh MA agents + a coordinator per invocation
   (manual click today; every scheduler tick once enabled — O(runs × roster) orphaned MA
   resources). The MA ids returned at explicit activation are never persisted (no columns on
   `playbooks`), so the runner has nothing to reuse.

4. **None of the 5 starter playbooks can ever fire automatically.**
   4 of 5 are schedule-triggered: the EventBridge dispatch leg is applied-DISABLED with an
   empty static tenant list (`infra/variables.tf:364-374`; neither flag in prod tfvars) — the
   flip itself is owner-gated and already tracked (GO_LIVE_CHECKLIST.md:70-72). The 5th
   (`lead_followup_drafter`) is event-triggered and the event leg is **unbuilt, not gated**:
   `dispatch_event` has zero production callers (`agents/playbooks/dispatch.py:14` admits
   "producer wiring at each domain-event site is a follow-up"); `POST /public/leads` never
   emits `lead.created`. The Studio happily shows these playbooks as Active with no warning.

### P1 — soon after

5. **Scheduled-dispatch fan-out is a hand-maintained tfvar.** `PLAYBOOK_DISPATCH_TENANTS` is a
   static comma list; every new signup needs a terraform edit + apply or their schedules
   silently never fire (exit 0, "nothing to do"). Not sellable at signup scale — the
   dispatcher should discover tenants with active schedule-playbooks from the DB.
6. **Module gating is UI-only.** `studio`/`marketplace`/`agents` sit under the $39/mo agents
   module (`shared/modules.py:40`) but `api/routes_studio.py` never checks entitlements — a
   tenant with the module toggled off (unpaid, under Phase-2 module billing) can drive
   `/studio/*` and `/agents` directly. Billing leakage once module billing activates.
7. **vault_id is plumbed nowhere.** `PgWorkspaceStore.upsert` accepts it, but
   `tenant_workspaces` has no such column and `AgentPlaneEnsure.ensure()` never returns one —
   every session runs `vault_id=None`, so the workspace-vault isolation boundary described in
   the tenancy model is not active. Wire it or document the deferral.
8. **StudioView has zero Playwright coverage.** Marketplace and Agents screens both have
   specs; the flagship builder screen has none — a wiring regression would ship silently.
9. **The schedule path has never executed anywhere.** Cron-matching is unit-tested with fakes,
   but `python -m agents.playbooks.dispatch --schedule` against a real `PgPlaybookStore` +
   runtime has no integration test, and the prod rule is DISABLED — test or live.
10. **MarketplaceView 404 parity.** Only 503 is special-cased
    (`web/src/api/MarketplaceView.tsx:74`); a 404 falls to a generic error instead of the
    "rolling out" copy StudioView uses.
11. **Worker `TOOLS` / registry drift.** `UpdateContact`/`CreateActivity`/`CreateDeal` are
    registered with live appliers but granted nowhere and unserved by `worker.TOOLS`
    (`worker/worker.py:56-60`) — a Studio-granted call would wedge the session. Grant+serve or
    remove; consider deriving TOOLS from the registry. _(Also flagged by the Greenlight audit,
    whose TODO backlog has not landed on main — filed here so it can't fall through.)_
12. **Approved sends read as real sends.** `send_email`/`issue_quote` appliers permanently
    return `performed:false` "draft-only until provider go-live"
    (`api/control/appliers.py:82-92`) while the UI toast says sent. Surface the `performed`
    flag; don't offer Approve as if it delivers. _(Same Greenlight-audit provenance as above.)_

### P2 — hygiene

13. **Live isolation gate doesn't probe `playbooks`** — `scripts/isolation_test.py` covers
    documents + the contacts FK only; playbooks RLS is proven statically and in CI Postgres,
    never against the live DB.
14. **No smoke script** for the studio/agents surface under `scripts/smoke/`.
15. **Mock-mode demo honesty** (demo-only, no customer impact; part of the deferred
    demo-honesty work): `screens/agents.tsx` "Add tool" / "Get more skills…" are toast-only
    dead ends; paid skills' "Get · $X" button installs free with no payment path
    (`screens/studio.tsx:88`); unguarded `await askClaude(...)` (`screens/studio.tsx:218`);
    autonomy/status toggles are local-state-only and reset on navigation.

### Already tracked elsewhere — deliberately not re-filed

- The owner-gated `playbook_dispatch_enabled` flip + tenant seeding, and the missing
  `FailedInvocations` alarm on the dispatch rule — GO_LIVE_CHECKLIST.md:70-72, 122.
- Workspace-key-pool seeding (real provisioning parks `pool_empty`).

### Stale TODO.md items corrected this pass

- "StudioDeps.registrar is hardcoded None everywhere in prod" (+ the registrar-wiring bullet)
  — fixed by #236 (`api/asgi.py:344-364`); only the live activation smoke remains open.
- "Agent marketplace in real mode — stub" — built in #233 (`MarketplaceView`).

---

## Release verdict

**Ship-able today as "configurable agents + manually-runnable playbooks"; not yet honest as
"automation."** The roster, template marketplace, and playbook CRUD/activate flows are real
end-to-end with the safety story (draft-only, trust rule, RLS) genuinely proven. But a paying
customer would meet four hard walls: their email drafts are a literal placeholder string; there
is no way to run a playbook from the UI (and no record anywhere of what ran); nothing fires on
schedule or event — including all five starter templates; and every run that does happen leaks
MA agents + a coordinator into the tenant workspace. Minimum bar for release is the four P0
items; P1 items 5-7 should land before the agents module is sold as a paid add-on.
