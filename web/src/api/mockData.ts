// Mock-mode fixtures + state machine for the ApiClient — MOCK BUILDS ONLY.
//
// This module is loaded exclusively through the build-time-gated dynamic
// import in client.ts (ApiClient.mockApi). Real-mode bundles (VITE_API_MOCK=0)
// statically fold that gate to false, so rollup never emits this chunk: none
// of the demo tenants, canned deals, fixture numbers or mock account ids below
// can appear in a production bundle. `npm run build:real` + grep proves it.
//
// The fixtures are canned, deterministic and offline so Playwright drives the
// full UI with no network. There is still NO tenant_id sent anywhere and no
// password stored — the mock honors the same wire contract as the real API.

import {
  ApiError,
  type ActionBody,
  type ActionResponse,
  type Approval,
  type ChatResponse,
  type DecideBody,
  type Integration,
  type IntegrationCredentialsBody,
  type IntegrationSyncResponse,
  type ListIntegrationsResponse,
  type SaveViewBody,
  type SavedViewRow,
  type SignupResponse,
  type SignupState,
  type StoreCredentialsResponse,
} from "./client";

const MOCK_TENANT = "tenant-demo";

function seedApprovals(): Approval[] {
  return [
    {
      id: 1,
      tenant_id: MOCK_TENANT,
      proposed_action: {
        action: "send_email",
        to: "ops@riverside-plumbing.example",
        subject: "Your Q3 renewal quote",
        body:
          "Hi Dana, thanks for the call. I have put together your renewal at the agreed " +
          "terms, a 6 percent uplift held flat on support. The signed quote is attached. " +
          "Happy to walk through it whenever works for you.",
      },
      agent: "nadia",
      reasoning:
        "Renewal is 11 days out and the buyer opened the prior quote six times. Sending now " +
        "keeps us ahead of the cycle and matches the discount policy we agreed.",
      value_at_stake: 22100,
      status: "pending",
    },
    {
      id: 2,
      tenant_id: MOCK_TENANT,
      proposed_action: {
        action: "apply_discount",
        deal: "Lantern Bakehouse",
        percent: 8,
        note: "One time onboarding credit to close before month end.",
      },
      agent: "scout",
      reasoning:
        "Deal has stalled two weeks at proposal. An 8 percent onboarding credit is inside the " +
        "approved band and the win probability lifts to 71 percent with it.",
      value_at_stake: 15700,
      status: "pending",
    },
  ];
}

function seedViews(): SavedViewRow[] {
  return [
    {
      tenant_id: MOCK_TENANT,
      view_id: "demo_pipeline",
      version: 1,
      spec_json: {
        view_id: "demo_pipeline",
        title: "Pipeline overview",
        version: 1,
        source_prompt: "Show me total pipeline and value by stage",
        semantic_refs: ["Deals.totalValue", "Deals.count", "Deals.stage"],
        layout: [
          { type: "kpi", title: "Open pipeline", metric: "Deals.totalValue" },
          { type: "kpi", title: "Open deals", metric: "Deals.count" },
          {
            type: "chart",
            title: "Pipeline value by stage",
            encoding: "vega-lite",
            spec: {
              mark: "bar",
              encoding: {
                x: { field: "stage", type: "nominal", title: "Stage" },
                y: { field: "value", type: "quantitative", title: "Value" },
              },
            },
            query: { measures: ["Deals.totalValue"], dimensions: ["Deals.stage"] },
          },
        ],
      },
      semantic_refs: ["Deals.totalValue", "Deals.count", "Deals.stage"],
      source_prompt: "Show me total pipeline and value by stage",
      created_by: "demo",
    },
  ];
}

// Mirrors api/integrations_routes.py KNOWN_INTEGRATIONS. Starts not_connected
// so the mock walks the same connect -> sync arc as the real API (incl. the
// 409 "connect first" on a premature sync).
function seedIntegrations(): Integration[] {
  return [
    {
      name: "hubspot",
      label: "HubSpot",
      category: "CRM & Marketing",
      description:
        "Sync companies, contacts, deals and notes from HubSpot CRM into your " +
        "Uplift data plane (read-only — Uplift never writes back).",
      connected: false,
      status: "not_connected",
    },
  ];
}

