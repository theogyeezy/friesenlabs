import { test, expect, type Page } from "@playwright/test";

// Workflows tab e2e — fully offline, against the REAL production bundle.
// Runs in the `chromium-real` Playwright project (VITE_API_MOCK=0 baked at
// build time, Cognito unconfigured so auth is inert and the gate is open).
// Every API call is intercepted with page.route — no real network, no server.
// Asserts the Workflows tab is honest end to end:
//   1. the real shell routes Workflows to the API-wired view (not the FLStore
//      drag-and-drop builder prototype, not the ComingSoon placeholder),
//   2. the OWNED provisioning diagram renders all 5 steps in funnel order
//      (signup → verify → pay → provision → activate) with arrows between,
//      straight from GET /workflows — the machine shown by its display NAME,
//      never an ARN,
//   3. recent executions render with name + status badge + timestamps when the
//      API says the feed is available; a still-RUNNING run shows "running",
//   4. executions_available:false is an INFORMATIVE state, not an error: the
//      "pending IAM grant (REQ-009)" reason renders the calm pending banner and
//      "not configured" renders the not-wired banner — the diagram still shows,
//   5. a 404 from /workflows (live API image predating the route) renders the
//      calm "rolling out" state with a working refresh, NOT an error wall,
//   6. friendly copy for 500s with a working retry; the raw "API <code>" string
//      and any ARN/account-id never reach the DOM.
//
// NOTE on routing: the document itself lives at /?view=workflows, which a plain
// "**/workflows" glob would ALSO match (** spans the query string) — so the API
// stub matches on url.pathname === "/workflows" exclusively.

const workflowsApi = (url: URL) => url.pathname === "/workflows";

// An account id + ARNs that must NEVER reach the browser. The real API strips
// these server-side (proven in tests/integration/test_api_workflows.py); the
// mocked responses below never contain them, and the assertions guard the UI
// from ever printing one regardless.
const ACCOUNT_ID = "186052668426";

const STEP_IDS = ["signup", "verify", "pay", "provision", "activate"];

// The OWNED provisioning funnel, exactly as api/workflows_routes.py serializes
// it (the UI renders whatever the API returns; content truth is pinned in the
// pytest suite — here we mirror it so the rendered tab is faithful).
const STEPS = [
  {
    id: "signup",
    label: "Sign up",
    description:
      "An account is created with an email and phone. Nothing is provisioned yet — " +
      "no workspace, no agents, no charge.",
  },
  {
    id: "verify",
    label: "Verify",
    description:
      "Email and phone are both confirmed before payment unlocks (verify-before-pay). " +
      "Verification links and codes are single-use and expire.",
  },
  {
    id: "pay",
    label: "Pay",
    description:
      "Checkout completes and ONLY the cryptographically signed Stripe webhook flips " +
      "the account to paid — never the browser redirect. A re-delivered webhook is a " +
      "no-op: provisioning starts exactly once.",
  },
  {
    id: "provision",
    label: "Provision",
    description:
      "The state machine builds the workspace step by step: tenant record, a dedicated " +
      "Anthropic workspace, the eight-agent crew, identity, and defaults. Every step is " +
      "idempotent (check-then-create) and a mid-failure parks the account for retry.",
  },
  {
    id: "activate",
    label: "Activate",
    description:
      "The terminal flip: the workspace goes live and the crew starts working. From here, " +
      "anything an agent does that touches the outside world routes through Greenlight.",
  },
];

const BASE = {
  machine: { name: "uplift-provisioning", kind: "provisioning" },
  steps: STEPS,
  step_count: 5,
};

const AVAILABLE = {
  ...BASE,
  executions_available: true,
  reason: null,
  recent_executions: [
    {
      name: "provision-acct-aurora",
      status: "SUCCEEDED",
      started_at: "2026-06-09T12:00:00+00:00",
      stopped_at: "2026-06-09T12:00:42+00:00",
    },
    {
      name: "provision-acct-lantern",
      status: "RUNNING",
      started_at: "2026-06-10T09:30:00+00:00",
      stopped_at: null,
    },
    {
      name: "provision-acct-river",
      status: "FAILED",
      started_at: "2026-06-08T08:00:00+00:00",
      stopped_at: "2026-06-08T08:01:07+00:00",
    },
  ],
};

