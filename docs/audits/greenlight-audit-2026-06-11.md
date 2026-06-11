# Greenlight customer-readiness audit — 2026-06-11

Four parallel read-only audit passes (backend core · agent-plane integration · web UI ·
persistence+tests), load-bearing claims spot-checked against source before inclusion.
Branch: `feat/matt-greenlight-audit` (from `main` @ 393e698). TODOs filed in `TODO.md`
§ "Greenlight customer-readiness audit".

## Verdict

**The core is sound and safe to put in front of customers; the operational shell around it
is not finished.** The draft-only guarantee, tenant isolation, and decide-race atomicity are
verified in code and tests. What's missing is the operational hygiene a paying customer hits
in week two: approval expiry, list pagination + a DB index, approval discovery in the UI
(badge/polling), and honest UI handling of draft-only and already-decided outcomes.

## Verified working (evidence-backed)

- **Draft-only guarantee is structural, not configurational.** `Tool.invoke` base class
  (`agents/tools/base.py:80-102`) routes every `Policy.ALWAYS_ASK` tool to Greenlight and
  returns `pending_approval` — no code path executes a side-effecting tool directly, on the
  worker, MA, or self-hosted runtime. `send_email`/`issue_quote` appliers are `record_only`
  (`api/control/appliers.py:82-92` → `{"performed": false, "reason": "draft-only until
  provider go-live"}`).
- **Decide is race-free and idempotent.** Conditional `UPDATE … WHERE status='pending'`;
  race loser gets rowcount 0 → 400; applier runs exactly once
  (`tests/integration/test_greenlight_apply_on_approve.py` M1–M3 tests, incl. audit-write-failure
  honesty and kill-switch-blocks-approve-but-keeps-pending).
- **Tenant isolation, defense in depth.** JWT `custom:tenant_id` is the only tenant source
  (`api/auth.py`); every Pg store op runs inside `SET LOCAL app.current_tenant` (`PgApprovalStore._tx`,
  `api/control/greenlight.py:132-150`); RLS is ENABLE+FORCE on `approvals` and `traces`
  (`db/schema.sql:360-361`); `crm_app` is non-owner/NOBYPASSRLS; routes re-check tenant post-read.
- **Kill switch + autonomy dial are persisted and multi-instance visible** (`tenant_settings`,
  TTL-cached, read-your-own-write invalidation; `tests/integration/test_control_rls.py`). Global
  kill switch is operator-allowlisted via `CONTROL_GLOBAL_OPERATOR_TENANTS`.
- **Edit guard**: edits may only touch existing payload keys, never `action` (422 otherwise);
  payload `action` key cannot override the registry name.
- **Traces**: append-only (REVOKE DELETE/UPDATE), keyset pagination, 200-char input/output cap.
- **Worker roster parity** enforced by `tests/unit/test_worker_roster_parity.py`
  (granted == served exactly).
- **Web**: queue loads/edits/decides with honest loading/error/empty states; kill switch +
  autonomy dial + traces in `/security` with 404 "not yet enabled" feature-detection; e2e specs
  cover approve, edit+approve, dial change, kill switch, 404 degrades.

## Gaps (release-relevant, all verified)

### P0 — fix before paying customers

1. **No approval expiry.** Schema comments name an `expired` status (`db/schema.sql:99`) but
   nothing implements it — no `expires_at` column, no age check in `decide()`, no sweeper.
   Stale approvals are approvable forever (an email drafted in January can fire in June).
2. **`GET /approvals` is unpaginated** (`api/app.py:234-236`; `list_pending` returns the bare
   list). Unbounded response; pair with the missing index below.
3. **No index for the pending-queue query.** No index on `approvals` at all in `db/schema.sql`;
   `list_pending` scans. Add `(tenant_id, status, created_at)` (partial on `pending`).
4. **Approvals are undiscoverable in real mode.** Nav badge is demo-only
   (`web/src/app.tsx:300` — `realMode ? null : pendingCount`), and `GreenlightQueue` loads once
   on mount with no polling/refresh. A customer who doesn't open the page never learns drafts
   are waiting.
