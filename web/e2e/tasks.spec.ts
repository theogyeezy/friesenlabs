import { test, expect, type Page, type Route } from "@playwright/test";

// Tasks surface e2e — fully offline, against the REAL production bundle.
// Runs in the `chromium-real` Playwright project (VITE_API_MOCK=0 baked at build
// time, Cognito unconfigured so auth is inert and the gate is open). Every API
// call is intercepted with page.route — no real network, no server. Asserts the
// Tasks view is honest end to end:
//   1. the real shell routes Tasks to the API-wired view (not ComingSoon),
//   2. rows render straight from GET /tasks with the overdue flag + scope counts,
//   3. switching scope re-queries the API (open -> done),
//   4. creating a task POSTs {title,...} ONLY (no tenant_id — the trust rule) and
//      reloads so the new row appears,
//   5. completing a task POSTs /tasks/{id}/complete and reloads,
//   6. archiving a task POSTs /tasks/{id}/archive and reloads,
//   7. a 404 from /tasks (live API image predating the route) renders the calm
//      "Tasks API is rolling out" state, not an error wall,
//   8. an empty tenant renders the honest empty state.
//
// NOTE: ApiClient.listTasks always appends `?scope=...`, so a glob like `**/tasks`
// does NOT match the query string — every matcher below keys on url.pathname.

const TASK_OVERDUE = {
  id: "t1111111-1111-1111-1111-111111111111",
  title: "Call Birchwood back about the security review",
  due_at: "2026-06-01T00:00:00+00:00",
  done_at: null,
  done: false,
  overdue: true,
  archived_at: null,
  contact_id: "p-1",
  deal_id: "d-1",
  contact_name: "Dana Whitfield",
  deal_title: "Birchwood platform expansion",
  created_by: "demo-user",
  created_at: "2026-05-28T00:00:00+00:00",
};

const TASK_UPCOMING = {
  id: "t2222222-2222-2222-2222-222222222222",
  title: "Send Halcyon the revised order form",
  due_at: "2026-12-20T00:00:00+00:00",
  done_at: null,
  done: false,
  overdue: false,
  archived_at: null,
  contact_id: null,
  deal_id: null,
  contact_name: null,
  deal_title: null,
  created_by: "demo-user",
  created_at: "2026-06-04T00:00:00+00:00",
};

const TASK_DONE = {
  ...TASK_UPCOMING,
  id: "t3333333-3333-3333-3333-333333333333",
  title: "Confirm Mesa Verde pilot kickoff",
  due_at: "2026-06-05T00:00:00+00:00",
  done_at: "2026-06-06T00:00:00+00:00",
  done: true,
  overdue: false,
};

function listResponse(tasks: Array<typeof TASK_OVERDUE>, scope: string) {
  const active = [TASK_OVERDUE, TASK_UPCOMING];
  return {
    tasks,
    count: tasks.length,
    has_more: false,
    limit: 50,
    offset: 0,
    scope,
    open_count: active.filter((t) => !t.done).length,
    overdue_count: active.filter((t) => !t.done && t.overdue).length,
  };
}

// Stub the shell's landing surfaces (Command Center loads first) so the test can
// navigate to Tasks cleanly.
async function stubShell(page: Page) {
  await page.route("**/views/*", (route) =>
    route.fulfill({ status: 404, json: { detail: "no such view" } }),
  );
  await page.route("**/approvals", (route) => route.fulfill({ json: { approvals: [] } }));
  await page.route(
    (url) => url.pathname === "/deals",
    (route) => route.fulfill({ json: { stages: [], total: 0, stage_order: [] } }),
  );
  await page.route(
    (url) => url.pathname === "/contacts",
    (route) => route.fulfill({ json: { contacts: [], count: 0, has_more: false, limit: 50, offset: 0, q: null } }),
  );
}

async function bodyText(page: Page): Promise<string> {
  return page.evaluate(() => document.body.innerText);
}

// GET /tasks router: returns open tasks by default, done tasks for ?scope=done,
// archived for ?scope=archived. The handler can be reassigned per-test by reading
// the latest closure variable.
async function routeTasksList(page: Page, byScope: Record<string, Array<typeof TASK_OVERDUE>>) {
  await page.route(
    (url) => url.pathname === "/tasks",
    async (route: Route) => {
      if (route.request().method() !== "GET") return route.fallback();
      const scope = new URL(route.request().url()).searchParams.get("scope") ?? "open";
      const tasks = byScope[scope] ?? [];
      await route.fulfill({ json: listResponse(tasks, scope) });
    },
  );
}

