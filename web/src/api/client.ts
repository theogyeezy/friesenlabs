// Typed client for the Uplift control-plane API (FastAPI, see api/app.py).
//
// TRUST RULE: this client NEVER sends tenant_id. The server derives the tenant
// solely from the verified JWT claim (api.auth.current_tenant); a tenant_id in a
// request body or header is forbidden by construction. The client only attaches
// the bearer token it is handed via config, which is read from the environment,
// never hardcoded.
//
// MOCK MODE: when configured with `mock: true` (the default for tests, driven by
// VITE_API_MOCK), every method resolves from canned fixtures and performs NO
// network I/O, so Playwright runs fully offline. Production flips mock->real and
// injects { baseURL, token }; no other code changes.

// ---------------------------------------------------------------------------
// Wire types (mirror api/app.py request/response shapes)
// ---------------------------------------------------------------------------

/** A pending/decided approval row. Mirrors greenlight.list_pending output. */
export interface Approval {
  id: number;
  tenant_id: string;
  proposed_action: Record<string, unknown> & { action?: string };
  agent: string | null;
  reasoning: string;
  value_at_stake: number | null;
  status: "pending" | "approved" | "denied";
  deny_message?: string;
  decided_by?: string | null;
}

export interface ListApprovalsResponse {
  approvals: Approval[];
}

export type Decision = "approve" | "edit" | "deny";

/** Body for POST /approvals/{id}/decide. Note: carries no tenant_id. */
export interface DecideBody {
  decision: Decision;
  edits?: Record<string, unknown>;
  deny_message?: string;
}

/** A persisted saved-view row. Mirrors api/views.py SavedViews rows. */
export interface SavedViewRow {
  tenant_id: string;
  view_id: string;
  version: number;
  spec_json: Record<string, unknown>;
  semantic_refs: string[];
  source_prompt: string;
  created_by: string;
}

export interface ListViewsResponse {
  views: SavedViewRow[];
}

/** Body for POST /views. Note: carries no tenant_id. */
export interface SaveViewBody {
  spec: Record<string, unknown>;
  source_prompt?: string;
}

export interface Citation {
  claim: string;
  source_ref: string;
  snippet: string;
}

/** Mirrors conv.session.Turn.as_dict(). */
export interface ChatResponse {
  answer: string;
  citations: Citation[];
  pending_approvals?: unknown[];
  slots?: Record<string, unknown>;
  needs_disambiguation?: unknown[];
  delegations?: string[];
  session_id?: string | null;
  tenant_id?: string | null;
}

/** Body for POST /actions. Note: carries no tenant_id. */
export interface ActionBody {
  name: string;
  side_effecting?: boolean;
  channel?: string | null;
  payload?: Record<string, unknown>;
  reasoning?: string;
  value_at_stake?: number | null;
  discount?: number | null;
}

