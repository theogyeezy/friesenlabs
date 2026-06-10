// Workflows view, wired to the control-plane API via ApiClient — the real-mode
// counterpart of the FLStore WorkflowBuilder prototype (mock mode only).
// Follows the AgentsRoster/ContactsDirectory conventions exactly. Everything
// rendered here is honest:
//
//   * The step diagram comes straight from GET /workflows: the OWNED
//     provisioning funnel (signup → verify → pay → provision → activate) the
//     state machine actually drives — serialized server-side from the owned
//     semantics, never invented client-side and never a live AWS Describe.
//     The descriptions tell the autonomy story inline: pay flips ONLY on the
//     signed Stripe webhook, provisioning is idempotent and parks on failure,
//     outbound email stays draft-gated, and everything an agent later does
//     that touches the outside world routes through Greenlight.
//   * Recent executions render with status badges ONLY when the API says they
//     are available. The feed carries name + status + timestamps — the API
//     strips ARNs/account ids server-side and never fetches run payloads.
//   * executions_available: false is an INFORMATIVE state, not an error: the
//     live api task holds states:StartExecution only until the REQ-009 read
//     grant lands (reason "pending IAM grant"), or the machine ARN simply
//     isn't wired on this deployment (reason "not configured"). The diagram
//     still renders; a calm banner explains the feed honestly.
//   * READ-ONLY by design: no run/retry/edit controls exist this cycle — the
//     UI promises nothing it can't keep.
//   * A 404 from /workflows means the live API image predates this route (the
//     web can deploy ahead of the API): a calm "rolling out" state with a
//     refresh affordance — NOT an error wall.
//   * Raw transport strings ("API <code>", server detail dumps) never reach
//     the DOM — every catch routes through friendlyErrorMessage.

import React from "react";
import {
  ApiClient,
  ApiError,
  defaultClient,
  friendlyErrorMessage,
  type WorkflowExecution,
  type WorkflowsResponse,
  type WorkflowStep,
} from "./client";
import { Spinner } from "./Spinner";

const { useState, useEffect, useCallback } = React;

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

const badgeBase: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  padding: "3px 10px",
  borderRadius: 999,
  fontSize: 11.5,
  fontWeight: 700,
  letterSpacing: ".02em",
  fontFamily: "var(--mono, ui-monospace, monospace)",
};

const dot = (color: string): React.CSSProperties => ({
  width: 6,
  height: 6,
  borderRadius: 999,
  background: color,
  flexShrink: 0,
});

// Status → badge palette. SUCCEEDED green, RUNNING amber (work in flight),
// FAILED/TIMED_OUT/ABORTED rose; anything unknown renders neutrally — the API
// passes statuses through verbatim and this view never invents one.
function badgeStyle(status: string | null): React.CSSProperties {
  switch (status) {
    case "SUCCEEDED":
      return { ...badgeBase, background: "rgba(63, 143, 92, .12)", color: "var(--green, #2e7d4f)" };
    case "RUNNING":
      return { ...badgeBase, background: "rgba(196, 138, 38, .14)", color: "var(--amber, #9a6b14)" };
    case "FAILED":
    case "TIMED_OUT":
    case "ABORTED":
      return { ...badgeBase, background: "rgba(180, 65, 59, .12)", color: "var(--rose, #b4413b)" };
    default:
      return { ...badgeBase, background: "var(--accent-soft, #f4f1ea)", color: "var(--ink-2, #5d564d)" };
  }
}

// "2026-06-09T12:00:00+00:00" → a compact local time, or "—" when absent.
function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

// The two honest unavailability reasons the API names (api/workflows_routes.py);
// anything else gets the generic temporary copy.
const REASON_PENDING_IAM = "pending IAM grant (REQ-009)";
const REASON_NOT_CONFIGURED = "not configured";

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface WorkflowsViewProps {
  client?: ApiClient;
  /** Navigate to the Greenlight queue (the shell passes navTo("approvals")).
   * Without it the legend's Greenlight link points at the ?view= seam. */
  onOpenGreenlight?: () => void;
}

