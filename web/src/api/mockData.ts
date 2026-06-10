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
  type AgentCrewResponse,
  type Approval,
  type ChatResponse,
  type CrewAgent,
  type CompanyDetailResponse,
  type CompanyRow,
  type ContactDetailResponse,
  type ContactRow,
  type DealCard,
  type DealDetailResponse,
  type DealStageGroup,
  type DecideBody,
  type DirectoryListParams,
  type Integration,
  type IntegrationCredentialsBody,
  type IntegrationSyncResponse,
  type ListCompaniesResponse,
  type ListContactsResponse,
  type ListDealsResponse,
  type ListIntegrationsResponse,
  type MoveStageBody,
  type MoveStageResponse,
  type SaveViewBody,
  type SavedViewRow,
  type SignupResponse,
  type SignupState,
  type StoreCredentialsResponse,
  type WorkflowsResponse,
} from "./client";

const MOCK_TENANT = "tenant-demo";

// The demo tenant's crew — mirrors the OWNED roster definitions (agents/roster.py +
// agents/coordinator.py) and the trusted registry's per-tool policies, so the mock
// shows the exact crew provisioning assembles. Id tails are fixture values shaped
// like the real truncation (last 6 chars; the real API never sends full ids either).
function seedCrewRoster(): CrewAgent[] {
  return [
    {
      name: "scout",
      role: "Lead research",
      description:
        "You are the lead-research specialist. Enrich and score leads using the tenant's " +
        "corpus and metrics; score conversion propensity with run_model and surface findings " +
        "as a saved view with build_view.",
      is_coordinator: false,
      tools: [
        { name: "search_rag", policy: "auto" },
        { name: "query_cube", policy: "auto" },
        { name: "read_crm", policy: "auto" },
        { name: "run_model", policy: "auto" },
        { name: "build_view", policy: "auto" },
      ],
    },
    {
      name: "nadia",
      role: "Outreach drafting",
      description:
        "You draft outreach. Personalize from the tenant's data; never send — drafts route " +
        "to a human.",
      is_coordinator: false,
      tools: [
        { name: "search_rag", policy: "auto" },
        { name: "read_crm", policy: "auto" },
        { name: "draft_email", policy: "auto" },
      ],
    },
    {
      name: "margo",
      role: "Quoting",
      description: "You handle quoting. Propose quotes grounded in deal data; issuing requires approval.",
      is_coordinator: false,
      tools: [
        { name: "read_crm", policy: "auto" },
        { name: "query_cube", policy: "auto" },
        { name: "issue_quote", policy: "always_ask" },
      ],
    },
    {
      name: "ledger",
      role: "CRM ops",
      description: "You handle ops and CRM mutations. All mutations route through Greenlight.",
      is_coordinator: false,
      tools: [
        { name: "read_crm", policy: "auto" },
        { name: "update_deal", policy: "always_ask" },
      ],
    },
    {
      name: "echo",
      role: "Follow-ups",
      description: "You handle follow-ups. Draft timely nudges; sends require approval.",
      is_coordinator: false,
      tools: [
        { name: "read_crm", policy: "auto" },
        { name: "draft_email", policy: "auto" },
      ],
    },
    {
      name: "pip",
      role: "Support",
      description: "You handle support questions grounded in the tenant's knowledge.",
      is_coordinator: false,
      tools: [
        { name: "search_rag", policy: "auto" },
        { name: "read_crm", policy: "auto" },
      ],
    },
    {
      name: "critic",
      role: "Review & risk",
      description:
        "You review the team's proposed actions and answers for correctness and risk before " +
        "they go out.",
      is_coordinator: false,
      tools: [],
    },
  ];
}

function seedAgentCrew(): AgentCrewResponse {
  const roster = seedCrewRoster();
  return {
    provisioned: true,
    environment_id_tail: "e6TBgZ",
    coordinator: {
      name: "uplift-orchestrator",
      role: "Coordinator",
      description:
        "You coordinate the Uplift team. Delegate research to scout, outreach drafting to " +
        "nadia, quoting to margo, follow-ups to echo, support to pip, ops to ledger, and " +
        "always run the critic before responding.",
      is_coordinator: true,
      tools: [],
      id_tail: "kQ9mXa",
    },
    roster,
    count: roster.length,
  };
}

