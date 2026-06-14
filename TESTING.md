# Testing — the trophy

This is how we test Uplift end to end, and **how to decide what to run for a given change**. The
shape is the *testing trophy*: a wide base of fast static + unit checks, a broad middle of
integration/component tests (where most of the value is), a thin top of full end-to-end flows, plus
two cross-cutting layers (accessibility, visual) and a production smoke.

**No single layer is "100% accurate."** The only ground truth is a real browser rendering real
pixels for a real user. Everything faster than that simulates *something* to buy speed and
determinism. So we don't chase one perfect test — we stack layers, each owning a failure class the
others can't see, and a layer "in code" is not the opposite of "a real browser" (Playwright runs in
CI *and* drives real Chromium).

```
        ╱╲        e2e (Playwright, real browser)        — full flows; few, slower
       ╱──╲       integration / component (Vitest+RTL)  — the bulk of the value
      ╱────╲      unit (Vitest / pytest)                — pure logic
     ╱──────╲     static (tsc, ESLint)                  — the foundation
   ─────────────  cross-cutting: a11y (axe) · visual (screenshots) · prod smoke
```

## The layers

| Layer | Tooling | Runs | Catches | Cannot see |
|---|---|---|---|---|
| **Static** | `tsc`, ESLint (react-hooks, jsx-a11y) | `web` typecheck + `npm run lint`; `ruff`/imports for py | type errors, hook misuse, dead code, a11y-lint | runtime behavior |
| **Unit** | Vitest (web), pytest (backend) | `npm run test:unit`, `pytest` | pure functions, edge cases | rendering, integration |
| **Component / integration** | Vitest + @testing-library/react (jsdom) | `npm run test:unit` | does the component render, fire events, call the right handlers; the **bulk** of UI logic | real CSS/layout/pixels |
| **Backend integration** | pytest `tests/integration` + real Postgres RLS gate | `pytest`, `scripts/isolation_test.py` | API + DB + tenant isolation | the browser |
| **E2E** | Playwright (real Chromium) | `npm run test:e2e` | full user flows through the real app (mock backend) | backend contract drift |
| **Accessibility** | `@axe-core/playwright` | part of `test:e2e` (`a11y.spec.ts`) | WCAG A/AA: contrast, names, roles, landmarks | non-a11y behavior |
| **Visual** | Playwright `toHaveScreenshot` (opt-in) | `npm run test:visual` | CSS/layout regressions the DOM tests pass through | logic |
| **Prod smoke** | Playwright vs the live URL (opt-in) | `npm run test:smoke` | the **real deployed** system + real backend | nothing — it's the real thing |

jsdom (the component layer) is a simulated DOM: it does **not** do layout or real CSS, so it can't
catch "the button is visually hidden." That's exactly what the e2e + visual layers cover. Use the
cheapest layer that can catch the bug, and let the slower layers cover what it can't.

## Commands

Backend (repo root, venv active):

```bash
pytest -q                          # unit + integration
python scripts/isolation_test.py   # multi-tenant RLS gate (after any data/auth/agent change)
bash scripts/smoke_all.sh          # roll-up smoke
terraform -chdir=infra validate    # infra
```

Frontend (`web/`):

```bash
npm run lint            # static: ESLint (errors fail; warnings advisory)
npm run typecheck       # static: tsc --noEmit
npm run test:unit       # unit + component (Vitest + Testing Library)  ← fast, run constantly
npm run test:watch      # the same, in watch mode while developing
npm run test:coverage   # component/unit coverage report
npm run test:e2e        # Playwright e2e + a11y (real Chromium; builds the bundles)
npm run test:a11y       # just the axe accessibility spec
npm run test:visual     # visual regression (opt-in; needs baselines — see below)
npm run test:smoke      # production smoke (set PROD_SMOKE_URL first)
npm test                # CI's frontend gate: vitest + auth-core node tests
```

## What to run for a change ("run what's needed")

Pick by what you touched. When in doubt, run one layer up.

| You changed… | Run |
|---|---|
| `web/src/**` component/UI logic | `npm run lint && npm run typecheck && npm run test:unit`, then the e2e spec(s) for that screen |
| a styling/layout/design token (`styles.css`, large CSS) | the above **+ `npm run test:a11y`** (contrast) **+ `npm run test:visual`** (layout) |
| `web/src/api/client.ts` or a shared web util | `npm run test:unit` (the affected unit tests) + `npm run typecheck` |
| `api/**`, `agents/**`, `conv/**`, `ingest/**`, `semantic/**` (backend) | `pytest -q`; **+ `scripts/isolation_test.py`** if it touched data/auth/agents/RLS |
| a tool/Greenlight/compliance path | `pytest -q` (tool + greenlight + compliance suites) + the backend integration tests |
| `db/schema.sql` or a migration | `pytest -q` + `isolation_test.py`; the migrate workflow ships it (see `infra/RUNBOOK.md`) |
| `infra/**` | `terraform -chdir=infra fmt -check && validate`, then a reviewed `plan` (Deploy pipeline) |
| `.github/workflows/**` | validate YAML; dry-run the affected job |
| anything user-facing, before calling it done | the relevant e2e spec in real Chromium — never just "it should work" |

After a **deploy**, run the production smoke against the live site (the one check mock-mode e2e
structurally can't do):

```bash
PROD_SMOKE_URL=https://www.friesenlabs.com npm run test:smoke
```

## CI

`.github/workflows/ci.yml` runs on every PR to `main`:

- **backend job** — `pytest` against a real Postgres+pgvector service, then the multi-tenant
  isolation gate; `terraform validate`.
- **web job** — `npm run lint` → `npm run typecheck` → `npm test` (vitest + auth-core) →
  `npm run build` → `npx playwright test` (e2e **+ the axe a11y spec**, all in real Chromium).
- **smoke job** — the roll-up `scripts/smoke_all.sh`.

The `visual` and `prod-smoke` specs are **opt-in** (each self-skips unless its env flag is set), so
they never flake CI; they're run deliberately (visual with generated baselines, smoke after deploy).

## Visual regression — baselines

Screenshot baselines are **platform-specific** (font hinting differs macOS vs CI Linux), so they are
generated in the CI environment and committed, never on a laptop:

```bash
# in the CI/Linux environment (or the Playwright Docker image):
RUN_VISUAL=1 npm run test:visual:update   # writes e2e/visual.spec.ts-snapshots/*.png
git add web/e2e/visual.spec.ts-snapshots && git commit
```

Then `npm run test:visual` diffs against them (tolerance: `maxDiffPixelRatio: 0.01`). Until baselines
exist for a platform the visual project simply skips. For a managed alternative, Storybook +
Chromatic does cloud visual review — not wired today.

## Conventions

- Component tests live next to source as `*.test.tsx` or in `__tests__/`. They render the **real**
  component with a small fake `ApiClient` (`isMock: () => true` short-circuits the conversation
  machinery) and assert on the DOM via `@testing-library` queries + `userEvent` (which wraps state
  updates in `act`, so the output stays warning-free).
- Test behavior the user can observe, not implementation details. Prefer `getByRole`/`getByTestId`
  and real events over poking internals.
- A bug fix starts with a failing test that reproduces it (TDD); see `CONTRIBUTING.md`.
