import { test, expect } from "@playwright/test";

// Status-page e2e — runs on the default MOCK bundle (:4173).
//
// Core invariant: when the API health probe returns "operational" the overall
// rollup MUST be "operational" — an informational/probe-less row whose state
// is "unknown" must NOT drag the rollup down to "degraded".
//
// Mock mode (VITE_API_MOCK !== "0") resolves fetchStatus() from a deterministic
// canned result (api component = "operational") without a network call, so
// these assertions exercise the full render path offline.

test("when API is healthy the status page reports operational (not degraded)", async ({ page }) => {
  await page.goto("/?view=status");
  await expect(page.getByTestId("status-page")).toBeVisible();

  // Wait for the probe to resolve (the "Checking…" loader disappears).
  await expect(page.getByTestId("status-loading")).toHaveCount(0);

  // The headline reflects the rollup: a healthy API probe must read operational,
  // and a probe-less "unknown" component must NOT drag the rollup to "degraded".
  await expect(page.getByTestId("status-headline")).toContainText(/operational/i);
  await expect(page.getByTestId("status-headline")).not.toContainText(/degraded/i);
});

test("overall headline reads 'All systems operational' when API is healthy", async ({ page }) => {
  await page.goto("/?view=status");
  await expect(page.getByTestId("status-page")).toBeVisible();

  // Wait for the probe to resolve.
  await expect(page.getByTestId("status-loading")).toHaveCount(0);

  // Headline must say "All systems operational", not "Some systems may be degraded".
  await expect(page.getByTestId("status-headline")).toContainText(/all systems operational/i);
});

test("status page renders component rows and the refresh control", async ({ page }) => {
  await page.goto("/?view=status");
  await expect(page.getByTestId("status-page")).toBeVisible();

  await expect(page.getByTestId("status-components")).toBeVisible();

  // Refresh is operable and shows a last-checked timestamp afterward.
  await page.getByTestId("status-refresh").click();
  await expect(page.getByTestId("status-checked-at")).toBeVisible();
});
