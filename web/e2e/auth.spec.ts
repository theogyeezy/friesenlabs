import { test, expect, type Page } from "@playwright/test";

// Auth flow e2e — FULLY OFFLINE, against the AUTH production bundle.
//
// These specs run in the `chromium-auth` Playwright project (:4175), whose
// webServer builds with VITE_API_MOCK=0 AND Cognito env baked in
// (npm run build:auth) — the same code paths a real authenticated deploy
// ships, with the Hosted UI domain pointed at a `.invalid` TLD host that can
// never resolve. Nothing leaves the machine: page.route intercepts
//   - GET  https://auth-e2e.uplift.invalid/oauth2/authorize  (302 back to the
//     SPA callback, echoing the state the app generated),
//   - POST https://auth-e2e.uplift.invalid/oauth2/token      (canned tokens),
//   - the control-plane API (/views/*, /approvals).
// Covered:
//   1. unauthenticated visitors get the sign-in gate, not the app shell,
//   2. the stubbed Hosted UI round-trip (PKCE callback + code exchange) lands
//      in the authed shell with tokens stored,
//   3. a 401 whose refresh fails ends the session: storage cleared, stale
//      query stripped, back on the sign-in route.

const HOSTED_UI = "https://auth-e2e.uplift.invalid";
const TOKEN_KEY = "uplift_auth_tokens"; // AUTH_TOKEN_STORAGE_KEY in auth/core.js

/** Build an unsigned JWT-shaped token (the SPA decodes, never verifies). */
function fakeJwt(payload: Record<string, unknown>): string {
  const b64url = (s: string) => Buffer.from(s).toString("base64url");
  return [b64url('{"alg":"none"}'), b64url(JSON.stringify(payload)), "sig"].join(".");
}

function freshIdToken(): string {
  return fakeJwt({
    email: "owner@riverside.example",
    "custom:tenant_id": "tenant-e2e",
    token_use: "id",
    exp: Math.floor(Date.now() / 1000) + 3600,
  });
}

/** The sign-in gate: the marketing landing's Sign in control. */
function signInGate(page: Page) {
  return page.locator("a.lp-signin", { hasText: "Sign in" }).first();
}

/** Stub the control plane: empty-but-healthy tenant. */
async function stubApi(page: Page): Promise<void> {
  await page.route("**/views/*", (route) =>
    route.fulfill({ status: 404, json: { detail: "no such view" } }),
  );
  await page.route("**/approvals", (route) => route.fulfill({ json: { approvals: [] } }));
}

test("unauthenticated visitors get the sign-in gate, never the app shell", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await stubApi(page);

  await page.goto("/");

  // The marketing landing with its Sign in control is the whole surface.
  await expect(signInGate(page)).toBeVisible({ timeout: 15_000 });
  // No authed shell, no API-backed surfaces.
  await expect(page.getByTestId("dashboard-view")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Ask agents" })).toHaveCount(0);

  // Deep links to gated surfaces are gated too.
  await page.goto("/?view=greenlight");
  await expect(signInGate(page)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("greenlight-queue")).toHaveCount(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("stubbed Hosted UI round-trip: PKCE callback exchange lands in the authed shell", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await stubApi(page);

  // Authorize: bounce straight back to the registered callback, echoing the
  // state the app stashed (PKCE pair lives in sessionStorage across the hop).
  await page.route(`${HOSTED_UI}/oauth2/authorize*`, (route) => {
    const url = new URL(route.request().url());
    expect(url.searchParams.get("response_type")).toBe("code");
    expect(url.searchParams.get("code_challenge_method")).toBe("S256");
    expect(url.searchParams.get("code_challenge")).toBeTruthy();
    const redirectUri = url.searchParams.get("redirect_uri") ?? "";
    const state = url.searchParams.get("state") ?? "";
    return route.fulfill({
      status: 302,
      headers: { location: `${redirectUri}?code=e2e-auth-code&state=${encodeURIComponent(state)}` },
    });
  });

  // Token endpoint: verify the exchange shape, return canned tokens. The CORS
  // header matters — the SPA calls this cross-origin with fetch().
  let tokenCalls = 0;
  await page.route(`${HOSTED_UI}/oauth2/token`, async (route) => {
    tokenCalls += 1;
    const body = route.request().postData() ?? "";
    const params = new URLSearchParams(body);
    expect(params.get("grant_type")).toBe("authorization_code");
    expect(params.get("code")).toBe("e2e-auth-code");
    expect(params.get("code_verifier")).toBeTruthy();
    return route.fulfill({
      headers: { "access-control-allow-origin": "*" },
      json: {
        id_token: freshIdToken(),
        access_token: "e2e-access-token",
        refresh_token: "e2e-refresh-token",
        token_type: "Bearer",
        expires_in: 3600,
      },
    });
  });

  await page.goto("/");
  await expect(signInGate(page)).toBeVisible({ timeout: 15_000 });
  await signInGate(page).click();

  // Through the (stubbed) Hosted UI and back: the authed shell mounts.
  await expect(page.getByRole("button", { name: "Ask agents" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("dashboard-view")).toBeVisible({ timeout: 15_000 });
  expect(tokenCalls).toBe(1);

  // Tokens are stored under the single documented key; the callback URL was
  // stripped back to the root.
  const stored = await page.evaluate(
    (key) => window.localStorage.getItem(key),
    TOKEN_KEY,
  );
  expect(stored).toBeTruthy();
  expect(JSON.parse(stored as string).refresh_token).toBe("e2e-refresh-token");
  expect(new URL(page.url()).pathname).toBe("/");

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("a 401 whose refresh fails ends the session: cleared storage, back to sign-in", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  // Arrive with a stored session (ID token still inside its lifetime, so no
  // proactive refresh fires — the API 401 is what triggers the refresh).
  await page.addInitScript(
    ({ key, tokens }) => {
      window.localStorage.setItem(key, JSON.stringify(tokens));
    },
    {
      key: TOKEN_KEY,
      tokens: {
        id_token: freshIdToken(),
        access_token: "stale-access-token",
        refresh_token: "revoked-refresh-token",
      },
    },
  );

  // The API rejects the session outright...
  await page.route("**/views/*", (route) =>
    route.fulfill({ status: 401, json: { detail: "unauthorized" } }),
  );
  await page.route("**/approvals", (route) =>
    route.fulfill({ status: 401, json: { detail: "unauthorized" } }),
  );
  // ...and the refresh grant fails too (revoked/rotated-away token).
  let refreshCalls = 0;
  await page.route(`${HOSTED_UI}/oauth2/token`, (route) => {
    refreshCalls += 1;
    return route.fulfill({
      status: 400,
      headers: { "access-control-allow-origin": "*" },
      json: { error: "invalid_grant" },
    });
  });

  // Land on a gated surface with a stale ?view= query in the URL.
  await page.goto("/?view=dashboard");

  // Session-expired path: storage cleared, stale query stripped, sign-in gate.
  await expect(signInGate(page)).toBeVisible({ timeout: 15_000 });
  await expect
    .poll(async () => page.evaluate((key) => window.localStorage.getItem(key), TOKEN_KEY), {
      timeout: 10_000,
    })
    .toBeNull();
  const finalUrl = new URL(page.url());
  expect(finalUrl.pathname).toBe("/");
  expect(finalUrl.search).toBe("");
  expect(refreshCalls).toBeGreaterThanOrEqual(1);

  // No authed surface lingers behind the gate.
  await expect(page.getByTestId("dashboard-view")).toHaveCount(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});
