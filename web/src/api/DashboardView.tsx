// Dashboard path wired to the control plane: loads a saved view via getView,
// renders it through the existing trusted SpecRenderer, and can persist edits
// back via saveView. Data for the renderer comes from the offline sampleLoadData
// stub (the renderer never fetches; loadData is injected). The spec itself comes
// from the API client (mock mode for tests).

import React from "react";
import { ApiClient, defaultClient } from "./client";
import { SpecRenderer } from "../dashboard/SpecRenderer";
import { sampleLoadData } from "../dashboard/sample";

const { useState, useEffect, useCallback } = React;

export interface DashboardViewProps {
  client?: ApiClient;
  viewId?: string;
}

export function DashboardView({ client, viewId = "demo_pipeline" }: DashboardViewProps) {
  const api = client ?? defaultClient();
  const [spec, setSpec] = useState<Record<string, unknown> | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedNote, setSavedNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const row = await api.getView(viewId);
      setSpec(row.spec_json);
      setVersion(row.version);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load this view.");
    }
  }, [api, viewId]);

  useEffect(() => {
    void load();
  }, [load]);

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
      setError(e instanceof Error ? e.message : "Could not save this view.");
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
        <div data-testid="dashboard-error" style={{ color: "var(--rose, #b4413b)", fontSize: 13, marginBottom: 12 }}>
          {error}
        </div>
      )}

      {spec ? (
        <SpecRenderer spec={spec} loadData={sampleLoadData} />
      ) : (
        !error && <div data-testid="dashboard-loading">Loading view...</div>
      )}
    </div>
  );
}

export default DashboardView;
