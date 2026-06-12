import { test, expect, type Page } from "@playwright/test";

// Integrations panel e2e — fully offline, against the REAL production bundle.
// Runs in the `chromium-real` Playwright project (VITE_API_MOCK=0 baked at
// build time, Cognito unconfigured so auth is inert and the gate is open).
// Every API call is intercepted with page.route — no real network, no server.
// Asserts the panel is honest end to end:
//   1. the real shell routes Switchboard to the API-wired panel (not the
//      FLStore prototype, not the ComingSoon placeholder),
//   2. loading spinner while the list is in flight; statuses rendered straight
//      from the API including a VISIBLE "Unknown" badge for connected:null,
//   3. list failure -> friendly copy + working retry; never "API <code>",
//   4. connect flow: masked token input; the POST body carries the token ONLY
//      (no tenant_id); the token never appears in the page; per-status copy
//      for 503 (not configured on this deployment), 422 and 502 — no fake
//      success on any of them,
//   5. sync-now: 409 renders honest "connect first" copy; success reports only
//      the counts the server returned; 503 stays honest.

const HUBSPOT = {
  name: "hubspot",
  label: "HubSpot",
  category: "CRM & Marketing",
  description:
    "Sync companies, contacts, deals and notes from HubSpot CRM into your " +
    "Uplift data plane (read-only — Uplift never writes back).",
  connected: false as boolean | null,
  status: "not_connected",
};

const GOHIGHLEVEL = {
  name: "gohighlevel",
  label: "GoHighLevel",
  category: "CRM",
  description: "Sync opportunities and contacts from GoHighLevel.",
  connected: false as boolean | null,
  status: "not_connected",
};

const CSV_CONNECTOR = {
  name: "csv",
  label: "CSV Import",
  category: "Files & Imports",
  description:
    "Import contacts, companies or deals from a CSV export (up to 5MB). " +
    "Column mapping is auto-detected and can be overridden per upload.",
  kind: "file",
  connected: null as boolean | null,
  status: "available",
  experimental: false,
};

const LIST_NOT_CONNECTED = {
  integrations: [HUBSPOT],
  secrets_configured: true,
  sync_configured: true,
  csv_import_configured: true,
};

const LIST_CONNECTED = {
  integrations: [{ ...HUBSPOT, connected: true, status: "connected" }],
  secrets_configured: true,
  sync_configured: true,
  csv_import_configured: true,
};

const LIST_WITH_CSV = {
  integrations: [CSV_CONNECTOR],
  secrets_configured: true,
  sync_configured: true,
  csv_import_configured: true,
};

async function bodyText(page: Page): Promise<string> {
  return page.evaluate(() => document.body.innerText);
}

