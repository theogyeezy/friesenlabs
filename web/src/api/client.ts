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
import { getValidIdToken, isAuthConfigured, refreshAuthForRetry, sessionExpired } from "../auth/cognito";
// Type-only imports (erased at build, no runtime/module-graph cost): the Cube
// query + data-row shapes the view-spec renderer is built around.
import type { CubeQuery } from "../dashboard/viewSpec";
import type { DataRow, LoadData } from "../dashboard/SpecRenderer";

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

/** Body for POST /views/{id}/refine — the NL "ask for a chart" instruction. */
export interface RefineViewBody {
  instruction: string;
}

/** One panel's resolved rows (POST /views/{id}/data). `panel` is the index into
 * the view-spec's `layout`; `rows` are the Cube result rows for that panel's
 * CubeQuery, keyed by member name. Mirrors api/cube_data_routes.py. */
export interface ViewDataPanel {
  panel: number;
  rows: DataRow[];
}

/** Response from POST /views/{id}/data: the primary (first data-bearing) panel's
 * rows under `rows`, plus the per-panel `panels` array for multi-panel views.
 * The server runs each panel's CubeQuery as the verified tenant (THE TRUST RULE)
 * — the client never sends a tenant_id and never a query (the saved spec is the
 * source of truth server-side). */
export interface ViewDataResponse {
  rows: DataRow[];
  panels: ViewDataPanel[];
}

/** GET /dashboards — named compositions of saved views (kind=dashboard rows). */
export interface ListDashboardsResponse {
  dashboards: SavedViewRow[];
}

/** GET /dashboards/{id} — the dashboard row plus the latest row of every view it
 * references, resolved server-side in one shot. A referenced view that no longer
 * resolves is simply absent from `views` (the screen shows a per-panel notice). */
export interface DashboardResolvedResponse {
  dashboard: SavedViewRow;
  views: Record<string, SavedViewRow>;
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
  /** Balto (conv/views.py): true when the turn is a view-shaped ask — the answer is the exact
   * Balto status line and `view_request` is what the client forwards to synthesizeView. */
  view_intent?: boolean;
  view_request?: string | null;
  /** Grounding observability (knowledge audit P0): the retrieval evidence for this turn.
   * null/undefined when retrieval was deliberately skipped (action/Balto turns). */
  grounding_status?: "grounded" | "no_sources_found" | "ungrounded" | "unavailable" | null;
  retrieved_count?: number | null;
}

/** Body for POST /views/synthesize — the NL ask Balto builds a view for. No tenant_id. */
export interface SynthesizeViewBody {
  request: string;
}

