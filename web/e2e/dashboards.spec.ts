import { test, expect, type Page } from "@playwright/test";

// Dashboards screen e2e — fully offline, against the REAL production bundle
// (chromium-real project, VITE_API_MOCK=0 baked at build time, auth inert).
// Every API call is intercepted with page.route — no real network, no server.
// Asserts the named-dashboard composition end to end:
//   1. /?view=dashboards lists the tenant's dashboards from GET /dashboards,
//   2. opening one loads GET /dashboards/{id} and renders EVERY referenced view
//      through the trusted SpecRenderer on the grid/span layout — real mode has
//      no live data plane, so each block honestly shows "No data yet",
//   3. a missing referenced view degrades to a per-panel notice, the rest renders,
//   4. creating a dashboard (name + picked views) POSTs a kind=dashboard spec
//      (save), then re-loads and opens it (load) — the save/load round trip,
//   5. a fresh tenant sees the honest empty state; a 500 renders friendly copy
//      with a working retry and never the raw "API <code>" string.
//
// NOTE on routing: the document lives at /?view=dashboards, which a plain
// "**/dashboards" glob would ALSO match (** spans the query string). Every API
// stub therefore matches on url.pathname exclusively (same as reports.spec.ts).

const isListDashboards = (url: URL) => url.pathname === "/dashboards";
const isGetDashboard = (url: URL) => /^\/dashboards\/[^/]+$/.test(url.pathname);
const isListViews = (url: URL) => url.pathname === "/views";
const isGetView = (url: URL) => /^\/views\/[^/]+$/.test(url.pathname);

const PIPELINE_ROW = {
  tenant_id: "tenant-e2e",
  view_id: "demo_pipeline",
  version: 1,
  spec_json: {
    view_id: "demo_pipeline",
    title: "Pipeline overview",
    version: 1,
    semantic_refs: ["Deals.pipeline_value", "Deals.count", "Deals.stage"],
    layout: [
      { type: "kpi", title: "Open pipeline", metric: "Deals.pipeline_value" },
      { type: "kpi", title: "Open deals", metric: "Deals.count" },
    ],
  },
  semantic_refs: ["Deals.pipeline_value", "Deals.count", "Deals.stage"],
  source_prompt: "Show me total pipeline",
  created_by: "e2e",
};

const FUNNEL_ROW = {
  tenant_id: "tenant-e2e",
  view_id: "stage_funnel",
  version: 2,
  spec_json: {
    view_id: "stage_funnel",
    title: "Stage funnel",
    version: 2,
    spec_version: 2,
    semantic_refs: ["Deals.count", "Deals.stage"],
    layout: [
      {
        type: "funnel",
        title: "Deals by stage",
        span: 12,
        query: { measures: ["Deals.count"], dimensions: ["Deals.stage"] },
      },
    ],
  },
  semantic_refs: ["Deals.count", "Deals.stage"],
  source_prompt: "Show the stage funnel",
  created_by: "e2e",
};

const DASHBOARD_SPEC = {
  kind: "dashboard",
  view_id: "exec_overview",
  title: "Executive overview",
  version: 1,
  spec_version: 2,
  grid: { columns: 12 },
  items: [
    { view_id: "demo_pipeline", span: 6 },
    { view_id: "stage_funnel", span: 6 },
  ],
};

const DASHBOARD_ROW = {
  tenant_id: "tenant-e2e",
  view_id: "exec_overview",
  version: 1,
  spec_json: DASHBOARD_SPEC,
  semantic_refs: [],
  source_prompt: "",
  created_by: "e2e",
};

const RESOLVED = {
  dashboard: DASHBOARD_ROW,
  views: { demo_pipeline: PIPELINE_ROW, stage_funnel: FUNNEL_ROW },
};

async function bodyText(page: Page): Promise<string> {
  return page.evaluate(() => document.body.innerText);
}

