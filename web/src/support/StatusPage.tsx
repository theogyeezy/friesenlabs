// StatusPage — a lightweight, public status page (the ?view=status seam).
//
// Reads the public health signal (GET /healthz) via fetchStatus
// (web/src/support/api.ts) and renders an honest component-health summary:
//   - an overall roll-up badge,
//   - one row per component with an operational / degraded / down / unknown
//     state and a short note,
//   - a manual "Refresh" control.
//
// HONESTY: no secrets are read or shown; a failed probe degrades to "unknown"
// (never a fabricated green, never a thrown error). The API exposes a single
// liveness probe today — the page says so plainly rather than implying it
// individually checks every subsystem. As infra surfaces richer per-component
// readiness (see the PR notes / STATUS_COMPONENTS), it flows through the same
// fetchStatus shape with no change needed here.

import React from "react";

import { fetchStatus, type ProbeState, type StatusReport } from "./api";

const STATE_META: Record<ProbeState, { label: string; color: string; bg: string }> = {
  operational: { label: "Operational", color: "#1a7f4b", bg: "rgba(26,127,75,0.12)" },
  degraded: { label: "Degraded", color: "#b06a00", bg: "rgba(176,106,0,0.12)" },
  down: { label: "Down", color: "#c0392b", bg: "rgba(192,57,43,0.12)" },
  unknown: { label: "Unknown", color: "#6b6258", bg: "rgba(107,98,88,0.12)" },
};

const OVERALL_HEADLINE: Record<ProbeState, string> = {
  operational: "All systems operational",
  degraded: "Some systems may be degraded",
  down: "We're experiencing an outage",
  unknown: "Status is currently unavailable",
};

function Badge({ state }: { state: ProbeState }) {
  const m = STATE_META[state];
  return (
    <span
      data-testid={`status-badge-${state}`}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 7,
        fontSize: 12.5,
        fontWeight: 650,
        color: m.color,
        background: m.bg,
        padding: "3px 10px",
        borderRadius: 999,
      }}
    >
      <span
        aria-hidden="true"
        style={{ width: 8, height: 8, borderRadius: 999, background: m.color, display: "inline-block" }}
      />
      {m.label}
    </span>
  );
}

export default function StatusPage() {
  const [report, setReport] = React.useState<StatusReport | null>(null);
  const [loading, setLoading] = React.useState(true);

  const load = React.useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetchStatus();
      setReport(r);
    } catch {
      // fetchStatus already degrades to "unknown" internally; this guards any
      // unexpected throw so the page never crashes to a blank screen.
      setReport({
        overall: "unknown",
        components: [],
        checkedAt: new Date().toISOString(),
      });
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  const overall: ProbeState = report?.overall ?? "unknown";

  return (
    <main
      style={{
        maxWidth: 640,
        margin: "0 auto",
        padding: "56px 22px 80px",
        fontFamily: "system-ui, sans-serif",
      }}
      data-testid="status-page"
    >
      <p style={{ fontSize: 12, letterSpacing: ".08em", textTransform: "uppercase", color: "#8a8278", fontWeight: 650 }}>
        Status
      </p>
      <div style={{ display: "flex", alignItems: "center", gap: 14, margin: "6px 0 4px", flexWrap: "wrap" }}>
        <h1 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.02em" }} data-testid="status-headline">
          {OVERALL_HEADLINE[overall]}
        </h1>
        <Badge state={overall} />
      </div>
      <p style={{ fontSize: 13.5, color: "#6b6258", lineHeight: 1.6 }}>
        Live health of Friesen Labs. This page reads our public health check directly, so it
        reflects what's actually responding right now.
      </p>

      <div
        style={{
          marginTop: 24,
          border: "1px solid rgba(0,0,0,0.08)",
          borderRadius: 14,
          overflow: "hidden",
        }}
        data-testid="status-components"
      >
        {loading && !report ? (
          <div style={{ padding: 20, fontSize: 14, color: "#8a8278" }} data-testid="status-loading">
            Checking…
          </div>
        ) : (
          (report?.components ?? []).map((c, i) => (
            <div
              key={c.id}
              data-testid={`status-component-${c.id}`}
              style={{
                display: "flex",
                alignItems: "flex-start",
                justifyContent: "space-between",
                gap: 14,
                padding: "16px 18px",
                borderTop: i === 0 ? "none" : "1px solid rgba(0,0,0,0.06)",
              }}
            >
              <div>
                <div style={{ fontSize: 14.5, fontWeight: 650, color: "#2a2622" }}>{c.label}</div>
                <div style={{ fontSize: 13, color: "#6b6258", marginTop: 3, lineHeight: 1.5 }}>{c.note}</div>
              </div>
              <Badge state={c.state} />
            </div>
          ))
        )}
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 16, marginTop: 18, flexWrap: "wrap" }}>
        <button
          data-testid="status-refresh"
          onClick={() => void load()}
          disabled={loading}
          style={{
            padding: "8px 16px",
            borderRadius: 10,
            border: "1px solid rgba(0,0,0,0.12)",
            background: "#fff",
            color: "#2a2622",
            fontSize: 13,
            fontWeight: 600,
            cursor: loading ? "default" : "pointer",
          }}
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
        {report && (
          <span style={{ fontSize: 12.5, color: "#8a8278" }} data-testid="status-checked-at">
            Last checked {new Date(report.checkedAt).toLocaleTimeString()}
          </span>
        )}
      </div>

      <p style={{ fontSize: 12.5, color: "#8a8278", marginTop: 22, lineHeight: 1.6 }}>
        Per-component health for the agent, data, and ingest planes will appear here as we expose
        individual probes. Need help? <a href="/?view=help" style={{ color: "#b4541f" }}>Contact support</a>.
      </p>
      <p style={{ marginTop: 14 }}>
        <a href="/" style={{ fontSize: 13, color: "#8a8278" }}>Back to home</a>
      </p>
    </main>
  );
}