/** Mirrors conv.views.ViewSynthesizer.synthesize results (status-keyed, honest). */
export interface SynthesizeViewResponse {
  status: "ok" | "exists" | "data_not_found" | "invalid";
  /** status=ok: the ephemeral draft handle (save persists it; discard = never saving). */
  draft_id?: string;
  /** status=ok: the validated view-spec JSON (schema + real Cube members — spec, not code). */
  spec?: Record<string, unknown>;
  /** status=exists: the saved view that already covers the ask. */
  view?: SavedViewRow;
  /** status=data_not_found: the honest "data does not exist on the platform" copy. */
  message?: string;
  error?: string;
  attempts?: number;
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
// Deals / pipeline wire types (mirror api/deals_routes.py shapes).
// ---------------------------------------------------------------------------

/** One deal card on the pipeline board (GET /deals). No tenant_id — the API
 * strips it; the client never needs it. */
export interface DealCard {
  id: string;
  title: string | null;
  stage: string;
  amount: number | null;
  currency: string | null;
  company_id: string | null;
  contact_id: string | null;
  company_name: string | null;
  created_at: string | null;
}

/** One ordered stage column: canonical stages first (kept even when empty so
 * the board has a stable spine), then any extra stages found in the data. */
export interface DealStageGroup {
  stage: string;
  label: string;
  deals: DealCard[];
  count: number;
  total_amount: number;
}

export interface ListDealsResponse {
  stages: DealStageGroup[];
  total: number;
  stage_order: string[];
}

/** Detail rows carry the joined contact display fields too. */
export interface DealDetail extends DealCard {
  contact_name?: string | null;
  contact_email?: string | null;
}

export interface DealActivity {
  id: string | null;
  kind: string | null;
  body: string | null;
  occurred_at: string | null;
}

export interface DealDetailResponse {
  deal: DealDetail;
  activities: DealActivity[];
}

/** Body for POST /deals/{id}/move-stage. Note: carries no tenant_id. */
export interface MoveStageBody {
  to_stage: string;
}

/**
 * The HONEST move-stage response: the move did NOT happen. The server landed a
 * Greenlight proposal (`approval_id`) and the deal stays in `from_stage` until
 * a human approves — surfaces must keep showing the current stage.
 */
export interface MoveStageResponse {
  queued: boolean;
  approval_id: number | string | null;
  status: string;
  from_stage: string;
  to_stage: string;
  detail: string;
}

// ---------------------------------------------------------------------------
// Contacts / companies directory wire types (mirror api/contacts_routes.py).
// READ-ONLY this cycle: there are no write methods — CRM writes arrive with a
// later update_contact tool through the Greenlight gate.
// ---------------------------------------------------------------------------

/** One directory contact row (GET /contacts). No tenant_id — the API strips
 * it. `title` is always null today: the schema carries no title column yet;
 * the API names it so this shape stays stable when it lands. */
export interface ContactRow {
  id: string;
  name: string | null;
  title: string | null;
  email: string | null;
  phone: string | null;
  company_id: string | null;
  company_name: string | null;
  created_at: string | null;
  /** Newest activity timestamp across the contact's logged activities. */
  last_activity_at: string | null;
}

export interface ListContactsResponse {
  contacts: ContactRow[];
  count: number;
  has_more: boolean;
  limit: number;
  offset: number;
  q: string | null;
}

export interface ContactActivity {
  id: string | null;
  kind: string | null;
  body: string | null;
  occurred_at: string | null;
}

/** A company's OPEN deal riding on contact/company detail (the Pipeline seam). */
export interface CompanyDeal {
  id: string;
  title: string | null;
  stage: string;
  amount: number | null;
  currency: string | null;
  company_id: string | null;
  contact_id: string | null;
  created_at: string | null;
}

export interface ContactDetailResponse {
  contact: ContactRow;
  activities: ContactActivity[];
  /** The contact's company's open deals — links toward the Pipeline board. */
  company_deals: CompanyDeal[];
}

/** One directory company row with contact + open-deal counts. */
export interface CompanyRow {
  id: string;
  name: string | null;
  domain: string | null;
  created_at: string | null;
  contact_count: number;
  open_deal_count: number;
}

export interface ListCompaniesResponse {
  companies: CompanyRow[];
  count: number;
  has_more: boolean;
  limit: number;
  offset: number;
  q: string | null;
}

export interface CompanyDetailResponse {
  company: CompanyRow;
  contacts: ContactRow[];
  deals: CompanyDeal[];
}

/** Query params for the directory lists. Carries NO tenant_id (the trust
 * rule); q is server-capped at 200 chars (422 beyond). */
export interface DirectoryListParams {
  q?: string;
  limit?: number;
  offset?: number;
}

// ---------------------------------------------------------------------------
// CRM write wire types (POST /contacts, PATCH /contacts/{id},
//                       POST /deals, PATCH /deals/{id}).
// No tenant_id anywhere — the trust rule; the server derives it from the JWT.
// ---------------------------------------------------------------------------

/** Body for POST /contacts. */
export interface CreateContactBody {
  name: string;
  email?: string | null;
  phone?: string | null;
  company_id?: string | null;
}

/** Body for PATCH /contacts/{id}: partial update, at least one field required. */
export interface EditContactBody {
  name?: string | null;
  email?: string | null;
  phone?: string | null;
  company_id?: string | null;
}

/** Response from POST /contacts (201). The created contact row (without tenant_id). */
export interface CreateContactResponse {
  contact: { id: string; name: string | null; email: string | null; phone: string | null };
}

/** Response from PATCH /contacts/{id}. */
export interface EditContactResponse {
  id: string;
  updated: Record<string, unknown>;
  skipped?: Record<string, string>;
  contact?: { id: string; name: string | null; email: string | null; phone: string | null };
}

/** Body for POST /deals. */
export interface CreateDealBody {
  title: string;
  amount?: number | null;
  stage?: string;
  contact_id?: string | null;
}

/** Body for PATCH /deals/{id}: partial update (title and/or amount). */
export interface EditDealBody {
  title?: string | null;
  amount?: number | null;
}

/** Response from POST /deals (201). */
export interface CreateDealResponse {
  deal: { id: string; name: string | null; stage: string; amount: number | null };
}

/** Response from PATCH /deals/{id}. */
export interface EditDealResponse {
  id: string;
  updated: Record<string, unknown>;
  deal?: { id: string; name: string | null; stage: string; amount: number | null };
}

// ---------------------------------------------------------------------------
// Onboarding / first-run wire types (mirror api/onboarding_routes.py shapes).
// The per-tenant first-run checklist state + the one-click load-sample result.
// No tenant_id anywhere — the server derives it from the verified claim.
// ---------------------------------------------------------------------------

/** The first-run checklist step ids (the server's STEP_IDS allow-list). */
export type OnboardingStepId = "load_data" | "try_chat" | "invite_team";

/** GET /onboarding — the calling tenant's first-run state. A brand-new tenant
 * gets the honest fresh default (every step false, not dismissed, no sample). */
export interface OnboardingState {
  tenant_id: string;
  steps: Record<OnboardingStepId, boolean>;
  dismissed: boolean;
  sample_loaded: boolean;
}

/** PUT /onboarding body — a partial update. Only provided fields change; steps
 * merge key-by-key. No `sample_loaded` (set ONLY by a real load-sample). */
export interface OnboardingPutBody {
  steps?: Partial<Record<OnboardingStepId, boolean>>;
  dismissed?: boolean;
}

/** POST /onboarding/load-sample — the idempotent demo-fixture load result. */
export interface LoadSampleResponse {
  loaded: boolean;
  counts: Record<string, number>;
  onboarding: OnboardingState | null;
}

// ---------------------------------------------------------------------------
// Agent crew wire types (mirror api/agents_routes.py shapes). READ-ONLY: the
// crew is defined by the owned roster + assembled by signup provisioning —
// there is nothing to mutate from the client.
// ---------------------------------------------------------------------------

/** A tool's TRUSTED policy, straight from the server-side registry: "auto"
 * tools run on their own (read-only), "always_ask" tools route every action
 * through Greenlight for human sign-off. Never invented client-side. */
export type ToolPolicy = "auto" | "always_ask";

export interface CrewTool {
  name: string;
  policy: ToolPolicy;
}

/** One crew member from the owned roster definitions: display name, specialty
 * label, the duty description (the agent's actual instruction), and its tools
 * with trusted policies. */
export interface CrewAgent {
  name: string;
  role: string;
  description: string;
  is_coordinator: boolean;
  tools: CrewTool[];
}

/** The coordinator additionally carries the TRUNCATED provisioned id tail
 * (last few chars for display — the API never sends the full MA id). */
export interface CrewCoordinator extends CrewAgent {
  id_tail: string | null;
}

export interface AgentCrewResponse {
  /** True only when this tenant's Managed Agents crew exists live (a real
   * tenant_workspaces row — not placeholders). False = assembles at signup. */
  provisioned: boolean;
  /** Last few chars of the provisioned environment id — never the full id. */
  environment_id_tail: string | null;
  coordinator: CrewCoordinator;
  roster: CrewAgent[];
  count: number;
}

// ---------------------------------------------------------------------------
// Workflows wire types (mirror api/workflows_routes.py shapes). READ-ONLY: the
// step diagram is the OWNED provisioning semantics serialized server-side, and
// the execution feed carries name + status + timestamps ONLY (ARNs and the AWS
// account id are stripped server-side; inputs/outputs are never fetched).
// ---------------------------------------------------------------------------

/** One step of the provisioning funnel (signup → verify → pay → provision →
 * activate) — static, owned semantics; never a live AWS Describe. */
export interface WorkflowStep {
  id: string;
  label: string;
  description: string;
}

/** Step Functions execution statuses, straight from AWS. The (string & {})
 * arm keeps unknown future statuses flowing through (rendered neutrally). */
export type WorkflowExecutionStatus =
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED"
  | "TIMED_OUT"
  | "ABORTED"
  | (string & {});

/** One recent run: display fields ONLY — no ARNs, no payloads, ever. */
export interface WorkflowExecution {
  name: string | null;
  status: WorkflowExecutionStatus | null;
  started_at: string | null;
  stopped_at: string | null;
}

export interface WorkflowsResponse {
  /** Display name + kind of the machine — never an ARN. */
  machine: { name: string; kind: string };
  steps: WorkflowStep[];
  step_count: number;
  /** False = the run feed is honestly unavailable (see `reason`): the live api
   * task lacks the read grant until REQ-009, or the ARN isn't configured. The
   * static diagram still renders either way — an informative state, NOT an
   * error. */
  executions_available: boolean;
  reason: string | null;
  recent_executions: WorkflowExecution[];
}

// ---------------------------------------------------------------------------
// Knowledge wire types (mirror api/knowledge_routes.py shapes). READ-ONLY: the
// inventory is a plain per-source aggregate over the tenant's documents, and
// search carries ref_id + source + a bounded snippet + score (the full content
// is never dumped; ARNs/embeddings never leave).
// ---------------------------------------------------------------------------

/** One ingested source's footprint in the tenant's corpus. */
export interface KnowledgeSource {
  source: string | null;
  document_count: number;
  /** Newest-ingested timestamp for the source (MAX(created_at)); null if absent. */
  last_updated: string | null;
}

export interface KnowledgeInventoryResponse {
  sources: KnowledgeSource[];
  source_count: number;
  total_documents: number;
}

/** One search hit: display fields only — no embedding, no full content dump. */
export interface KnowledgeSearchResult {
  ref_id: string | null;
  source: string | null;
  snippet: string;
  score: number | null;
}

export interface KnowledgeSearchResponse {
  query: string;
  results: KnowledgeSearchResult[];
  /** False = the query embedder (Titan/Bedrock) isn't reachable yet (env-key-gated):
   * the result list is empty and `reason` explains it — an informative state, NOT an
   * error. The inventory tab stays useful regardless. */
  search_available: boolean;
  reason: string | null;
}

/** POST /knowledge/documents result — the customer document-add path (knowledge audit P0). */
export interface KnowledgeAddDocumentResponse {
  ref_id: string | null;
  chunks: number;
  source: string | null;
  title: string | null;
}

// ---------------------------------------------------------------------------
// Integrations wire types (mirror api/integrations_routes.py shapes).
// ---------------------------------------------------------------------------

/** Connection status straight from the API — never invented client-side. */
export type IntegrationStatus = "connected" | "not_connected" | "unknown" | "available";

/** Connector kind: "sync" = credentialed pull connector; "file" = CSV push import (no vault slot). */
export type IntegrationKind = "sync" | "file";

/** One known connector + this tenant's connection status (GET /integrations). */
export interface Integration {
  name: string;
  label: string;
  category: string;
  description: string;
  /** true/false from the vault check; null = the API honestly couldn't tell. File-kind = always null. */
  connected: boolean | null;
  status: IntegrationStatus;
  /** "sync" = credentialed pull; "file" = CSV push import (POST /integrations/csv/import). */
  kind: IntegrationKind;
  /** True when the connector is experimental/preview. */
  experimental?: boolean;
  /** Latest recorded sync run for this connector; null/absent = none recorded
   *  (or history isn't configured) — the UI never invents a "last synced". */
  last_sync?: SyncRun | null;
}

/** One sync-run history row (integration_sync_runs; GET /integrations/{name}/syncs). */
export interface SyncRun {
  id: string;
  source: string;
  triggered_by: "api" | "schedule";
  status: "running" | "succeeded" | "failed" | "aborted";
  started_at: string | null;
  finished_at: string | null;
  pulled: number | null;
  landed_rows: number | null;
  chunks: number | null;
  embedded: number | null;
  skipped: number | null;
  /** Exception CLASS name only — the API never relays provider error text. */
  error: string | null;
}

export interface ListIntegrationsResponse {
  integrations: Integration[];
  /** False = the deployment has no secret writer: connecting will 503. */
  secrets_configured: boolean;
  /** False = the ingestion plane isn't wired: sync-now will 503. */
  sync_configured: boolean;
  /** False = the csv importer isn't wired: POST /integrations/csv/import will 503. */
  csv_import_configured: boolean;
  /** False/absent = no sync-run store: history 503s and last_sync stays null.
   *  Optional so a web deploy ahead of the API parses older payloads cleanly. */
  sync_history_configured?: boolean;
}

/**
 * Body for POST /integrations/{name}/credentials. Carries the token ONLY —
 * never a tenant_id (the server derives the vault slot from the verified
 * claim). The token is write-only: the API never echoes it back and this
 * client never logs it.
 */
export interface IntegrationCredentialsBody {
  token: string;
}

/** Response from POST /integrations/{name}/credentials (no token echo). */
export interface StoreCredentialsResponse {
  name: string;
  secret_ref: string;
  stored: boolean;
  status: IntegrationStatus;
  /** true = the provider accepted the token at connect time; null/absent = the
   *  API couldn't verify (no prober / provider unreachable) — stored anyway.
   *  A definitive provider rejection never reaches here (the POST 422s). */
  verified?: boolean | null;
}

/** Response from DELETE /integrations/{name}/credentials (disconnect). */
export interface DeleteCredentialsResponse {
  name: string;
  /** false = nothing was vaulted (idempotent disconnect, still a 200). */
  deleted: boolean;
  status: IntegrationStatus;
}

/**
 * Response from POST /integrations/{name}/sync. With a sync-run store wired the
 * API answers 202 with `run` (the background run to watch in the history);
 * a legacy/storeless deployment answers 200 with the inline `result` bag.
 */
export interface IntegrationSyncResponse {
  name: string;
  result?: Record<string, unknown>;
  run?: SyncRun;
}

/** Response from GET /integrations/{name}/syncs (newest first). */
export interface IntegrationSyncHistoryResponse {
  name: string;
  runs: SyncRun[];
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
  /**
   * The Stripe-hosted Checkout page the BROWSER must be sent to
   * (window.location.assign). Payment success is never assumed client-side:
   * provisioning fires only off the signed Stripe webhook, and after the
   * round-trip the flow polls GET /signup/{id} for the real state. null on
   * the env-gated internal bypass (settled server-side, no Stripe page).
   */
  checkout_url: string | null;
  /**
   * "internal_comp" when the env-gated internal-domain bypass settled the
   * payment immediately through the SAME idempotent ledger + provisioning
   * path as the webhook. Absent on the normal Stripe path.
   */
  bypass?: string;
  /** Present on the Stripe path only (absent on the bypass response). */
  checkout_id?: string;
  stripe_customer_id?: string;
}

/** Response from GET /signup/{account_id}: the current funnel state. */
export interface GetSignupResponse {
  state: SignupState;
}

// ---------------------------------------------------------------------------
// Public lead capture (pre-auth: no bearer token, no tenant_id).
//
// The marketing site's "Book a call" / "Email us" forms POST here. Public by
// design — there is no account or tenant yet — so the request carries only the
// visitor's contact details. Callers degrade to a mailto: link on any non-2xx
// (see web/src/screens/landing.tsx) so a lead is never silently dropped.
// ---------------------------------------------------------------------------

/** Which marketing form produced the lead. */
export type LeadKind = "book_call" | "email";

/** Body for POST /public/leads. Carries no tenant_id (none exists yet). */
export interface LeadBody {
  kind: LeadKind;
  name: string;
  email: string;
  message?: string;
  company?: string;
}

/** Response from POST /public/leads — just an acknowledgement. */
export interface LeadResponse {
  ok: boolean;
  id?: string;
}

// ---------------------------------------------------------------------------
// Control plane: kill switch + autonomy + decision traces (authed).
//
// These back the Security & control surface. The TRUST RULE still holds — the
// server scopes every read/write to the verified JWT tenant; the client never
// sends a tenant_id. Each endpoint may answer 404 on a deployment where the
// control plane isn't wired yet; the UI feature-detects that and shows the
// control as honestly disabled rather than faking a working toggle.
// ---------------------------------------------------------------------------

/** GET/PUT /control/killswitch — the master stop for all agents. */
export interface KillswitchState {
  engaged: boolean;
  scope: "global";
}

/** Autonomy ladder: 0 = off/suggest-only … 3 = fully autonomous. */
export type AutonomyLevel = 0 | 1 | 2 | 3;

/** GET/PUT /control/autonomy — the workspace-wide autonomy level. */
export interface AutonomyState {
  level: AutonomyLevel;
}

/** One row in the decision-trace feed (GET /control/traces). Display-only:
 * no payloads, no tenant_id — just what a human needs to audit a decision. */
export interface DecisionTrace {
  id: string;
  ts: string | null;
  tool: string | null;
  decision: string | null;
  status: string | null;
}

export interface DecisionTracesResponse {
  traces: DecisionTrace[];
}

// ---------------------------------------------------------------------------
// Self-service billing (authed, Stripe Customer Portal).
//
// The portal is Stripe-hosted: the tenant changes their card, cancels, or views
// invoices on Stripe's pages, then returns to our return_url. The TRUST RULE
// holds — the server resolves the Stripe customer from the verified JWT tenant;
// the client sends no tenant_id and no customer id.
// ---------------------------------------------------------------------------

/** GET /billing — the settings screen's billing bootstrap read. */
export interface BillingState {
  /** True when this tenant has a Stripe customer (so "Manage billing" can do something). */
  customer: boolean;
  /** The current plan id (starter/team/scale), or null if not yet on a plan. */
  plan: string | null;
  /** Subscription lifecycle: "active" | "past_due" | "unpaid" | "canceled" | ... */
  status: string;
}

/** POST /billing/portal-session — the Stripe-hosted portal URL to redirect to. */
export interface BillingPortalSessionResponse {
  /** The billing.stripe.com URL the browser must be sent to (window.location.assign). */
  url: string;
}

// --- Cortex (ML) health (GET /cortex/health) -------------------------------
/** The champion model summary, or null when the tenant has no champion yet. */
export interface CortexChampion {
  version: number;
  estimator: string;
  metrics: Record<string, number>;
}
/** Live-AUC drift verdict, or null when there's no/insufficient prediction evidence. */
export interface CortexDrift {
  drift: boolean;
  recent_auc: number | null;
  n_outcomes: number;
  /** The champion's registered (training-time) AUC — the baseline drift compares against. */
  registered_auc: number;
  /** Present when a number can't honestly be computed (#194 drift honesty). */
  reason?: string;
}
/** GET /cortex/health — real per-tenant model health (NO fabricated numbers).
 * status: "no_registry" (registry unwired) | "no_champion" (no model yet) |
 * "serving" (champion live) | "drifting" (live-AUC degraded). */
export interface CortexHealth {
  tenant_id: string;
  status: "no_registry" | "no_champion" | "serving" | "drifting";
  champion: CortexChampion | null;
  model_count: number;
  drift: CortexDrift | null;
}

// --- Billing invoices (GET /billing/invoices) ------------------------------
/** One Stripe invoice row, normalized server-side from the tenant's customer. */
export interface Invoice {
  id: string;
  number: string | null;
  amount_due: number;
  amount_paid: number;
  currency: string;
  status: string;
  created: number;
  hosted_invoice_url: string | null;
  invoice_pdf: string | null;
}

// --- CSV import (POST /integrations/csv/import) -----------------------------
/** Per-row error from a CSV import (1-based spreadsheet line). */
export interface CsvImportRowError {
  row: number;
  error: string;
}

/** The csv_import.ImportReport shape (asdict — mirror of
 * ingest/connectors/csv_import.py). Counts + errors come straight from the
 * server; per-row problems land in `errors` (never throw), a whole-file problem
 * is an ApiError(422). */
export interface CsvImportReport {
  entity: string;
  mapping: Record<string, string>;
  total_rows: number;
  imported: number;
  rows_upserted: number;
  embedded: number;
  skipped_unchanged: number;
  errors: CsvImportRowError[];
}

/** POST /integrations/csv/import wraps the report as {name, report}. */
export interface CsvImportResponse {
  name: string;
  report: CsvImportReport;
}

// --- Account data lifecycle (GET /account/export, POST /account/delete) -----
/** GET /account/export — the tenant's full RLS-scoped data bundle (sections are
 * omitted when their store is unconfigured). Typed loosely: it's an egress dump. */
export type AccountExport = Record<string, unknown>;
/** POST /account/delete — per-table teardown report. Append-only audit tables are
 * reported under `retained` (with a reason), never force-deleted. */
export interface AccountDeleteReport {
  deleted: Record<string, number>;
  retained: Record<string, string>;
  failed: Record<string, string>;
}

// --- Persisted workspace settings (GET/PUT /account/settings) ---------------
/** A flat map of notification preference flags (bool) / values (string). */
export type NotificationPrefs = Record<string, boolean | string>;
/** GET/PUT /account/settings — workspace name + notification prefs. */
export interface WorkspaceSettings {
  workspace_name: string | null;
  notification_prefs: NotificationPrefs;
}
/** PUT /account/settings body — a partial update (only present fields are written). */
export interface WorkspaceSettingsUpdate {
  workspace_name?: string;
  notification_prefs?: NotificationPrefs;
}

// --- Module entitlements (GET/PUT /account/modules — the "your suite" surface) ----
/** One module in the catalog + whether this tenant has it enabled. */
export interface ModuleEntry {
  id: string;
  name: string;
  monthly_cents: number;
  required: boolean;
  enabled: boolean;
}
/** GET/PUT /account/modules — the catalog, the à-la-carte monthly total, and the enabled
 * route-ids the app gates its nav/routes against. */
/** Phase-2 billing sync result, present on a PUT response only when module billing is wired
 * (per-module Stripe Prices configured). status: synced | no_customer | no_subscription | error. */
export interface ModuleBilling {
  status: string;
  added?: string[];
  removed?: string[];
  error?: string;
}
export interface ModuleCatalog {
  modules: ModuleEntry[];
  monthly_total_cents: number;
  enabled_routes: string[];
  /** Only on PUT responses, and only when billing is configured server-side. */
  billing?: ModuleBilling;
}

/** A Sidecar next-action suggestion grounded in a real CRM row (GET /sidecar/suggestions). */
export interface SidecarSuggestion {
  id: string;
  kind: string;
  entity_type: "deal" | "contact";
  entity_id: string;
  title: string;
  detail: string;
  value_at_stake: number | null;
  action: { action: string; [k: string]: unknown };
}
export interface SidecarSuggestions {
  suggestions: SidecarSuggestion[];
  total: number;
  truncated: boolean;
}
/** POST /sidecar/act result — the queued Greenlight draft. */
export interface SidecarActResult {
  status: string;
  approval_id: string | null;
  suggestion_id: string;
  action: string;
}

// --- Agent marketplace (starter playbook templates) -------------------------
/** One committed starter template (GET /studio/templates) — a "ready-made agent"
 * a tenant can add to its library. `definition` is the playbook spec (opaque here). */
export interface StudioTemplateSummary {
  template_id: string;
  summary: string;
  definition: Record<string, unknown>;
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
    // A 401 that survives refresh+retry is a dead session: clear it and land
    // on the sign-in route (the gated root) instead of refresh-churning.
    config.onAuthRejected = () => sessionExpired();
  }
  return config;
}