test("dashboards gallery lists the tenant's dashboards", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route(isListDashboards, (route) =>
    route.fulfill({ json: { dashboards: [DASHBOARD_ROW] } })
  );
  await page.route(isListViews, (route) =>
    route.fulfill({ json: { views: [PIPELINE_ROW, FUNNEL_ROW] } })
  );

  await page.goto("/?view=dashboards");
  await expect(page.getByTestId("dashboards-view")).toBeVisible({ timeout: 15_000 });

  const card = page.getByTestId("dashboard-card");
  await expect(card).toHaveCount(1);
  await expect(card).toContainText("Executive overview");
  await expect(card).toContainText("2 views");

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("opening a dashboard renders every referenced view via the trusted SpecRenderer", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route(isListDashboards, (route) =>
    route.fulfill({ json: { dashboards: [DASHBOARD_ROW] } })
  );
  await page.route(isListViews, (route) =>
    route.fulfill({ json: { views: [PIPELINE_ROW, FUNNEL_ROW] } })
  );
  await page.route(isGetDashboard, (route) => route.fulfill({ json: RESOLVED }));

  await page.goto("/?view=dashboards");
  await page.getByTestId("dashboard-card").click();

  await expect(page.getByTestId("dashboard-open")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("dashboard-version")).toContainText("version 1");

  // Two panels, each a full SpecRenderer mount of the referenced view.
  await expect(page.getByTestId("dashboard-panel")).toHaveCount(2);
  await expect(page.getByTestId("spec-renderer")).toHaveCount(2);
  const text = await bodyText(page);
  expect(text).toContain("Pipeline overview");
  expect(text).toContain("Stage funnel");

  // Real mode has no live data plane: panels honestly say "No data yet" —
  // never demo numbers, never blank panels.
  await expect(page.getByTestId("kpi-empty").first()).toContainText("No data yet");
  await expect(page.getByTestId("funnel-empty")).toBeVisible();

  // Back returns to the gallery.
  await page.getByTestId("dashboard-back").click();
  await expect(page.getByTestId("dashboards-gallery")).toBeVisible();

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("a missing referenced view degrades to a per-panel notice", async ({ page }) => {
  await page.route(isListDashboards, (route) =>
    route.fulfill({ json: { dashboards: [DASHBOARD_ROW] } })
  );
  await page.route(isListViews, (route) =>
    route.fulfill({ json: { views: [PIPELINE_ROW] } })
  );
  // The funnel view vanished server-side: resolve returns only the pipeline.
  await page.route(isGetDashboard, (route) =>
    route.fulfill({
      json: { dashboard: DASHBOARD_ROW, views: { demo_pipeline: PIPELINE_ROW } },
    })
  );

  await page.goto("/?view=dashboards");
  await page.getByTestId("dashboard-card").click();

  await expect(page.getByTestId("dashboard-open")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("dashboard-panel")).toHaveCount(2);
  // One real render + one honest notice; the dashboard never hard-fails.
  await expect(page.getByTestId("spec-renderer")).toHaveCount(1);
  await expect(page.getByTestId("dashboard-panel-missing")).toHaveCount(1);
  await expect(page.getByTestId("dashboard-panel-missing")).toContainText("View not available");
});

test("creating a dashboard saves a kind=dashboard spec and opens it", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  let saved: Record<string, unknown> | null = null;
  const dashboards: Array<typeof DASHBOARD_ROW> = [];

  await page.route(isListViews, (route) =>
    route.fulfill({ json: { views: [PIPELINE_ROW, FUNNEL_ROW] } })
  );
  await page.route(isListDashboards, (route, request) => {
    if (request.method() === "POST") {
      const body = request.postDataJSON() as { spec: Record<string, unknown> };
      saved = body.spec;
      const row = {
        ...DASHBOARD_ROW,
        view_id: String(body.spec.view_id),
        spec_json: { ...body.spec, version: 1 },
      };
      dashboards.push(row as typeof DASHBOARD_ROW);
      route.fulfill({ json: row });
      return;
    }
    route.fulfill({ json: { dashboards } });
  });
  await page.route(isGetDashboard, (route) =>
    route.fulfill({
      json: {
        dashboard: dashboards[0],
        views: { demo_pipeline: PIPELINE_ROW, stage_funnel: FUNNEL_ROW },
      },
    })
  );

  await page.goto("/?view=dashboards");
  await expect(page.getByTestId("dashboards-empty")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("new-dashboard").click();
  await expect(page.getByTestId("dashboard-composer")).toBeVisible();
  // Create is disabled until a name + at least one view are picked.
  await expect(page.getByTestId("create-dashboard")).toBeDisabled();

  await page.getByTestId("dashboard-name").fill("Morning numbers");
  await page.getByTestId("pick-demo_pipeline").check();
  await page.getByTestId("pick-stage_funnel").check();
  await page.getByTestId("create-dashboard").click();

  // The save/load round trip lands on the open dashboard.
  await expect(page.getByTestId("dashboard-open")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("spec-renderer")).toHaveCount(2);

  // The POSTed spec is a well-formed kind=dashboard composition.
  expect(saved).not.toBeNull();
  const spec = saved as unknown as Record<string, unknown>;
  expect(spec.kind).toBe("dashboard");
  expect(spec.spec_version).toBe(2);
  expect(spec.title).toBe("Morning numbers");
  expect(spec.view_id).toBe("morning_numbers");
  expect(spec.items).toEqual([
    { view_id: "demo_pipeline", span: 6 },
    { view_id: "stage_funnel", span: 6 },
  ]);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("fresh tenant sees the honest empty state", async ({ page }) => {
  await page.route(isListDashboards, (route) => route.fulfill({ json: { dashboards: [] } }));
  await page.route(isListViews, (route) => route.fulfill({ json: { views: [] } }));

  await page.goto("/?view=dashboards");
  await expect(page.getByTestId("dashboards-empty")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("dashboards-empty")).toContainText("No dashboards yet");
  await expect(page.getByTestId("dashboard-card")).toHaveCount(0);
});

test("a 500 renders friendly copy with retry; raw API strings never reach the DOM", async ({ page }) => {
  let failures = 0;
  await page.route(isListDashboards, (route) => {
    failures += 1;
    if (failures === 1) {
      route.fulfill({ status: 500, json: { detail: "boom internal stacktrace" } });
      return;
    }
    route.fulfill({ json: { dashboards: [DASHBOARD_ROW] } });
  });
  await page.route(isListViews, (route) =>
    route.fulfill({ json: { views: [PIPELINE_ROW, FUNNEL_ROW] } })
  );

  await page.goto("/?view=dashboards");
  await expect(page.getByTestId("dashboards-error")).toBeVisible({ timeout: 15_000 });

  const text = await bodyText(page);
  expect(text).not.toContain("boom internal stacktrace");
  expect(text).not.toMatch(/API 500/);

  await page.getByTestId("dashboards-retry").click();
  await expect(page.getByTestId("dashboard-card")).toHaveCount(1, { timeout: 15_000 });
});

test("the sidebar has a Dashboards entry that mounts the screen", async ({ page }) => {
  // The screen must be REACHABLE, not just deep-linkable: the shell's
  // "Insights & admin" nav carries a Dashboards item (window.FL_DATA NAV2)
  // that routes to the API-wired DashboardsView.
  await page.route(isListDashboards, (route) =>
    route.fulfill({ json: { dashboards: [DASHBOARD_ROW] } })
  );
  await page.route(isListViews, (route) =>
    route.fulfill({ json: { views: [PIPELINE_ROW, FUNNEL_ROW] } })
  );
  // The shell lands on Command Center first; give its saved-view fetch an
  // answer so the page settles (content irrelevant to this test).
  await page.route(isGetView, (route) => route.fulfill({ json: PIPELINE_ROW }));

  await page.goto("/");
  const navItem = page.locator(".nav-item", { hasText: "Dashboards" });
  await expect(navItem).toBeVisible({ timeout: 15_000 });
  await navItem.click();

  await expect(page.getByTestId("dashboards-view")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("dashboard-card")).toHaveCount(1);
});
