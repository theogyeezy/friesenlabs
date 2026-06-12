import { test, expect, type Page } from "@playwright/test";

// Reports tab e2e — fully offline, against the REAL production bundle.
// Runs in the `chromium-real` Playwright project (VITE_API_MOCK=0 baked at
// build time, Cognito unconfigured so auth is inert and the gate is open).
// Every API call is intercepted with page.route — no real network, no server.
// Asserts the Reports tab is honest end to end:
//   1. the real shell routes Reports to the API-wired ReportsView (not the
//      FLStore Reports prototype + DataAssistant overlay, not ComingSoon),
//   2. the saved-views gallery lists the tenant's views from GET /views,
//   3. opening a view renders it through the trusted dashboard SpecRenderer
//      (the closed kpi/chart catalog) and shows its version,
//   4. "ask for a chart" rides POST /views/{id}/refine: a 501 (agent runtime
//      not wired) degrades to the honest "not live yet" state, NOT an error
//      wall; a 200 re-renders the new version,
//   5. a fresh tenant (GET /views -> []) sees the honest empty state,
//   6. a 500 on the list renders friendly copy with a working retry, and the
//      raw "API <code>" string / server detail never reaches the DOM.
//
// NOTE on routing: the document lives at /?view=reports, which a plain
// "**/views" glob would ALSO match (** spans the query string). So every API
// stub matches on url.pathname exclusively.

const isListViews = (url: URL) => url.pathname === "/views";
const isRefine = (url: URL) => /^\/views\/[^/]+\/refine$/.test(url.pathname);
const isGetView = (url: URL) =>
  /^\/views\/[^/]+$/.test(url.pathname) && !url.pathname.endsWith("/refine");
const isSynthesize = (url: URL) => url.pathname === "/views/synthesize";
const isSaveDraft = (url: URL) => /^\/views\/drafts\/[^/]+\/save$/.test(url.pathname);

const PIPELINE_SPEC = {
  view_id: "demo_pipeline",
  title: "Pipeline overview",
  version: 1,
  source_prompt: "Show me total pipeline and value by stage",
  semantic_refs: ["Deals.totalValue", "Deals.count", "Deals.stage"],
  layout: [
    { type: "kpi", title: "Open pipeline", metric: "Deals.totalValue" },
    {
      type: "chart",
      title: "Pipeline value by stage",
      encoding: "vega-lite",
      spec: {
        mark: "bar",
        encoding: {
          x: { field: "stage", type: "nominal", title: "Stage" },
          y: { field: "value", type: "quantitative", title: "Value" },
        },
      },
      query: { measures: ["Deals.totalValue"], dimensions: ["Deals.stage"] },
    },
  ],
};

const PIPELINE_ROW = {
  tenant_id: "tenant-e2e",
  view_id: "demo_pipeline",
  version: 1,
  spec_json: PIPELINE_SPEC,
  semantic_refs: PIPELINE_SPEC.semantic_refs,
  source_prompt: PIPELINE_SPEC.source_prompt,
  created_by: "e2e",
};

const LEADS_ROW = {
  tenant_id: "tenant-e2e",
  view_id: "lead_sources",
  version: 2,
  spec_json: {
    view_id: "lead_sources",
    title: "Lead sources",
    version: 2,
    source_prompt: "Break new deals down by source",
    semantic_refs: ["Deals.count", "Deals.source"],
    layout: [{ type: "kpi", title: "New deals", metric: "Deals.count" }],
  },
  semantic_refs: ["Deals.count", "Deals.source"],
  source_prompt: "Break new deals down by source",
  created_by: "e2e",
};

const LIST = { views: [PIPELINE_ROW, LEADS_ROW] };

async function bodyText(page: Page): Promise<string> {
  return page.evaluate(() => document.body.innerText);
}

