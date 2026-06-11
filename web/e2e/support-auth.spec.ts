import { test, expect } from "@playwright/test";

// Support-surface footer e2e — runs on the AUTH bundle (:4175, chromium-auth),
// the only project where a signed-out visitor sees the marketing landing at "/"
// (exactly what a production deploy serves). The filename ends in
// `auth.spec.ts` so the playwright project routing picks it up here, matching
// conversion.spec.ts's surface.
//
// Covers the footer entries this feature adds to the landing page:
//   - a "Status" link pointing at the public status page (/?view=status),
//   - a "Help" entry that opens the in-app support dialog (with Escape-close).

test("the landing footer exposes Status and Help entries", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator(".lp-footer")).toBeVisible({ timeout: 15_000 });

  // Status link points at the public status page.
  await expect(page.getByTestId("footer-status")).toHaveAttribute("href", "/?view=status");

  // The footer "Help & contact" entry opens the support dialog.
  await page.getByTestId("footer-help").click();
  await expect(page.getByTestId("support-dialog")).toBeVisible();

  // The dialog carries the form fields.
  await expect(page.getByTestId("support-subject")).toBeVisible();
  await expect(page.getByTestId("support-message")).toBeVisible();

  // Escape closes it (dialog a11y contract — matches the landing modals).
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("support-dialog")).toHaveCount(0);
});

test("the footer Help button in the brand row also opens the dialog", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator(".lp-footer")).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("footer-help-btn").click();
  await expect(page.getByTestId("support-dialog")).toBeVisible();
});
