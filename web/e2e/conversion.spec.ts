import { test, expect, type Page } from "@playwright/test";

// Conversion-path e2e (#120) — FULLY OFFLINE, against the AUTH production
// bundle (chromium-auth project, :4175 — VITE_API_MOCK=0 + Cognito env baked
// in with a `.invalid` Hosted UI host that can never resolve). This is the
// only project where a signed-out visitor sees the marketing landing, i.e.
// exactly what a real deploy serves at "/".
//
// Covered (the #120 routing map):
//   1. no dead anchors — every <a> on the marketing page carries a real href,
//   2. nav section anchors actually scroll (the #pricing cross-check),
//   3. "Get started" reaches the signup funnel (?view=signup),
//   4. "Sign in" triggers a redirect TOWARD the Cognito /authorize URL
//      carrying state + code_challenge (intercepted, never followed) — the
//      SPA's PKCE signIn(), not a bare Hosted-UI URL,
//   5. gated deep links (?view=greenlight …) show the focused sign-in gate
//      when signed out — never the marketing page — and mount the real
//      surface when a session exists.

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

/** Anchors with a missing/empty href attribute, labelled for the failure message. */
async function deadAnchors(page: Page): Promise<string[]> {
  return page.evaluate(() =>
    Array.from(document.querySelectorAll("a"))
      .filter((a) => {
        const href = a.getAttribute("href");
        return href === null || href.trim() === "";
      })
      .map((a) => (a.textContent || a.className || "<unnamed>").trim().slice(0, 60)),
  );
}

test("marketing page has zero dead anchors and the #pricing nav anchor scrolls", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto("/");
  await expect(page.locator(".lp-nav")).toBeVisible({ timeout: 15_000 });

  // 1. deadAnchors == 0 — every anchor (nav, footer, research, sign-in, CTAs)
  // carries a real href.
  const dead = await deadAnchors(page);
  expect(dead, `dead anchors: ${dead.join(" | ")}`).toHaveLength(0);

  // 2. The Pricing nav anchor carries the section href AND scrolling works
  // (normal-scroll page post-#128 — the section must arrive in the viewport).
  const pricingLink = page.locator(".lp-nav-links a", { hasText: "Pricing" });
  await expect(pricingLink).toHaveAttribute("href", "#pricing");
  await pricingLink.click();
  await expect
    .poll(() => page.evaluate(() => window.scrollY), { timeout: 10_000 })
    .toBeGreaterThan(100);
  await expect
    .poll(
      () =>
        page.evaluate(() => {
          const el = document.getElementById("pricing");
          return el ? Math.abs(el.getBoundingClientRect().top) : Number.MAX_SAFE_INTEGER;
        }),
      { timeout: 10_000 },
    )
    .toBeLessThan(200);
  expect(new URL(page.url()).hash).toBe("#pricing");

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("Get started reaches the signup funnel", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto("/");

  // The hero CTA is a real link to the funnel too.
  await expect(
    page.locator(".lp-hero-cta a.btn-primary", { hasText: "Build your suite" }),
  ).toHaveAttribute("href", "/?view=signup");

  await page.locator(".lp-nav-cta a.btn-primary", { hasText: "Get started" }).click();

  const flow = page.getByTestId("signup-flow");
  await expect(flow).toBeVisible({ timeout: 15_000 });
  await expect(flow).toHaveAttribute("data-step", "account");
  expect(new URL(page.url()).searchParams.get("view")).toBe("signup");

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("Sign in redirects toward the Cognito /authorize URL carrying state + code_challenge", async ({
  page,
}) => {
  // Intercept the Hosted UI authorize endpoint — never follow it. The assert
  // is on the OUTGOING URL the SPA built (PKCE pair + CSRF state), proving the
  // nav control runs signIn(), not a bare hand-built Hosted-UI link.
  let authorizeUrl: string | null = null;
  await page.route(`${HOSTED_UI}/oauth2/authorize*`, (route) => {
    authorizeUrl = route.request().url();
    return route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<title>intercepted</title>authorize intercepted",
    });
  });

  await page.goto("/");
  const origin = new URL(page.url()).origin;

  const signInLink = page.locator("a.lp-signin", { hasText: "Sign in" }).first();
  await expect(signInLink).toBeVisible({ timeout: 15_000 });
  await signInLink.click();

  await expect.poll(() => authorizeUrl, { timeout: 15_000 }).not.toBeNull();
  const u = new URL(authorizeUrl as unknown as string);
  expect(`${u.protocol}//${u.host}`).toBe(HOSTED_UI);
  expect(u.pathname).toBe("/oauth2/authorize");
  expect(u.searchParams.get("response_type")).toBe("code");
  expect(u.searchParams.get("client_id")).toBe("e2e-client-id");
  expect(u.searchParams.get("redirect_uri")).toBe(`${origin}/auth/callback`);
  expect(u.searchParams.get("state")).toBeTruthy();
  expect(u.searchParams.get("code_challenge")).toBeTruthy();
  expect(u.searchParams.get("code_challenge_method")).toBe("S256");
});

