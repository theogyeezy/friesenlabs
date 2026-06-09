# Brief: Convert the Friesen Labs prototype into a Vite + React + TypeScript app in `web/`

## Context (you have zero prior context — read this fully)
The existing front end is a **Babel-standalone in-browser prototype** at
`/Users/yee/Desktop/Friesen Labs/` (note the space in the path). It is ~45 `.jsx` files plus
`styles.css`, `landing.css`, `index.html`, images, and a `design_handoff_friesen_labs/` folder.

How it currently works:
- `index.html` pulls React 18 UMD + `@babel/standalone` from unpkg, then loads each `.jsx` via
  `<script type="text/babel" src="...">` **in a specific order** (see that file's script tags).
- Files do **NOT** use ES imports. They share **globals**: `React` (and `const {useState,...} = React`),
  `window.FL_DATA` (from `data.jsx`), `useStore` (from `store.jsx`), `Icon` (from `icons.jsx`),
  `window.claude` (in-page AI helper from `ai.jsx`), etc. `app.jsx` defines `App()` and mounts it.

This repo (`friesenlabs`, the Uplift build) wants this as a real **React + TypeScript** app under
`web/`, building with Vite, so it can later be wired to the Uplift backend (chat dock, dashboard
renderer, Greenlight UI, etc.). You OWN `web/` exclusively — do not touch any other directory.

## Brand / product constraints (from the prototype's CLAUDE.md — honor these)
- **No em-dashes (—) in user-facing copy.** Use commas. (Brand voice.)
- Do **NOT** surface "Claude" in user-facing copy; say **"Managed"**. (Internal code/comments fine.)
- Warm-tech aesthetic: cream canvas, warm ink, indigo primary; Hanken Grotesk + JetBrains Mono.
- Product naming: Cortex = intelligence layer; Sidecar = agentic suite; Switchboard = connector/data layer.

## Goal
A Vite + React 18 + TypeScript app in `web/` that **builds green** (`npm run build`) and renders the
existing prototype shell + screens, with the shared-global architecture replaced by clean module
wiring. Keep ALL existing screens and styling intact (copy `styles.css`, `landing.css`, fonts, images).

## Method (pragmatic — building green is the bar, perfect types come later)
1. `cd web` and scaffold Vite React-TS: `npm create vite@latest . -- --template react-ts` (or author
   the equivalent `package.json`, `vite.config.ts`, `tsconfig.json`, `index.html`, `src/main.tsx`).
   Pin React 18.3.x to match the prototype.
2. Copy the source files in: `src/screens/*.tsx` (rename each `.jsx`→`.tsx`), plus `src/data.tsx`,
   `src/store.tsx`, `src/icons.tsx`, `src/ai.tsx`. Copy `styles.css` + `landing.css` into `src/` and
   import them from `main.tsx`. Copy images into `web/public/` and fix their paths.
3. Replace the global-sharing with real modules:
   - Add a small `src/globals.ts` (or per-file shims) that **exports** what each file currently
     dumps on the window: `FL_DATA`, `useStore`, `Icon`, the `claude` helper, etc. Convert
     `const {useState} = React` → `import React, { useState } from "react"`.
   - Where a file reads `window.FL_DATA`/`useStore`/`Icon`, change to an `import`.
   - `app.tsx` should export `App`; `main.tsx` renders `<App/>` into `#root`.
   - Preserve load-order semantics (data/store/icons/ai must initialize before screens use them —
     module imports handle this naturally once converted).
4. Keep TypeScript **non-strict** initially (`"strict": false`, `"noImplicitAny": false`,
   `allowJs": true`) so the build passes; add `// @ts-nocheck` to any file too tangled to type
   quickly. Leave a `web/CONVERSION_NOTES.md` listing every file you `@ts-nocheck`'d so types can be
   tightened later. Do not delete features to make it build.
5. The in-page `window.claude` AI helper: keep it as a typed stub module (`src/ai.tsx`) that returns
   simulated text, exactly as the prototype does. Production wiring is a later phase. **Do not** call
   any real API.

## Output / Definition of done for this brief
- `web/` is a self-contained Vite React-TS app.
- `cd web && npm install && npm run build` **exits 0** (report the exact output).
- `npm run dev` serves the app and the shell renders (sidebar + a default screen). Note any screens
  that error at runtime in `CONVERSION_NOTES.md` (don't silently drop them).
- A Playwright smoke at `web/e2e/smoke.spec.ts` that starts the dev/preview server, loads `/`, and
  asserts the app shell mounts (e.g. `#root` has children / a known nav label is visible). Add
  `@playwright/test` as a devDep and a `playwright.config.ts` (headless, webServer = vite preview).
- Update `web/README.md` with how to dev/build/test.

## Constraints
- Touch **only** `web/`. Do not run `git`. Do not install anything outside `web/`.
- Do not call real external APIs. No secrets. Keep the `window.claude` helper simulated.
- Honor the brand constraints above (no em-dashes / no "Claude" in visible copy).
- If something is ambiguous, prefer the choice that keeps `npm run build` green and preserves screens.

## Report back
A short summary: what scaffolding you created, how many screens migrated, the `npm run build` result
(verbatim tail), the Playwright smoke result, and the list of `@ts-nocheck` files in CONVERSION_NOTES.md.