function cannedChat(_message: string): ChatResponse {
  return {
    answer:
      "Across the open pipeline, three deals are most likely to close this week: Riverside " +
      "Plumbing, Lantern Bakehouse, and Maple Grove Vet. Riverside is the clear priority today.",
    citations: [
      {
        claim: "Riverside Plumbing is the highest probability deal at 78 percent.",
        source_ref: "deal:riverside-plumbing",
        snippet: "Riverside Plumbing, 22.1k, win probability 78 percent, quote opened 6 times.",
      },
      {
        claim: "Pipeline grew 12 percent this week.",
        source_ref: "report:weekly-pipeline",
        snippet: "Weekly pipeline rollup: total open value up 12 percent to 124.8k.",
      },
    ],
    pending_approvals: [],
    slots: {},
    needs_disambiguation: [],
    delegations: [],
    session_id: "mock-session",
    tenant_id: MOCK_TENANT,
  };
}

// A single in-flight mock signup. The mock walks the state machine forward:
// created -> email_verified -> phone_verified -> paid -> provisioning -> active.
// We store NO password (the form never sends one and the client never logs it);
// we keep only what the API contract carries. There is no tenant_id here, by
// construction: the funnel mints a tenant server-side only after provisioning.
interface MockSignup {
  account_id: string;
  state: SignupState;
  email_verified: boolean;
  phone_verified: boolean;
  // How many GET /signup polls remain before flipping provisioning -> active,
  // so the UI shows a real "provisioning..." step instead of an instant jump.
  provisioningPollsLeft: number;
}

/**
 * The whole mock API surface behind one object: ApiClient lazily instantiates
 * one MockApi per client (state was per-client before the extraction too), so
 * decide/save/signup behave statefully across a test run.
 */
export class MockApi {
  private approvals: Approval[] = seedApprovals();
  private views: SavedViewRow[] = seedViews();
  private signupState: MockSignup | null = null;
  private integrations: Integration[] = seedIntegrations();
  // Names with a "vaulted" credential. The token VALUE is never retained —
  // the mock honors the write-only contract (no echo, no storage, no logging).
  private integrationVault = new Set<string>();

  listApprovals(): Approval[] {
    return this.approvals.filter((a) => a.status === "pending").map((a) => ({ ...a }));
  }

  decideApproval(id: number, body: DecideBody): Approval {
    const rec = this.approvals.find((a) => a.id === id);
    if (!rec || rec.status !== "pending") {
      throw new ApiError(400, `approval ${id} not pending`);
    }
    if (body.decision === "deny") {
      rec.status = "denied";
      rec.deny_message = body.deny_message ?? "";
    } else if (body.decision === "approve" || body.decision === "edit") {
      if (body.decision === "edit" && body.edits) {
        rec.proposed_action = { ...rec.proposed_action, ...body.edits };
      }
      rec.status = "approved";
    } else {
      throw new ApiError(400, `unknown decision ${String(body.decision)}`);
    }
    rec.decided_by = "demo-user";
    return { ...rec };
  }

  listViews(): SavedViewRow[] {
    return this.views.map((v) => ({ ...v }));
  }

  getView(viewId: string): SavedViewRow {
    const v = this.views.find((row) => row.view_id === viewId);
    if (!v) throw new ApiError(404, "no such view");
    return { ...v };
  }

  saveView(body: SaveViewBody): SavedViewRow {
    const spec = body.spec as Record<string, unknown>;
    const viewId = String(spec.view_id ?? "");
    const existing = this.views.filter((r) => r.view_id === viewId);
    const version = existing.length ? Math.max(...existing.map((r) => r.version)) + 1 : 1;
    const row: SavedViewRow = {
      tenant_id: MOCK_TENANT,
      view_id: viewId,
      version,
      spec_json: { ...spec, version },
      semantic_refs: (spec.semantic_refs as string[]) ?? [],
      source_prompt: body.source_prompt ?? "",
      created_by: "demo-user",
    };
    this.views.push(row);
    return { ...row };
  }

