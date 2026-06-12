// Greenlight approval queue, wired to the control-plane API via ApiClient.
//
// Each pending item shows the agent's reasoning, the value at stake, a structured
// read-only view of WHAT the action will do (recipient, deal, changes — approval
// audit P0: a reviewer must never approve blind), and an editable draft when the
// action carries one. A human can approve, approve with edits, or deny with an
// optional reason. The queue polls quietly for new items (without clobbering
// in-progress edits), reports the tenant's total pending count, degrades to an
// honest "not yet enabled" card on a 404 control plane, and the post-approve
// toast distinguishes a real applied write from a draft-only record. A failed
// decision on ONE item writes the error inside that item (the queue stays intact)
// and offers an in-item retry. The raw bearer token is never rendered.

import React from "react";
import {
  ApiClient,
  ApiError,
  defaultClient,
  friendlyErrorMessage,
  type Approval,
} from "./client";
import { Spinner } from "./Spinner";

const { useState, useEffect, useCallback, useRef } = React;

// How often the queue re-checks for new pending items (quiet — no spinner, edits kept).
const POLL_INTERVAL_MS = 45_000;

function formatMoney(v: number | null): string {
  if (v === null || v === undefined) return "n/a";
  if (v >= 1000) return `$${(v / 1000).toFixed(1)}k`;
  return `$${v.toFixed(0)}`;
}

// The payload key the editable textarea owns (body/note/...), if the action has one.
const DRAFT_KEYS = ["body", "note", "message", "justification", "summary"] as const;

function draftKeyFor(a: Approval): string | null {
  const pa = a.proposed_action ?? {};
  for (const key of DRAFT_KEYS) {
    if (typeof (pa as Record<string, unknown>)[key] === "string") return key;
  }
  return null;
}

function draftFromAction(a: Approval): string {
  const key = draftKeyFor(a);
  if (key === null) return "";
  return String((a.proposed_action as Record<string, unknown>)[key]);
}

function actionLabel(a: Approval): string {
  const action = (a.proposed_action ?? {}).action;
  return typeof action === "string" ? action.replace(/_/g, " ") : "action";
}

