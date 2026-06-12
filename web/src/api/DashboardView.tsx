// Dashboard path wired to the control plane: loads a saved view via getView,
// renders it through the existing trusted SpecRenderer, and can persist edits
// back via saveView. The renderer never fetches; loadData is injected:
//   - MOCK builds inject the offline sampleLoadData fixture stub, so demos and
//     tests render deterministic numbers with no network.
//   - REAL builds inject a loader built from POST /views/{id}/data — the saved
//     spec's CubeQueries resolved as the verified tenant (THE TRUST RULE). When
//     the data plane is unavailable (503/error) the loader degrades to zero rows
//     so every block honestly renders "No data yet" / "could not be loaded";
//     demo fixture numbers must NEVER be presented as a real tenant's data.

import React from "react";
import {
  ApiClient,
  ApiError,
  buildViewDataLoader,
  defaultClient,
  friendlyErrorMessage,
} from "./client";
import { SpecRenderer, type LoadData } from "../dashboard/SpecRenderer";
import { Spinner } from "./Spinner";

const { useState, useEffect, useCallback, useRef } = React;

// Empty real-mode loader: resolve every query to zero rows. Used as the calm
// fallback when the live data plane is unavailable (503/error) — SpecRenderer
// turns that into explicit per-block "No data yet" states. Never swap in
// sampleLoadData here: canned demo numbers on a real tenant's dashboard are a lie.
const noLiveData: LoadData = async () => [];

// Mock-build data loader: the offline sampleLoadData fixture, lazily loaded
// behind a BUILD-TIME gate (Vite replaces import.meta.env.VITE_API_MOCK with a
// literal). Real-mode bundles fold this branch away, so the demo fixture
// numbers in ../dashboard/sample are never emitted into a production bundle.
let mockLoadData: LoadData = noLiveData;
if (import.meta.env.VITE_API_MOCK !== "0" && import.meta.env.VITE_API_MOCK !== "false") {
  mockLoadData = async (query) => (await import("../dashboard/sample")).sampleLoadData(query);
}

export interface DashboardViewProps {
  client?: ApiClient;
  /** When supplied, load this exact view. When omitted, the component calls
   *  listViews() on mount and picks: 'demo_pipeline' if present, else the
   *  first row, else shows the honest empty state.  */
  viewId?: string;
  /** First-run: shell passes a handler that loads the demo fixture into this
   *  tenant. Without it the empty state stays explanatory-only (no CTA). */
  onLoadSample?: () => void | Promise<void>;
  /** Shell callback to open the chat/agent panel ("Ask your agents"). */
  onAskAgents?: () => void;
}

