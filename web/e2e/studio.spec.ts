import { test, expect, type Page } from "@playwright/test";

// Agent Studio e2e — against the REAL production bundle (chromium-real, VITE_API_MOCK=0).
// Every API call is intercepted with page.route — no real network. Covers the audit-P0
// surfaces: the playbook library, Run now (draft-only result honesty), the persisted run
// history panel, and the trigger-dispatch honesty banner (schedule/event playbooks must
// never read as live automation when the leg is off).

const PB_ACTIVE = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "Stale deal nudger",
  version: 2,
  status: "active",
  definition: {
    name: "Stale deal nudger",
    trigger: { kind: "schedule", schedule: "0 13 * * 1" },
    roster: [{ agent: "nadia", tools: ["draft_email"] }],
    autonomy: "L1",
    greenlight: { side_effects: "always_ask" },
  },
  template_id: "stale_deal_nudger",
  created_by: "u1",
  created_at: "2026-06-11T00:00:00Z",
  updated_at: "2026-06-11T00:00:00Z",
  ma_registered: true,
};

const PB_DRAFT = {
  ...PB_ACTIVE,
  id: "22222222-2222-2222-2222-222222222222",
  name: "Welcome drafter",
  status: "draft",
  definition: { ...PB_ACTIVE.definition, name: "Welcome drafter", trigger: { kind: "manual" } },
  ma_registered: false,
};

const RUN_PENDING = {
  id: "33333333-3333-3333-3333-333333333333",
  playbook_id: PB_ACTIVE.id,
  run_id: "r-1",
  status: "pending",
  trigger: { kind: "manual", name: "run-now" },
  record: {
    answer: "Drafted two nudges.",
    actions_proposed: [{ tool_name: "send_email" }, { tool_name: "send_email" }],
    delegations: ["nadia"],
  },
  created_at: "2026-06-11T10:00:00Z",
};

// The digest split: unserved tool calls are NOT drafts — status "incomplete", never
// "awaiting approval" (the live 15:15Z run's read_crm/query_cube case).
const RUN_INCOMPLETE = {
  id: "44444444-4444-4444-4444-444444444444",
  playbook_id: PB_ACTIVE.id,
  run_id: "r-2",
  status: "incomplete",
  trigger: { kind: "schedule", name: "*/15 * * * *" },
  record: {
    answer: "",
    actions_proposed: [],
    calls_unserved: [{ tool: "read_crm" }, { tool: "query_cube" }],
    delegations: ["scout"],
  },
  created_at: "2026-06-12T15:16:00Z",
};

async function shell(page: Page) {
  await page.route("**/views/*", (route) => route.fulfill({ json: { views: [] } }));
  await page.route("**/approvals", (route) => route.fulfill({ json: { approvals: [] } }));
}

async function studioRoutes(page: Page, opts?: { schedulingEnabled?: boolean }) {
  await page.route("**/studio/playbooks", (route) =>
    route.fulfill({
      json: {
        playbooks: [PB_ACTIVE, PB_DRAFT],
        dispatch: { scheduling_enabled: opts?.schedulingEnabled ?? false, events_enabled: true },
      },
    }),
  );
  await page.route("**/studio/templates", (route) => route.fulfill({ json: { templates: [] } }));
}

async function gotoStudio(page: Page) {
  await page.goto("/");
  await expect(page.getByTestId("dashboard-view")).toBeVisible({ timeout: 15_000 });
  await page.locator(".nav-item", { hasText: "Studio" }).click();
  await expect(page.getByTestId("studio-view")).toBeVisible();
}

test("Studio renders the library; an inert schedule trigger is bannered honestly", async ({ page }) => {
  await shell(page);
  await studioRoutes(page); // scheduling OFF -> the active schedule playbook is inert
  await gotoStudio(page);

  await expect(page.getByTestId("playbook-row")).toHaveCount(2);
  // Honesty: the active schedule-playbook is flagged, and the banner explains it.
  await expect(page.getByTestId("studio-dispatch-banner")).toBeVisible();
  await expect(page.getByTestId("playbook-trigger-inert")).toBeVisible();
  // The draft (manual) playbook is NOT flagged.
  await expect(page.getByTestId("playbook-trigger-inert")).toHaveCount(1);
});

test("No banner when the trigger legs are live", async ({ page }) => {
  await shell(page);
  await studioRoutes(page, { schedulingEnabled: true });
  await gotoStudio(page);
  await expect(page.getByTestId("playbook-row")).toHaveCount(2);
  await expect(page.getByTestId("studio-dispatch-banner")).toHaveCount(0);
  await expect(page.getByTestId("playbook-trigger-inert")).toHaveCount(0);
});

test("Run now reports drafts waiting in Greenlight — never 'sent'", async ({ page }) => {
  await shell(page);
  await studioRoutes(page);
  let ran = false;
  await page.route(`**/studio/playbooks/${PB_ACTIVE.id}/run`, (route) => {
    ran = route.request().method() === "POST";
    return route.fulfill({
      json: { ran: true, run: { status: "pending", actions_proposed: [{}, {}], answer: "Drafted." } },
    });
  });
  await gotoStudio(page);

  await page.getByTestId("playbook-run").click();
  await expect(page.getByTestId("studio-notice")).toContainText("2 draft actions now wait in Greenlight");
  await expect(page.getByTestId("studio-notice")).toContainText("Nothing was sent");
  expect(ran).toBe(true);
});

test("Run now degrades honestly when the agent plane is unconfigured", async ({ page }) => {
  await shell(page);
  await studioRoutes(page);
  await page.route(`**/studio/playbooks/${PB_ACTIVE.id}/run`, (route) =>
    route.fulfill({ json: { ran: false, run_reason: "agent plane not configured on this task" } }),
  );
  await gotoStudio(page);
  await page.getByTestId("playbook-run").click();
  await expect(page.getByTestId("studio-notice")).toContainText("Couldn't run");
  await expect(page.getByTestId("studio-notice")).toContainText("agent plane not configured");
});

test("The runs panel lists persisted history and degrades honestly", async ({ page }) => {
  await shell(page);
  await studioRoutes(page);
  await page.route(`**/studio/playbooks/${PB_ACTIVE.id}/runs?*`, (route) =>
    route.fulfill({ json: { runs: [RUN_PENDING, RUN_INCOMPLETE] } }),
  );
  await gotoStudio(page);

  await page.getByTestId("playbook-runs").click();
  await expect(page.getByTestId("playbook-runs-panel")).toBeVisible();
  await expect(page.getByTestId("playbook-run-row")).toHaveCount(2);
  const rows = page.getByTestId("playbook-run-row");
  await expect(rows.nth(0)).toContainText("awaiting approval");
  await expect(rows.nth(0)).toContainText("2 draft action(s) routed to Greenlight");
  // The split: unserved tool calls are honestly "tools unserved" — NEVER "awaiting approval".
  await expect(rows.nth(1)).toContainText("tools unserved");
  await expect(rows.nth(1)).toContainText("2 tool call(s) left unserved — nothing awaits your approval");
  await expect(rows.nth(1)).not.toContainText("awaiting approval");
});

test("Runs panel 503 shows the not-available note, not an error wall", async ({ page }) => {
  await shell(page);
  await studioRoutes(page);
  await page.route(`**/studio/playbooks/${PB_ACTIVE.id}/runs?*`, (route) =>
    route.fulfill({ status: 503, json: { detail: "run history not configured" } }),
  );
  await gotoStudio(page);
  await page.getByTestId("playbook-runs").click();
  await expect(page.getByTestId("playbook-runs-panel")).toContainText(
    "Run history isn't available on this deployment yet",
  );
});
