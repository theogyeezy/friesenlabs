// Component/integration test (jsdom) for the Greenlight approval queue — the human-in-the-loop gate.
// Asserts the reviewer sees reasoning + recipient before approving, that approving a send_email
// proposal reports it as a DRAFT (never "sent" — the draft-only honesty signal), and that the raw
// payload/token never render. Mirrors e2e/greenlight.spec.ts at the faster component layer.
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GreenlightQueue } from "../GreenlightQueue";
import type { ApiClient, Approval } from "../client";

const PENDING: Approval = {
  id: 1,
  tenant_id: "t-1",
  proposed_action: {
    action: "send_email",
    to: "ops@riverside-plumbing.example",
    subject: "Your Q3 renewal quote",
    body: "Hi — here's the renewal. Reply unsubscribe to opt out.",
  },
  agent: "nadia",
  reasoning: "Renewal is 11 days out; the customer asked for the quote.",
  value_at_stake: 22100,
  status: "pending",
} as Approval;

// A send_email approval applies as record_only (performed:false) — the draft-only contract the
// backend guarantees and the toast keys off. Modelling it here keeps the test faithful.
const APPROVED_RECORD_ONLY = {
  ...PENDING,
  status: "approved",
  apply_result: { performed: false, reason: "draft-only until provider go-live" },
} as Approval;

function fakeClient(overrides: Partial<ApiClient> = {}): ApiClient {
  return {
    isMock: () => false,
    listApprovals: vi.fn(async () => ({ approvals: [PENDING], cursor: null, total_pending: 1 })),
    decideApproval: vi.fn(async () => APPROVED_RECORD_ONLY),
    ...overrides,
  } as unknown as ApiClient;
}

describe("GreenlightQueue", () => {
  it("shows the agent's reasoning + recipient so a reviewer never approves blind", async () => {
    render(<GreenlightQueue client={fakeClient()} />);
    const item = await screen.findByTestId("approval-item");
    expect(within(item).getByTestId("approval-reasoning")).toHaveTextContent("Renewal is 11 days out");
    expect(within(item).getByTestId("approval-details")).toHaveTextContent(
      "ops@riverside-plumbing.example",
    );
  });

  it("approving a send_email proposal reports it as a DRAFT, never 'sent'", async () => {
    const decide = vi.fn(async () => APPROVED_RECORD_ONLY);
    render(<GreenlightQueue client={fakeClient({ decideApproval: decide })} />);
    const item = await screen.findByTestId("approval-item");

    await userEvent.setup().click(within(item).getByTestId("approve-btn"));

    await waitFor(() => expect(decide).toHaveBeenCalledWith(1, expect.objectContaining({ decision: "approve" })));
    // The honest toast: a record-only action is a DRAFT, never claimed as sent.
    const toast = await screen.findByTestId("gl-toast");
    expect(toast).toHaveTextContent(/draft/i);
    expect(toast).not.toHaveTextContent(/\bsent\b/i);
  });

  it("never renders the raw payload key or a bearer token", async () => {
    render(<GreenlightQueue client={fakeClient()} />);
    await screen.findByTestId("approval-item");
    expect(document.body.textContent).not.toContain("proposed_action");
    expect(document.body.textContent).not.toContain("Bearer ");
  });
});
