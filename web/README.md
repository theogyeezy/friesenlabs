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

## Test

```bash
npm test        # unit tests (node --test, zero deps): the auth core
npm run test:e2e  # Playwright e2e (mock mode, fully offline)
```

`npm test` runs `test/*.test.mjs` under Node's built-in `node:test` runner (the
same zero-dependency pattern as `semantic/test/`) against the pure auth helpers
in `src/auth/core.js` — PKCE challenge shape, state validation, token-storage
round-trip, JWT payload decode, and the 401-refresh-retry policy.

`npm run test:e2e` builds the app, starts `vite preview` headless, loads `/`,
and asserts the app shell mounts (the `#root` has rendered content, the sidebar
is visible, the default "Command Center" screen renders, and there are no page
errors). Browser binaries install on first run with:

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
    auth/               Cognito Hosted UI login: core.js (pure PKCE/JWT/storage
                        helpers), cognito.ts (redirect/exchange/refresh/logout),
                        AuthContext.tsx (useAuth provider)
    styles.css          app styles (warm-tech aesthetic)
    landing.css         marketing-site styles
    screens/            ~40 screen + helper modules (charts, panels, gamify,
                        tweaks-panel, and every product screen)
  public/               static images served at the root
  e2e/                  Playwright smoke test
  test/                 node --test unit tests (auth core)
  playwright.config.ts
  CONVERSION_NOTES.md   how the global-sharing prototype was converted, and the
                        list of @ts-nocheck files to tighten later
