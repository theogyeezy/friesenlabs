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
  type DeleteCredentialsResponse,
  type Integration,
  type IntegrationCredentialsBody,
  type IntegrationSyncHistoryResponse,
  type IntegrationSyncResponse,
  type ListCompaniesResponse,
  type ListContactsResponse,
  type ListDealsResponse,
  type ListIntegrationsResponse,
  type MoveStageBody,
  type MoveStageResponse,
  type SaveViewBody,
  type RefineViewBody,
  type SavedViewRow,
  type SynthesizeViewBody,
  type SynthesizeViewResponse,
  type KnowledgeInventoryResponse,
  type KnowledgeSearchResponse,
  type CheckoutResponse,
  type SignupResponse,
  type SignupState,
  type StoreCredentialsResponse,
  type WorkflowsResponse,
  type AutonomyLevel,
  type AutonomyState,
  type DecisionTrace,
  type KillswitchState,
  type OnboardingState,
  type OnboardingPutBody,
  type OnboardingStepId,
  type LoadSampleResponse,
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
    {
      tenant_id: MOCK_TENANT,
      view_id: "won_deals",
      version: 1,
      spec_json: {
        view_id: "won_deals",
        title: "Won deals",
        version: 1,
        source_prompt: "How many deals have we won?",
        semantic_refs: ["Deals.count"],
        layout: [{ type: "kpi", title: "Deals won", metric: "Deals.count" }],
      },
      semantic_refs: ["Deals.count"],
      source_prompt: "How many deals have we won?",
      created_by: "demo",
    },
  ];
}