export function WorkflowsView({ client, onOpenGreenlight }: WorkflowsViewProps) {
  const api = client ?? defaultClient();
  const [data, setData] = useState<WorkflowsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rollout, setRollout] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setRollout(false);
    try {
      setData(await api.getWorkflows());
    } catch (e) {
      setData(null);
      if (e instanceof ApiError && e.status === 404) {
        // The live API image predates /workflows (the web can deploy ahead of
        // the API): a calm rollout note, not an error wall.
        setRollout(true);
      } else {
        setError(friendlyErrorMessage(e, "Couldn't load your workflows. Please try again."));
      }
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  const stepCard = (s: WorkflowStep, i: number, last: boolean): React.ReactElement => (
    <React.Fragment key={s.id}>
      <div
        data-testid="workflow-step"
        data-step-id={s.id}
        style={{ ...card, display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div
            aria-hidden="true"
            style={{
              width: 30,
              height: 30,
              borderRadius: 10,
              flexShrink: 0,
              display: "grid",
              placeItems: "center",
              fontSize: 12.5,
              fontWeight: 750,
              fontFamily: "var(--mono, ui-monospace, monospace)",
              background: "var(--accent-soft, #f4f1ea)",
              color: "var(--ink, #2a2622)",
            }}
          >
            {i + 1}
          </div>
          <span style={{ fontSize: 14.5, fontWeight: 750, color: "var(--ink, #2a2622)" }}>
            {s.label}
          </span>
        </div>
        <p style={{ fontSize: 12.5, lineHeight: 1.55, color: "var(--ink-2, #5d564d)", margin: 0 }}>
          {s.description}
        </p>
      </div>
      {!last && (
        <div
          aria-hidden="true"
          data-testid="workflow-step-arrow"
          style={{
            display: "grid",
            placeItems: "center",
            color: "var(--ink-4, #b3aa9d)",
            fontSize: 16,
            fontWeight: 700,
            padding: "0 2px",
          }}
        >
          →
        </div>
      )}
    </React.Fragment>
  );

  const executionRow = (e: WorkflowExecution, i: number): React.ReactElement => (
    <div
      key={`${e.name ?? "run"}-${i}`}
      data-testid="execution-row"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        flexWrap: "wrap",
        padding: "11px 0",
        borderTop: i === 0 ? "none" : "1px solid var(--line-2, #efe9df)",
      }}
    >
      <span
        style={{
          flex: 1,
          minWidth: 160,
          fontSize: 13,
          fontWeight: 650,
          fontFamily: "var(--mono, ui-monospace, monospace)",
          color: "var(--ink, #2a2622)",
          overflowWrap: "anywhere",
        }}
      >
        {e.name ?? "(unnamed run)"}
      </span>
      <span data-testid="execution-status" data-status={e.status ?? "UNKNOWN"} style={badgeStyle(e.status)}>
        <span aria-hidden="true" style={dot("currentColor")} />
        {e.status ?? "UNKNOWN"}
      </span>
      <span style={{ fontSize: 12, fontFamily: "var(--mono, ui-monospace, monospace)", ...muted }}>
        {fmtTime(e.started_at)}
        {" → "}
        {e.status === "RUNNING" ? "running" : fmtTime(e.stopped_at)}
      </span>
    </div>
  );

  // The honest "feed unavailable" banner — INFORMATIVE, never error styling.
  const unavailableBanner = (reason: string | null): React.ReactElement => {
    const pendingIam = reason === REASON_PENDING_IAM;
    const notConfigured = reason === REASON_NOT_CONFIGURED;
    return (
      <div
        data-testid={pendingIam ? "executions-pending-iam" : "executions-unavailable"}
        data-reason={reason ?? ""}
        style={{ ...card, background: "var(--accent-soft, #f4f1ea)", fontSize: 13.5 }}
      >
        <div style={{ fontWeight: 700, marginBottom: 4, display: "flex", alignItems: "center", gap: 8 }}>
          <span aria-hidden="true" style={dot("var(--amber, #9a6b14)")} />
          {pendingIam ? "Run history is almost here" : "Run history isn't wired up yet"}
        </div>
        <p style={{ ...muted, lineHeight: 1.55, margin: 0 }}>
          {pendingIam
            ? "The pipeline below is exactly what runs your workspace — but reading its live " +
              "run feed needs one more (queued) read permission on our side. Nothing is " +
              "wrong, and nothing here is hidden from you on purpose: the feed lights up " +
              "the moment the grant lands."
            : notConfigured
              ? "This deployment doesn't have the run feed connected yet. The pipeline below " +
                "is still the real automation that provisions every workspace."
              : "The run feed is temporarily unavailable. The pipeline below is unaffected — " +
                "try again in a moment."}
        </p>
      </div>
    );
  };

  return (
    <div
      data-testid="workflows-view"
      style={{ maxWidth: 980, margin: "0 auto", padding: "32px 24px", fontFamily: "system-ui, sans-serif" }}
    >
      <div style={{ marginBottom: 18 }}>
        <div style={{ fontSize: 12, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase", ...muted }}>
          Uplift workflows
        </div>
        <h1 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.02em", margin: "6px 0 4px" }}>Workflows</h1>
        <p style={{ ...muted, fontSize: 14 }}>
          The automation that actually runs your workspace — from signup to a live agent crew.
          Read-only today: you&rsquo;re seeing the real machine, not a builder.
        </p>
      </div>

      {loading && <Spinner testid="workflows-loading" label="Loading your workflows..." />}

      {/* The live API image may predate /workflows: a calm rollout note, not an error wall. */}
      {rollout && (
        <div data-testid="workflows-rollout" style={{ ...card, color: "var(--ink, #2a2622)", fontSize: 13.5 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Workflows API is rolling out</div>
          <p style={{ ...muted, lineHeight: 1.5 }}>
            Your deployment doesn&rsquo;t serve the workflows endpoint yet — refresh after the
            next API deploy. Nothing is wrong with your workspace.
          </p>
          <button data-testid="workflows-rollout-refresh" onClick={() => void load()} style={{ ...ghostBtn, marginTop: 10 }}>
            Refresh
          </button>
        </div>
      )}

      {error && (
        <div
          data-testid="workflows-error"
          style={{ ...card, borderColor: "var(--rose, #b4413b)", color: "var(--ink, #2a2622)", fontSize: 13.5 }}
        >
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Something needs another try</div>
          <p style={{ ...muted, lineHeight: 1.5 }}>{error}</p>
          <button data-testid="workflows-retry" onClick={() => void load()} style={{ ...ghostBtn, marginTop: 10 }}>
            Try again
          </button>
        </div>
      )}

      {!loading && !error && !rollout && data !== null && (
        <>
          {/* the machine header: display name + what it is — never an ARN */}
          <div
            data-testid="workflow-machine"
            style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", fontSize: 13, marginBottom: 14 }}
          >
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 7,
                padding: "5px 12px",
                borderRadius: 999,
                background: "var(--accent-soft, #f4f1ea)",
                color: "var(--ink, #2a2622)",
                fontWeight: 700,
                fontFamily: "var(--mono, ui-monospace, monospace)",
              }}
            >
              <span aria-hidden="true" style={dot("var(--green, #2e7d4f)")} />
              {data.machine.name}
            </span>
            <span style={{ ...muted }}>
              the provisioning pipeline — every Uplift workspace is built by this machine
            </span>
          </div>

          {/* the step diagram: 5 owned steps, in funnel order */}
          <div
            data-testid="workflow-steps"
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr) auto)",
              alignItems: "stretch",
              gap: 10,
              marginBottom: 14,
            }}
          >
            {data.steps.map((s, i) => stepCard(s, i, i === data.steps.length - 1))}
          </div>

          {/* the autonomy legend — the same promise the Agents tab makes */}
          <div
            data-testid="workflow-legend"
            style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", fontSize: 12.5, marginBottom: 18, ...muted }}
          >
            <span aria-hidden="true" style={dot("var(--amber, #9a6b14)")} />
            Steps that touch the outside world stay draft-gated, and once your crew is live every
            outbound action waits for your sign-off in{" "}
            {onOpenGreenlight ? (
              <button
                data-testid="legend-greenlight"
                onClick={onOpenGreenlight}
                style={{ ...ghostBtn, padding: "1px 8px", fontSize: 12, borderRadius: 8 }}
              >
                Greenlight
              </button>
            ) : (
              <a data-testid="legend-greenlight" href="/?view=greenlight" style={{ color: "inherit" }}>
                Greenlight
              </a>
            )}
          </div>

          {/* recent executions — or the honest unavailable banner */}
          <h2 style={{ fontSize: 16, fontWeight: 750, letterSpacing: "-.01em", margin: "0 0 10px" }}>
            Recent runs
          </h2>
          {data.executions_available ? (
            data.recent_executions.length === 0 ? (
              <div data-testid="executions-empty" style={{ ...card, fontSize: 13.5, ...muted }}>
                No runs yet — this machine fires when a new workspace is provisioned.
              </div>
            ) : (
              <div data-testid="executions-list" style={{ ...card, paddingTop: 7, paddingBottom: 7 }}>
                {data.recent_executions.map(executionRow)}
              </div>
            )
          ) : (
            unavailableBanner(data.reason)
          )}
        </>
      )}
    </div>
  );
}

export default WorkflowsView;
