// Dashboard path wired to the control plane: loads a saved view via getView,
// renders it through the existing trusted SpecRenderer, and can persist edits
// back via saveView. The renderer never fetches; loadData is injected:
//   - MOCK builds inject the offline sampleLoadData fixture stub, so demos and
//     tests render deterministic numbers with no network.
//   - REAL builds inject noLiveData below. The live data plane (the Cube
//     semantic layer) is not deployed yet, so every block honestly renders its
//     "No data yet" state — demo fixture numbers must NEVER be presented as a
//     real tenant's data.

import React from "react";
import { ApiClient, ApiError, defaultClient, friendlyErrorMessage } from "./client";
import { SpecRenderer, type LoadData } from "../dashboard/SpecRenderer";
import { Spinner } from "./Spinner";

const { useState, useEffect, useCallback } = React;

// Real-mode data loader: there is no live query path yet (Cube is authored but
// unapplied — see TODO.md), so resolve every query to zero rows. SpecRenderer
// turns that into explicit per-block "No data yet" states. Do NOT swap in
// sampleLoadData here: canned demo numbers on a real tenant's dashboard are a
// lie. When the semantic layer ships, this becomes the real query client.
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
  viewId?: string;
}

export function DashboardView({ client, viewId = "demo_pipeline" }: DashboardViewProps) {
  const api = client ?? defaultClient();
  // The selected saved view — seeded from the prop, switched via the views dropdown.
  const [currentViewId, setCurrentViewId] = useState(viewId);
  // The tenant's saved views (latest version per view_id) backing the dropdown.
  const [views, setViews] = useState<Array<{ view_id: string; title: string }>>([]);
  const [spec, setSpec] = useState<Record<string, unknown> | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  // True when the tenant simply has no saved view yet (a 404, not a failure).
  const [empty, setEmpty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedNote, setSavedNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setEmpty(false);
    try {
      const row = await api.getView(currentViewId);
      setSpec(row.spec_json);
      setVersion(row.version);
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
  }, [api, currentViewId]);

  useEffect(() => {
    void load();
  }, [load]);

  // The dropdown's list — degrades silently to an empty list (the selected view
  // still loads on its own; a list failure must not take the dashboard down).
  useEffect(() => {
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
  }, [api, savedNote]);

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
        {views.length > 0 && (
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
              onClick={() => void load()}
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
        </div>
      )}

      {!loading && spec && (
        <SpecRenderer spec={spec} loadData={api.isMock() ? mockLoadData : noLiveData} />
      )}
    </div>
  );
}

export default DashboardView;