// Every payload field that is NOT the action discriminator or the editable draft, rendered
// read-only so the reviewer sees recipient/deal/changes before approving.
function detailEntries(a: Approval): Array<[string, string]> {
  const pa = (a.proposed_action ?? {}) as Record<string, unknown>;
  const draftKey = draftKeyFor(a);
  return Object.entries(pa)
    .filter(([k]) => k !== "action" && k !== draftKey)
    .map(([k, v]) => [
      k.replace(/_/g, " "),
      typeof v === "string" ? v : JSON.stringify(v),
    ]);
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
  const [denyReasons, setDenyReasons] = useState<Record<number, string>>({});
  const [total, setTotal] = useState<number | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notEnabled, setNotEnabled] = useState(false);
  // Per-item decision errors: set when decide() fails for ONE item.
  // Keyed by approval id; kept separately from the page-level load error so a
  // failed decision leaves the queue intact and shows the error in-item.
  const [itemErrors, setItemErrors] = useState<Record<number, string>>({});
  // The last decision attempted per item, so a per-item retry repeats it faithfully.
  const [lastDecision, setLastDecision] = useState<Record<number, "approve" | "edit" | "deny">>({});
  const [busy, setBusy] = useState<Record<number, boolean>>({});
  const [toast, setToast] = useState<string | null>(null);
  const busyRef = useRef(busy);
  busyRef.current = busy;

  const showToast = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(null), 3500);
  }, []);

  const applyPage = useCallback((approvals: Approval[], opts: { keepDrafts: boolean }) => {
    setItems(approvals);
    setDrafts((d) => {
      const next: Record<number, string> = {};
      for (const a of approvals) {
        // A quiet refresh must never clobber an edit in progress — the user's text wins.
        next[a.id] = opts.keepDrafts && a.id in d ? d[a.id] : draftFromAction(a);
      }
      return next;
    });
  }, []);

  const load = useCallback(
    async (opts: { quiet?: boolean } = {}) => {
      if (!opts.quiet) {
        setLoading(true);
        setError(null);
      }
      try {
        const r = await api.listApprovals();
        applyPage(r.approvals, { keepDrafts: Boolean(opts.quiet) });
        setTotal(r.total_pending ?? r.approvals.length);
        setCursor(r.cursor ?? null);
        setNotEnabled(false);
        if (opts.quiet) setError(null);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) {
          // Feature detection (the SecurityControls pattern): the web can deploy ahead of the
          // control plane — say so honestly instead of a generic error.
          setNotEnabled(true);
        } else if (!opts.quiet) {
          setError(friendlyErrorMessage(e, "Couldn't load the queue. Please try again."));
        }
        // A failed QUIET poll changes nothing — the visible queue stays as-is.
      } finally {
        if (!opts.quiet) setLoading(false);
      }
    },
    [api, applyPage],
  );

  useEffect(() => {
    void load();
  }, [load]);

  // Quiet polling: new approvals appear without a manual refresh; in-flight decisions and
  // in-progress edits are never disturbed.
  useEffect(() => {
    const t = window.setInterval(() => {
      if (Object.values(busyRef.current).some(Boolean)) return;
      void load({ quiet: true });
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(t);
  }, [load]);

  const loadMore = useCallback(async () => {
    if (!cursor) return;
    try {
      const r = await api.listApprovals({ cursor });
      setItems((cur) => [...cur, ...r.approvals]);
      setDrafts((d) => {
        const next = { ...d };
        for (const a of r.approvals) if (!(a.id in next)) next[a.id] = draftFromAction(a);
        return next;
      });
      setCursor(r.cursor ?? null);
      if (r.total_pending !== undefined) setTotal(r.total_pending);
    } catch (e) {
      setError(friendlyErrorMessage(e, "Couldn't load more. Please try again."));
    }
  }, [api, cursor]);

  const decide = useCallback(
    async (a: Approval, decision: "approve" | "edit" | "deny") => {
      setBusy((b) => ({ ...b, [a.id]: true }));
      // Remember this item's decision so a per-item RETRY repeats the SAME action
      // (a failed deny must retry as deny, never silently become an approve).
      setLastDecision((p) => ({ ...p, [a.id]: decision }));
      // Clear any prior per-item error for this item before retrying.
      setItemErrors((prev) => { const n = { ...prev }; delete n[a.id]; return n; });
      try {
        const body =
          decision === "edit"
            ? { decision, edits: editsFor(a, drafts[a.id]) }
            : decision === "deny"
              ? { decision, deny_message: denyReasons[a.id]?.trim() || "Declined by reviewer." }
              : { decision };
        const decided = await api.decideApproval(a.id, body);
        // Removed only AFTER the server confirmed the decision (no optimistic lie).
        setItems((cur) => cur.filter((i) => i.id !== a.id));
        setTotal((t) => (t === null ? t : Math.max(0, t - 1)));
        // Honest outcome copy: an approved draft that performed nothing real must never
        // read as "sent" (send_email/issue_quote stay draft-only until provider go-live).
        const draftOnly = decided?.apply_result?.performed === false;
        showToast(
          decision === "deny"
            ? "Declined"
            : decision === "edit"
              ? draftOnly
                ? "Approved with edits — recorded as a draft (no real send yet)"
                : "Approved with edits"
              : draftOnly
                ? "Approved — recorded as a draft (no real send yet)"
                : "Approved and applied",
        );
      } catch (e) {
        if (
          e instanceof ApiError &&
          e.status === 400 &&
          /already |not pending|expired/.test(e.detail)
        ) {
          // Someone else decided it (or it expired) between render and click — say so
          // specifically and bring the queue back in sync.
          showToast("That one was already decided elsewhere (or expired) — refreshed the queue.");
          void load({ quiet: true });
        } else {
          // Per-item error: the item STAYS in the queue; the page-level error is
          // NOT set so the page-level "queue failed to load" card never shows.
          setItemErrors((prev) => ({
            ...prev,
            [a.id]: friendlyErrorMessage(e, "That decision didn't go through. Please try again."),
          }));
        }
      } finally {
        setBusy((b) => ({ ...b, [a.id]: false }));
      }
    },
    [api, drafts, denyReasons, load, showToast],
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
        {!loading && !error && !notEnabled && (
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 10 }}>
            <div data-testid="pending-count" style={{ fontSize: 13, color: "var(--ink-3, #8a8278)" }}>
              {total ?? items.length} pending
            </div>
            <button
              data-testid="gl-refresh"
              onClick={() => void load()}
              style={{ ...ghostBtn, padding: "3px 10px", fontSize: 12, color: "var(--ink-3, #8a8278)" }}
            >
              Refresh
            </button>
          </div>
        )}
      </div>

      {loading && <Spinner testid="gl-loading" label="Loading the queue..." />}

      {notEnabled && !loading && (
        <div data-testid="gl-not-enabled" style={{ ...card, color: "var(--ink-3, #8a8278)" }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: "var(--ink, #2a2622)" }}>Not yet enabled</div>
          <p style={{ fontSize: 13, marginTop: 4, lineHeight: 1.5 }}>
            Greenlight isn't enabled on this deployment yet — the control plane is still rolling out.
          </p>
        </div>
      )}

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

      {!loading && !error && !notEnabled && items.length === 0 && (
        <div data-testid="gl-empty" style={{ ...card, textAlign: "center", color: "var(--ink-3, #8a8278)" }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: "var(--ink, #2a2622)" }}>Inbox zero</div>
          <p style={{ fontSize: 13, marginTop: 4 }}>
            Nothing is waiting on you. When an agent proposes a send, a discount, or any
            side-effecting action, it lands here for your sign off.
          </p>
        </div>
      )}

      {items.map((a) => {
        const details = detailEntries(a);
        const hasDraft = draftKeyFor(a) !== null;
        return (
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
              {a.expires_at ? ` · expires ${new Date(a.expires_at).toLocaleDateString()}` : ""}
            </div>

            <div
              data-testid="approval-reasoning"
              style={{ fontSize: 13.5, color: "var(--ink, #2a2622)", marginTop: 12, lineHeight: 1.5, background: "var(--accent-soft, #f4f1ea)", borderRadius: 10, padding: "10px 12px" }}
            >
              <b style={{ fontWeight: 700 }}>Why: </b>
              {a.reasoning}
            </div>

            {details.length > 0 && (
              <div data-testid="approval-details" style={{ marginTop: 12, fontSize: 13, lineHeight: 1.6 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-3, #8a8278)", marginBottom: 4 }}>
                  What this will do
                </div>
                {details.map(([k, v]) => (
                  <div key={k} style={{ display: "flex", gap: 8 }}>
                    <span style={{ color: "var(--ink-3, #8a8278)", minWidth: 90, textTransform: "capitalize" }}>{k}</span>
                    <span style={{ color: "var(--ink, #2a2622)", overflowWrap: "anywhere" }}>{v}</span>
                  </div>
                ))}
              </div>
            )}

            {hasDraft && (
              <>
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
              </>
            )}

            <div style={{ display: "flex", gap: 8, marginTop: 14, alignItems: "center", flexWrap: "wrap" }}>
              <button
                data-testid="approve-btn"
                disabled={busy[a.id]}
                onClick={() => void decide(a, hasDraft && draftChanged(a, drafts[a.id]) ? "edit" : "approve")}
                style={primaryBtn}
              >
                {hasDraft && draftChanged(a, drafts[a.id]) ? "Approve edited" : "Approve"}
              </button>
              <button
                data-testid="deny-btn"
                disabled={busy[a.id]}
                onClick={() => void decide(a, "deny")}
                style={ghostBtn}
              >
                Deny
              </button>
              <input
                data-testid="deny-reason"
                type="text"
                placeholder="Reason (optional — helps the agent learn)"
                value={denyReasons[a.id] ?? ""}
                disabled={busy[a.id]}
                onChange={(e) => setDenyReasons((d) => ({ ...d, [a.id]: e.target.value }))}
                style={{
                  flex: "1 1 220px",
                  minWidth: 180,
                  border: "1px solid var(--line, #e3ddd3)",
                  borderRadius: 10,
                  padding: "7px 10px",
                  fontSize: 12.5,
                  fontFamily: "inherit",
                  color: "var(--ink, #2a2622)",
                }}
              />
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
                  onClick={() => void decide(a, lastDecision[a.id] ?? (hasDraft && draftChanged(a, drafts[a.id]) ? "edit" : "approve"))}
                  disabled={busy[a.id]}
                  style={{ ...ghostBtn, marginLeft: 8, padding: "4px 10px", fontSize: 12.5, color: "var(--ink, #2a2622)" }}
                >
                  Retry
                </button>
              </div>
            )}
          </div>
        );
      })}

      {!loading && !error && cursor && (
        <button data-testid="gl-load-more" onClick={() => void loadMore()} style={{ ...ghostBtn, color: "var(--ink, #2a2622)" }}>
          Load more
        </button>
      )}

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

// Build the edits patch: replace the draft-bearing field the action used. Actions with no
// draft field produce no edits (a novel key would be rejected by the server's edit guard).
function editsFor(a: Approval, current: string | undefined): Record<string, unknown> {
  const key = draftKeyFor(a);
  if (key === null) return {};
  return { [key]: current ?? "" };
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