// The demo tenant's workflows view — mirrors api/workflows_routes.py exactly: the OWNED
// provisioning funnel (5 static steps; the diagram is owned semantics, never a live AWS
// Describe) plus a few canned executions shaped like the real feed (name + status +
// timestamps ONLY — the real API strips ARNs server-side and the mock holds none either).
function seedWorkflows(): WorkflowsResponse {
  return {
    machine: { name: "uplift-provisioning", kind: "provisioning" },
    steps: [
      {
        id: "signup",
        label: "Sign up",
        description:
          "An account is created with an email and phone. Nothing is provisioned yet — " +
          "no workspace, no agents, no charge.",
      },
      {
        id: "verify",
        label: "Verify",
        description:
          "Email and phone are both confirmed before payment unlocks (verify-before-pay). " +
          "Verification links and codes are single-use and expire.",
      },
      {
        id: "pay",
        label: "Pay",
        description:
          "Checkout completes and ONLY the cryptographically signed Stripe webhook flips " +
          "the account to paid — never the browser redirect. A re-delivered webhook is a " +
          "no-op: provisioning starts exactly once.",
      },
      {
        id: "provision",
        label: "Provision",
        description:
          "The state machine builds the workspace step by step: tenant record, a dedicated " +
          "Anthropic workspace, the eight-agent crew, identity, and defaults. Every step is " +
          "idempotent (check-then-create) and a mid-failure parks the account for retry — " +
          "never a half-built tenant. Outbound email stays draft-gated until sends are " +
          "deliberately enabled.",
      },
      {
        id: "activate",
        label: "Activate",
        description:
          "The terminal flip: the workspace goes live and the crew starts working. From " +
          "here, anything an agent does that touches the outside world routes through " +
          "Greenlight for human sign-off — autonomy never outruns your approval.",
      },
    ],
    step_count: 5,
    executions_available: true,
    reason: null,
    recent_executions: [
      {
        name: "provision-demo-aurora-co",
        status: "SUCCEEDED",
        started_at: "2026-06-09T12:00:00+00:00",
        stopped_at: "2026-06-09T12:00:42+00:00",
      },
      {
        name: "provision-demo-lantern",
        status: "RUNNING",
        started_at: "2026-06-10T09:30:00+00:00",
        stopped_at: null,
      },
      {
        name: "provision-demo-riverside",
        status: "FAILED",
        started_at: "2026-06-08T08:00:00+00:00",
        stopped_at: "2026-06-08T08:01:07+00:00",
      },
    ],
  };
}

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

// Mirrors api/deals_routes.py STAGE_ORDER/STAGE_LABELS — the canonical column
// spine the mock board groups into, same shape as the real GET /deals.
const STAGE_ORDER = ["new", "qualified", "proposal", "negotiation", "closed_won", "closed_lost"];
const STAGE_LABELS: Record<string, string> = {
  new: "New",
  qualified: "Qualified",
  proposal: "Proposal",
  negotiation: "Negotiation",
  closed_won: "Closed won",
  closed_lost: "Closed lost",
};

function seedDeals(): DealCard[] {
  return [
    {
      id: "d0000000-0000-0000-0000-000000000001",
      title: "Birchwood platform expansion",
      stage: "negotiation",
      amount: 84000,
      currency: "USD",
      company_id: "c-1",
      contact_id: "p-1",
      company_name: "Birchwood Capital",
      created_at: "2026-06-01T00:00:00+00:00",
    },
    {
      id: "d0000000-0000-0000-0000-000000000002",
      title: "Halcyon fleet rollout",
      stage: "qualified",
      amount: 132000,
      currency: "USD",
      company_id: "c-2",
      contact_id: "p-2",
      company_name: "Halcyon Logistics",
      created_at: "2026-06-02T00:00:00+00:00",
    },
    {
      id: "d0000000-0000-0000-0000-000000000003",
      title: "Mesa Verde pilot",
      stage: "new",
      amount: 9500,
      currency: "USD",
      company_id: "c-3",
      contact_id: "p-3",
      company_name: "Mesa Verde Health",
      created_at: "2026-06-03T00:00:00+00:00",
    },
  ];
}

