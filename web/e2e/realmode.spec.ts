import { test, expect, type Page } from "@playwright/test";

// Real-mode states e2e — fully offline, against the REAL production bundle.
// These specs run in the `chromium-real` Playwright project, whose webServer
// builds with VITE_API_MOCK=0 baked in (npm run build:real) — exactly what a
// production deploy ships. There is no runtime URL seam: the old `?apimock=0`
// param was removed so a deployed bundle's mode can never be flipped from the
// URL. Every API call is intercepted with page.route, so no real network and
// no server are involved; Cognito is unconfigured in this build, so auth is
// inert and the sign-in gate is open. Asserts:
//   1. the real shell mounts the ApiClient-backed surfaces (Command Center ->
//      DashboardView, Greenlight -> GreenlightQueue, Ask agents -> ChatDock),
//   2. KPI/chart blocks show an explicit "No data yet" — never the offline
//      demo fixture numbers (sampleLoadData stays mock-only),
//   3. every other route renders the honest "isn't live yet" panel instead of
//      an FLStore prototype screen, and no prototype chrome (fake badges,
//      "5 agents online" rail, scripted notifications, onboarding) appears,
//   4. /chat 503 renders "Agents unavailable" copy,
//   5. loading spinners, "Inbox zero" / "No saved views yet" empty states,
//   6. friendly copy for 500/network failures with a working retry,
//   7. the raw "API <code>" string never reaches the user.

// Mirrors the validated view-spec shape (web/src/dashboard/viewSpec.ts). In
// real mode there is no live data plane yet, so the KPI blocks must render
// "No data yet" rather than resolving demo numbers.
const VIEW_SPEC = {
  view_id: "demo_pipeline",
  title: "Pipeline overview",
  version: 1,
  source_prompt: "Show me total pipeline and value by stage",
  semantic_refs: ["Deals.totalValue", "Deals.count"],
  layout: [
    { type: "kpi", title: "Open pipeline", metric: "Deals.totalValue" },
    { type: "kpi", title: "Open deals", metric: "Deals.count" },
  ],
};

const VIEW_ROW = {
  tenant_id: "tenant-e2e",
  view_id: "demo_pipeline",
  version: 1,
  spec_json: VIEW_SPEC,
  semantic_refs: VIEW_SPEC.semantic_refs,
  source_prompt: VIEW_SPEC.source_prompt,
  created_by: "e2e",
};

async function bodyText(page: Page): Promise<string> {
  return page.evaluate(() => document.body.innerText);
}

