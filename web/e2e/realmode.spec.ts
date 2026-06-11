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
  // Marketplace is now an API-wired real-mode view (GET /studio/templates) — stub
  // it empty so it renders its honest empty state rather than hitting the network.
  await page.route("**/studio/templates", (route) => route.fulfill({ json: { templates: [] } }));

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

  // Marketplace is LIVE in real mode now: the API-wired MarketplaceView (over
  // GET /studio/templates), NOT the FLStore prototype agent catalog and NOT a
  // ComingSoon panel. With an empty catalog it shows the honest empty state.
  await page.locator(".nav-item", { hasText: "Marketplace" }).click();
  await expect(page.getByTestId("marketplace-empty")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("coming-soon")).toHaveCount(0);

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

test("chat surfaces an honest grounding note when the corpus has no matching documents", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  // Grounding observability (knowledge audit P0): an empty-corpus answer must be
  // distinguishable from a refusal — the turn carries grounding_status and the dock says so.
  await page.route("**/chat", (route) =>
    route.fulfill({
      status: 200,
      json: {
        answer: "Acme looks healthy based on recent activity.",
        citations: [],
        pending_approvals: [],
        slots: {},
        needs_disambiguation: [],
        delegations: [],
        session_id: "s1",
        tenant_id: "t1",
        view_intent: false,
        view_request: null,
        grounding_status: "no_sources_found",
        retrieved_count: 0,
      },
    }),
  );

  await page.goto("/?view=chat");
  await expect(page.getByTestId("chat-dock")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("chat-input").fill("How is Acme doing?");
  await page.getByTestId("chat-send").click();

  const reply = page.getByTestId("chat-msg-agent").last();
  await expect(reply).toContainText("Acme looks healthy", { timeout: 15_000 });
  await expect(page.getByTestId("grounding-note")).toContainText("knowledge base");

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

// --- Security & control surface (real mode) --------------------------------
// The kill switch + autonomy dial PUT real state through /control/*, and each
// control feature-detects a 404 and degrades to a disabled "not yet enabled"
// state rather than faking a working toggle. Decision traces are read-only.

test("Security controls reflect + write real /control state", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  let engaged = false;
  let level = 1;
  await page.route("**/control/killswitch", (route) => {
    if (route.request().method() === "PUT") {
      engaged = JSON.parse(route.request().postData() || "{}").engaged;
    }
    return route.fulfill({ json: { engaged, scope: "global" } });
  });
  await page.route("**/control/autonomy", (route) => {
    if (route.request().method() === "PUT") {
      level = JSON.parse(route.request().postData() || "{}").level;
    }
    return route.fulfill({ json: { level } });
  });
  await page.route("**/control/traces*", (route) =>
    route.fulfill({
      json: {
        traces: [
          { id: "t1", ts: "2026-06-10T14:00:00Z", tool: "send_email", decision: "approved", status: "executed" },
        ],
      },
    }),
  );

  await page.goto("/");
  await expect(page.getByTestId("dashboard-view")).toBeVisible({ timeout: 15_000 });
  await page.locator(".nav-item", { hasText: "Security" }).click();

  const controls = page.getByTestId("security-controls");
  await expect(controls).toBeVisible({ timeout: 15_000 });

  // Kill switch loads LIVE and flips on click (server-confirmed, not optimistic).
  const toggle = page.getByTestId("killswitch-toggle");
  await expect(toggle).toBeEnabled();
  await expect(toggle).toHaveAttribute("aria-checked", "false");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-checked", "true", { timeout: 15_000 });

  // Autonomy reflects level 1, and choosing L2 writes it.
  await expect(page.getByTestId("autonomy-1")).toHaveAttribute("aria-checked", "true");
  await page.getByTestId("autonomy-2").click();
  await expect(page.getByTestId("autonomy-2")).toHaveAttribute("aria-checked", "true", { timeout: 15_000 });

  // Read-only traces render.
  await expect(page.getByTestId("traces-list")).toBeVisible();
  await expect(page.getByTestId("trace-row").first()).toContainText("send_email");

  // No raw transport strings ever surface.
  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("Security controls degrade honestly when /control 404s", async ({ page }) => {
  await page.route("**/control/killswitch", (route) => route.fulfill({ status: 404, body: "nope" }));
  await page.route("**/control/autonomy", (route) => route.fulfill({ status: 404, body: "nope" }));
  await page.route("**/control/traces*", (route) => route.fulfill({ status: 404, body: "nope" }));

  await page.goto("/");
  await expect(page.getByTestId("dashboard-view")).toBeVisible({ timeout: 15_000 });
  await page.locator(".nav-item", { hasText: "Security" }).click();

  await expect(page.getByTestId("security-controls")).toBeVisible({ timeout: 15_000 });

  // Disabled, honest degrade — never a fake working toggle.
  await expect(page.getByTestId("killswitch-unavailable")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("killswitch-toggle")).toBeDisabled();
  await expect(page.getByTestId("autonomy-unavailable")).toBeVisible();
  await expect(page.getByTestId("autonomy-0")).toBeDisabled();
  await expect(page.getByTestId("traces-unavailable")).toBeVisible();

  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
});
