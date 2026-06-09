# Brief: Section A4 — Signup funnel UI + PostHog client (web/)

## Goal
Wire the marketing → signup → verify → pay → provisioned funnel in the existing React+TS app to the
signup API, and add the PostHog client (funnel capture + session replay masking + first-party proxy
note). Mock mode so Playwright runs offline. Keep build + typecheck + e2e green.

## Owner / directory
You own **`web/`** only. Do NOT touch any other directory. Do NOT run git. Reuse the existing
`web/src/api/client.ts` pattern (injectable baseURL + token + mock mode) and the existing
`landing.tsx` / `onboarding.tsx` screens.

## Signup API contract (public, pre-auth — these endpoints are being built in parallel; code to this)
All under the same baseURL, NO bearer token (the account has no tenant yet):
- `POST /signup` body `{email, phone}` → `{account_id, state}`
- `POST /signup/{account_id}/verify-email` body `{token}` → `{state, email_verified}`
- `POST /signup/{account_id}/verify-phone` body `{code}` → `{state, phone_verified}`
- `POST /signup/{account_id}/checkout` body `{plan}` → `{checkout_id, stripe_customer_id}`
- (Provisioning happens server-side on the Stripe webhook; the UI polls `GET /signup/{account_id}`
  → `{state}` until `active`, then routes to login.)
The trust rule still holds: the client NEVER sends tenant_id.

## What to build (in web/)
- Extend `web/src/api/client.ts` with `signup`, `verifyEmail`, `verifyPhone`, `checkout`,
  `getSignup` methods + mock-mode fixtures that walk the state machine
  (created→email_verified→phone_verified→paid→provisioning→active).
- A `web/src/signup/SignupFlow.tsx` multi-step flow: account form (email/password/phone, password
  strength via a simple zxcvbn-style meter — input never logged), email-verify step, phone-OTP step,
  plan + explicit price consent ("You'll be charged $X/mo"), then a "provisioning…" poll → success.
  Reachable via `?view=signup`. Reuse the brand styling.
- `web/src/analytics/posthog.ts` — a thin, INJECTABLE PostHog wrapper (no real key in code; read from
  `import.meta.env.VITE_POSTHOG_KEY`; **no-op in mock/test mode**). Capture the funnel events
  (`landing_view, signup_started, email_verified, phone_verified, payment_submitted,
  payment_succeeded, instance_provisioned, first_login`), init with `session_recording.maskAllInputs:
  true`, and document the `/ph` first-party reverse-proxy in `web/README.md`. Revenue is captured
  server-side (don't emit payment_succeeded$ from the client).

## Tests
- `web/e2e/signup.spec.ts` (Playwright, mock mode): walk the full funnel to "active"; assert the price
  consent text shows before pay; assert no password/token is rendered into the DOM or any analytics
  call; assert the client never sends tenant_id.
- Keep smoke/dashboard/greenlight specs passing.

## Done when
`cd web && npm install && npm run build` exit 0; `npm run typecheck` clean; `npx playwright test`
passes (all specs). PostHog is a no-op without a key (offline-safe). Update `web/README.md`.

## Constraints
- Touch only web/. No real network in tests (mock mode + PostHog no-op). No secrets/keys in code. No git.
- Honor brand rules (no em-dashes in user-facing copy; say "Managed" not "Claude"). Mask inputs in replay.
