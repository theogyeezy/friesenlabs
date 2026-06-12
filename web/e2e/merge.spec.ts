import { test, expect, type Page, type Route } from "@playwright/test";

// Dedupe / merge e2e (CRM-depth #16) — fully offline, against the REAL bundle in
// the `chromium-real` project. The merge panel lives inside the Contacts directory
// (the "Find duplicates" button opens it as a modal). Asserts:
//   1. the panel loads clusters from GET /contacts/duplicates,
//   2. picking a winner + merging POSTs {winner_id,loser_id} ONLY (no tenant_id —
//      the trust rule) to /contacts/merge and shows the honest success summary,
//   3. toggling to Companies re-queries GET /companies/duplicates,
//   4. an empty result renders the honest "no duplicates" state.
//
// NOTE: pathname predicate matchers — /contacts (directory, has ?query) must not
// swallow /contacts/duplicates or /contacts/merge.

const DUP_CONTACTS = {
  clusters: [{
    key: "email:dana@x.com",
    reason: "email",
    members: [
      { id: "c-win", name: "Dana W", email: "dana@x.com" },
      { id: "c-lose", name: "Dana Whitfield", email: "dana@x.com" },
    ],
  }],
  count: 1,
};

const DUP_COMPANIES = {
  clusters: [{
    key: "domain:birch.example",
    reason: "domain",
    members: [
      { id: "co-win", name: "Birchwood", domain: "birch.example" },
      { id: "co-lose", name: "Birchwood Capital", domain: "birch.example" },
    ],
  }],
  count: 1,
};

const EMPTY = { clusters: [], count: 0 };

// Directory initial load + shell surfaces.
async function stubDirectory(page: Page) {
  await page.route("**/views/*", (r) => r.fulfill({ status: 404, json: { detail: "no such view" } }));
  await page.route("**/approvals", (r) => r.fulfill({ json: { approvals: [] } }));
  await page.route((url) => url.pathname === "/deals", (r) => r.fulfill({ json: { stages: [], total: 0, stage_order: [] } }));
  await page.route(
    (url) => url.pathname === "/contacts",
    (r) => r.fulfill({ json: { contacts: [], count: 0, has_more: false, limit: 50, offset: 0, q: null } }),
  );
  await page.route(
    (url) => url.pathname === "/companies",
    (r) => r.fulfill({ json: { companies: [], count: 0, has_more: false, limit: 50, offset: 0, q: null } }),
  );
}

async function bodyText(page: Page): Promise<string> {
  return page.evaluate(() => document.body.innerText);
}

test("opens the merge panel, lists contact clusters, merges with no tenant_id in the body", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await stubDirectory(page);

  let merged = false;
  let postBody: Record<string, unknown> | null = null;
  await page.route(
    (url) => url.pathname === "/contacts/merge",
    async (route: Route) => {
      postBody = JSON.parse(route.request().postData() ?? "{}");
      merged = true;
      await route.fulfill({ json: { winner: { id: "c-win", name: "Dana W" }, loser_id: "c-lose", repointed: { deals: 1, activities: 2, tasks: 0 } } });
    },
  );
  await page.route(
    (url) => url.pathname === "/contacts/duplicates",
    (route) => route.fulfill({ json: merged ? EMPTY : DUP_CONTACTS }),
  );
  await page.route(
    (url) => url.pathname === "/companies/duplicates",
    (route) => route.fulfill({ json: EMPTY }),
  );

  await page.goto("/?view=contacts");
  await expect(page.getByTestId("contacts-directory")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("find-duplicates-btn").click();
  await expect(page.getByTestId("merge-panel")).toBeVisible();
  await expect(page.getByTestId("merge-cluster")).toHaveCount(1);
  await expect(page.getByTestId("merge-cluster-reason")).toContainText("same email");

  // First member is the default KEEP (winner). Merge.
  await page.getByTestId("merge-btn").click();
  await expect(page.getByTestId("merge-done")).toBeVisible();

  // THE TRUST RULE: the POST body carries the two ids, never a tenant_id.
  expect(postBody).toBeTruthy();
  expect(postBody).not.toHaveProperty("tenant_id");
  expect(postBody).toMatchObject({ winner_id: "c-win", loser_id: "c-lose" });

  // After merge the cluster is gone (empty).
  await expect(page.getByTestId("merge-empty")).toBeVisible();

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("toggling to Companies re-queries /companies/duplicates", async ({ page }) => {
  await stubDirectory(page);
  await page.route((url) => url.pathname === "/contacts/duplicates", (r) => r.fulfill({ json: EMPTY }));
  await page.route((url) => url.pathname === "/companies/duplicates", (r) => r.fulfill({ json: DUP_COMPANIES }));

  await page.goto("/?view=contacts");
  await page.getByTestId("find-duplicates-btn").click();
  await expect(page.getByTestId("merge-panel")).toBeVisible();
  // Contacts is empty by default.
  await expect(page.getByTestId("merge-empty")).toBeVisible();

  await page.getByTestId("merge-entity-companies").click();
  await expect(page.getByTestId("merge-cluster")).toHaveCount(1);
  expect(await bodyText(page)).toContain("same domain");
});

test("a winner pick other than the default is the kept record", async ({ page }) => {
  await stubDirectory(page);
  let postBody: Record<string, unknown> | null = null;
  await page.route((url) => url.pathname === "/companies/duplicates", (r) => r.fulfill({ json: EMPTY }));
  await page.route((url) => url.pathname === "/contacts/duplicates", (r) => r.fulfill({ json: DUP_CONTACTS }));
  await page.route(
    (url) => url.pathname === "/contacts/merge",
    async (route: Route) => {
      postBody = JSON.parse(route.request().postData() ?? "{}");
      await route.fulfill({ json: { winner: { id: "c-lose", name: "Dana Whitfield" }, loser_id: "c-win", repointed: {} } });
    },
  );

  await page.goto("/?view=contacts");
  await page.getByTestId("find-duplicates-btn").click();
  await expect(page.getByTestId("merge-cluster")).toHaveCount(1);

  // Pick the SECOND member as the winner, then merge.
  await page.getByTestId("merge-member").nth(1).getByTestId("merge-winner-radio").check();
  await page.getByTestId("merge-btn").click();
  await expect(page.getByTestId("merge-done")).toBeVisible();

  expect(postBody).toMatchObject({ winner_id: "c-lose", loser_id: "c-win" });
});
