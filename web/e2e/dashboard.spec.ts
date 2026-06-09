import { test, expect } from "@playwright/test";

// Phase 7 dashboard renderer e2e. Loads the demo mount (?view=dashboard-demo)
// and asserts:
//   1. a valid spec renders the KPI card (with its number) and a Vega-Lite chart
//      (an <svg> inside the chart host),
//   2. an invalid/malicious spec renders the safe fallback and injects NO
//      script or raw HTML into the page (spec-not-code).

test("valid spec renders KPI + Vega-Lite chart", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto("/?view=dashboard-demo");

  // KPI card renders with its formatted number.
  const kpi = page.getByTestId("kpi-card").first();
  await expect(kpi).toBeVisible({ timeout: 15_000 });
  const kpiValue = page.getByTestId("kpi-value").first();
  await expect(kpiValue).toBeVisible();
  await expect(kpiValue).toHaveText(/[0-9]/, { timeout: 15_000 });

  // Vega-Lite chart renders an <svg> inside the chart host.
  const chartHost = page.getByTestId("chart-host").first();
  await expect(chartHost).toBeVisible({ timeout: 15_000 });
  await expect(chartHost.locator("svg").first()).toBeVisible({ timeout: 15_000 });

  // No fallback for the valid spec.
  await expect(page.getByTestId("spec-fallback")).toHaveCount(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("invalid spec renders safe fallback and injects no script/HTML", async ({ page }) => {
  await page.goto("/?view=dashboard-demo");

  // Switch to the malicious/invalid spec.
  await page.getByTestId("show-invalid").click();

  // The safe fallback appears; no catalog component rendered.
  const fallback = page.getByTestId("spec-fallback");
  await expect(fallback).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("kpi-card")).toHaveCount(0);
  await expect(page.getByTestId("chart-card")).toHaveCount(0);
  await expect(page.getByTestId("table-card")).toHaveCount(0);

  // Spec-not-code: the injected onerror/script never executed, and no element
  // from the spec's raw-HTML strings reached the DOM.
  const pwned = await page.evaluate(() => (window as unknown as { __pwned?: boolean }).__pwned);
  expect(pwned).toBeUndefined();

  // No <script> smuggled from the spec, no injected <img> with the onerror payload.
  const injectedImg = await page.locator('img[onerror]').count();
  expect(injectedImg).toBe(0);
  const bodyHtml = await page.evaluate(() => document.body.innerHTML);
  expect(bodyHtml).not.toContain("window.__pwned");
});
