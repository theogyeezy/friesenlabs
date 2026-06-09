# Conversion notes: prototype -> Vite + React + TypeScript

This `web/` app is a conversion of the in-browser Babel-standalone prototype
(`/Users/yee/Desktop/Friesen Labs/`) into a real Vite + React 18 + TypeScript
app. The goal of this pass was a **green build** while preserving every screen
and the existing styling; full strict typing is a later phase.

## What changed structurally

- **Build tooling.** Authored `package.json`, `vite.config.ts`, `tsconfig.json`,
  `index.html`, and `src/main.tsx`. React/ReactDOM are pinned to `18.3.1` to
  match the prototype's UMD versions. The unpkg React/Babel `<script>` tags are
  gone; everything is a real ES module now.
- **Global sharing -> module graph.** The prototype shared state through `window`
  globals (`window.FL_DATA`, `useStore`, `Icon`, `window.claude`, chart/panel/
  tweak helpers, every screen component) and relied on a fixed `<script>` load
  order in `index.html`. That contract is preserved as a real module graph:
  `src/globals.ts` is a side-effecting barrel that imports the infrastructure +
  screen modules in the **same order** the prototype loaded them, so each
  module's `window.X = ...` registrations run deterministically before any
  screen renders. Screens read their shared dependencies from `window` at the
  top of the module (`const { Icon, useStore, ... } = window as any`). All such
  reads are consumed inside component render bodies, which run after the whole
  graph has loaded, so init order is safe.
- **React imports.** Each `const { useState, ... } = React;` header was replaced
  with a real `import React from "react"` plus a destructure of the React hooks.
- **AI helper.** `ai.jsx` became `src/ai.tsx`, a **typed, simulated stub**. It
  installs a `window.claude.complete` that returns short, business-flavored text
  with no network call and no secrets. Production will swap this for the Managed
  runtime seam. No "Claude" appears in user-facing copy (visible copy says
  "Managed" / "AI"); `askClaude` is an internal function name only.
- **Assets.** `styles.css` + `landing.css` copied into `src/` and imported from
  `main.tsx`. `matt-yee.jpeg` and `nick-friesen.png` copied into `public/`; the
  two references in `landing.tsx` were repointed to `/matt-yee.jpeg` and
  `/nick-friesen.png` (served from the public root).
- **CSS fix.** `landing.css` line 207 had a malformed trailing `: 0; }` fragment
  after a complete rule (a source typo in the prototype). Removed so the CSS
  minifier is warning-free. No visual change.

## Brand constraints honored

- No em-dashes in user-facing copy (existing copy already complied; the AI stub
  copy uses commas).
- "Managed" not "Claude" in visible copy. Renamed the in-app product surface
  from "Friesen Labs" to "Uplift" in the new shell title/AI context strings to
  match this repo's product naming; screen copy is otherwise preserved verbatim.

## Entry points

The prototype had three HTML entry points. This pass wires the **main app**
(`app.tsx`, the sidebar shell + ~ all screens) as the single-page app mounted by
`main.tsx`. The two marketing entries were converted too (they count as migrated
screens) but are **not** mounted into the SPA:

- `src/screens/landing.tsx` (from `landing.jsx`, the marketing site)
- `src/screens/landing-demos.tsx` (interactive demos used by landing)
- `src/screens/foundation.tsx` (the Foundation page)

Their self-mounting `ReactDOM.createRoot(...).render(...)` calls were stripped so
importing them is inert. To ship them later, give each its own Vite entry +
barrel (they read `Icon`/charts/landing-demos globals the same way screens do).

## `@ts-nocheck` files (42)

Every converted prototype file carries `// @ts-nocheck` at the top to get the
build green quickly; these are the files where types should be tightened later.
The hand-authored modules (`src/ai.tsx`, `src/globals.ts`, `src/main.tsx`) are
typed and do **not** use `@ts-nocheck`.

- `src/app.tsx`
- `src/data.tsx`
- `src/icons.tsx`
- `src/store.tsx`
- `src/screens/agent-market.tsx`
- `src/screens/agents.tsx`
- `src/screens/billing.tsx`
- `src/screens/brain.tsx`
- `src/screens/calendar.tsx`
- `src/screens/charts.tsx`
- `src/screens/chat.tsx`
- `src/screens/commandbot.tsx`
- `src/screens/contacts.tsx`
- `src/screens/cortex.tsx`
- `src/screens/crm.tsx`
- `src/screens/dashboard.tsx`
- `src/screens/email.tsx`
- `src/screens/foundation.tsx`
- `src/screens/frontline.tsx`
- `src/screens/gamify.tsx`
- `src/screens/greenlight.tsx`
- `src/screens/import-data.tsx`
- `src/screens/intake.tsx`
- `src/screens/integrations.tsx`
- `src/screens/knowledge.tsx`
- `src/screens/landing-demos.tsx`
- `src/screens/landing.tsx`
- `src/screens/onboarding.tsx`
- `src/screens/panels.tsx`
- `src/screens/personal-recall.tsx`
- `src/screens/reports.tsx`
- `src/screens/reviews.tsx`
- `src/screens/salesdesk.tsx`
- `src/screens/security.tsx`
- `src/screens/sell.tsx`
- `src/screens/settings.tsx`
- `src/screens/sidecar.tsx`
- `src/screens/studio.tsx`
- `src/screens/templates.tsx`
- `src/screens/tour.tsx`
- `src/screens/tweaks-panel.tsx`
- `src/screens/workflow.tsx`

## Runtime status

`npm run build` exits 0 and the Playwright smoke (`e2e/smoke.spec.ts`) confirms
the shell mounts against `vite preview` with **zero page errors** ("Command
Center" default screen + sidebar render). No screens errored at runtime during
the smoke load. Screens reachable only via in-app navigation were not each
individually click-tested; if any throws on navigation it would surface here and
should be recorded.
