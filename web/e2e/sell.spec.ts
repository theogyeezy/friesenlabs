import { test, expect, type Page } from "@playwright/test";

// Sell (gamification) tab e2e — fully offline, against the REAL production
// bundle. Runs in the `chromium-real` Playwright project (VITE_API_MOCK=0 baked
// at build time, Cognito unconfigured so auth is inert and the entitlement gate
// is open). Every API call is intercepted with page.route — no real network.
//
// Asserts the Sell tab is honest end to end:
//   1. the real shell routes Sell (via the real module gate, no longer the mock
//      gamifyOn toggle) to the API-wired SellView — not the FLStore confetti
//      prototype, not the ComingSoon placeholder,
//   2. a 503 from /sell/me (the points store isn't wired on the task — INERT BY
//      DEFAULT) renders the calm "isn't switched on yet" panel with NO
//      fabricated level/xp/streak/leaderboard — never an error wall,
//   3. when the API serves real data, the rep's level/xp/streak + leaderboard +
//      quests render those exact server numbers (a null display_name falls back
//      to the user id),
//   4. a 404 (live API image predating the routes) renders the calm rollout
//      state with a working refresh,
//   5. raw transport strings ("API <code>", server detail) never reach the DOM.
//
// Navigation is by NAV-CLICK from Command Center (the real module gate, not the
// old mock gamifyOn toggle, is what makes the Sell nav appear). NOTE on routing:
// the document lives at /, and /sell/* are the only API paths — the stub matches
// on url.pathname.startsWith("/sell/") so it can never catch the document.

const sellApi = (url: URL) => url.pathname.startsWith("/sell/");

const ME = {
  user_id: "rep-avery",
  level: 3,
  xp: 520,
  events: 14,
  streak: 4,
  today: { points: 40, events: 2 },
  progress: { level: 3, xp: 520, into_level: 120, span: 200, to_next: 80, next_level_xp: 600, pct: 0.6 },
};

const LEADERBOARD = {
  leaderboard: [
    { user_id: "rep-avery", display_name: "Avery Stone", points: 520, events: 14 },
    { user_id: "rep-quinn", display_name: null, points: 300, events: 9 },
  ],
};

const QUESTS = {
  quests: [
    {
      id: "close-deals",
      title: "Close 5 deals",
      description: "Win 5 deals in 30 days. Each close credits points toward your level and streak.",
      event_type: "deal.closed_won",
      window_days: 30,
      target: 5,
      current: 2,
      progress: 2,
      complete: false,
      reward_points: 50,
    },
  ],
};

async function bodyText(page: Page): Promise<string> {
  return page.evaluate(() => document.body.innerText);
}

// Raw transport strings must NEVER reach the DOM, in ANY state.
async function assertNoRawTransport(page: Page): Promise<void> {
  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
}

// Land on Command Center (stub its surfaces) then nav-click into the Sell tab.
// The Sell-specific /sell/* stub must already be registered by the caller.
async function navToSell(page: Page): Promise<void> {
  await page.route("**/views/*", (route) => route.fulfill({ status: 404, json: { detail: "no such view" } }));
  await page.route("**/approvals", (route) => route.fulfill({ json: { approvals: [] } }));
  await page.goto("/");
  await expect(page.getByTestId("dashboard-empty")).toBeVisible({ timeout: 15_000 });
  await page.locator(".nav-item", { hasText: /^Sell$/ }).click();
}

