// Agents crew roster, wired to the control-plane API via ApiClient — the
// real-mode counterpart of the FLStore AgentsConsole prototype (mock mode
// only). Follows the ContactsDirectory/PipelineBoard conventions exactly.
// Everything rendered here is honest:
//
//   * The crew comes straight from GET /agents: the 7 specialists + the
//     coordinator exactly as signup provisioning assembles them (the OWNED
//     roster definitions server-side — names, specialties, duty descriptions,
//     tool lists). Nothing is invented client-side.
//   * Every tool chip carries the server-side registry's TRUSTED policy — the
//     autonomy story made visible: green = "runs on its own" (read-only,
//     auto), amber = "asks first" (side-effecting; every action routes
//     through Greenlight for human sign-off).
//   * A provisioned tenant gets the live badge with the TRUNCATED environment
//     id tail (the API never sends full Managed Agents ids); an unprovisioned
//     tenant sees the same crew with the honest "assembles at signup" state —
//     never a fake "online" claim. No live agent status is shown at all (that
//     arrives with the worker); the provisioning row is the only truth here.
//   * READ-ONLY by design: no pause/configure controls exist this cycle — the
//     UI promises nothing it can't keep.
//   * A 404 from /agents means the live API image predates this route (the
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
  type AgentCrewResponse,
  type CrewAgent,
  type CrewCoordinator,
} from "./client";
import { Spinner } from "./Spinner";

const { useState, useEffect, useCallback } = React;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** "scout" -> "Scout", "uplift-orchestrator" -> "Uplift orchestrator". */
function displayName(name: string): string {
  const clean = name.replace(/[-_]+/g, " ").trim();
  return clean ? clean[0].toUpperCase() + clean.slice(1) : name;
}

function initials(name: string): string {
  return displayName(name)
    .split(" ")
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

const AUTO_LABEL = "runs on its own";
const ASK_LABEL = "asks first";

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

const chipBase: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 5,
  padding: "3px 10px",
  borderRadius: 999,
  fontSize: 11.5,
  fontWeight: 650,
  fontFamily: "var(--mono, ui-monospace, monospace)",
};

// Green = auto (read-only; executes and returns results). Amber = always_ask
// (side-effecting; the gate routes every action to Greenlight, nothing runs
// without a human). The palette mirrors the house --green/--amber tokens.
const chipAuto: React.CSSProperties = {
  ...chipBase,
  background: "rgba(63, 143, 92, .12)",
  color: "var(--green, #2e7d4f)",
};

const chipAsk: React.CSSProperties = {
  ...chipBase,
  background: "rgba(196, 138, 38, .14)",
  color: "var(--amber, #9a6b14)",
};

const dot = (color: string): React.CSSProperties => ({
  width: 6,
  height: 6,
  borderRadius: 999,
  background: color,
  flexShrink: 0,
});

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface AgentsRosterProps {
  client?: ApiClient;
  /** Navigate to the Greenlight queue (the shell passes navTo("approvals")).
   * Without it the legend's Greenlight link points at the ?view= seam. */
  onOpenGreenlight?: () => void;
}

