// Security & control surface, wired to the control-plane API via ApiClient —
// the real-mode counterpart of the FLStore Security prototype (mock mode only).
//
// Everything here is HONEST about what's actually wired:
//   * The kill switch and the autonomy dial reflect real server state
//     (GET /control/killswitch, GET /control/autonomy) and PUT real changes.
//     No optimistic lie: a toggle only flips after the server confirms; a
//     failed write reverts and surfaces friendly copy.
//   * FEATURE DETECTION: if an endpoint answers 404 (the web can deploy ahead
//     of the control plane), the control renders DISABLED with a "not yet
//     enabled" tooltip instead of a fake working toggle. Each control degrades
//     independently — the kill switch can be live while traces are still
//     rolling out.
//   * Decision traces (GET /control/traces) are READ-ONLY and degrade the same
//     way; the list never invents a row.
//   * Raw transport strings never reach the DOM — catches route through
//     friendlyErrorMessage.

import React from "react";
import {
  ApiClient,
  ApiError,
  defaultClient,
  friendlyErrorMessage,
  type AutonomyLevel,
  type AutonomyState,
  type DecisionTrace,
  type KillswitchState,
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

const dot = (color: string): React.CSSProperties => ({
  width: 6,
  height: 6,
  borderRadius: 999,
  background: color,
  flexShrink: 0,
});

const NOT_ENABLED_TOOLTIP =
  "Not yet enabled on this deployment — the control plane is still rolling out.";

// The autonomy ladder, the same story the research card tells: a graduated
// on-ramp, not a binary switch.
const AUTONOMY_LEVELS: { level: AutonomyLevel; label: string; desc: string }[] = [
  { level: 0, label: "Suggest only", desc: "Agents draft, never act. You send everything." },
  { level: 1, label: "Ask first", desc: "Agents act only after you approve each action." },
  { level: 2, label: "Autonomous", desc: "Agents act within your guardrails; risky moves still ask." },
  { level: 3, label: "Full autonomy", desc: "Agents act on their own; the kill switch is your stop." },
];

// A control's load state. "unavailable" is the 404 feature-detect degrade.
type Avail = "loading" | "ready" | "unavailable" | "error";

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

function traceBadge(status: string | null): React.CSSProperties {
  switch ((status ?? "").toLowerCase()) {
    case "executed":
    case "approved":
      return { ...badgeBase, background: "rgba(63, 143, 92, .12)", color: "var(--green, #2e7d4f)" };
    case "blocked":
    case "denied":
      return { ...badgeBase, background: "rgba(180, 65, 59, .12)", color: "var(--rose, #b4413b)" };
    default:
      return { ...badgeBase, background: "var(--accent-soft, #f4f1ea)", color: "var(--ink-2, #5d564d)" };
  }
}

// A small honest "rolling out" note reused by each degraded control.
function NotEnabledNote({ testid }: { testid: string }): React.ReactElement {
  return (
    <p data-testid={testid} style={{ ...muted, fontSize: 12.5, lineHeight: 1.5, margin: "8px 0 0" }}>
      Not yet enabled on this deployment — this control lights up the moment the control plane is wired.
    </p>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface SecurityControlsProps {
  client?: ApiClient;
}

export function SecurityControls({ client }: SecurityControlsProps) {
  const api = client ?? defaultClient();

  // Kill switch
  const [ksAvail, setKsAvail] = useState<Avail>("loading");
  const [killswitch, setKillswitch] = useState<KillswitchState | null>(null);
  const [ksSaving, setKsSaving] = useState(false);
  const [ksError, setKsError] = useState<string | null>(null);

  // Autonomy
  const [autoAvail, setAutoAvail] = useState<Avail>("loading");
  const [autonomy, setAutonomy] = useState<AutonomyState | null>(null);
  const [autoSaving, setAutoSaving] = useState(false);
  const [autoError, setAutoError] = useState<string | null>(null);

  // Traces
  const [trAvail, setTrAvail] = useState<Avail>("loading");
  const [traces, setTraces] = useState<DecisionTrace[]>([]);

  // Load all three independently — one being unavailable never blocks another.
  const loadKillswitch = useCallback(async () => {
    setKsAvail("loading");
    setKsError(null);
    try {
      setKillswitch(await api.getKillswitch());
      setKsAvail("ready");
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setKsAvail("unavailable");
      else {
        setKsError(friendlyErrorMessage(e, "Couldn't load the kill switch. Please try again."));
        setKsAvail("error");
      }
    }
  }, [api]);

  const loadAutonomy = useCallback(async () => {
    setAutoAvail("loading");
    setAutoError(null);
    try {
      setAutonomy(await api.getAutonomy());
      setAutoAvail("ready");
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setAutoAvail("unavailable");
      else {
        setAutoError(friendlyErrorMessage(e, "Couldn't load autonomy. Please try again."));
        setAutoAvail("error");
      }
    }
  }, [api]);

  const loadTraces = useCallback(async () => {
    setTrAvail("loading");
    try {
      setTraces(await api.getControlTraces(50));
      setTrAvail("ready");
    } catch (e) {
      // Traces are read-only context; any failure (404 or otherwise) degrades
      // to the honest "not enabled" note rather than an error wall.
      setTrAvail(e instanceof ApiError && e.status === 404 ? "unavailable" : "error");
    }
  }, [api]);

  useEffect(() => {
    void loadKillswitch();
    void loadAutonomy();
    void loadTraces();
  }, [loadKillswitch, loadAutonomy, loadTraces]);

  const toggleKillswitch = useCallback(async () => {
    if (!killswitch || ksSaving) return;
    const next = !killswitch.engaged;
    setKsSaving(true);
    setKsError(null);
    try {
      setKillswitch(await api.setKillswitch(next));
    } catch (e) {
      setKsError(friendlyErrorMessage(e, "Couldn't update the kill switch. Please try again."));
    } finally {
      setKsSaving(false);
    }
  }, [api, killswitch, ksSaving]);

  const chooseAutonomy = useCallback(
    async (level: AutonomyLevel) => {
      if (autoSaving || (autonomy && autonomy.level === level)) return;
      setAutoSaving(true);
      setAutoError(null);
      try {
        setAutonomy(await api.setAutonomy(level));
      } catch (e) {
        setAutoError(friendlyErrorMessage(e, "Couldn't update autonomy. Please try again."));
      } finally {
        setAutoSaving(false);
      }
    },
    [api, autonomy, autoSaving],
  );

  const engaged = killswitch?.engaged ?? false;
  const ksDisabled = ksAvail !== "ready" || ksSaving;

  return (
    <div
      data-testid="security-controls"
      style={{ maxWidth: 820, margin: "0 auto", padding: "32px 24px", fontFamily: "system-ui, sans-serif" }}
    >
      <div style={{ marginBottom: 18 }}>
        <div style={{ fontSize: 12, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase", ...muted }}>
          Workspace security
        </div>
        <h1 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.02em", margin: "6px 0 4px" }}>
          Security &amp; control
        </h1>
        <p style={{ ...muted, fontSize: 14 }}>
          You&rsquo;re always in charge. Stop every agent in one tap, set how much they can do on
          their own, and review the decisions they&rsquo;ve made.
        </p>
      </div>

      {/* --- Kill switch -------------------------------------------------- */}
      <div data-testid="killswitch-card" style={{ ...card, marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
          <div style={{ flex: 1, minWidth: 200 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
              <span aria-hidden="true" style={dot(engaged ? "var(--rose, #b4413b)" : "var(--green, #2e7d4f)")} />
              <b style={{ fontSize: 15, fontWeight: 720 }}>Kill switch</b>
              {ksAvail === "ready" && (
                <span data-testid="killswitch-state" style={traceBadge(engaged ? "blocked" : "executed")}>
                  {engaged ? "ENGAGED" : "LIVE"}
                </span>
              )}
            </div>
            <p style={{ ...muted, fontSize: 12.5, lineHeight: 1.5, margin: "6px 0 0" }}>
              {engaged
                ? "A full stop. No agent reads, analyzes, or acts until you switch it back on."
                : "Agents are running within your guardrails. Flip this to stop everything at once."}
            </p>
          </div>

          {ksAvail === "loading" ? (
            <Spinner testid="killswitch-loading" label="Loading…" />
          ) : (
            <button
              data-testid="killswitch-toggle"
              role="switch"
              aria-checked={engaged}
              aria-label="Kill switch"
              disabled={ksDisabled}
              title={ksAvail === "ready" ? undefined : NOT_ENABLED_TOOLTIP}
              onClick={() => void toggleKillswitch()}
              style={{
                width: 56,
                height: 32,
                borderRadius: 999,
                border: "none",
                position: "relative",
                cursor: ksDisabled ? "not-allowed" : "pointer",
                opacity: ksAvail === "ready" ? 1 : 0.5,
                background: engaged ? "var(--rose, #b4413b)" : "var(--line, #e3ddd3)",
                transition: "background .15s",
                flexShrink: 0,
              }}
            >
              <span
                aria-hidden="true"
                style={{
                  position: "absolute",
                  top: 3,
                  left: engaged ? 27 : 3,
                  width: 26,
                  height: 26,
                  borderRadius: "50%",
                  background: "#fff",
                  transition: "left .15s",
                  boxShadow: "0 1px 3px rgba(0,0,0,.2)",
                }}
              />
            </button>
          )}
        </div>
        {ksAvail === "unavailable" && <NotEnabledNote testid="killswitch-unavailable" />}
        {ksAvail === "error" && (
          <div style={{ marginTop: 10 }}>
            <p style={{ color: "var(--rose, #b4413b)", fontSize: 12.5, margin: "0 0 8px" }}>{ksError}</p>
            <button data-testid="killswitch-retry" onClick={() => void loadKillswitch()} style={ghostBtn}>
              Try again
            </button>
          </div>
        )}
        {ksAvail === "ready" && ksError && (
          <p data-testid="killswitch-error" style={{ color: "var(--rose, #b4413b)", fontSize: 12.5, margin: "10px 0 0" }}>
            {ksError}
          </p>
        )}
      </div>

      {/* --- Autonomy dial ----------------------------------------------- */}
      <div data-testid="autonomy-card" style={{ ...card, marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 4 }}>
          <b style={{ fontSize: 15, fontWeight: 720 }}>Autonomy</b>
          <span style={{ ...muted, fontSize: 12.5 }}>applies to every agent at once</span>
        </div>

        {autoAvail === "loading" ? (
          <Spinner testid="autonomy-loading" label="Loading…" />
        ) : autoAvail === "error" ? (
          <div style={{ marginTop: 8 }}>
            <p style={{ color: "var(--rose, #b4413b)", fontSize: 12.5, margin: "0 0 8px" }}>{autoError}</p>
            <button data-testid="autonomy-retry" onClick={() => void loadAutonomy()} style={ghostBtn}>
              Try again
            </button>
          </div>
        ) : (
          <>
            <div
              role="radiogroup"
              aria-label="Autonomy level"
              data-testid="autonomy-dial"
              style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 8, marginTop: 10 }}
            >
              {AUTONOMY_LEVELS.map((opt) => {
                const selected = autoAvail === "ready" && autonomy?.level === opt.level;
                const disabled = autoAvail !== "ready" || autoSaving;
                return (
                  <button
                    key={opt.level}
                    data-testid={`autonomy-${opt.level}`}
                    role="radio"
                    aria-checked={selected}
                    disabled={disabled}
                    title={autoAvail === "ready" ? undefined : NOT_ENABLED_TOOLTIP}
                    onClick={() => void chooseAutonomy(opt.level)}
                    style={{
                      textAlign: "left",
                      padding: "12px 14px",
                      borderRadius: 12,
                      cursor: disabled ? "not-allowed" : "pointer",
                      opacity: autoAvail === "ready" ? 1 : 0.5,
                      border: selected ? "2px solid var(--accent, #2a2622)" : "1px solid var(--line, #e3ddd3)",
                      background: selected ? "var(--accent-soft, #f4f1ea)" : "var(--surface, #fff)",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
                      <span style={{ fontFamily: "var(--mono, monospace)", fontSize: 11, fontWeight: 700, ...muted }}>
                        L{opt.level}
                      </span>
                      <b style={{ fontSize: 13.5, fontWeight: 700 }}>{opt.label}</b>
                    </div>
                    <p style={{ ...muted, fontSize: 11.5, lineHeight: 1.4, margin: "5px 0 0" }}>{opt.desc}</p>
                  </button>
                );
              })}
            </div>
            {autoAvail === "unavailable" && <NotEnabledNote testid="autonomy-unavailable" />}
            {autoAvail === "ready" && autoError && (
              <p data-testid="autonomy-error" style={{ color: "var(--rose, #b4413b)", fontSize: 12.5, margin: "10px 0 0" }}>
                {autoError}
              </p>
            )}
          </>
        )}
      </div>

      {/* --- Decision traces (read-only) --------------------------------- */}
      <div data-testid="traces-card" style={{ ...card }}>
        <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 10 }}>
          <b style={{ fontSize: 15, fontWeight: 720 }}>Decision traces</b>
          <span style={{ ...muted, fontSize: 12.5 }}>the latest actions your agents decided on</span>
        </div>

        {trAvail === "loading" && <Spinner testid="traces-loading" label="Loading decisions…" />}
        {trAvail === "unavailable" && <NotEnabledNote testid="traces-unavailable" />}
        {trAvail === "error" && (
          <p data-testid="traces-error" style={{ ...muted, fontSize: 12.5, margin: 0 }}>
            The decision feed is temporarily unavailable — try refreshing in a moment.
          </p>
        )}
        {trAvail === "ready" &&
          (traces.length === 0 ? (
            <p data-testid="traces-empty" style={{ ...muted, fontSize: 13, margin: 0 }}>
              No decisions logged yet. They&rsquo;ll appear here as your agents work.
            </p>
          ) : (
            <div data-testid="traces-list">
              {traces.map((t, i) => (
                <div
                  key={t.id}
                  data-testid="trace-row"
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
                      minWidth: 140,
                      fontSize: 13,
                      fontWeight: 650,
                      fontFamily: "var(--mono, ui-monospace, monospace)",
                      color: "var(--ink, #2a2622)",
                      overflowWrap: "anywhere",
                    }}
                  >
                    {t.tool ?? "(tool)"}
                  </span>
                  <span style={{ fontSize: 12.5, ...muted }}>{t.decision ?? "—"}</span>
                  <span data-testid="trace-status" data-status={t.status ?? ""} style={traceBadge(t.status)}>
                    {(t.status ?? "unknown").toUpperCase()}
                  </span>
                  <span style={{ fontSize: 12, fontFamily: "var(--mono, ui-monospace, monospace)", ...muted }}>
                    {fmtTime(t.ts)}
                  </span>
                </div>
              ))}
            </div>
          ))}
      </div>
    </div>
  );
}

export default SecurityControls;
