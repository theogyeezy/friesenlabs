import { test, expect, type Page } from "@playwright/test";

// First-run / onboarding e2e — fully offline, against the REAL production bundle.
// Runs in the `chromium-real` Playwright project (VITE_API_MOCK=0 baked at build
// time, Cognito unconfigured so auth is inert and the gate is open). Every API
// call is intercepted with page.route — no real network, no server.
//
// Asserts the first-run experience is honest end to end:
//   1. a brand-new tenant (incomplete onboarding_state) sees the dismissible
//      first-run checklist atop the workspace — never blocking the app,
//   2. the empty Contacts surface shows a calm empty state with a "Load sample
//      data" CTA (not a blank panel),
//   3. clicking "Load sample data" POSTs /onboarding/load-sample, then the
//      surface remounts and re-fetches → the populated directory renders,
//   4. the checklist's load_data step flips to "Done" and the progress updates,
//   5. "Skip for now" PUTs {dismissed:true} and hides the checklist (persisted
//      per tenant), and on a reload with dismissed=true it stays hidden.

const FRESH = {
  tenant_id: "t-1",
  steps: { load_data: false, try_chat: false, invite_team: false },
  dismissed: false,
  sample_loaded: false,
};

const LOADED_STATE = {
  tenant_id: "t-1",
  steps: { load_data: true, try_chat: false, invite_team: false },
  dismissed: true, // after a load most of the checklist is irrelevant; keep it hidden post-load in this stub
  sample_loaded: true,
};

const CONTACT = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "Dana Whitfield",
  title: null,
  email: "dana@birchwoodcap.example",
  phone: "+1 512 555 0150",
  company_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  company_name: "Birchwood Capital",
  created_at: "2026-05-20T00:00:00+00:00",
  last_activity_at: "2026-06-05T00:00:00+00:00",
};

function contactsList(rows: Array<typeof CONTACT>) {
  return { contacts: rows, count: rows.length, has_more: false, limit: 50, offset: 0, q: null };
}

async function bodyText(page: Page): Promise<string> {
  return page.evaluate(() => document.body.innerText);
}

// Stub the surfaces the shell touches on the dashboard landing so it boots clean.
async function stubShell(page: Page) {
  await page.route("**/views/*", (route) =>
    route.fulfill({ status: 404, json: { detail: "no such view" } }),
  );
  await page.route("**/approvals", (route) => route.fulfill({ json: { approvals: [] } }));
  await page.route("**/companies", (route) => route.fulfill({ json: { companies: [], count: 0, has_more: false, limit: 50, offset: 0, q: null } }));
}

test("brand-new tenant sees the dismissible first-run checklist (never blocks the app)", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await stubShell(page);
  await page.route("**/onboarding", (route) => route.fulfill({ json: FRESH }));

  await page.goto("/");
  await expect(page.getByTestId("first-run-checklist")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("first-run-progress")).toContainText("0 of 3 done");
  // The app is NOT blocked — the dashboard still renders beneath the checklist.
  await expect(page.getByTestId("dashboard-empty")).toBeVisible();
  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("empty Contacts shows a Load-sample CTA; loading it populates the directory", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await stubShell(page);

  // Onboarding starts fresh.
  await page.route("**/onboarding", (route) => route.fulfill({ json: FRESH }));

  // /contacts is EMPTY before the load, POPULATED after — flip on the load-sample POST.
  let loaded = false;
  let loadSampleCalled = false;
  await page.route("**/contacts**", (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    return route.fulfill({ json: contactsList(loaded ? [CONTACT] : []) });
  });
  await page.route("**/onboarding/load-sample", (route) => {
    loadSampleCalled = true;
    loaded = true;
    return route.fulfill({
      json: { loaded: true, counts: { companies: 40, contacts: 120, deals: 60 }, onboarding: LOADED_STATE },
    });
  });

  await page.goto("/");
  await page.locator(".nav-item", { hasText: "Contacts" }).click();

  // The calm empty state with a CTA — not a blank panel.
  await expect(page.getByTestId("dir-empty")).toBeVisible({ timeout: 15_000 });
  const cta = page.getByTestId("dir-empty-load-sample");
  await expect(cta).toBeVisible();

  await cta.click();

  // The directory remounts + re-fetches → the populated row renders.
  await expect(page.getByTestId("dir-list")).toBeVisible({ timeout: 15_000 });
  expect(await bodyText(page)).toContain("Dana Whitfield");
  expect(loadSampleCalled).toBe(true);
  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("checklist load-sample step flips to Done; progress updates", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await stubShell(page);
  await page.route("**/contacts**", (route) =>
    route.request().method() === "GET" ? route.fulfill({ json: contactsList([]) }) : route.fallback(),
  );

  await page.route("**/onboarding", (route) => route.fulfill({ json: FRESH }));
  // After load-sample, return a state that's still incomplete (not dismissed) so the checklist
  // remains visible with load_data done — proving the per-step completion reflects.
  const afterLoad = { tenant_id: "t-1", steps: { load_data: true, try_chat: false, invite_team: false }, dismissed: false, sample_loaded: true };
  await page.route("**/onboarding/load-sample", (route) =>
    route.fulfill({ json: { loaded: true, counts: { contacts: 120, companies: 40, deals: 60 }, onboarding: afterLoad } }),
  );

  await page.goto("/");
  await expect(page.getByTestId("first-run-checklist")).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("first-run-cta-load_data").click();

  // The step row flips done; the success toast + progress reflect it.
  await expect(page.getByTestId("first-run-step-load_data")).toHaveAttribute("data-done", "true", { timeout: 15_000 });
  await expect(page.getByTestId("first-run-progress")).toContainText("1 of 3 done");
  await expect(page.getByTestId("first-run-toast")).toBeVisible();
  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("Skip for now dismisses the checklist and persists (PUT dismissed:true)", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await stubShell(page);
  await page.route("**/contacts**", (route) =>
    route.request().method() === "GET" ? route.fulfill({ json: contactsList([]) }) : route.fallback(),
  );

  let putBody: Record<string, unknown> | null = null;
  await page.route("**/onboarding", (route) => {
    if (route.request().method() === "PUT") {
      putBody = route.request().postDataJSON() as Record<string, unknown>;
      return route.fulfill({ json: { ...FRESH, dismissed: true } });
    }
    return route.fulfill({ json: FRESH });
  });

  await page.goto("/");
  await expect(page.getByTestId("first-run-checklist")).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("first-run-dismiss").click();

  // The checklist hides; the PUT carried {dismissed:true} and NO tenant_id (the trust rule).
  await expect(page.getByTestId("first-run-checklist")).toHaveCount(0, { timeout: 15_000 });
  expect(putBody).toEqual({ dismissed: true });
  expect(JSON.stringify(putBody)).not.toContain("tenant_id");
  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("a dismissed tenant never sees the checklist on load", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await stubShell(page);
  await page.route("**/onboarding", (route) => route.fulfill({ json: { ...FRESH, dismissed: true } }));

  await page.goto("/");
  await expect(page.getByTestId("dashboard-empty")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("first-run-checklist")).toHaveCount(0);
  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("when the API doesn't serve /onboarding yet (404), the shell degrades silently", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await stubShell(page);
  await page.route("**/onboarding", (route) => route.fulfill({ status: 404, json: { detail: "no such route" } }));

  await page.goto("/");
  await expect(page.getByTestId("dashboard-empty")).toBeVisible({ timeout: 15_000 });
  // No checklist, no error wall — the first-run UI simply doesn't render.
  await expect(page.getByTestId("first-run-checklist")).toHaveCount(0);
  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});
