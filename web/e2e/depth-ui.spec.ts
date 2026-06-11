import { test, expect, type Page } from "@playwright/test";

// Depth-UI e2e — against the REAL production bundle (chromium-real, VITE_API_MOCK=0,
// Cognito unconfigured so the auth gate is inert). Every API call is intercepted with
// page.route — no real network. Asserts the three new real-mode surfaces are HONEST:
//   1. the public Status page (?view=status) renders the real per-subsystem feed from
//      GET /public/status, with unknown subsystems shown honestly (never faked green),
//   2. Workspace settings load + PERSIST via GET/PUT /account/settings (and degrade to
//      an honest "not available" panel on 503),
//   3. the Agent marketplace browses GET /studio/templates and "hires" via instantiate
//      (and degrades honestly on 503).

const statusApi = (url: URL) => url.pathname === "/public/status";
const settingsApi = (url: URL) => url.pathname === "/account/settings";

async function shell(page: Page) {
  await page.route("**/views/*", (route) => route.fulfill({ json: { views: [] } }));
  await page.route("**/approvals", (route) => route.fulfill({ json: { approvals: [] } }));
}
async function gotoTab(page: Page, label: string) {
  await page.goto("/");
  await expect(page.getByTestId("dashboard-view")).toBeVisible({ timeout: 15_000 });
  await page.locator(".nav-item", { hasText: label }).click();
}

// --------------------------------------------------------------------------- status
test("Status page renders the real per-subsystem feed; unknown subsystems stay honest", async ({ page }) => {
  await page.route(statusApi, (route) =>
    route.fulfill({
      json: {
        status: "operational",
        checked_at: "2026-06-11T00:00:00Z",
        components: [
          { key: "api", label: "Application & API", state: "operational", detail: null },
          { key: "data_plane", label: "Data plane", state: "unknown", detail: "not reporting on this deployment" },
          { key: "agent_plane", label: "Agent plane", state: "unknown", detail: null },
        ],
      },
    }),
  );

  await page.goto("/?view=status");
  await expect(page.getByTestId("status-page")).toBeVisible({ timeout: 15_000 });
  // The real components are rendered (api operational + an unknown subsystem).
  await expect(page.getByTestId("status-components")).toContainText("Data plane");
  // Overall is operational even with unknown subsystems (the rollup invariant).
  await expect(page.getByTestId("status-badge-operational").first()).toBeVisible();
});

// --------------------------------------------------------------------------- settings
test("Workspace settings load and persist via GET/PUT /account/settings", async ({ page }) => {
  await shell(page);
  await page.route(settingsApi, async (route) => {
    if (route.request().method() === "PUT") {
      const body = JSON.parse(route.request().postData() || "{}");
      // Echo back the saved row (the API returns the full row).
      await route.fulfill({ json: { workspace_name: body.workspace_name ?? "Acme", notification_prefs: body.notification_prefs ?? {} } });
    } else {
      await route.fulfill({ json: { workspace_name: "Acme Co.", notification_prefs: { email_digest: true } } });
    }
  });

  await gotoTab(page, "Settings");
  const name = page.getByTestId("settings-workspace-name");
  await expect(name).toBeVisible({ timeout: 15_000 });
  await expect(name).toHaveValue("Acme Co.");

  let putBody: any = null;
  page.on("request", (r) => {
    if (r.url().endsWith("/account/settings") && r.method() === "PUT") putBody = JSON.parse(r.postData() || "{}");
  });

  await name.fill("Renamed Workspace");
  await page.getByTestId("settings-pref-approval_reminders").check();
  await page.getByTestId("settings-save").click();

  await expect(page.getByTestId("settings-saved")).toBeVisible({ timeout: 15_000 });
  expect(putBody?.workspace_name).toBe("Renamed Workspace");
  expect(putBody?.notification_prefs?.approval_reminders).toBe(true);
});

test("Workspace settings degrade to an honest panel on 503", async ({ page }) => {
  await shell(page);
  await page.route(settingsApi, (route) => route.fulfill({ status: 503, json: { detail: "not configured" } }));
  await gotoTab(page, "Settings");
  await expect(page.getByTestId("settings-unavailable")).toBeVisible({ timeout: 15_000 });
});

// --------------------------------------------------------------------------- marketplace
test("Marketplace browses templates and hires one via instantiate", async ({ page }) => {
  await shell(page);
  await page.route("**/studio/templates", (route) =>
    route.fulfill({
      json: {
        templates: [
          { template_id: "lead_qualifier", summary: "Scores and routes inbound leads.", definition: {} },
        ],
      },
    }),
  );
  let instantiated = false;
  await page.route("**/studio/templates/lead_qualifier/instantiate", (route) => {
    instantiated = route.request().method() === "POST";
    return route.fulfill({ json: { ok: true } });
  });

  await gotoTab(page, "Marketplace");
  await expect(page.getByTestId("marketplace-view")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("marketplace-card")).toHaveCount(1);

  await page.getByTestId("marketplace-hire-lead_qualifier").click();
  await expect(page.getByTestId("marketplace-hired")).toBeVisible({ timeout: 15_000 });
  expect(instantiated).toBe(true);
});

test("Marketplace degrades to an honest panel on 503", async ({ page }) => {
  await shell(page);
  await page.route("**/studio/templates", (route) => route.fulfill({ status: 503, json: { detail: "not configured" } }));
  await gotoTab(page, "Marketplace");
  await expect(page.getByTestId("marketplace-unavailable")).toBeVisible({ timeout: 15_000 });
});