```

## Dashboard renderer

`src/dashboard/` holds the Phase 7 trusted view-spec renderer: the one component
that turns a declarative dashboard spec into pixels.

- `viewSpec.ts` is the client-side mirror of
  `shared/schemas/view_spec.schema.json`: a TypeScript type plus
  `validateViewSpec(spec)`, a hand-written validator that rejects unknown
  component types, any chart encoding other than `vega-lite`, and any extra
  ("additional") property at every level. The catalog is closed by construction.
- `SpecRenderer.tsx` is the renderer. It re-validates the spec first and shows a
  safe "could not render" fallback if invalid. It renders ONLY the catalog: KPI
  card, Vega-Lite chart (via `vega-embed`, SVG, loaders disabled), and table.
  It never uses `dangerouslySetInnerHTML`, `eval`, or any raw-HTML sink, so spec
  strings can only appear as escaped React text. Data comes solely through the
  injected `loadData(query)` prop; the renderer never fetches.
- `sample.ts` is a valid KPI + bar-chart spec plus an offline `loadData` stub.
- `Demo.tsx` mounts the renderer at `?view=dashboard-demo` (a switch in
  `main.tsx`), with a toggle between the valid spec and a malicious/invalid spec
  to demonstrate the safe fallback.

The e2e (`e2e/dashboard.spec.ts`) asserts the KPI number and chart SVG render
for the valid spec, and that an injected `<script>`/HTML payload never reaches
the DOM or executes for the invalid spec.

## Control-plane API client

`src/api/` wires the app to the FastAPI control plane (`api/app.py`).

- `client.ts` is the typed client. It takes an injectable `baseURL` plus a
  `getToken` callback and exposes `listApprovals`, `decideApproval`,
  `listViews`, `getView`, `saveView`, `chat`, and `runAction`. The bearer is the
  **Cognito ID token** (the API rejects access tokens — `api/auth.py` checks
  `token_use=id` and `aud=client_id`), read per request from the auth layer
  (`src/auth/`), never hardcoded and never snapshotted into the singleton. It is
  attached only as `Authorization: Bearer <token>`. On a 401 the client makes
  one refresh attempt (`refreshAuth`) and retries once; a second 401 surfaces as
  an `ApiError` and the UI treats the session as signed out. The client NEVER
  sends `tenant_id`: the server derives the tenant from the verified token (the
  trust rule), and no request body shape carries a tenant field.
- Mock mode is the default (`VITE_API_MOCK` unset, or anything other than `0` /
  `false`). In mock mode every method resolves from in-memory fixtures and makes
  no network call, so Playwright runs fully offline. Production is a config flip:
  set `VITE_API_MOCK=0`, `VITE_API_BASE_URL`, and the `VITE_COGNITO_*` vars
  (see "Auth" below).
- The wired surfaces mount via the same `?view=` seam as the dashboard demo and
  do not touch the converted (`@ts-nocheck`) shell:
  - `?view=greenlight` (`GreenlightQueue.tsx`): the approval queue. Each pending
    item shows the agent's reasoning, the value at stake, and an editable draft;
    approve / approve-with-edits / deny call `decideApproval` and drop the item.
    The bearer token and the full proposed-action payload are never rendered.
  - `?view=chat` (`ChatDock.tsx`): calls `chat` and renders the answer with
    inline citations (claim, source ref, snippet).
  - `?view=dashboard` (`DashboardView.tsx`): loads a saved view via `getView`,
    renders it through the trusted `SpecRenderer`, and persists edits via
    `saveView`.
  - `?view=signup` (`signup/SignupFlow.tsx`): the marketing to provisioned
    funnel (see below).

The client also exposes the public, pre-auth signup methods `signup`,
`verifyEmail`, `verifyPhone`, `checkout`, and `getSignup`. These run before an
account has a tenant, so they attach **no** bearer token and (like every other
method) **never** send a `tenant_id`. Their mock fixtures walk the funnel state
machine (`created -> email_verified -> phone_verified -> paid -> provisioning ->
active`) so the whole flow runs offline.

`e2e/greenlight.spec.ts` exercises the queue in mock mode (reasoning + value
render, approve removes the item, edited draft approves with edits, and no
token/payload leaks into the DOM).

## Auth (Cognito Hosted UI, PKCE)

`src/auth/` is the hand-rolled login flow — authorization code + PKCE (S256)
against the Cognito Hosted UI, with **no** auth SDK (no `aws-amplify`, no
`oidc-client-ts`):

- `core.js` — pure, dependency-free helpers (PKCE pair, state, JWT payload
  decode, token/PKCE storage, the 401-refresh-retry policy). Plain ESM JS so
  `node --test` unit-tests it directly (`npm test`) without a build step.
- `cognito.ts` — the browser wiring: `signIn()` (stash verifier+state in
  sessionStorage, redirect to `/oauth2/authorize`), `handleCallback()`
  (validate state, exchange the code at `/oauth2/token`, one-shot so
  StrictMode can't burn the single-use code), `refreshTokens()`
  (`grant_type=refresh_token`, single-flight), `signOut()` (clear + Hosted UI
  `/logout`). Tokens persist in localStorage under one key
  (`uplift_auth_tokens`; the XSS tradeoff is documented at the constant).
- `AuthContext.tsx` — `useAuth()` exposes `{isAuthenticated, idToken, claims,
  email, tenantId, signIn, signOut}`. Claims are a base64 decode for display
  only (the API verifies signatures). The provider auto-refreshes when the ID
  token is within 5 minutes of expiry; a failed refresh signs out locally
  (no redirect loop).

Routing: `main.tsx` handles `location.pathname === "/auth/callback"` (the
Amplify SPA rewrite and `vite preview` both serve index.html there), exchanges
the code, then `history.replaceState`s back to `/`.

The sign-in gate is active only when **Cognito is configured AND mock mode is
off** (`VITE_COGNITO_DOMAIN` nonempty + `VITE_API_MOCK=0`): signed-out visitors
get the marketing landing (`screens/landing.tsx`) with its Sign in controls
wired to the Hosted UI; signed-in users get the shell with their real email in
the topbar chip. Otherwise — local dev, unit tests, Playwright — the entire
auth layer is inert (zero listeners, zero network) and the app behaves exactly
as the historical mock build.

Env contract (provided at Amplify build time, see
`infra/modules/web_hosting/main.tf`): `VITE_COGNITO_DOMAIN` (bare Hosted UI
host, no scheme), `VITE_COGNITO_CLIENT_ID` (public client, no secret),
`VITE_COGNITO_REGION`. Redirect URI is `{origin}/auth/callback`; logout URI is
`{origin}/` (both registered in `infra/variables.tf`). There is no
`VITE_API_TOKEN` anymore.

## Signup funnel + analytics

`src/signup/SignupFlow.tsx` is the multi-step funnel reachable at `?view=signup`:
account form (email, password with an in-memory strength meter, phone) → email
verify → phone OTP → plan + explicit price consent ("You'll be charged $X/mo")
→ provisioning poll → success.

- The **password never leaves the browser.** The signup API contract carries only
  `{email, phone}`; the password lives in component state, is never sent, never
  logged, and never passed to analytics. The strength meter reads only derived
  signal (length/variety) in memory.
- The email token and SMS code go only to their verify endpoints and are never
  rendered back, stored, or captured.
- `e2e/signup.spec.ts` walks the funnel to `active` in mock mode, asserts the
  price consent shows before the pay action, and asserts no password / token /
  OTP / `tenant_id` leaks into the DOM or into any analytics call.

`src/analytics/posthog.ts` is a thin, **injectable** PostHog wrapper.

- The project key is read **only** from the environment
  (`VITE_POSTHOG_KEY`); there is no key literal in the source.
- It is a **hard no-op in mock/test mode** and whenever no key is present, so
  Playwright and local previews make zero analytics network calls.
- It captures the funnel events `landing_view`, `signup_started`,
  `email_verified`, `phone_verified`, `payment_submitted`, `payment_succeeded`,
  `instance_provisioned`, `first_login`. Revenue is captured **server-side** on
  the Stripe webhook, so the client never emits a `$`-amount on
  `payment_succeeded`.
- Session replay is initialized with `session_recording.maskAllInputs: true`, so
  passwords, OTP codes, emails, and phone numbers never reach a recording. A
  defensive `sanitize()` also strips `tenant_id` / `password` / `token` / `code`
  from any capture payload.

### `/ph` first-party reverse proxy

In production, analytics ingestion is routed through a **first-party reverse
proxy** so the browser never contacts a third-party analytics domain directly
(this dodges ad-blockers and keeps all traffic same-origin). The wrapper sets
PostHog's `api_host` (and `ui_host`) to the same-origin path **`/ph`**, read from
`VITE_POSTHOG_HOST` (default `/ph`).

Wire the edge so requests under `/ph/*` are rewritten and forwarded to PostHog
server-side. For example, with a CloudFront / nginx style rule:

```
# Browser  -> https://app.uplift.example/ph/*   (same origin, no 3rd-party domain)
# Proxy    -> https://us.i.posthog.com/*         (strip the /ph prefix; static
#                                                  assets -> us-assets.i.posthog.com)
location /ph/ {
  proxy_pass https://us.i.posthog.com/;   # adjust region host as needed
  proxy_set_header Host us.i.posthog.com;
}
```

No PostHog key lives in this config or anywhere in the repo; the key is supplied
to the browser bundle only via the `VITE_POSTHOG_KEY` build env, and ingestion is
authenticated by that key, not by the proxy.

## Notes

- Brand voice: no em-dashes in user-facing copy; say "Managed" not "Claude" in
  visible copy.
- Product naming: Cortex (intelligence layer), Sidecar (agentic suite),
  Switchboard (connector/data layer).
- The marketing `landing` / `foundation` screens are converted but not wired
  into this single-page app; see `CONVERSION_NOTES.md`.
```
