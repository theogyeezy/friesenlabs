import { test, expect, type Page } from "@playwright/test";

// Knowledge tab e2e — fully offline, against the REAL production bundle.
// Runs in the `chromium-real` Playwright project (VITE_API_MOCK=0 baked at
// build time, Cognito unconfigured so auth is inert and the gate is open).
// Every API call is intercepted with page.route — no real network, no server.
// Asserts the Knowledge tab is honest end to end:
//   1. the real shell routes Knowledge to the API-wired view (not the FLStore
//      Knowledge prototype, not the ComingSoon placeholder),
//   2. the inventory renders per-source counts + totals straight from
//      GET /knowledge; an un-ingested tenant gets the calm empty state,
//   3. search rides GET /knowledge/search: results render with snippet + match
//      score; an embedder-unavailable response shows the calm "warming up"
//      banner (NOT an error); no matches shows the empty-search state,
//   4. a 404 from /knowledge (live API image predating the route) renders the
//      calm "rolling out" state with a working refresh, NOT an error wall,
//   5. friendly copy for 500s with a working retry; the raw "API <code>" string
//      never reaches the DOM.
//
// NOTE on routing: the document lives at /?view=knowledge, which a plain
// "**/knowledge" glob would ALSO match (** spans the query string), and
// /knowledge/search shares the /knowledge prefix — so the stubs match on
// url.pathname EXACTLY.

const inventoryApi = (url: URL) => url.pathname === "/knowledge";
const searchApi = (url: URL) => url.pathname === "/knowledge/search";

const INVENTORY = {
  sources: [
    { source: "hubspot", document_count: 1280, last_updated: "2026-06-09T12:00:00+00:00" },
    { source: "call", document_count: 262, last_updated: "2026-06-08T09:30:00+00:00" },
    { source: "upload", document_count: 17, last_updated: null },
  ],
  source_count: 3,
  total_documents: 1559,
};

const EMPTY_INVENTORY = { sources: [], source_count: 0, total_documents: 0 };

const SEARCH_HITS = {
  query: "negotiation deals",
  results: [
    {
      ref_id: "deal-westlake",
      source: "hubspot",
      snippet: "Westlake Galleria chiller retrofit — Pinnacle Property Partners, negotiation, $284,000.",
      score: 0.8137,
    },
    {
      ref_id: "call-42",
      source: "call",
      snippet: "Discovery call: Meridian wants the retrofit scoped before Q3; budget approved.",
      score: 0.71,
    },
  ],
  search_available: true,
  reason: null,
};

const SEARCH_UNAVAILABLE = {
  query: "anything",
  results: [],
  search_available: false,
  reason: "search model not configured",
};

const SEARCH_EMPTY = { query: "zzz", results: [], search_available: true, reason: null };

async function bodyText(page: Page): Promise<string> {
  return page.evaluate(() => document.body.innerText);
}

