import { test, expect, type Page } from "@playwright/test";

// Signup checkout e2e — REAL production bundle (chromium-real project,
// VITE_API_MOCK=0 baked at build time), fully offline: every API call AND the
// Stripe-hosted Checkout page itself are intercepted with page.route, so no
// real network, server, or Stripe are involved. Asserts the honest payment
// contract end to end:
//   1. POST /signup/{id}/checkout answers {checkout_url} and the browser is
//      SENT THERE (window.location.assign) — the client never fakes a payment
//      success and never captures a client-side payment_succeeded,
//   2. returning from Stripe resumes the flow from the pending sessionStorage
//      marker and POLLS GET /signup/{id} until the webhook-driven state
//      reaches "active" — success is the server's word, never the redirect's
//      (the first poll answers "paid" to prove the flow waits out webhook lag),
//   3. a cancelled checkout lands back on the plan step with honest "you
//      haven't been charged" copy and clears the resume marker,
//   4. the env-gated internal bypass response ({checkout_url: null, bypass:
//      "internal_comp"}) advances to provisioning without ever visiting Stripe.
//
// NOTE on routing: the document lives at /?view=signup, which a plain
// "**/signup" glob would ALSO match (** spans the query string). Every API
// stub therefore matches on url.pathname exclusively (same as
// dashboards.spec.ts / reports.spec.ts).

const ACCT = "acct_e2e_checkout";
const STRIPE_URL = "https://checkout.stripe.test/c/pay/cs_e2e_1";
const PENDING_KEY = "fl_signup_pending";

const EMAIL = "founder@riverside.example";
const PW = "Sup3rSecret!pw";

// Stub the pre-checkout funnel legs (signup + both verifies) for one account.
async function stubFunnel(page: Page, accountId: string) {
  await page.route((url) => url.pathname === "/signup", (route) =>
    route.fulfill({ json: { account_id: accountId, state: "created" } }),
  );
  await page.route((url) => url.pathname === `/signup/${accountId}/verify-email`, (route) =>
    route.fulfill({ json: { state: "email_verified", email_verified: true } }),
  );
  await page.route((url) => url.pathname === `/signup/${accountId}/verify-phone`, (route) =>
    route.fulfill({ json: { state: "phone_verified", phone_verified: true } }),
  );
}

// Walk the UI from the account form to the plan step.
async function walkToPlan(page: Page) {
  await page.goto("/?view=signup");
  const flow = page.getByTestId("signup-flow");
  await expect(flow).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("su-email").fill(EMAIL);
  await page.getByTestId("su-password").fill(PW);
  await page.getByTestId("su-phone").fill("512 555 0142");
  await page.getByTestId("account-submit").click();
  await expect(flow).toHaveAttribute("data-step", "email", { timeout: 15_000 });
  await page.getByTestId("su-email-token").fill("246810");
  await page.getByTestId("email-submit").click();
  await expect(flow).toHaveAttribute("data-step", "phone", { timeout: 15_000 });
  await page.getByTestId("su-phone-code").fill("135790");
  await page.getByTestId("phone-submit").click();
  await expect(flow).toHaveAttribute("data-step", "plan", { timeout: 15_000 });
  return flow;
}

