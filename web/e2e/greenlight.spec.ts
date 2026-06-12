import { test, expect } from "@playwright/test";

// Phase 9b Greenlight e2e (mock mode, fully offline). The approval queue is wired
// to the control-plane client; the client defaults to mock mode so no server is
// needed. Asserts:
//   1. a pending item shows its reasoning + value at stake + an editable draft,
//   2. approving it removes it from the queue,
//   3. the raw bearer token and the full proposed-action payload never render.

test("greenlight queue shows reasoning + value, approve removes the item", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto("/?view=greenlight");

  const queue = page.getByTestId("greenlight-queue");
  await expect(queue).toBeVisible({ timeout: 15_000 });

  // Two seeded pending items.
  const items = page.getByTestId("approval-item");
  await expect(items).toHaveCount(2, { timeout: 15_000 });

  const first = items.first();

  // Reasoning is shown.
  await expect(first.getByTestId("approval-reasoning")).toContainText("Renewal is 11 days out");

  // Value at stake is shown.
  await expect(first.getByTestId("approval-value")).toContainText("$22.1k at stake");

  // Editable draft is present and editable.
  const draft = first.getByTestId("approval-draft");
  await expect(draft).toBeVisible();
  await expect(draft).toBeEditable();

  // Approving the first item removes it from the queue.
  await first.getByTestId("approve-btn").click();
  await expect(page.getByTestId("approval-item")).toHaveCount(1, { timeout: 15_000 });

  // Confirmation toast.
  await expect(page.getByTestId("gl-toast")).toBeVisible();

  // The token and the raw payload (recipient email, action key) are never rendered.
  const bodyText = await page.evaluate(() => document.body.innerText);
  expect(bodyText).not.toContain("Bearer ");
  expect(bodyText).not.toContain("ops@riverside-plumbing.example");
  expect(bodyText).not.toContain("proposed_action");

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("greenlight: editing the draft approves with edits", async ({ page }) => {
  await page.goto("/?view=greenlight");

  const first = page.getByTestId("approval-item").first();
  await expect(first).toBeVisible({ timeout: 15_000 });

  const draft = first.getByTestId("approval-draft");
  await draft.fill("Revised note from the reviewer.");

  // The approve button reflects the edited state.
  await expect(first.getByTestId("approve-btn")).toHaveText("Approve edited");

  await first.getByTestId("approve-btn").click();
  await expect(page.getByTestId("gl-toast")).toContainText("Approved with edits");
  await expect(page.getByTestId("approval-item")).toHaveCount(1, { timeout: 15_000 });
});

// Relies on the real build (chromium-real) with page.route API stubbing.
// Asserts per-item decide errors: a failed decide() for ONE item writes the
// error INSIDE that item (not the page-level card), keeps the item in the
// queue, and leaves other items + the header intact. The retry in the item
// re-calls decide for that item only.

test("greenlight: decide() 500 shows per-item error, queue stays intact", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  // Two pending approvals.
  const APPROVAL_A = {
    id: 1,
    tenant_id: "t1",
    proposed_action: { action: "send_email", body: "Hello there" },
    agent: "nadia",
    reasoning: "Renewal is 11 days out and no contact since June 1.",
    value_at_stake: 22100,
    status: "pending" as const,
  };
  const APPROVAL_B = {
    id: 2,
    tenant_id: "t1",
    proposed_action: { action: "issue_quote", body: "Updated quote" },
    agent: "margo",
    reasoning: "Deal has been in negotiation for 14 days.",
    value_at_stake: 9500,
    status: "pending" as const,
  };

  await page.route("**/approvals", (route) =>
    route.fulfill({ json: { approvals: [APPROVAL_A, APPROVAL_B] } }),
  );

  // The first decide call (on item A, approval id 1) returns 500.
  await page.route("**/approvals/1/decide", (route) =>
    route.fulfill({ status: 500, json: { detail: "Internal Server Error" } }),
  );

  await page.goto("/?view=greenlight");
  const items = page.getByTestId("approval-item");
  await expect(items).toHaveCount(2, { timeout: 15_000 });

  // The header is visible before the error.
  await expect(page.getByTestId("greenlight-queue")).toBeVisible();

  // Click Approve on the FIRST item.
  const firstItem = items.first();
  await firstItem.getByTestId("approve-btn").click();

  // The per-item error renders WITHIN the affected approval-item.
  const itemError = firstItem.getByTestId("item-error");
  await expect(itemError).toBeVisible({ timeout: 15_000 });

  // The page-level load-error card must NOT appear.
  await expect(page.getByTestId("gl-error")).toHaveCount(0);

  // The item is NOT removed from the queue — both items still present.
  await expect(items).toHaveCount(2);

  // The other item (B) is unaffected and still shows its action.
  const secondItem = items.nth(1);
  await expect(secondItem.getByTestId("approval-action")).toBeVisible();

  // The header (h1 + pending count) is still intact.
  await expect(page.locator("h1", { hasText: "Greenlight" })).toBeVisible();

  // The retry button in the item re-calls decide — stub it to succeed now.
  await page.route("**/approvals/1/decide", (route) =>
    route.fulfill({ json: { ok: true } }),
  );
  const retryBtn = firstItem.getByTestId("item-error-retry");
  await expect(retryBtn).toBeVisible();
  await retryBtn.click();

  // After a successful retry, item A is removed; only B remains.
  await expect(items).toHaveCount(1, { timeout: 15_000 });

  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});
