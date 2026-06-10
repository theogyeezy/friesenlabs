import { test, expect, type Page } from "@playwright/test";

// Keyboard focus visibility — WCAG 2.4.7 (#127).
//
// Tab through the first interactive elements on BOTH public surfaces and assert
// the keyboard-dispatched focus is actually visible:
//   - links/buttons/tabbable widgets → a real outline (the global
//     `:focus-visible` rule in styles.css; `!important` so component-level and
//     inline `outline: none` resets can't kill it),
//   - text-entry controls (input/textarea/select) → the softer accent halo
//     (box-shadow) that is their designed focus indicator.
// Mouse clicks must stay ring-free — that's the :focus-visible contract.
//
// Surfaces (one shared bundle — main.tsx imports styles.css + landing.css):
//   - authed shell: the default mock build at baseURL (:4173, this project),
//   - marketing landing: the AUTH build on :4175 renders the signed-out
//     landing as the whole surface. That server is booted by the same
//     playwright.config webServer block, so hitting it from here is still
//     fully offline.

const LANDING_URL = "http://localhost:4175/";

type FocusInfo = {
  key: string;
  tag: string;
  outlineStyle: string;
  outlineWidth: number;
  boxShadow: string;
};

/** Computed focus styling of the currently focused element (null if body/none). */
async function activeFocusInfo(page: Page): Promise<FocusInfo | null> {
  return page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (!el || el === document.body || el === document.documentElement) return null;
    const cs = getComputedStyle(el);
    return {
      key: `${el.tagName}#${el.id}.${typeof el.className === "string" ? el.className : ""}@${el.textContent?.slice(0, 24) ?? ""}`,
      tag: el.tagName.toLowerCase(),
      outlineStyle: cs.outlineStyle,
      outlineWidth: parseFloat(cs.outlineWidth) || 0,
      boxShadow: cs.boxShadow,
    };
  });
}

/**
 * Press Tab repeatedly (real keyboard-dispatched focus, so :focus-visible
 * matches) and assert every element that receives focus shows a visible
 * indicator. Requires at least `minElements` distinct focusables.
 */
async function assertTabFocusVisible(page: Page, minElements: number): Promise<void> {
  const seen = new Map<string, FocusInfo>();
  for (let i = 0; i < 12 && seen.size < minElements + 2; i++) {
    await page.keyboard.press("Tab");
    const info = await activeFocusInfo(page);
    if (info) seen.set(info.key, info);
  }
  expect(
    seen.size,
    `expected at least ${minElements} tabbable interactive elements, saw ${seen.size}`,
  ).toBeGreaterThanOrEqual(minElements);

  let outlined = 0;
  for (const info of seen.values()) {
    const textEntry = info.tag === "input" || info.tag === "textarea" || info.tag === "select";
    if (textEntry) {
      // soft focus design: the accent halo is the visible indicator
      const visible = info.boxShadow !== "none" || (info.outlineStyle !== "none" && info.outlineWidth > 0);
      expect(visible, `no visible focus indicator on ${info.key}`).toBe(true);
    } else {
      // the issue's probe: keyboard focus must produce a real outline
      expect(info.outlineStyle, `outline suppressed on ${info.key}`).not.toBe("none");
      expect(info.outlineWidth, `zero-width outline on ${info.key}`).toBeGreaterThan(0);
      outlined++;
    }
  }
  expect(outlined, "no outlined link/button encountered while tabbing").toBeGreaterThan(0);
}

test("authed shell: tabbing shows a visible focus ring on the first interactive elements", async ({
  page,
}) => {
  // Skip the first-run onboarding/tour overlays so we tab the real shell.
  await page.addInitScript(() => {
    localStorage.setItem("fl_onboarded", "1");
    localStorage.setItem("fl_toured", "1");
  });
  await page.goto("/");
  await expect(page.getByText("Command Center").first()).toBeVisible({ timeout: 15_000 });

  await assertTabFocusVisible(page, 3);

  // Mouse focus stays ring-free: click a sidebar nav button and assert no outline.
  const navItem = page.locator(".nav-item").first();
  await navItem.click();
  const clicked = await activeFocusInfo(page);
  if (clicked && clicked.tag === "button") {
    expect(clicked.outlineStyle, "mouse click should not draw a focus ring").toBe("none");
  }
});

test("marketing landing: tabbing shows a visible focus ring on the first interactive elements", async ({
  page,
}) => {
  // The landing makes no authed API calls, but stub the control plane anyway
  // so nothing ever tries to leave the machine.
  await page.route("**/views/*", (route) => route.fulfill({ status: 404, json: { detail: "no" } }));
  await page.route("**/approvals", (route) => route.fulfill({ json: { approvals: [] } }));

  await page.goto(LANDING_URL);
  // Signed-out surface = the marketing landing with its Sign in control.
  await expect(page.locator("a.lp-signin", { hasText: "Sign in" }).first()).toBeVisible({
    timeout: 15_000,
  });

  await assertTabFocusVisible(page, 2);
});
