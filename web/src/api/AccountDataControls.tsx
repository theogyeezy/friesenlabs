// Account data export + deletion (GDPR / data portability) surface.
//
// Wired to the real API via ApiClient — the Settings panel that lets a tenant
// download their data or schedule account deletion.
//
// Everything here is HONEST:
//   * "Download my data" calls GET /account/export. If the endpoint answers 503
//     (the export worker isn't wired on this deployment), the button is replaced
//     with an honest "Export isn't available on this deployment yet" notice — no
//     fake "success" state.
//   * "Delete account" is behind an explicit confirm gate: the user must type the
//     workspace id before the delete button enables. On click it calls POST
//     /account/delete. A 503 means nothing was deleted — we show an honest
//     "contact support" message and never imply the deletion ran. A 422 surfaces
//     the server's validation detail.
//   * Raw transport strings never reach the DOM — catches route through
//     friendlyErrorMessage, with 503 handled explicitly before the fallback.
//   * THE TRUST RULE holds: the server derives the tenant from the verified JWT;
//     neither call sends a tenant_id.

import React from "react";
import {
  ApiClient,
  ApiError,
  defaultClient,
  friendlyErrorMessage,
  type AccountDeleteReport,
} from "./client";
import { Spinner } from "./Spinner";

const { useState, useCallback } = React;

// ---------------------------------------------------------------------------
// Styles (mirrored from SecurityControls / BillingManage)
// ---------------------------------------------------------------------------

const card: React.CSSProperties = {
  border: "1px solid var(--line, #e3ddd3)",
  background: "var(--surface, #fff)",
  borderRadius: 14,
  padding: "18px 20px",
};

const muted: React.CSSProperties = { color: "var(--ink-3, #8a8278)" };

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

const destructiveBtn: React.CSSProperties = {
  padding: "9px 16px",
  borderRadius: 10,
  border: "none",
  background: "var(--rose, #b4413b)",
  color: "#fff",
  fontSize: 13.5,
  fontWeight: 680,
  cursor: "pointer",
};

// ---------------------------------------------------------------------------
// Download my data control
// ---------------------------------------------------------------------------