5. **Approval blindness.** The card's editable draft falls back to `""` when
   `proposed_action` has no `body|note|message|justification|summary`
   (`web/src/api/GreenlightQueue.tsx:24-30`), and no recipient/deal/contact context is rendered —
   a user can approve an action without seeing what it does or to whom.
6. **Isolation gate never probes `approvals`.** `scripts/isolation_test.py` covers documents +
   FKs; `test_control_rls.py` covers traces/settings. No cross-tenant approval read/decide probe.

### P1 — fix soon after

7. **Kill switch is invisible to the chat path.** No kill-switch reference in `conv/session.py`
   or `agents/runtime_selfhosted.py` (grep-verified). On MA, proposals still queue during a pause
   (arguably by design — execution is gated at approve); on the self-hosted (HIPAA-fallback)
   runtime, the whole tool loop runs with no pause check. Decide+document the MA behavior; add a
   check on the self-hosted path.
8. **Draft-only results read as real sends.** Applier returns `performed: false`, but the UI
   toast says "Approved and sent" and nothing logs/marks record-only outcomes server-side. A
   compliance reviewer (or customer) can misread a draft as a sent email.
9. **Already-decided UX.** A 400 "not pending" (another user decided first) surfaces as the
   generic "That decision didn't go through" and the stale item stays in the list — detect it,
   say so, refresh. *(Note: a reported "optimistic removal without rollback" bug was a false
   positive — removal happens only after a successful `await`.)*
10. **`GreenlightQueue` lacks the 404 "not yet enabled" degrade** that SecurityControls has —
    a 404 renders as a generic error.
11. **Registry/roster drift hazard.** `worker.TOOLS` is hand-curated (`worker/worker.py:56-60`);
    `UpdateContact`/`CreateActivity`/`CreateDeal` are registered with live CRM appliers but
    granted to no agent and not served. Parity test catches drift in CI only.
12. **Applier failure handling**: only the exception class name is recorded, no
    `logger.exception` (`api/app.py:~281`); no retry for transient CRM failures; error
    "approval X not pending" doesn't say the actual status.
13. **Deny is reason-less** in real mode (hardcoded "Declined by reviewer.",
    `GreenlightQueue.tsx`); the demo prototype's reason picker never shipped. Agents can't learn
    from rejections.
14. **Compliance checks are skeletal**: quiet hours hardcoded 21:00–08:00 trusting a
    payload-supplied `local_hour` (no tenant timezone), and blocks aren't logged.

### P2 — hygiene / hardening

15. **Authorization is single-role**: any authed tenant member can approve anything and flip
    the tenant kill switch/dial (v1 "every user is admin" — fine if documented; revisit when
    roles land).
16. **No retention/archival** for decided approvals (append-forever; pair with the index).
17. **Test gaps**: pool-exhaustion retry loop untested (`greenlight.py:115-130`); dial TTL-cache
    read-your-own-write untested; e2e missing deny flow and double-decide/stale-approval.
18. **Applier signature** lacks `approval_id`/`decided_by` — limits downstream audit/Cortex
    feedback tie-in.

## Test coverage map (condensed)

Covered: queue mechanics + race arbiter + edit guard (unit, 13), autonomy L0–L3 + overrides
(unit, 6), kill switch scopes (unit, 4), gate pipeline incl. compliance-block + L2 threshold
(unit, 4), applier dispatch + record-only (unit, 4), apply-on-approve end-to-end incl.
concurrency/killswitch/audit-failure/cross-tenant-404 (integration, 14), traces+settings RLS +
multi-instance (integration, 4), web approve/edit e2e (2), live `verify_agent_plane.py` steps
4–6 (propose → approve → execute, draft-only held).

Not covered: approvals-table RLS probe, expiry (nothing to test), deny e2e, stale/double-decide
e2e, pool exhaustion, dial cache read-back.
