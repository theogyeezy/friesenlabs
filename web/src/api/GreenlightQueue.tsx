// Greenlight approval queue, wired to the control-plane API via ApiClient.
//
// This is the demo centerpiece: each pending item shows the agent's reasoning,
// the value at stake, and an editable draft. A human can approve, approve with
// edits, or deny. Approving/denying removes the item from the queue. All data
// flows through the injected ApiClient (mock mode for tests); the raw bearer
// token and the full proposed-action payload are never rendered.

import React from "react";
import { ApiClient, defaultClient, friendlyErrorMessage, type Approval } from "./client";
import { Spinner } from "./Spinner";

const { useState, useEffect, useCallback } = React;

function formatMoney(v: number | null): string {
  if (v === null || v === undefined) return "n/a";
  if (v >= 1000) return `$${(v / 1000).toFixed(1)}k`;
  return `$${v.toFixed(0)}`;
}

// Pull a human-readable editable draft out of the proposed action without
// exposing the whole payload. Prefer a body/note/message field; fall back to a
// short summary line.
function draftFromAction(a: Approval): string {
  const pa = a.proposed_action ?? {};
  for (const key of ["body", "note", "message", "justification", "summary"]) {
    const v = (pa as Record<string, unknown>)[key];
    if (typeof v === "string") return v;
  }
  return "";
}

function actionLabel(a: Approval): string {
  const action = (a.proposed_action ?? {}).action;
  return typeof action === "string" ? action.replace(/_/g, " ") : "action";
}

const card: React.CSSProperties = {
  border: "1px solid var(--line, #e3ddd3)",
  background: "var(--surface, #fff)",
  borderRadius: 14,
  padding: "18px 20px",
  marginBottom: 16,
};

export interface GreenlightQueueProps {
  client?: ApiClient;
}

