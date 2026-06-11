import { test, expect, type Page } from "@playwright/test";

// Self-service billing e2e — fully offline, against the REAL production bundle
// (chromium-real project, VITE_API_MOCK=0). Every API call is intercepted with
// page.route; no real network, no Stripe. Asserts:
//   1. the settings route shows the live Plan & billing panel (GET /billing),
//   2. "Manage billing" POSTs /billing/portal-session and redirects the browser
//      to the returned Stripe portal URL (window.location.assign — stubbed so
//      the test captures the target instead of navigating away),
//   3. a 403 (no customer mapping) disables the button with honest copy,
//   4. a 404 (billing routes not deployed) degrades to an honest notice.

// The dashboard must mount for the shell to render; stub its reads like the
// other real-mode specs.
const VIEW_ROW = {
  tenant_id: "tenant-e2e",
  view_id: "demo_pipeline",
  version: 1,
  spec_json: {
    view_id: "demo_pipeline",
    title: "Pipeline overview",
    version: 1,
    source_prompt: "x",
    semantic_refs: ["Deals.count"],
    layout: [{ type: "kpi", title: "Open deals", metric: "Deals.count" }],
  },
  semantic_refs: ["Deals.count"],
  source_prompt: "x",
  created_by: "e2e",
};

async function stubShell(page: Page) {
  await page.route("**/views/*", (route) => route.fulfill({ json: VIEW_ROW }));
  await page.route("**/approvals", (route) => route.fulfill({ json: { approvals: [] } }));
}

async function gotoSettings(page: Page) {
  await page.goto("/");
  await expect(page.getByTestId("dashboard-view")).toBeVisible({ timeout: 15_000 });
  await page.locator(".nav-item", { hasText: "Settings" }).click();
}

test("manage billing redirects to the Stripe customer portal", async ({ page }) => {
  await stubShell(page);
  await page.route("**/billing", (route) =>
    route.fulfill({ json: { customer: true, plan: "team", status: "active" } }),
  );
  const PORTAL_URL = "https://billing.stripe.com/p/session/bps_e2e";
  let portalPosted = false;
  await page.route("**/billing/portal-session", (route) => {
    portalPosted = route.request().method() === "POST";
    return route.fulfill({ json: { url: PORTAL_URL } });
  });
  // window.location.assign navigates to the Stripe portal — intercept that
  // navigation so the test captures the target instead of leaving the app.
  // (Overriding window.location.assign is unreliable; routing the nav is not.)
  let assignedTo = "";
  await page.route(`${PORTAL_URL}**`, (route) => {
    assignedTo = route.request().url();
    return route.fulfill({ contentType: "text/html", body: "<html><body>stripe portal</body></html>" });
  });

  await gotoSettings(page);

  // The live panel shows the plan + an active status badge.
  await expect(page.getByTestId("billing-panel")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("billing-plan")).toContainText("Team");
  await expect(page.getByTestId("billing-status")).toContainText("Active");

  // Click "Manage billing" -> POST /billing/portal-session -> redirect to Stripe.
  await page.getByTestId("manage-billing").click();
  await expect.poll(() => assignedTo).toBe(PORTAL_URL);
  expect(portalPosted).toBe(true);
});

test("no customer mapping disables the button with honest copy", async ({ page }) => {
  await stubShell(page);
  await page.route("**/billing", (route) =>
    route.fulfill({ json: { customer: false, plan: null, status: "active" } }),
  );

  await gotoSettings(page);

  await expect(page.getByTestId("billing-panel")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("manage-billing")).toBeDisabled();
  await expect(page.getByTestId("billing-panel")).toContainText("nothing to manage");
});

test("billing routes not deployed -> honest not-available state", async ({ page }) => {
  await stubShell(page);
  await page.route("**/billing", (route) =>
    route.fulfill({ status: 404, json: { detail: "Not Found" } }),
  );

  await gotoSettings(page);

  await expect(page.getByTestId("billing-panel")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("billing-panel")).toContainText("isn't available");
  await expect(page.getByTestId("manage-billing")).toHaveCount(0);
});
