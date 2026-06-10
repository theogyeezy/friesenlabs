// Typed client for the Uplift control-plane API (FastAPI, see api/app.py).
//
// TRUST RULE: this client NEVER sends tenant_id. The server derives the tenant
// solely from the verified JWT claim (api.auth.current_tenant); a tenant_id in a
// request body or header is forbidden by construction. The client only attaches
// the bearer token its `getToken` callback hands it per request — the Cognito
// ID token from the auth layer (api/auth.py requires token_use=id), never a
// literal in the source.
//
// MOCK MODE: when configured with `mock: true` (the default for tests, driven by
// VITE_API_MOCK), every method resolves from canned fixtures and performs NO
// network I/O, so Playwright runs fully offline. Production flips mock->real and
// injects { baseURL, getToken, refreshAuth }; no other code changes.

import { fetchWithAuthRetry } from "../auth/core.js";
import { getValidIdToken, isAuthConfigured, localSignOut, refreshAuthForRetry } from "../auth/cognito";

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
// Signup funnel wire types (public, pre-auth: no bearer token, no tenant_id).
//
// These endpoints run before an account has a tenant, so requests carry NO
// Authorization header and NO tenant_id. The server mints the tenant only after
// payment provisions the instance; the client never names it.
// ---------------------------------------------------------------------------

/** State machine the signup funnel walks through, server-driven. */
export type SignupState =
  | "created"
  | "email_verified"
  | "phone_verified"
  | "paid"
  | "provisioning"
  | "active";

/** Body for POST /signup. Carries no tenant_id (none exists yet). */
export interface SignupBody {
  email: string;
  phone: string;
}

/** Response from POST /signup. */
export interface SignupResponse {
  account_id: string;
  state: SignupState;
}

/** Body for POST /signup/{account_id}/verify-email. */
export interface VerifyEmailBody {
  token: string;
}

export interface VerifyEmailResponse {
  state: SignupState;
  email_verified: boolean;
}

/** Body for POST /signup/{account_id}/verify-phone. */
export interface VerifyPhoneBody {
  code: string;
}

export interface VerifyPhoneResponse {
  state: SignupState;
  phone_verified: boolean;
}

/** Body for POST /signup/{account_id}/checkout. */
export interface CheckoutBody {
  plan: string;
}

export interface CheckoutResponse {
  checkout_id: string;
  stripe_customer_id: string;
}