function ExportControl({ api }: { api: ApiClient }) {
  const [loading, setLoading] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const handleExport = useCallback(async () => {
    setLoading(true);
    setError(null);
    setDone(false);
    try {
      const data = await api.exportAccountData();
      // Build a Blob from the JSON, trigger a download, then revoke the URL.
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "uplift-export.json";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      setDone(true);
    } catch (e) {
      if (e instanceof ApiError && e.status === 503) {
        setUnavailable(true);
      } else {
        setError(friendlyErrorMessage(e, "Couldn't export your data. Please try again."));
      }
    } finally {
      setLoading(false);
    }
  }, [api]);

  return (
    <div data-testid="export-control" style={{ ...card, marginBottom: 16 }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 14, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 200 }}>
          <b style={{ fontSize: 15, fontWeight: 720 }}>Download my data</b>
          <p style={{ ...muted, fontSize: 12.5, lineHeight: 1.5, margin: "6px 0 0" }}>
            Export everything Uplift holds for your workspace — contacts, deals, activities, and
            knowledge — as a single JSON file. Your data is yours.
          </p>
        </div>

        {loading ? (
          <Spinner testid="export-loading" label="Exporting…" />
        ) : unavailable ? null : (
          <button
            type="button"
            data-testid="export-btn"
            style={primaryBtn}
            onClick={() => void handleExport()}
          >
            Download
          </button>
        )}
      </div>

      {unavailable && (
        <p
          data-testid="export-unavailable"
          style={{ ...muted, fontSize: 12.5, lineHeight: 1.5, margin: "10px 0 0" }}
        >
          Export isn&rsquo;t available on this deployment yet — contact support if you need your
          data urgently.
        </p>
      )}

      {done && !error && (
        <p
          data-testid="export-success"
          style={{ fontSize: 12.5, lineHeight: 1.5, margin: "10px 0 0", color: "var(--green, #2e7d4f)" }}
        >
          Your export has been downloaded.
        </p>
      )}

      {error && (
        <div style={{ marginTop: 10 }}>
          <p
            data-testid="export-error"
            style={{ color: "var(--rose, #b4413b)", fontSize: 12.5, margin: "0 0 8px" }}
          >
            {error}
          </p>
          <button
            type="button"
            data-testid="export-retry"
            style={ghostBtn}
            onClick={() => void handleExport()}
          >
            Try again
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Delete account control
// ---------------------------------------------------------------------------

function DeleteControl({ api }: { api: ApiClient }) {
  const [revealed, setRevealed] = useState(false);
  const [confirmInput, setConfirmInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<AccountDeleteReport | null>(null);

  const handleDelete = useCallback(async () => {
    if (!confirmInput.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const r = await api.requestAccountDelete(confirmInput.trim());
      setReport(r);
    } catch (e) {
      if (e instanceof ApiError && e.status === 503) {
        // Endpoint is inert on this deployment — nothing was deleted.
        setError(
          "Account deletion isn’t enabled on this deployment yet — contact support to request data removal.",
        );
      } else if (e instanceof ApiError && e.status === 422) {
        // Server-side validation: surface the detail (e.g. wrong confirm id).
        setError(e.detail || "The confirmation didn’t match. Check the workspace id and try again.");
      } else {
        setError(friendlyErrorMessage(e, "Couldn’t request account deletion. Please try again."));
      }
    } finally {
      setLoading(false);
    }
  }, [api, confirmInput]);

  // Once a teardown report comes back, show it — whether successful or not.
  if (report) {
    return (
      <div data-testid="delete-control" style={{ ...card, borderColor: "var(--rose, #b4413b)" }}>
        <b style={{ fontSize: 15, fontWeight: 720 }}>Account deletion requested</b>
        <div
          data-testid="delete-report"
          style={{ marginTop: 12, fontSize: 13, lineHeight: 1.6 }}
        >
          <p style={{ margin: "0 0 8px", color: "var(--ink-2, #5d564d)" }}>
            Your workspace data has been torn down. Append-only audit records are
            retained for compliance (shown below with the reason).
          </p>
          {Object.keys(report.deleted).length > 0 && (
            <div style={{ marginBottom: 6 }}>
              <span style={{ fontWeight: 650, fontSize: 12 }}>Deleted:</span>{" "}
              <span style={{ ...muted, fontSize: 12 }}>
                {Object.entries(report.deleted)
                  .map(([table, count]) => `${table} (${count})`)
                  .join(", ")}
              </span>
            </div>
          )}
          {Object.keys(report.retained).length > 0 && (
            <div style={{ marginBottom: 6 }}>
              <span style={{ fontWeight: 650, fontSize: 12 }}>Retained:</span>{" "}
              <span style={{ ...muted, fontSize: 12 }}>
                {Object.entries(report.retained)
                  .map(([table, reason]) => `${table} (${reason})`)
                  .join(", ")}
              </span>
            </div>
          )}
          {Object.keys(report.failed).length > 0 && (
            <div style={{ marginBottom: 6 }}>
              <span style={{ fontWeight: 650, fontSize: 12, color: "var(--rose, #b4413b)" }}>
                Failed:
              </span>{" "}
              <span style={{ fontSize: 12, color: "var(--rose, #b4413b)" }}>
                {Object.entries(report.failed)
                  .map(([table, err]) => `${table} (${err})`)
                  .join(", ")}
              </span>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div
      data-testid="delete-control"
      style={{
        ...card,
        borderColor: revealed ? "var(--rose, #b4413b)" : "var(--line, #e3ddd3)",
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: 14, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 200 }}>
          <b style={{ fontSize: 15, fontWeight: 720, color: "var(--rose, #b4413b)" }}>
            Delete account
          </b>
          <p style={{ ...muted, fontSize: 12.5, lineHeight: 1.5, margin: "6px 0 0" }}>
            Permanently remove your workspace, all data, and your subscription. This cannot be
            undone.
          </p>
        </div>

        {!revealed && (
          <button
            type="button"
            data-testid="delete-reveal-btn"
            style={{
              ...ghostBtn,
              color: "var(--rose, #b4413b)",
              borderColor: "var(--rose, #b4413b)",
            }}
            onClick={() => setRevealed(true)}
          >
            Delete account
          </button>
        )}
      </div>

      {revealed && (
        <div
          data-testid="delete-confirm-block"
          style={{
            marginTop: 16,
            padding: "14px 16px",
            background: "oklch(0.98 0.01 25)",
            borderRadius: 10,
            border: "1px solid var(--rose, #b4413b)",
          }}
        >
          <p
            style={{
              margin: "0 0 12px",
              fontSize: 13,
              fontWeight: 650,
              color: "var(--rose, #b4413b)",
            }}
          >
            This is irreversible. All your contacts, deals, activities, integrations, and agent
            history will be deleted. You cannot undo this.
          </p>
          <label
            style={{ display: "block", fontSize: 12.5, fontWeight: 650, marginBottom: 6, ...muted }}
            htmlFor="delete-confirm-input"
          >
            Type your workspace id to confirm:
          </label>
          <input
            id="delete-confirm-input"
            data-testid="delete-confirm-input"
            type="text"
            value={confirmInput}
            onChange={(e) => setConfirmInput(e.target.value)}
            placeholder="workspace id"
            style={{
              width: "100%",
              boxSizing: "border-box",
              padding: "8px 12px",
              borderRadius: 8,
              border: "1px solid var(--line, #e3ddd3)",
              fontSize: 13.5,
              fontFamily: "var(--mono, ui-monospace, monospace)",
              marginBottom: 12,
              outline: "none",
            }}
          />

          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            {loading ? (
              <Spinner testid="delete-loading" label="Requesting deletion…" />
            ) : (
              <>
                <button
                  type="button"
                  data-testid="delete-confirm-btn"
                  style={{
                    ...destructiveBtn,
                    opacity: confirmInput.trim() ? 1 : 0.45,
                    cursor: confirmInput.trim() ? "pointer" : "not-allowed",
                  }}
                  disabled={!confirmInput.trim()}
                  onClick={() => void handleDelete()}
                >
                  Permanently delete
                </button>
                <button
                  type="button"
                  data-testid="delete-cancel-btn"
                  style={ghostBtn}
                  onClick={() => {
                    setRevealed(false);
                    setConfirmInput("");
                    setError(null);
                  }}
                >
                  Cancel
                </button>
              </>
            )}
          </div>

          {error && (
            <p
              data-testid="delete-error"
              role="alert"
              style={{
                color: "var(--rose, #b4413b)",
                fontSize: 12.5,
                margin: "10px 0 0",
                lineHeight: 1.5,
              }}
            >
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Composed settings surface
// ---------------------------------------------------------------------------

/**
 * Real-mode Settings — account data export + deletion (GDPR). `client` is
 * injectable for tests; defaults to the shared app client.
 */
export function AccountDataControls({ client }: { client?: ApiClient }) {
  const api = client ?? defaultClient();

  return (
    <div
      data-testid="account-data-controls"
      style={{ maxWidth: 820, margin: "0 auto", padding: "32px 24px", fontFamily: "system-ui, sans-serif" }}
    >
      <div style={{ marginBottom: 18 }}>
        <div
          style={{
            fontSize: 12,
            fontWeight: 600,
            letterSpacing: ".06em",
            textTransform: "uppercase",
            ...muted,
          }}
        >
          Workspace settings
        </div>
        <h1 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.02em", margin: "6px 0 4px" }}>
          Your data
        </h1>
        <p style={{ ...muted, fontSize: 14 }}>
          Download a copy of everything Uplift holds for your workspace, or permanently delete your
          account.
        </p>
      </div>

      <ExportControl api={api} />
      <DeleteControl api={api} />
    </div>
  );
}

export default AccountDataControls;
