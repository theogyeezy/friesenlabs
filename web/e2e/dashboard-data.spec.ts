import { test, expect } from "@playwright/test";

// Data-plane e2e — proves the dashboard's SpecRenderer renders REAL rows through
// the injected data loader, not the "No data yet" stub.
//
// Background (the headline fix): every dashboard/report/Balto panel used to
// render "No data yet" because the SpecRenderer loadData prop was a hard stub
// (`noLiveData = async () => []`) in DashboardView/DashboardsView/ReportsView/
// ChatDock. The fix wires a real loader:
//   * real mode  -> a loader built from POST /views/{id}/data (the saved spec's
//                   CubeQueries resolved as the verified tenant; see
//                   api/cube_data_routes.py + client.ts buildViewDataLoader),
//   * mock mode  -> the offline sampleLoadData fixture (canned, deterministic).
// Both paths flow through the SAME injected-loader wiring this test exercises.
//
// This spec runs in the `chromium` project against the MOCK build (port 4173):
// the dashboard mounts, the loader resolves rows, and every panel shows real
// numbers — not the empty state. The 503/error DEGRADE path is covered by the
// real-mode reports.spec.ts (no live data plane -> "No data yet"), and the
// loadViewData/buildViewDataLoader contract is unit-true via the typed client.

test("the dashboard renders REAL rows through the injected loader, not 'No data yet'", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  // ?view=dashboard mounts the API-wired DashboardView (the mock seam used by
  // balto.spec). In real mode this same component lives on the Command Center.
  await page.goto("/?view=dashboard");

  await expect(page.getByTestId("dashboard-view")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("spec-renderer")).toBeVisible({ timeout: 15_000 });

  // The KPI renders its REAL measure value through the loader — not the empty
  // "No data yet" state. (Proves the loadData prop resolves rows end to end.)
  const kpiValue = page.getByTestId("kpi-value").first();
  await expect(kpiValue).toBeVisible({ timeout: 15_000 });
  await expect(kpiValue).toHaveText(/[0-9]/, { timeout: 15_000 });
  await expect(page.getByTestId("kpi-empty")).toHaveCount(0);

  // The chart draws an <svg> from the loaded rows — not the empty state.
  const chartHost = page.getByTestId("chart-host").first();
  await expect(chartHost).toBeVisible({ timeout: 15_000 });
  await expect(chartHost.locator("svg").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("chart-empty")).toHaveCount(0);

  // No whole-view error wall, no fallback, and no raw "API <code>" in the DOM.
  await expect(page.getByTestId("dashboard-error")).toHaveCount(0);
  await expect(page.getByTestId("spec-fallback")).toHaveCount(0);
  const text = await page.evaluate(() => document.body.innerText);
  expect(text).not.toMatch(/API \d+/);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});
