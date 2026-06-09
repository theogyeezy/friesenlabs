# Uplift web

The Uplift front end: a Vite + React 18 + TypeScript app converted from the
original in-browser prototype. It renders the agentic-ops shell (sidebar,
topbar, command palette, tweaks panel) and the full set of product screens
(Command Center, Uplift CRM, Sell, Frontline, Workflows, Greenlight, Agents,
Sidecar, Cortex, Switchboard, Reports, Security, Settings, and more).

The agent "work" and the in-app AI helper are **simulated** in this build. There
are no real external API calls and no secrets. Production wiring (the Managed
runtime, dashboard renderer, Greenlight, chat dock) lands in a later phase
behind the `window.claude` seam (`src/ai.tsx`).

## Requirements

- Node 18+ (developed against Node 26)
- npm

## Install

```bash
npm install
```

## Develop

```bash
npm run dev
```

Serves the app on http://localhost:5173 with HMR. The shell mounts with the
Command Center as the default screen.

## Build

```bash
npm run build
```

Type-checks are intentionally lenient for this conversion pass (see
`CONVERSION_NOTES.md`); the build outputs to `dist/`. To run the TypeScript
checker on its own:

```bash
npm run typecheck
```

## Preview the production build

```bash
npm run preview
```

Serves the built `dist/` on http://localhost:4173.

## Test (Playwright smoke)

```bash
npm run test:e2e
```

This builds the app, starts `vite preview` headless, loads `/`, and asserts the
app shell mounts (the `#root` has rendered content, the sidebar is visible, the
default "Command Center" screen renders, and there are no page errors). Browser
binaries install on first run with:

```bash
npx playwright install chromium
```

## Layout

```
web/
  index.html            Vite entry, mounts src/main.tsx into #root
  src/
    main.tsx            imports CSS + globals barrel, renders <App/>
    globals.ts          side-effecting barrel: registers shared window globals
                        in the prototype's original load order
    app.tsx             the shell (sidebar, topbar, routing, palette, tweaks)
    data.tsx            mock workspace data (window.FL_DATA)
    store.tsx           shared store + useStore hook
    icons.tsx           icon set + logo
    ai.tsx              simulated, typed Managed AI helper (window.claude stub)
    styles.css          app styles (warm-tech aesthetic)
    landing.css         marketing-site styles
    screens/            ~40 screen + helper modules (charts, panels, gamify,
                        tweaks-panel, and every product screen)
  public/               static images served at the root
  e2e/                  Playwright smoke test
  playwright.config.ts
  CONVERSION_NOTES.md   how the global-sharing prototype was converted, and the
                        list of @ts-nocheck files to tighten later
```

## Notes

- Brand voice: no em-dashes in user-facing copy; say "Managed" not "Claude" in
  visible copy.
- Product naming: Cortex (intelligence layer), Sidecar (agentic suite),
  Switchboard (connector/data layer).
- The marketing `landing` / `foundation` screens are converted but not wired
  into this single-page app; see `CONVERSION_NOTES.md`.
```