export function GreenlightQueue({ client }: GreenlightQueueProps) {
  const api = client ?? defaultClient();
  const [items, setItems] = useState<Approval[]>([]);
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  // Per-item deny reason text (optional, shown when Deny is clicked).
  const [denyReasons, setDenyReasons] = useState<Record<number, string>>({});
  // Tracks which items have Deny clicked (to show the reason textarea).
  const [denyPending, setDenyPending] = useState<Record<number, boolean>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Per-item decision errors: set when decide() fails for ONE item.
  // Keyed by approval id; kept separately from the page-level load error so a
  // failed decision leaves the queue intact and shows the error in-item.
  const [itemErrors, setItemErrors] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<Record<number, boolean>>({});
  const [toast, setToast] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const approvals = await api.listApprovals();
      setItems(approvals);
      const d: Record<number, string> = {};
      approvals.forEach((a) => (d[a.id] = draftFromAction(a)));
      setDrafts(d);
    } catch (e) {
      setError(friendlyErrorMessage(e, "Couldn't load the queue. Please try again."));
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  const decide = useCallback(
    async (a: Approval, decision: "approve" | "edit" | "deny") => {
      setBusy((b) => ({ ...b, [a.id]: true }));
      // Clear any prior per-item error for this item before retrying.
      setItemErrors((prev) => { const n = { ...prev }; delete n[a.id]; return n; });
      try {
        const body =
          decision === "edit"
            ? { decision, edits: editsFor(a, drafts[a.id]) }
            : decision === "deny"
              ? { decision, deny_message: denyReasons[a.id]?.trim() || "Declined by reviewer." }
              : { decision };
        await api.decideApproval(a.id, body);
        // Optimistically drop it from the visible queue.
        setItems((cur) => cur.filter((i) => i.id !== a.id));
        setDenyPending((p) => { const n = { ...p }; delete n[a.id]; return n; });
        setToast(
          decision === "deny"
            ? "Declined"
            : decision === "edit"
              ? "Approved with edits"
              : "Approved and sent",
        );
        window.setTimeout(() => setToast(null), 2500);
      } catch (e) {
        // Per-item error: the item STAYS in the queue; the page-level error is
        // NOT set so the page-level "queue failed to load" card never shows.
        setItemErrors((prev) => ({
          ...prev,
          [a.id]: friendlyErrorMessage(e, "That decision didn't go through. Please try again."),
        }));
      } finally {
        setBusy((b) => ({ ...b, [a.id]: false }));
      }
    },
    [api, drafts, denyReasons],
  );

  return (
    <div
      data-testid="greenlight-queue"
      style={{ maxWidth: 760, margin: "0 auto", padding: "32px 24px", fontFamily: "system-ui, sans-serif" }}
    >
      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 12, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--ink-3, #8a8278)" }}>
          Human in the loop
        </div>
        <h1 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.02em", margin: "6px 0 4px" }}>Greenlight</h1>
        <p style={{ color: "var(--ink-3, #8a8278)", fontSize: 14 }}>
          Every agent action that needs your sign off, in one queue.
        </p>
        {/* Only claim a count once we actually know it (post-load, no error). */}
        {!loading && !error && (
          <div data-testid="pending-count" style={{ marginTop: 10, fontSize: 13, color: "var(--ink-3, #8a8278)" }}>
            {items.length} pending
          </div>
        )}
      </div>

      {loading && <Spinner testid="gl-loading" label="Loading the queue..." />}
      {error && (
        <div
          data-testid="gl-error"
          style={{ ...card, borderColor: "var(--rose, #b4413b)", color: "var(--ink, #2a2622)", fontSize: 13.5 }}
        >
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Something needs another try</div>
          <p style={{ color: "var(--ink-3, #8a8278)", lineHeight: 1.5 }}>{error}</p>
          {!loading && (
            <button data-testid="gl-retry" onClick={() => void load()} style={{ ...ghostBtn, marginTop: 10, color: "var(--ink, #2a2622)" }}>
              Try again
            </button>
          )}
        </div>
      )}

      {!loading && !error && items.length === 0 && (
        <div data-testid="gl-empty" style={{ ...card, textAlign: "center", color: "var(--ink-3, #8a8278)" }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: "var(--ink, #2a2622)" }}>Inbox zero</div>
          <p style={{ fontSize: 13, marginTop: 4 }}>
            Nothing is waiting on you. When an agent proposes a send, a discount, or any
            side-effecting action, it lands here for your sign off.
          </p>
        </div>
      )}

      {items.map((a) => (
        <div key={a.id} data-testid="approval-item" data-approval-id={a.id} style={card}>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 12 }}>
            <div data-testid="approval-action" style={{ fontSize: 16, fontWeight: 720, textTransform: "capitalize" }}>
              {actionLabel(a)}
            </div>
            <div
              data-testid="approval-value"
              style={{ fontSize: 14, fontWeight: 700, color: "var(--ink, #2a2622)", whiteSpace: "nowrap" }}
            >
              {formatMoney(a.value_at_stake)} at stake
            </div>
          </div>

          <div style={{ fontSize: 12.5, color: "var(--ink-3, #8a8278)", marginTop: 2 }}>
            Proposed by {a.agent ?? "an agent"}
          </div>

          <div
            data-testid="approval-reasoning"
            style={{ fontSize: 13.5, color: "var(--ink, #2a2622)", marginTop: 12, lineHeight: 1.5, background: "var(--accent-soft, #f4f1ea)", borderRadius: 10, padding: "10px 12px" }}
          >
            <b style={{ fontWeight: 700 }}>Why: </b>
            {a.reasoning}
          </div>

          <div style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-3, #8a8278)", margin: "16px 0 6px" }}>
            Draft, editable
          </div>
          <textarea
            data-testid="approval-draft"
            value={drafts[a.id] ?? ""}
            disabled={busy[a.id]}
            onChange={(e) => setDrafts((d) => ({ ...d, [a.id]: e.target.value }))}
            style={{
              width: "100%",
              minHeight: 90,
              resize: "vertical",
              borderRadius: 10,
              border: "1px solid var(--line, #e3ddd3)",
              padding: "10px 12px",
              fontSize: 13.5,
              fontFamily: "inherit",
              lineHeight: 1.5,
              boxSizing: "border-box",
            }}
          />

          {denyPending[a.id] && (
            <div style={{ marginTop: 10 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-3, #8a8278)", marginBottom: 4 }}>
                Reason (optional)
              </div>
              <textarea
                data-testid="deny-reason"
                value={denyReasons[a.id] ?? ""}
                disabled={busy[a.id]}
                placeholder="Why are you declining this? (optional)"
                onChange={(e) => setDenyReasons((r) => ({ ...r, [a.id]: e.target.value }))}
                style={{
                  width: "100%",
                  minHeight: 60,
                  resize: "vertical",
                  borderRadius: 10,
                  border: "1px solid var(--line, #e3ddd3)",
                  padding: "8px 12px",
                  fontSize: 13.5,
                  fontFamily: "inherit",
                  lineHeight: 1.5,
                  boxSizing: "border-box",
                }}
              />
            </div>
          )}

          <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
            <button
              data-testid="approve-btn"
              disabled={busy[a.id]}
              onClick={() => void decide(a, draftChanged(a, drafts[a.id]) ? "edit" : "approve")}
              style={primaryBtn}
            >
              {draftChanged(a, drafts[a.id]) ? "Approve edited" : "Approve"}
            </button>
            {denyPending[a.id] ? (
              <>
                <button
                  data-testid="deny-confirm-btn"
                  disabled={busy[a.id]}
                  onClick={() => void decide(a, "deny")}
                  style={{ ...ghostBtn, color: "var(--rose, #b4413b)" }}
                >
                  Confirm deny
                </button>
                <button
                  data-testid="deny-cancel-btn"
                  disabled={busy[a.id]}
                  onClick={() => setDenyPending((p) => { const n = { ...p }; delete n[a.id]; return n; })}
                  style={{ ...ghostBtn, color: "var(--ink-3, #8a8278)" }}
                >
                  Cancel
                </button>
              </>
            ) : (
              <button
                data-testid="deny-btn"
                disabled={busy[a.id]}
                onClick={() => setDenyPending((p) => ({ ...p, [a.id]: true }))}
                style={ghostBtn}
              >
                Deny
              </button>
            )}
          </div>

          {itemErrors[a.id] && (
            <div
              data-testid="item-error"
              role="alert"
              style={{
                marginTop: 10,
                padding: "10px 12px",
                borderRadius: 10,
                background: "var(--rose-soft, #fdf2f2)",
                border: "1px solid var(--rose, #b4413b)",
                color: "var(--ink, #2a2622)",
                fontSize: 13,
              }}
            >
              <span style={{ color: "var(--rose, #b4413b)", fontWeight: 700 }}>Decision failed. </span>
              {itemErrors[a.id]}
              <button
                data-testid="item-error-retry"
                onClick={() => void decide(a, draftChanged(a, drafts[a.id]) ? "edit" : "approve")}
                disabled={busy[a.id]}
                style={{ ...ghostBtn, marginLeft: 8, padding: "4px 10px", fontSize: 12.5, color: "var(--ink, #2a2622)" }}
              >
                Retry
              </button>
            </div>
          )}
        </div>
      ))}

      {toast && (
        <div
          data-testid="gl-toast"
          style={{
            position: "fixed",
            bottom: 24,
            left: "50%",
            transform: "translateX(-50%)",
            background: "var(--ink, #2a2622)",
            color: "#fff",
            borderRadius: 12,
            padding: "10px 16px",
            fontSize: 13.5,
            fontWeight: 600,
          }}
        >
          {toast}
        </div>
      )}
    </div>
  );
}

// Did the reviewer change the draft text vs the original action draft?
function draftChanged(a: Approval, current: string | undefined): boolean {
  return (current ?? "") !== draftFromAction(a);
}

// Build the edits patch: replace whichever draft-bearing field the action used.
function editsFor(a: Approval, current: string | undefined): Record<string, unknown> {
  const pa = a.proposed_action ?? {};
  for (const key of ["body", "note", "message", "justification", "summary"]) {
    if (typeof (pa as Record<string, unknown>)[key] === "string") {
      return { [key]: current ?? "" };
    }
  }
  return { body: current ?? "" };
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

export default GreenlightQueue;