// ---------------------------------------------------------------------------
// Fixtures (mock mode): canned, deterministic, offline — in MOCK BUILDS ONLY.
//
// The fixture data and mock state machine live in ./mockData, loaded through
// the build-time-gated dynamic import below. Real-mode bundles fold the gate
// to false at build time, so the mock chunk (demo tenants, canned deals,
// fixture numbers, mock account ids) is never emitted into a production
// bundle — prod bundle hygiene, provable by grepping dist-real.
// ---------------------------------------------------------------------------

/** Shape of the lazily-loaded mock module (type-only; erased at runtime). */
type MockDataModule = typeof import("./mockData");

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

  // Lazily-instantiated mock API (one per client, so decide/save/signup stay
  // stateful within a test run). Loaded only in mock builds — see mockApi().
  private mockApiPromise: Promise<InstanceType<MockDataModule["MockApi"]>> | null = null;

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

  // Multipart upload (file bodies — CSV import). Like request() but sends a
  // FormData body and does NOT set Content-Type (the browser sets the multipart
  // boundary). Same bearer-auth + 401-refresh-retry discipline; still no tenant_id
  // (the server derives it from the verified token).
  private async requestMultipart<T>(method: string, path: string, form: FormData): Promise<T> {
    const doFetch = async () => {
      // Auth-only headers — never force Content-Type on a multipart body.
      const h: Record<string, string> = {};
      const token = this.getToken ? await this.getToken() : "";
      if (token) h["Authorization"] = `Bearer ${token}`;
      return this.fetchImpl(`${this.baseURL}${path}`, { method, headers: h, body: form });
    };
    const res = await fetchWithAuthRetry(doFetch, this.refreshAuth);
    if (res.status === 401 && this.onAuthRejected) this.onAuthRejected();
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

  // --- mock surface (mock builds only) ---------------------------------------

  /**
   * The lazily-loaded mock API. The outer condition is the BUILD-TIME gate:
   * Vite replaces import.meta.env.VITE_API_MOCK with a literal, so in real
   * builds the branch folds away and rollup never emits the mockData chunk.
   * The throw is unreachable in practice (this.mock is only true in mock
   * builds — apiMockEnabled() above shares the same env flag), but fails
   * loudly rather than fetching fixtures if that invariant ever breaks.
   */
  private mockApi(): Promise<InstanceType<MockDataModule["MockApi"]>> {
    if (import.meta.env.VITE_API_MOCK !== "0" && import.meta.env.VITE_API_MOCK !== "false") {
      if (this.mockApiPromise === null) {
        this.mockApiPromise = import("./mockData").then((m) => new m.MockApi());
      }
      return this.mockApiPromise;
    }
    return Promise.reject(new Error("mock fixtures are not part of real-mode builds"));
  }

  // --- API methods ----------------------------------------------------------

  async listApprovals(): Promise<Approval[]> {
    if (this.mock) {
      return (await this.mockApi()).listApprovals();
    }
    const data = await this.request<ListApprovalsResponse>("GET", "/approvals");
    return data.approvals;
  }

  async decideApproval(id: number, body: DecideBody): Promise<Approval> {
    if (this.mock) {
      return (await this.mockApi()).decideApproval(id, body);
    }
    return this.request<Approval>("POST", `/approvals/${id}/decide`, body);
  }

  async listViews(): Promise<SavedViewRow[]> {
    if (this.mock) {
      return (await this.mockApi()).listViews();
    }
    const data = await this.request<ListViewsResponse>("GET", "/views");
    return data.views;
  }

  async getView(viewId: string): Promise<SavedViewRow> {
    if (this.mock) {
      return (await this.mockApi()).getView(viewId);
    }
    return this.request<SavedViewRow>("GET", `/views/${encodeURIComponent(viewId)}`);
  }

  async saveView(body: SaveViewBody): Promise<SavedViewRow> {
    if (this.mock) {
      return (await this.mockApi()).saveView(body);
    }
    return this.request<SavedViewRow>("POST", "/views", body);
  }

  /**
   * POST /views/{id}/data: resolve a saved view's CubeQueries into rows, run as
   * the verified tenant server-side (THE TRUST RULE — the client sends no
   * tenant_id and no query; the saved spec is the source of truth). Returns the
   * primary panel's `rows` plus the per-panel `panels` array.
   *
   * Mock builds have no live data plane, so they return an empty payload — the
   * caller's per-panel loader then renders the honest "No data yet" state, never
   * a canned number on a fixture-less view. (Mock SURFACES inject the offline
   * sampleLoadData fixture directly, bypassing this method; see the view files.)
   *
   * Real builds may answer 503 (cube not configured / warming), 404 (no such
   * view), or 502 (upstream Cube failure). These surface as ApiError for the
   * caller to map to a calm "data temporarily unavailable" empty state.
   */
  async loadViewData(viewId: string): Promise<ViewDataResponse> {
    if (this.mock) {
      return { rows: [], panels: [] };
    }
    return this.request<ViewDataResponse>(
      "POST",
      `/views/${encodeURIComponent(viewId)}/data`,
    );
  }

  /**
   * NL refine of a saved view ("ask for a chart"): the agent patches the
   * existing spec ("make it a line chart, last 90 days") and the new version is
   * persisted server-side. Returns the new SavedViewRow.
   *
   * The real route (POST /views/{id}/refine) answers 501 when the agent runtime
   * (view_patcher) isn't wired on this deployment — the caller renders that as
   * an honest "not live yet" state, never a hard error.
   */
  async refineView(viewId: string, body: RefineViewBody): Promise<SavedViewRow> {
    if (this.mock) {
      return (await this.mockApi()).refineView(viewId, body);
    }
    return this.request<SavedViewRow>(
      "POST",
      `/views/${encodeURIComponent(viewId)}/refine`,
      body,
    );
  }

  /** GET /dashboards: the tenant's named dashboards (kind=dashboard saved views). */
  async listDashboards(): Promise<SavedViewRow[]> {
    if (this.mock) {
      return (await this.mockApi()).listDashboards();
    }
    const data = await this.request<ListDashboardsResponse>("GET", "/dashboards");
    return data.dashboards;
  }

  /** GET /dashboards/{id}: the dashboard plus every referenced view, resolved. */
  async getDashboard(viewId: string): Promise<DashboardResolvedResponse> {
    if (this.mock) {
      return (await this.mockApi()).getDashboard(viewId);
    }
    return this.request<DashboardResolvedResponse>(
      "GET",
      `/dashboards/${encodeURIComponent(viewId)}`,
    );
  }

  /** POST /dashboards: save (create or version-bump) a kind=dashboard spec. */
  async saveDashboard(body: SaveViewBody): Promise<SavedViewRow> {
    if (this.mock) {
      return (await this.mockApi()).saveDashboard(body);
    }
    return this.request<SavedViewRow>("POST", "/dashboards", body);
  }

  async chat(message: string): Promise<ChatResponse> {
    if (this.mock) {
      return (await this.mockApi()).chat(message);
    }
    return this.request<ChatResponse>("POST", "/chat", { message });
  }

  /**
   * Balto: synthesize a NEW tenant view from an NL ask (POST /views/synthesize).
   * Status-keyed and honest — `data_not_found` when no Cube member can answer the ask
   * (never a hallucinated view), `exists` when a saved view already covers it. Nothing
   * is persisted by this call; `ok` returns an ephemeral draft.
   */
  async synthesizeView(body: SynthesizeViewBody): Promise<SynthesizeViewResponse> {
    if (this.mock) {
      return (await this.mockApi()).synthesizeView(body);
    }
    return this.request<SynthesizeViewResponse>("POST", "/views/synthesize", body);
  }

  /** Persist a Balto draft via the existing saved-view store (the explicit user save). */
  async saveViewDraft(draftId: string): Promise<SavedViewRow> {
    if (this.mock) {
      return (await this.mockApi()).saveViewDraft(draftId);
    }
    return this.request<SavedViewRow>(
      "POST",
      `/views/drafts/${encodeURIComponent(draftId)}/save`,
    );
  }

  async runAction(body: ActionBody): Promise<ActionResponse> {
    if (this.mock) {
      return (await this.mockApi()).runAction(body);
    }
    return this.request<ActionResponse>("POST", "/actions", body);
  }

  // --- deals / pipeline (authed) ----------------------------------------------

  /** GET /deals: the board — deals grouped into ordered stage columns. */
  async listDeals(): Promise<ListDealsResponse> {
    if (this.mock) {
      return (await this.mockApi()).listDeals();
    }
    return this.request<ListDealsResponse>("GET", "/deals");
  }

  /** GET /deals/{id}: one deal + its recent activities (the detail drawer). */
  async getDeal(dealId: string): Promise<DealDetailResponse> {
    if (this.mock) {
      return (await this.mockApi()).getDeal(dealId);
    }
    return this.request<DealDetailResponse>("GET", `/deals/${encodeURIComponent(dealId)}`);
  }

  /**
   * POST /deals/{id}/move-stage: proposes the move through Greenlight — the
   * deal is NOT moved. The body carries to_stage ONLY (no tenant_id — the
   * trust rule); a {queued: true} response means a human still has to approve,
   * so callers must keep rendering the CURRENT stage. Errors: 503 data plane
   * unconfigured, 404 no such deal, 409 same stage / move blocked, 422 empty
   * stage — surfaced as ApiError for the caller to map honestly.
   */
  async moveDealStage(dealId: string, body: MoveStageBody): Promise<MoveStageResponse> {
    if (this.mock) {
      return (await this.mockApi()).moveDealStage(dealId, body);
    }
    return this.request<MoveStageResponse>(
      "POST",
      `/deals/${encodeURIComponent(dealId)}/move-stage`,
      body,
    );
  }

  // --- contacts / companies directory (authed, read-only) ---------------------

  /** Build the directory query string. Values are URL-encoded; tenant_id can
   * never appear (the typed params shape has no such field). */
  private directoryQuery(params: DirectoryListParams = {}): string {
    const qs = new URLSearchParams();
    if (params.q !== undefined && params.q !== "") qs.set("q", params.q);
    if (params.limit !== undefined) qs.set("limit", String(params.limit));
    if (params.offset !== undefined) qs.set("offset", String(params.offset));
    const s = qs.toString();
    return s ? `?${s}` : "";
  }

  /** GET /contacts: the paginated, searchable contact directory. */
  async listContacts(params: DirectoryListParams = {}): Promise<ListContactsResponse> {
    if (this.mock) {
      return (await this.mockApi()).listContacts(params);
    }
    return this.request<ListContactsResponse>("GET", `/contacts${this.directoryQuery(params)}`);
  }

  /** GET /contacts/{id}: one contact + activities + the company's open deals. */
  async getContact(contactId: string): Promise<ContactDetailResponse> {
    if (this.mock) {
      return (await this.mockApi()).getContact(contactId);
    }
    return this.request<ContactDetailResponse>(
      "GET",
      `/contacts/${encodeURIComponent(contactId)}`,
    );
  }

  /** GET /companies: the company directory with contact + open-deal counts. */
  async listCompanies(params: DirectoryListParams = {}): Promise<ListCompaniesResponse> {
    if (this.mock) {
      return (await this.mockApi()).listCompanies(params);
    }
    return this.request<ListCompaniesResponse>(
      "GET",
      `/companies${this.directoryQuery(params)}`,
    );
  }

  /** GET /companies/{id}: one company + its contacts + its open deals. */
  async getCompany(companyId: string): Promise<CompanyDetailResponse> {
    if (this.mock) {
      return (await this.mockApi()).getCompany(companyId);
    }
    return this.request<CompanyDetailResponse>(
      "GET",
      `/companies/${encodeURIComponent(companyId)}`,
    );
  }

  /**
   * POST /contacts: create a contact in the tenant's CRM. Body carries name and
   * optional fields ONLY (no tenant_id — the trust rule). Returns 201 + the
   * created contact row. Errors: 503 data plane unconfigured, 422 validation.
   *
   * Mock builds return a canned shape (offline/prototype: no persistence, just
   * confirms the wire contract so forms can render a success state in Playwright).
   */
  async createContact(body: CreateContactBody): Promise<CreateContactResponse> {
    if (this.mock) {
      return {
        contact: {
          id: `mock-contact-${Date.now()}`,
          name: body.name,
          email: body.email ?? null,
          phone: body.phone ?? null,
        },
      };
    }
    return this.request<CreateContactResponse>("POST", "/contacts", body);
  }

  /**
   * PATCH /contacts/{id}: edit name/company/email/phone. Body carries only
   * the fields to change (no tenant_id — the trust rule). Returns the update
   * result. Errors: 503 unconfigured, 404 no such contact (cross-tenant or
   * missing — indistinguishable by design), 422 validation.
   */
  async updateContact(contactId: string, body: EditContactBody): Promise<EditContactResponse> {
    if (this.mock) {
      return { id: contactId, updated: body as Record<string, unknown> };
    }
    return this.request<EditContactResponse>(
      "PATCH",
      `/contacts/${encodeURIComponent(contactId)}`,
      body,
    );
  }

  /**
   * POST /deals: create a deal in the tenant's pipeline. Body carries title and
   * optional fields ONLY (no tenant_id — the trust rule). Returns 201 + the
   * created deal row. Errors: 503 data plane unconfigured, 422 validation.
   *
   * Mock builds return a canned shape (offline/prototype — no persistence).
   */
  async createDeal(body: CreateDealBody): Promise<CreateDealResponse> {
    if (this.mock) {
      return {
        deal: {
          id: `mock-deal-${Date.now()}`,
          name: body.title,
          stage: body.stage ?? "new",
          amount: body.amount ?? null,
        },
      };
    }
    return this.request<CreateDealResponse>("POST", "/deals", body);
  }

  /**
   * PATCH /deals/{id}: edit title/amount. Body carries only the fields to
   * change (no tenant_id — the trust rule). Errors: 503 unconfigured, 404 no
   * such deal (cross-tenant or missing — indistinguishable by design), 422 validation.
   */
  async updateDeal(dealId: string, body: EditDealBody): Promise<EditDealResponse> {
    if (this.mock) {
      return { id: dealId, updated: body as Record<string, unknown> };
    }
    return this.request<EditDealResponse>(
      "PATCH",
      `/deals/${encodeURIComponent(dealId)}`,
      body,
    );
  }

  // --- agent crew (authed, read-only) ------------------------------------------

  /** GET /agents: the tenant's crew — the 7 specialists + coordinator from the
   * owned roster, each tool carrying its trusted policy (auto vs always_ask),
   * plus the provisioned MA id tails (truncated; the full ids never arrive). */
  async getAgentCrew(): Promise<AgentCrewResponse> {
    if (this.mock) {
      return (await this.mockApi()).getAgentCrew();
    }
    return this.request<AgentCrewResponse>("GET", "/agents");
  }

  // --- workflows (authed, read-only) -------------------------------------------

  /** GET /workflows: the provisioning machine — the OWNED 5-step diagram plus
   * recent executions (name/status/timestamps only) when the read grant exists;
   * an honest {executions_available: false, reason} degrade otherwise. */
  async getWorkflows(): Promise<WorkflowsResponse> {
    if (this.mock) {
      return (await this.mockApi()).getWorkflows();
    }
    return this.request<WorkflowsResponse>("GET", "/workflows");
  }

  // --- knowledge (authed, read-only) -------------------------------------------

  /** GET /knowledge: the tenant's knowledge-base inventory — per-source document
   * counts + newest-ingested timestamp + totals. A plain aggregate (no embedder),
   * so it's honest the moment the data plane is wired; an un-ingested tenant gets
   * zeros. */
  async getKnowledge(): Promise<KnowledgeInventoryResponse> {
    if (this.mock) {
      return (await this.mockApi()).getKnowledge();
    }
    return this.request<KnowledgeInventoryResponse>("GET", "/knowledge");
  }

  /** GET /knowledge/search?q=: cosine search over the tenant's corpus (ref_id +
   * source + snippet + score). Degrades honestly to {search_available: false,
   * reason} when the Titan query embedder isn't reachable (env-key-gated). */
  async searchKnowledge(query: string, limit?: number): Promise<KnowledgeSearchResponse> {
    if (this.mock) {
      return (await this.mockApi()).searchKnowledge(query, limit);
    }
    const params = new URLSearchParams({ q: query });
    if (limit !== undefined) params.set("limit", String(limit));
    return this.request<KnowledgeSearchResponse>("GET", `/knowledge/search?${params.toString()}`);
  }

  /** POST /knowledge/documents: add one document (paste) to the tenant's corpus —
   * chunked + embedded server-side under the verified tenant. A 503 means uploads
   * aren't switched on for this deployment (the ingest plane's INGEST_REAL_STORES gate). */
  async addKnowledgeDocument(title: string, content: string): Promise<KnowledgeAddDocumentResponse> {
    if (this.mock) {
      return (await this.mockApi()).addKnowledgeDocument(title, content);
    }
    return this.request<KnowledgeAddDocumentResponse>("POST", "/knowledge/documents", {
      title,
      content,
    });
  }

  // --- integrations (authed) -------------------------------------------------

  /** GET /integrations: known connectors + this tenant's connection status. */
  async listIntegrations(): Promise<ListIntegrationsResponse> {
    if (this.mock) {
      return (await this.mockApi()).listIntegrations();
    }
    return this.request<ListIntegrationsResponse>("GET", "/integrations");
  }

  /**
   * POST /integrations/{name}/credentials: vault the tenant's token. The body
   * carries the token ONLY (no tenant_id — the trust rule); the response never
   * echoes it. Errors: 503 storage unconfigured, 422 empty token, 502 vault
   * write failed — surfaced as ApiError for the caller to map honestly.
   */
  async storeIntegrationCredentials(
    name: string,
    body: IntegrationCredentialsBody,
  ): Promise<StoreCredentialsResponse> {
    if (this.mock) {
      return (await this.mockApi()).storeIntegrationCredentials(name, body);
    }
    return this.request<StoreCredentialsResponse>(
      "POST",
      `/integrations/${encodeURIComponent(name)}/credentials`,
      body,
    );
  }

  /**
   * DELETE /integrations/{name}/credentials: disconnect — remove this tenant's
   * vault slot. Idempotent (deleted:false when nothing was vaulted). Errors:
   * 503 storage unconfigured, 409 file-kind, 502 vault delete failed.
   */
  async deleteIntegrationCredentials(name: string): Promise<DeleteCredentialsResponse> {
    if (this.mock) {
      return (await this.mockApi()).deleteIntegrationCredentials(name);
    }
    return this.request<DeleteCredentialsResponse>(
      "DELETE",
      `/integrations/${encodeURIComponent(name)}/credentials`,
    );
  }

  /**
   * POST /integrations/{name}/sync: kick one incremental sync for THIS tenant
   * (server derives the tenant from the verified claim). A run-store deployment
   * answers 202 {run} (poll the history); a storeless one answers 200 {result}.
   * Errors: 503 sync unconfigured, 409 connect first OR a sync already running,
   * 502 sync failed.
   */
  async kickIntegrationSync(name: string): Promise<IntegrationSyncResponse> {
    if (this.mock) {
      return (await this.mockApi()).kickIntegrationSync(name);
    }
    return this.request<IntegrationSyncResponse>(
      "POST",
      `/integrations/${encodeURIComponent(name)}/sync`,
    );
  }

  /**
   * GET /integrations/{name}/syncs: recent sync-run history, newest first.
   * Errors: 503 no run store wired, 409 file-kind, 502 read failed.
   */
  async listIntegrationSyncs(name: string): Promise<IntegrationSyncHistoryResponse> {
    if (this.mock) {
      return (await this.mockApi()).listIntegrationSyncs(name);
    }
    return this.request<IntegrationSyncHistoryResponse>(
      "GET",
      `/integrations/${encodeURIComponent(name)}/syncs`,
    );
  }

  // --- signup funnel (public, pre-auth) -------------------------------------
  //
  // None of these attach a bearer token (the account has no tenant yet) and none
  // send a tenant_id. The mock walks the state machine forward deterministically
  // so Playwright can drive the whole funnel offline.

  /** POST /signup: create the pending account from {email, phone}. */
  async signup(body: SignupBody): Promise<SignupResponse> {
    if (this.mock) {
      return (await this.mockApi()).signup();
    }
    // Pre-auth: send without a bearer token. Body carries email/phone only.
    return this.requestPublic<SignupResponse>("POST", "/signup", body);
  }

  /** POST /signup/{id}/verify-email: confirm the email token. */
  async verifyEmail(accountId: string, body: VerifyEmailBody): Promise<VerifyEmailResponse> {
    if (this.mock) {
      return (await this.mockApi()).verifyEmail(accountId);
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
      return (await this.mockApi()).verifyPhone(accountId);
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
      return (await this.mockApi()).checkout(accountId);
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
      return (await this.mockApi()).getSignup(accountId);
    }
    return this.requestPublic<GetSignupResponse>(
      "GET",
      `/signup/${encodeURIComponent(accountId)}`,
    );
  }

  // --- public lead capture (pre-auth) ---------------------------------------

  /**
   * POST /public/leads: capture a "Book a call" / "Email us" lead. Public, so
   * it sends no bearer token and no tenant_id. Throws ApiError on non-2xx (incl.
   * 404 when the route isn't deployed yet) so the caller can fall back to a
   * mailto: link and keep the user-visible confirmation honest.
   */
  async submitLead(body: LeadBody): Promise<LeadResponse> {
    if (this.mock) {
      // Offline/test builds: acknowledge without a network call so the funnel
      // is exercisable without a backend.
      return { ok: true };
    }
    return this.requestPublic<LeadResponse>("POST", "/public/leads", body);
  }

  // --- control plane: kill switch / autonomy / traces (authed) ---------------
  //
  // Each may throw ApiError(404) where the control plane isn't deployed yet;
  // the Security surface feature-detects that and degrades to a disabled
  // control instead of a fake working toggle.

  /** GET /control/killswitch: the master stop state. */
  async getKillswitch(): Promise<KillswitchState> {
    if (this.mock) {
      return (await this.mockApi()).getKillswitch();
    }
    return this.request<KillswitchState>("GET", "/control/killswitch");
  }

  /** PUT /control/killswitch: engage/disengage the master stop. */
  async setKillswitch(engaged: boolean): Promise<KillswitchState> {
    if (this.mock) {
      return (await this.mockApi()).setKillswitch(engaged);
    }
    return this.request<KillswitchState>("PUT", "/control/killswitch", {
      engaged,
      scope: "global",
    });
  }

  /** GET /control/autonomy: the workspace autonomy level (0–3). */
  async getAutonomy(): Promise<AutonomyState> {
    if (this.mock) {
      return (await this.mockApi()).getAutonomy();
    }
    return this.request<AutonomyState>("GET", "/control/autonomy");
  }

  /** PUT /control/autonomy: set the workspace autonomy level (0–3). */
  async setAutonomy(level: AutonomyLevel): Promise<AutonomyState> {
    if (this.mock) {
      return (await this.mockApi()).setAutonomy(level);
    }
    return this.request<AutonomyState>("PUT", "/control/autonomy", { level });
  }

  /** GET /control/traces?limit=: the recent decision-trace feed (read-only). */
  async getControlTraces(limit = 50): Promise<DecisionTrace[]> {
    if (this.mock) {
      return (await this.mockApi()).getControlTraces(limit);
    }
    const data = await this.request<DecisionTracesResponse>(
      "GET",
      `/control/traces?limit=${encodeURIComponent(String(limit))}`,
    );
    return data.traces;
  }

  /** GET /onboarding: the calling tenant's first-run state (checklist + flags). */
  async getOnboarding(): Promise<OnboardingState> {
    if (this.mock) {
      return (await this.mockApi()).getOnboarding();
    }
    return this.request<OnboardingState>("GET", "/onboarding");
  }

  /** PUT /onboarding: persist a partial first-run update (step done / dismiss). */
  async putOnboarding(body: OnboardingPutBody): Promise<OnboardingState> {
    if (this.mock) {
      return (await this.mockApi()).putOnboarding(body);
    }
    return this.request<OnboardingState>("PUT", "/onboarding", body);
  }

  /** POST /onboarding/load-sample: one-click, idempotent demo-fixture load into
   * the calling tenant. Reports the loaded row counts + the updated state. */
  async loadSampleData(): Promise<LoadSampleResponse> {
    if (this.mock) {
      return (await this.mockApi()).loadSampleData();
    }
    return this.request<LoadSampleResponse>("POST", "/onboarding/load-sample");
  }

  // --- self-service billing (authed, Stripe Customer Portal) -----------------

  /**
   * GET /billing: whether this tenant has a Stripe customer + its plan + billing
   * status. May throw ApiError(404) where the billing routes aren't deployed yet;
   * the settings screen feature-detects that and hides the live "Manage billing"
   * control rather than faking one.
   */
  async getBillingState(): Promise<BillingState> {
    if (this.mock) {
      // Offline/prototype builds: a stable "on a plan" shape so the settings
      // screen renders without a backend. No network call.
      return { customer: true, plan: "team", status: "active" };
    }
    return this.request<BillingState>("GET", "/billing");
  }

  /**
   * POST /billing/portal-session: mint a Stripe Customer Portal session and get
   * the URL to send the browser to (window.location.assign). The server resolves
   * the customer from the verified JWT tenant — the client sends no tenant_id and
   * no customer id. Throws ApiError(403) when no customer mapping exists yet, 503
   * when Stripe isn't configured; the caller maps those to honest copy.
   */
  async createBillingPortalSession(): Promise<BillingPortalSessionResponse> {
    if (this.mock) {
      // No real portal offline — the prototype button just no-ops to a notice.
      return { url: "" };
    }
    return this.request<BillingPortalSessionResponse>("POST", "/billing/portal-session");
  }

  /**
   * GET /billing/invoices: the tenant's real Stripe invoices (customer resolved
   * server-side from the verified JWT — the client sends no customer id). Returns
   * [] when the tenant has no Stripe customer yet; may throw ApiError(404) where
   * billing isn't deployed — callers show honest empty/degraded copy, never fakes.
   */
  async listInvoices(): Promise<Invoice[]> {
    if (this.mock) {
      // Offline/demo builds render the mock billing store, not this path.
      return [];
    }
    const data = await this.request<{ invoices: Invoice[] }>("GET", "/billing/invoices");
    return data.invoices ?? [];
  }

  // --- Cortex (ML) health ----------------------------------------------------

  /**
   * GET /cortex/health: real per-tenant model health — champion + drift verdict,
   * with NO fabricated numbers. status "no_registry"/"no_champion" are honest
   * empty states (the registry isn't wired / no model trained yet) the UI renders
   * as degraded, not as green. May throw ApiError(404) where Cortex isn't deployed.
   */
  async getCortexHealth(): Promise<CortexHealth> {
    if (this.mock) {
      // Demo builds: an honest "not wired" shape — no invented accuracy/drift.
      return { tenant_id: "demo", status: "no_registry", champion: null, model_count: 0, drift: null };
    }
    return this.request<CortexHealth>("GET", "/cortex/health");
  }

  // --- CSV import (file upload) ----------------------------------------------

  /**
   * POST /integrations/csv/import: upload a CSV for `entity` (contacts|companies|
   * deals) as multipart. The server parses + lands rows; per-row problems come
   * back in the report's `errors` (never throw), a whole-file problem is an
   * ApiError(422), and an unconfigured ingest plane is an honest ApiError(503).
   */
  async csvImport(entity: string, file: File): Promise<CsvImportReport> {
    if (this.mock) {
      // No real ingest offline — report nothing imported (the UI shows the honest
      // "demo mode" notice rather than pretending rows landed).
      return {
        entity, mapping: {}, total_rows: 0, imported: 0,
        rows_upserted: 0, embedded: 0, skipped_unchanged: 0, errors: [],
      };
    }
    const form = new FormData();
    form.append("entity", entity);
    form.append("file", file);
    // The endpoint wraps the report as {name, report} — unwrap to the report.
    const res = await this.requestMultipart<CsvImportResponse>(
      "POST", "/integrations/csv/import", form,
    );
    return res.report;
  }

  // --- Account data lifecycle (GDPR) -----------------------------------------

  /**
   * GET /account/export: the tenant's full RLS-scoped data bundle for download.
   * Throws ApiError(503) when all stores are unconfigured (nothing to export).
   */
  async exportAccountData(): Promise<AccountExport> {
    if (this.mock) {
      return {};
    }
    return this.request<AccountExport>("GET", "/account/export");
  }

  /**
   * POST /account/delete: teardown of the tenant's own mutable data. Requires a
   * confirm token equal to the tenant id (422 otherwise). Append-only audit tables
   * are reported under `retained`, never force-deleted. ApiError(503) when the
   * destructive path isn't wired live (the default) — a caller must not pretend.
   */
  async requestAccountDelete(confirm: string): Promise<AccountDeleteReport> {
    if (this.mock) {
      return { deleted: {}, retained: {}, failed: {} };
    }
    return this.request<AccountDeleteReport>("POST", "/account/delete", { confirm });
  }

  // --- persisted workspace settings (GET/PUT /account/settings) ---------------

  /**
   * GET /account/settings: the tenant's persisted workspace name + notification
   * prefs (server derives the tenant from the verified claim). A tenant that has
   * never saved gets the empty default shape ({workspace_name: null, prefs: {}}),
   * never a 404. ApiError(503) where the settings store isn't wired.
   */
  async getSettings(): Promise<WorkspaceSettings> {
    if (this.mock) {
      return { workspace_name: "Acme Co.", notification_prefs: {} };
    }
    return this.request<WorkspaceSettings>("GET", "/account/settings");
  }

  /**
   * PUT /account/settings: persist a partial update (only the fields present are
   * written; the rest are left untouched). Returns the full saved row. The client
   * never sends a tenant_id (the trust rule). 422 on invalid name/prefs.
   */
  async putSettings(body: WorkspaceSettingsUpdate): Promise<WorkspaceSettings> {
    if (this.mock) {
      return {
        workspace_name: body.workspace_name ?? "Acme Co.",
        notification_prefs: body.notification_prefs ?? {},
      };
    }
    return this.request<WorkspaceSettings>("PUT", "/account/settings", body);
  }

  // --- module entitlements (the "your suite" surface, GET/PUT /account/modules) ----

  /**
   * GET /account/modules: the module catalog + this tenant's enabled set + monthly total +
   * the enabled route-ids the app gates its nav/routes against. In mock mode every module is
   * enabled (the demo shows the full suite).
   */
  async getModules(): Promise<ModuleCatalog> {
    if (this.mock) {
      return { modules: [], monthly_total_cents: 0, enabled_routes: [] };
    }
    return this.request<ModuleCatalog>("GET", "/account/modules");
  }

  /**
   * PUT /account/modules: set the enabled module ids (the Settings "your suite" toggles).
   * Required modules are forced on server-side; unknown ids dropped. Returns the saved catalog.
   */
  async putModules(enabled: string[]): Promise<ModuleCatalog> {
    if (this.mock) {
      return { modules: [], monthly_total_cents: 0, enabled_routes: [] };
    }
    return this.request<ModuleCatalog>("PUT", "/account/modules", { enabled });
  }

  // --- sidecar (the agentic layer: grounded next-action suggestions) ----------

  /**
   * GET /sidecar/suggestions: grounded next-action suggestions over the tenant's CRM (aging open
   * deals, unreachable contacts, etc.). Mock mode returns an empty set (no fabricated suggestions).
   */
  async getSidecarSuggestions(): Promise<SidecarSuggestions> {
    if (this.mock) {
      return { suggestions: [], total: 0, truncated: false };
    }
    return this.request<SidecarSuggestions>("GET", "/sidecar/suggestions");
  }

  /**
   * POST /sidecar/act: accept a suggestion by id. The server resolves the suggestion's predefined
   * action and enqueues a DRAFT into Greenlight (Sidecar never writes the CRM directly). Returns the
   * created approval id + the action name. 409 when the suggestion no longer applies.
   */
  async actOnSidecarSuggestion(id: string): Promise<SidecarActResult> {
    if (this.mock) {
      return { status: "queued", approval_id: null, suggestion_id: id, action: "create_activity" };
    }
    return this.request<SidecarActResult>("POST", "/sidecar/act", { id });
  }

  // --- agent marketplace (starter templates) ---------------------------------

  /**
   * GET /studio/templates: the committed starter "ready-made agents" a tenant can
   * add to its library. Read-only; nothing is invented client-side.
   */
  async getStudioTemplates(): Promise<StudioTemplateSummary[]> {
    if (this.mock) {
      return [];
    }
    const data = await this.request<{ templates: StudioTemplateSummary[] }>("GET", "/studio/templates");
    return data.templates ?? [];
  }

  /**
   * POST /studio/templates/{id}/instantiate: copy a starter template into the
   * tenant's playbooks as a draft ("hire" it). Returns the created row (opaque here).
   */
  async instantiateTemplate(templateId: string): Promise<unknown> {
    if (this.mock) {
      return { ok: true };
    }
    return this.request<unknown>(
      "POST",
      `/studio/templates/${encodeURIComponent(templateId)}/instantiate`,
    );
  }
}

