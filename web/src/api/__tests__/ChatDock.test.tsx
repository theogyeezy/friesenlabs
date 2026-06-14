// Component/integration test (jsdom) for the ChatDock Greenlight affordance — the layer that was
// missing. Renders the real component, fires real events through @testing-library, asserts on the
// resulting DOM. No browser, fully deterministic. (Real-pixel rendering is covered by the e2e +
// visual layers; see TESTING.md.)
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatDock, routedApprovalCount } from "../ChatDock";
import type { ApiClient, ChatResponse } from "../client";

/** A minimal fake ApiClient. isMock()=true short-circuits the conversation-history machinery so the
 * test exercises just chat() + the affordance. `chat` returns whatever the test wires. */
function fakeClient(chatResponse: ChatResponse): ApiClient {
  return {
    isMock: () => true,
    chat: vi.fn(async () => chatResponse),
    continueChat: vi.fn(async () => ({ answer: "", citations: [], settled: true }) as ChatResponse),
  } as unknown as ApiClient;
}

const STAGED: ChatResponse = {
  answer: "I've drafted that email and queued it for your approval.",
  citations: [],
  settled: true,
  pending_approvals: [
    { status: "pending_approval", tool_name: "draft_email", custom_tool_use_id: "ctu-1" },
  ],
} as ChatResponse;

const READ_ONLY: ChatResponse = {
  answer: "Your pipeline has 45 open deals.",
  citations: [],
  settled: true,
  pending_approvals: [],
} as ChatResponse;

async function send(message: string) {
  const user = userEvent.setup();
  await user.type(screen.getByTestId("chat-input"), message);
  await user.click(screen.getByTestId("chat-send"));
}

describe("ChatDock — Greenlight approval affordance", () => {
  it("surfaces a 'Review in Greenlight' affordance when a turn stages an approval", async () => {
    render(<ChatDock client={fakeClient(STAGED)} />);
    await send("draft a follow-up email to Vada");

    const prompt = await screen.findByTestId("chat-approval-prompt");
    expect(prompt).toBeVisible();
    expect(prompt).toHaveTextContent("1 action is waiting for your approval");
    expect(screen.getByTestId("chat-review-greenlight")).toBeInTheDocument();
  });

  it("calls onOpenGreenlight when the affordance is clicked", async () => {
    const onOpenGreenlight = vi.fn();
    render(<ChatDock client={fakeClient(STAGED)} onOpenGreenlight={onOpenGreenlight} />);
    await send("draft a follow-up email to Vada");

    const review = await screen.findByTestId("chat-review-greenlight");
    await userEvent.setup().click(review);
    expect(onOpenGreenlight).toHaveBeenCalledTimes(1);
  });

  it("shows NO affordance for a read-only answer", async () => {
    render(<ChatDock client={fakeClient(READ_ONLY)} />);
    await send("how is my pipeline looking?");

    // The answer renders…
    await waitFor(() =>
      expect(screen.getByText(/45 open deals/)).toBeInTheDocument(),
    );
    // …and there is nothing to approve.
    expect(screen.queryByTestId("chat-approval-prompt")).not.toBeInTheDocument();
  });
});

describe("routedApprovalCount — counts only real staged items", () => {
  it("counts entries carrying a tool_name", () => {
    const seen = new Set<string>();
    expect(
      routedApprovalCount(
        [{ tool_name: "draft_email", custom_tool_use_id: "a" }], seen),
    ).toBe(1);
  });

  it("ignores async-settle markers (no tool_name)", () => {
    const seen = new Set<string>();
    expect(
      routedApprovalCount(
        [{ status: "pending", reason: "requires_action" }, { status: "pending", reason: "settle_budget" }],
        seen),
    ).toBe(0);
  });

  it("dedupes the same staged call across settle legs by custom_tool_use_id", () => {
    const seen = new Set<string>();
    const leg = [{ tool_name: "draft_email", custom_tool_use_id: "x" }];
    expect(routedApprovalCount(leg, seen)).toBe(1);
    expect(routedApprovalCount(leg, seen)).toBe(0); // already counted — not double-counted
  });

  it("is null-safe for a missing pending_approvals array", () => {
    expect(routedApprovalCount(undefined, new Set())).toBe(0);
  });
});
