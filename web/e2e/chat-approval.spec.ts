import { test, expect } from "@playwright/test";

// Chat -> Greenlight affordance (mock build, fully offline). The live matrix gap (2026-06-12) was
// that a "draft an email" ask staged NOTHING the user could act on. Now the agent stages a
// send_email approval and the chat surfaces a "Review in Greenlight" affordance that takes the
// user straight to the queue — so a queued email is never silently stranded.

test("a draft-email ask surfaces the approval and links to the Greenlight queue", async ({ page }) => {
  await page.goto("/?view=chat");
  await expect(page.getByTestId("chat-dock")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("chat-input").fill("Draft a follow-up email to Vada about the renewal");
  await page.getByTestId("chat-send").click();

  // The agent confirms it queued the email for approval...
  await expect(page.getByText(/queued it for your approval/i)).toBeVisible({ timeout: 15_000 });

  // ...and a "Review in Greenlight" affordance appears (1 action waiting).
  const prompt = page.getByTestId("chat-approval-prompt");
  await expect(prompt).toBeVisible({ timeout: 15_000 });
  await expect(prompt).toContainText("1 action is waiting for your approval");

  const review = page.getByTestId("chat-review-greenlight");
  await expect(review).toBeVisible();
  await review.click();

  // The standalone chat mount falls back to the /?view=greenlight deep link → the real queue.
  await expect(page.getByTestId("greenlight-queue")).toBeVisible({ timeout: 15_000 });
});

test("a non-email ask shows no approval affordance", async ({ page }) => {
  await page.goto("/?view=chat");
  await page.getByTestId("chat-input").fill("How is my pipeline looking?");
  await page.getByTestId("chat-send").click();

  // A read-only answer renders, and there is nothing to approve.
  await expect(page.getByTestId("chat-msg-agent").last()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("chat-approval-prompt")).toHaveCount(0);
});