// Mirrors api/integrations_routes.py KNOWN_INTEGRATIONS. Starts not_connected
// so the mock walks the same connect -> sync arc as the real API (incl. the
// 409 "connect first" on a premature sync). CSV is a file-kind connector with
// no vault slot — it always shows "available" in the mock (importer wired).
function seedIntegrations(): Integration[] {
  return [
    {
      name: "hubspot",
      label: "HubSpot",
      category: "CRM & Marketing",
      description:
        "Sync companies, contacts, deals and notes from HubSpot CRM into your " +
        "Uplift data plane (read-only — Uplift never writes back).",
      kind: "sync" as const,
      connected: false,
      status: "not_connected",
      experimental: false,
    },
    {
      name: "csv",
      label: "CSV Import",
      category: "Files & Imports",
      description:
        "Import contacts, companies or deals from a CSV export (up to 5MB). " +
        "Column mapping is auto-detected and can be overridden per upload.",
      kind: "file" as const,
      connected: null,
      status: "available" as const,
      experimental: false,
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

// ---------------------------------------------------------------------------
// Balto (NL view creation) — mirrors conv/views.py deterministically.
// ---------------------------------------------------------------------------

// The EXACT status line conv.views.BALTO_STATUS emits — the chat shows it verbatim.
const BALTO_STATUS = "Our synthesizing agent Balto is mushing away to get this view for you.";
// The honest copy when no Cube member can answer the ask (conv.views.DATA_NOT_ON_PLATFORM).
const DATA_NOT_ON_PLATFORM =
  "Your request cannot be fulfilled because the data does not exist on the platform.";

// Same word-bounded intent shape as conv/views.py (so e.g. "review" never matches "view").
const VIEW_INTENT_RE =
  /\b(graphs?|charts?|plots?|dashboards?|visuali[sz]ations?|visuali[sz]e[ds]?|views?)\b/i;

// Tokens of the demo tenant's member catalog (Deals.count / Deals.totalValue / Deals.stage /
// Deals.createdAt / Contacts.count) — the data-not-found gate checks the ask against these.
const MOCK_MEMBER_TOKENS = new Set([
  "deal", "deals", "stage", "stages", "pipeline", "value", "total",
  "contact", "contacts", "count", "created",
]);

function requestCoversMockData(request: string): boolean {
  const words = (request.toLowerCase().match(/[a-z0-9]+/g) ?? []).filter(
    (w) => !VIEW_INTENT_RE.test(w),
  );
  return words.some((w) => MOCK_MEMBER_TOKENS.has(w) || MOCK_MEMBER_TOKENS.has(w.replace(/s$/, "")));
}

function baltoSpec(request: string): Record<string, unknown> {
  // Deterministic, schema-valid spec over the demo catalog (chart + KPI on Deals).
  return {
    view_id: "balto_deals_by_stage",
    title: "Deals by stage",
    source_prompt: request,
    semantic_refs: ["Deals.count", "Deals.stage"],
    layout: [
      { type: "kpi", title: "Open deals", metric: "Deals.count" },
      {
        type: "chart",
        title: "Deals by stage",
        encoding: "vega-lite",
        spec: {
          mark: "bar",
          encoding: {
            x: { field: "stage", type: "nominal", title: "Stage" },
            y: { field: "value", type: "quantitative", title: "Deals" },
          },
        },
        query: { measures: ["Deals.count"], dimensions: ["Deals.stage"] },
      },
    ],
  };
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
  // Control-plane state — stateful within a run so toggles round-trip.
  private killswitch: KillswitchState = { engaged: false, scope: "global" };
  private autonomy: AutonomyState = { level: 1 };
  // First-run state — starts as a brand-new tenant (nothing done) so the mock/e2e
  // flow exercises the empty-state -> load-sample -> populated path and the
  // first-run checklist dismiss/persist. load_sample flips the contacts seed on.
  private onboarding: OnboardingState = {
    tenant_id: "demo-tenant",
    steps: { load_data: false, try_chat: false, invite_team: false },
    dismissed: false,
    sample_loaded: false,
  };
  // Balto drafts — ephemeral, save-or-discard (mirrors conv.views.ViewSynthesizer drafts).
  private viewDrafts = new Map<string, { spec: Record<string, unknown>; request: string }>();
  private draftSeq = 0;

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
    // Mirrors GET /views: renderable view specs only — kind=dashboard rows
    // live on listDashboards.
    return this.views
      .filter((v) => (v.spec_json as Record<string, unknown>).kind !== "dashboard")
      .map((v) => ({ ...v }));
  }

  getView(viewId: string): SavedViewRow {
    const v = this.views.find((row) => row.view_id === viewId);
    if (!v) throw new ApiError(404, "no such view");
    return { ...v };
  }

  // --- dashboards (kind=dashboard saved views) — mirror api/app.py /dashboards ---

  listDashboards(): SavedViewRow[] {
    return this.views
      .filter((v) => (v.spec_json as Record<string, unknown>).kind === "dashboard")
      .map((v) => ({ ...v }));
  }

  getDashboard(viewId: string): { dashboard: SavedViewRow; views: Record<string, SavedViewRow> } {
    const dash = this.views.find(
      (row) =>
        row.view_id === viewId &&
        (row.spec_json as Record<string, unknown>).kind === "dashboard",
    );
    if (!dash) throw new ApiError(404, "no such dashboard");
    const items = ((dash.spec_json as Record<string, unknown>).items ?? []) as Array<{
      view_id: string;
    }>;
    const views: Record<string, SavedViewRow> = {};
    for (const item of items) {
      const ref = this.views
        .filter(
          (r) =>
            r.view_id === item.view_id &&
            (r.spec_json as Record<string, unknown>).kind !== "dashboard",
        )
        .reduce<SavedViewRow | null>((a, b) => (a === null || b.version > a.version ? b : a), null);
      if (ref) views[item.view_id] = { ...ref };
    }
    return { dashboard: { ...dash }, views };
  }

  saveDashboard(body: SaveViewBody): SavedViewRow {
    const spec = body.spec as Record<string, unknown>;
    if (spec.kind !== "dashboard") throw new ApiError(422, 'spec.kind must be "dashboard"');
    return this.saveView(body);
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

  // Mock NL refine ("ask for a chart"): stands in for the agent's view_patcher.
  // Deterministic so demos/tests are stable — recognizes a couple of common
  // chart asks (line vs bar) and otherwise just re-versions the spec with the
  // instruction recorded as the new source_prompt. The real route runs the
  // agent; this never claims to.
  refineView(viewId: string, body: RefineViewBody): SavedViewRow {
    const existing = this.views.filter((r) => r.view_id === viewId);
    if (!existing.length) throw new ApiError(404, "no such view");
    const latest = existing.reduce((a, b) => (b.version > a.version ? b : a));
    const spec = JSON.parse(JSON.stringify(latest.spec_json)) as Record<string, unknown>;
    const want = body.instruction.toLowerCase();
    const mark = want.includes("line") ? "line" : want.includes("bar") ? "bar" : null;
    if (mark && Array.isArray(spec.layout)) {
      for (const block of spec.layout as Array<Record<string, unknown>>) {
        if (block.type === "chart" && block.spec && typeof block.spec === "object") {
          (block.spec as Record<string, unknown>).mark = mark;
        }
      }
    }
    spec.source_prompt = body.instruction;
    return this.saveView({ spec, source_prompt: body.instruction });
  }

  chat(message: string): ChatResponse {
    // Balto: a view-shaped ask answers the EXACT status line and flags the turn so the
    // client drives synthesizeView — mirrors conv.session.Conversation.send.
    if (VIEW_INTENT_RE.test(message)) {
      return {
        answer: BALTO_STATUS,
        citations: [],
        pending_approvals: [],
        slots: {},
        needs_disambiguation: [],
        delegations: [],
        session_id: "mock-session",
        tenant_id: MOCK_TENANT,
        view_intent: true,
        view_request: message,
      };
    }
    return cannedChat(message);
  }

  // Balto view synthesis — deterministic mirror of POST /views/synthesize.
  synthesizeView(body: SynthesizeViewBody): SynthesizeViewResponse {
    const request = (body.request ?? "").trim();
    if (!request) return { status: "invalid", error: "empty view request" };
    // (1) An existing saved view that already covers the ask (every content word present).
    const words = (request.toLowerCase().match(/[a-z0-9]+/g) ?? []).filter(
      (w) => !VIEW_INTENT_RE.test(w) && !["a", "an", "the", "of", "show", "me", "my"].includes(w),
    );
    const covering = this.views.find((v) => {
      const hay = `${String((v.spec_json as Record<string, unknown>).title ?? "")} ${v.source_prompt} ${v.view_id}`.toLowerCase();
      return words.length > 0 && words.every((w) => hay.includes(w.replace(/s$/, "")));
    });
    if (covering) return { status: "exists", view: { ...covering } };
    // (2) The member-catalog gate — never hallucinate a view for data that isn't here.
    if (!requestCoversMockData(request)) {
      return { status: "data_not_found", message: DATA_NOT_ON_PLATFORM };
    }
    // (3-4) A validated draft (ephemeral until saved).
    const draftId = `draft-${++this.draftSeq}`;
    const spec = baltoSpec(request);
    this.viewDrafts.set(draftId, { spec, request });
    return { status: "ok", draft_id: draftId, spec, attempts: 1 };
  }

  saveViewDraft(draftId: string): SavedViewRow {
    const draft = this.viewDrafts.get(draftId);
    if (!draft) throw new ApiError(404, "no such draft");
    const row = this.saveView({ spec: draft.spec, source_prompt: draft.request });
    this.viewDrafts.delete(draftId); // consumed — same discard-after-save as the real route
    return row;
  }

  getWorkflows(): WorkflowsResponse {
    const w = seedWorkflows();
    return {
      ...w,
      steps: w.steps.map((s) => ({ ...s })),
      recent_executions: w.recent_executions.map((e) => ({ ...e })),
    };
  }

  getKnowledge(): KnowledgeInventoryResponse {
    // The demo tenant's ingested corpus — mirrors api/knowledge_routes.py: per-source counts +
    // newest-ingested timestamp + honest totals (a plain aggregate; the real API strips nothing
    // sensitive because the inventory carries none).
    const sources = [
      { source: "hubspot", document_count: 1280, last_updated: "2026-06-09T12:00:00+00:00" },
      { source: "call", document_count: 262, last_updated: "2026-06-08T09:30:00+00:00" },
      { source: "email", document_count: 188, last_updated: "2026-06-09T15:20:00+00:00" },
      { source: "upload", document_count: 17, last_updated: "2026-06-07T18:05:00+00:00" },
    ];
    return {
      sources: sources.map((s) => ({ ...s })),
      source_count: sources.length,
      total_documents: sources.reduce((n, s) => n + s.document_count, 0),
    };
  }

  searchKnowledge(query: string, _limit?: number): KnowledgeSearchResponse {
    // A few canned hits shaped like the real feed (ref_id + source + snippet + score). The mock
    // always has the embedder, so search_available is true; the real API degrades honestly when
    // the Titan embedder isn't reachable.
    const results = [
      {
        ref_id: "deal-westlake",
        source: "hubspot",
        snippet:
          "Westlake Galleria chiller retrofit — Pinnacle Property Partners, negotiation stage, $284,000.",
        score: 0.8137,
      },
      {
        ref_id: "call-meridian-42",
        source: "call",
        snippet:
          "Discovery call: Meridian wants the retrofit scoped before Q3; budget approved, decision by the controller.",
        score: 0.7421,
      },
    ];
    return { query, results: results.map((r) => ({ ...r })), search_available: true, reason: null };
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
      csv_import_configured: true,
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

  deleteIntegrationCredentials(name: string): DeleteCredentialsResponse {
    const rec = this.requireIntegration(name);
    // Mirrors the API: idempotent — disconnecting an unconnected source is a
    // 200 with deleted:false, never an error.
    const deleted = this.integrationVault.delete(rec.name);
    rec.connected = false;
    rec.status = "not_connected";
    return { name: rec.name, deleted, status: "not_connected" };
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

  listIntegrationSyncs(name: string): IntegrationSyncHistoryResponse {
    const rec = this.requireIntegration(name);
    // The mock keeps no run history (kickIntegrationSync answers inline like a
    // storeless deployment) — an honest empty list, mirroring a fresh tenant.
    return { name: rec.name, runs: [] };
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

  checkout(accountId: string): CheckoutResponse {
    const s = this.requireSignup(accountId);
    // The mock settles like the server's env-gated internal bypass: there is
    // no Stripe-hosted page offline, so the response carries checkout_url null
    // + bypass, and the state machine advances server-side (here) exactly like
    // PaymentService.internal_comp -> on_paid. The SPA must NOT fake a payment
    // success: it advances on the bypass marker and then polls getSignup.
    s.state = "provisioning";
    s.provisioningPollsLeft = 2;
    return { checkout_url: null, bypass: "internal_comp" };
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

  // --- control plane: kill switch / autonomy / traces ------------------------

  getKillswitch(): KillswitchState {
    return { ...this.killswitch };
  }

  setKillswitch(engaged: boolean): KillswitchState {
    this.killswitch = { engaged, scope: "global" };
    return { ...this.killswitch };
  }

  getAutonomy(): AutonomyState {
    return { ...this.autonomy };
  }

  setAutonomy(level: AutonomyLevel): AutonomyState {
    this.autonomy = { level };
    return { ...this.autonomy };
  }

  getControlTraces(limit = 50): DecisionTrace[] {
    const seed: DecisionTrace[] = [
      { id: "trace_001", ts: "2026-06-10T14:22:00Z", tool: "send_email", decision: "approved", status: "executed" },
      { id: "trace_002", ts: "2026-06-10T14:18:00Z", tool: "update_deal", decision: "auto", status: "executed" },
      { id: "trace_003", ts: "2026-06-10T13:55:00Z", tool: "issue_quote", decision: "denied", status: "blocked" },
      { id: "trace_004", ts: "2026-06-10T13:40:00Z", tool: "search_knowledge", decision: "auto", status: "executed" },
    ];
    return seed.slice(0, Math.max(0, limit));
  }

  getOnboarding(): OnboardingState {
    return { ...this.onboarding, steps: { ...this.onboarding.steps } };
  }

  putOnboarding(body: OnboardingPutBody): OnboardingState {
    if (body.steps) {
      for (const [sid, done] of Object.entries(body.steps)) {
        if (sid in this.onboarding.steps) {
          this.onboarding.steps[sid as OnboardingStepId] = !!done;
        }
      }
    }
    if (typeof body.dismissed === "boolean") this.onboarding.dismissed = body.dismissed;
    return this.getOnboarding();
  }

  loadSampleData(): LoadSampleResponse {
    // Idempotent: the demo fixture's counts, the same on every call (a real
    // wipe-then-insert never duplicates). Marks the load done so populated
    // views surface immediately.
    this.onboarding.sample_loaded = true;
    this.onboarding.steps.load_data = true;
    return {
      loaded: true,
      counts: { companies: 40, contacts: 120, deals: 60, activities: 441, documents: 449 },
      onboarding: this.getOnboarding(),
    };
  }
}
