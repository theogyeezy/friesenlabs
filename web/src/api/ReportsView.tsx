// Reports view, wired to the control-plane API via ApiClient — the real-mode
// counterpart of the FLStore Reports prototype + DataAssistant overlay (mock
// mode only). Follows the DashboardView/WorkflowsView conventions exactly, and
// REUSES the trusted dashboard spec engine rather than reinventing a renderer:
//
//   * The gallery lists the tenant's saved views straight from GET /views
//     (RLS-scoped, read). Each card shows the view's title, the natural-language
//     prompt that produced it, and its version. No demo views are ever shown in
//     real mode — a fresh tenant sees the honest empty state.
//   * Opening a view loads it via GET /views/{id} and renders it through the
//     SAME SpecRenderer the dashboard uses — the closed catalog (kpi / chart /
//     table), re-validated before drawing, never code-from-spec. Data is
//     injected the same way DashboardView injects it: real builds resolve every
//     query to zero rows (the Cube data plane isn't deployed yet) so each block
//     honestly renders "No data yet"; mock builds use the offline fixture.
//   * "Ask for a chart" is the NL refine flow over the open view, wired to the
//     EXISTING POST /views/{id}/refine route ("make it a line chart, last 90
//     days"). The agent (AnthropicViewPatcher) patches the spec server-side, the
//     new version is persisted, and we re-render it. The composer is enabled
//     optimistically — the route is the feature-detect signal: when the agent
//     runtime (view_patcher) IS wired (the org Anthropic key is present) refine
//     succeeds and we re-render the new version; when it ISN'T the route answers
//     501 and we degrade to the calm "AI chart authoring isn't live yet" state,
//     exactly like chat's 503 — NEVER a hard error, and ONLY on 501 (a 422 stays
//     an inline "try rephrasing", any other failure a friendly retry). (Generating
//     a brand-new chart from scratch is the same agent capability; today it rides
//     the per-view refine — there is no generate-from-nothing route, and we don't
//     invent one.)
//   * Every transport failure routes through friendlyErrorMessage: raw
//     "API <code>" strings and server detail dumps never reach the DOM.

import React from "react";
import {
  ApiClient,
  ApiError,
  buildViewDataLoader,
  defaultClient,
  friendlyErrorMessage,
  type SavedViewRow,
} from "./client";
import { SpecRenderer, type LoadData } from "../dashboard/SpecRenderer";
import { Spinner } from "./Spinner";

const { useState, useEffect, useCallback } = React;

// ---------------------------------------------------------------------------
// Data loaders — identical policy to DashboardView. The renderer never fetches
// on its own; it pulls only through this injected loader.
// ---------------------------------------------------------------------------

// Empty real-mode loader: the calm fallback when the live data plane is
// unavailable (503/error) — resolve every query to zero rows so each block
// honestly renders "No data yet". Canned demo numbers on a real tenant's report
// would be a lie, so we never swap in the fixture here.
const noLiveData: LoadData = async () => [];

// Mock build: the offline fixture, behind a BUILD-TIME gate (Vite folds the
// branch away in real bundles, so the fixture never ships to production).
let mockLoadData: LoadData = noLiveData;
if (import.meta.env.VITE_API_MOCK !== "0" && import.meta.env.VITE_API_MOCK !== "false") {
  mockLoadData = async (query) => (await import("../dashboard/sample")).sampleLoadData(query);
}

// ---------------------------------------------------------------------------
// Styles (house style: hairline cards on the soft surface palette)
// ---------------------------------------------------------------------------

const card: React.CSSProperties = {
  border: "1px solid var(--line, #e3ddd3)",
  background: "var(--surface, #fff)",
  borderRadius: 14,
  padding: "18px 20px",
};

const ghostBtn: React.CSSProperties = {
  padding: "8px 16px",
  borderRadius: 10,
  border: "1px solid var(--line, #e3ddd3)",
  background: "transparent",
  color: "var(--ink, #2a2622)",
  fontSize: 13.5,
  fontWeight: 650,
  cursor: "pointer",
};

const muted: React.CSSProperties = { color: "var(--ink-3, #8a8278)" };

function specTitle(row: SavedViewRow): string {
  const t = (row.spec_json as Record<string, unknown>)?.title;
  return typeof t === "string" && t.trim() ? t : row.view_id;
}

// ---------------------------------------------------------------------------
// Error / empty primitives (shared shape with the other API-wired surfaces)
// ---------------------------------------------------------------------------

