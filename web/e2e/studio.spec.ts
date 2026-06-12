import { test, expect, type Page } from "@playwright/test";

// Agent Studio e2e — against the REAL production bundle (chromium-real,
// VITE_API_MOCK=0 baked at build time, Cognito unconfigured so auth is inert
// and the gate is open). Every API call is intercepted with page.route — no
// real network, no server.
//
// Asserts the Studio tab is honest end to end:
//   1. nav routes to studio-view (not coming-soon)
//   2. studio-empty on {playbooks:[]}
//   3. studio-rollout + refresh on 404
//   4. studio-unavailable (calm, not an error wall) on 503
//   5. studio-error + retry on 500
//   6. composer open -> invalid JSON feedback data-ok=0 -> valid data-ok=1
//      -> save -> notice 'Playbook created.'
//   7. activate honesty BOTH branches:
//      registered:false -> notice contains 'record-only' and NOT 'its crew is registered'
//      registered:true  -> notice contains 'its crew is registered'
//   8. run BOTH branches:
//      ran:true  -> notice contains 'drafted' AND 'Greenlight' and NOT 'sent'
//      ran:false -> notice contains 'record-only'
//
// NOTE on routing: stub matches on url.pathname === '/studio/playbooks' and
// '/studio/templates' exactly — NOT a broad **/studio* glob which also matches
// /?view=studio (the page URL). This matches the convention used in agents.spec.ts.

const playbooksApi = (url: URL) => url.pathname === "/studio/playbooks";
const templatesApi = (url: URL) => url.pathname === "/studio/templates";

const EMPTY_RESPONSE = { playbooks: [], templates: [] };

const SAMPLE_DEFINITION = {
  name: "Lead qualifier",
  description: "Qualifies inbound leads",
  trigger: { kind: "manual" },
  roster: [{ agent: "scout", tools: ["search_rag"] }],
  autonomy: "L1",
  greenlight: { side_effects: "always_ask" },
};

const DRAFT_PLAYBOOK = {
  id: "pb-001",
  name: "Lead qualifier",
  version: 1,
  status: "draft",
  definition: SAMPLE_DEFINITION,
  template_id: null,
  created_by: null,
  created_at: null,
  updated_at: null,
};

const ACTIVE_PLAYBOOK = {
  ...DRAFT_PLAYBOOK,
  id: "pb-002",
  status: "active",
  name: "Active outreach",
};

function stubBoth(page: Page, playbooks: unknown[], templates: unknown[]) {
  void page.route((url) => playbooksApi(url), (route) =>
    route.fulfill({ json: { playbooks } }),
  );
  void page.route((url) => templatesApi(url), (route) =>
    route.fulfill({ json: { templates } }),
  );
}

async function gotoStudio(page: Page) {
  // The real shell routes by nav-click (there is no ?view= deep-link seam in the real build).
  await page.goto("/");
  await page.locator(".nav-item", { hasText: /^Studio$/ }).click();
  await expect(page.getByTestId("studio-view")).toBeVisible({ timeout: 15_000 });
}

// ---------------------------------------------------------------------------
// 1. nav routes to studio-view (not coming-soon)
// ---------------------------------------------------------------------------

