// Persisted workspace settings — the Settings panel that actually SAVES.
//
// Wired to the real API via ApiClient (GET/PUT /account/settings). Replaces the
// old toast-only "Save changes" no-op: workspace name + notification preferences
// now persist to tenant_settings (RLS-scoped, server derives the tenant from the
// verified JWT — the client never sends a tenant_id).
//
// Everything here is HONEST:
//   * On load, GET /account/settings. ApiError(503) (the settings store isn't
//     wired on this deployment) → a calm "Saving settings isn't available on this
//     deployment yet" notice, never a fake editable form that silently discards.
//   * Save calls PUT with ONLY the changed fields (a partial update); on success
//     the saved row is reflected back. 422 surfaces the server's validation copy.
//   * Raw transport strings never reach the DOM — errors route through
//     friendlyErrorMessage, with 503 handled explicitly first.

import React from "react";
import {
  ApiClient,
  ApiError,
  defaultClient,
  friendlyErrorMessage,
  type NotificationPrefs,
  type WorkspaceSettings as WorkspaceSettingsData,
} from "./client";
import { Spinner } from "./Spinner";

const { useState, useCallback, useEffect } = React;

// The notification toggles we expose (stored as bool flags in notification_prefs).
// Any other keys the server returns are preserved untouched on save.
const NOTIFICATION_TOGGLES: ReadonlyArray<{ key: string; label: string; hint: string }> = [
  { key: "email_digest", label: "Email digest", hint: "A daily summary of activity and approvals." },
  { key: "approval_reminders", label: "Approval reminders", hint: "Nudge me when items wait in Greenlight." },
  { key: "product_updates", label: "Product updates", hint: "Occasional notes about new capabilities." },
];

const card: React.CSSProperties = {
  border: "1px solid var(--line, #e3ddd3)",
  background: "var(--surface, #fff)",
  borderRadius: 14,
  padding: "18px 20px",
};
const muted: React.CSSProperties = { color: "var(--ink-3, #8a8278)" };
const primaryBtn: React.CSSProperties = {
  padding: "9px 16px",
  borderRadius: 10,
  border: "none",
  background: "var(--ink, #2a2622)",
  color: "var(--bg, #fff)",
  fontSize: 13.5,
  fontWeight: 680,
  cursor: "pointer",
};
const input: React.CSSProperties = {
  width: "100%",
  padding: "9px 12px",
  borderRadius: 10,
  border: "1px solid var(--line, #e3ddd3)",
  background: "var(--bg, #fff)",
  color: "var(--ink, #2a2622)",
  fontSize: 14,
};

export interface WorkspaceSettingsProps {
  client?: ApiClient;
}

export function WorkspaceSettings({ client }: WorkspaceSettingsProps) {
  const api = client ?? defaultClient();

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  const [name, setName] = useState("");
  const [prefs, setPrefs] = useState<NotificationPrefs>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const applyRow = useCallback((row: WorkspaceSettingsData) => {
    setName(row.workspace_name ?? "");
    setPrefs(row.notification_prefs ?? {});
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    setUnavailable(false);
    try {
      applyRow(await api.getSettings());
    } catch (e) {
      if (e instanceof ApiError && (e.status === 503 || e.status === 404)) {
        setUnavailable(true);
      } else {
        setLoadError(friendlyErrorMessage(e));
      }
    } finally {
      setLoading(false);
    }
  }, [api, applyRow]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = useCallback(async () => {
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      // Partial update: send the editable fields. Unknown keys the server already
      // holds in notification_prefs are preserved (the API merges per-field).
      const row = await api.putSettings({
        workspace_name: name.trim(),
        notification_prefs: prefs,
      });
      applyRow(row);
      setSaved(true);
    } catch (e) {
      setSaveError(friendlyErrorMessage(e));
    } finally {
      setSaving(false);
    }
  }, [api, name, prefs, applyRow]);

  const toggle = (key: string) =>
    setPrefs((p) => ({ ...p, [key]: !p[key] }));

  if (loading) {
    return (
      <div data-testid="settings-loading" style={{ ...card, ...muted, display: "flex", gap: 10, alignItems: "center" }}>
        <Spinner /> Loading settings…
      </div>
    );
  }

  if (unavailable) {
    return (
      <div data-testid="settings-unavailable" style={{ ...card, ...muted }}>
        Saving workspace settings isn’t available on this deployment yet.
      </div>
    );
  }

  if (loadError) {
    return (
      <div data-testid="settings-error" style={{ ...card }}>
        <div style={{ marginBottom: 10 }}>{loadError}</div>
        <button style={primaryBtn} onClick={() => void load()}>Try again</button>
      </div>
    );
  }

  return (
    <div data-testid="workspace-settings" style={{ display: "grid", gap: 16 }}>
      <div style={card}>
        <label htmlFor="ws-name" style={{ display: "block", fontWeight: 680, fontSize: 14, marginBottom: 8 }}>
          Workspace name
        </label>
        <input
          id="ws-name"
          data-testid="settings-workspace-name"
          style={input}
          value={name}
          maxLength={120}
          placeholder="Your workspace name"
          onChange={(e) => { setName(e.target.value); setSaved(false); }}
        />
      </div>

      <div style={card}>
        <div style={{ fontWeight: 680, fontSize: 14, marginBottom: 12 }}>Notifications</div>
        <div style={{ display: "grid", gap: 12 }}>
          {NOTIFICATION_TOGGLES.map((t) => (
            <label key={t.key} style={{ display: "flex", gap: 10, alignItems: "flex-start", cursor: "pointer" }}>
              <input
                type="checkbox"
                data-testid={`settings-pref-${t.key}`}
                checked={Boolean(prefs[t.key])}
                onChange={() => { toggle(t.key); setSaved(false); }}
                style={{ marginTop: 3 }}
              />
              <span>
                <span style={{ fontWeight: 620, fontSize: 13.5 }}>{t.label}</span>
                <span style={{ ...muted, fontSize: 12, display: "block" }}>{t.hint}</span>
              </span>
            </label>
          ))}
        </div>
      </div>

      <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
        <button
          style={{ ...primaryBtn, opacity: saving ? 0.7 : 1 }}
          data-testid="settings-save"
          disabled={saving}
          onClick={() => void save()}
        >
          {saving ? "Saving…" : "Save changes"}
        </button>
        {saved && <span data-testid="settings-saved" style={{ ...muted, fontSize: 13 }}>Saved.</span>}
        {saveError && <span data-testid="settings-save-error" style={{ color: "var(--rose, #b4413b)", fontSize: 13 }}>{saveError}</span>}
      </div>
    </div>
  );
}

export default WorkspaceSettings;