const PENDING_IAM = {
  ...BASE,
  executions_available: false,
  reason: "pending IAM grant (REQ-009)",
  recent_executions: [],
};

const NOT_CONFIGURED = {
  ...BASE,
  executions_available: false,
  reason: "not configured",
  recent_executions: [],
};

async function bodyText(page: Page): Promise<string> {
  return page.evaluate(() => document.body.innerText);
}

// No ARN fragment and no AWS account id may ever reach the DOM, in ANY state.
async function assertNoArnLeak(page: Page): Promise<void> {
  const text = await bodyText(page);
  expect(text).not.toContain("arn:");
  expect(text).not.toContain(ACCOUNT_ID);
  expect(text).not.toMatch(/API \d+/);
}

test("real shell routes Workflows to the API-wired view, not the prototype builder or ComingSoon", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  // The shell lands on Command Center first — stub its surfaces too.
  await page.route("**/views/*", (route) =>
    route.fulfill({ status: 404, json: { detail: "no such view" } }),
  );
  await page.route("**/approvals", (route) => route.fulfill({ json: { approvals: [] } }));
  await page.route(workflowsApi, (route) => route.fulfill({ json: AVAILABLE }));

  await page.goto("/");
  await expect(page.getByTestId("dashboard-empty")).toBeVisible({ timeout: 15_000 });

  await page.locator(".nav-item", { hasText: /^Workflows$/ }).click();
  await expect(page.getByTestId("workflows-view")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("coming-soon")).toHaveCount(0);

  // The OWNED diagram: 5 step cards in funnel order, 4 arrows between them.
  await expect(page.getByTestId("workflow-step")).toHaveCount(5);
  await expect(page.getByTestId("workflow-step-arrow")).toHaveCount(4);
  const ids = await page.getByTestId("workflow-step").evaluateAll((els) =>
    els.map((el) => el.getAttribute("data-step-id")),
  );
  expect(ids).toEqual(STEP_IDS);

  // The machine is named by its display name — never an ARN.
  await expect(page.getByTestId("workflow-machine")).toContainText("uplift-provisioning");

  const text = await bodyText(page);
  expect(text).toContain("Sign up");
  expect(text).toContain("Provision");
  // No FLStore prototype chrome (the mock builder's drag-and-drop canvas copy).
  expect(text).not.toContain("Design a multi-agent workflow");
  expect(text).not.toContain("drop it on the canvas");

  await assertNoArnLeak(page);
  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("recent executions render name + status badge + timestamps; a RUNNING run shows 'running'", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route(workflowsApi, (route) => route.fulfill({ json: AVAILABLE }));

  await page.goto("/?view=workflows");
  await expect(page.getByTestId("workflows-view")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("execution-row")).toHaveCount(3);

  // Status badges mirror the feed, status passed through verbatim.
  const statuses = await page.getByTestId("execution-status").evaluateAll((els) =>
    els.map((el) => el.getAttribute("data-status")),
  );
  expect(statuses).toEqual(["SUCCEEDED", "RUNNING", "FAILED"]);

  const text = await bodyText(page);
  expect(text).toContain("provision-acct-aurora");
  expect(text).toContain("provision-acct-lantern");
  expect(text).toContain("provision-acct-river");
  // The still-running execution renders "running" rather than a stop time.
  expect(text).toContain("running");

  // The unavailable banners must NOT render when the feed is live.
  await expect(page.getByTestId("executions-pending-iam")).toHaveCount(0);
  await expect(page.getByTestId("executions-unavailable")).toHaveCount(0);

  await assertNoArnLeak(page);
  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("pending-IAM (REQ-009) degrades to the calm pending banner — diagram intact, no error wall", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route(workflowsApi, (route) => route.fulfill({ json: PENDING_IAM }));

  await page.goto("/?view=workflows");
  await expect(page.getByTestId("workflows-view")).toBeVisible({ timeout: 15_000 });

  // The diagram STILL renders — the tab stays useful.
  await expect(page.getByTestId("workflow-step")).toHaveCount(5);

  // The pending-IAM banner, INFORMATIVE not error.
  const banner = page.getByTestId("executions-pending-iam");
  await expect(banner).toBeVisible();
  await expect(banner).toHaveAttribute("data-reason", "pending IAM grant (REQ-009)");
  await expect(page.getByTestId("execution-row")).toHaveCount(0);
  // Not an error wall: no error card, no scary copy, no raw reason/ARN leak.
  await expect(page.getByTestId("workflows-error")).toHaveCount(0);
  const text = await bodyText(page);
  expect(text).not.toContain("Something needs another try");
  expect(text).not.toContain("REQ-009"); // the internal grant id stays internal
  await assertNoArnLeak(page);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("not-configured degrades to the not-wired banner — diagram intact", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route(workflowsApi, (route) => route.fulfill({ json: NOT_CONFIGURED }));

  await page.goto("/?view=workflows");
  await expect(page.getByTestId("workflows-view")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("workflow-step")).toHaveCount(5);

  const banner = page.getByTestId("executions-unavailable");
  await expect(banner).toBeVisible();
  await expect(banner).toHaveAttribute("data-reason", "not configured");
  await expect(page.getByTestId("executions-pending-iam")).toHaveCount(0);
  await expect(page.getByTestId("workflows-error")).toHaveCount(0);

  await assertNoArnLeak(page);
  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("404 from /workflows renders the honest rollout state, not an error wall", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  let calls = 0;
  await page.route(workflowsApi, async (route) => {
    calls += 1;
    if (calls === 1) {
      // The live API image predates the route: FastAPI answers its plain 404.
      await route.fulfill({ status: 404, json: { detail: "Not Found" } });
    } else {
      await route.fulfill({ json: AVAILABLE });
    }
  });

  await page.goto("/?view=workflows");

  const rollout = page.getByTestId("workflows-rollout");
  await expect(rollout).toBeVisible({ timeout: 15_000 });
  await expect(rollout).toContainText("Workflows API is rolling out");
  // NOT an error wall: no error card, no raw status text, no scary copy.
  await expect(page.getByTestId("workflows-error")).toHaveCount(0);
  let text = await bodyText(page);
  expect(text).not.toContain("Not Found");
  expect(text).not.toContain("Something needs another try");
  expect(text).not.toMatch(/API \d+/);

  // Refresh recovers once the API serves the route.
  await page.getByTestId("workflows-rollout-refresh").click();
  await expect(page.getByTestId("workflow-step")).toHaveCount(5, { timeout: 15_000 });
  await expect(page.getByTestId("workflows-rollout")).toHaveCount(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("500 -> friendly copy with retry; raw 'API <code>' never reaches the DOM", async ({ page }) => {
  let calls = 0;
  await page.route(workflowsApi, async (route) => {
    calls += 1;
    if (calls === 1) {
      await route.fulfill({ status: 500, json: { detail: "db exploded" } });
    } else {
      await route.fulfill({ json: AVAILABLE });
    }
  });

  await page.goto("/?view=workflows");

  const err = page.getByTestId("workflows-error");
  await expect(err).toBeVisible({ timeout: 15_000 });
  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
  expect(text).not.toContain("db exploded");
  // Error and diagram never render together.
  await expect(page.getByTestId("workflow-step")).toHaveCount(0);

  await page.getByTestId("workflows-retry").click();
  await expect(page.getByTestId("workflow-step")).toHaveCount(5, { timeout: 15_000 });
  await expect(page.getByTestId("workflows-error")).toHaveCount(0);
});
