import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

// Accessibility layer: axe-core asserts WCAG 2 A/AA on the key REAL-APP surfaces, in a real
// browser, in CI. This catches a11y regressions (contrast, names, roles, landmarks) that neither
// unit nor logic tests can see — it already drove fixes (missing form labels + two too-light design
// tokens). We gate on serious/critical impact so the suite is meaningful but not blocked by cosmetic
// best-practice notes. (The marketing landing renders only in the real build + had a Lighthouse ~100
// audit; adding it under chromium-real is a tracked follow-up — see TESTING.md.)
const SURFACES: Array<{ name: string; path: string; ready: string }> = [
  { name: "chat", path: "/?view=chat", ready: '[data-testid="chat-dock"]' },
  { name: "greenlight", path: "/?view=greenlight", ready: '[data-testid="greenlight-queue"]' },
  { name: "dashboards", path: "/?view=dashboards", ready: '[data-testid="dashboards-view"]' },
];

for (const s of SURFACES) {
  test(`a11y: ${s.name} has no serious/critical axe violations`, async ({ page }) => {
    await page.goto(s.path, { waitUntil: "domcontentloaded" });
    await page.locator(s.ready).first().waitFor({ state: "visible", timeout: 20_000 });

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();

    const blocking = results.violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    // Surface a readable summary on failure (rule id + where) instead of a giant blob.
    const summary = blocking.map(
      (v) => `${v.id} (${v.impact}) — ${v.nodes.length} node(s): ${v.help}`,
    );
    expect(blocking, `serious/critical a11y violations on ${s.name}:\n${summary.join("\n")}`).toEqual(
      [],
    );
  });
}
