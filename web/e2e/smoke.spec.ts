import { test, expect } from "@playwright/test";

// Smoke test: load the app shell and assert it actually mounted.
// We check that #root has children (React rendered something) and that a known
// navigation label from the sidebar is visible.
test("app shell mounts", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto("/");

  // #root should have mounted content
  const root = page.locator("#root");
  await expect(root).toBeAttached();
  await expect(root.locator("*").first()).toBeVisible({ timeout: 15_000 });

  // The shell renders the brand and a default screen heading (Command Center).
  await expect(page.getByText("Command Center").first()).toBeVisible({
    timeout: 15_000,
  });

  // Sidebar brand should be present.
  await expect(page.locator(".sidebar").first()).toBeVisible();

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});