test("real shell routes Tasks to the API-wired view, not ComingSoon", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await stubShell(page);
  await routeTasksList(page, { open: [TASK_OVERDUE, TASK_UPCOMING] });

  await page.goto("/");
  await page.locator(".nav-item", { hasText: "Tasks" }).click();

  await expect(page.getByTestId("tasks-view")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("coming-soon")).toHaveCount(0);
  await expect(page.getByTestId("task-row")).toHaveCount(2);

  const text = await bodyText(page);
  expect(text).toContain("Call Birchwood back about the security review");
  expect(text).toContain("Send Halcyon the revised order form");
  // overdue flag + scope counts
  await expect(page.getByTestId("task-overdue-flag")).toHaveCount(1);
  await expect(page.getByTestId("tasks-open-count")).toHaveText("2");
  await expect(page.getByTestId("tasks-overdue-count")).toHaveText("1");

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("switching scope re-queries the API (open -> done)", async ({ page }) => {
  await stubShell(page);
  await routeTasksList(page, { open: [TASK_OVERDUE, TASK_UPCOMING], done: [TASK_DONE] });

  await page.goto("/?view=tasks");
  await expect(page.getByTestId("tasks-view")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("task-row")).toHaveCount(2);

  await page.getByTestId("tasks-scope-done").click();
  await expect(page.getByTestId("task-row")).toHaveCount(1);
  expect(await bodyText(page)).toContain("Confirm Mesa Verde pilot kickoff");
});

test("create a task POSTs title only (no tenant_id) and reloads", async ({ page }) => {
  await stubShell(page);
  // After create, the list should show the new row — flip the open list on POST.
  let created = false;
  await page.route(
    (url) => url.pathname === "/tasks",
    async (route: Route) => {
      const req = route.request();
      if (req.method() === "POST") {
        const body = JSON.parse(req.postData() ?? "{}");
        expect(body).not.toHaveProperty("tenant_id"); // THE TRUST RULE
        created = true;
        await route.fulfill({
          status: 201,
          json: { task: { ...TASK_UPCOMING, id: "new-1", title: body.title } },
        });
        return;
      }
      const scope = new URL(req.url()).searchParams.get("scope") ?? "open";
      const open = created
        ? [TASK_OVERDUE, { ...TASK_UPCOMING, id: "new-1", title: "Draft the Q3 renewal note" }]
        : [TASK_OVERDUE];
      await route.fulfill({ json: listResponse(scope === "open" ? open : [], scope) });
    },
  );

  await page.goto("/?view=tasks");
  await expect(page.getByTestId("tasks-view")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("task-row")).toHaveCount(1);

  await page.getByTestId("new-task-btn").click();
  await page.getByTestId("task-form-title").fill("Draft the Q3 renewal note");
  await page.getByTestId("task-form-submit").click();

  await expect(page.getByTestId("task-row")).toHaveCount(2);
  expect(await bodyText(page)).toContain("Draft the Q3 renewal note");
});

test("completing a task POSTs /tasks/{id}/complete and reloads", async ({ page }) => {
  await stubShell(page);
  let completedPath: string | null = null;
  let completed = false;
  await page.route(
    (url) => /^\/tasks\/[^/]+\/complete$/.test(url.pathname),
    async (route: Route) => {
      completedPath = new URL(route.request().url()).pathname;
      completed = true;
      await route.fulfill({ json: { task: { ...TASK_OVERDUE, done: true, done_at: "2026-06-12T00:00:00+00:00" } } });
    },
  );
  await page.route(
    (url) => url.pathname === "/tasks",
    async (route: Route) => {
      const scope = new URL(route.request().url()).searchParams.get("scope") ?? "open";
      // Once completed, the open list no longer carries the task.
      const open = completed ? [] : [TASK_OVERDUE];
      await route.fulfill({ json: listResponse(scope === "open" ? open : [], scope) });
    },
  );

  await page.goto("/?view=tasks");
  await expect(page.getByTestId("task-row")).toHaveCount(1);
  await page.getByTestId("task-complete-toggle").click();

  await expect(page.getByTestId("tasks-empty")).toBeVisible();
  expect(completedPath).toMatch(/^\/tasks\/.+\/complete$/);
});

test("archiving a task POSTs /tasks/{id}/archive and reloads", async ({ page }) => {
  await stubShell(page);
  let archivedPath: string | null = null;
  let archived = false;
  await page.route(
    (url) => /^\/tasks\/[^/]+\/archive$/.test(url.pathname),
    async (route: Route) => {
      archivedPath = new URL(route.request().url()).pathname;
      archived = true;
      await route.fulfill({ json: { id: TASK_OVERDUE.id, archived: true, archived_at: "2026-06-12T00:00:00+00:00" } });
    },
  );
  await page.route(
    (url) => url.pathname === "/tasks",
    async (route: Route) => {
      const scope = new URL(route.request().url()).searchParams.get("scope") ?? "open";
      const open = archived ? [] : [TASK_OVERDUE];
      await route.fulfill({ json: listResponse(scope === "open" ? open : [], scope) });
    },
  );

  await page.goto("/?view=tasks");
  await expect(page.getByTestId("task-row")).toHaveCount(1);
  await page.getByTestId("task-archive-btn").click();

  await expect(page.getByTestId("tasks-empty")).toBeVisible();
  expect(archivedPath).toMatch(/^\/tasks\/.+\/archive$/);
});

test("404 from /tasks renders the honest rollout state, not an error wall", async ({ page }) => {
  await stubShell(page);
  await page.route(
    (url) => url.pathname === "/tasks",
    (route) => route.fulfill({ status: 404, json: { detail: "Not Found" } }),
  );

  await page.goto("/?view=tasks");
  await expect(page.getByTestId("tasks-rollout")).toBeVisible({ timeout: 15_000 });
  const text = await bodyText(page);
  expect(text).toContain("rolling out");
  expect(text).not.toContain("API 404"); // raw transport string never reaches the DOM
});

test("empty tenant renders the honest empty state, not a fake list", async ({ page }) => {
  await stubShell(page);
  await routeTasksList(page, { open: [] });

  await page.goto("/?view=tasks");
  await expect(page.getByTestId("tasks-empty")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("task-row")).toHaveCount(0);
});
