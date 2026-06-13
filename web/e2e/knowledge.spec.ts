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

test("503 from GET /knowledge -> calm UNPROVISIONED panel (distinct from rolling-out); no error wall", async ({ page }) => {
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

  // The P1 split: a 503 (data plane unwired) is NOT the 404 "rolling out" story.
  const panel = page.getByTestId("knowledge-unprovisioned");
  await expect(panel).toBeVisible({ timeout: 15_000 });
  await expect(panel).toContainText("isn't switched on for this workspace");
  await expect(page.getByTestId("knowledge-rollout")).toHaveCount(0);
  await expect(page.getByTestId("knowledge-error")).toHaveCount(0);

  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
  expect(text).not.toContain("reader not configured");

  // Refresh recovers once the plane is wired.
  await page.getByTestId("knowledge-rollout-refresh").click();
  await expect(page.getByTestId("knowledge-source")).toHaveCount(3, { timeout: 15_000 });
  await expect(page.getByTestId("knowledge-unprovisioned")).toHaveCount(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("a transient search failure offers a retry — NOT the warming-up story", async ({ page }) => {
  let searches = 0;
  await page.route(inventoryApi, (route) => route.fulfill({ json: INVENTORY }));
  await page.route(searchApi, async (route) => {
    searches += 1;
    if (searches === 1) {
      await route.fulfill({
        json: { query: "deals", results: [], search_available: false,
                reason: "search failed", reason_code: "search_error" },
      });
    } else {
      await route.fulfill({ json: SEARCH_HITS });
    }
  });

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-view")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("knowledge-search-input").fill("deals");
  await page.getByTestId("knowledge-search-submit").click();

  const failed = page.getByTestId("knowledge-search-failed");
  await expect(failed).toContainText("Search hit a snag");
  await expect(page.getByTestId("knowledge-search-unavailable")).toHaveCount(0);

  // The retry recovers in place.
  await page.getByTestId("knowledge-search-retry").click();
  await expect(page.getByTestId("knowledge-result")).toHaveCount(2);
});

// ---------------------------------------------------------------------------
// Add-document path (knowledge audit P0): POST /knowledge/documents
// ---------------------------------------------------------------------------

const docsApi = (url: URL) => url.pathname === "/knowledge/documents";

test("add document posts to /knowledge/documents and refreshes the inventory", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  const UPLOADED_INVENTORY = {
    sources: [{ source: "upload", document_count: 2, last_updated: "2026-06-11T10:00:00+00:00" }],
    source_count: 1,
    total_documents: 2,
  };
  let posted = false;
  await page.route(inventoryApi, (route) =>
    route.fulfill({ status: 200, json: posted ? UPLOADED_INVENTORY : EMPTY_INVENTORY }),
  );
  await page.route(docsApi, async (route, request) => {
    // The collection path serves BOTH the pages list (GET) and the create (POST).
    if (request.method() !== "POST") {
      return route.fulfill({ json: { documents: [], total: 0 } });
    }
    posted = true;
    const body = request.postDataJSON() as { title: string; content: string };
    expect(body.title).toBe("Pricing policy");
    expect(body.content).toContain("Discounts cap at 15%");
    await route.fulfill({
      status: 201,
      json: { ref_id: "upload:pricing-policy-ab12cd34", chunks: 2, source: "upload", title: body.title },
    });
  });

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-empty")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("knowledge-add-toggle").click();
  await page.getByTestId("knowledge-add-title").fill("Pricing policy");
  await page.getByTestId("knowledge-add-content").fill("Discounts cap at 15% without approval.");
  await page.getByTestId("knowledge-add-submit").click();

  await expect(page.getByTestId("knowledge-add-note")).toContainText("2 sections indexed");
  // The inventory refreshed and now shows the upload source — no more empty state.
  await expect(page.getByTestId("knowledge-source")).toHaveCount(1);
  await expect(page.getByTestId("knowledge-empty")).toHaveCount(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("add document degrades honestly when uploads aren't switched on (503)", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route(inventoryApi, (route) =>
    route.fulfill({ status: 200, json: EMPTY_INVENTORY }),
  );
  await page.route(docsApi, (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "document upload not configured — the ingest plane (INGEST_REAL_STORES + a DSN) is not wired on this task" },
    }),
  );

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-empty")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("knowledge-add-toggle").click();
  await page.getByTestId("knowledge-add-title").fill("Pricing policy");
  await page.getByTestId("knowledge-add-content").fill("Discounts cap at 15%.");
  await page.getByTestId("knowledge-add-submit").click();

  // Honest copy: the doc did NOT land, uploads aren't enabled — never a fake success.
  await expect(page.getByTestId("knowledge-add-unavailable")).toContainText("was not saved");
  await expect(page.getByTestId("knowledge-add-note")).toHaveCount(0);
  const text = await bodyText(page);
  expect(text).not.toContain("INGEST_REAL_STORES"); // server detail never reaches the DOM

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

// ---------------------------------------------------------------------------
// Pages — the Notion-style editable corpus (GET/PUT/DELETE /knowledge/documents)
// ---------------------------------------------------------------------------

const REF_PRICING = "upload:pricing-policy-aa11bb22";
const REF_LEGACY = "upload:old-playbook-00aa11bb";
const REF_EDITED = "upload:pricing-policy-deadbeef";

// The client encodeURIComponent()s the ref, so match on the DECODED pathname.
const docDetailApi = (url: URL) => decodeURIComponent(url.pathname).startsWith("/knowledge/documents/");
const detailRefOf = (url: URL) => decodeURIComponent(url.pathname).slice("/knowledge/documents/".length);

const PAGES = {
  documents: [
    {
      ref_id: REF_PRICING,
      title: "Pricing policy",
      preview: "Standard discounts cap at 15% without approval.",
      chunks: 2,
      editable: true,
      created_at: "2026-06-07T18:05:00+00:00",
      updated_at: "2026-06-11T09:00:00+00:00",
    },
    {
      ref_id: REF_LEGACY,
      title: "Old playbook",
      preview: "",
      chunks: 2,
      editable: false,
      created_at: "2026-06-01T08:00:00+00:00",
      updated_at: "2026-06-01T08:00:00+00:00",
    },
  ],
  total: 2,
};

const PRICING_DOC = {
  ref_id: REF_PRICING,
  title: "Pricing policy",
  content: "## Standard rates\n\nThe 2026 price book lists every rate.\n\n## Discounts\n\n- cap at **15%** without approval",
  editable: true,
  sections: null,
  chunks: 2,
  created_at: "2026-06-07T18:05:00+00:00",
  updated_at: "2026-06-11T09:00:00+00:00",
};

const LEGACY_DOC = {
  ref_id: REF_LEGACY,
  title: "Old playbook",
  content: null,
  editable: false,
  sections: ["Old playbook Opening checklist: disarm, lights, till float.", "Closing: reconcile, backup, alarm."],
  chunks: 2,
  created_at: "2026-06-01T08:00:00+00:00",
  updated_at: "2026-06-01T08:00:00+00:00",
};

test("pages rail lists documents; opening one renders the stored original as rich text", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route(inventoryApi, (route) => route.fulfill({ json: INVENTORY }));
  await page.route(docsApi, (route) => route.fulfill({ json: PAGES }));
  await page.route(docDetailApi, (route, request) => {
    const ref = detailRefOf(new URL(request.url()));
    return route.fulfill({ json: ref === REF_PRICING ? PRICING_DOC : LEGACY_DOC });
  });

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-view")).toBeVisible({ timeout: 15_000 });

  // Both pages list; the legacy one is marked read-only.
  await expect(page.getByTestId("knowledge-page-item")).toHaveCount(2);
  await expect(page.getByTestId("knowledge-pages")).toContainText("read-only");

  await page.getByTestId("knowledge-page-item").first().click();
  await expect(page.getByTestId("knowledge-doc-title")).toHaveText("Pricing policy");
  // Markdown RENDERED (heading text present, raw ## absent) through the safe subset renderer.
  const body = page.getByTestId("knowledge-doc-body");
  await expect(body).toContainText("Standard rates");
  await expect(body).not.toContainText("##");
  await expect(page.getByTestId("knowledge-doc-edit")).toBeVisible();

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("a legacy page shows its indexed sections read-only with the honest re-add note", async ({ page }) => {
  await page.route(inventoryApi, (route) => route.fulfill({ json: INVENTORY }));
  await page.route(docsApi, (route) => route.fulfill({ json: PAGES }));
  await page.route(docDetailApi, (route) => route.fulfill({ json: LEGACY_DOC }));

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-page-item")).toHaveCount(2, { timeout: 15_000 });

  await page.getByTestId("knowledge-page-item").nth(1).click();
  await expect(page.getByTestId("knowledge-doc-title")).toHaveText("Old playbook");
  await expect(page.getByTestId("knowledge-legacy-note")).toContainText("before editing existed");
  await expect(page.getByTestId("knowledge-doc-body")).toContainText("Opening checklist");
  // No Edit affordance on a page whose original text doesn't exist — delete still offered.
  await expect(page.getByTestId("knowledge-doc-edit")).toHaveCount(0);
  await expect(page.getByTestId("knowledge-doc-delete")).toBeVisible();
});

test("creating a page POSTs, then opens the saved page", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  let created = false;
  await page.route(inventoryApi, (route) => route.fulfill({ json: created ? INVENTORY : EMPTY_INVENTORY }));
  await page.route(docsApi, async (route, request) => {
    if (request.method() === "POST") {
      created = true;
      const body = request.postDataJSON() as { title: string; content: string };
      expect(body.title).toBe("Refund policy");
      expect(body.content).toContain("30 days");
      return route.fulfill({
        status: 201,
        json: { ref_id: "upload:refund-policy-12ab34cd", chunks: 1, source: "upload", title: body.title },
      });
    }
    return route.fulfill({
      json: created
        ? {
            documents: [
              { ref_id: "upload:refund-policy-12ab34cd", title: "Refund policy", preview: "Returns within 30 days.",
                chunks: 1, editable: true, created_at: "2026-06-12T10:00:00+00:00", updated_at: "2026-06-12T10:00:00+00:00" },
            ],
            total: 1,
          }
        : { documents: [], total: 0 },
    });
  });
  await page.route(docDetailApi, (route) =>
    route.fulfill({
      json: {
        ref_id: "upload:refund-policy-12ab34cd", title: "Refund policy",
        content: "Returns within **30 days** with proof of purchase.",
        editable: true, sections: null, chunks: 1,
        created_at: "2026-06-12T10:00:00+00:00", updated_at: "2026-06-12T10:00:00+00:00",
      },
    }),
  );

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-pages-empty")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("knowledge-add-toggle").click();
  await page.getByTestId("knowledge-add-title").fill("Refund policy");
  await page.getByTestId("knowledge-add-content").fill("Returns within 30 days with proof of purchase.");
  await page.getByTestId("knowledge-add-submit").click();

  await expect(page.getByTestId("knowledge-add-note")).toContainText("1 section indexed");
  // The saved page opened in the reader and the rail refreshed.
  await expect(page.getByTestId("knowledge-doc-title")).toHaveText("Refund policy");
  await expect(page.getByTestId("knowledge-page-item")).toHaveCount(1);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("the editor previews markdown before saving", async ({ page }) => {
  await page.route(inventoryApi, (route) => route.fulfill({ json: EMPTY_INVENTORY }));
  await page.route(docsApi, (route) => route.fulfill({ json: { documents: [], total: 0 } }));

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-view")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("knowledge-add-toggle").click();
  await page.getByTestId("knowledge-add-content").fill("## Hours\n\n- Mon to Fri, **9 to 5:30**");
  await page.getByTestId("knowledge-editor-preview").click();

  const rendered = page.getByTestId("knowledge-editor-rendered");
  await expect(rendered).toContainText("Hours");
  await expect(rendered).not.toContainText("##"); // rendered, not raw
  // Flip back to write mode — the text survives the round trip.
  await page.getByTestId("knowledge-editor-write").click();
  await expect(page.getByTestId("knowledge-add-content")).toHaveValue("## Hours\n\n- Mon to Fri, **9 to 5:30**");
});

test("editing a page PUTs to the old ref and follows the returned new ref", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  let edited = false;
  await page.route(inventoryApi, (route) => route.fulfill({ json: INVENTORY }));
  await page.route(docsApi, (route) =>
    route.fulfill({
      json: edited
        ? { documents: [{ ...PAGES.documents[0], ref_id: REF_EDITED, updated_at: "2026-06-12T11:00:00+00:00" }], total: 1 }
        : PAGES,
    }),
  );
  await page.route(docDetailApi, async (route, request) => {
    const url = new URL(request.url());
    const ref = detailRefOf(url);
    if (request.method() === "PUT") {
      expect(ref).toBe(REF_PRICING);
      const body = request.postDataJSON() as { title: string; content: string };
      expect(body.content).toContain("20%");
      edited = true;
      return route.fulfill({
        json: { ref_id: REF_EDITED, chunks: 2, source: "upload", title: body.title,
                replaced_ref_id: REF_PRICING, previous_removed: true },
      });
    }
    if (ref === REF_EDITED) {
      return route.fulfill({
        json: { ...PRICING_DOC, ref_id: REF_EDITED, content: "Discounts cap at **20%** now.",
                updated_at: "2026-06-12T11:00:00+00:00" },
      });
    }
    return route.fulfill({ json: PRICING_DOC });
  });

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-page-item")).toHaveCount(2, { timeout: 15_000 });

  await page.getByTestId("knowledge-page-item").first().click();
  await expect(page.getByTestId("knowledge-doc-edit")).toBeVisible();
  await page.getByTestId("knowledge-doc-edit").click();

  // The editor is pre-filled with the EXACT stored original.
  await expect(page.getByTestId("knowledge-add-title")).toHaveValue("Pricing policy");
  await expect(page.getByTestId("knowledge-add-content")).toHaveValue(PRICING_DOC.content);

  await page.getByTestId("knowledge-add-content").fill("Discounts cap at 20% now.");
  await page.getByTestId("knowledge-add-submit").click();

  // The reader follows the NEW ref returned by the API.
  await expect(page.getByTestId("knowledge-doc-body")).toContainText("20%");
  await expect(page.getByTestId("knowledge-doc-cleanup-note")).toHaveCount(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("a failed old-version cleanup after an edit surfaces the honest note", async ({ page }) => {
  await page.route(inventoryApi, (route) => route.fulfill({ json: INVENTORY }));
  await page.route(docsApi, (route) => route.fulfill({ json: PAGES }));
  await page.route(docDetailApi, async (route, request) => {
    if (request.method() === "PUT") {
      return route.fulfill({
        json: { ref_id: REF_EDITED, chunks: 2, source: "upload", title: "Pricing policy",
                replaced_ref_id: REF_PRICING, previous_removed: false },
      });
    }
    return route.fulfill({ json: { ...PRICING_DOC, ref_id: REF_EDITED } });
  });

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-page-item")).toHaveCount(2, { timeout: 15_000 });
  await page.getByTestId("knowledge-page-item").first().click();
  await page.getByTestId("knowledge-doc-edit").click();
  await page.getByTestId("knowledge-add-content").fill("New text.");
  await page.getByTestId("knowledge-add-submit").click();

  await expect(page.getByTestId("knowledge-doc-cleanup-note")).toContainText("previous version");
});

test("deleting a page confirms, DELETEs, and returns to the rail", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  let deleted = false;
  await page.route(inventoryApi, (route) => route.fulfill({ json: INVENTORY }));
  await page.route(docsApi, (route) =>
    route.fulfill({ json: deleted ? { documents: [PAGES.documents[1]], total: 1 } : PAGES }),
  );
  await page.route(docDetailApi, async (route, request) => {
    if (request.method() === "DELETE") {
      expect(detailRefOf(new URL(request.url()))).toBe(REF_PRICING);
      deleted = true;
      return route.fulfill({ json: { ref_id: REF_PRICING, deleted: true, rows_removed: 3 } });
    }
    return route.fulfill({ json: PRICING_DOC });
  });

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-page-item")).toHaveCount(2, { timeout: 15_000 });
  await page.getByTestId("knowledge-page-item").first().click();
  await expect(page.getByTestId("knowledge-doc-title")).toHaveText("Pricing policy");

  // Two-step confirm: Delete arms it; the confirm button fires the DELETE.
  await page.getByTestId("knowledge-doc-delete").click();
  await page.getByTestId("knowledge-doc-confirm-delete").click();

  await expect(page.getByTestId("knowledge-doc")).toHaveCount(0);
  await expect(page.getByTestId("knowledge-page-item")).toHaveCount(1);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("a search hit inside the editable corpus opens its page", async ({ page }) => {
  await page.route(inventoryApi, (route) => route.fulfill({ json: INVENTORY }));
  await page.route(docsApi, (route) => route.fulfill({ json: PAGES }));
  await page.route(docDetailApi, (route) => route.fulfill({ json: PRICING_DOC }));
  await page.route(searchApi, (route) =>
    route.fulfill({
      json: {
        query: "discounts",
        results: [
          { ref_id: `${REF_PRICING}#1`, source: "upload",
            snippet: "Standard discounts cap at 15% without approval.", score: 0.91 },
          { ref_id: "deal-westlake", source: "hubspot",
            snippet: "Westlake Galleria chiller retrofit.", score: 0.62 },
        ],
        search_available: true,
        reason: null,
      },
    }),
  );

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-view")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("knowledge-search-input").fill("discounts");
  await page.getByTestId("knowledge-search-submit").click();

  await expect(page.getByTestId("knowledge-result")).toHaveCount(2);
  // Only the upload-corpus hit offers Open page (a CRM hit isn't a page).
  await expect(page.getByTestId("knowledge-result-open")).toHaveCount(1);
  await page.getByTestId("knowledge-result-open").click();
  await expect(page.getByTestId("knowledge-doc-title")).toHaveText("Pricing policy");
});

test("the pages rail filter narrows a long list client-side", async ({ page }) => {
  const MANY_PAGES = {
    documents: ["Pricing policy", "Refund policy", "Onboarding SOP", "Safety guide", "Vendor list", "Holiday hours"]
      .map((title, i) => ({
        ref_id: `upload:page-${i}-aabbcc0${i}`, title, preview: `${title} preview text.`,
        chunks: 1, editable: true,
        created_at: "2026-06-10T08:00:00+00:00", updated_at: "2026-06-10T08:00:00+00:00",
      })),
    total: 6,
  };
  await page.route(inventoryApi, (route) => route.fulfill({ json: INVENTORY }));
  await page.route(docsApi, (route) => route.fulfill({ json: MANY_PAGES }));

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-page-item")).toHaveCount(6, { timeout: 15_000 });

  await page.getByTestId("knowledge-pages-filter").fill("policy");
  await expect(page.getByTestId("knowledge-page-item")).toHaveCount(2);

  await page.getByTestId("knowledge-pages-filter").fill("zzz");
  await expect(page.getByTestId("knowledge-pages-nomatch")).toContainText("zzz");

  await page.getByTestId("knowledge-pages-filter").fill("");
  await expect(page.getByTestId("knowledge-page-item")).toHaveCount(6);
});

test("Enter continues markdown lists in the editor; an empty item exits the list", async ({ page }) => {
  await page.route(inventoryApi, (route) => route.fulfill({ json: EMPTY_INVENTORY }));
  await page.route(docsApi, (route) => route.fulfill({ json: { documents: [], total: 0 } }));

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-view")).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("knowledge-add-toggle").click();

  const content = page.getByTestId("knowledge-add-content");
  await content.fill("- first");
  await content.press("Enter");
  // Wait for the continuation commit BEFORE typing on (the assertion auto-retries) —
  // otherwise a slow CI render races the next keystrokes into the wrong caret position.
  await expect(content).toHaveValue("- first\n- ");
  await content.pressSequentially("second");
  await expect(content).toHaveValue("- first\n- second");

  // Empty item + Enter exits the list (the dangling "- " is removed).
  await content.press("Enter");
  await expect(content).toHaveValue("- first\n- second\n- ");
  await content.press("Enter");
  await expect(content).toHaveValue("- first\n- second\n");

  // Ordered lists increment.
  await content.fill("1. one");
  await content.press("Enter");
  await expect(content).toHaveValue("1. one\n2. ");
  await content.pressSequentially("two");
  await expect(content).toHaveValue("1. one\n2. two");
});

test("search pages with 'Show more results' and appends in place", async ({ page }) => {
  const hit = (i: number) => ({
    ref_id: `demo:doc:hit:${i}`, source: "hubspot",
    snippet: `Result number ${i} about negotiation.`, score: 0.9 - i * 0.01,
  });
  await page.route(inventoryApi, (route) => route.fulfill({ json: INVENTORY }));
  await page.route(searchApi, (route, request) => {
    const u = new URL(request.url());
    const off = Number(u.searchParams.get("offset") || "0");
    // 12 ranked hits total; the default page size (8) splits them 8 + 4.
    const all = Array.from({ length: 12 }, (_, i) => hit(i));
    const slice = all.slice(off, off + 8);
    return route.fulfill({
      json: { query: "negotiation", results: slice, search_available: true, reason: null,
              reason_code: null, offset: off,
              next_offset: slice.length === 8 ? off + 8 : null },
    });
  });

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-view")).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("knowledge-search-input").fill("negotiation");
  await page.getByTestId("knowledge-search-submit").click();

  await expect(page.getByTestId("knowledge-result")).toHaveCount(8);
  await page.getByTestId("knowledge-search-more").click();
  // Appended in place — 12 rows now, and the short page ends the paging honestly.
  await expect(page.getByTestId("knowledge-result")).toHaveCount(12);
  await expect(page.getByTestId("knowledge-search-more")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Citation → knowledge page (chat grounds on pages; pages open from citations)
// ---------------------------------------------------------------------------

const CHAT_TURN = {
  answer: "Standard discounts cap at 15% without owner approval.",
  citations: [
    { claim: "Discounts cap at 15%.", source_ref: `${REF_PRICING}#1`,
      snippet: "Standard discounts cap at 15% without approval." },
    { claim: "The Westlake deal is in negotiation.", source_ref: "deal-westlake",
      snippet: "Westlake Galleria chiller retrofit, negotiation stage." },
  ],
  pending_approvals: [], slots: {}, needs_disambiguation: [], delegations: [],
  session_id: "s1", tenant_id: "t1", view_intent: false, view_request: null,
  grounding_status: "grounded", retrieved_count: 2, settled: true,
};

test("a chat citation into the editable corpus deep-links to its knowledge page (standalone)", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route((url) => url.pathname === "/chat", (route) => route.fulfill({ json: CHAT_TURN }));
  await page.route(inventoryApi, (route) => route.fulfill({ json: INVENTORY }));
  await page.route(docsApi, (route) => route.fulfill({ json: PAGES }));
  await page.route(docDetailApi, (route) => route.fulfill({ json: PRICING_DOC }));

  await page.goto("/?view=chat");
  await expect(page.getByTestId("chat-dock")).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("chat-input").fill("What's our discount policy?");
  await page.getByTestId("chat-send").click();

  await expect(page.getByTestId("citation")).toHaveCount(2, { timeout: 15_000 });
  // Only the page-chunk citation links — a CRM ref ('deal-westlake') is not a page.
  await expect(page.getByTestId("citation-open-page")).toHaveCount(1);

  // Standalone chat has no shell to soft-navigate: the link is a real ?view=knowledge&doc=
  // deep link (knowledge pages are URL-addressable).
  await page.getByTestId("citation-open-page").click();
  await expect(page).toHaveURL(/view=knowledge&doc=/);
  await expect(page.getByTestId("knowledge-doc-title")).toHaveText("Pricing policy", { timeout: 15_000 });

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("in the shell, a citation opens the page WITHOUT a reload (soft route switch)", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route("**/views/*", (route) => route.fulfill({ status: 404, json: { detail: "no such view" } }));
  await page.route("**/approvals", (route) => route.fulfill({ json: { approvals: [] } }));
  await page.route((url) => url.pathname === "/chat", (route) => route.fulfill({ json: CHAT_TURN }));
  await page.route(inventoryApi, (route) => route.fulfill({ json: INVENTORY }));
  await page.route(docsApi, (route) => route.fulfill({ json: PAGES }));
  await page.route(docDetailApi, (route) => route.fulfill({ json: PRICING_DOC }));

  await page.goto("/");
  await expect(page.getByTestId("dashboard-empty")).toBeVisible({ timeout: 15_000 });

  await page.getByRole("button", { name: "Ask agents" }).click();
  const dock = page.locator(".chat.show").getByTestId("chat-dock");
  await expect(dock).toBeVisible({ timeout: 15_000 });
  await dock.getByTestId("chat-input").fill("What's our discount policy?");
  await dock.getByTestId("chat-send").click();

  await expect(page.getByTestId("citation-open-page")).toHaveCount(1, { timeout: 15_000 });
  await page.getByTestId("citation-open-page").click();

  // The shell routed to Knowledge and opened the page in place — same document, no reload.
  await expect(page.getByTestId("knowledge-doc-title")).toHaveText("Pricing policy", { timeout: 15_000 });
  expect(page.url()).not.toContain("doc=");

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("pages endpoint 404 (web ahead of the API) degrades calmly; inventory + search stay useful", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.route(inventoryApi, (route) => route.fulfill({ json: INVENTORY }));
  await page.route(docsApi, (route) => route.fulfill({ status: 404, json: { detail: "Not Found" } }));
  await page.route(searchApi, (route) => route.fulfill({ json: SEARCH_HITS }));

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-view")).toBeVisible({ timeout: 15_000 });

  await expect(page.getByTestId("knowledge-pages-rollout")).toContainText("rolling out");
  await expect(page.getByTestId("knowledge-source")).toHaveCount(3);

  await page.getByTestId("knowledge-search-input").fill("negotiation deals");
  await page.getByTestId("knowledge-search-submit").click();
  await expect(page.getByTestId("knowledge-result")).toHaveCount(2);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

// ---------------------------------------------------------------------------
// Page hierarchy — tree rail, breadcrumbs, the Move panel (knowledge_pages)
// ---------------------------------------------------------------------------

const REF_PARENT = "upload:handbook-aa00aa00";
const REF_CHILD = "upload:hours-bb11bb11";
const REF_OTHER = "upload:pricing-cc22cc22";

const TREE_PAGES = {
  documents: [
    { ref_id: REF_CHILD, title: "Hours", preview: "Mon-Fri 9 to 5:30.", chunks: 1, editable: true,
      created_at: "2026-06-12T10:00:00+00:00", updated_at: "2026-06-12T12:00:00+00:00",
      parent_ref: REF_PARENT, sort_order: 0 },
    { ref_id: REF_OTHER, title: "Pricing", preview: "Rates and discounts.", chunks: 1, editable: true,
      created_at: "2026-06-12T09:00:00+00:00", updated_at: "2026-06-12T11:00:00+00:00",
      parent_ref: null, sort_order: 2 },
    { ref_id: REF_PARENT, title: "Handbook", preview: "Everything operational.", chunks: 1, editable: true,
      created_at: "2026-06-12T08:00:00+00:00", updated_at: "2026-06-12T10:30:00+00:00",
      parent_ref: null, sort_order: 1 },
  ],
  total: 3,
  organize_available: true,
};

const docFor = (ref: string, title: string) => ({
  ref_id: ref, title, content: `Body of ${title}.`, editable: true, sections: null,
  chunks: 1, created_at: "2026-06-12T08:00:00+00:00", updated_at: "2026-06-12T12:00:00+00:00",
});

test("the rail renders sub-pages indented under parents in manual order", async ({ page }) => {
  await page.route(inventoryApi, (route) => route.fulfill({ json: INVENTORY }));
  await page.route(docsApi, (route) => route.fulfill({ json: TREE_PAGES }));

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-page-item")).toHaveCount(3, { timeout: 15_000 });

  const items = page.getByTestId("knowledge-page-item");
  // Manual order: Handbook (sort 1) before Pricing (sort 2); Hours nests under Handbook.
  await expect(items.nth(0)).toContainText("Handbook");
  await expect(items.nth(0)).toHaveAttribute("data-depth", "0");
  await expect(items.nth(1)).toContainText("Hours");
  await expect(items.nth(1)).toHaveAttribute("data-depth", "1");
  await expect(items.nth(2)).toContainText("Pricing");
  await expect(items.nth(2)).toHaveAttribute("data-depth", "0");
});

test("breadcrumbs show the ancestor chain and navigate", async ({ page }) => {
  await page.route(inventoryApi, (route) => route.fulfill({ json: INVENTORY }));
  await page.route(docsApi, (route) => route.fulfill({ json: TREE_PAGES }));
  await page.route(docDetailApi, (route, request) => {
    const ref = detailRefOf(new URL(request.url()));
    return route.fulfill({ json: ref === REF_CHILD ? docFor(REF_CHILD, "Hours") : docFor(REF_PARENT, "Handbook") });
  });

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-page-item")).toHaveCount(3, { timeout: 15_000 });

  await page.getByTestId("knowledge-page-item").nth(1).click(); // Hours (the child)
  await expect(page.getByTestId("knowledge-doc-title")).toHaveText("Hours");
  const crumbs = page.getByTestId("knowledge-doc-breadcrumbs");
  await expect(crumbs).toContainText("Handbook / Hours");
  await crumbs.getByRole("button", { name: "Handbook" }).click();
  await expect(page.getByTestId("knowledge-doc-title")).toHaveText("Handbook");
  // The top-level parent has no chain of its own.
  await expect(page.getByTestId("knowledge-doc-breadcrumbs")).toHaveCount(0);
});

test("the Move panel re-parents via PATCH /location and refreshes the tree", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  let moved = false;
  await page.route(inventoryApi, (route) => route.fulfill({ json: INVENTORY }));
  await page.route(docsApi, (route) =>
    route.fulfill({
      json: moved
        ? { ...TREE_PAGES,
            documents: TREE_PAGES.documents.map((d) =>
              d.ref_id === REF_OTHER ? { ...d, parent_ref: REF_PARENT, sort_order: 5 } : d) }
        : TREE_PAGES,
    }),
  );
  await page.route(docDetailApi, async (route, request) => {
    const url = new URL(request.url());
    const path = decodeURIComponent(url.pathname);
    if (request.method() === "PATCH" && path.endsWith("/location")) {
      expect(path).toContain(REF_OTHER);
      const body = request.postDataJSON() as { parent_ref?: string | null };
      expect(body.parent_ref).toBe(REF_PARENT);
      moved = true;
      return route.fulfill({
        json: { ref_id: REF_OTHER, parent_ref: REF_PARENT, sort_order: 5, organize_available: true },
      });
    }
    return route.fulfill({ json: docFor(REF_OTHER, "Pricing") });
  });

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-page-item")).toHaveCount(3, { timeout: 15_000 });

  await page.getByTestId("knowledge-page-item").nth(2).click(); // Pricing
  await page.getByTestId("knowledge-doc-move").click();
  await page.getByTestId("knowledge-move-parent").selectOption(REF_PARENT);

  // The tree refreshed: Pricing now renders nested under Handbook.
  await expect(page.getByTestId("knowledge-page-item").nth(2)).toHaveAttribute("data-depth", "1", { timeout: 15_000 });
  await expect(page.getByTestId("knowledge-doc-move-panel")).toHaveCount(0); // closed on success

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("organize affordances stay hidden until the migration lands", async ({ page }) => {
  await page.route(inventoryApi, (route) => route.fulfill({ json: INVENTORY }));
  await page.route(docsApi, (route) =>
    route.fulfill({ json: { ...TREE_PAGES, organize_available: false,
                            documents: TREE_PAGES.documents.map((d) => ({ ...d, parent_ref: null })) } }),
  );
  await page.route(docDetailApi, (route) => route.fulfill({ json: docFor(REF_PARENT, "Handbook") }));

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-page-item")).toHaveCount(3, { timeout: 15_000 });
  await page.getByTestId("knowledge-page-item").first().click();
  await expect(page.getByTestId("knowledge-doc-title")).toBeVisible();
  await expect(page.getByTestId("knowledge-doc-move")).toHaveCount(0);
});

test("tree nodes collapse and expand (session-local)", async ({ page }) => {
  await page.route(inventoryApi, (route) => route.fulfill({ json: INVENTORY }));
  await page.route(docsApi, (route) => route.fulfill({ json: TREE_PAGES }));

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-page-item")).toHaveCount(3, { timeout: 15_000 });

  // Only the parent (the one row with children) shows a toggle.
  await expect(page.getByTestId("knowledge-page-toggle")).toHaveCount(1);
  await page.getByTestId("knowledge-page-toggle").click();
  // The subtree is hidden — and the page did NOT open (the toggle swallows the click).
  await expect(page.getByTestId("knowledge-page-item")).toHaveCount(2);
  await expect(page.getByTestId("knowledge-doc")).toHaveCount(0);
  await page.getByTestId("knowledge-page-toggle").click();
  await expect(page.getByTestId("knowledge-page-item")).toHaveCount(3);
});

test("'+ Sub-page' creates and nests under the open page in one flow", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  const SUB_REF = "upload:after-hours-dd33dd33";
  let nested = false;
  await page.route(inventoryApi, (route) => route.fulfill({ json: INVENTORY }));
  await page.route(docsApi, async (route, request) => {
    if (request.method() === "POST") {
      const body = request.postDataJSON() as { title: string };
      expect(body.title).toBe("After hours");
      return route.fulfill({
        status: 201,
        json: { ref_id: SUB_REF, chunks: 1, source: "upload", title: body.title },
      });
    }
    return route.fulfill({
      json: nested
        ? { ...TREE_PAGES, total: 4,
            documents: [...TREE_PAGES.documents,
              { ref_id: SUB_REF, title: "After hours", preview: "On-call rotation.", chunks: 1,
                editable: true, created_at: "2026-06-12T13:00:00+00:00",
                updated_at: "2026-06-12T13:00:00+00:00", parent_ref: REF_PARENT, sort_order: 9 }] }
        : TREE_PAGES,
    });
  });
  await page.route(docDetailApi, async (route, request) => {
    const url = new URL(request.url());
    const path = decodeURIComponent(url.pathname);
    if (request.method() === "PATCH" && path.endsWith("/location")) {
      expect(path).toContain(SUB_REF);
      const body = request.postDataJSON() as { parent_ref?: string | null };
      expect(body.parent_ref).toBe(REF_PARENT);
      nested = true;
      return route.fulfill({
        json: { ref_id: SUB_REF, parent_ref: REF_PARENT, sort_order: 9, organize_available: true },
      });
    }
    const ref = detailRefOf(url);
    return route.fulfill({ json: ref === SUB_REF ? docFor(SUB_REF, "After hours") : docFor(REF_PARENT, "Handbook") });
  });

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-page-item")).toHaveCount(3, { timeout: 15_000 });

  await page.getByTestId("knowledge-page-item").first().click(); // Handbook
  await page.getByTestId("knowledge-doc-subpage").click();
  await page.getByTestId("knowledge-add-title").fill("After hours");
  await page.getByTestId("knowledge-add-content").fill("On-call rotation and escalation.");
  await page.getByTestId("knowledge-add-submit").click();

  // Created AND nested: the rail shows it under Handbook, breadcrumbs prove the chain.
  await expect(page.getByTestId("knowledge-doc-title")).toHaveText("After hours", { timeout: 15_000 });
  await expect(page.getByTestId("knowledge-doc-breadcrumbs")).toContainText("Handbook / After hours");
  const subItem = page.getByTestId("knowledge-page-item").filter({ hasText: "After hours" }).first();
  await expect(subItem).toHaveAttribute("data-depth", "1");

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("drag a page onto another to nest it; the top dropzone un-nests", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  let parentNow: string | null = null; // Pricing's live parent in the stub
  await page.route(inventoryApi, (route) => route.fulfill({ json: INVENTORY }));
  await page.route(docsApi, (route) =>
    route.fulfill({
      json: { ...TREE_PAGES,
              documents: TREE_PAGES.documents.map((d) =>
                d.ref_id === REF_OTHER ? { ...d, parent_ref: parentNow, sort_order: 9 } : d) },
    }),
  );
  await page.route(docDetailApi, async (route, request) => {
    const url = new URL(request.url());
    const path = decodeURIComponent(url.pathname);
    if (request.method() === "PATCH" && path.endsWith("/location")) {
      expect(path).toContain(REF_OTHER);
      const body = request.postDataJSON() as { parent_ref?: string | null };
      parentNow = body.parent_ref ?? null;
      return route.fulfill({
        json: { ref_id: REF_OTHER, parent_ref: parentNow, sort_order: 9, organize_available: true },
      });
    }
    return route.fulfill({ json: docFor(REF_OTHER, "Pricing") });
  });

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-page-item")).toHaveCount(3, { timeout: 15_000 });

  const pricing = page.getByTestId("knowledge-page-item").filter({ hasText: "Pricing" }).first();
  const handbook = page.getByTestId("knowledge-page-item").filter({ hasText: "Handbook" }).first();

  // Drag Pricing onto Handbook -> nests (PATCH parent_ref) and the rail re-renders nested.
  await pricing.dragTo(handbook);
  await expect(
    page.getByTestId("knowledge-page-item").filter({ hasText: "Pricing" }).first(),
  ).toHaveAttribute("data-depth", "1", { timeout: 15_000 });

  // Drag it to the top dropzone -> back to top level.
  await page.getByTestId("knowledge-page-item").filter({ hasText: "Pricing" }).first()
    .dragTo(page.getByTestId("knowledge-pages-top-dropzone"));
  await expect(
    page.getByTestId("knowledge-page-item").filter({ hasText: "Pricing" }).first(),
  ).toHaveAttribute("data-depth", "0", { timeout: 15_000 });

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("dragging a parent onto its own child is refused — no request fires", async ({ page }) => {
  let patches = 0;
  await page.route(inventoryApi, (route) => route.fulfill({ json: INVENTORY }));
  await page.route(docsApi, (route) => route.fulfill({ json: TREE_PAGES }));
  await page.route(docDetailApi, async (route, request) => {
    if (request.method() === "PATCH") {
      patches += 1;
      return route.fulfill({ json: { ref_id: "x", parent_ref: null, sort_order: 0, organize_available: true } });
    }
    return route.fulfill({ json: docFor(REF_PARENT, "Handbook") });
  });

  await page.goto("/?view=knowledge");
  await expect(page.getByTestId("knowledge-page-item")).toHaveCount(3, { timeout: 15_000 });

  const handbook = page.getByTestId("knowledge-page-item").filter({ hasText: "Handbook" }).first();
  const hours = page.getByTestId("knowledge-page-item").filter({ hasText: "Hours" }).first();
  await handbook.dragTo(hours); // own child: the dropzone never accepts
  await page.waitForTimeout(300);
  expect(patches).toBe(0);
  // Tree unchanged.
  await expect(handbook).toHaveAttribute("data-depth", "0");
  await expect(hours).toHaveAttribute("data-depth", "1");
});
