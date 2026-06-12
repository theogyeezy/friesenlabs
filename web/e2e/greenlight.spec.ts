import { test, expect } from "@playwright/test";

// Phase 9b Greenlight e2e (mock mode, fully offline). The approval queue is wired
// to the control-plane client; the client defaults to mock mode so no server is
// needed. Asserts:
//   1. a pending item shows its reasoning + value at stake + an editable draft,
//   2. the structured "What this will do" panel shows recipient/subject — a
//      reviewer never approves blind (Greenlight audit P0; the raw JSON payload
//      and the bearer token still never render),
//   3. approving removes the item; the toast is HONEST about draft-only actions,
//   4. denying carries an optional reason and removes the item.

test("greenlight queue shows reasoning + value + what-it-will-do, approve removes the item", async ({ page }) => {
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

  // The structured details panel shows WHO this email goes to before approval.
  const details = first.getByTestId("approval-details");
  await expect(details).toContainText("ops@riverside-plumbing.example");
  await expect(details).toContainText("Your Q3 renewal quote");

  // Editable draft is present and editable.
  const draft = first.getByTestId("approval-draft");
  await expect(draft).toBeVisible();
  await expect(draft).toBeEditable();

  // Approving the first item (send_email — record-only) removes it from the queue
  // and the toast says DRAFT, never "sent".
  await first.getByTestId("approve-btn").click();
  await expect(page.getByTestId("approval-item")).toHaveCount(1, { timeout: 15_000 });
  await expect(page.getByTestId("gl-toast")).toContainText("recorded as a draft");

  // The token and the raw payload structure are never rendered.
  const bodyText = await page.evaluate(() => document.body.innerText);
  expect(bodyText).not.toContain("Bearer ");
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

test("greenlight: deny with an optional reason declines the item", async ({ page }) => {
  await page.goto("/?view=greenlight");

  const first = page.getByTestId("approval-item").first();
  await expect(first).toBeVisible({ timeout: 15_000 });

  await first.getByTestId("deny-reason").fill("Wrong recipient — use the billing contact.");
  await first.getByTestId("deny-btn").click();

  await expect(page.getByTestId("gl-toast")).toContainText("Declined");
  await expect(page.getByTestId("approval-item")).toHaveCount(1, { timeout: 15_000 });
});
