import { test, expect, type Page } from "@playwright/test";

// Cortex ML health tab e2e — fully offline, against the REAL production bundle.
// Runs in the `chromium-real` Playwright project (VITE_API_MOCK=0 baked at
// build time, Cognito unconfigured so auth is inert and the gate is open).
// Every API call is intercepted with page.route — no real network, no server.
// Asserts the Cortex tab is honest end to end:
//   1. the real shell routes Cortex to the API-wired view (CortexView, not the
//      FLStore CortexDemo prototype, not a ComingSoon placeholder),
//   2. a 404 from /cortex/health (live API image predating the route) renders
//      the calm "rolling out" state with a working refresh, NOT an error wall,
//   3. "no_registry" status renders the degraded "not enabled" panel (NOT green),
//   4. "no_champion" status renders the empty "no model trained yet" state,
//   5. "serving" renders the champion card with version, estimator, and metrics,
//   6. "drifting" gets the warning treatment + stable/degraded drift verdict,
//   7. drift.recent_auc = null shows the honest "insufficient live evidence"
//      reason — never a fabricated number,
//   8. friendly copy for 500s with a working retry; raw "API <code>" never
//      reaches the DOM.
//
// CI has no backend — the view shows its error / not-available path in every
// case: this spec asserts THAT, never a populated dashboard.
//
// NOTE on routing: the document lives at /?view=cortex, which a plain
// "**/cortex/**" glob would also match other paths — stub on url.pathname EXACTLY.

const cortexApi = (url: URL) => url.pathname === "/cortex/health";

const NO_REGISTRY = {
  tenant_id: "t-test",
  status: "no_registry",
  champion: null,
  model_count: 0,
  drift: null,
};

const NO_CHAMPION = {
  tenant_id: "t-test",
  status: "no_champion",
  champion: null,
  model_count: 2,
  drift: null,
};

const SERVING = {
  tenant_id: "t-test",
  status: "serving",
  champion: {
    version: "v3",
    estimator: "GradientBoostingClassifier",
    metrics: { auc: 0.847, f1: 0.723 },
  },
  model_count: 3,
  drift: {
    drift: false,
    recent_auc: 0.831,
    n_outcomes: 142,
    registered_auc: 0.847,
    reason: "ok",
  },
};

const DRIFTING = {
  tenant_id: "t-test",
  status: "drifting",
  champion: {
    version: "v2",
    estimator: "RandomForestClassifier",
    metrics: { auc: 0.81, f1: 0.69 },
  },
  model_count: 2,
  drift: {
    drift: true,
    recent_auc: 0.641,
    n_outcomes: 89,
    registered_auc: 0.81,
    reason: "degraded beyond tolerance",
  },
};

const SERVING_NULL_AUC = {
  tenant_id: "t-test",
  status: "serving",
  champion: {
    version: "v1",
    estimator: "LogisticRegression",
    metrics: { auc: 0.77 },
  },
  model_count: 1,
  drift: {
    drift: false,
    recent_auc: null,
    n_outcomes: 4,
    registered_auc: 0.77,
    reason: "insufficient live evidence: fewer than 10 resolved outcomes",
  },
};

async function bodyText(page: Page): Promise<string> {
  return page.evaluate(() => document.body.innerText);
}

// The app routes via in-app nav (route state starts at "dashboard"; there is no
// ?view= URL routing), so reach Cortex the same way the billing spec reaches
// Settings: land on the dashboard, then click the Cortex nav item. The shell
// reads /views + /approvals on load — stub them empty so the dashboard renders.
async function gotoCortex(page: Page) {
  await page.route("**/views/*", (route) => route.fulfill({ json: { views: [] } }));
  await page.route("**/approvals", (route) => route.fulfill({ json: { approvals: [] } }));
  await page.goto("/");
  await expect(page.getByTestId("dashboard-view")).toBeVisible({ timeout: 15_000 });
  await page.locator(".nav-item", { hasText: "Cortex" }).click();
}