// ---------------------------------------------------------------------------
// View-data loader: turn a /views/{id}/data payload into the SpecRenderer's
// injected loadData(query) prop.
//
// The renderer pulls per-panel: it calls loadData(query) once per data-bearing
// block, NOT knowing the panel's layout index. The server resolves rows BY
// layout index (panels[].panel). So we re-derive each layout block's query the
// same way the server does (mirror of api/cube_data_routes.py `_panel_query`)
// and the SAME way the renderer will call it, then key the panel rows by a
// canonical query signature. An incoming query resolves to its panel's rows;
// anything unmatched (e.g. the stat headline, whose panel query is the trend)
// falls back to the primary `rows`, then to [] — an honest "No data yet", never
// a fabricated number and never a crash.
// ---------------------------------------------------------------------------

/** Stable, order-insensitive signature for a CubeQuery so the renderer's call
 * and the server's resolved panel query match regardless of key ordering. */
function querySignature(query: CubeQuery): string {
  const norm = (arr?: string[]) => [...(arr ?? [])].sort();
  return JSON.stringify({
    measures: norm(query.measures),
    dimensions: norm(query.dimensions),
    // timeDimensions/filters carry through verbatim (already deterministic
    // enough for a same-spec round-trip; they originate from the saved spec).
    timeDimensions: query.timeDimensions ?? [],
    filters: query.filters ?? [],
  });
}

