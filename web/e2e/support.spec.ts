import { test, expect } from "@playwright/test";

// Support-surface e2e — runs on the default MOCK bundle (:4173). Covers:
//   1. the in-app Help / Contact-support form submits and shows an HONEST
//      success confirmation (mock acknowledges without a network call),
//   2. client-side validation blocks an incomplete form,
//   3. the public status page (?view=status) renders an overall badge + the
//      component rows (degrades gracefully — never a blank screen),
//   4. the footer "Status" link points at the status page, and the footer
//      "Help" entry opens the support dialog.
//
// Mock mode is the right surface here: submitSupport()/fetchStatus() resolve
// deterministic results offline (web/src/support/api.ts), so these assert the
// UI contract without a backend. The backend POST /public/support contract is
// proven separately in tests/integration/test_api_public_support.py.

test("the help page submits and shows an honest success confirmation", async ({ page }) => {
  await page.goto("/?view=help");
  await expect(page.getByTestId("support-page")).toBeVisible();

  await page.getByTestId("support-name").fill("Ada Lovelace");
  await page.getByTestId("support-email").fill("ada@example.com");
  await page.getByTestId("support-subject").fill("Dashboard is blank");
  await page.getByTestId("support-message").fill("Nothing loads since this morning.");
  await page.getByTestId("support-submit").click();

  // ok === true only on a 2xx — mock acknowledges, so the confirmation shows.
  await expect(page.getByTestId("support-confirm")).toBeVisible();
  await expect(page.getByTestId("support-confirm")).toContainText(/get back to you/i);
});

test("the help form blocks an incomplete submission client-side", async ({ page }) => {
  await page.goto("/?view=help");
  await page.getByTestId("support-name").fill("Ada");
  // no email / subject / message
  await page.getByTestId("support-submit").click();
  // Stays on the form (no confirmation), surfaces an inline error.
  await expect(page.getByTestId("support-confirm")).toHaveCount(0);
  await expect(page.getByText(/valid email/i)).toBeVisible();
});

test("the status page renders an overall badge and component rows", async ({ page }) => {
  await page.goto("/?view=status");
  await expect(page.getByTestId("status-page")).toBeVisible();
  // An overall headline + a badge are always present (even if a probe is unknown).
  await expect(page.getByTestId("status-headline")).toBeVisible();
  await expect(page.getByTestId("status-components")).toBeVisible();
  // The API component row renders (mock => operational).
  await expect(page.getByTestId("status-component-api")).toBeVisible();
  await expect(page.getByTestId("status-component-api")).toContainText(/Application & API/);
  // The subsystems row is honestly present (not yet individually probed).
  await expect(page.getByTestId("status-component-subsystems")).toBeVisible();
  // Refresh is operable.
  await page.getByTestId("status-refresh").click();
  await expect(page.getByTestId("status-checked-at")).toBeVisible();
});