export function DashboardView({ client, viewId, onLoadSample, onAskAgents }: DashboardViewProps) {
  const api = client ?? defaultClient();
  // The selected saved view — either from the explicit prop or resolved via
  // listViews() on first mount. null = not yet resolved.
  const [currentViewId, setCurrentViewId] = useState<string | null>(viewId ?? null);
  // The tenant's saved views (latest version per view_id) backing the dropdown.
  const [views, setViews] = useState<Array<{ view_id: string; title: string }>>([]);
  const [spec, setSpec] = useState<Record<string, unknown> | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  // The injected data loader for the open spec. Mock builds use the offline
  // fixture; real builds use a loader built from POST /views/{id}/data (or the
  // empty noLiveData fallback when the data plane is unavailable).
  const [dataLoader, setDataLoader] = useState<LoadData>(() =>
    api.isMock() ? mockLoadData : noLiveData,
  );
  const [loading, setLoading] = useState(true);
  // True when the tenant simply has no saved views (listViews empty or the
  // resolved id 404'd with no fallback), not a fetch failure.
  const [empty, setEmpty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedNote, setSavedNote] = useState<string | null>(null);
  const [loadingSample, setLoadingSample] = useState(false);

  const runLoadSample = useCallback(async () => {
    if (loadingSample || !onLoadSample) return;
    setLoadingSample(true);
    try {
      await onLoadSample();
    } finally {
      setLoadingSample(false);
    }
  }, [loadingSample, onLoadSample]);

  // loadView: fetch and render a specific view id (called once we know the id).
  const loadView = useCallback(async (id: string) => {
    setLoading(true);
    setError(null);
    setEmpty(false);
    try {
      const row = await api.getView(id);
      setSpec(row.spec_json);
      setVersion(row.version);
      // Mock builds keep the offline fixture loader; real builds resolve the
      // spec's CubeQueries via POST /views/{id}/data. A data-plane failure
      // (503/404/502/network) is NOT a view-load error: keep the spec and fall
      // back to the empty loader so each panel shows its calm "No data yet" /
      // "could not be loaded" state instead of crashing the whole view.
      if (!api.isMock()) {
        try {
          const data = await api.loadViewData(id);
          setDataLoader(() => buildViewDataLoader(row.spec_json, data));
        } catch {
          setDataLoader(() => noLiveData);
        }
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        // A fresh tenant with no saved views is the normal empty state,
        // not an error.
        setEmpty(true);
      } else {
        setError(friendlyErrorMessage(e, "Couldn't load this view. Please try again."));
      }
    } finally {
      setLoading(false);
    }
  }, [api]);

  // resolvedRef: tracks the id we loaded most recently to avoid double-loads
  // when the dropdown effect fires right after mount resolution.
  const resolvedRef = useRef<string | null>(null);

  // Mount-time resolution: if viewId was supplied as a prop, load it directly.
  // Otherwise call listViews() and pick the best default:
  //   1. 'demo_pipeline' if present (the Load-sample seed id)
  //   2. the first row's view_id otherwise
  //   3. honest empty state when listViews() is truly empty
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      if (viewId) {
        resolvedRef.current = viewId;
        setCurrentViewId(viewId);
        await loadView(viewId);
        return;
      }
      // No explicit id — resolve via listViews().
      setLoading(true);
      setError(null);
      setEmpty(false);
      try {
        const rows = await api.listViews();
        if (cancelled) return;
        // Populate the dropdown list immediately (no extra round-trip needed).
        setViews(
          rows.map((r) => ({
            view_id: r.view_id,
            title: String((r.spec_json as Record<string, unknown>).title ?? r.view_id),
          })),
        );
        if (rows.length === 0) {
          setEmpty(true);
          setLoading(false);
          return;
        }
        const preferred = rows.find((r) => r.view_id === "demo_pipeline");
        const chosen = (preferred ?? rows[0]).view_id;
        resolvedRef.current = chosen;
        setCurrentViewId(chosen);
        await loadView(chosen);
      } catch {
        if (!cancelled) {
          setError("Couldn't load your views. Please try again.");
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // mount-only — remount via key to refresh

  // Dropdown's list refresher — runs after a save (savedNote change) or when
  // currentViewId settles after mount. Degrades silently.
  useEffect(() => {
    if (!currentViewId) return;
    let cancelled = false;
    void (async () => {
      try {
        const rows = await api.listViews();
        if (cancelled) return;
        setViews(
          rows.map((r) => ({
            view_id: r.view_id,
            title: String((r.spec_json as Record<string, unknown>).title ?? r.view_id),
          })),
        );
      } catch {
        // keep the empty list — the dropdown simply has nothing to offer
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [api, savedNote]); // savedNote drives refresh after saves

  // Dropdown switch: when the user changes to a different view, load it.
  // Skip the very first time currentViewId settles from mount-resolution
  // (resolvedRef tracks that we already loaded it).
  useEffect(() => {
    if (!currentViewId) return;
    if (resolvedRef.current === currentViewId) return; // already loaded at mount
    resolvedRef.current = currentViewId;
    void loadView(currentViewId);
  }, [currentViewId, loadView]);

  const save = useCallback(async () => {
    if (!spec) return;
    setSaving(true);
    setSavedNote(null);
    try {
      const row = await api.saveView({
        spec,
        source_prompt: String((spec as Record<string, unknown>).source_prompt ?? ""),
      });
      setVersion(row.version);
      setSavedNote(`Saved as version ${row.version}`);
      window.setTimeout(() => setSavedNote(null), 2500);
    } catch (e) {
      setError(friendlyErrorMessage(e, "Couldn't save this view. Please try again."));
    } finally {
      setSaving(false);
    }
  }, [api, spec]);

  return (
    <div
      data-testid="dashboard-view"
      style={{ maxWidth: 920, margin: "0 auto", padding: "40px 24px", fontFamily: "system-ui, sans-serif" }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
        <strong style={{ fontSize: 14 }}>Saved view</strong>
        {views.length > 0 && currentViewId && (
          <select
            data-testid="views-dropdown"
            aria-label="Select a saved view"
            value={currentViewId}
            onChange={(e) => setCurrentViewId(e.target.value)}
            style={{
              padding: "6px 10px",
              borderRadius: 8,
              border: "1px solid var(--line, #ccc)",
              background: "#fff",
              fontSize: 13,
              fontFamily: "inherit",
            }}
          >
            {/* Keep the current id selectable even if it isn't in the list (fresh 404 state). */}
            {!views.some((v) => v.view_id === currentViewId) && (
              <option value={currentViewId}>{currentViewId}</option>
            )}
            {views.map((v) => (
              <option key={v.view_id} value={v.view_id}>
                {v.title}
              </option>
            ))}
          </select>
        )}
        {version !== null && (
          <span data-testid="view-version" style={{ fontSize: 12, color: "var(--ink-3, #8a8278)" }}>
            version {version}
          </span>
        )}
        <button
          data-testid="save-view"
          onClick={() => void save()}
          disabled={saving || !spec}
          style={{
            marginLeft: "auto",
            padding: "6px 14px",
            borderRadius: 8,
            border: "1px solid var(--line, #ccc)",
            background: "#fff",
            cursor: "pointer",
            fontSize: 13,
          }}
        >
          {saving ? "Saving..." : "Save view"}
        </button>
        {savedNote && (
          <span data-testid="saved-note" style={{ fontSize: 12, color: "var(--green, #2f8a4f)" }}>
            {savedNote}
          </span>
        )}
      </div>

      {error && (
        <div
          data-testid="dashboard-error"
          style={{
            border: "1px solid var(--rose, #b4413b)",
            background: "var(--surface, #fff)",
            borderRadius: 14,
            padding: "18px 20px",
            marginBottom: 12,
            fontSize: 13.5,
          }}
        >
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Something needs another try</div>
          <p style={{ color: "var(--ink-3, #8a8278)", lineHeight: 1.5 }}>{error}</p>
          {!loading && (
            <button
              data-testid="dashboard-retry"
              onClick={() => { if (currentViewId) void loadView(currentViewId); }}
              style={{
                marginTop: 10,
                padding: "7px 14px",
                borderRadius: 8,
                border: "1px solid var(--line, #ccc)",
                background: "#fff",
                cursor: "pointer",
                fontSize: 13,
              }}
            >
              Try again
            </button>
          )}
        </div>
      )}

      {loading && <Spinner testid="dashboard-loading" label="Loading view..." />}

      {!loading && empty && (
        <div
          data-testid="dashboard-empty"
          style={{
            border: "1px solid var(--line, #e3ddd3)",
            background: "var(--surface, #fff)",
            borderRadius: 14,
            padding: "26px 24px",
            textAlign: "center",
            color: "var(--ink-3, #8a8278)",
          }}
        >
          <div style={{ fontSize: 15, fontWeight: 700, color: "var(--ink, #2a2622)" }}>
            No saved views yet
          </div>
          <p style={{ fontSize: 13, marginTop: 6, lineHeight: 1.5 }}>
            Ask your agents for a metric or chart, then save it. Saved views land here, versioned,
            for the whole workspace.
          </p>
          {onLoadSample && (
            <button
              type="button"
              data-testid="dashboard-empty-load-sample"
              onClick={() => void runLoadSample()}
              disabled={loadingSample}
              aria-busy={loadingSample}
              style={{
                marginTop: 16,
                appearance: "none",
                border: "1px solid transparent",
                borderRadius: 10,
                padding: "9px 16px",
                fontSize: 13,
                fontWeight: 700,
                fontFamily: "inherit",
                cursor: loadingSample ? "default" : "pointer",
                background: "var(--accent, #b4593b)",
                color: "var(--accent-ink-on, #fff)",
                opacity: loadingSample ? 0.7 : 1,
              }}
            >
              {loadingSample ? "Loading…" : "Load sample data"}
            </button>
          )}
          {onAskAgents && (
            <button
              type="button"
              data-testid="dashboard-empty-cta"
              onClick={onAskAgents}
              style={{
                marginTop: onLoadSample ? 10 : 16,
                marginLeft: onLoadSample ? 10 : 0,
                appearance: "none",
                border: "1px solid var(--line, #e3ddd3)",
                borderRadius: 10,
                padding: "9px 16px",
                fontSize: 13,
                fontWeight: 700,
                fontFamily: "inherit",
                cursor: "pointer",
                background: "#fff",
                color: "var(--ink, #2a2622)",
              }}
            >
              Ask your agents
            </button>
          )}
        </div>
      )}

      {!loading && spec && <SpecRenderer spec={spec} loadData={dataLoader} />}
    </div>
  );
}

export default DashboardView;