// Stub list + get-view so the gallery and detail both resolve.
async function stubViews(page: Page): Promise<void> {
  await page.route(isListViews, (route) => route.fulfill({ json: LIST }));
  await page.route(isGetView, (route) => route.fulfill({ json: PIPELINE_ROW }));
}

test("real shell routes Reports to the API-wired gallery, not the prototype or ComingSoon", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  // Command Center loads first — give it a view + empty approvals.
  await page.route(isGetView, (route) => route.fulfill({ json: PIPELINE_ROW }));
  await page.route(isListViews, (route) => route.fulfill({ json: LIST }));
  await page.route("**/approvals", (route) => route.fulfill({ json: { approvals: [] } }));

  await page.goto("/");
  await expect(page.getByTestId("dashboard-view")).toBeVisible({ timeout: 15_000 });

  await page.locator(".nav-item", { hasText: /^Reports$/ }).click();
  await expect(page.getByTestId("reports-view")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("coming-soon")).toHaveCount(0);

  // The gallery lists both saved views, by title.
  await expect(page.getByTestId("report-card")).toHaveCount(2);
  const text = await bodyText(page);
  expect(text).toContain("Pipeline overview");
  expect(text).toContain("Lead sources");
  // No FLStore Reports prototype chrome (the fabricated analytics screen).
  expect(text).not.toContain("Agent leaderboard");
  expect(text).not.toContain("Revenue influenced");

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("opening a view renders the trusted SpecRenderer (kpi + chart) with its version", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await stubViews(page);

  await page.goto("/?view=reports");
  await expect(page.getByTestId("reports-view")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("report-card").first().click();
  await expect(page.getByTestId("report-detail")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("report-version")).toContainText("version 1");

  // The dashboard renderer draws the closed catalog: a KPI card + a chart card.
  await expect(page.getByTestId("spec-renderer")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("kpi-card").first()).toBeVisible();
  await expect(page.getByTestId("chart-card").first()).toBeVisible();
  // Real mode: no live data plane, so every block honestly says "No data yet" —
  // never a demo number. The chart host stays hidden behind the empty state.
  await expect(page.getByTestId("kpi-empty").first()).toContainText("No data yet");
  await expect(page.getByTestId("chart-empty").first()).toBeVisible();

  // The "ask for a chart" composer is present.
  await expect(page.getByTestId("refine-composer")).toBeVisible();
  await expect(page.getByTestId("refine-input")).toBeVisible();

  // Back returns to the gallery.
  await page.getByTestId("report-back").click();
  await expect(page.getByTestId("reports-gallery")).toBeVisible();

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("'ask for a chart' on 501 degrades to the honest 'not live yet' state, not an error wall", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await stubViews(page);
  await page.route(isRefine, (route) =>
    route.fulfill({ status: 501, json: { detail: "NL refine needs a view_patcher (agent runtime)" } }),
  );

  await page.goto("/?view=reports");
  await page.getByTestId("report-card").first().click();
  await expect(page.getByTestId("report-detail")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("refine-input").fill("make it a line chart for the last 90 days");
  await page.getByTestId("refine-submit").click();

  // Honest unavailable state — informative, NOT an error.
  await expect(page.getByTestId("refine-unavailable")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("report-error")).toHaveCount(0);
  await expect(page.getByTestId("refine-error")).toHaveCount(0);
  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
  expect(text).not.toContain("view_patcher"); // internal detail stays internal
  expect(text).not.toContain("Something needs another try");

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("'ask for a chart' on 200 re-renders the new version", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await stubViews(page);
  const refined = {
    ...PIPELINE_ROW,
    version: 2,
    spec_json: { ...PIPELINE_SPEC, version: 2, source_prompt: "make it a line chart" },
    source_prompt: "make it a line chart",
  };
  await page.route(isRefine, (route) => route.fulfill({ json: refined }));

  await page.goto("/?view=reports");
  await page.getByTestId("report-card").first().click();
  await expect(page.getByTestId("report-version")).toContainText("version 1", { timeout: 15_000 });

  await page.getByTestId("refine-input").fill("make it a line chart");
  await page.getByTestId("refine-submit").click();

  await expect(page.getByTestId("refine-note")).toContainText("version 2", { timeout: 15_000 });
  await expect(page.getByTestId("report-version")).toContainText("version 2");

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("a fresh tenant (no views) sees the honest empty state", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route(isListViews, (route) => route.fulfill({ json: { views: [] } }));

  await page.goto("/?view=reports");
  await expect(page.getByTestId("reports-view")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("reports-empty")).toBeVisible();
  await expect(page.getByTestId("reports-empty")).toContainText("No saved reports yet");
  await expect(page.getByTestId("report-card")).toHaveCount(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("500 on the list -> friendly copy with a working retry; no raw 'API <code>'", async ({ page }) => {
  let calls = 0;
  await page.route(isListViews, async (route) => {
    calls += 1;
    if (calls === 1) {
      await route.fulfill({ status: 500, json: { detail: "db exploded" } });
    } else {
      await route.fulfill({ json: LIST });
    }
  });

  await page.goto("/?view=reports");

  const err = page.getByTestId("reports-error");
  await expect(err).toBeVisible({ timeout: 15_000 });
  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
  expect(text).not.toContain("db exploded");
  await expect(page.getByTestId("report-card")).toHaveCount(0);

  await page.getByTestId("reports-retry").click();
  await expect(page.getByTestId("report-card")).toHaveCount(2, { timeout: 15_000 });
  await expect(page.getByTestId("reports-error")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// New: synthesize (NL "Ask for a chart") + 404/503 degrade
// ---------------------------------------------------------------------------

test("synthesize ok -> draft preview -> save -> gallery reload", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  const DRAFT_SPEC = {
    view_id: "balto_draft_1",
    title: "Pipeline by owner",
    version: 1,
    source_prompt: "pipeline by owner",
    semantic_refs: ["Deals.totalValue", "Deals.owner"],
    layout: [{ type: "kpi", title: "Pipeline", metric: "Deals.totalValue" }],
  };
  const DRAFT_ROW = {
    tenant_id: "tenant-e2e",
    view_id: "balto_draft_1",
    version: 1,
    spec_json: DRAFT_SPEC,
    semantic_refs: DRAFT_SPEC.semantic_refs,
    source_prompt: DRAFT_SPEC.source_prompt,
    created_by: "balto",
  };

  await page.route(isListViews, (route) => route.fulfill({ json: { views: [PIPELINE_ROW] } }));
  await page.route(isSynthesize, (route) =>
    route.fulfill({
      json: { status: "ok", draft_id: "draft-xyz", spec: DRAFT_SPEC },
    }),
  );
  await page.route(isSaveDraft, (route) => route.fulfill({ json: DRAFT_ROW }));

  await page.goto("/?view=reports");
  await expect(page.getByTestId("reports-view")).toBeVisible({ timeout: 15_000 });

  // Open the synthesize composer.
  await page.getByTestId("reports-ask-chart").click();
  await expect(page.getByTestId("report-synthesize-composer")).toBeVisible();

  // Type and submit.
  await page.getByTestId("report-synthesize-input").fill("pipeline by owner");
  await page.getByTestId("report-synthesize-submit").click();

  // Draft preview with SpecRenderer and Save button.
  await expect(page.getByTestId("report-synthesize-preview")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("spec-renderer")).toBeVisible();
  await expect(page.getByTestId("report-synthesize-save")).toBeVisible();

  // Reload the gallery after save to include the new view.
  await page.route(isListViews, (route) =>
    route.fulfill({ json: { views: [PIPELINE_ROW, DRAFT_ROW] } }),
  );

  await page.getByTestId("report-synthesize-save").click();
  // After save, composer closes and gallery reloads.
  await expect(page.getByTestId("report-synthesize-composer")).toHaveCount(0, { timeout: 15_000 });
  await expect(page.getByTestId("report-card")).toHaveCount(2, { timeout: 15_000 });

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("synthesize data_not_found -> honest refusal copy", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route(isListViews, (route) => route.fulfill({ json: { views: [] } }));
  await page.route(isSynthesize, (route) =>
    route.fulfill({
      json: {
        status: "data_not_found",
        message: "no metric can answer that in your connected sources",
      },
    }),
  );

  await page.goto("/?view=reports");
  await expect(page.getByTestId("reports-view")).toBeVisible({ timeout: 15_000 });

  // The empty CTA drives the composer open.
  await page.getByTestId("reports-empty-cta").click();
  await expect(page.getByTestId("report-synthesize-composer")).toBeVisible();

  await page.getByTestId("report-synthesize-input").fill("total revenue by imaginary dimension");
  await page.getByTestId("report-synthesize-submit").click();

  // Honest refusal — no draft preview, no save button.
  await expect(page.getByTestId("report-synthesize-refusal")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("report-synthesize-preview")).toHaveCount(0);
  await expect(page.getByTestId("report-synthesize-save")).toHaveCount(0);

  const text = await bodyText(page);
  expect(text).toContain("no metric can answer");
  expect(text).not.toMatch(/API \d+/);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("synthesize 503 -> unavailable degrade, not an error wall", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route(isListViews, (route) => route.fulfill({ json: { views: [] } }));
  await page.route(isSynthesize, (route) =>
    route.fulfill({ status: 503, json: { detail: "synthesize agent not configured" } }),
  );

  await page.goto("/?view=reports");
  await expect(page.getByTestId("reports-view")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("reports-empty-cta").click();
  await expect(page.getByTestId("report-synthesize-composer")).toBeVisible();

  await page.getByTestId("report-synthesize-input").fill("pipeline by owner");
  await page.getByTestId("report-synthesize-submit").click();

  // Calm unavailable state — not an error wall.
  await expect(page.getByTestId("report-synthesize-unavailable")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("report-synthesize-preview")).toHaveCount(0);

  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
  expect(text).not.toContain("synthesize agent not configured");
  expect(text).not.toContain("Something needs another try");

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("GET /views 404 -> reports-rollout calm panel with refresh; no error wall", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  let calls = 0;
  await page.route(isListViews, async (route) => {
    calls += 1;
    if (calls === 1) {
      await route.fulfill({ status: 404, json: { detail: "Not Found" } });
    } else {
      await route.fulfill({ json: LIST });
    }
  });

  await page.goto("/?view=reports");

  const rollout = page.getByTestId("reports-rollout");
  await expect(rollout).toBeVisible({ timeout: 15_000 });
  await expect(rollout).toContainText("Reports API is rolling out");
  await expect(page.getByTestId("reports-error")).toHaveCount(0);

  const text = await bodyText(page);
  expect(text).not.toContain("Not Found");
  expect(text).not.toMatch(/API \d+/);

  // Refresh recovers once the API serves the route.
  await page.getByTestId("reports-rollout-refresh").click();
  await expect(page.getByTestId("report-card")).toHaveCount(2, { timeout: 15_000 });
  await expect(page.getByTestId("reports-rollout")).toHaveCount(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("GET /views/{id} 404 -> honest 'report not found' empty state, not a red error", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await stubViews(page);
  // The specific view 404s.
  await page.route(isGetView, (route) =>
    route.fulfill({ status: 404, json: { detail: "view not found" } }),
  );

  await page.goto("/?view=reports");
  await expect(page.getByTestId("reports-view")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("report-card").first().click();
  await expect(page.getByTestId("report-detail")).toBeVisible({ timeout: 15_000 });

  // Honest "not found" — not a red error card.
  await expect(page.getByTestId("report-not-found")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("report-error")).toHaveCount(0);

  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
  expect(text).not.toContain("view not found");
  expect(text).not.toContain("Something needs another try");

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});