test("nav-click routes Sell to the API-wired view; a 503 shows the honest offline state (no fabricated points)", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  // The points store isn't wired on this task → every /sell read answers 503.
  await page.route(sellApi, (route) => route.fulfill({ status: 503, json: { detail: "gamification is unavailable" } }));
  await navToSell(page);

  await expect(page.getByTestId("sell-view")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("coming-soon")).toHaveCount(0);

  // The honest offline panel — INERT BY DEFAULT, never an error wall.
  await expect(page.getByTestId("sell-offline")).toBeVisible();
  await expect(page.getByTestId("sell-error")).toHaveCount(0);

  // NO fabricated points anywhere: no stat tiles, no badges, no leaderboard rows.
  await expect(page.getByTestId("sell-me")).toHaveCount(0);
  await expect(page.getByTestId("sell-level")).toHaveCount(0);
  await expect(page.getByTestId("badge-chip")).toHaveCount(0);
  await expect(page.getByTestId("leaderboard-row")).toHaveCount(0);

  const text = await bodyText(page);
  expect(text).toContain("isn’t switched on yet");
  // Not the FLStore confetti prototype, not a server detail dump.
  expect(text).not.toContain("Make selling fun");
  expect(text).not.toContain("gamification is unavailable");
  await assertNoRawTransport(page);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("real data renders the rep's actual level/xp/streak + leaderboard + quests (server numbers, not invented)", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route(sellApi, (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === "/sell/me") return route.fulfill({ json: ME });
    if (p === "/sell/leaderboard") return route.fulfill({ json: LEADERBOARD });
    if (p === "/sell/quests") return route.fulfill({ json: QUESTS });
    return route.fulfill({ status: 404, json: { detail: "Not Found" } });
  });
  await navToSell(page);

  await expect(page.getByTestId("sell-view")).toBeVisible({ timeout: 15_000 });

  // My standing — the exact server numbers.
  await expect(page.getByTestId("sell-level-value")).toHaveText("3");
  await expect(page.getByTestId("sell-xp-value")).toHaveText("520");
  await expect(page.getByTestId("sell-streak-value")).toHaveText("4d");
  await expect(page.getByTestId("sell-today-value")).toHaveText("40");

  // Badges derived from real fields only.
  await expect(page.getByTestId("badge-chip").filter({ hasText: "Level 3" })).toBeVisible();
  await expect(page.getByTestId("badge-chip").filter({ hasText: "4-day streak" })).toBeVisible();

  // Quest, with its real progress.
  await expect(page.getByTestId("quest-card")).toHaveCount(1);
  await expect(page.getByTestId("quest-progress")).toHaveText("2 / 5");

  // Leaderboard: two rows; a null display_name falls back to the user id.
  await expect(page.getByTestId("leaderboard-row")).toHaveCount(2);
  const text = await bodyText(page);
  expect(text).toContain("Avery Stone");
  expect(text).toContain("rep-quinn"); // null display_name → user id
  expect(text).toContain("520 XP");

  // No offline / rollout / error chrome when data is live.
  await expect(page.getByTestId("sell-offline")).toHaveCount(0);
  await expect(page.getByTestId("sell-rollout")).toHaveCount(0);
  await expect(page.getByTestId("sell-error")).toHaveCount(0);
  await assertNoRawTransport(page);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("404 from /sell/me renders the honest rollout state with a working refresh, not an error wall", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  let calls = 0;
  await page.route(sellApi, async (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === "/sell/me") {
      calls += 1;
      if (calls === 1) return route.fulfill({ status: 404, json: { detail: "Not Found" } });
      return route.fulfill({ json: ME });
    }
    if (p === "/sell/leaderboard") return route.fulfill({ json: LEADERBOARD });
    if (p === "/sell/quests") return route.fulfill({ json: QUESTS });
    return route.fulfill({ status: 404, json: {} });
  });
  await navToSell(page);

  const rollout = page.getByTestId("sell-rollout");
  await expect(rollout).toBeVisible({ timeout: 15_000 });
  await expect(rollout).toContainText("Sell is rolling out");
  await expect(page.getByTestId("sell-error")).toHaveCount(0);
  const text = await bodyText(page);
  expect(text).not.toContain("Not Found");
  expect(text).not.toMatch(/API \d+/);

  // Refresh recovers once the API serves the route.
  await page.getByTestId("sell-rollout-refresh").click();
  await expect(page.getByTestId("sell-level-value")).toHaveText("3", { timeout: 15_000 });
  await expect(page.getByTestId("sell-rollout")).toHaveCount(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});