test("real shell routes Knowledge to the API-wired view, not the prototype or ComingSoon", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  // The shell lands on Command Center first — stub its surfaces too.
  await page.route("**/views/*", (route) =>
    route.fulfill({ status: 404, json: { detail: "no such view" } }),
  );
  await page.route("**/approvals", (route) => route.fulfill({ json: { approvals: [] } }));
  await page.route(inventoryApi, (route) => route.fulfill({ json: INVENTORY }));

  await page.goto("/");
  await expect(page.getByTestId("dashboard-empty")).toBeVisible({ timeout: 15_000 });

  await page.locator(".nav-item", { hasText: /^Knowledge$/ }).click();
  await expect(page.getByTestId("knowledge-view")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("coming-soon")).toHaveCount(0);

  // Inventory: 3 source cards + the totals line.
  await expect(page.getByTestId("knowledge-source")).toHaveCount(3);
  await expect(page.getByTestId("knowledge-total")).toContainText("1,559");

  const text = await bodyText(page);
  expect(text).toContain("HubSpot");
  expect(text).toContain("Calls");
  // No raw transport strings.
  expect(text).not.toMatch(/API \d+/);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("un-ingested tenant gets the calm empty inventory state, not an error", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route(inventoryApi, (route) => route.fulfill({ json: EMPTY_INVENTORY }));

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-view")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("knowledge-empty")).toBeVisible();
  await expect(page.getByTestId("knowledge-source")).toHaveCount(0);
  await expect(page.getByTestId("knowledge-total")).toHaveCount(0);
  await expect(page.getByTestId("knowledge-error")).toHaveCount(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("search renders results with snippet + match score", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route(inventoryApi, (route) => route.fulfill({ json: INVENTORY }));
  await page.route(searchApi, (route) => route.fulfill({ json: SEARCH_HITS }));

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-view")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("knowledge-search-input").fill("negotiation deals");
  await page.getByTestId("knowledge-search-submit").click();

  await expect(page.getByTestId("knowledge-result")).toHaveCount(2);
  const text = await bodyText(page);
  expect(text).toContain("Westlake Galleria chiller retrofit");
  expect(text).toContain("81% match"); // 0.8137 -> 81%
  // The inventory still renders alongside search.
  await expect(page.getByTestId("knowledge-source")).toHaveCount(3);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("search 'warming up' (embedder unavailable) shows the calm banner, not an error", async ({ page }) => {
  await page.route(inventoryApi, (route) => route.fulfill({ json: INVENTORY }));
  await page.route(searchApi, (route) => route.fulfill({ json: SEARCH_UNAVAILABLE }));

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-view")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("knowledge-search-input").fill("anything");
  await page.getByTestId("knowledge-search-submit").click();

  const banner = page.getByTestId("knowledge-search-unavailable");
  await expect(banner).toBeVisible();
  await expect(banner).toContainText("Search is warming up");
  await expect(page.getByTestId("knowledge-result")).toHaveCount(0);
  await expect(page.getByTestId("knowledge-search-error")).toHaveCount(0);
  // The internal reason string stays internal.
  const text = await bodyText(page);
  expect(text).not.toContain("search model not configured");
  // Inventory still useful.
  await expect(page.getByTestId("knowledge-source")).toHaveCount(3);
});

test("search with no matches shows the empty-search state", async ({ page }) => {
  await page.route(inventoryApi, (route) => route.fulfill({ json: INVENTORY }));
  await page.route(searchApi, (route) => route.fulfill({ json: SEARCH_EMPTY }));

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-view")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("knowledge-search-input").fill("zzz");
  await page.getByTestId("knowledge-search-submit").click();

  await expect(page.getByTestId("knowledge-search-empty")).toBeVisible();
  await expect(page.getByTestId("knowledge-result")).toHaveCount(0);
});

test("404 from /knowledge renders the honest rollout state, not an error wall", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  let calls = 0;
  await page.route(inventoryApi, async (route) => {
    calls += 1;
    if (calls === 1) {
      await route.fulfill({ status: 404, json: { detail: "Not Found" } });
    } else {
      await route.fulfill({ json: INVENTORY });
    }
  });

  await page.goto("/?view=knowledge");

  const rollout = page.getByTestId("knowledge-rollout");
  await expect(rollout).toBeVisible({ timeout: 15_000 });
  await expect(rollout).toContainText("Knowledge API is rolling out");
  await expect(page.getByTestId("knowledge-error")).toHaveCount(0);
  let text = await bodyText(page);
  expect(text).not.toContain("Not Found");
  expect(text).not.toMatch(/API \d+/);

  // Refresh recovers once the API serves the route.
  await page.getByTestId("knowledge-rollout-refresh").click();
  await expect(page.getByTestId("knowledge-source")).toHaveCount(3, { timeout: 15_000 });
  await expect(page.getByTestId("knowledge-rollout")).toHaveCount(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("500 -> friendly copy with retry; raw 'API <code>' never reaches the DOM", async ({ page }) => {
  let calls = 0;
  await page.route(inventoryApi, async (route) => {
    calls += 1;
    if (calls === 1) {
      await route.fulfill({ status: 500, json: { detail: "db exploded" } });
    } else {
      await route.fulfill({ json: INVENTORY });
    }
  });

  await page.goto("/?view=knowledge");

  const err = page.getByTestId("knowledge-error");
  await expect(err).toBeVisible({ timeout: 15_000 });
  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
  expect(text).not.toContain("db exploded");
  await expect(page.getByTestId("knowledge-source")).toHaveCount(0);

  await page.getByTestId("knowledge-retry").click();
  await expect(page.getByTestId("knowledge-source")).toHaveCount(3, { timeout: 15_000 });
  await expect(page.getByTestId("knowledge-error")).toHaveCount(0);
});

test("503 from GET /knowledge -> calm knowledge-rollout panel; no error wall", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  let calls = 0;
  await page.route(inventoryApi, async (route) => {
    calls += 1;
    if (calls === 1) {
      await route.fulfill({ status: 503, json: { detail: "reader not configured" } });
    } else {
      await route.fulfill({ json: INVENTORY });
    }
  });

  await page.goto("/?view=knowledge");

  // Calm rollout panel — same as 404; never a red error wall.
  const rollout = page.getByTestId("knowledge-rollout");
  await expect(rollout).toBeVisible({ timeout: 15_000 });
  await expect(rollout).toContainText("Knowledge API is rolling out");
  await expect(page.getByTestId("knowledge-error")).toHaveCount(0);

  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
  expect(text).not.toContain("reader not configured");

  // Refresh recovers.
  await page.getByTestId("knowledge-rollout-refresh").click();
  await expect(page.getByTestId("knowledge-source")).toHaveCount(3, { timeout: 15_000 });
  await expect(page.getByTestId("knowledge-rollout")).toHaveCount(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});
