import { expect, test } from "@playwright/test";

// Proves the prototype-feed XSS is fixed: a malicious f.html payload routed through SafeHtml is
// sanitized — no script/onerror runs, but safe markup survives.
test("feed HTML is sanitized: script/onerror neutralized, safe markup kept", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto("/?view=safehtml-demo");

  const feed = page.locator('[data-testid="feed"]');
  await expect(feed).toBeVisible();

  // Safe markup survived.
  await expect(feed.locator("b")).toHaveText("safe bold");

  // The injected handler never fired and the script tag was stripped.
  const pwned = await page.evaluate(() => (window as any).__pwned);
  expect(pwned).toBeUndefined();
  expect(await feed.locator("script").count()).toBe(0);
  // The onerror img either has no onerror attr or no src that triggers it.
  const hasOnerror = await feed.evaluate((el) =>
    Array.from(el.querySelectorAll("img")).some((i) => i.hasAttribute("onerror")),
  );
  expect(hasOnerror).toBe(false);
  expect(errors).toEqual([]);
});
