import { test, expect } from "@playwright/test";

// Real-build (chromium-real) DashboardView tests — API stubbed via page.route.

const isListViews = (url: URL) => url.pathname === "/views";
const isGetView = (url: URL) => /^\/views\/[^/]+$/.test(url.pathname);

const PIPELINE_HEALTH_ROW = {
  tenant_id: "tenant-e2e",
  view_id: "pipeline-health",
  version: 1,
  spec_json: {
    view_id: "pipeline-health",
    title: "Pipeline health",
    version: 1,
    semantic_refs: ["Deals.pipeline_value"],
    layout: [
      { type: "kpi", title: "Open pipeline", metric: "Deals.pipeline_value" },
    ],
  },
  semantic_refs: ["Deals.pipeline_value"],
  source_prompt: "Show me pipeline health",
  created_by: "e2e",
};

// ---------------------------------------------------------------------------
// Default-view resolution: DashboardView with no explicit viewId prop calls
// listViews() on mount and resolves the best view id rather than hard-coding
// 'demo_pipeline'. These tests run against the real-mode build (chromium-real)
// with page.route API stubs — no live server needed.
// ---------------------------------------------------------------------------

test("default-view resolution: picks first view when demo_pipeline absent", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  // listViews returns pipeline-health (not demo_pipeline).
  await page.route(isListViews, (route) =>
    route.fulfill({ json: { views: [PIPELINE_HEALTH_ROW] } })
  );
  // demo_pipeline is absent: should never be requested, but guard with 404.
  await page.route(isGetView, (route, request) => {
    const viewId = new URL(request.url()).pathname.split("/").pop();
    if (viewId === "demo_pipeline") {
      route.fulfill({ status: 404, json: { detail: "not found" } });
      return;
    }
    // pipeline-health resolves correctly.
    route.fulfill({ json: PIPELINE_HEALTH_ROW });
  });

  await page.goto("/");
  await expect(page.getByTestId("dashboard-view")).toBeVisible({ timeout: 15_000 });

  // The resolved spec renders through SpecRenderer — empty state is never shown.
  await expect(page.getByTestId("spec-renderer")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("dashboard-empty")).toHaveCount(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("default-view resolution: picks demo_pipeline when present in the list", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  const DEMO_ROW = {
    ...PIPELINE_HEALTH_ROW,
    view_id: "demo_pipeline",
    spec_json: { ...PIPELINE_HEALTH_ROW.spec_json, view_id: "demo_pipeline", title: "Demo pipeline" },
  };

  // listViews returns both: pipeline-health listed first, demo_pipeline second.
  await page.route(isListViews, (route) =>
    route.fulfill({ json: { views: [PIPELINE_HEALTH_ROW, DEMO_ROW] } })
  );
  let fetchedViewId = "";
  await page.route(isGetView, (route, request) => {
    fetchedViewId = new URL(request.url()).pathname.split("/").pop() ?? "";
    route.fulfill({ json: DEMO_ROW });
  });

  await page.goto("/");
  await expect(page.getByTestId("spec-renderer")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("dashboard-empty")).toHaveCount(0);

  // demo_pipeline should be chosen (preferred over the first-row fallback).
  expect(fetchedViewId).toBe("demo_pipeline");

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("empty-state CTAs render when listViews returns empty", async ({ page }) => {
  await page.route(isListViews, (route) =>
    route.fulfill({ json: { views: [] } })
  );

  await page.goto("/");
  await expect(page.getByTestId("dashboard-view")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("dashboard-empty")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("dashboard-empty")).toContainText("No saved views yet");
  // spec-renderer must NOT be shown.
  await expect(page.getByTestId("spec-renderer")).toHaveCount(0);
});