test("checkout redirects the browser to Stripe, then resume polls status to active", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await stubFunnel(page, ACCT);
  await page.route((url) => url.pathname === `/signup/${ACCT}/checkout`, (route) =>
    route.fulfill({
      json: {
        checkout_id: "cs_e2e_1",
        stripe_customer_id: "cus_e2e_1",
        checkout_url: STRIPE_URL,
      },
    }),
  );
  // The Stripe-hosted page, stubbed: proves the browser was actually SENT to
  // checkout_url (window.location.assign), not that payment was faked in-app.
  await page.route("https://checkout.stripe.test/**", (route) =>
    route.fulfill({ contentType: "text/html", body: "<html><body>Stripe Checkout (stubbed)</body></html>" }),
  );
  // The status poll. The first answer is the honest "paid" (the signed webhook
  // landed but provisioning is still running); every poll after that stays
  // non-terminal ("provisioning") until the test has OBSERVED the provisioning
  // step and flips `releaseActive`, at which point a later poll reports "active".
  //
  // Holding the intermediate state open (instead of flipping to "active" on the
  // very next, immediate poll tick) is what kills the flake: provisioning -> success
  // used to race the DOM sampler — the app could reach "success" within a single
  // route round-trip, before Playwright ever sampled "provisioning", timing out
  // the assertion. The contract is unchanged: success is still only ever the
  // server's word, reached via >= 2 polls, never the redirect's.
  let statusCalls = 0;
  let releaseActive = false;
  await page.route((url) => url.pathname === `/signup/${ACCT}`, (route) => {
    statusCalls += 1;
    const state = releaseActive ? "active" : statusCalls === 1 ? "paid" : "provisioning";
    route.fulfill({ json: { account_id: ACCT, state, tenant_id: null } });
  });

  await walkToPlan(page);
  await page.getByTestId("pay-submit").click();

  // The browser lands on Stripe's hosted page — a real navigation away.
  await page.waitForURL("https://checkout.stripe.test/**", { timeout: 15_000 });

  // The resume marker survived the hand-off (same-origin sessionStorage).
  // Simulate Stripe's success_url redirect back into the SPA.
  await page.goto("/?view=signup&checkout=success");
  const flow = page.getByTestId("signup-flow");
  await expect(flow).toBeVisible({ timeout: 15_000 });

  // Resume: never "success" straight from the redirect — the flow polls. The
  // stub holds the funnel in provisioning until we've observed that step, so
  // this assertion can't lose the race to an instant "active" flip.
  await expect(flow).toHaveAttribute("data-step", "provisioning", { timeout: 15_000 });
  // Provisioning observed — now let the next poll report the webhook-completed
  // state, proving the flow advances on the server's word, not the redirect's.
  releaseActive = true;
  await expect(flow).toHaveAttribute("data-step", "success", { timeout: 15_000 });
  await expect(page.getByTestId("step-success")).toContainText("You're all set");
  expect(statusCalls).toBeGreaterThanOrEqual(2);

  // The marker is consumed once the signup lands.
  const pending = await page.evaluate(() => sessionStorage.getItem("fl_signup_pending"));
  expect(pending).toBeNull();

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("a cancelled checkout lands back on the plan step with honest no-charge copy", async ({ page }) => {
  const acct = "acct_e2e_cancel";
  // Park the resume marker exactly as submitCheckout does before redirecting,
  // then arrive on the cancel_url. The server says the account never paid.
  await page.addInitScript(
    ([key, value]) => sessionStorage.setItem(key, value),
    [PENDING_KEY, acct] as const,
  );
  await page.route((url) => url.pathname === `/signup/${acct}`, (route) =>
    route.fulfill({ json: { account_id: acct, state: "phone_verified", tenant_id: null } }),
  );

  await page.goto("/?view=signup&checkout=cancel");
  const flow = page.getByTestId("signup-flow");
  await expect(flow).toBeVisible({ timeout: 15_000 });

  // Honest landing: back on the plan step, told nothing was charged.
  await expect(flow).toHaveAttribute("data-step", "plan", { timeout: 15_000 });
  await expect(page.getByTestId("signup-error")).toContainText("haven't been charged");

  // The marker is cleared so a later visit doesn't resume a dead checkout.
  const pending = await page.evaluate(() => sessionStorage.getItem("fl_signup_pending"));
  expect(pending).toBeNull();
});

test("the internal bypass response advances to provisioning without visiting Stripe", async ({ page }) => {
  const acct = "acct_e2e_bypass";
  const stripeNavs: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("stripe")) stripeNavs.push(r.url());
  });

  await stubFunnel(page, acct);
  // The env-gated internal-domain bypass: settled server-side through the same
  // idempotent ledger + provisioning path as the webhook — no Stripe page.
  await page.route((url) => url.pathname === `/signup/${acct}/checkout`, (route) =>
    route.fulfill({
      json: { checkout_url: null, bypass: "internal_comp", handled: true, account_id: acct },
    }),
  );
  let statusCalls = 0;
  await page.route((url) => url.pathname === `/signup/${acct}`, (route) => {
    statusCalls += 1;
    route.fulfill({
      json: { account_id: acct, state: statusCalls === 1 ? "provisioning" : "active", tenant_id: null },
    });
  });

  const flow = await walkToPlan(page);
  await page.getByTestId("pay-submit").click();

  // Straight to provisioning (the server settled it), then the poll decides.
  await expect(flow).toHaveAttribute("data-step", "provisioning", { timeout: 15_000 });
  await expect(flow).toHaveAttribute("data-step", "success", { timeout: 15_000 });
  expect(stripeNavs, `unexpected Stripe traffic: ${stripeNavs.join(", ")}`).toHaveLength(0);
});
