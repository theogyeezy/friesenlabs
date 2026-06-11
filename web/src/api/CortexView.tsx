// Cortex ML health view, wired to the control-plane API via ApiClient — the
// real-mode counterpart of the FLStore CortexDemo prototype (src/screens/
// cortex.tsx, mock mode only). Follows the KnowledgeView / SecurityControls
// conventions exactly.
//
// Everything rendered here is honest about what the ML registry actually shows:
//
//   * The payload comes straight from GET /cortex/health: champion version +
//     estimator + registered training metrics + live-AUC drift verdict —
//     all from the server-side manifest, never generated client-side.
//   * "no_registry" => the S3 / local registry isn't wired for this deploy.
//     A calm degraded panel, NOT a green "active" card.
//   * "no_champion" => registry exists, this tenant has no trained model yet.
//     A calm empty state (model_count shown, champion card absent).
//   * "serving" / "drifting" => champion + metrics grid + drift verdict.
//     When drift.recent_auc is null (thin evidence) the reason is shown
//     honestly — a number is NEVER fabricated.
//     "drifting" gets a warning treatment (amber/rose tinting).
//   * A fetch failure or ApiError(404) (the route not yet deployed on the live
//     image) feature-detects like SecurityControls degrades on 404: an honest
//     "not yet available" note, never a fake working panel.
//   * Raw transport strings ("API <code>", server detail dumps) never reach the
//     DOM — every catch routes through friendlyErrorMessage.

