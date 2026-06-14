import { test, expect } from "@playwright/test";

// Visual-regression layer (opt-in). Pixel-diffs key surfaces against committed baselines — the only
// layer that catches "the CSS broke and the control is invisible/misplaced", which DOM-assertion
// tests pass straight through. Runs only under the `visual` project with RUN_VISUAL=1 because
// baselines are PLATFORM-SPECIFIC and must be generated in the CI environment
// (npm run test:visual:update there, then commit). See TESTING.md.
test.skip(!process.env.RUN_VISUAL, "visual regression is opt-in (set RUN_VISUAL=1)");

test("visual: landing page", async ({ page }) => {
  await page.goto("/");
  await page.locator("main").first().waitFor({ state: "visible" });
  // Mask the live-pulse/clock chrome that legitimately changes between runs.
  await expect(page).toHaveScreenshot("landing.png", { fullPage: true });
});

test("visual: chat empty state", async ({ page }) => {
  await page.goto("/?view=chat");
  await page.getByTestId("chat-dock").waitFor({ state: "visible" });
  await expect(page.getByTestId("chat-dock")).toHaveScreenshot("chat-dock.png");
});

test("visual: greenlight queue", async ({ page }) => {
  await page.goto("/?view=greenlight");
  await page.getByTestId("greenlight-queue").waitFor({ state: "visible" });
  await expect(page.getByTestId("greenlight-queue")).toHaveScreenshot("greenlight-queue.png");
});