test("real shell routes Switchboard to the API-wired panel, not the prototype", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  // The shell lands on Command Center first — stub its surfaces too.
  await page.route("**/views/*", (route) =>
    route.fulfill({ status: 404, json: { detail: "no such view" } }),
  );
  await page.route("**/approvals", (route) => route.fulfill({ json: { approvals: [] } }));
  await page.route("**/integrations", (route) => route.fulfill({ json: LIST_NOT_CONNECTED }));

  await page.goto("/");
  await expect(page.getByTestId("dashboard-empty")).toBeVisible({ timeout: 15_000 });

  await page.locator(".nav-item", { hasText: "Switchboard" }).click();
  await expect(page.getByTestId("integrations-panel")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("coming-soon")).toHaveCount(0);

  // The list comes from the API: HubSpot, honestly not connected.
  const item = page.getByTestId("integration-item");
  await expect(item).toHaveCount(1);
  await expect(item).toContainText("HubSpot");
  await expect(page.getByTestId("int-status")).toContainText("Not connected");
  await expect(page.getByTestId("int-connected-count")).toContainText("0 of 1 connected");

  // No prototype chrome: the FLStore IntegrationHub banner never renders here.
  const text = await bodyText(page);
  expect(text).not.toContain("Open Sidecar");
  expect(text).not.toContain("Not ready to move?");

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("spinner while the list is in flight; statuses straight from the API", async ({ page }) => {
  await page.route("**/integrations", async (route) => {
    await new Promise((r) => setTimeout(r, 800));
    await route.fulfill({ json: LIST_NOT_CONNECTED });
  });

  await page.goto("/?view=integrations");

  // Spinner during the in-flight load; no premature count claim.
  await expect(page.getByTestId("int-loading")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("int-connected-count")).toHaveCount(0);

  await expect(page.getByTestId("integration-item")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("int-loading")).toHaveCount(0);
  await expect(page.getByTestId("int-status")).toContainText("Not connected");
});

test("connected:null renders a visible Unknown badge — never invented", async ({ page }) => {
  await page.route("**/integrations", (route) =>
    route.fulfill({
      json: {
        integrations: [{ ...HUBSPOT, connected: null, status: "unknown" }],
        secrets_configured: false,
        sync_configured: false,
      },
    }),
  );

  await page.goto("/?view=integrations");

  await expect(page.getByTestId("int-status")).toContainText("Unknown", { timeout: 15_000 });
  // The deployment note is honest about why connecting won't work.
  await expect(page.getByTestId("int-secrets-note")).toContainText("isn’t configured");
  // No connected claim anywhere.
  await expect(page.getByTestId("int-connected-count")).toContainText("0 of 1 connected");
});

test("list 500 -> friendly copy with retry; recovery renders the list", async ({ page }) => {
  let calls = 0;
  await page.route("**/integrations", async (route) => {
    calls += 1;
    if (calls === 1) {
      await route.fulfill({ status: 500, json: { detail: "db exploded" } });
    } else {
      await route.fulfill({ json: LIST_NOT_CONNECTED });
    }
  });

  await page.goto("/?view=integrations");

  const err = page.getByTestId("int-error");
  await expect(err).toBeVisible({ timeout: 15_000 });
  await expect(err).toContainText("went wrong on our side");
  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
  expect(text).not.toContain("db exploded");
  // Error and list never render together.
  await expect(page.getByTestId("integration-item")).toHaveCount(0);

  await page.getByTestId("int-retry").click();
  await expect(page.getByTestId("integration-item")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("int-error")).toHaveCount(0);
});

test("connect: masked input, token-only body, status flips from the API response", async ({ page }) => {
  const TOKEN = "pat-na1-supersecret-e2e-token";
  let postBody: Record<string, unknown> | null = null;

  await page.route("**/integrations", (route) => route.fulfill({ json: LIST_NOT_CONNECTED }));
  await page.route("**/integrations/*/credentials", async (route) => {
    postBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      json: {
        name: "hubspot",
        secret_ref: "uplift/tenant-e2e/hubspot",
        stored: true,
        status: "connected",
      },
    });
  });

  await page.goto("/?view=integrations");
  await expect(page.getByTestId("integration-item")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("int-connect-btn").click();
  const input = page.getByTestId("int-token-input");
  await expect(input).toBeVisible();
  // The token field is masked — never clear text in the DOM.
  await expect(input).toHaveAttribute("type", "password");

  // Save is disabled until a non-empty token is pasted (the 422 contract is
  // still handled server-side; see the dedicated 422 spec below).
  await expect(page.getByTestId("int-token-save")).toBeDisabled();
  await input.fill(TOKEN);
  await page.getByTestId("int-token-save").click();

  // Status straight from the response; honest success copy; form closed.
  await expect(page.getByTestId("int-status")).toContainText("Connected", { timeout: 15_000 });
  await expect(page.getByTestId("int-card-msg")).toHaveAttribute("data-kind", "ok");
  await expect(page.getByTestId("int-token-input")).toHaveCount(0);
  await expect(page.getByTestId("int-connected-count")).toContainText("1 of 1 connected");
  // Connected integrations gain the sync control.
  await expect(page.getByTestId("int-sync-btn")).toBeVisible();

  // The POST body carried the token ONLY — never a tenant_id (the trust rule).
  expect(postBody).not.toBeNull();
  expect(postBody).toEqual({ token: TOKEN });

  // The token is never echoed back into the page.
  const text = await bodyText(page);
  expect(text).not.toContain(TOKEN);
});