import React from "react";
import {
  ApiClient,
  ApiError,
  defaultClient,
  friendlyErrorMessage,
  type CortexHealth,
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

const monoLabel: React.CSSProperties = {
  fontSize: 11.5,
  fontFamily: "var(--mono, ui-monospace, monospace)",
  fontWeight: 700,
  letterSpacing: ".02em",
};

function statusBadgeStyle(status: CortexHealth["status"]): React.CSSProperties {
  switch (status) {
    case "serving":
      return { ...badgeBase, background: "rgba(63, 143, 92, .12)", color: "var(--green, #2e7d4f)" };
    case "drifting":
      return { ...badgeBase, background: "rgba(200, 120, 40, .12)", color: "var(--amber, #b06000)" };
    case "no_champion":
      return { ...badgeBase, background: "var(--accent-soft, #f4f1ea)", color: "var(--ink-2, #5d564d)" };
    case "no_registry":
    default:
      return { ...badgeBase, background: "var(--accent-soft, #f4f1ea)", color: "var(--ink-2, #5d564d)" };
  }
}

function statusLabel(status: CortexHealth["status"]): string {
  switch (status) {
    case "serving": return "SERVING";
    case "drifting": return "DRIFTING";
    case "no_champion": return "NO MODEL";
    case "no_registry": return "DISABLED";
    default: return (status as string).toUpperCase();
  }
}

function fmtMetric(v: number): string {
  // Metrics like AUC, F1 are 0–1 fractions; render as 0.000 (3dp) so the
  // display is precise without being misleadingly "99.2%"-style.
  if (v >= 0 && v <= 1) return v.toFixed(3);
  return v.toLocaleString();
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function CortexView({ client }: { client?: ApiClient }) {
  const api = client ?? defaultClient();
  const [data, setData] = useState<CortexHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rollout, setRollout] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setRollout(false);
    try {
      setData(await api.getCortexHealth());
    } catch (e) {
      setData(null);
      if (e instanceof ApiError && e.status === 404) {
        setRollout(true);
      } else {
        setError(friendlyErrorMessage(e, "Couldn't load Cortex health. Please try again."));
      }
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div
      data-testid="cortex-view"
      style={{ maxWidth: 860, margin: "0 auto", padding: "32px 24px", fontFamily: "system-ui, sans-serif" }}
    >
      {/* Header */}
      <div style={{ marginBottom: 18 }}>
        <div style={{ fontSize: 12, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase", ...muted }}>
          Uplift Cortex
        </div>
        <h1 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.02em", margin: "6px 0 4px" }}>
          Cortex
        </h1>
        <p style={{ ...muted, fontSize: 14 }}>
          Your workspace&rsquo;s private ML layer — a model trained on your own outcomes, predicting
          which leads and deals are most likely to close. Read-only: training runs automatically.
        </p>
      </div>

      {loading && <Spinner testid="cortex-loading" label="Loading Cortex health..." />}

      {/* 404 feature-detect: the live API image predates /cortex/health — a calm note, not an error wall. */}
      {rollout && (
        <div data-testid="cortex-rollout" style={{ ...card, fontSize: 13.5 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Cortex health API is rolling out</div>
          <p style={{ ...muted, lineHeight: 1.5 }}>
            Your deployment doesn&rsquo;t serve the Cortex health endpoint yet — refresh after the
            next API deploy. Nothing is wrong with your workspace.
          </p>
          <button
            data-testid="cortex-rollout-refresh"
            onClick={() => void load()}
            style={{ ...ghostBtn, marginTop: 10 }}
          >
            Refresh
          </button>
        </div>
      )}

      {error && (
        <div
          data-testid="cortex-error"
          style={{ ...card, borderColor: "var(--rose, #b4413b)", fontSize: 13.5 }}
        >
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Something needs another try</div>
          <p style={{ ...muted, lineHeight: 1.5 }}>{error}</p>
          <button data-testid="cortex-retry" onClick={() => void load()} style={{ ...ghostBtn, marginTop: 10 }}>
            Try again
          </button>
        </div>
      )}

      {!loading && !error && !rollout && data !== null && (
        <>
          {/* no_registry: the ML registry isn't wired for this deployment */}
          {data.status === "no_registry" && (
            <div data-testid="cortex-no-registry" style={{ ...card, background: "var(--accent-soft, #f4f1ea)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 6 }}>
                <b style={{ fontSize: 15, fontWeight: 720 }}>Cortex isn&rsquo;t enabled for your workspace yet</b>
              </div>
              <p style={{ ...muted, fontSize: 13.5, lineHeight: 1.55, margin: 0 }}>
                The ML registry hasn&rsquo;t been configured on this deployment — Cortex will light up
                automatically once the model registry is wired to your workspace. There&rsquo;s nothing
                for you to do here right now.
              </p>
            </div>
          )}

          {/* no_champion: registry exists but no model trained for this tenant yet */}
          {data.status === "no_champion" && (
            <div data-testid="cortex-no-champion" style={{ ...card }}>
              <div style={{ display: "flex", alignItems: "center", gap: 9, flexWrap: "wrap", marginBottom: 6 }}>
                <b style={{ fontSize: 15, fontWeight: 720 }}>No model trained yet</b>
                <span style={statusBadgeStyle("no_champion")}>{statusLabel("no_champion")}</span>
              </div>
              <p style={{ ...muted, fontSize: 13.5, lineHeight: 1.55, margin: "0 0 8px" }}>
                Cortex is connected — your workspace registry is ready — but hasn&rsquo;t been trained yet.
                The first training run will happen automatically once your account has enough outcome
                history.
              </p>
              <div style={{ fontSize: 12.5, ...muted }}>
                <span style={monoLabel}>versions in registry:</span>{" "}
                <span data-testid="cortex-model-count">{data.model_count}</span>
              </div>
            </div>
          )}

          {/* serving / drifting: a champion is active */}
          {(data.status === "serving" || data.status === "drifting") && data.champion !== null && (
            <>
              {/* status header card */}
              <div
                data-testid="cortex-champion"
                style={{
                  ...card,
                  marginBottom: 12,
                  borderColor: data.status === "drifting"
                    ? "var(--amber, #b06000)"
                    : "var(--line, #e3ddd3)",
                  background: data.status === "drifting"
                    ? "rgba(200, 120, 40, .05)"
                    : "var(--surface, #fff)",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 10 }}>
                  <b style={{ fontSize: 15, fontWeight: 720 }}>Champion model</b>
                  <span data-testid="cortex-status-badge" style={statusBadgeStyle(data.status)}>
                    {statusLabel(data.status)}
                  </span>
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12, marginBottom: 12 }}>
                  <div>
                    <div style={{ ...monoLabel, ...muted, marginBottom: 2 }}>version</div>
                    <div data-testid="cortex-champion-version" style={{ fontSize: 14, fontWeight: 720 }}>
                      {data.champion.version}
                    </div>
                  </div>
                  <div>
                    <div style={{ ...monoLabel, ...muted, marginBottom: 2 }}>estimator</div>
                    <div data-testid="cortex-champion-estimator" style={{ fontSize: 14, fontWeight: 720 }}>
                      {data.champion.estimator}
                    </div>
                  </div>
                  <div>
                    <div style={{ ...monoLabel, ...muted, marginBottom: 2 }}>versions in registry</div>
                    <div data-testid="cortex-model-count" style={{ fontSize: 14, fontWeight: 720 }}>
                      {data.model_count}
                    </div>
                  </div>
                </div>

                {/* metrics grid — every key in the record, no fabrication */}
                {Object.keys(data.champion.metrics).length > 0 && (
                  <div>
                    <div style={{ ...monoLabel, ...muted, marginBottom: 6 }}>training metrics</div>
                    <div
                      data-testid="cortex-metrics"
                      style={{ display: "flex", flexWrap: "wrap", gap: 10 }}
                    >
                      {Object.entries(data.champion.metrics).map(([key, val]) => (
                        <div
                          key={key}
                          data-testid="cortex-metric"
                          data-metric={key}
                          style={{
                            padding: "7px 12px",
                            borderRadius: 10,
                            border: "1px solid var(--line, #e3ddd3)",
                            background: "var(--accent-soft, #f4f1ea)",
                          }}
                        >
                          <div style={{ ...monoLabel, ...muted, fontSize: 10.5, marginBottom: 2 }}>{key}</div>
                          <div style={{ fontSize: 16, fontWeight: 760 }}>{fmtMetric(val)}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              {/* drift verdict card */}
              {data.drift !== null && (
                <div
                  data-testid="cortex-drift"
                  style={{
                    ...card,
                    borderColor: data.drift.drift
                      ? "var(--rose, #b4413b)"
                      : data.status === "drifting"
                        ? "var(--amber, #b06000)"
                        : "var(--line, #e3ddd3)",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 8, flexWrap: "wrap" }}>
                    <b style={{ fontSize: 14, fontWeight: 720 }}>Live drift</b>
                    {data.drift.drift ? (
                      <span style={{ ...badgeBase, background: "rgba(180, 65, 59, .12)", color: "var(--rose, #b4413b)" }}>
                        DEGRADED
                      </span>
                    ) : data.drift.recent_auc !== null ? (
                      <span style={{ ...badgeBase, background: "rgba(63, 143, 92, .12)", color: "var(--green, #2e7d4f)" }}>
                        STABLE
                      </span>
                    ) : (
                      <span style={{ ...badgeBase, background: "var(--accent-soft, #f4f1ea)", color: "var(--ink-2, #5d564d)" }}>
                        PENDING
                      </span>
                    )}
                  </div>

                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 10 }}>
                    <div>
                      <div style={{ ...monoLabel, ...muted, fontSize: 10.5, marginBottom: 2 }}>live AUC</div>
                      <div data-testid="cortex-drift-auc" style={{ fontSize: 15, fontWeight: 720 }}>
                        {data.drift.recent_auc !== null
                          ? fmtMetric(data.drift.recent_auc)
                          : <span style={{ ...muted, fontSize: 13 }}>—</span>
                        }
                      </div>
                    </div>
                    <div>
                      <div style={{ ...monoLabel, ...muted, fontSize: 10.5, marginBottom: 2 }}>registered AUC</div>
                      <div style={{ fontSize: 15, fontWeight: 720 }}>
                        {fmtMetric(data.drift.registered_auc)}
                      </div>
                    </div>
                    <div>
                      <div style={{ ...monoLabel, ...muted, fontSize: 10.5, marginBottom: 2 }}>outcomes logged</div>
                      <div data-testid="cortex-drift-n" style={{ fontSize: 15, fontWeight: 720 }}>
                        {data.drift.n_outcomes.toLocaleString()}
                      </div>
                    </div>
                  </div>

                  {/* honest reason — shown when live_auc is null (thin evidence) */}
                  {data.drift.recent_auc === null && (
                    <p data-testid="cortex-drift-reason" style={{ ...muted, fontSize: 12.5, lineHeight: 1.5, margin: "10px 0 0" }}>
                      {data.drift.reason}
                    </p>
                  )}
                  {data.drift.drift && (
                    <p data-testid="cortex-drift-warning" style={{ color: "var(--rose, #b4413b)", fontSize: 12.5, lineHeight: 1.5, margin: "10px 0 0" }}>
                      Live accuracy has fallen below the registered baseline — a retrain is recommended.
                    </p>
                  )}
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}

export default CortexView;
