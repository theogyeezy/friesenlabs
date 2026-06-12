import { test, expect, type Page } from "@playwright/test";

// Pipeline board e2e — fully offline, against the REAL production bundle.
// Runs in the `chromium-real` Playwright project (VITE_API_MOCK=0 baked at
// build time, Cognito unconfigured so auth is inert and the gate is open).
// Every API call is intercepted with page.route — no real network, no server.
// Asserts the board is honest end to end:
//   1. the real shell routes Pipeline to the API-wired board (not the FLStore
//      prototype, not the ComingSoon placeholder),
//   2. stage columns + deal cards render straight from GET /deals (joined
//      company names, ordered canonical spine, counts),
//   3. clicking a card opens the detail drawer fed by GET /deals/{id} (deal +
//      recent activities),
//   4. queueing a stage move POSTs {to_stage} ONLY (no tenant_id — the trust
//      rule), shows the honest "queued for approval in Greenlight" toast, and
//      keeps rendering the CURRENT stage — the UI never pretends the move
//      happened,
//   5. a 404 from /deals (live API image predating the route) renders the
//      calm "Pipeline API is rolling out" state, NOT an error wall,
//   6. friendly copy for 500s with a working retry; the raw "API <code>"
//      string never reaches the DOM.

const DEAL_A = {
  id: "11111111-1111-1111-1111-111111111111",
  title: "Birchwood platform expansion",
  stage: "negotiation",
  amount: 84000,
  currency: "USD",
  company_id: "c-1",
  contact_id: "p-1",
  company_name: "Birchwood Capital",
  created_at: "2026-06-01T00:00:00+00:00",
};

const DEAL_B = {
  id: "22222222-2222-2222-2222-222222222222",
  title: "Mesa Verde pilot",
  stage: "new",
  amount: 9500,
  currency: "USD",
  company_id: "c-2",
  contact_id: "p-2",
  company_name: "Mesa Verde Health",
  created_at: "2026-06-02T00:00:00+00:00",
};

const STAGE_ORDER = ["new", "qualified", "proposal", "negotiation", "closed_won", "closed_lost"];
const STAGE_LABELS: Record<string, string> = {
  new: "New",
  qualified: "Qualified",
  proposal: "Proposal",
  negotiation: "Negotiation",
  closed_won: "Closed won",
  closed_lost: "Closed lost",
};

function board(deals: Array<typeof DEAL_A>) {
  const stages = STAGE_ORDER.map((stage) => {
    const rows = deals.filter((d) => d.stage === stage);
    return {
      stage,
      label: STAGE_LABELS[stage],
      deals: rows,
      count: rows.length,
      total_amount: rows.reduce((s, d) => s + (d.amount ?? 0), 0),
    };
  });
  return { stages, total: deals.length, stage_order: STAGE_ORDER };
}

const DETAIL_A = {
  deal: { ...DEAL_A, contact_name: "Dana Whitfield", contact_email: "dana@birchwoodcap.example" },
  activities: [
    {
      id: "act-1",
      kind: "call",
      body: "Walked Dana through the security review; she wants RLS docs.",
      occurred_at: "2026-06-05T00:00:00+00:00",
    },
    {
      id: "act-2",
      kind: "email",
      body: "Sent the revised order form (net-45 -> net-30).",
      occurred_at: "2026-06-04T00:00:00+00:00",
    },
  ],
};

const QUEUED = {
  queued: true,
  approval_id: 7,
  status: "pending_approval",
  from_stage: "negotiation",
  to_stage: "closed_won",
  detail: "queued for approval in Greenlight — the deal stays in 'negotiation' until a human approves",
};

async function bodyText(page: Page): Promise<string> {
  return page.evaluate(() => document.body.innerText);
}

