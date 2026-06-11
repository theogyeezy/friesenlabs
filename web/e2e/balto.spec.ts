import { test, expect } from "@playwright/test";

// Balto — NL view creation in chat (mock build, fully offline against the fake
// runtime fixtures). Covers the owner-spec flow end to end:
//   1. a view-shaped ask shows the EXACT Balto status line while the agent works,
//   2. a button appears in chat that opens the new visualization,
//   3. the overlay renders via the trusted SpecRenderer and backs out with the X,
//   4. the user can SAVE the view (existing saved-view store) or discard it,
//   5. data that does not exist on the platform gets the honest refusal,
//   6. the views dropdown selects among the tenant's saved views.

const BALTO_STATUS =
  "Our synthesizing agent Balto is mushing away to get this view for you.";

test("chat -> Balto -> button -> overlay -> X -> save", async ({ page }) => {
  await page.goto("/?view=chat");
  await expect(page.getByTestId("chat-dock")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("chat-input").fill("Show me a chart of deals by stage");
  await page.getByTestId("chat-send").click();

  // The EXACT Balto status line (owner spec) renders in the thread.
  await expect(page.getByText(BALTO_STATUS, { exact: true })).toBeVisible({ timeout: 15_000 });

  // On success a button APPEARS IN CHAT that opens the new visualization.
  const open = page.getByTestId("balto-open-view");
  await expect(open).toBeVisible({ timeout: 15_000 });
  await open.click();

  // The overlay renders the view through the existing spec renderer (KPI + chart svg).
  const overlay = page.getByTestId("view-overlay");
  await expect(overlay).toBeVisible();
  await expect(overlay.getByTestId("kpi-card").first()).toBeVisible({ timeout: 15_000 });
  await expect(overlay.getByTestId("chart-host").locator("svg").first()).toBeVisible({
    timeout: 15_000,
  });
  // Save and discard are both offered (the user has the OPTION to save).
  await expect(overlay.getByTestId("view-overlay-save")).toBeVisible();
  await expect(overlay.getByTestId("view-overlay-discard")).toBeVisible();

  // The X backs out of the visualization.
  await page.getByTestId("view-overlay-close").click();
  await expect(page.getByTestId("view-overlay")).toHaveCount(0);

  // Reopen from the same chat button, then save — persists via the saved-view store.
  await open.click();
  await page.getByTestId("view-overlay-save").click();
  await expect(page.getByTestId("view-overlay-saved")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("view-overlay-saved")).toHaveText(/version 1/);
});

test("discard keeps the view ephemeral (nothing persisted, chat keeps working)", async ({
  page,
}) => {
  await page.goto("/?view=chat");
  await page.getByTestId("chat-input").fill("Build a dashboard of contacts");
  await page.getByTestId("chat-send").click();

  const open = page.getByTestId("balto-open-view");
  await expect(open).toBeVisible({ timeout: 15_000 });
  await open.click();
  await expect(page.getByTestId("view-overlay")).toBeVisible();

  await page.getByTestId("view-overlay-discard").click();
  await expect(page.getByTestId("view-overlay")).toHaveCount(0);
  // No saved confirmation ever appeared — the draft was never persisted.
  await expect(page.getByTestId("view-overlay-saved")).toHaveCount(0);
});

test("data that does not exist on the platform gets the honest refusal", async ({ page }) => {
  await page.goto("/?view=chat");
  await page.getByTestId("chat-input").fill("Graph the daily weather in Austin");
  await page.getByTestId("chat-send").click();

  // Balto still mushes first...
  await expect(page.getByText(BALTO_STATUS, { exact: true })).toBeVisible({ timeout: 15_000 });
  // ...then the honest answer: no Cube member can answer it, so no view is invented.
  await expect(
    page.getByText(
      "Your request cannot be fulfilled because the data does not exist on the platform.",
      { exact: true },
    ),
  ).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("balto-open-view")).toHaveCount(0);
});

test("views dropdown selects among the tenant's saved views", async ({ page }) => {
  await page.goto("/?view=dashboard");
  await expect(page.getByTestId("dashboard-view")).toBeVisible({ timeout: 15_000 });

  // The dropdown lists the seeded saved views (latest version per view_id).
  const dropdown = page.getByTestId("views-dropdown");
  await expect(dropdown).toBeVisible({ timeout: 15_000 });
  await expect(dropdown.locator("option")).toHaveText([
    /Pipeline overview/,
    /Won deals/,
  ]);

  // Default view renders first...
  await expect(page.getByTestId("chart-host").first()).toBeVisible({ timeout: 15_000 });

  // ...and selecting another saved view swaps the rendered spec.
  await dropdown.selectOption("won_deals");
  await expect(page.getByText("Deals won")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("chart-host")).toHaveCount(0);
});