test("gated deep link shows the focused sign-in gate when signed out — not the marketing page", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto("/?view=greenlight");

  // The focused gate, with the sign-in-gate contract control (a.lp-signin).
  await expect(page.getByTestId("signin-gate")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator("a.lp-signin", { hasText: "Sign in" })).toBeVisible();
  // Not the gated surface, and not the marketing page.
  await expect(page.getByTestId("greenlight-queue")).toHaveCount(0);
  await expect(page.locator(".lp-nav")).toHaveCount(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("Email-us lead posts to /public/leads and confirms only on 2xx", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  let posted: { kind?: string; name?: string; email?: string } | null = null;
  await page.route("**/public/leads", (route) => {
    posted = JSON.parse(route.request().postData() || "{}");
    return route.fulfill({ json: { ok: true, id: "lead_1" } });
  });

  await page.goto("/");
  await page.locator("button", { hasText: "Email us" }).first().click();

  await page.getByTestId("email-name").fill("Dana Okafor");
  await page.getByTestId("email-email").fill("dana@birch.example");
  await page.getByTestId("email-message").fill("Want to automate quoting.");
  await page.getByTestId("email-submit").click();

  // Honest confirmation only after a real 2xx.
  await expect(page.getByTestId("lead-confirm")).toBeVisible({ timeout: 15_000 });
  expect(posted?.kind).toBe("email");
  expect(posted?.email).toBe("dana@birch.example");
  // The trust rule: no tenant_id ever leaves the client.
  expect(JSON.stringify(posted)).not.toContain("tenant_id");

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("Email-us lead degrades to a mailto fallback on 404 (no false confirmation)", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  // The route isn't deployed yet → 404 on every attempt (incl. the retry).
  await page.route("**/public/leads", (route) => route.fulfill({ status: 404, body: "not found" }));

  await page.goto("/");
  await page.locator("button", { hasText: "Email us" }).first().click();
  await page.getByTestId("email-name").fill("Dana Okafor");
  await page.getByTestId("email-email").fill("dana@birch.example");
  await page.getByTestId("email-submit").click();

  // No fake "we'll get back to you" — instead an honest mailto fallback.
  await expect(page.getByTestId("lead-fallback")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("lead-confirm")).toHaveCount(0);
  const href = await page.getByTestId("lead-mailto").getAttribute("href");
  expect(href).toMatch(/^mailto:[^?]+@/);
  expect(href).toContain("dana%40birch.example");

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("gated deep link mounts the real surface when a session exists (SPA precedence)", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.addInitScript(
    ({ key, tokens }) => {
      window.localStorage.setItem(key, JSON.stringify(tokens));
    },
    {
      key: TOKEN_KEY,
      tokens: {
        id_token: freshIdToken(),
        access_token: "e2e-access-token",
        refresh_token: "e2e-refresh-token",
      },
    },
  );
  await page.route("**/approvals", (route) => route.fulfill({ json: { approvals: [] } }));

  await page.goto("/?view=greenlight");

  await expect(page.getByTestId("greenlight-queue")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("signin-gate")).toHaveCount(0);
  await expect(page.locator(".lp-nav")).toHaveCount(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});
