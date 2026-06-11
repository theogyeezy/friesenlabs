import { test, expect } from "@playwright/test";

// Signup funnel e2e (mock mode, fully offline). The SignupFlow is wired to the
// control-plane client, which defaults to mock mode, so no server and no real
// network are involved. PostHog is a hard no-op in mock mode (no key, disabled),
// so analytics make zero network calls.
//
// PAYMENT HONESTY: the client never fakes payment success. The mock checkout
// answers the server's internal-bypass shape ({checkout_url: null, bypass:
// "internal_comp"} — settled server-side, no Stripe page offline), so this spec
// exercises the bypass branch: advance to provisioning and let the GET /signup
// status poll — never the client — declare the workspace active. The Stripe
// redirect branch (checkout_url -> window.location.assign -> resume + poll) is
// covered offline in signup-real.spec.ts against the real bundle. Asserts:
//   1. the full funnel walks created -> ... -> active (the success step shows),
//   2. the explicit price consent ("You'll be charged $X/mo") renders BEFORE pay,
//   3. no password / verify token / OTP code is rendered back into the DOM,
//   4. no tenant_id, password, or token leaks into the DOM or into any analytics
//      call (we install a recording shim on window before the app loads),
//   5. the pending-checkout resume marker is cleaned up once the signup lands.

const PW = "Sup3rSecret!pw";
const EMAIL_TOKEN = "246810";
const PHONE_CODE = "135790";

test("signup funnel walks to active with price consent and no secret leaks", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  // Record every fetch URL the page issues, so we can prove no real network
  // ingestion (PostHog / API) happens in mock mode.
  const fetchedUrls: string[] = [];
  page.on("request", (r) => fetchedUrls.push(r.url()));

  // Install an analytics capture recorder BEFORE any app code runs. If the
  // wrapper were ever (incorrectly) enabled, this would catch every captured
  // event + props so we can assert no secret is ever passed to analytics.
  await page.addInitScript(() => {
    (window as unknown as { __phCaptures: unknown[] }).__phCaptures = [];
    (window as unknown as { __ph: unknown }).__ph = {
      init() {},
      capture(event: string, props?: unknown) {
        (window as unknown as { __phCaptures: unknown[] }).__phCaptures.push({ event, props });
      },
      reset() {},
    };
  });

  await page.goto("/?view=signup");

  const flow = page.getByTestId("signup-flow");
  await expect(flow).toBeVisible({ timeout: 15_000 });
  await expect(flow).toHaveAttribute("data-step", "account");

  // --- Step 1: account form + password strength meter ---------------------
  await page.getByTestId("su-email").fill("founder@riverside.example");
  const pwField = page.getByTestId("su-password");
  await pwField.fill(PW);
  await expect(page.getByTestId("pw-meter")).toBeVisible();
  await expect(page.getByTestId("pw-strength-label")).toContainText("Strong");
  // The password is masked in the DOM (type=password); its value is never text.
  await expect(pwField).toHaveAttribute("type", "password");

  await page.getByTestId("su-phone").fill("512 555 0142");
  await page.getByTestId("account-submit").click();

  // --- Step 2: email verify ----------------------------------------------
  await expect(flow).toHaveAttribute("data-step", "email", { timeout: 15_000 });
  await page.getByTestId("su-email-token").fill(EMAIL_TOKEN);
  await page.getByTestId("email-submit").click();

  // --- Step 3: phone verify ----------------------------------------------
  await expect(flow).toHaveAttribute("data-step", "phone", { timeout: 15_000 });
  await page.getByTestId("su-phone-code").fill(PHONE_CODE);
  await page.getByTestId("phone-submit").click();

  // --- Step 4: plan + EXPLICIT price consent shows BEFORE pay -------------
  await expect(flow).toHaveAttribute("data-step", "plan", { timeout: 15_000 });
  await page.getByTestId("plan-scale").click();
  const consent = page.getByTestId("price-consent");
  await expect(consent).toBeVisible();
  await expect(consent).toContainText("You'll be charged");
  await expect(page.getByTestId("price-consent-amount")).toHaveText("$799/mo");
  // The pay button must still be present (i.e., consent precedes the pay action).
  await expect(page.getByTestId("pay-submit")).toBeVisible();
  await page.getByTestId("pay-submit").click();

  // --- Step 5: provisioning poll -> Step 6: success ----------------------
  // The bypass-settled checkout advances to provisioning; only the status
  // poll (GET /signup -> "active") moves the flow to success.
  await expect(flow).toHaveAttribute("data-step", "provisioning", { timeout: 15_000 });
  await expect(flow).toHaveAttribute("data-step", "success", { timeout: 15_000 });
  await expect(page.getByTestId("step-success")).toContainText("You're all set");

  // The pending-checkout resume marker (written before handing the browser to
  // checkout, so the flow can resume after the round-trip) must be cleaned up
  // once the signup reaches active.
  const pendingMarker = await page.evaluate(() => sessionStorage.getItem("fl_signup_pending"));
  expect(pendingMarker).toBeNull();

  // --- Leak assertions: DOM ----------------------------------------------
  const bodyText = await page.evaluate(() => document.body.innerText);
  expect(bodyText).not.toContain(PW);
  expect(bodyText).not.toContain(EMAIL_TOKEN);
  expect(bodyText).not.toContain(PHONE_CODE);
  expect(bodyText).not.toContain("tenant_id");
  expect(bodyText).not.toContain("Bearer ");

  const html = await page.content();
  expect(html).not.toContain(PW);
  expect(html).not.toContain("tenant_id");

  // --- Leak assertions: analytics ----------------------------------------
  // The wrapper is disabled in mock mode, so nothing should have been captured
  // through our shim. Even if it had, none of these strings may appear.
  const captures = await page.evaluate(
    () => (window as unknown as { __phCaptures: unknown[] }).__phCaptures,
  );
  const capturesJson = JSON.stringify(captures);
  expect(capturesJson).not.toContain(PW);
  expect(capturesJson).not.toContain(EMAIL_TOKEN);
  expect(capturesJson).not.toContain(PHONE_CODE);
  expect(capturesJson).not.toContain("tenant_id");

  // --- No real network ingestion (mock mode + PostHog no-op) --------------
  const ingestHosts = fetchedUrls.filter(
    (u) => u.includes("posthog.com") || u.includes("/ph/") || /\/signup(\b|\/)/.test(new URL(u).pathname),
  );
  expect(ingestHosts, `unexpected network ingestion: ${ingestHosts.join(", ")}`).toHaveLength(0);

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});
