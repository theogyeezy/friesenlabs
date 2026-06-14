import { test, expect, type Page } from "@playwright/test";

// Agents crew roster e2e — fully offline, against the REAL production bundle.
// Runs in the `chromium-real` Playwright project (VITE_API_MOCK=0 baked at
// build time, Cognito unconfigured so auth is inert and the gate is open).
// Every API call is intercepted with page.route — no real network, no server.
// Asserts the Agents tab is honest end to end:
//   1. the real shell routes Agents to the API-wired roster (not the FLStore
//      prototype console, not the ComingSoon placeholder),
//   2. the crew renders all 8 cards (7 specialists + the coordinator,
//      distinguished) straight from GET /agents,
//   3. tool chips carry the registry's policies: auto = green "runs on its
//      own", always_ask = amber "asks first" — the autonomy story visible,
//   4. a provisioned tenant shows the badge with the TRUNCATED environment id
//      tail; an unprovisioned tenant gets the honest "assembles at signup"
//      state (same crew, no live claim),
//   5. a 404 from /agents (live API image predating the route) renders the
//      calm "rolling out" state, NOT an error wall,
//   6. friendly copy for 500s with a working retry; the raw "API <code>"
//      string never reaches the DOM.
//
// NOTE on routing: the document itself lives at /?view=agents, which a plain
// "**/agents" glob would ALSO match (** spans the query string) — so the API
// stub matches on url.pathname === "/agents" exclusively.

const agentsApi = (url: URL) => url.pathname === "/agents";

// Mirrors the OWNED roster definitions (agents/roster.py) + the trusted
// registry's policies (agents/tools/registry.py) — the same truth the API
// serializes, so chip assertions below ARE registry assertions.
const ROSTER = [
  {
    name: "scout",
    role: "Lead research",
    description:
      "You are the lead-research specialist. Enrich and score leads using the tenant's corpus and metrics; score conversion propensity with run_model and surface findings as a saved view with build_view.",
    is_coordinator: false,
    tools: [
      { name: "search_rag", policy: "auto" },
      { name: "query_cube", policy: "auto" },
      { name: "read_crm", policy: "auto" },
      { name: "run_model", policy: "auto" },
      { name: "build_view", policy: "auto" },
    ],
  },
  {
    name: "nadia",
    role: "Outreach drafting",
    description:
      "You draft outreach. Personalize from the tenant's data; calling draft_email STAGES the email in the Greenlight approval queue for a human — it never sends on its own.",
    is_coordinator: false,
    tools: [
      { name: "search_rag", policy: "auto" },
      { name: "read_crm", policy: "auto" },
      { name: "draft_email", policy: "always_ask" },
    ],
  },
  {
    name: "margo",
    role: "Quoting",
    description:
      "You handle quoting. Propose quotes grounded in deal data; issuing requires approval.",
    is_coordinator: false,
    tools: [
      { name: "read_crm", policy: "auto" },
      { name: "query_cube", policy: "auto" },
      { name: "issue_quote", policy: "always_ask" },
    ],
  },
  {
    name: "ledger",
    role: "CRM ops",
    description: "You handle ops and CRM mutations. All mutations route through Greenlight.",
    is_coordinator: false,
    tools: [
      { name: "read_crm", policy: "auto" },
      { name: "update_deal", policy: "always_ask" },
    ],
  },
  {
    name: "echo",
    role: "Follow-ups",
    description:
      "You handle follow-ups. Calling draft_email STAGES the nudge in the Greenlight approval queue for a human — it never sends on its own.",
    is_coordinator: false,
    tools: [
      { name: "read_crm", policy: "auto" },
      { name: "draft_email", policy: "always_ask" },
    ],
  },
  {
    name: "pip",
    role: "Support",
    description: "You handle support questions grounded in the tenant's knowledge.",
    is_coordinator: false,
    tools: [
      { name: "search_rag", policy: "auto" },
      { name: "read_crm", policy: "auto" },
    ],
  },
  {
    name: "critic",
    role: "Review & risk",
    description:
      "You review the team's proposed actions and answers for correctness and risk before they go out.",
    is_coordinator: false,
    tools: [],
  },
];