function ErrorCard({
  message,
  onRetry,
  testid,
  retryTestid,
}: {
  message: string;
  onRetry?: () => void;
  testid: string;
  retryTestid: string;
}) {
  return (
    <div
      data-testid={testid}
      style={{ ...card, border: "1px solid var(--rose, #b4413b)", fontSize: 13.5 }}
    >
      <div style={{ fontWeight: 700, marginBottom: 4 }}>Something needs another try</div>
      <p style={{ ...muted, lineHeight: 1.5 }}>{message}</p>
      {onRetry && (
        <button data-testid={retryTestid} onClick={onRetry} style={{ ...ghostBtn, marginTop: 10 }}>
          Try again
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Gallery — the saved-views grid
// ---------------------------------------------------------------------------

function Gallery({
  views,
  onOpen,
}: {
  views: SavedViewRow[];
  onOpen: (viewId: string) => void;
}) {
  return (
    <div
      data-testid="reports-gallery"
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
        gap: 14,
      }}
    >
      {views.map((v) => (
        <button
          key={v.view_id}
          data-testid="report-card"
          data-view-id={v.view_id}
          onClick={() => onOpen(v.view_id)}
          style={{ ...card, textAlign: "left", cursor: "pointer", display: "block" }}
        >
          <div style={{ fontSize: 15, fontWeight: 720, color: "var(--ink, #2a2622)" }}>
            {specTitle(v)}
          </div>
          {v.source_prompt && (
            <p style={{ ...muted, fontSize: 12.5, marginTop: 6, lineHeight: 1.45 }}>
              &ldquo;{v.source_prompt}&rdquo;
            </p>
          )}
          <div style={{ ...muted, fontSize: 11.5, marginTop: 10 }}>version {v.version}</div>
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Detail — render one view's spec + the "ask for a chart" refine composer
// ---------------------------------------------------------------------------

function Detail({
  api,
  viewId,
  onBack,
}: {
  api: ApiClient;
  viewId: string;
  onBack: () => void;
}) {
  const [spec, setSpec] = useState<Record<string, unknown> | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  // Injected data loader for the open report (mock fixture, or a real loader
  // built from POST /views/{id}/data with the empty fallback on data-plane loss).
  const [dataLoader, setDataLoader] = useState<LoadData>(() =>
    api.isMock() ? mockLoadData : noLiveData,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // "Ask for a chart" composer state.
  const [instruction, setInstruction] = useState("");
  const [refining, setRefining] = useState(false);
  const [refineError, setRefineError] = useState<string | null>(null);
  // True once the route has answered 501: the agent runtime isn't wired here.
  const [refineUnavailable, setRefineUnavailable] = useState(false);
  const [refineNote, setRefineNote] = useState<string | null>(null);

  // Resolve the spec's CubeQueries into a real-mode loader. A data-plane failure
  // (503/404/502/network) is never fatal to the report: fall back to the empty
  // loader so each panel shows its calm "No data yet" state. Mock builds keep
  // the offline fixture loader and skip the network entirely.
  const loadData = useCallback(
    async (loadedSpec: Record<string, unknown>) => {
      if (api.isMock()) return;
      try {
        const data = await api.loadViewData(viewId);
        setDataLoader(() => buildViewDataLoader(loadedSpec, data));
      } catch {
        setDataLoader(() => noLiveData);
      }
    },
    [api, viewId],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const row = await api.getView(viewId);
      setSpec(row.spec_json);
      setVersion(row.version);
      await loadData(row.spec_json);
    } catch (e) {
      setError(friendlyErrorMessage(e, "Couldn't load this report. Please try again."));
    } finally {
      setLoading(false);
    }
  }, [api, viewId, loadData]);

  useEffect(() => {
    void load();
  }, [load]);

  const refine = useCallback(async () => {
    const text = instruction.trim();
    if (!text) return;
    setRefining(true);
    setRefineError(null);
    setRefineNote(null);
    try {
      const row = await api.refineView(viewId, { instruction: text });
      setSpec(row.spec_json);
      setVersion(row.version);
      // The refined spec has new/changed panels — re-resolve its data so the
      // new version renders real rows, not the previous version's loader.
      await loadData(row.spec_json);
      setInstruction("");
      setRefineNote(`Updated — version ${row.version}`);
      window.setTimeout(() => setRefineNote(null), 2500);
    } catch (e) {
      if (e instanceof ApiError && e.status === 501) {
        // Agent runtime (view_patcher) not wired on this deployment — honest,
        // not an error. Same posture as chat's 503.
        setRefineUnavailable(true);
      } else if (e instanceof ApiError && e.status === 422) {
        setRefineError("That request didn't produce a valid chart. Try rephrasing it.");
      } else {
        setRefineError(friendlyErrorMessage(e, "Couldn't update this report. Please try again."));
      }
    } finally {
      setRefining(false);
    }
  }, [api, viewId, instruction, loadData]);

  return (
    <div data-testid="report-detail">
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
        <button data-testid="report-back" onClick={onBack} style={ghostBtn}>
          &larr; All reports
        </button>
        {version !== null && (
          <span data-testid="report-version" style={{ ...muted, fontSize: 12 }}>
            version {version}
          </span>
        )}
      </div>

      {loading && <Spinner testid="report-loading" label="Loading report..." />}

      {!loading && error && (
        <ErrorCard
          message={error}
          onRetry={() => void load()}
          testid="report-error"
          retryTestid="report-retry"
        />
      )}

      {!loading && !error && spec && (
        <>
          <SpecRenderer spec={spec} loadData={dataLoader} />

          {/* "Ask for a chart" — NL refine over the open view. */}
          <div data-testid="refine-composer" style={{ ...card, marginTop: 18 }}>
            <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 4 }}>Ask for a chart</div>
            <p style={{ ...muted, fontSize: 12.5, lineHeight: 1.5, marginBottom: 10 }}>
              Describe the change in plain English — e.g. &ldquo;make it a line chart for the
              last 90 days&rdquo; or &ldquo;break it down by owner.&rdquo; Your agents rebuild the
              view and save the new version.
            </p>

            {refineUnavailable ? (
              <div data-testid="refine-unavailable" style={{ ...muted, fontSize: 13, lineHeight: 1.5 }}>
                AI chart authoring isn&rsquo;t live yet on this workspace. Your saved reports
                render here today; ask-for-a-chart turns on with the agent runtime.
              </div>
            ) : (
              <>
                <textarea
                  data-testid="refine-input"
                  value={instruction}
                  onChange={(e) => setInstruction(e.target.value)}
                  rows={2}
                  placeholder="Make it a line chart for the last 90 days"
                  style={{
                    width: "100%",
                    boxSizing: "border-box",
                    padding: "10px 12px",
                    borderRadius: 10,
                    border: "1px solid var(--line, #e3ddd3)",
                    background: "var(--bg, #fff)",
                    color: "var(--ink, #2a2622)",
                    fontSize: 13.5,
                    fontFamily: "inherit",
                    resize: "vertical",
                  }}
                />
                <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 10 }}>
                  <button
                    data-testid="refine-submit"
                    onClick={() => void refine()}
                    disabled={refining || !instruction.trim()}
                    style={{
                      ...ghostBtn,
                      background: "var(--accent, #4f46e5)",
                      color: "#fff",
                      border: "1px solid transparent",
                      opacity: refining || !instruction.trim() ? 0.6 : 1,
                      cursor: refining || !instruction.trim() ? "default" : "pointer",
                    }}
                  >
                    {refining ? "Working..." : "Ask"}
                  </button>
                  {refineNote && (
                    <span data-testid="refine-note" style={{ fontSize: 12, color: "var(--green, #2f8a4f)" }}>
                      {refineNote}
                    </span>
                  )}
                </div>
                {refineError && (
                  <p data-testid="refine-error" style={{ ...muted, fontSize: 12.5, marginTop: 10, lineHeight: 1.5 }}>
                    {refineError}
                  </p>
                )}
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Top-level: gallery <-> detail
// ---------------------------------------------------------------------------

export interface ReportsViewProps {
  client?: ApiClient;
  onAskAgents?: () => void;
}

export function ReportsView({ client, onAskAgents }: ReportsViewProps) {
  const api = client ?? defaultClient();
  const [views, setViews] = useState<SavedViewRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setViews(await api.listViews());
    } catch (e) {
      setError(friendlyErrorMessage(e, "Couldn't load your reports. Please try again."));
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div
      data-testid="reports-view"
      style={{ maxWidth: 920, margin: "0 auto", padding: "40px 24px", fontFamily: "system-ui, sans-serif" }}
    >
      <div style={{ marginBottom: 18 }}>
        <h1 style={{ fontSize: 22, fontWeight: 760, letterSpacing: "-.02em" }}>Reports</h1>
        <p style={{ ...muted, fontSize: 13.5, marginTop: 4 }}>
          Saved views for your whole workspace — versioned, and built from plain-English asks.
        </p>
      </div>

      {selected ? (
        // key={selected} so each view gets a fresh Detail instance — refine
        // state (the 501 "not live" notice, errors, the draft instruction)
        // never bleeds from one report into another.
        <Detail key={selected} api={api} viewId={selected} onBack={() => setSelected(null)} />
      ) : (
        <>
          {loading && <Spinner testid="reports-loading" label="Loading reports..." />}

          {!loading && error && (
            <ErrorCard
              message={error}
              onRetry={() => void load()}
              testid="reports-error"
              retryTestid="reports-retry"
            />
          )}

          {!loading && !error && views && views.length === 0 && (
            <div data-testid="reports-empty" style={{ ...card, textAlign: "center" }}>
              <div style={{ fontSize: 15, fontWeight: 700, color: "var(--ink, #2a2622)" }}>
                No saved reports yet
              </div>
              <p style={{ ...muted, fontSize: 13, marginTop: 6, lineHeight: 1.5 }}>
                Ask your agents for a metric or chart, then save it. Saved views land here,
                versioned, for the whole workspace.
              </p>
              {onAskAgents && (
                <button
                  data-testid="reports-empty-cta"
                  onClick={onAskAgents}
                  style={{
                    ...ghostBtn,
                    background: "var(--accent, #4f46e5)",
                    color: "#fff",
                    border: "1px solid transparent",
                    marginTop: 16,
                  }}
                >
                  Ask your agents
                </button>
              )}
            </div>
          )}

          {!loading && !error && views && views.length > 0 && (
            <Gallery views={views} onOpen={(id) => setSelected(id)} />
          )}
        </>
      )}
    </div>
  );
}

export default ReportsView;