test("real shell routes Pipeline to the API-wired board, not the prototype or ComingSoon", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  // The shell lands on Command Center first — stub its surfaces too.
  await page.route("**/views/*", (route) =>
    route.fulfill({ status: 404, json: { detail: "no such view" } }),
  );
  await page.route("**/approvals", (route) => route.fulfill({ json: { approvals: [] } }));
  await page.route("**/deals", (route) => route.fulfill({ json: board([DEAL_A, DEAL_B]) }));

  await page.goto("/");
  await expect(page.getByTestId("dashboard-empty")).toBeVisible({ timeout: 15_000 });

  await page.locator(".nav-item", { hasText: "Pipeline" }).click();
  await expect(page.getByTestId("pipeline-board")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("coming-soon")).toHaveCount(0);

  // Columns in canonical order; cards carry titles + joined company names.
  await expect(page.getByTestId("stage-col")).toHaveCount(6);
  const colStages = await page
    .getByTestId("stage-col")
    .evaluateAll((els) => els.map((el) => el.getAttribute("data-stage")));
  expect(colStages).toEqual(STAGE_ORDER);
  await expect(page.getByTestId("deal-card")).toHaveCount(2);
  await expect(page.getByTestId("pipeline-count")).toContainText("2 open deals");
  const text = await bodyText(page);
  expect(text).toContain("Birchwood Capital");
  expect(text).toContain("Mesa Verde Health");

  // No FLStore prototype chrome: the mock CRM screen's scripted agents/fake
  // badge counts never render here.
  expect(text).not.toContain("Riverside Plumbing");
  await expect(page.locator(".nav-badge")).toHaveCount(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("drawer opens with deal detail + activities; move-stage queues honestly", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  let movePostBody: Record<string, unknown> | null = null;
  let movePostPath = "";

  await page.route("**/deals", (route) => route.fulfill({ json: board([DEAL_A, DEAL_B]) }));
  await page.route("**/deals/*", (route) => route.fulfill({ json: DETAIL_A }));
  await page.route("**/deals/*/move-stage", async (route) => {
    movePostBody = route.request().postDataJSON() as Record<string, unknown>;
    movePostPath = new URL(route.request().url()).pathname;
    await route.fulfill({ json: QUEUED });
  });

  await page.goto("/?view=pipeline");
  await expect(page.getByTestId("deal-card").first()).toBeVisible({ timeout: 15_000 });

  // Open the Birchwood deal -> drawer with detail + activities.
  await page.locator(`[data-deal-id="${DEAL_A.id}"]`).click();
  const drawer = page.getByTestId("deal-drawer");
  await expect(drawer).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("drawer-title")).toContainText("Birchwood platform expansion");
  await expect(page.getByTestId("drawer-stage")).toHaveAttribute("data-stage", "negotiation");
  await expect(page.getByTestId("activity-item")).toHaveCount(2);
  await expect(page.getByTestId("activity-item").first()).toContainText("security review");

  // Queue a move to Closed won.
  await page.getByTestId("move-select").selectOption("closed_won");
  await page.getByTestId("move-queue-btn").click();

  // Honest toast: queued for approval, with a Greenlight link — and the deal
  // STAYS in negotiation everywhere (drawer chip + board column).
  const toast = page.getByTestId("pipeline-toast");
  await expect(toast).toBeVisible({ timeout: 15_000 });
  await expect(toast).toContainText("Queued for approval in Greenlight");
  await expect(toast).toContainText("stays in its current stage");
  await expect(page.getByTestId("toast-greenlight-link")).toBeVisible();
  await expect(page.getByTestId("drawer-stage")).toHaveAttribute("data-stage", "negotiation");
  await expect(page.getByTestId("drawer-pending-move")).toContainText("waiting for approval");

  // The POST hit the right path and carried to_stage ONLY — never a tenant_id
  // (the trust rule).
  expect(movePostPath).toBe(`/deals/${DEAL_A.id}/move-stage`);
  expect(movePostBody).toEqual({ to_stage: "closed_won" });

  // Close the drawer: the card still sits in the negotiation column, with an
  // honest "awaiting approval" badge — never moved client-side.
  await page.getByTestId("drawer-close").click();
  const negotiationCol = page.locator('[data-testid="stage-col"][data-stage="negotiation"]');
  await expect(negotiationCol.locator(`[data-deal-id="${DEAL_A.id}"]`)).toBeVisible();
  const wonCol = page.locator('[data-testid="stage-col"][data-stage="closed_won"]');
  await expect(wonCol.locator(`[data-deal-id="${DEAL_A.id}"]`)).toHaveCount(0);
  await expect(page.getByTestId("deal-pending-move")).toContainText("awaiting approval");

  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("404 from /deals renders the honest rollout state, not an error wall", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  let calls = 0;
  await page.route("**/deals", async (route) => {
    calls += 1;
    if (calls === 1) {
      // The live API image predates the route: FastAPI answers its plain 404.
      await route.fulfill({ status: 404, json: { detail: "Not Found" } });
    } else {
      await route.fulfill({ json: board([DEAL_B]) });
    }
  });

  await page.goto("/?view=pipeline");

  const rollout = page.getByTestId("pipeline-rollout");
  await expect(rollout).toBeVisible({ timeout: 15_000 });
  await expect(rollout).toContainText("Pipeline API is rolling out");
  await expect(rollout).toContainText("refresh after the next API deploy");
  // NOT an error wall: no error card, no raw status text, no scary copy.
  await expect(page.getByTestId("pipeline-error")).toHaveCount(0);
  let text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
  expect(text).not.toContain("Not Found");
  expect(text).not.toContain("Something needs another try");

  // Refresh recovers once the API serves the route.
  await page.getByTestId("pipeline-rollout-refresh").click();
  await expect(page.getByTestId("deal-card")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("pipeline-rollout")).toHaveCount(0);
  text = await bodyText(page);
  expect(text).toContain("Mesa Verde pilot");

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("list 500 -> friendly copy with retry; raw 'API <code>' never reaches the DOM", async ({ page }) => {
  let calls = 0;
  await page.route("**/deals", async (route) => {
    calls += 1;
    if (calls === 1) {
      await route.fulfill({ status: 500, json: { detail: "db exploded" } });
    } else {
      await route.fulfill({ json: board([DEAL_A]) });
    }
  });

  await page.goto("/?view=pipeline");

  const err = page.getByTestId("pipeline-error");
  await expect(err).toBeVisible({ timeout: 15_000 });
  await expect(err).toContainText("went wrong on our side");
  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
  expect(text).not.toContain("db exploded");
  // Error and board never render together.
  await expect(page.getByTestId("deal-card")).toHaveCount(0);

  await page.getByTestId("pipeline-retry").click();
  await expect(page.getByTestId("deal-card")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("pipeline-error")).toHaveCount(0);
});

test("move-stage 409 (gate blocked) surfaces honest copy; nothing claimed queued", async ({ page }) => {
  await page.route("**/deals", (route) => route.fulfill({ json: board([DEAL_A]) }));
  await page.route("**/deals/*", (route) => route.fulfill({ json: DETAIL_A }));
  await page.route("**/deals/*/move-stage", (route) =>
    route.fulfill({ status: 409, json: { detail: "move blocked: kill switch engaged" } }),
  );

  await page.goto("/?view=pipeline");
  await expect(page.getByTestId("deal-card").first()).toBeVisible({ timeout: 15_000 });
  await page.locator(`[data-deal-id="${DEAL_A.id}"]`).click();
  await expect(page.getByTestId("deal-drawer")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("move-select").selectOption("closed_won");
  await page.getByTestId("move-queue-btn").click();

  const err = page.getByTestId("move-error");
  await expect(err).toBeVisible({ timeout: 15_000 });
  await expect(err).toContainText("kill switch engaged"); // the server's human-authored detail
  // No queued claims anywhere; stage untouched.
  await expect(page.getByTestId("pipeline-toast")).toHaveCount(0);
  await expect(page.getByTestId("drawer-pending-move")).toHaveCount(0);
  await expect(page.getByTestId("drawer-stage")).toHaveAttribute("data-stage", "negotiation");
  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
});

test("empty tenant renders the honest empty state, not a fake board", async ({ page }) => {
  await page.route("**/deals", (route) => route.fulfill({ json: board([]) }));

  await page.goto("/?view=pipeline");

  await expect(page.getByTestId("pipeline-empty")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("pipeline-empty")).toContainText("No deals yet");
  await expect(page.getByTestId("pipeline-count")).toContainText("0 open deals");
  await expect(page.getByTestId("deal-card")).toHaveCount(0);
});

test("Cortex score: a real champion score renders the conversion-likelihood badge", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route("**/deals", (route) => route.fulfill({ json: board([DEAL_A]) }));
  await page.route("**/deals/*", (route) => route.fulfill({ json: DETAIL_A }));
  // GET /cortex/score?deal_id=<uuid> for the open deal — a real logged score.
  await page.route("**/cortex/score**", (route) =>
    route.fulfill({
      json: {
        deal_id: DEAL_A.id,
        tenant_id: "t-1",
        status: "scored",
        score: 0.73,
        model_version: 4,
      },
    }),
  );

  await page.goto("/?view=pipeline");
  await expect(page.getByTestId("deal-card").first()).toBeVisible({ timeout: 15_000 });
  await page.locator(`[data-deal-id="${DEAL_A.id}"]`).click();
  await expect(page.getByTestId("deal-drawer")).toBeVisible({ timeout: 15_000 });

  const badge = page.getByTestId("cortex-score-badge");
  await expect(badge).toBeVisible({ timeout: 15_000 });
  await expect(badge).toContainText("73%");
  await expect(badge).toContainText("likely to convert");

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("Cortex score: no champion (503) renders NOTHING — honest degradation, no error", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route("**/deals", (route) => route.fulfill({ json: board([DEAL_A]) }));
  await page.route("**/deals/*", (route) => route.fulfill({ json: DETAIL_A }));
  // The honest no_champion shape: 503 with score null — the client maps it to null.
  await page.route("**/cortex/score**", (route) =>
    route.fulfill({
      status: 503,
      json: {
        deal_id: DEAL_A.id,
        tenant_id: "t-1",
        status: "no_champion",
        score: null,
        model_version: null,
      },
    }),
  );

  await page.goto("/?view=pipeline");
  await expect(page.getByTestId("deal-card").first()).toBeVisible({ timeout: 15_000 });
  await page.locator(`[data-deal-id="${DEAL_A.id}"]`).click();
  await expect(page.getByTestId("deal-drawer")).toBeVisible({ timeout: 15_000 });

  // The drawer loads fully, but NO badge and NO error — Cortex simply isn't surfaced.
  await expect(page.getByTestId("drawer-title")).toContainText("Birchwood platform expansion");
  await expect(page.getByTestId("cortex-score-badge")).toHaveCount(0);
  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
  expect(text).not.toContain("no_champion");

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});