// Mirrors api/contacts_routes.py shapes. company_ids line up with seedDeals so
// the contact drawer's "open deals" seam shows the same canned pipeline.
function seedContacts(): ContactRow[] {
  return [
    {
      id: "c0000000-0000-0000-0000-000000000001",
      name: "Dana Whitfield",
      title: null,
      email: "dana@birchwoodcap.example",
      phone: "+1 512 555 0150",
      company_id: "c-1",
      company_name: "Birchwood Capital",
      created_at: "2026-05-20T00:00:00+00:00",
      last_activity_at: "2026-06-05T00:00:00+00:00",
    },
    {
      id: "c0000000-0000-0000-0000-000000000002",
      name: "Priya Raman",
      title: null,
      email: "priya@halcyonlogistics.example",
      phone: "+1 737 555 0188",
      company_id: "c-2",
      company_name: "Halcyon Logistics",
      created_at: "2026-05-22T00:00:00+00:00",
      last_activity_at: "2026-06-03T00:00:00+00:00",
    },
    {
      id: "c0000000-0000-0000-0000-000000000003",
      name: "Marcus Oyelaran",
      title: null,
      email: "marcus@mesaverde.example",
      phone: null,
      company_id: "c-3",
      company_name: "Mesa Verde Health",
      created_at: "2026-05-25T00:00:00+00:00",
      last_activity_at: null,
    },
  ];
}