test("real shell routes Studio nav to studio-view, not coming-soon", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  // Stub shell surfaces too (Command Center loads first on `/`).
  await page.route("**/views/*", (route) =>
    route.fulfill({ status: 404, json: { detail: "no such view" } }),
  );
  await page.route("**/approvals", (route) => route.fulfill({ json: { approvals: [] } }));
  stubBoth(page, [], []);

  await page.goto("/");
  await expect(page.getByTestId("dashboard-empty")).toBeVisible({ timeout: 15_000 });

  await page.locator(".nav-item", { hasText: /^Studio$/ }).click();
  await expect(page.getByTestId("studio-view")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("coming-soon")).toHaveCount(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

// ---------------------------------------------------------------------------
// 2. studio-empty on {playbooks:[]}
// ---------------------------------------------------------------------------

test("studio-empty renders when the server returns zero playbooks", async ({ page }) => {
  stubBoth(page, [], []);

  await gotoStudio(page);
  await expect(page.getByTestId("studio-empty")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("playbook-row")).toHaveCount(0);

  const text = await page.evaluate(() => document.body.innerText);
  expect(text).not.toMatch(/API \d+/);
});

// ---------------------------------------------------------------------------
// 3. studio-rollout on 404 + refresh recovers
// ---------------------------------------------------------------------------

test("404 from /studio/playbooks renders studio-rollout, not an error wall; refresh recovers", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  let calls = 0;
  await page.route((url) => playbooksApi(url), async (route) => {
    calls += 1;
    if (calls === 1) {
      await route.fulfill({ status: 404, json: { detail: "Not Found" } });
    } else {
      await route.fulfill({ json: { playbooks: [] } });
    }
  });
  await page.route((url) => templatesApi(url), (route) =>
    route.fulfill({ json: { templates: [] } }),
  );

  await gotoStudio(page);

  const rollout = page.getByTestId("studio-rollout");
  await expect(rollout).toBeVisible({ timeout: 15_000 });
  await expect(rollout).toContainText("rolling out");
  // NOT an error wall
  await expect(page.getByTestId("studio-error")).toHaveCount(0);
  const text = await page.evaluate(() => document.body.innerText);
  expect(text).not.toMatch(/API \d+/);
  expect(text).not.toContain("Not Found");

  // Refresh recovers
  await page.getByTestId("studio-rollout-refresh").click();
  await expect(page.getByTestId("studio-empty")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("studio-rollout")).toHaveCount(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

// ---------------------------------------------------------------------------
// 4. studio-unavailable on 503 (calm card, not an error wall)
// ---------------------------------------------------------------------------

test("503 from /studio/playbooks renders studio-unavailable calm card, not error", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route((url) => playbooksApi(url), (route) =>
    route.fulfill({ status: 503, json: { detail: "data plane not wired" } }),
  );
  await page.route((url) => templatesApi(url), (route) =>
    route.fulfill({ json: { templates: [] } }),
  );

  await gotoStudio(page);

  const unavailable = page.getByTestId("studio-unavailable");
  await expect(unavailable).toBeVisible({ timeout: 15_000 });
  // NOT an error wall
  await expect(page.getByTestId("studio-error")).toHaveCount(0);
  await expect(page.getByTestId("studio-rollout")).toHaveCount(0);
  const text = await page.evaluate(() => document.body.innerText);
  expect(text).not.toMatch(/API \d+/);
  expect(text).not.toContain("data plane not wired");

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

// ---------------------------------------------------------------------------
// 5. studio-error + retry on 500
// ---------------------------------------------------------------------------

test("500 -> studio-error with retry; raw error detail never reaches the DOM", async ({ page }) => {
  let calls = 0;
  await page.route((url) => playbooksApi(url), async (route) => {
    calls += 1;
    if (calls === 1) {
      await route.fulfill({ status: 500, json: { detail: "db exploded" } });
    } else {
      await route.fulfill({ json: { playbooks: [] } });
    }
  });
  await page.route((url) => templatesApi(url), (route) =>
    route.fulfill({ json: { templates: [] } }),
  );

  await gotoStudio(page);

  const err = page.getByTestId("studio-error");
  await expect(err).toBeVisible({ timeout: 15_000 });
  const text = await page.evaluate(() => document.body.innerText);
  expect(text).not.toMatch(/API \d+/);
  expect(text).not.toContain("db exploded");
  // No unavailable/rollout alongside error
  await expect(page.getByTestId("studio-rollout")).toHaveCount(0);
  await expect(page.getByTestId("studio-unavailable")).toHaveCount(0);

  // Retry recovers
  await page.getByTestId("studio-retry").click();
  await expect(page.getByTestId("studio-empty")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("studio-error")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// 6. composer: invalid JSON feedback data-ok=0 -> valid data-ok=1 -> save
// ---------------------------------------------------------------------------

test("composer: invalid JSON shows data-ok=0 feedback; valid shows data-ok=1; save shows 'Playbook created.'", async ({ page }) => {
  // Stub all /studio/playbooks requests — handles initial GET and later POST + reload GET.
  let created = false;
  await page.route((url) => playbooksApi(url), async (route) => {
    if (route.request().method() === "POST") {
      created = true;
      await route.fulfill({ json: { ...DRAFT_PLAYBOOK, id: "pb-new" } });
    } else {
      await route.fulfill({ json: { playbooks: created ? [{ ...DRAFT_PLAYBOOK, id: "pb-new" }] : [] } });
    }
  });
  await page.route((url) => templatesApi(url), (route) =>
    route.fulfill({ json: { templates: [] } }),
  );

  await gotoStudio(page);
  await page.getByTestId("studio-new").click();
  await expect(page.getByTestId("studio-editor")).toBeVisible({ timeout: 10_000 });

  const textarea = page.getByTestId("studio-editor-text");

  // Type invalid JSON
  await textarea.fill("not valid json {{{");
  const feedback = page.getByTestId("studio-editor-feedback");
  await expect(feedback).toBeVisible();
  await expect(feedback).toHaveAttribute("data-ok", "0");

  // Fix it with valid JSON
  const validDef = JSON.stringify(SAMPLE_DEFINITION, null, 2);
  await textarea.fill(validDef);
  await expect(feedback).toHaveAttribute("data-ok", "1");

  // Save
  await page.getByTestId("studio-editor-save").click();

  // Editor closes; notice "Playbook created." with data-ok=1
  await expect(page.getByTestId("studio-editor")).toHaveCount(0, { timeout: 10_000 });
  const notice = page.getByTestId("studio-notice");
  await expect(notice).toBeVisible({ timeout: 10_000 });
  await expect(notice).toContainText("Playbook created.");
  await expect(notice).toHaveAttribute("data-ok", "1");
});

// ---------------------------------------------------------------------------
// 7a. activate honesty: registered:false -> notice contains 'record-only',
//     does NOT contain 'its crew is registered'
// ---------------------------------------------------------------------------

test("activate registered:false -> notice contains 'record-only', not 'its crew is registered'", async ({ page }) => {
  await page.route((url) => playbooksApi(url), (route) =>
    route.fulfill({ json: { playbooks: [DRAFT_PLAYBOOK] } }),
  );
  await page.route((url) => templatesApi(url), (route) =>
    route.fulfill({ json: { templates: [] } }),
  );
  await page.route("**/studio/playbooks/pb-001/activate", (route) =>
    route.fulfill({
      json: {
        ...DRAFT_PLAYBOOK,
        status: "active",
        registered: false,
        registration_reason: "agent plane not configured",
      },
    }),
  );

  await gotoStudio(page);
  await page.getByTestId("playbook-activate").click();

  const notice = page.getByTestId("studio-notice");
  await expect(notice).toBeVisible({ timeout: 10_000 });
  const text = await notice.textContent();
  expect(text).toContain("record-only");
  expect(text).not.toContain("its crew is registered");
  await expect(notice).toHaveAttribute("data-ok", "1");
});

// ---------------------------------------------------------------------------
// 7b. activate honesty: registered:true -> notice contains 'its crew is registered'
// ---------------------------------------------------------------------------

test("activate registered:true -> notice contains 'its crew is registered'", async ({ page }) => {
  await page.route((url) => playbooksApi(url), (route) =>
    route.fulfill({ json: { playbooks: [DRAFT_PLAYBOOK] } }),
  );
  await page.route((url) => templatesApi(url), (route) =>
    route.fulfill({ json: { templates: [] } }),
  );
  await page.route("**/studio/playbooks/pb-001/activate", (route) =>
    route.fulfill({
      json: {
        ...DRAFT_PLAYBOOK,
        status: "active",
        registered: true,
        registration: { agents: ["scout"], agent_id_tails: ["abc123"], coordinator_id_tail: "xyz789" },
      },
    }),
  );

  await gotoStudio(page);
  await page.getByTestId("playbook-activate").click();

  const notice = page.getByTestId("studio-notice");
  await expect(notice).toBeVisible({ timeout: 10_000 });
  const text = await notice.textContent();
  expect(text).toContain("its crew is registered");
  await expect(notice).toHaveAttribute("data-ok", "1");
});

// ---------------------------------------------------------------------------
// 8a. run honesty: ran:true -> notice contains 'drafted' AND 'Greenlight', NOT 'sent'
// ---------------------------------------------------------------------------

test("run ran:true -> notice contains 'drafted' and 'Greenlight', never 'sent'", async ({ page }) => {
  await page.route((url) => playbooksApi(url), (route) =>
    route.fulfill({ json: { playbooks: [ACTIVE_PLAYBOOK] } }),
  );
  await page.route((url) => templatesApi(url), (route) =>
    route.fulfill({ json: { templates: [] } }),
  );
  await page.route("**/studio/playbooks/pb-002/run", (route) =>
    route.fulfill({
      json: {
        ran: true,
        run: { status: "pending", actions: [{ type: "draft_email" }, { type: "read_crm" }] },
      },
    }),
  );

  await gotoStudio(page);
  await page.getByTestId("playbook-run").click();

  const notice = page.getByTestId("studio-notice");
  await expect(notice).toBeVisible({ timeout: 10_000 });
  const text = await notice.textContent();
  expect(text).toContain("drafted");
  expect(text).toContain("Greenlight");
  expect(text).not.toContain("sent");
  await expect(notice).toHaveAttribute("data-ok", "1");
});

// ---------------------------------------------------------------------------
// 8b. run honesty: ran:false -> notice contains 'record-only'
// ---------------------------------------------------------------------------

test("run ran:false -> notice contains 'record-only'", async ({ page }) => {
  await page.route((url) => playbooksApi(url), (route) =>
    route.fulfill({ json: { playbooks: [ACTIVE_PLAYBOOK] } }),
  );
  await page.route((url) => templatesApi(url), (route) =>
    route.fulfill({ json: { templates: [] } }),
  );
  await page.route("**/studio/playbooks/pb-002/run", (route) =>
    route.fulfill({
      json: {
        ran: false,
        run_reason: "agent plane not configured",
      },
    }),
  );

  await gotoStudio(page);
  await page.getByTestId("playbook-run").click();

  const notice = page.getByTestId("studio-notice");
  await expect(notice).toBeVisible({ timeout: 10_000 });
  const text = await notice.textContent();
  expect(text).toContain("record-only");
  await expect(notice).toHaveAttribute("data-ok", "1");
});
