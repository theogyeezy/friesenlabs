# Neural Constellation Hero — design spec

**Date:** 2026-06-11 · **Lane:** Matt (app code only) · **Status:** approved via 10-iteration
visual-companion session (v1–v10 mockups in `.superpowers/brainstorm/`, local-only)

## What

Replace the landing-page hero visual with a live-rendered "neural constellation" of the product
suite: the 11 real products as glowing hubs (Command Center at the heart), each with its own
neuron cluster, signals firing between *every pair* of products, activity cards narrating what
agents just did, and a recurring Security guardrail interception that animates the draft-only
guarantee. Inspired by youtiva.com's hero, which we verified is a stock MP4 on a Webflow
template; ours is rendered live in canvas, ~8KB of dependency-free code, product-true.

## Decisions (from brainstorm)

1. **Placement:** the constellation becomes the hero (full-bleed stage, headline overlaid).
   The current hero content (product window demo, roster, trust strip, CRM note) moves to a new
   section directly below. Skip-link target `id="main" role="main"` stays on the new hero.
2. **Copy (blended):** eyebrow `MEET YOUR AI BACK OFFICE`; H1 **"Your AI workforce, working.
   Watched by you."**; sub "Eleven products, one network. Agents research leads, write quotes,
   chase follow-ups and book meetings around the clock. You approve anything important.";
   CTAs unchanged: primary "Build your suite" → `/?view=signup`, ghost "See it in action" → demos.
   No em-dashes or arrows in visible copy.
3. **Data:** curated, product-true scripted feed (no backend). Real-telemetry wiring is a
   possible follow-up, out of scope here.
4. **Security showcase:** every ~9s an outbound send streaks to Security, which intercepts it
   (green shield glyph + double expanding ring + green guardrail card "Caught a send outside
   policy, routed to Greenlight for you"), then the held draft relays along a green route to
   Greenlight. Greens use the existing `--green` family; everything else stays clay.

## Visual / motion spec (locked in v10, one fix)

- **Graph:** center hub Command Center + 10 product hubs (Uplift CRM, Frontline, Workflows,
  Greenlight, Agents, Switchboard, Sidecar, Knowledge, Cortex, Security) on an organic ring
  (radius 0.55±0.07, y ±0.26); 21 satellite neurons per hub (gaussian σ≈0.19) + 8 intra-cluster
  links; 170 background dust points (slow 0.55× parallax). Seeded LCG (seed 42) so layout is
  deterministic.
- **Projection:** rotation 0.00009 rad/ms around Y, tilt 0.3, perspective k = 3.2/(3.2−z),
  per-node sine wobble ±0.012. Stage `height: 86vh; min-height: 540px`.
- **Traffic:** routes may connect ANY two hubs (no fixed lanes). A route is invisible until
  used: while a signal travels it draws a faint full line + bright trail + glowing head, then
  disappears. Hubs emit every 760ms (35% chance each, 20% double); arrivals relay onward
  (p=0.55, second branch p=0.15); 30% of destinations bias to Greenlight/Command Center (where
  product flows converge). Route cap 30. Ambient intra-cluster pulses: spawn ~700ms, cap 24.
  Landing signals flash an expanding ring on the hub.
- **Activity cards:** up to 2 product cards (+1 reserved Security slot). Solid paper
  (`--surface`), hairline border, 3px clay left bar (green for Security), 4px cream halo
  (`box-shadow: 0 0 0 4px`), deep soft shadow. Mono header `PRODUCT · just now`, body with
  clay-highlighted numbers. New card every 1700ms, life 3200ms (Security 4200ms). Cards dock
  beside-and-below their hub on the side away from the viewport edge, follow the hub as the
  network rotates (thin stem line + anchor dot), dodge each other, and are **fenced above 56%
  of stage height** (v10 used 58/62% and could kiss the eyebrow; tightened here) so they never
  touch the hero text block. Per-product message sets (3 each) using the page's personas
  (Margo, Pip, Nadia, Ledger, Scout).
- **Palette mapping:** demo hexes → existing `.lp` tokens. Paper/bg gradient → `--bg`/`--surface`;
  node/edge clay → `--accent` / `--accent-ink`; card ink → `--ink`; hairlines → `--line`;
  security green → `--green`. Canvas colors may stay literal (canvas cannot read CSS vars
  cheaply) but must visually match the tokens above.

## Architecture

- **New file `web/src/screens/landing-constellation.tsx`** — self-contained
  `<ConstellationHero>` component: a `position:relative` stage with `<canvas>`, three card
  `<div>`s (refs, direct-DOM mutation in the rAF loop — zero per-frame React state), a LIVE
  badge, and `{children}` rendered as the overlay (copy lives in `landing.tsx`).
  Engine = straight TS port of mockup v10 (graph build, project, routes, cluster pulses,
  cards, security cycle, shield glyph).
- **Lifecycle guards:** rAF runs only while the stage is on screen (IntersectionObserver) AND
  the tab is visible (`visibilitychange`); `prefers-reduced-motion` renders one static frame
  with a single static card and no loop; DPR capped at 2; full cleanup on unmount.
- **`landing.tsx` changes:** hero section becomes the constellation (keeps `lp-hero` class,
  `id="main" role="main"`, the only `.lp-hero-cta` on the page, h1). Previous hero content
  moves to a new `lp-section` right below: pill, heading demoted to `lp-h2` "Your business,
  run by agents." (avoids duplicating the new H1), lead, CTA row reclassed `lp-window-cta`
  (so the e2e `.lp-hero-cta a.btn-primary` locator still matches exactly once), trust strip,
  CRM note, HeroRoster, and the product-window demo plate.
- **`landing.css`:** new `.lp-constellation` block (stage, canvas, overlay column, acard styles,
  livebadge) inside the `.lp` scope; reuse existing tokens; `lp-window-cta` alias of the old
  hero-cta layout.
- **A11y:** canvas + cards container `aria-hidden="true"` (decorative narration); semantic
  hero content (eyebrow/h1/sub/CTAs) stays in DOM order on top; heading order preserved
  (new h1, then h2s); no keyboard traps (nothing focusable inside the canvas layer).
- **Perf budget:** no new dependencies; component ≤ ~10KB pre-gzip; no effect on the
  authed-app code split; landing first-load stays ~250KB gz; Lighthouse 100s must hold.

## Testing

1. `npm run typecheck` + `npm run build` green.
2. Playwright e2e: existing `conversion.spec.ts` hero-CTA test passes unchanged (single
   `.lp-hero-cta`); landing smoke specs pass.
3. Browser verify (devtools MCP): constellation animates, cards cycle and never enter the
   text block, security moment plays ≤10s after load, no console errors; mobile (390px)
   renders with reduced density; reduced-motion emulation shows a static frame.
4. Lighthouse spot-check on the changed page (no regression in SEO/BP/A11y).

## Out of scope

Real telemetry endpoint for the activity feed; dark-skin variant; replacing the moved-down
product-window section (it stays as-is, just relocated).
