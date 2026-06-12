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