test("404 from /cortex/health renders the honest rollout state with refresh, not an error wall", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  let calls = 0;
  await page.route(cortexApi, async (route) => {
    calls += 1;
    if (calls === 1) {
      await route.fulfill({ status: 404, json: { detail: "Not Found" } });
    } else {
      await route.fulfill({ json: NO_REGISTRY });
    }
  });

  await gotoCortex(page);

  const rollout = page.getByTestId("cortex-rollout");
  await expect(rollout).toBeVisible({ timeout: 15_000 });
  await expect(rollout).toContainText("Cortex health API is rolling out");
  await expect(page.getByTestId("cortex-error")).toHaveCount(0);

  const text = await bodyText(page);
  expect(text).not.toContain("Not Found");
  expect(text).not.toMatch(/API \d+/);

  // Refresh recovers once the API serves the route.
  await page.getByTestId("cortex-rollout-refresh").click();
  await expect(page.getByTestId("cortex-no-registry")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("cortex-rollout")).toHaveCount(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("no_registry status renders the degraded 'not enabled' panel, not a green/active state", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route(cortexApi, (route) => route.fulfill({ json: NO_REGISTRY }));

  await gotoCortex(page);
  await expect(page.getByTestId("cortex-view")).toBeVisible({ timeout: 15_000 });

  const panel = page.getByTestId("cortex-no-registry");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("Cortex isn");
  // Must NOT show a champion card or drift verdict.
  await expect(page.getByTestId("cortex-champion")).toHaveCount(0);
  await expect(page.getByTestId("cortex-drift")).toHaveCount(0);
  await expect(page.getByTestId("cortex-error")).toHaveCount(0);
  await expect(page.getByTestId("coming-soon")).toHaveCount(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("no_champion status renders the calm empty state with model_count", async ({ page }) => {
  await page.route(cortexApi, (route) => route.fulfill({ json: NO_CHAMPION }));

  await gotoCortex(page);
  await expect(page.getByTestId("cortex-view")).toBeVisible({ timeout: 15_000 });

  await expect(page.getByTestId("cortex-no-champion")).toBeVisible();
  await expect(page.getByTestId("cortex-model-count")).toContainText("2");
  await expect(page.getByTestId("cortex-champion")).toHaveCount(0);
  await expect(page.getByTestId("cortex-no-registry")).toHaveCount(0);
});

test("serving status renders champion with version, estimator, and metrics", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route(cortexApi, (route) => route.fulfill({ json: SERVING }));

  await gotoCortex(page);
  await expect(page.getByTestId("cortex-view")).toBeVisible({ timeout: 15_000 });

  await expect(page.getByTestId("cortex-champion")).toBeVisible();
  await expect(page.getByTestId("cortex-champion-version")).toContainText("v3");
  await expect(page.getByTestId("cortex-champion-estimator")).toContainText("GradientBoostingClassifier");
  await expect(page.getByTestId("cortex-model-count")).toContainText("3");

  // Metrics grid must have both auc and f1.
  await expect(page.getByTestId("cortex-metric")).toHaveCount(2);

  // Status badge is SERVING.
  await expect(page.getByTestId("cortex-status-badge")).toContainText("SERVING");

  // Drift card visible, live AUC shown, NOT null.
  await expect(page.getByTestId("cortex-drift")).toBeVisible();
  await expect(page.getByTestId("cortex-drift-auc")).toContainText("0.831");

  // No raw transport strings.
  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("drifting status gets the warning treatment", async ({ page }) => {
  await page.route(cortexApi, (route) => route.fulfill({ json: DRIFTING }));

  await gotoCortex(page);
  await expect(page.getByTestId("cortex-view")).toBeVisible({ timeout: 15_000 });

  await expect(page.getByTestId("cortex-status-badge")).toContainText("DRIFTING");
  await expect(page.getByTestId("cortex-drift-warning")).toBeVisible();
  await expect(page.getByTestId("cortex-drift-auc")).toContainText("0.641");
});

test("drift.recent_auc=null shows honest 'insufficient live evidence' reason, never a fabricated number", async ({ page }) => {
  await page.route(cortexApi, (route) => route.fulfill({ json: SERVING_NULL_AUC }));

  await gotoCortex(page);
  await expect(page.getByTestId("cortex-view")).toBeVisible({ timeout: 15_000 });

  await expect(page.getByTestId("cortex-drift")).toBeVisible();
  // The AUC cell must show the dash placeholder, NOT a number.
  const aucCell = page.getByTestId("cortex-drift-auc");
  await expect(aucCell).toBeVisible();
  const aucText = await aucCell.textContent();
  expect(aucText?.trim()).toBe("—");

  // The reason string must appear.
  await expect(page.getByTestId("cortex-drift-reason")).toBeVisible();
  await expect(page.getByTestId("cortex-drift-reason")).toContainText("insufficient live evidence");

  // No drift warning (drift=false).
  await expect(page.getByTestId("cortex-drift-warning")).toHaveCount(0);
});

test("500 -> friendly copy with retry; raw 'API <code>' never reaches the DOM", async ({ page }) => {
  let calls = 0;
  await page.route(cortexApi, async (route) => {
    calls += 1;
    if (calls === 1) {
      await route.fulfill({ status: 500, json: { detail: "internal error" } });
    } else {
      await route.fulfill({ json: NO_REGISTRY });
    }
  });

  await gotoCortex(page);

  const err = page.getByTestId("cortex-error");
  await expect(err).toBeVisible({ timeout: 15_000 });
  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
  expect(text).not.toContain("internal error");
  await expect(page.getByTestId("cortex-champion")).toHaveCount(0);

  await page.getByTestId("cortex-retry").click();
  await expect(page.getByTestId("cortex-no-registry")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("cortex-error")).toHaveCount(0);
});