const COORDINATOR = {
  name: "uplift-orchestrator",
  role: "Coordinator",
  description:
    "You coordinate the Uplift team. Delegate research to scout, outreach drafting to nadia, quoting to margo, follow-ups to echo, support to pip, ops to ledger, and always run the critic before responding.",
  is_coordinator: true,
  tools: [],
  id_tail: "kQ9mXa" as string | null,
};

const PROVISIONED_CREW = {
  provisioned: true,
  environment_id_tail: "e6TBgZ",
  coordinator: COORDINATOR,
  roster: ROSTER,
  count: ROSTER.length,
};

const UNPROVISIONED_CREW = {
  provisioned: false,
  environment_id_tail: null,
  coordinator: { ...COORDINATOR, id_tail: null },
  roster: ROSTER,
  count: ROSTER.length,
};

async function bodyText(page: Page): Promise<string> {
  return page.evaluate(() => document.body.innerText);
}

test("real shell routes Agents to the API-wired roster, not the prototype or ComingSoon", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  // The shell lands on Command Center first — stub its surfaces too.
  await page.route("**/views/*", (route) =>
    route.fulfill({ status: 404, json: { detail: "no such view" } }),
  );
  await page.route("**/approvals", (route) => route.fulfill({ json: { approvals: [] } }));
  await page.route(agentsApi, (route) => route.fulfill({ json: PROVISIONED_CREW }));

  await page.goto("/");
  await expect(page.getByTestId("dashboard-empty")).toBeVisible({ timeout: 15_000 });

  await page.locator(".nav-item", { hasText: /^Agents$/ }).click();
  await expect(page.getByTestId("agents-roster")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("coming-soon")).toHaveCount(0);

  // The crew: 7 specialist cards + the coordinator card, distinguished.
  await expect(page.getByTestId("agent-card")).toHaveCount(7);
  await expect(page.getByTestId("coordinator-card")).toHaveCount(1);
  await expect(page.getByTestId("coordinator-tag")).toContainText("Coordinator");

  const text = await bodyText(page);
  expect(text).toContain("Scout");
  expect(text).toContain("Lead research");
  expect(text).toContain("Uplift orchestrator");
  // No FLStore prototype chrome (the mock console's "agents online" claims).
  expect(text).not.toContain("5 agents online");
  await expect(page.locator(".nav-badge")).toHaveCount(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("tool chips carry the registry's policies: auto green, always_ask amber 'asks first'", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route(agentsApi, (route) => route.fulfill({ json: PROVISIONED_CREW }));

  await page.goto("/?view=agents");
  await expect(page.getByTestId("agents-roster")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("agent-card")).toHaveCount(7);

  // Chip totals mirror the roster definitions: 17 tools, 4 of them gated (ledger update_deal,
  // margo issue_quote, and nadia/echo draft_email — staging an email is gated like any send).
  await expect(page.getByTestId("tool-chip")).toHaveCount(17);
  await expect(page.locator('[data-testid="tool-chip"][data-policy="auto"]')).toHaveCount(13);
  await expect(page.locator('[data-testid="tool-chip"][data-policy="always_ask"]')).toHaveCount(4);

  // The poles of the autonomy story, chip by chip per the registry:
  // ledger's update_deal asks first; scout's search_rag runs on its own.
  const ledger = page.locator('[data-agent-name="ledger"]');
  const updateDeal = ledger.locator('[data-tool="update_deal"]');
  await expect(updateDeal).toHaveAttribute("data-policy", "always_ask");
  await expect(updateDeal).toContainText("asks first");
  const margoQuote = page.locator('[data-agent-name="margo"] [data-tool="issue_quote"]');
  await expect(margoQuote).toHaveAttribute("data-policy", "always_ask");
  const scoutRag = page.locator('[data-agent-name="scout"] [data-tool="search_rag"]');
  await expect(scoutRag).toHaveAttribute("data-policy", "auto");
  // nadia's draft_email STAGES a send_email approval — gated ("asks first"), never auto-sent.
  const nadiaDraft = page.locator('[data-agent-name="nadia"] [data-tool="draft_email"]');
  await expect(nadiaDraft).toHaveAttribute("data-policy", "always_ask");
  await expect(nadiaDraft).toContainText("asks first");

  // The legend explains both colors in plain words.
  const legend = page.getByTestId("policy-legend");
  await expect(legend).toContainText("runs on its own");
  await expect(legend).toContainText("asks first");

  // The critic and the coordinator honestly carry no tools.
  await expect(page.locator('[data-agent-name="critic"] [data-testid="tool-chip"]')).toHaveCount(0);
  await expect(page.locator('[data-testid="coordinator-card"] [data-testid="tool-chip"]')).toHaveCount(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("provisioned tenant shows the badge with the TRUNCATED ids only", async ({ page }) => {
  await page.route(agentsApi, (route) => route.fulfill({ json: PROVISIONED_CREW }));

  await page.goto("/?view=agents");
  await expect(page.getByTestId("crew-provisioned")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("crew-provisioned")).toContainText("Crew provisioned");
  await expect(page.getByTestId("crew-env-tail")).toContainText("…e6TBgZ");
  await expect(page.getByTestId("coordinator-id-tail")).toContainText("…kQ9mXa");
  // No unprovisioned state alongside the live badge.
  await expect(page.getByTestId("crew-unprovisioned")).toHaveCount(0);

  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
});

test("unprovisioned tenant gets the honest 'assembles at signup' state, same crew, no live claim", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route(agentsApi, (route) => route.fulfill({ json: UNPROVISIONED_CREW }));

  await page.goto("/?view=agents");
  const state = page.getByTestId("crew-unprovisioned");
  await expect(state).toBeVisible({ timeout: 15_000 });
  await expect(state).toContainText("Your crew assembles at signup");

  // The same crew definitions still render — but nothing claims to be live.
  await expect(page.getByTestId("agent-card")).toHaveCount(7);
  await expect(page.getByTestId("coordinator-card")).toHaveCount(1);
  await expect(page.getByTestId("crew-provisioned")).toHaveCount(0);
  await expect(page.getByTestId("crew-env-tail")).toHaveCount(0);
  await expect(page.getByTestId("coordinator-id-tail")).toHaveCount(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("404 from /agents renders the honest rollout state, not an error wall", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  let calls = 0;
  await page.route(agentsApi, async (route) => {
    calls += 1;
    if (calls === 1) {
      // The live API image predates the route: FastAPI answers its plain 404.
      await route.fulfill({ status: 404, json: { detail: "Not Found" } });
    } else {
      await route.fulfill({ json: PROVISIONED_CREW });
    }
  });

  await page.goto("/?view=agents");

  const rollout = page.getByTestId("crew-rollout");
  await expect(rollout).toBeVisible({ timeout: 15_000 });
  await expect(rollout).toContainText("Agents API is rolling out");
  await expect(rollout).toContainText("refresh after the next API deploy");
  // NOT an error wall: no error card, no raw status text, no scary copy.
  await expect(page.getByTestId("crew-error")).toHaveCount(0);
  let text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
  expect(text).not.toContain("Not Found");
  expect(text).not.toContain("Something needs another try");

  // Refresh recovers once the API serves the route.
  await page.getByTestId("crew-rollout-refresh").click();
  await expect(page.getByTestId("agent-card")).toHaveCount(7, { timeout: 15_000 });
  await expect(page.getByTestId("crew-rollout")).toHaveCount(0);
  text = await bodyText(page);
  expect(text).toContain("Scout");

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("500 -> friendly copy with retry; raw 'API <code>' never reaches the DOM", async ({ page }) => {
  let calls = 0;
  await page.route(agentsApi, async (route) => {
    calls += 1;
    if (calls === 1) {
      await route.fulfill({ status: 500, json: { detail: "db exploded" } });
    } else {
      await route.fulfill({ json: PROVISIONED_CREW });
    }
  });

  await page.goto("/?view=agents");

  const err = page.getByTestId("crew-error");
  await expect(err).toBeVisible({ timeout: 15_000 });
  await expect(err).toContainText("went wrong on our side");
  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
  expect(text).not.toContain("db exploded");
  // Error and crew cards never render together.
  await expect(page.getByTestId("agent-card")).toHaveCount(0);

  await page.getByTestId("crew-retry").click();
  await expect(page.getByTestId("agent-card")).toHaveCount(7, { timeout: 15_000 });
  await expect(page.getByTestId("crew-error")).toHaveCount(0);
});