  chat(message: string): ChatResponse {
    return cannedChat(message);
  }

  runAction(body: ActionBody): ActionResponse {
    // Side-effecting actions route to Greenlight; non-side-effecting auto-run.
    if (body.side_effecting) {
      return {
        status: "needs_approval",
        decision: "propose",
        detail: "Queued for Greenlight review.",
        approval: null,
        result: null,
      };
    }
    return { status: "executed", decision: "auto", detail: "", approval: null, result: { ok: true } };
  }

  // --- integrations ----------------------------------------------------------

  listIntegrations(): ListIntegrationsResponse {
    return {
      integrations: this.integrations.map((i) => ({ ...i })),
      secrets_configured: true,
      sync_configured: true,
    };
  }

  storeIntegrationCredentials(
    name: string,
    body: IntegrationCredentialsBody,
  ): StoreCredentialsResponse {
    const rec = this.requireIntegration(name);
    if (!body.token || !body.token.trim()) {
      throw new ApiError(422, "token must be non-empty");
    }
    // Vault the FACT of a credential only — never the token itself.
    this.integrationVault.add(rec.name);
    rec.connected = true;
    rec.status = "connected";
    return {
      name: rec.name,
      secret_ref: `uplift/${MOCK_TENANT}/${rec.name}`,
      stored: true,
      status: "connected",
    };
  }

  kickIntegrationSync(name: string): IntegrationSyncResponse {
    const rec = this.requireIntegration(name);
    if (!this.integrationVault.has(rec.name)) {
      // Mirrors the API's guard: no vaulted per-tenant credential = no sync.
      throw new ApiError(
        409,
        `connect ${rec.name} first — no per-tenant credential is vaulted`,
      );
    }
    return {
      name: rec.name,
      result: { pulled: 4, landed_rows: 4, chunks: 9, embedded: 9, skipped: 0 },
    };
  }

  private requireIntegration(name: string): Integration {
    const rec = this.integrations.find((i) => i.name === name);
    if (!rec) throw new ApiError(404, `unknown integration: ${name}`);
    return rec;
  }

  // --- signup funnel ---------------------------------------------------------

  signup(): SignupResponse {
    this.signupState = {
      account_id: "acct_mock_001",
      state: "created",
      email_verified: false,
      phone_verified: false,
      provisioningPollsLeft: 2,
    };
    return { account_id: this.signupState.account_id, state: this.signupState.state };
  }

  verifyEmail(accountId: string): { state: SignupState; email_verified: boolean } {
    const s = this.requireSignup(accountId);
    s.email_verified = true;
    s.state = "email_verified";
    return { state: s.state, email_verified: true };
  }

  verifyPhone(accountId: string): { state: SignupState; phone_verified: boolean } {
    const s = this.requireSignup(accountId);
    s.phone_verified = true;
    s.state = "phone_verified";
    return { state: s.state, phone_verified: true };
  }

  checkout(accountId: string): { checkout_id: string; stripe_customer_id: string } {
    const s = this.requireSignup(accountId);
    // Payment "succeeds" in the mock; provisioning kicks off server-side.
    s.state = "provisioning";
    s.provisioningPollsLeft = 2;
    return { checkout_id: "cs_mock_001", stripe_customer_id: "cus_mock_001" };
  }

  getSignup(accountId: string): { state: SignupState } {
    const s = this.requireSignup(accountId);
    if (s.state === "provisioning") {
      if (s.provisioningPollsLeft > 0) {
        s.provisioningPollsLeft -= 1;
      } else {
        s.state = "active";
      }
    }
    return { state: s.state };
  }

  private requireSignup(accountId: string): MockSignup {
    if (!this.signupState || this.signupState.account_id !== accountId) {
      throw new ApiError(404, "no such signup");
    }
    return this.signupState;
  }
}