/** Response from GET /signup/{account_id}: the current funnel state. */
export interface GetSignupResponse {
  state: SignupState;
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

export interface ApiClientConfig {
  /** Base URL of the control plane, e.g. "https://api.uplift.example". */
  baseURL?: string;
  /**
   * Returns the bearer token to attach — the Cognito ID token (api/auth.py
   * rejects access tokens). Called PER REQUEST, so a refreshed token is always
   * picked up even though defaultClient() is a long-lived singleton. May be
   * async (the auth layer refreshes proactively near expiry). Absent or
   * empty => no Authorization header.
   */
  getToken?: () => string | null | Promise<string | null>;
  /**
   * Called once when an authenticated request comes back 401. Should attempt
   * a token refresh and resolve true when a retry is worthwhile. Absent => no
   * retry; the 401 surfaces as an ApiError.
   */
  refreshAuth?: () => Promise<boolean>;
  /**
   * Called when a 401 survives the refresh+retry path (the refresh SUCCEEDED
   * but the API still rejects — dead/desynced session). Should drop the local
   * session so the UI flips to signed-out instead of refresh-churning forever.
   */
  onAuthRejected?: () => void;
  /** When true, resolve from fixtures and never hit the network. */
  mock?: boolean;
  /** Injected fetch (defaults to window.fetch). Lets tests stub if ever needed. */
  fetchImpl?: typeof fetch;
}

/**
 * The single source of truth for the mock flag. VITE_API_MOCK defaults ON when
 * unset so tests and local previews run offline; "0"/"false" builds real mode.
 *
 * The flag is BUILD-TIME ONLY, decided by the Vite env and baked into the
 * bundle. There is deliberately no runtime override (the old `?apimock=0` URL
 * seam is gone): a deployed bundle's mode can never be flipped from the URL in
 * either direction. Offline Playwright coverage of real mode runs against a
 * dedicated VITE_API_MOCK=0 build (see web/playwright.config.ts) with fetch
 * stubbed via page.route.
 */
export function apiMockEnabled(): boolean {
  const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env ?? {};
  const mockFlag = env.VITE_API_MOCK;
  // Mock unless explicitly disabled with "0" / "false" at build time.
  return mockFlag === undefined ? true : !(mockFlag === "0" || mockFlag === "false");
}

/** True when app surfaces should mount the mock/prototype experience. */
export function isApiMock(): boolean {
  return apiMockEnabled();
}

/**
 * Resolve config from the Vite environment (mock flag semantics above). In
 * real mode with Cognito configured, the token callbacks wire to the auth
 * layer: the ID token is read (and refreshed) per request, never snapshotted
 * into the client. In mock/unconfigured builds no callback is wired, so the
 * auth layer stays fully inert.
 */
export function configFromEnv(): ApiClientConfig {
  const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env ?? {};
  const mock = apiMockEnabled();
  const config: ApiClientConfig = {
    baseURL: env.VITE_API_BASE_URL ?? "",
    mock,
  };
  if (!mock && isAuthConfigured()) {
    config.getToken = () => getValidIdToken();
    config.refreshAuth = () => refreshAuthForRetry();
    config.onAuthRejected = () => localSignOut();
  }
  return config;
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

// ---------------------------------------------------------------------------
// Friendly error copy.
//
// Raw transport errors must NEVER reach the user: not ApiError's
// "API <code>: <detail>" message, not fetch's "Failed to fetch", not a bare
// HTTP statusText. Every surface catch block routes through this mapper.
// ---------------------------------------------------------------------------

export const NETWORK_ERROR_MESSAGE =
  "Can't reach Uplift right now. Check your connection and try again.";

// When an error body isn't JSON, ApiError.detail falls back to the HTTP
// statusText. Those bare phrases are not user copy — map them to the fallback.
const BARE_STATUS_TEXTS = new Set([
  "bad request",
  "unauthorized",
  "forbidden",
  "not found",
  "method not allowed",
  "conflict",
  "unprocessable entity",
  "unprocessable content",
  "too many requests",
  "internal server error",
  "bad gateway",
  "service unavailable",
  "gateway timeout",
  "",
]);

/**
 * Map any caught error to copy fit for the user. ApiError statuses get
 * specific phrasing; remaining 4xx surface the server's human-authored
 * `detail` when present (e.g. "approval 3 not pending"); network failures get
 * connection copy; anything else gets the caller's contextual fallback.
 */
export function friendlyErrorMessage(
  e: unknown,
  fallback = "Something went wrong. Please try again.",
): string {
  if (e instanceof ApiError) {
    switch (e.status) {
      case 401:
        return "Your session has ended. Please sign in again.";
      case 403:
        return "You don't have permission to do that in this workspace.";
      case 429:
        return "Too many requests right now. Give it a moment and try again.";
      case 503:
        return "That part of Uplift isn't available right now. Please try again shortly.";
    }
    if (e.status >= 500) {
      return "Something went wrong on our side. Please try again in a moment.";
    }
    // Remaining 4xx (400/404/409/422...): the API authors human-readable
    // detail strings — surface them, but never the raw "API <code>" message
    // and never a bare statusText (the non-JSON-body fallback).
    if (e.detail && !BARE_STATUS_TEXTS.has(e.detail.trim().toLowerCase())) {
      return e.detail;
    }
    return fallback;
  }
  // fetch() rejects with a TypeError on network failure / CORS / DNS.
  if (e instanceof TypeError) return NETWORK_ERROR_MESSAGE;
  return fallback;
}

export class ApiClient {
  private baseURL: string;
  private getToken?: ApiClientConfig["getToken"];
  private refreshAuth?: ApiClientConfig["refreshAuth"];
  private onAuthRejected?: ApiClientConfig["onAuthRejected"];
  private mock: boolean;
  private fetchImpl: typeof fetch;

  // Mutable in-memory mock stores so decide/save behave statefully in tests.
  private mockApprovals: Approval[] | null = null;
  private mockViews: SavedViewRow[] | null = null;
  private mockSignup: MockSignup | null = null;

  constructor(config: ApiClientConfig = {}) {
    this.baseURL = (config.baseURL ?? "").replace(/\/$/, "");
    this.getToken = config.getToken;
    this.refreshAuth = config.refreshAuth;
    this.onAuthRejected = config.onAuthRejected;
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

  private async headers(): Promise<Record<string, string>> {
    const h: Record<string, string> = { "Content-Type": "application/json" };
    // Attach ONLY the bearer token (the Cognito ID token, read per request via
    // the getToken callback). Never a tenant_id header. The server derives
    // tenant from the verified token.
    const token = this.getToken ? await this.getToken() : "";
    if (token) h["Authorization"] = `Bearer ${token}`;
    return h;
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const doFetch = async () =>
      this.fetchImpl(`${this.baseURL}${path}`, {
        method,
        // Headers are rebuilt per attempt so a retry carries a refreshed token.
        headers: await this.headers(),
        // Bodies never include tenant_id (the trust rule); callers cannot inject it
        // because the typed body shapes have no such field.
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    // On a 401: one refresh attempt (refreshAuth), then one retry. A second
    // 401 falls through to the ApiError below and surfaces as signed-out.
    const res = await fetchWithAuthRetry(doFetch, this.refreshAuth);
    if (res.status === 401 && this.onAuthRejected) {
      // The refresh either failed (already signed out locally) or succeeded yet
      // the API still rejects — a dead session either way. Drop it so the UI
      // flips to signed-out instead of refresh-churning on every request.
      this.onAuthRejected();
    }
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

  // Pre-auth request: no Authorization header at all (the signup funnel runs
  // before any tenant or token exists). Still never sends a tenant_id; the typed
  // body shapes have no such field.
  private async requestPublic<T>(method: string, path: string, body?: unknown): Promise<T> {
    const res = await this.fetchImpl(`${this.baseURL}${path}`, {
      method,
      headers: { "Content-Type": "application/json" },
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

  // --- signup funnel (public, pre-auth) -------------------------------------
  //
  // None of these attach a bearer token (the account has no tenant yet) and none
  // send a tenant_id. The mock walks the state machine forward deterministically
  // so Playwright can drive the whole funnel offline.

  /** POST /signup: create the pending account from {email, phone}. */
  async signup(body: SignupBody): Promise<SignupResponse> {
    if (this.mock) {
      this.mockSignup = {
        account_id: "acct_mock_001",
        state: "created",
        email_verified: false,
        phone_verified: false,
        provisioningPollsLeft: 2,
      };
      return { account_id: this.mockSignup.account_id, state: this.mockSignup.state };
    }
    // Pre-auth: send without a bearer token. Body carries email/phone only.
    return this.requestPublic<SignupResponse>("POST", "/signup", body);
  }

  /** POST /signup/{id}/verify-email: confirm the email token. */
  async verifyEmail(accountId: string, body: VerifyEmailBody): Promise<VerifyEmailResponse> {
    if (this.mock) {
      const s = this.requireMockSignup(accountId);
      s.email_verified = true;
      s.state = "email_verified";
      return { state: s.state, email_verified: true };
    }
    return this.requestPublic<VerifyEmailResponse>(
      "POST",
      `/signup/${encodeURIComponent(accountId)}/verify-email`,
      body,
    );
  }

  /** POST /signup/{id}/verify-phone: confirm the SMS code. */
  async verifyPhone(accountId: string, body: VerifyPhoneBody): Promise<VerifyPhoneResponse> {
    if (this.mock) {
      const s = this.requireMockSignup(accountId);
      s.phone_verified = true;
      s.state = "phone_verified";
      return { state: s.state, phone_verified: true };
    }
    return this.requestPublic<VerifyPhoneResponse>(
      "POST",
      `/signup/${encodeURIComponent(accountId)}/verify-phone`,
      body,
    );
  }

  /** POST /signup/{id}/checkout: start Stripe checkout for the chosen plan. */
  async checkout(accountId: string, body: CheckoutBody): Promise<CheckoutResponse> {
    if (this.mock) {
      const s = this.requireMockSignup(accountId);
      // Payment "succeeds" in the mock; provisioning kicks off server-side.
      s.state = "provisioning";
      s.provisioningPollsLeft = 2;
      return { checkout_id: "cs_mock_001", stripe_customer_id: "cus_mock_001" };
    }
    return this.requestPublic<CheckoutResponse>(
      "POST",
      `/signup/${encodeURIComponent(accountId)}/checkout`,
      body,
    );
  }

  /** GET /signup/{id}: poll the funnel state until it reaches "active". */
  async getSignup(accountId: string): Promise<GetSignupResponse> {
    if (this.mock) {
      const s = this.requireMockSignup(accountId);
      if (s.state === "provisioning") {
        if (s.provisioningPollsLeft > 0) {
          s.provisioningPollsLeft -= 1;
        } else {
          s.state = "active";
        }
      }
      return { state: s.state };
    }
    return this.requestPublic<GetSignupResponse>(
      "GET",
      `/signup/${encodeURIComponent(accountId)}`,
    );
  }

  private requireMockSignup(accountId: string): MockSignup {
    if (!this.mockSignup || this.mockSignup.account_id !== accountId) {
      throw new ApiError(404, "no such signup");
    }
    return this.mockSignup;
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