test("real mode mounts the api-wired surfaces in the shell — no demo data", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route("**/views/*", (route) => route.fulfill({ json: VIEW_ROW }));
  await page.route("**/approvals", (route) => route.fulfill({ json: { approvals: [] } }));

  // Fresh profile, NO localStorage prep: the prototype onboarding/tour must
  // not appear in real mode (they are mock-only overlays).
  await page.goto("/");

  // Command Center renders the API-backed saved view, not the prototype.
  await expect(page.getByTestId("dashboard-view")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("kpi-card").first()).toBeVisible({ timeout: 15_000 });

  // The KPI blocks honestly say "No data yet" — the demo fixture numbers
  // (sampleLoadData: 380,000 / 42) must never render in real mode.
  await expect(page.getByTestId("kpi-empty")).toHaveCount(2, { timeout: 15_000 });
  await expect(page.getByTestId("kpi-empty").first()).toContainText("No data yet");
  const dashText = await bodyText(page);
  expect(dashText).not.toContain("380,000");

  // No prototype chrome: fake nav badges, the "5 agents online" rail, the
  // FLStore command palette trigger, the onboarding overlay.
  await expect(page.locator(".nav-badge")).toHaveCount(0);
  expect(dashText).not.toContain("5 agents online");
  expect(dashText).not.toContain("Search or ask");

  // Greenlight nav -> the ApiClient-backed queue with its honest empty state.
  await page.locator(".nav-item", { hasText: "Greenlight" }).click();
  await expect(page.getByTestId("greenlight-queue")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("gl-empty")).toContainText("Inbox zero");
  await expect(page.getByTestId("pending-count")).toContainText("0 pending");

  // Ask agents -> the API-wired chat dock inside the slide-over (panel gets
  // .show only once opened; the dock itself stays mounted off-screen).
  await page.getByRole("button", { name: "Ask agents" }).click();
  await expect(page.locator(".chat.show").getByTestId("chat-dock")).toBeVisible({ timeout: 15_000 });

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("real mode: non-API routes render the honest 'isn't live yet' panel, not the prototype", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route("**/views/*", (route) => route.fulfill({ json: VIEW_ROW }));
  await page.route("**/approvals", (route) => route.fulfill({ json: { approvals: [] } }));

  await page.goto("/");
  await expect(page.getByTestId("dashboard-view")).toBeVisible({ timeout: 15_000 });

  // Billing (an FLStore prototype screen in mock mode) -> ComingSoon panel.
  // (Pipeline and Contacts are no longer here: they mount the API-wired
  // PipelineBoard / ContactsDirectory in real mode — covered in
  // pipeline.spec.ts / contacts.spec.ts.)
  await page.locator(".nav-item", { hasText: "Billing" }).click();
  await expect(page.getByTestId("coming-soon")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("coming-soon")).toContainText("isn’t live yet");

  // Templates -> ComingSoon panel (an FLStore prototype screen in mock mode).
  // (Reports is no longer here: it mounts the API-wired ReportsView in real
  // mode — the saved-views gallery + spec renderer — covered in reports.spec.ts.)
  await page.locator(".nav-item", { hasText: "Templates" }).click();
  await expect(page.getByTestId("coming-soon")).toBeVisible({ timeout: 15_000 });

  // Marketplace routes to the panel instead of the prototype agent catalog.
  await page.locator(".nav-item", { hasText: "Marketplace" }).click();
  await expect(page.getByTestId("coming-soon")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("coming-soon")).toContainText("Marketplace");

  // The notifications panel is honest: no scripted FLStore feed events.
  // Topbar icon buttons: [0] mobile menu, [1] bell, [2] theme toggle.
  await page.locator("header.topbar .icon-btn").nth(1).click();
  await expect(page.getByTestId("notif-empty")).toContainText("No notifications yet");

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("chat shows 'Agents unavailable' on /chat 503 — never the raw API error", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route("**/chat", (route) =>
    route.fulfill({ status: 503, json: { detail: "agent runtime not configured" } }),
  );

  await page.goto("/?view=chat");
  await expect(page.getByTestId("chat-dock")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("chat-input").fill("What closed this week?");
  await page.getByTestId("chat-send").click();

  const reply = page.getByTestId("chat-msg-agent").last();
  await expect(reply).toContainText("Agents unavailable", { timeout: 15_000 });

  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
  expect(text).not.toContain("agent runtime not configured");

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("greenlight: spinner while loading, friendly 500 copy, retry recovers to empty state", async ({ page }) => {
  let calls = 0;
  await page.route("**/approvals", async (route) => {
    calls += 1;
    if (calls === 1) {
      // Hold the first response open long enough to observe the spinner.
      await new Promise((r) => setTimeout(r, 1_000));
      await route.fulfill({ status: 500, json: { detail: "boom" } });
    } else {
      await route.fulfill({ json: { approvals: [] } });
    }
  });

  await page.goto("/?view=greenlight");

  // Spinner during the in-flight load; no premature "0 pending" claim.
  await expect(page.getByTestId("gl-loading")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("pending-count")).toHaveCount(0);

  // 500 -> friendly copy, never the raw status or server detail.
  const err = page.getByTestId("gl-error");
  await expect(err).toBeVisible({ timeout: 15_000 });
  await expect(err).toContainText("went wrong on our side");
  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
  expect(text).not.toContain("boom");
  expect(text).not.toContain("Internal Server Error");

  // The error state never shows the empty state at the same time.
  await expect(page.getByTestId("gl-empty")).toHaveCount(0);

  // Retry -> the queue loads (empty tenant) -> Inbox zero.
  await page.getByTestId("gl-retry").click();
  await expect(page.getByTestId("gl-empty")).toContainText("Inbox zero", { timeout: 15_000 });
  await expect(page.getByTestId("gl-error")).toHaveCount(0);
  await expect(page.getByTestId("pending-count")).toContainText("0 pending");
});

test("dashboard: empty tenant (404) shows the empty panel, not an error", async ({ page }) => {
  await page.route("**/views/*", (route) =>
    route.fulfill({ status: 404, json: { detail: "no such view" } }),
  );

  await page.goto("/?view=dashboard");

  await expect(page.getByTestId("dashboard-empty")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("dashboard-empty")).toContainText("No saved views yet");
  await expect(page.getByTestId("dashboard-error")).toHaveCount(0);
  await expect(page.getByTestId("dashboard-loading")).toHaveCount(0);

  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
});

test("dashboard: 500 shows friendly copy with retry; recovery renders the view honestly", async ({ page }) => {
  let calls = 0;
  await page.route("**/views/*", async (route) => {
    calls += 1;
    if (calls === 1) {
      await route.fulfill({ status: 500, json: { detail: "db exploded" } });
    } else {
      await route.fulfill({ json: VIEW_ROW });
    }
  });

  await page.goto("/?view=dashboard");

  const err = page.getByTestId("dashboard-error");
  await expect(err).toBeVisible({ timeout: 15_000 });
  await expect(err).toContainText("went wrong on our side");
  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
  expect(text).not.toContain("db exploded");

  await page.getByTestId("dashboard-retry").click();
  await expect(page.getByTestId("kpi-card").first()).toBeVisible({ timeout: 15_000 });
  // Recovered view still shows honest no-data KPIs, not demo numbers.
  await expect(page.getByTestId("kpi-empty").first()).toContainText("No data yet");
  await expect(page.getByTestId("dashboard-error")).toHaveCount(0);
});

test("network failure shows friendly connection copy, not the transport error", async ({ page }) => {
  await page.route("**/approvals", (route) => route.abort());

  await page.goto("/?view=greenlight");

  const err = page.getByTestId("gl-error");
  await expect(err).toBeVisible({ timeout: 15_000 });
  await expect(err).toContainText("Check your connection");

  const text = await bodyText(page);
  expect(text).not.toContain("Failed to fetch");
  expect(text).not.toMatch(/API \d+/);
});
