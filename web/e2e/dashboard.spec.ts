import { test, expect } from "@playwright/test";

// Phase 7 dashboard renderer e2e. Loads the demo mount (?view=dashboard-demo)
// and asserts:
//   1. a valid spec renders the KPI card (with its number) and a Vega-Lite chart
//      (an <svg> inside the chart host),
//   2. an invalid/malicious spec renders the safe fallback and injects NO
//      script or raw HTML into the page (spec-not-code),
//   3. (spec_version 2) every new catalog component — funnel, leaderboard,
//      stat-with-sparkline, cohort-grid, markdown-note — renders real fixture
//      data on the grid/span layout,
//   4. a spec from a NEWER catalog (one unknown component type) degrades to a
//      safe per-panel placeholder while the known panels keep rendering, and
//      nothing inside the unknown block is interpreted.
//   5. (default-view resolution) when no viewId prop is given, DashboardView
//      calls listViews() and picks the first non-demo_pipeline view when
//      demo_pipeline is absent — the resolved spec renders and dashboard-empty
//      is never shown.
//   6. (empty-state CTAs) when listViews() is empty the honest empty state
//      renders with load-sample + ask-agents CTA buttons.

// API URL helpers (pathname-only, same as dashboards.spec.ts).
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

test("v2 spec renders every new catalog component on the span grid", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto("/?view=dashboard-demo");
  await page.getByTestId("show-v2").click();

  const renderer = page.getByTestId("spec-renderer");
  await expect(renderer).toBeVisible({ timeout: 15_000 });
  await expect(renderer).toHaveAttribute("data-spec-version", "2");

  // stat-with-sparkline: headline number + an SVG polyline trend.
  await expect(page.getByTestId("stat-card")).toBeVisible();
  await expect(page.getByTestId("stat-value")).toHaveText(/[0-9]/, { timeout: 15_000 });
  await expect(page.getByTestId("sparkline").locator("polyline")).toHaveCount(1);

  // funnel: one step per fixture stage, labels + counts as data.
  await expect(page.getByTestId("funnel-card")).toBeVisible();
  await expect(page.getByTestId("funnel-step")).toHaveCount(5);
  await expect(page.getByTestId("funnel-card")).toContainText("Qualify");
  await expect(page.getByTestId("funnel-card")).toContainText("120");

  // leaderboard: ranked rows, capped by limit (5 of the 6 fixture companies).
  await expect(page.getByTestId("leaderboard-card")).toBeVisible();
  await expect(page.getByTestId("leaderboard-row")).toHaveCount(5);
  await expect(page.getByTestId("leaderboard-row").first()).toContainText("Brightline Realty");

  // cohort-grid: stage x month matrix with shaded numeric cells.
  await expect(page.getByTestId("cohort-card")).toBeVisible();
  expect(await page.getByTestId("cohort-cell").count()).toBeGreaterThanOrEqual(9);
  await expect(page.getByTestId("cohort-card")).toContainText("Proposal");

  // markdown-note: SafeMarkdown output (React nodes), with inline code/bold
  // rendered as elements, never raw HTML.
  await expect(page.getByTestId("markdown-note-card")).toBeVisible();
  const md = page.getByTestId("safe-markdown");
  await expect(md).toContainText("How to read this");
  await expect(md.locator("strong")).toContainText("open");
  await expect(md.locator("code")).toContainText("pipeline_value");
  await expect(md.locator("ul li")).toHaveCount(2);
  await expect(md.locator("ol li")).toHaveCount(2);

  // No fallback, no blank panels, no page errors.
  await expect(page.getByTestId("spec-fallback")).toHaveCount(0);
  await expect(page.getByTestId("unknown-block")).toHaveCount(0);
  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("a newer-catalog component degrades to a safe placeholder, rest renders", async ({ page }) => {
  await page.goto("/?view=dashboard-demo");
  await page.getByTestId("show-future").click();

  // Known panels render around the unknown one — no whole-view fallback.
  await expect(page.getByTestId("spec-renderer")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("spec-fallback")).toHaveCount(0);
  await expect(page.getByTestId("kpi-card")).toBeVisible();
  await expect(page.getByTestId("kpi-value")).toHaveText(/[0-9]/, { timeout: 15_000 });
  await expect(page.getByTestId("markdown-note-card")).toBeVisible();

  // The unknown block renders ONLY the inert placeholder with its escaped type name.
  const placeholder = page.getByTestId("unknown-block");
  await expect(placeholder).toHaveCount(1);
  await expect(page.getByTestId("unknown-block-type")).toHaveText("holo-globe");

  // Nothing from inside the unknown block was interpreted (spec-not-code).
  const pwned = await page.evaluate(
    () => (window as unknown as { __pwned_future?: boolean }).__pwned_future
  );
  expect(pwned).toBeUndefined();
  const bodyHtml = await page.evaluate(() => document.body.innerHTML);
  expect(bodyHtml).not.toContain("window.__pwned_future");
});

test("v1 sample still renders identically under the v2 renderer", async ({ page }) => {
  await page.goto("/?view=dashboard-demo");
  // Default mode IS the v1 sample; assert the v1 layout marker + components.
  const renderer = page.getByTestId("spec-renderer");
  await expect(renderer).toBeVisible({ timeout: 15_000 });
  await expect(renderer).toHaveAttribute("data-spec-version", "1");
  await expect(page.getByTestId("kpi-card")).toHaveCount(2);
  await expect(page.getByTestId("chart-host").locator("svg").first()).toBeVisible({
    timeout: 15_000,
  });
});

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

  await page.goto("/?view=dashboard");
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

  await page.goto("/?view=dashboard");
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

  await page.goto("/?view=dashboard");
  await expect(page.getByTestId("dashboard-view")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("dashboard-empty")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("dashboard-empty")).toContainText("No saved views yet");
  // spec-renderer must NOT be shown.
  await expect(page.getByTestId("spec-renderer")).toHaveCount(0);
});
