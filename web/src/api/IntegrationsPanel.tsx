// Integrations (Switchboard), wired to the control-plane API via ApiClient.
//
// The real-mode counterpart of the FLStore prototype screen
// (src/screens/integrations.tsx, mock mode only). Everything rendered here is
// honest: the list, the connected/not-connected/unknown badges and the
// secrets/sync configuration notes come straight from GET /integrations —
// nothing is invented client-side and there are no fake successes anywhere.
//
// Connect flow: a masked token input POSTs to /integrations/{name}/credentials.
// The token is write-only — held transiently in component state, sent in the
// request body (token ONLY, never a tenant_id: THE TRUST RULE), never logged,
// never echoed back into the DOM, and cleared as soon as the request settles
// successfully. Per-status copy mirrors the API contract: 503 storage not
// configured on this deployment, 422 empty token, 502 vault write failed.
//
// Sync-now: POSTs /integrations/{name}/sync per connected integration and
// reports the SyncResult counts the server actually returned. 503 (ingestion
// plane not wired), 409 (connect first — no vaulted credential) and 502 are
// surfaced with honest copy; raw "API <code>" strings never reach the user.

import React from "react";
import {
  ApiClient,
  ApiError,
  defaultClient,
  friendlyErrorMessage,
  type Integration,
  type IntegrationStatus,
  type ListIntegrationsResponse,
} from "./client";
import { Spinner } from "./Spinner";

const { useState, useEffect, useCallback } = React;

// ---------------------------------------------------------------------------
// Honest per-status copy. The API authors machine-facing detail strings (env
// var names, REQ ids) — these map each contract status to user-facing copy
// without ever exposing the raw "API <code>" message or the token.
// ---------------------------------------------------------------------------

function connectErrorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 503) {
      return "Credential storage isn't configured on this deployment yet, so connecting is unavailable for now.";
    }
    if (e.status === 422) {
      return "That token can't be empty. Paste the full token and try again.";
    }
    if (e.status === 502) {
      return "The credential vault didn't accept the write. Nothing was stored — please try again.";
    }
  }
  return friendlyErrorMessage(e, "Couldn't store the credential. Please try again.");
}

function syncErrorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 503) {
      return "Sync isn't configured on this deployment yet, so nothing was synced.";
    }
    if (e.status === 409) {
      return "Connect this integration first — no credential is stored for your workspace, so the sync didn't run.";
    }
    if (e.status === 502) {
      return "The sync didn't complete. Nothing to show — please try again shortly.";
    }
  }
  return friendlyErrorMessage(e, "Couldn't start the sync. Please try again.");
}

// Summarize ONLY what the server reported — counts that exist in the result.
// No invented numbers: when the result carries none of the known fields, the
// summary claims nothing beyond completion.
function syncSummary(result: Record<string, unknown>): string {
  const parts: string[] = [];
  const labels: Array<[string, string]> = [
    ["pulled", "pulled"],
    ["landed_rows", "landed"],
    ["embedded", "embedded"],
    ["skipped", "skipped"],
  ];
  for (const [field, label] of labels) {
    const v = result[field];
    if (typeof v === "number") parts.push(`${v} ${label}`);
  }
  return parts.length > 0 ? `Sync finished: ${parts.join(", ")}.` : "Sync finished.";
}

// ---------------------------------------------------------------------------
// Status badge — the three statuses the API can answer, including a VISIBLE
// "Unknown" (connected: null = the server honestly couldn't determine it).
// ---------------------------------------------------------------------------

const BADGE: Record<IntegrationStatus, { label: string; fg: string; bg: string }> = {
  connected: { label: "Connected", fg: "oklch(0.42 0.1 152)", bg: "oklch(0.95 0.04 152)" },
  not_connected: { label: "Not connected", fg: "var(--ink-3, #8a8278)", bg: "var(--accent-soft, #f4f1ea)" },
  unknown: { label: "Unknown", fg: "oklch(0.5 0.12 60)", bg: "oklch(0.96 0.05 85)" },
};