/** Mirrors the /actions response (ActionGate result). */
export interface ActionResponse {
  status: string;
  decision: string;
  detail?: string;
  approval?: Approval | null;
  result?: unknown;
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

export interface ApiClientConfig {
  /** Base URL of the control plane, e.g. "https://api.uplift.example". */
  baseURL?: string;
  /** Bearer token. Read from config/env only; never hardcoded. */
  token?: string;
  /** When true, resolve from fixtures and never hit the network. */
  mock?: boolean;
  /** Injected fetch (defaults to window.fetch). Lets tests stub if ever needed. */
  fetchImpl?: typeof fetch;
}

/**
 * Resolve config from the Vite environment. VITE_API_MOCK enables mock mode;
 * it defaults ON when unset so tests and local previews run offline. The token
 * and base URL come from the environment, never a literal in the source.
 */
export function configFromEnv(): ApiClientConfig {
  const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env ?? {};
  const mockFlag = env.VITE_API_MOCK;
  // Mock unless explicitly disabled with "0" / "false".
  const mock = mockFlag === undefined ? true : !(mockFlag === "0" || mockFlag === "false");
  return {
    baseURL: env.VITE_API_BASE_URL ?? "",
    token: env.VITE_API_TOKEN ?? "",
    mock,
  };
}

// ---------------------------------------------------------------------------
// Fixtures (mock mode): canned, deterministic, offline
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(`API ${status}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export class ApiClient {
  private baseURL: string;
  private token: string;
  private mock: boolean;
  private fetchImpl: typeof fetch;

  // Mutable in-memory mock stores so decide/save behave statefully in tests.
  private mockApprovals: Approval[] | null = null;
  private mockViews: SavedViewRow[] | null = null;

  constructor(config: ApiClientConfig = {}) {
    this.baseURL = (config.baseURL ?? "").replace(/\/$/, "");
    this.token = config.token ?? "";
    this.mock = config.mock ?? false;
    this.fetchImpl =
      config.fetchImpl ??
      (typeof globalThis !== "undefined" && globalThis.fetch
        ? globalThis.fetch.bind(globalThis)
        : (undefined as unknown as typeof fetch));
  }

  isMock(): boolean {
    return this.mock;
  }

  // --- internal request helper (real mode only) -----------------------------

  private headers(): Record<string, string> {
    const h: Record<string, string> = { "Content-Type": "application/json" };
    // Attach ONLY the bearer token. Never a tenant_id header. The server derives
    // tenant from the verified token.
    if (this.token) h["Authorization"] = `Bearer ${this.token}`;
    return h;
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const res = await this.fetchImpl(`${this.baseURL}${path}`, {
      method,
      headers: this.headers(),
      // Bodies never include tenant_id (the trust rule); callers cannot inject it
      // because the typed body shapes have no such field.
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const j = (await res.json()) as { detail?: string };
        if (j && typeof j.detail === "string") detail = j.detail;
      } catch {
        // non-JSON error body; keep statusText
      }
      throw new ApiError(res.status, detail);
    }
    return (await res.json()) as T;
  }

  // --- mock-store accessors -------------------------------------------------

  private approvalsStore(): Approval[] {
    if (this.mockApprovals === null) this.mockApprovals = seedApprovals();
    return this.mockApprovals;
  }

  private viewsStore(): SavedViewRow[] {
    if (this.mockViews === null) this.mockViews = seedViews();
    return this.mockViews;
  }

  // --- API methods ----------------------------------------------------------

  async listApprovals(): Promise<Approval[]> {
    if (this.mock) {
      return this.approvalsStore().filter((a) => a.status === "pending").map((a) => ({ ...a }));
    }
    const data = await this.request<ListApprovalsResponse>("GET", "/approvals");
    return data.approvals;
  }

  async decideApproval(id: number, body: DecideBody): Promise<Approval> {
    if (this.mock) {
      const store = this.approvalsStore();
      const rec = store.find((a) => a.id === id);
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
    return this.request<Approval>("POST", `/approvals/${id}/decide`, body);
  }

  async listViews(): Promise<SavedViewRow[]> {
    if (this.mock) {
      return this.viewsStore().map((v) => ({ ...v }));
    }
    const data = await this.request<ListViewsResponse>("GET", "/views");
    return data.views;
  }

  async getView(viewId: string): Promise<SavedViewRow> {
    if (this.mock) {
      const v = this.viewsStore().find((row) => row.view_id === viewId);
      if (!v) throw new ApiError(404, "no such view");
      return { ...v };
    }
    return this.request<SavedViewRow>("GET", `/views/${encodeURIComponent(viewId)}`);
  }

  async saveView(body: SaveViewBody): Promise<SavedViewRow> {
    if (this.mock) {
      const store = this.viewsStore();
      const spec = body.spec as Record<string, unknown>;
      const viewId = String(spec.view_id ?? "");
      const existing = store.filter((r) => r.view_id === viewId);
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
      store.push(row);
      return { ...row };
    }
    return this.request<SavedViewRow>("POST", "/views", body);
  }

  async chat(message: string): Promise<ChatResponse> {
    if (this.mock) {
      return cannedChat(message);
    }
    return this.request<ChatResponse>("POST", "/chat", { message });
  }

  async runAction(body: ActionBody): Promise<ActionResponse> {
    if (this.mock) {
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
    return this.request<ActionResponse>("POST", "/actions", body);
  }
}

/** Build a client from the Vite environment (mock by default). */
export function createClient(overrides: ApiClientConfig = {}): ApiClient {
  return new ApiClient({ ...configFromEnv(), ...overrides });
}

/** A shared, lazily-created default client for app surfaces. */
let _default: ApiClient | null = null;
export function defaultClient(): ApiClient {
  if (_default === null) _default = createClient();
  return _default;
}
