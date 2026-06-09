# Brief: Phase 9b — Wire the React front end to the control-plane API

## Goal
Point the existing React+TS app (`web/`) at the FastAPI control plane so the three demo surfaces work
against the API: the **chat dock** (POST /chat), the **dashboard renderer** (GET/POST /views, render
the returned spec with the existing SpecRenderer), and the **Greenlight UI** — the demo centerpiece:
the approval queue with editable drafts + reasoning + value-at-stake (GET /approvals, POST
/approvals/{id}/decide). Keep build + typecheck + Playwright green, with an offline mock mode so e2e
needs no server.

## Owner / directory
You own **`web/`** only. Do NOT touch any other directory. Do NOT run git.

## API contract (from api/app.py — read it; do not change it)
- `GET /healthz` → {status}
- `GET /approvals` → {approvals:[{id, tenant_id, proposed_action, agent, reasoning, value_at_stake, status}]}
- `POST /approvals/{id}/decide` body {decision:"approve"|"edit"|"deny", edits?, deny_message?}
- `GET /views` → {views:[...]}, `GET /views/{id}` → row, `POST /views` body {spec, source_prompt}
- `POST /chat` body {message} → {answer, citations:[{claim, source_ref, snippet}], ...}
- `POST /actions` body {name, side_effecting, channel, payload, ...} → {status, decision, approval, result}
- Auth: every authed call sends `Authorization: Bearer <token>`. tenant_id is NEVER sent (the server
  derives it from the token — the trust rule). The client just attaches the token it was given.

## What to build (in web/)
- `web/src/api/client.ts` — a typed API client with an **injectable baseURL + token** and a
  **mock mode** (when `import.meta.env.VITE_API_MOCK` or a `mock` flag is set, return canned fixtures
  instead of fetching) so Playwright runs fully offline. Methods: `listApprovals`, `decideApproval`,
  `listViews`, `getView`, `saveView`, `chat`, `runAction`. Never put a token in code; read from config.
- Wire the existing **Greenlight** screen to `listApprovals`/`decideApproval` (show reasoning +
  value-at-stake + an editable draft; approve/edit/deny). If a suitable screen exists
  (`greenlight.tsx`), adapt it; otherwise add a thin container that uses the client.
- Wire the **chat dock** to `chat` (render answer + inline citations).
- Wire a **dashboard** path that calls `getView`/`saveView` and renders via the existing
  `SpecRenderer`.
- Keep all of this behind the client so production just flips mock→real + injects baseURL/token.

## Tests
- `web/e2e/greenlight.spec.ts` (Playwright, mock mode): the approval queue shows a pending item with
  its reasoning + value-at-stake; approving it removes it from the queue; the payload/token is never
  rendered.
- Keep `web/e2e/smoke.spec.ts` + `web/e2e/dashboard.spec.ts` passing.

## Done when
`cd web && npm install && npm run build` exits 0; `npm run typecheck` clean; `npx playwright test`
passes (smoke + dashboard + greenlight). The client defaults to mock mode for tests; real mode is a
config flip. No tenant_id is ever sent from the client. Update `web/README.md`.

## Constraints
- Touch only `web/`. No real network in tests (mock mode). No secrets/tokens in code. No git.
- Honor brand rules (no em-dashes in user-facing copy; say "Managed" not "Claude").