test("oauth: HubSpot offers a 'Connect with login' redirect; paste-key is the Advanced fallback", async ({ page }) => {
  await page.route("**/integrations", (route) => route.fulfill({ json: LIST_NOT_CONNECTED }));
  // /oauth/start is AUTH-GATED: it signs the tenant into the state and returns the
  // provider authorize_url as JSON. The panel fetches it (with the bearer) and then
  // redirects the browser to that URL. Mock the JSON, and the authorize page itself,
  // so the test neither needs a real token nor actually leaves for the provider.
  await page.route("**/integrations/hubspot/oauth/start", (route) =>
    route.fulfill({ json: { name: "hubspot", authorize_url: "https://app.hubspot.com/oauth/authorize?client_id=test&state=signed" } }),
  );
  await page.route("https://app.hubspot.com/oauth/authorize**", (route) =>
    route.fulfill({ contentType: "text/html", body: "<!doctype html><title>authorize</title>ok" }),
  );

  await page.goto("/?view=integrations");
  await expect(page.getByTestId("integration-item")).toBeVisible({ timeout: 15_000 });

  // HubSpot is OAuth-capable (no oauth_available flag in the payload -> the
  // graceful-degrade default treats "hubspot" as capable): a primary login button.
  const oauthBtn = page.getByTestId("int-oauth-btn");
  await expect(oauthBtn).toBeVisible();
  await expect(oauthBtn).toContainText("Connect with HubSpot");
  await expect(oauthBtn).toHaveAttribute("data-provider", "hubspot");

  // The paste-an-API-key path is still present, demoted to an Advanced fallback.
  await expect(page.getByTestId("int-connect-btn")).toContainText("Advanced: paste an API key instead");

  // Clicking fetches the signed start URL, then redirects to the provider's authorize page.
  await oauthBtn.click();
  await page.waitForURL("https://app.hubspot.com/oauth/authorize**", { timeout: 15_000 });
});

test("oauth: GoHighLevel offers a 'Connect with login' redirect; paste-key is the Advanced fallback", async ({ page }) => {
  await page.route("**/integrations", (route) =>
    route.fulfill({
      json: { integrations: [GOHIGHLEVEL], secrets_configured: true, sync_configured: true, csv_import_configured: true },
    }),
  );
  // /oauth/start is AUTH-GATED and returns the provider authorize_url as JSON; the
  // panel fetches it (with the bearer) and then redirects to that URL.
  await page.route("**/integrations/gohighlevel/oauth/start", (route) =>
    route.fulfill({ json: { name: "gohighlevel", authorize_url: "https://marketplace.gohighlevel.com/oauth/chooselocation?client_id=test&state=signed" } }),
  );
  await page.route("https://marketplace.gohighlevel.com/oauth/chooselocation**", (route) =>
    route.fulfill({ contentType: "text/html", body: "<!doctype html><title>authorize</title>ok" }),
  );

  await page.goto("/?view=integrations");
  await expect(page.getByTestId("integration-item")).toBeVisible({ timeout: 15_000 });

  // GoHighLevel is OAuth-capable (no oauth_available flag in the payload -> the
  // graceful-degrade default treats "gohighlevel" as capable): a primary login button.
  const oauthBtn = page.getByTestId("int-oauth-btn");
  await expect(oauthBtn).toBeVisible();
  await expect(oauthBtn).toContainText("Connect with GoHighLevel");
  await expect(oauthBtn).toHaveAttribute("data-provider", "gohighlevel");

  // The paste-an-API-key path is still present, demoted to an Advanced fallback.
  await expect(page.getByTestId("int-connect-btn")).toContainText("Advanced: paste an API key instead");

  // Clicking fetches the signed start URL, then redirects to the provider's authorize page.
  await oauthBtn.click();
  await page.waitForURL("https://marketplace.gohighlevel.com/oauth/chooselocation**", { timeout: 15_000 });
});

test("oauth: a connector that advertises oauth_available:false shows only the paste-key path", async ({ page }) => {
  // A non-hubspot sync connector that explicitly opts out of OAuth: no button,
  // never a broken one — just the existing "Connect" (paste-key) flow.
  const PIPEDRIVE = {
    name: "pipedrive",
    label: "Pipedrive",
    category: "CRM",
    description: "Sync deals and contacts from Pipedrive.",
    connected: false as boolean | null,
    status: "not_connected",
    oauth_available: false,
  };
  await page.route("**/integrations", (route) =>
    route.fulfill({
      json: { integrations: [PIPEDRIVE], secrets_configured: true, sync_configured: true, csv_import_configured: true },
    }),
  );

  await page.goto("/?view=integrations");
  await expect(page.getByTestId("integration-item")).toBeVisible({ timeout: 15_000 });

  await expect(page.getByTestId("int-oauth-btn")).toHaveCount(0);
  // The paste-key entry point keeps its plain "Connect" label (primary, not demoted).
  await expect(page.getByTestId("int-connect-btn")).toContainText("Connect");
  await expect(page.getByTestId("int-connect-btn")).not.toContainText("Advanced");
});