/** Re-derive a layout block's row-bearing CubeQuery, mirroring the server's
 * `_panel_query`. Returns null for panels that carry no data query (markdown). */
function blockQuery(block: Record<string, unknown>): CubeQuery | null {
  const type = block.type;
  if (
    type === "chart" ||
    type === "table" ||
    type === "funnel" ||
    type === "leaderboard" ||
    type === "cohort-grid"
  ) {
    const q = block.query;
    return q && typeof q === "object" ? (q as CubeQuery) : null;
  }
  if (type === "kpi") {
    const metric = block.metric;
    if (typeof metric !== "string") return null;
    const query: CubeQuery = { measures: [metric] };
    const flt = block.filter as Record<string, unknown> | undefined;
    if (flt && typeof flt === "object") {
      if (Array.isArray(flt.filters)) query.filters = flt.filters as CubeQuery["filters"];
      if (Array.isArray(flt.timeDimensions))
        query.timeDimensions = flt.timeDimensions as CubeQuery["timeDimensions"];
      if (Array.isArray(flt.dimensions)) query.dimensions = flt.dimensions as string[];
    }
    return query;
  }
  if (type === "stat-with-sparkline") {
    const trend = block.trend;
    return trend && typeof trend === "object" ? (trend as CubeQuery) : null;
  }
  return null;
}

/**
 * Build the SpecRenderer `loadData(query)` prop from a resolved /views/{id}/data
 * payload + the view's own spec. Each incoming query resolves to its panel's
 * rows; unmatched queries fall back to the primary rows, then to []. Pure and
 * synchronous-resolving (returns a Promise to satisfy the LoadData contract).
 */
export function buildViewDataLoader(
  spec: Record<string, unknown> | null | undefined,
  data: ViewDataResponse,
): LoadData {
  const bySignature = new Map<string, DataRow[]>();
  const layout = (spec?.layout as unknown[]) ?? [];
  const rowsForPanel = new Map<number, DataRow[]>();
  for (const p of data.panels ?? []) rowsForPanel.set(p.panel, p.rows ?? []);

  layout.forEach((block, i) => {
    if (!block || typeof block !== "object") return;
    const q = blockQuery(block as Record<string, unknown>);
    if (q === null) return;
    const rows = rowsForPanel.get(i);
    if (rows !== undefined) bySignature.set(querySignature(q), rows);
  });

  return async (query: CubeQuery): Promise<DataRow[]> => {
    const hit = bySignature.get(querySignature(query));
    if (hit !== undefined) return hit;
    return data.rows ?? [];
  };
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