function StatusBadge({ status }: { status: IntegrationStatus }) {
  // An unrecognized status from a newer API still renders honestly as Unknown.
  const b = BADGE[status] ?? BADGE.unknown;
  return (
    <span
      data-testid="int-status"
      data-status={status}
      style={{
        fontSize: 12,
        fontWeight: 650,
        padding: "3px 10px",
        borderRadius: 999,
        color: b.fg,
        background: b.bg,
        whiteSpace: "nowrap",
      }}
    >
      {b.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------

const card: React.CSSProperties = {
  border: "1px solid var(--line, #e3ddd3)",
  background: "var(--surface, #fff)",
  borderRadius: 14,
  padding: "18px 20px",
  marginBottom: 16,
};

interface CardMsg {
  kind: "ok" | "error";
  text: string;
}

export interface IntegrationsPanelProps {
  client?: ApiClient;
}

export function IntegrationsPanel({ client }: IntegrationsPanelProps) {
  const api = client ?? defaultClient();
  const [data, setData] = useState<ListIntegrationsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Per-integration UI state, keyed by integration name.
  const [connectOpen, setConnectOpen] = useState<Record<string, boolean>>({});
  // Tokens live ONLY here (in memory, masked in the DOM) until the POST
  // settles; success clears them. They are never logged or echoed back.
  const [tokens, setTokens] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [msgs, setMsgs] = useState<Record<string, CardMsg>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.listIntegrations());
    } catch (e) {
      setData(null);
      setError(friendlyErrorMessage(e, "Couldn't load your integrations. Please try again."));
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  const setMsg = (name: string, msg: CardMsg | null) =>
    setMsgs((m) => {
      const next = { ...m };
      if (msg === null) delete next[name];
      else next[name] = msg;
      return next;
    });

  const connect = useCallback(
    async (item: Integration) => {
      const token = tokens[item.name] ?? "";
      setBusy((b) => ({ ...b, [item.name]: true }));
      setMsg(item.name, null);
      try {
        const res = await api.storeIntegrationCredentials(item.name, { token });
        // Status straight from the API response — never assumed.
        setData((cur) =>
          cur === null
            ? cur
            : {
                ...cur,
                integrations: cur.integrations.map((i) =>
                  i.name === item.name
                    ? { ...i, status: res.status, connected: res.status === "connected" }
                    : i,
                ),
              },
        );
        // Drop the token from memory and close the form: it was vaulted
        // server-side and must not linger client-side.
        setTokens((t) => {
          const next = { ...t };
          delete next[item.name];
          return next;
        });
        setConnectOpen((o) => ({ ...o, [item.name]: false }));
        setMsg(item.name, {
          kind: "ok",
          text: `${item.label} is connected. The token was stored in your workspace vault and is never shown again.`,
        });
      } catch (e) {
        // The token is deliberately NOT part of any message; the input keeps
        // its (masked) value so the user can correct and retry.
        setMsg(item.name, { kind: "error", text: connectErrorMessage(e) });
      } finally {
        setBusy((b) => ({ ...b, [item.name]: false }));
      }
    },
    [api, tokens],
  );

  const syncNow = useCallback(
    async (item: Integration) => {
      setBusy((b) => ({ ...b, [item.name]: true }));
      setMsg(item.name, null);
      try {
        const res = await api.kickIntegrationSync(item.name);
        setMsg(item.name, { kind: "ok", text: syncSummary(res.result ?? {}) });
      } catch (e) {
        setMsg(item.name, { kind: "error", text: syncErrorMessage(e) });
      } finally {
        setBusy((b) => ({ ...b, [item.name]: false }));
      }
    },
    [api],
  );

  const items = data?.integrations ?? [];
  const connectedCount = items.filter((i) => i.status === "connected").length;

  return (
    <div
      data-testid="integrations-panel"
      style={{ maxWidth: 760, margin: "0 auto", padding: "32px 24px", fontFamily: "system-ui, sans-serif" }}
    >
      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 12, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--ink-3, #8a8278)" }}>
          Connect your stack
        </div>
        <h1 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.02em", margin: "6px 0 4px" }}>Switchboard</h1>
        <p style={{ color: "var(--ink-3, #8a8278)", fontSize: 14 }}>
          Connect the tools your business runs on. Tokens go straight to your workspace vault and are never shown again.
        </p>
        {/* Only claim a count once we actually know it (post-load, no error). */}
        {!loading && !error && data !== null && (
          <div data-testid="int-connected-count" style={{ marginTop: 10, fontSize: 13, color: "var(--ink-3, #8a8278)" }}>
            {connectedCount} of {items.length} connected
          </div>
        )}
      </div>

      {/* Deployment configuration notes, straight from the API — shown so an
          unconfigured deployment is honest up front, not only after a 503. */}
      {!loading && !error && data !== null && !data.secrets_configured && (
        <div data-testid="int-secrets-note" style={{ ...card, fontSize: 13, color: "var(--ink-3, #8a8278)" }}>
          Credential storage isn&rsquo;t configured on this deployment, so connecting an
          integration won&rsquo;t work yet. Connection statuses may show as Unknown.
        </div>
      )}
      {!loading && !error && data !== null && data.secrets_configured && !data.sync_configured && (
        <div data-testid="int-sync-note" style={{ ...card, fontSize: 13, color: "var(--ink-3, #8a8278)" }}>
          Sync isn&rsquo;t configured on this deployment yet — connected integrations
          can&rsquo;t be synced from here for now.
        </div>
      )}

      {loading && <Spinner testid="int-loading" label="Loading integrations..." />}

      {error && (
        <div
          data-testid="int-error"
          style={{ ...card, borderColor: "var(--rose, #b4413b)", color: "var(--ink, #2a2622)", fontSize: 13.5 }}
        >
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Something needs another try</div>
          <p style={{ color: "var(--ink-3, #8a8278)", lineHeight: 1.5 }}>{error}</p>
          <button data-testid="int-retry" onClick={() => void load()} style={{ ...ghostBtn, marginTop: 10, color: "var(--ink, #2a2622)" }}>
            Try again
          </button>
        </div>
      )}

      {!loading && !error && data !== null && items.length === 0 && (
        <div data-testid="int-empty" style={{ ...card, textAlign: "center", color: "var(--ink-3, #8a8278)" }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: "var(--ink, #2a2622)" }}>No integrations available yet</div>
          <p style={{ fontSize: 13, marginTop: 4 }}>
            When connectors are available for your workspace, they&rsquo;ll appear here.
          </p>
        </div>
      )}

      {!loading &&
        !error &&
        items.map((item) => {
          const isBusy = !!busy[item.name];
          const open = !!connectOpen[item.name];
          const msg = msgs[item.name];
          const token = tokens[item.name] ?? "";
          return (
            <div key={item.name} data-testid="integration-item" data-integration={item.name} style={card}>
              <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 12 }}>
                <div style={{ fontSize: 16, fontWeight: 720 }}>{item.label}</div>
                <StatusBadge status={item.status} />
              </div>
              <div style={{ fontSize: 12.5, color: "var(--ink-3, #8a8278)", marginTop: 2 }}>{item.category}</div>
              <p style={{ fontSize: 13.5, color: "var(--ink, #2a2622)", marginTop: 10, lineHeight: 1.5 }}>
                {item.description}
              </p>

              {msg && (
                <div
                  data-testid="int-card-msg"
                  data-kind={msg.kind}
                  style={{
                    fontSize: 13,
                    lineHeight: 1.5,
                    marginTop: 12,
                    padding: "10px 12px",
                    borderRadius: 10,
                    color: msg.kind === "error" ? "var(--rose, #b4413b)" : "var(--ink, #2a2622)",
                    background: msg.kind === "error" ? "oklch(0.97 0.02 18)" : "var(--accent-soft, #f4f1ea)",
                  }}
                >
                  {msg.text}
                </div>
              )}

              {open ? (
                <div style={{ marginTop: 14 }}>
                  <label
                    htmlFor={`int-token-${item.name}`}
                    style={{ display: "block", fontSize: 12, fontWeight: 600, color: "var(--ink-3, #8a8278)", marginBottom: 6 }}
                  >
                    Access token (stored in your vault, never displayed)
                  </label>
                  <input
                    id={`int-token-${item.name}`}
                    data-testid="int-token-input"
                    // Masked: the token is never rendered in clear text.
                    type="password"
                    autoComplete="off"
                    spellCheck={false}
                    placeholder="Paste the access token"
                    value={token}
                    disabled={isBusy}
                    onChange={(e) => setTokens((t) => ({ ...t, [item.name]: e.target.value }))}
                    style={{
                      width: "100%",
                      boxSizing: "border-box",
                      borderRadius: 10,
                      border: "1px solid var(--line, #e3ddd3)",
                      padding: "10px 12px",
                      fontSize: 13.5,
                      fontFamily: "inherit",
                    }}
                  />
                  <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
                    <button
                      data-testid="int-token-save"
                      disabled={isBusy || token.trim() === ""}
                      onClick={() => void connect(item)}
                      style={{ ...primaryBtn, opacity: isBusy || token.trim() === "" ? 0.6 : 1 }}
                    >
                      {isBusy ? "Storing..." : "Save token"}
                    </button>
                    <button
                      data-testid="int-token-cancel"
                      disabled={isBusy}
                      onClick={() => {
                        // Cancel drops the token from memory immediately.
                        setTokens((t) => {
                          const next = { ...t };
                          delete next[item.name];
                          return next;
                        });
                        setConnectOpen((o) => ({ ...o, [item.name]: false }));
                      }}
                      style={ghostBtn}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
                  <button
                    data-testid="int-connect-btn"
                    disabled={isBusy}
                    onClick={() => {
                      setMsg(item.name, null);
                      setConnectOpen((o) => ({ ...o, [item.name]: true }));
                    }}
                    style={item.status === "connected" ? ghostNeutralBtn : primaryBtn}
                  >
                    {item.status === "connected" ? "Replace token" : "Connect"}
                  </button>
                  {item.status === "connected" && (
                    <button
                      data-testid="int-sync-btn"
                      disabled={isBusy}
                      onClick={() => void syncNow(item)}
                      style={primaryBtn}
                    >
                      {isBusy ? "Syncing..." : "Sync now"}
                    </button>
                  )}
                </div>
              )}
            </div>
          );
        })}
    </div>
  );
}

const primaryBtn: React.CSSProperties = {
  padding: "8px 16px",
  borderRadius: 10,
  border: "none",
  background: "var(--accent, #2a2622)",
  color: "#fff",
  fontSize: 13.5,
  fontWeight: 650,
  cursor: "pointer",
};

const ghostBtn: React.CSSProperties = {
  padding: "8px 16px",
  borderRadius: 10,
  border: "1px solid var(--line, #e3ddd3)",
  background: "transparent",
  color: "var(--rose, #b4413b)",
  fontSize: 13.5,
  fontWeight: 650,
  cursor: "pointer",
};

const ghostNeutralBtn: React.CSSProperties = {
  ...ghostBtn,
  color: "var(--ink, #2a2622)",
};

export default IntegrationsPanel;