test("connect 503 -> 'not configured on this deployment' copy, no fake success", async ({ page }) => {
  await page.route("**/integrations", (route) => route.fulfill({ json: LIST_NOT_CONNECTED }));
  await page.route("**/integrations/*/credentials", (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "secret storage not configured — set INTEGRATIONS_REAL_SECRETS (REQ-006)" },
    }),
  );

  await page.goto("/?view=integrations");
  await expect(page.getByTestId("integration-item")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("int-connect-btn").click();
  await page.getByTestId("int-token-input").fill("some-token");
  await page.getByTestId("int-token-save").click();

  const msg = page.getByTestId("int-card-msg");
  await expect(msg).toBeVisible({ timeout: 15_000 });
  await expect(msg).toHaveAttribute("data-kind", "error");
  await expect(msg).toContainText("isn't configured on this deployment");

  // Still honestly not connected; no raw API string or env-var leakage.
  await expect(page.getByTestId("int-status")).toContainText("Not connected");
  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
  expect(text).not.toContain("INTEGRATIONS_REAL_SECRETS");
});

test("connect 422 -> honest empty-token copy", async ({ page }) => {
  await page.route("**/integrations", (route) => route.fulfill({ json: LIST_NOT_CONNECTED }));
  await page.route("**/integrations/*/credentials", (route) =>
    route.fulfill({ status: 422, json: { detail: "token must be non-empty" } }),
  );

  await page.goto("/?view=integrations");
  await expect(page.getByTestId("integration-item")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("int-connect-btn").click();
  await page.getByTestId("int-token-input").fill("looks-fine-client-side");
  await page.getByTestId("int-token-save").click();

  const msg = page.getByTestId("int-card-msg");
  await expect(msg).toBeVisible({ timeout: 15_000 });
  await expect(msg).toHaveAttribute("data-kind", "error");
  await expect(msg).toContainText("token can't be empty");
  await expect(page.getByTestId("int-status")).toContainText("Not connected");
});

test("connect 502 -> vault-write-failed copy, nothing claimed stored", async ({ page }) => {
  await page.route("**/integrations", (route) => route.fulfill({ json: LIST_NOT_CONNECTED }));
  await page.route("**/integrations/*/credentials", (route) =>
    route.fulfill({ status: 502, json: { detail: "secret store write failed" } }),
  );

  await page.goto("/?view=integrations");
  await expect(page.getByTestId("integration-item")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("int-connect-btn").click();
  await page.getByTestId("int-token-input").fill("some-token");
  await page.getByTestId("int-token-save").click();

  const msg = page.getByTestId("int-card-msg");
  await expect(msg).toBeVisible({ timeout: 15_000 });
  await expect(msg).toHaveAttribute("data-kind", "error");
  await expect(msg).toContainText("Nothing was stored");
  await expect(page.getByTestId("int-status")).toContainText("Not connected");
  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
});

test("sync 409 -> honest 'connect first' copy, no fake run", async ({ page }) => {
  // The list claims connected (e.g. stale), but the sync-time vault check says
  // otherwise — the API's 409 must surface honestly.
  await page.route("**/integrations", (route) => route.fulfill({ json: LIST_CONNECTED }));
  await page.route("**/integrations/*/sync", (route) =>
    route.fulfill({
      status: 409,
      json: { detail: "connect hubspot first — no per-tenant credential is vaulted" },
    }),
  );

  await page.goto("/?view=integrations");
  await expect(page.getByTestId("int-sync-btn")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("int-sync-btn").click();

  const msg = page.getByTestId("int-card-msg");
  await expect(msg).toBeVisible({ timeout: 15_000 });
  await expect(msg).toHaveAttribute("data-kind", "error");
  await expect(msg).toContainText("Connect this integration first");
  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
});

test("sync success reports only the counts the server returned", async ({ page }) => {
  await page.route("**/integrations", (route) => route.fulfill({ json: LIST_CONNECTED }));
  await page.route("**/integrations/*/sync", (route) =>
    route.fulfill({
      json: {
        name: "hubspot",
        result: { pulled: 7, landed_rows: 7, chunks: 12, embedded: 12, skipped: 1, cursor: "2026-06-09T00:00:00Z" },
      },
    }),
  );

  await page.goto("/?view=integrations");
  await expect(page.getByTestId("int-sync-btn")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("int-sync-btn").click();

  const msg = page.getByTestId("int-card-msg");
  await expect(msg).toBeVisible({ timeout: 15_000 });
  await expect(msg).toHaveAttribute("data-kind", "ok");
  await expect(msg).toContainText("Sync finished: 7 pulled, 7 landed, 12 embedded, 1 skipped.");
});

test("sync 503 -> honest 'not configured' copy", async ({ page }) => {
  await page.route("**/integrations", (route) => route.fulfill({ json: LIST_CONNECTED }));
  await page.route("**/integrations/*/sync", (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "sync not configured — the ingestion plane is not wired on this task" },
    }),
  );

  await page.goto("/?view=integrations");
  await expect(page.getByTestId("int-sync-btn")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("int-sync-btn").click();

  const msg = page.getByTestId("int-card-msg");
  await expect(msg).toBeVisible({ timeout: 15_000 });
  await expect(msg).toHaveAttribute("data-kind", "error");
  await expect(msg).toContainText("Sync isn't configured on this deployment");
  const text = await bodyText(page);
  expect(text).not.toContain("ingestion plane");
  expect(text).not.toMatch(/API \d+/);
});

// ---------------------------------------------------------------------------
// CSV import (file-kind card) — honest controls + honest error paths.
// ---------------------------------------------------------------------------

test("csv card renders entity picker + file input; no credential form", async ({ page }) => {
  await page.route("**/integrations", (route) => route.fulfill({ json: LIST_WITH_CSV }));

  await page.goto("/?view=integrations");
  await expect(page.getByTestId("integration-item")).toBeVisible({ timeout: 15_000 });

  // The CSV card shows its upload controls — not the credential-token form.
  await expect(page.getByTestId("csv-import-form")).toBeVisible();
  await expect(page.getByTestId("csv-entity-picker")).toBeVisible();
  await expect(page.getByTestId("csv-file-input")).toBeVisible();
  await expect(page.getByTestId("csv-import-submit")).toBeVisible();

  // The credential form (int-token-input) and sync button must NOT appear for a
  // file-kind card — CSV has no vault slot.
  await expect(page.getByTestId("int-token-input")).toHaveCount(0);
  await expect(page.getByTestId("int-connect-btn")).toHaveCount(0);
  await expect(page.getByTestId("int-sync-btn")).toHaveCount(0);

  // Import button is disabled until a file is selected.
  await expect(page.getByTestId("csv-import-submit")).toBeDisabled();
});

test("csv import 503 -> 'not enabled on this deployment' copy, no fake rows-landed", async ({ page }) => {
  await page.route("**/integrations", (route) => route.fulfill({ json: LIST_WITH_CSV }));
  await page.route("**/integrations/csv/import", (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "csv import not configured — the ingestion plane is not wired on this task" },
    }),
  );

  await page.goto("/?view=integrations");
  await expect(page.getByTestId("csv-import-form")).toBeVisible({ timeout: 15_000 });

  // Inject a dummy file so the import button becomes enabled.
  await page.getByTestId("csv-file-input").setInputFiles({
    name: "test.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("name,email\nAlice,alice@example.com"),
  });
  await expect(page.getByTestId("csv-import-submit")).toBeEnabled();
  await page.getByTestId("csv-import-submit").click();

  const msg = page.getByTestId("int-card-msg");
  await expect(msg).toBeVisible({ timeout: 15_000 });
  await expect(msg).toHaveAttribute("data-kind", "error");
  // Honest copy — not a fake success, not a raw API string.
  await expect(msg).toContainText("isn't enabled on this deployment");
  // The import result block must NOT appear — no rows were landed.
  await expect(page.getByTestId("csv-import-result")).toHaveCount(0);
  const text = await bodyText(page);
  expect(text).not.toMatch(/API \d+/);
  expect(text).not.toContain("ingestion plane");
});

// ---------------------------------------------------------------------------
// Release-readiness surfaces: last-synced line, disconnect, async (202) sync.
// ---------------------------------------------------------------------------

const RUN = {
  id: "run-1",
  source: "hubspot",
  triggered_by: "api" as const,
  status: "succeeded" as const,
  started_at: "2026-06-11T10:00:00Z",
  finished_at: "2026-06-11T10:02:00Z",
  pulled: 12,
  landed_rows: 12,
  chunks: 30,
  embedded: 28,
  skipped: 2,
  error: null as string | null,
};

test("last-synced line renders straight from the API; absent history renders nothing", async ({ page }) => {
  await page.route("**/integrations", (route) =>
    route.fulfill({
      json: {
        integrations: [
          { ...HUBSPOT, connected: true, status: "connected", last_sync: RUN },
        ],
        secrets_configured: true,
        sync_configured: true,
        csv_import_configured: true,
        sync_history_configured: true,
      },
    }),
  );

  await page.goto("/?view=integrations");

  const line = page.getByTestId("int-last-sync");
  await expect(line).toBeVisible({ timeout: 15_000 });
  await expect(line).toContainText("Last synced");
  await expect(line).toContainText("12 landed");

  // And the inverse: no last_sync -> no line, never an invented timestamp.
  await page.unroute("**/integrations");
  await page.route("**/integrations", (route) => route.fulfill({ json: LIST_CONNECTED }));
  await page.reload();
  await expect(page.getByTestId("integration-item")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("int-last-sync")).toHaveCount(0);
});

test("disconnect: inline confirm first, DELETE on confirm only, status flips from the response", async ({ page }) => {
  const deletes: string[] = [];
  await page.route("**/integrations", (route) => route.fulfill({ json: LIST_CONNECTED }));
  await page.route("**/integrations/hubspot/credentials", async (route) => {
    if (route.request().method() !== "DELETE") return route.fallback();
    deletes.push(route.request().url());
    await route.fulfill({
      json: { name: "hubspot", deleted: true, status: "not_connected" },
    });
  });

  await page.goto("/?view=integrations");
  await expect(page.getByTestId("int-disconnect-btn")).toBeVisible({ timeout: 15_000 });

  // Step 1: the destructive click only opens the inline confirm — no DELETE yet.
  await page.getByTestId("int-disconnect-btn").click();
  await expect(page.getByTestId("int-disconnect-confirm")).toBeVisible();
  expect(deletes).toHaveLength(0);

  // Cancel keeps the connection (still no DELETE).
  await page.getByTestId("int-disconnect-cancel").click();
  await expect(page.getByTestId("int-disconnect-confirm")).toHaveCount(0);
  expect(deletes).toHaveLength(0);

  // Confirm fires exactly one DELETE; the badge flips from the response.
  await page.getByTestId("int-disconnect-btn").click();
  await page.getByTestId("int-disconnect-confirm").click();
  await expect(page.getByTestId("int-card-msg")).toContainText("disconnected", { timeout: 15_000 });
  expect(deletes).toHaveLength(1);
  await expect(page.getByTestId("int-status")).toContainText("Not connected");
});

test("async sync: 202 run accepted, panel polls history and reports the recorded counts", async ({ page }) => {
  await page.route("**/integrations", (route) => route.fulfill({ json: LIST_CONNECTED }));
  await page.route("**/integrations/hubspot/sync", (route) =>
    route.fulfill({
      status: 202,
      json: { name: "hubspot", run: { ...RUN, status: "running", finished_at: null } },
    }),
  );
  // First (immediate) poll already finds the run finished with its counts.
  await page.route("**/integrations/hubspot/syncs", (route) =>
    route.fulfill({ json: { name: "hubspot", runs: [RUN] } }),
  );

  await page.goto("/?view=integrations");
  await expect(page.getByTestId("int-sync-btn")).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("int-sync-btn").click();

  // The settled message reports ONLY the recorded counts, via the history poll.
  await expect(page.getByTestId("int-card-msg")).toContainText("Sync finished: 12 pulled", {
    timeout: 15_000,
  });
  await expect(page.getByTestId("int-card-msg")).toContainText("28 embedded");
});
