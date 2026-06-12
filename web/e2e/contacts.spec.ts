import { test, expect, type Page } from "@playwright/test";

// Contacts directory e2e — fully offline, against the REAL production bundle.
// Runs in the `chromium-real` Playwright project (VITE_API_MOCK=0 baked at
// build time, Cognito unconfigured so auth is inert and the gate is open).
// Every API call is intercepted with page.route — no real network, no server.
// Asserts the directory is honest end to end:
//   1. the real shell routes Contacts to the API-wired directory (not the
//      FLStore prototype, not the ComingSoon placeholder),
//   2. contact rows render straight from GET /contacts (joined company names,
//      last-activity timestamps),
//   3. typing in search re-queries with ?q= — the term travels as a URL value
//      and the rendered rows come from the server's filtered answer,
//   4. clicking a row opens the detail drawer fed by GET /contacts/{id}
//      (contact + activities + the company's OPEN deals linking toward the
//      Pipeline board),
//   5. the Companies toggle switches to GET /companies (contact + open-deal
//      counts) with its own drawer,
//   6. a 404 from /contacts (live API image predating the routes) renders the
//      calm "rolling out" state, NOT an error wall,
//   7. friendly copy for 500s with a working retry; the raw "API <code>"
//      string never reaches the DOM.

const CONTACT_DANA = {
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

const CONTACT_MARCUS = {
  id: "22222222-2222-2222-2222-222222222222",
  name: "Marcus Oyelaran",
  title: null,
  email: "marcus@mesaverde.example",
  phone: null,
  company_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  company_name: "Mesa Verde Health",
  created_at: "2026-05-25T00:00:00+00:00",
  last_activity_at: null,
};

const OPEN_DEAL = {
  id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
  title: "Birchwood platform expansion",
  stage: "negotiation",
  amount: 84000,
  currency: "USD",
  company_id: CONTACT_DANA.company_id,
  contact_id: CONTACT_DANA.id,
  created_at: "2026-06-01T00:00:00+00:00",
};

const COMPANY_BIRCHWOOD = {
  id: CONTACT_DANA.company_id,
  name: "Birchwood Capital",
  domain: "birchwoodcap.example",
  created_at: "2026-05-01T00:00:00+00:00",
  contact_count: 1,
  open_deal_count: 1,
};

const COMPANY_MESA = {
  id: CONTACT_MARCUS.company_id,
  name: "Mesa Verde Health",
  domain: "mesaverde.example",
  created_at: "2026-05-02T00:00:00+00:00",
  contact_count: 1,
  open_deal_count: 0,
};

function contactsPage(rows: Array<typeof CONTACT_DANA | typeof CONTACT_MARCUS>, q: string | null = null) {
  return { contacts: rows, count: rows.length, has_more: false, limit: 50, offset: 0, q };
}

function companiesPage(rows: Array<typeof COMPANY_BIRCHWOOD>) {
  return { companies: rows, count: rows.length, has_more: false, limit: 50, offset: 0, q: null };
}

const DANA_DETAIL = {
  contact: CONTACT_DANA,
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
  company_deals: [OPEN_DEAL],
};

const BIRCHWOOD_DETAIL = {
  company: COMPANY_BIRCHWOOD,
  contacts: [CONTACT_DANA],
  deals: [OPEN_DEAL],
};

async function bodyText(page: Page): Promise<string> {
  return page.evaluate(() => document.body.innerText);
}

test("real shell routes Contacts to the API-wired directory, not the prototype or ComingSoon", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  // The shell lands on Command Center first — stub its surfaces too.
  await page.route("**/views/*", (route) =>
    route.fulfill({ status: 404, json: { detail: "no such view" } }),
  );
  await page.route("**/approvals", (route) => route.fulfill({ json: { approvals: [] } }));
  await page.route("**/contacts?*", (route) =>
    route.fulfill({ json: contactsPage([CONTACT_DANA, CONTACT_MARCUS]) }),
  );

  await page.goto("/");
  await expect(page.getByTestId("dashboard-empty")).toBeVisible({ timeout: 15_000 });

  await page.locator(".nav-item", { hasText: "Contacts" }).click();
  await expect(page.getByTestId("contacts-directory")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("coming-soon")).toHaveCount(0);

  // Rows carry names, emails, joined company names, last-activity copy.
  await expect(page.getByTestId("contact-row")).toHaveCount(2);
  const text = await bodyText(page);
  expect(text).toContain("Dana Whitfield");
  expect(text).toContain("dana@birchwoodcap.example");
  expect(text).toContain("Birchwood Capital");
  expect(text).toContain("Mesa Verde Health");
  expect(text).toContain("no activity yet"); // Marcus — honest, not invented

  // No FLStore prototype chrome (the mock Contacts screen's scripted people).
  expect(text).not.toContain("Riverside Plumbing");
  await expect(page.locator(".nav-badge")).toHaveCount(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("search re-queries the server with ?q= and renders its filtered answer", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  const qSeen: Array<string | null> = [];
  await page.route("**/contacts?*", (route) => {
    const url = new URL(route.request().url());
    const q = url.searchParams.get("q");
    qSeen.push(q);
    const rows = q
      ? [CONTACT_DANA, CONTACT_MARCUS].filter(
          (c) => c.name.toLowerCase().includes(q.toLowerCase()) || c.email.includes(q),
        )
      : [CONTACT_DANA, CONTACT_MARCUS];
    return route.fulfill({ json: contactsPage(rows, q) });
  });

  await page.goto("/?view=contacts");
  await expect(page.getByTestId("contact-row")).toHaveCount(2, { timeout: 15_000 });

  await page.getByTestId("dir-search").fill("dana");
  await expect(page.getByTestId("contact-row")).toHaveCount(1, { timeout: 15_000 });
  const text = await bodyText(page);
  expect(text).toContain("Dana Whitfield");
  expect(text).not.toContain("Marcus Oyelaran");
  // The term traveled to the server as a query VALUE (no tenant_id anywhere).
  expect(qSeen).toContain("dana");

  // Clearing the search restores the unfiltered list.
  await page.getByTestId("dir-search").fill("");
  await expect(page.getByTestId("contact-row")).toHaveCount(2, { timeout: 15_000 });

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("drawer opens with contact detail: activities + company open deals linking to Pipeline", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route("**/contacts?*", (route) =>
    route.fulfill({ json: contactsPage([CONTACT_DANA, CONTACT_MARCUS]) }),
  );
  await page.route("**/contacts/*", (route) => route.fulfill({ json: DANA_DETAIL }));

  await page.goto("/?view=contacts");
  await expect(page.getByTestId("contact-row").first()).toBeVisible({ timeout: 15_000 });

  await page.locator(`[data-contact-id="${CONTACT_DANA.id}"]`).click();
  const drawer = page.getByTestId("dir-drawer");
  await expect(drawer).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("drawer-title")).toContainText("Dana Whitfield");
  await expect(page.getByTestId("drawer-email")).toContainText("dana@birchwoodcap.example");
  await expect(page.getByTestId("activity-item")).toHaveCount(2);
  await expect(page.getByTestId("activity-item").first()).toContainText("security review");

  // The Pipeline seam: the company's open deal rides along, linking onward.
  const deal = page.getByTestId("company-deal");
  await expect(deal).toHaveCount(1);
  await expect(deal).toContainText("Birchwood platform expansion");
  await expect(deal).toContainText("Negotiation");
  await expect(deal).toContainText("View in Pipeline");
  // On the bare ?view=contacts seam the link points at the pipeline seam.
  await expect(deal).toHaveAttribute("href", "/?view=pipeline");

  // Esc closes the drawer (house slide-over pattern).
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("dir-drawer")).toHaveCount(0);

  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("companies toggle lists counts and opens the company drawer", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route("**/contacts?*", (route) =>
    route.fulfill({ json: contactsPage([CONTACT_DANA, CONTACT_MARCUS]) }),
  );
  await page.route("**/companies?*", (route) =>
    route.fulfill({ json: companiesPage([COMPANY_BIRCHWOOD, COMPANY_MESA]) }),
  );
  await page.route("**/companies/*", (route) => route.fulfill({ json: BIRCHWOOD_DETAIL }));

  await page.goto("/?view=contacts");
  await expect(page.getByTestId("contact-row").first()).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("dir-tab-companies").click();
  await expect(page.getByTestId("company-row")).toHaveCount(2, { timeout: 15_000 });
  const birchwoodRow = page.locator(`[data-company-id="${COMPANY_BIRCHWOOD.id}"]`);
  await expect(birchwoodRow.getByTestId("company-contact-count")).toContainText("1 contact");
  await expect(birchwoodRow.getByTestId("company-deal-count")).toContainText("1 open deal");
  const mesaRow = page.locator(`[data-company-id="${COMPANY_MESA.id}"]`);
  await expect(mesaRow.getByTestId("company-deal-count")).toContainText("0 open deals");

  await birchwoodRow.click();
  const drawer = page.getByTestId("dir-drawer");
  await expect(drawer).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("drawer-title")).toContainText("Birchwood Capital");
  await expect(page.getByTestId("company-deal")).toContainText("Birchwood platform expansion");
  await expect(page.getByTestId("company-contact-row")).toContainText("Dana Whitfield");

  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("404 from /contacts renders the honest rollout state, not an error wall", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  let calls = 0;
  await page.route("**/contacts?*", async (route) => {
    calls += 1;
    if (calls === 1) {
      // The live API image predates the routes: FastAPI answers its plain 404.
      await route.fulfill({ status: 404, json: { detail: "Not Found" } });
    } else {
      await route.fulfill({ json: contactsPage([CONTACT_MARCUS]) });
    }
  });

  await page.goto("/?view=contacts");

  const rollout = page.getByTestId("dir-rollout");
  await expect(rollout).toBeVisible({ timeout: 15_000 });
  await expect(rollout).toContainText("Contacts API is rolling out");
  await expect(rollout).toContainText("refresh after the next API deploy");
  // NOT an error wall: no error card, no raw status text, no scary copy.
  await expect(page.getByTestId("dir-error")).toHaveCount(0);
  let text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
  expect(text).not.toContain("Not Found");
  expect(text).not.toContain("Something needs another try");

  // Refresh recovers once the API serves the routes.
  await page.getByTestId("dir-rollout-refresh").click();
  await expect(page.getByTestId("contact-row")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("dir-rollout")).toHaveCount(0);
  text = await bodyText(page);
  expect(text).toContain("Marcus Oyelaran");

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("list 500 -> friendly copy with retry; raw 'API <code>' never reaches the DOM", async ({ page }) => {
  let calls = 0;
  await page.route("**/contacts?*", async (route) => {
    calls += 1;
    if (calls === 1) {
      await route.fulfill({ status: 500, json: { detail: "db exploded" } });
    } else {
      await route.fulfill({ json: contactsPage([CONTACT_DANA]) });
    }
  });

  await page.goto("/?view=contacts");

  const err = page.getByTestId("dir-error");
  await expect(err).toBeVisible({ timeout: 15_000 });
  await expect(err).toContainText("went wrong on our side");
  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
  expect(text).not.toContain("db exploded");
  // Error and rows never render together.
  await expect(page.getByTestId("contact-row")).toHaveCount(0);

  await page.getByTestId("dir-retry").click();
  await expect(page.getByTestId("contact-row")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("dir-error")).toHaveCount(0);
});

test("empty tenant renders the honest empty state, not fake rows", async ({ page }) => {
  await page.route("**/contacts?*", (route) => route.fulfill({ json: contactsPage([]) }));

  await page.goto("/?view=contacts");

  await expect(page.getByTestId("dir-empty")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("dir-empty")).toContainText("No contacts yet");
  await expect(page.getByTestId("contact-row")).toHaveCount(0);
});


// ===========================================================================
// Companies create + contact archive + contact note (the CRM build)
// ===========================================================================

test("create a company from the Companies tab posts to /companies", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  let postBody: Record<string, unknown> | null = null;

  await page.route("**/views/*", (r) => r.fulfill({ status: 404, json: { detail: "x" } }));
  await page.route("**/approvals", (r) => r.fulfill({ json: { approvals: [] } }));
  await page.route("**/companies?*", (r) => r.fulfill({ json: companiesPage([COMPANY_BIRCHWOOD]) }));
  await page.route("**/companies", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    postBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({ status: 201, json: { company: { id: "co-new", name: "Acme Inc", domain: "acme.com" } } });
  });

  await page.goto("/");
  await page.locator(".nav-item", { hasText: "Contacts" }).click();
  await page.getByTestId("dir-tab-companies").click();
  await page.getByTestId("add-company-btn").click();
  await expect(page.getByTestId("company-form")).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("company-form-name").fill("Acme Inc");
  await page.getByTestId("company-form-domain").fill("acme.com");
  await page.getByTestId("company-form-submit").click();

  await expect(async () => { expect(postBody).not.toBeNull(); }).toPass({ timeout: 5_000 });
  expect(postBody).toEqual({ name: "Acme Inc", domain: "acme.com" });
  expect(postBody).not.toHaveProperty("tenant_id");
  expect(errors, errors.join("\n")).toHaveLength(0);
});

test("archive a contact posts to /contacts/{id}/archive", async ({ page }) => {
  let archivePath = "";
  await page.route("**/views/*", (r) => r.fulfill({ status: 404, json: { detail: "x" } }));
  await page.route("**/approvals", (r) => r.fulfill({ json: { approvals: [] } }));
  await page.route("**/contacts?*", (r) => r.fulfill({ json: contactsPage([CONTACT_DANA]) }));
  await page.route(`**/contacts/${CONTACT_DANA.id}`, (r) =>
    r.fulfill({ json: { contact: CONTACT_DANA, activities: [], company_deals: [] } }));
  await page.route(`**/contacts/${CONTACT_DANA.id}/archive`, async (route) => {
    archivePath = new URL(route.request().url()).pathname;
    await route.fulfill({ json: { id: CONTACT_DANA.id, archived: true, archived_at: "now" } });
  });

  await page.goto("/");
  await page.locator(".nav-item", { hasText: "Contacts" }).click();
  await page.locator(`[data-contact-id="${CONTACT_DANA.id}"]`).first().click();
  await expect(page.getByTestId("archive-contact-btn")).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("archive-contact-btn").click();
  await expect(async () => { expect(archivePath).toContain("/archive"); }).toPass({ timeout: 5_000 });
  expect(archivePath).toBe(`/contacts/${CONTACT_DANA.id}/archive`);
});

test("log a note on a contact posts to /contacts/{id}/activities", async ({ page }) => {
  let noteBody: Record<string, unknown> | null = null;
  await page.route("**/views/*", (r) => r.fulfill({ status: 404, json: { detail: "x" } }));
  await page.route("**/approvals", (r) => r.fulfill({ json: { approvals: [] } }));
  await page.route("**/contacts?*", (r) => r.fulfill({ json: contactsPage([CONTACT_DANA]) }));
  await page.route(`**/contacts/${CONTACT_DANA.id}`, (r) =>
    r.fulfill({ json: { contact: CONTACT_DANA, activities: [], company_deals: [] } }));
  await page.route(`**/contacts/${CONTACT_DANA.id}/activities`, async (route) => {
    noteBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({ status: 201, json: { activity: { id: "a1", kind: "note", body: "Called back", occurred_at: null } } });
  });

  await page.goto("/");
  await page.locator(".nav-item", { hasText: "Contacts" }).click();
  await page.locator(`[data-contact-id="${CONTACT_DANA.id}"]`).first().click();
  await page.getByTestId("note-input").fill("Called back");
  await page.getByTestId("note-submit").click();
  await expect(async () => { expect(noteBody).not.toBeNull(); }).toPass({ timeout: 5_000 });
  expect(noteBody).toEqual({ kind: "note", body: "Called back" });
});