export function AgentsRoster({ client, onOpenGreenlight }: AgentsRosterProps) {
  const api = client ?? defaultClient();
  const [crew, setCrew] = useState<AgentCrewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rollout, setRollout] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setRollout(false);
    try {
      setCrew(await api.getAgentCrew());
    } catch (e) {
      setCrew(null);
      if (e instanceof ApiError && e.status === 404) {
        // The live API image predates /agents (the web can deploy ahead of
        // the API): a calm rollout note, not an error wall.
        setRollout(true);
      } else {
        setError(friendlyErrorMessage(e, "Couldn't load your agent crew. Please try again."));
      }
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  const toolChip = (t: { name: string; policy: string }, i: number): React.ReactElement => {
    const ask = t.policy === "always_ask";
    return (
      <span
        key={`${t.name}-${i}`}
        data-testid="tool-chip"
        data-tool={t.name}
        data-policy={t.policy}
        title={ask ? `${t.name}: ${ASK_LABEL} — every action goes to Greenlight` : `${t.name}: ${AUTO_LABEL} (read-only)`}
        style={ask ? chipAsk : chipAuto}
      >
        <span aria-hidden="true" style={dot("currentColor")} />
        {t.name}
        {ask && <span style={{ fontWeight: 600, opacity: 0.85 }}>· {ASK_LABEL}</span>}
      </span>
    );
  };

  const agentCard = (a: CrewAgent | CrewCoordinator): React.ReactElement => {
    const isCoord = a.is_coordinator;
    const idTail = "id_tail" in a ? a.id_tail : null;
    return (
      <div
        key={a.name}
        data-testid={isCoord ? "coordinator-card" : "agent-card"}
        data-agent-name={a.name}
        style={{
          ...card,
          borderColor: isCoord ? "var(--accent, #2a2622)" : "var(--line, #e3ddd3)",
          borderWidth: isCoord ? 1.5 : 1,
          gridColumn: isCoord ? "1 / -1" : undefined,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
          <div
            aria-hidden="true"
            style={{
              width: 38,
              height: 38,
              borderRadius: 12,
              flexShrink: 0,
              display: "grid",
              placeItems: "center",
              fontSize: 13,
              fontWeight: 750,
              background: isCoord ? "var(--accent, #2a2622)" : "var(--accent-soft, #f4f1ea)",
              color: isCoord ? "#fff" : "var(--ink, #2a2622)",
            }}
          >
            {initials(a.name)}
          </div>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span style={{ fontSize: 14.5, fontWeight: 750, color: "var(--ink, #2a2622)" }}>
                {displayName(a.name)}
              </span>
              {isCoord && (
                <span
                  data-testid="coordinator-tag"
                  style={{
                    fontSize: 10.5,
                    fontWeight: 700,
                    letterSpacing: ".05em",
                    textTransform: "uppercase",
                    padding: "2px 9px",
                    borderRadius: 999,
                    background: "var(--accent, #2a2622)",
                    color: "#fff",
                  }}
                >
                  Coordinator
                </span>
              )}
            </div>
            <div style={{ fontSize: 12.5, ...muted }}>{a.role}</div>
          </div>
          {isCoord && idTail && (
            <span
              data-testid="coordinator-id-tail"
              title="The short tail of your coordinator's id (the full id never leaves the server)."
              style={{ fontSize: 11.5, fontFamily: "var(--mono, ui-monospace, monospace)", ...muted }}
            >
              agent …{idTail}
            </span>
          )}
        </div>
        <p style={{ fontSize: 13, lineHeight: 1.55, color: "var(--ink-2, #5d564d)", margin: "0 0 10px" }}>
          {a.description}
        </p>
        {a.tools.length === 0 ? (
          <div data-testid="no-tools" style={{ fontSize: 12, ...muted }}>
            {isCoord
              ? "Delegates to the specialists — no tools of its own."
              : "No custom tools — works from the conversation alone."}
          </div>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>{a.tools.map(toolChip)}</div>
        )}
      </div>
    );
  };

  return (
    <div
      data-testid="agents-roster"
      style={{ maxWidth: 980, margin: "0 auto", padding: "32px 24px", fontFamily: "system-ui, sans-serif" }}
    >
      <div style={{ marginBottom: 18 }}>
        <div style={{ fontSize: 12, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase", ...muted }}>
          Uplift agents
        </div>
        <h1 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.02em", margin: "6px 0 4px" }}>Agents</h1>
        <p style={{ ...muted, fontSize: 14 }}>
          Your always-on team — seven specialists and a coordinator, assembled for your workspace.
          Anything that touches the outside world asks first in Greenlight.
        </p>
      </div>

      {loading && <Spinner testid="crew-loading" label="Loading your crew..." />}

      {/* The live API image may predate /agents: a calm rollout note, not an error wall. */}
      {rollout && (
        <div data-testid="crew-rollout" style={{ ...card, color: "var(--ink, #2a2622)", fontSize: 13.5 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Agents API is rolling out</div>
          <p style={{ ...muted, lineHeight: 1.5 }}>
            Your deployment doesn&rsquo;t serve the agents endpoint yet — refresh after the next
            API deploy. Nothing is wrong with your crew.
          </p>
          <button data-testid="crew-rollout-refresh" onClick={() => void load()} style={{ ...ghostBtn, marginTop: 10 }}>
            Refresh
          </button>
        </div>
      )}

      {error && (
        <div
          data-testid="crew-error"
          style={{ ...card, borderColor: "var(--rose, #b4413b)", color: "var(--ink, #2a2622)", fontSize: 13.5 }}
        >
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Something needs another try</div>
          <p style={{ ...muted, lineHeight: 1.5 }}>{error}</p>
          <button data-testid="crew-retry" onClick={() => void load()} style={{ ...ghostBtn, marginTop: 10 }}>
            Try again
          </button>
        </div>
      )}

      {!loading && !error && !rollout && crew !== null && (
        <>
          {/* provisioned badge / assembles-at-signup state */}
          {crew.provisioned ? (
            <div
              data-testid="crew-provisioned"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                flexWrap: "wrap",
                fontSize: 13,
                marginBottom: 14,
              }}
            >
              <span
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 7,
                  padding: "5px 12px",
                  borderRadius: 999,
                  background: "rgba(63, 143, 92, .12)",
                  color: "var(--green, #2e7d4f)",
                  fontWeight: 700,
                }}
              >
                <span aria-hidden="true" style={dot("currentColor")} />
                Crew provisioned
              </span>
              {crew.environment_id_tail && (
                <span
                  data-testid="crew-env-tail"
                  title="The short tail of your private environment's id (the full id never leaves the server)."
                  style={{ fontFamily: "var(--mono, ui-monospace, monospace)", fontSize: 12, ...muted }}
                >
                  environment …{crew.environment_id_tail}
                </span>
              )}
            </div>
          ) : (
            <div data-testid="crew-unprovisioned" style={{ ...card, marginBottom: 16, fontSize: 13.5 }}>
              <div style={{ fontWeight: 700, marginBottom: 4 }}>Your crew assembles at signup</div>
              <p style={{ ...muted, lineHeight: 1.55, margin: 0 }}>
                This is the team every Uplift workspace gets. When your workspace finishes
                provisioning, these eight agents are created for your tenant — until then,
                nothing here claims to be running.
              </p>
            </div>
          )}

          {/* the autonomy legend — what the chip colors promise */}
          <div
            data-testid="policy-legend"
            style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap", fontSize: 12.5, marginBottom: 16, ...muted }}
          >
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <span aria-hidden="true" style={dot("var(--green, #2e7d4f)")} />
              <b style={{ color: "var(--ink, #2a2622)", fontWeight: 650 }}>{AUTO_LABEL}</b> — read-only, executes directly
            </span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <span aria-hidden="true" style={dot("var(--amber, #9a6b14)")} />
              <b style={{ color: "var(--ink, #2a2622)", fontWeight: 650 }}>{ASK_LABEL}</b> — every action needs your sign-off in{" "}
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
            </span>
          </div>

          {/* the crew grid: coordinator first (full-width, distinguished), then the 7 */}
          <div
            data-testid="crew-grid"
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
              gap: 14,
            }}
          >
            {agentCard(crew.coordinator)}
            {crew.roster.map((a) => agentCard(a))}
          </div>
        </>
      )}
    </div>
  );
}

export default AgentsRoster;