function seedCompanies(): CompanyRow[] {
  return [
    {
      id: "c-1",
      name: "Birchwood Capital",
      domain: "birchwoodcap.example",
      created_at: "2026-05-01T00:00:00+00:00",
      contact_count: 1,
      open_deal_count: 1,
    },
    {
      id: "c-2",
      name: "Halcyon Logistics",
      domain: "halcyonlogistics.example",
      created_at: "2026-05-02T00:00:00+00:00",
      contact_count: 1,
      open_deal_count: 1,
    },
    {
      id: "c-3",
      name: "Mesa Verde Health",
      domain: "mesaverde.example",
      created_at: "2026-05-03T00:00:00+00:00",
      contact_count: 1,
      open_deal_count: 1,
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
  private deals: DealCard[] = seedDeals();
  private contacts: ContactRow[] = seedContacts();
  private companies: CompanyRow[] = seedCompanies();
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

  getWorkflows(): WorkflowsResponse {
    const w = seedWorkflows();
    return {
      ...w,
      steps: w.steps.map((s) => ({ ...s })),
      recent_executions: w.recent_executions.map((e) => ({ ...e })),
    };
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

  // --- deals / pipeline --------------------------------------------------------

  listDeals(): ListDealsResponse {
    const byStage = new Map<string, DealCard[]>(STAGE_ORDER.map((s) => [s, []]));
    for (const d of this.deals) {
      const list = byStage.get(d.stage) ?? [];
      byStage.set(d.stage, list);
      list.push({ ...d });
    }
    const order = [...STAGE_ORDER, ...[...byStage.keys()].filter((s) => !STAGE_ORDER.includes(s)).sort()];
    const stages: DealStageGroup[] = order.map((stage) => {
      const deals = byStage.get(stage) ?? [];
      return {
        stage,
        label: STAGE_LABELS[stage] ?? stage,
        deals,
        count: deals.length,
        total_amount: deals.reduce((sum, d) => sum + (d.amount ?? 0), 0),
      };
    });
    return { stages, total: this.deals.length, stage_order: [...STAGE_ORDER] };
  }

  getDeal(dealId: string): DealDetailResponse {
    const d = this.deals.find((row) => row.id === dealId);
    if (!d) throw new ApiError(404, "no such deal");
    return {
      deal: { ...d, contact_name: "Dana Whitfield", contact_email: "dana@example.com" },
      activities: [
        {
          id: "act-1",
          kind: "call",
          body: "Walked through the security review; they want the RLS docs.",
          occurred_at: "2026-06-05T00:00:00+00:00",
        },
        {
          id: "act-2",
          kind: "email",
          body: "Sent the revised order form (net-45 -> net-30).",
          occurred_at: "2026-06-04T00:00:00+00:00",
        },
      ],
    };
  }

  moveDealStage(dealId: string, body: MoveStageBody): MoveStageResponse {
    const d = this.deals.find((row) => row.id === dealId);
    if (!d) throw new ApiError(404, "no such deal");
    const to = (body.to_stage ?? "").trim();
    if (!to) throw new ApiError(422, "to_stage must be non-empty");
    if (to === d.stage) throw new ApiError(409, `deal is already in stage '${d.stage}'`);
    // Mirrors the real API: the deal is NOT moved — a Greenlight proposal is
    // queued and the stage stays put until a human approves it there.
    const approvalId = Math.max(0, ...this.approvals.map((a) => a.id)) + 1;
    this.approvals.push({
      id: approvalId,
      tenant_id: MOCK_TENANT,
      proposed_action: {
        action: "update_deal",
        deal_id: d.id,
        changes: { stage: to },
        from_stage: d.stage,
      },
      agent: "demo-user",
      reasoning: `Move deal '${d.title}' from stage '${d.stage}' to '${to}' (requested on the pipeline board).`,
      value_at_stake: d.amount,
      status: "pending",
    });
    return {
      queued: true,
      approval_id: approvalId,
      status: "pending_approval",
      from_stage: d.stage,
      to_stage: to,
      detail: `queued for approval in Greenlight — the deal stays in '${d.stage}' until a human approves`,
    };
  }

  // --- contacts / companies directory ------------------------------------------

  listContacts(params: DirectoryListParams = {}): ListContactsResponse {
    const q = (params.q ?? "").trim().toLowerCase();
    const limit = Math.max(1, Math.min(params.limit ?? 50, 200));
    const offset = Math.max(0, params.offset ?? 0);
    const filtered = this.contacts.filter(
      (c) =>
        !q ||
        (c.name ?? "").toLowerCase().includes(q) ||
        (c.email ?? "").toLowerCase().includes(q),
    );
    const page = filtered.slice(offset, offset + limit).map((c) => ({ ...c }));
    return {
      contacts: page,
      count: page.length,
      has_more: offset + limit < filtered.length,
      limit,
      offset,
      q: q || null,
    };
  }

  getContact(contactId: string): ContactDetailResponse {
    const c = this.contacts.find((row) => row.id === contactId);
    if (!c) throw new ApiError(404, "no such contact");
    const open = this.deals.filter(
      (d) => d.company_id === c.company_id && d.stage !== "closed_won" && d.stage !== "closed_lost",
    );
    return {
      contact: { ...c },
      activities: c.last_activity_at
        ? [
            {
              id: "act-1",
              kind: "call",
              body: "Walked through the security review; they want the RLS docs.",
              occurred_at: c.last_activity_at,
            },
            {
              id: "act-2",
              kind: "email",
              body: "Sent the revised order form (net-45 -> net-30).",
              occurred_at: "2026-06-04T00:00:00+00:00",
            },
          ]
        : [],
      company_deals: open.map((d) => ({
        id: d.id,
        title: d.title,
        stage: d.stage,
        amount: d.amount,
        currency: d.currency,
        company_id: d.company_id,
        contact_id: d.contact_id,
        created_at: d.created_at,
      })),
    };
  }

  listCompanies(params: DirectoryListParams = {}): ListCompaniesResponse {
    const q = (params.q ?? "").trim().toLowerCase();
    const limit = Math.max(1, Math.min(params.limit ?? 50, 200));
    const offset = Math.max(0, params.offset ?? 0);
    const filtered = this.companies.filter(
      (c) =>
        !q ||
        (c.name ?? "").toLowerCase().includes(q) ||
        (c.domain ?? "").toLowerCase().includes(q),
    );
    const page = filtered.slice(offset, offset + limit).map((c) => ({ ...c }));
    return {
      companies: page,
      count: page.length,
      has_more: offset + limit < filtered.length,
      limit,
      offset,
      q: q || null,
    };
  }

  getCompany(companyId: string): CompanyDetailResponse {
    const c = this.companies.find((row) => row.id === companyId);
    if (!c) throw new ApiError(404, "no such company");
    const open = this.deals.filter(
      (d) => d.company_id === c.id && d.stage !== "closed_won" && d.stage !== "closed_lost",
    );
    return {
      company: { ...c },
      contacts: this.contacts.filter((p) => p.company_id === c.id).map((p) => ({ ...p })),
      deals: open.map((d) => ({
        id: d.id,
        title: d.title,
        stage: d.stage,
        amount: d.amount,
        currency: d.currency,
        company_id: d.company_id,
        contact_id: d.contact_id,
        created_at: d.created_at,
      })),
    };
  }

  // --- agent crew --------------------------------------------------------------

  getAgentCrew(): AgentCrewResponse {
    const crew = seedAgentCrew();
    return {
      ...crew,
      coordinator: { ...crew.coordinator, tools: [...crew.coordinator.tools] },
      roster: crew.roster.map((a) => ({ ...a, tools: a.tools.map((t) => ({ ...t })) })),
    };
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
