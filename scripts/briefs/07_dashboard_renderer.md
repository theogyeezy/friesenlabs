# Brief: Phase 7 (renderer) — the trusted view-spec renderer in web/

## Goal
Build the ONE component that turns a validated view-spec into pixels (Build Guide Step 42). The agent
emits a declarative spec (already built + validated server-side in `shared/view_spec.py` +
`shared/schemas/view_spec.schema.json`); the front end interprets it with a FIXED CATALOG — Vega-Lite
for charts inside a simple card/table component set, themed per tenant. The renderer is the only
attack surface, so it renders ONLY catalog components and re-validates the spec before rendering.

## Owner / directory
You own **`web/`** only (the React+TS app already exists there — Vite + React 18 + TS). Do NOT touch
any other directory. Do NOT run git. Reuse the existing app conventions (see `web/README.md`,
`web/CONVERSION_NOTES.md`).

## What to build
- `web/src/dashboard/viewSpec.ts` — the TypeScript type for a view-spec + a client-side validator.
  Mirror `shared/schemas/view_spec.schema.json` (copy it to `web/src/dashboard/view_spec.schema.json`
  and validate with `ajv`, OR hand-write a zod schema). Reject unknown component types, non-`vega-lite`
  encodings, and `additionalProperties` — same guarantees as the server schema. Export
  `validateViewSpec(spec): {ok, errors}`.
- `web/src/dashboard/SpecRenderer.tsx` — the trusted renderer. Given a spec:
  1. `validateViewSpec` first; if invalid, render a safe "couldn't render" message — NEVER render
     unvalidated/unknown content, NEVER `dangerouslySetInnerHTML`, never eval.
  2. For each `layout` block render only the catalog component: `kpi` → a KPI card, `chart`
     (encoding `vega-lite`) → a Vega-Lite chart (use `vega-embed` or `react-vega`), `table` → a table.
  3. Theme from CSS vars already in the app (warm-tech aesthetic). No `<script>`, no raw HTML from spec.
  - The chart's data comes from a `loadData(query)` prop (injected; a stub returning fixture rows in
    the demo/test) — the renderer does not fetch by itself.
- `web/src/dashboard/sample.ts` — a valid sample spec (a KPI + a bar chart over Deals) for the demo/e2e.
- A demo route/mount so the renderer is reachable in the running app (e.g. a `?view=demo` switch or a
  small dashboard demo screen) — keep it minimal and consistent with the existing shell.

## Tests
- `web/e2e/dashboard.spec.ts` — Playwright (headless, vite preview): load the demo, assert the KPI card
  renders with its number and the Vega-Lite chart `<canvas>`/`<svg>` appears; assert an INVALID spec
  renders the safe fallback (and does NOT inject script/HTML).
- Keep the existing `web/e2e/smoke.spec.ts` passing.

## Definition of done
- `cd /Users/yee/Desktop/friesenlabs/web && npm install && npm run build` exits 0; `npm run typecheck`
  clean (add `@types` as needed).
- `npx playwright test` passes (both smoke + the new dashboard spec).
- The renderer renders ONLY catalog components, re-validates first, and never renders code/HTML from a
  spec. Update `web/README.md` with a short "dashboard renderer" section.

## Constraints
- Touch only `web/`. No real network/API (data via the injected `loadData` stub). No secrets. No git.
- Honor brand rules (no em-dashes in user-facing copy; say "Managed" not "Claude").

## Report
Files created, the verbatim `npm run build` + `npx playwright test` tails, and how the renderer
guarantees spec-not-code (what it refuses to render).
