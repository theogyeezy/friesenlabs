import { test, expect } from "@playwright/test";

// Production smoke (runs under playwright.smoke.config.ts against the LIVE site + real backend).
// Self-skips when PROD_SMOKE_URL is unset so it never breaks a normal pipeline; run it after a
// deploy. This is the encoded version of the manual "is the live site actually up?" check — the one
// thing mock-mode e2e structurally cannot verify.
test.skip(!process.env.PROD_SMOKE_URL, "prod smoke is opt-in (set PROD_SMOKE_URL)");

test("the landing page loads over HTTPS with the real title", async ({ page }) => {
  const res = await page.goto("/");
  expect(res?.status(), "landing must return 2xx").toBeLessThan(400);
  await expect(page).toHaveTitle(/Friesen Labs/i);
  await expect(page.locator("main").first()).toBeVisible();
});

test("the API health endpoint is reachable through the edge", async ({ request }) => {
  // /healthz rides the same CloudFront/ALB path the SPA's /api calls use.
  const res = await request.get("/healthz");
  expect(res.status(), "edge /healthz must be 200").toBe(200);
});

test("an unauthenticated API call is rejected (auth wall is up)", async ({ request }) => {
  // The product is RLS-scoped: /api/* with no token must never return tenant data.
  const res = await request.get("/api/approvals");
  expect([401, 403], `unauth /api/approvals returned ${res.status()}`).toContain(res.status());
});
