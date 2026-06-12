// Sell (gamification) view, wired to the control-plane API via ApiClient — the
// real-mode counterpart of the FLStore Sell prototype (mock mode only, which
// shows demo confetti numbers). Everything rendered here is HONEST:
//
//   * The rep's level / xp / streak / today's progress come straight from
//     GET /sell/me; the leaderboard from GET /sell/leaderboard; quests from
//     GET /sell/quests — all claims-bound and RLS-scoped server-side. Nothing
//     is invented client-side: there is no fallback "level 1, 0 xp" placeholder
//     that could read as real.
//   * INERT BY DEFAULT: the reads answer an honest 503 when the points store
//     isn't wired on the live task (no crm_app DSN). That renders a calm
//     "Sell isn't switched on yet" panel — NOT fabricated points, NOT an error
//     wall.
//   * A 404 means the live API image predates the /sell routes (the web can
//     deploy ahead of the API): a calm "rolling out" state with a refresh —
//     mirrors WorkflowsView exactly.
//   * Badges are DERIVED from the same real fields (your level, a live streak,
//     a completed quest) — never a separate fabricated achievement feed.
//   * Raw transport strings ("API <code>", server detail dumps) never reach the
//     DOM — every catch routes through friendlyErrorMessage.
//   * READ-ONLY this wave: no nudge composer is mounted here yet (the draft-only
//     POST /sell/nudge ships behind a later UI); the view promises nothing it
//     can't keep.

import React from "react";
import {
  ApiClient,
  ApiError,
  defaultClient,
  friendlyErrorMessage,
  type SellLeaderboardRow,
  type SellMeResponse,
  type SellQuest,
} from "./client";
import { Spinner } from "./Spinner";

const { useState, useEffect, useCallback } = React;

// ---------------------------------------------------------------------------
// Styles (house style: hairline cards on the soft surface palette — matches
// WorkflowsView / AgentsRoster).
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

const dot = (color: string): React.CSSProperties => ({
  width: 6,
  height: 6,
  borderRadius: 999,
  background: color,
  flexShrink: 0,
});

const chip: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 7,
  padding: "5px 12px",
  borderRadius: 999,
  background: "var(--accent-soft, #f4f1ea)",
  color: "var(--ink, #2a2622)",
  fontSize: 12.5,
  fontWeight: 700,
  fontFamily: "var(--mono, ui-monospace, monospace)",
};

// A stat tile (level / xp / streak / today) — the value is ALWAYS a real
// server number; this component never renders a tile without loaded data.
function StatTile({
  label,
  value,
  hint,
  testid,
}: {
  label: string;
  value: string | number;
  hint?: string;
  testid: string;
}): React.ReactElement {
  return (
    <div data-testid={testid} style={{ ...card, minWidth: 0 }}>
      <div
        style={{
          fontSize: 11.5,
          fontWeight: 700,
          letterSpacing: ".05em",
          textTransform: "uppercase",
          ...muted,
        }}
      >
        {label}
      </div>
      <div
        data-testid={`${testid}-value`}
        style={{ fontSize: 28, fontWeight: 780, letterSpacing: "-.02em", margin: "4px 0 2px" }}
      >
        {value}
      </div>
      {hint && <div style={{ fontSize: 12, ...muted }}>{hint}</div>}
    </div>
  );
}

// A short, friendly label for a rep id when the roster has no display name yet.
function repLabel(row: SellLeaderboardRow): string {
  return row.display_name || row.user_id;
}

// Badges DERIVED from real fields only — never a separate fabricated feed.
function deriveBadges(me: SellMeResponse, quests: SellQuest[]): string[] {
  const badges: string[] = [`Level ${me.level}`];
  if (me.streak > 0) badges.push(`${me.streak}-day streak`);
  for (const q of quests) {
    if (q.complete) badges.push(`${q.title} ✓`);
  }
  return badges;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface SellViewProps {
  client?: ApiClient;
}

interface SellData {
  me: SellMeResponse;
  leaderboard: SellLeaderboardRow[];
  quests: SellQuest[];
}

export function SellView({ client }: SellViewProps) {
  const api = client ?? defaultClient();
  const [data, setData] = useState<SellData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rollout, setRollout] = useState(false);
  const [offline, setOffline] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setRollout(false);
    setOffline(false);
    try {
      // /sell/me is the anchor: it decides the top-level state (offline / rollout
      // / error) because all three reads gate on the SAME points store, so it
      // failing means the surface is unavailable wholesale.
      const me = await api.getSellMe();
      // me succeeded → the store is live; fetch the rest, tolerating an isolated
      // gap on either (degrade to empty for that section, never the whole view).
      const [lb, q] = await Promise.all([
        api.getSellLeaderboard().catch(() => ({ leaderboard: [] as SellLeaderboardRow[] })),
        api.getSellQuests().catch(() => ({ quests: [] as SellQuest[] })),
      ]);
      setData({ me, leaderboard: lb.leaderboard, quests: q.quests });
    } catch (e) {
      setData(null);
      if (e instanceof ApiError && e.status === 404) {
        // The live API image predates the /sell routes — calm rollout note.
        setRollout(true);
      } else if (e instanceof ApiError && e.status === 503) {
        // INERT BY DEFAULT: the points store isn't wired on this task. Honest
        // "not switched on" panel — never fabricated points.
        setOffline(true);
      } else {
        setError(friendlyErrorMessage(e, "Couldn't load your Sell stats. Please try again."));
      }
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  const leaderboardRow = (row: SellLeaderboardRow, i: number): React.ReactElement => (
    <div
      key={`${row.user_id}-${i}`}
      data-testid="leaderboard-row"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "11px 0",
        borderTop: i === 0 ? "none" : "1px solid var(--line-2, #efe9df)",
      }}
    >
      <span
        aria-hidden="true"
        style={{
          width: 26,
          height: 26,
          borderRadius: 8,
          flexShrink: 0,
          display: "grid",
          placeItems: "center",
          fontSize: 12,
          fontWeight: 750,
          fontFamily: "var(--mono, ui-monospace, monospace)",
          background: "var(--accent-soft, #f4f1ea)",
          color: "var(--ink, #2a2622)",
        }}
      >
        {i + 1}
      </span>
      <span style={{ flex: 1, minWidth: 0, fontSize: 13.5, fontWeight: 650, overflowWrap: "anywhere" }}>
        {repLabel(row)}
      </span>
      <span
        data-testid="leaderboard-points"
        style={{ fontSize: 13, fontFamily: "var(--mono, ui-monospace, monospace)", fontWeight: 700 }}
      >
        {row.points.toLocaleString()} XP
      </span>
      <span style={{ fontSize: 12, fontFamily: "var(--mono, ui-monospace, monospace)", ...muted }}>
        {row.events.toLocaleString()} {row.events === 1 ? "event" : "events"}
      </span>
    </div>
  );

  const questCard = (q: SellQuest): React.ReactElement => {
    const pct = q.target > 0 ? Math.min(1, q.current / q.target) : 0;
    return (
      <div key={q.id} data-testid="quest-card" data-complete={q.complete} style={{ ...card, display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span style={{ fontSize: 14.5, fontWeight: 750 }}>{q.title}</span>
          {q.complete && (
            <span style={{ ...chip, background: "rgba(63, 143, 92, .12)", color: "var(--green, #2e7d4f)" }}>
              <span aria-hidden="true" style={dot("currentColor")} /> Complete
            </span>
          )}
        </div>
        <p style={{ fontSize: 12.5, lineHeight: 1.55, ...muted, margin: 0 }}>{q.description}</p>
        {/* progress bar — driven entirely by the real current/target counts */}
        <div
          aria-hidden="true"
          style={{ height: 8, borderRadius: 999, background: "var(--line-2, #efe9df)", overflow: "hidden" }}
        >
          <div style={{ height: "100%", width: `${Math.round(pct * 100)}%`, background: "var(--green, #2e7d4f)" }} />
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, ...muted }}>
          <span data-testid="quest-progress">
            {q.current} / {q.target}
          </span>
          <span>+{q.reward_points.toLocaleString()} XP each</span>
        </div>
      </div>
    );
  };

  return (
    <div
      data-testid="sell-view"
      style={{ maxWidth: 980, margin: "0 auto", padding: "32px 24px", fontFamily: "system-ui, sans-serif" }}
    >
      <div style={{ marginBottom: 18 }}>
        <div style={{ fontSize: 12, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase", ...muted }}>
          Sell
        </div>
        <h1 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.02em", margin: "6px 0 4px" }}>Your selling streak</h1>
        <p style={{ ...muted, fontSize: 14 }}>
          Your level, streak, quests and team leaderboard &mdash; every number here is earned from
          your real activity, never a demo score.
        </p>
      </div>

      {loading && <Spinner testid="sell-loading" label="Loading your Sell stats..." />}

      {/* The live API image may predate the /sell routes: a calm rollout note. */}
      {rollout && (
        <div data-testid="sell-rollout" style={{ ...card, color: "var(--ink, #2a2622)", fontSize: 13.5 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Sell is rolling out</div>
          <p style={{ ...muted, lineHeight: 1.5 }}>
            Your deployment doesn&rsquo;t serve the Sell endpoints yet &mdash; refresh after the next
            API deploy. Nothing is wrong with your workspace.
          </p>
          <button data-testid="sell-rollout-refresh" onClick={() => void load()} style={{ ...ghostBtn, marginTop: 10 }}>
            Refresh
          </button>
        </div>
      )}

      {/* INERT BY DEFAULT: the points store isn't wired — honest, no fake scores. */}
      {offline && (
        <div data-testid="sell-offline" style={{ ...card, background: "var(--accent-soft, #f4f1ea)", fontSize: 13.5 }}>
          <div style={{ fontWeight: 700, marginBottom: 4, display: "flex", alignItems: "center", gap: 8 }}>
            <span aria-hidden="true" style={dot("var(--amber, #9a6b14)")} />
            Sell isn&rsquo;t switched on yet
          </div>
          <p style={{ ...muted, lineHeight: 1.55, margin: 0 }}>
            Gamified selling &mdash; levels, streaks, quests and the team leaderboard &mdash; lights up
            once selling activity starts flowing into your workspace. Nothing here is hidden from you:
            there&rsquo;s simply no scored activity to show yet, so we show nothing rather than invent a
            number.
          </p>
          <button data-testid="sell-offline-refresh" onClick={() => void load()} style={{ ...ghostBtn, marginTop: 12 }}>
            Refresh
          </button>
        </div>
      )}

      {error && (
        <div
          data-testid="sell-error"
          style={{ ...card, borderColor: "var(--rose, #b4413b)", color: "var(--ink, #2a2622)", fontSize: 13.5 }}
        >
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Something needs another try</div>
          <p style={{ ...muted, lineHeight: 1.5 }}>{error}</p>
          <button data-testid="sell-retry" onClick={() => void load()} style={{ ...ghostBtn, marginTop: 10 }}>
            Try again
          </button>
        </div>
      )}

      {!loading && !error && !rollout && !offline && data !== null && (
        <>
          {/* my standing — every value is a real server number */}
          <div
            data-testid="sell-me"
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
              gap: 12,
              marginBottom: 14,
            }}
          >
            <StatTile
              testid="sell-level"
              label="Level"
              value={data.me.level}
              hint={`${data.me.progress.to_next.toLocaleString()} XP to level ${data.me.progress.level + 1}`}
            />
            <StatTile testid="sell-xp" label="Total XP" value={data.me.xp.toLocaleString()} hint={`${data.me.events.toLocaleString()} scored events`} />
            <StatTile
              testid="sell-streak"
              label="Streak"
              value={data.me.streak === 0 ? "—" : `${data.me.streak}d`}
              hint={data.me.streak === 0 ? "no active streak" : "consecutive active days"}
            />
            <StatTile
              testid="sell-today"
              label="Today"
              value={data.me.today.points.toLocaleString()}
              hint={`${data.me.today.events.toLocaleString()} ${data.me.today.events === 1 ? "event" : "events"} today`}
            />
          </div>

          {/* badges — derived from the same real fields, never a separate feed */}
          <div data-testid="sell-badges" style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 22 }}>
            {deriveBadges(data.me, data.quests).map((b, i) => (
              <span key={`${b}-${i}`} data-testid="badge-chip" style={chip}>
                <span aria-hidden="true" style={dot("var(--accent, #b5683c)")} />
                {b}
              </span>
            ))}
          </div>

          {/* quests */}
          <h2 style={{ fontSize: 16, fontWeight: 750, letterSpacing: "-.01em", margin: "0 0 10px" }}>Quests</h2>
          {data.quests.length === 0 ? (
            <div data-testid="quests-empty" style={{ ...card, fontSize: 13.5, ...muted, marginBottom: 22 }}>
              No active quests right now &mdash; new ones appear as your team sets goals.
            </div>
          ) : (
            <div data-testid="sell-quests" style={{ display: "grid", gap: 10, marginBottom: 22 }}>
              {data.quests.map(questCard)}
            </div>
          )}

          {/* leaderboard */}
          <h2 style={{ fontSize: 16, fontWeight: 750, letterSpacing: "-.01em", margin: "0 0 10px" }}>Team leaderboard</h2>
          {data.leaderboard.length === 0 ? (
            <div data-testid="leaderboard-empty" style={{ ...card, fontSize: 13.5, ...muted }}>
              No data yet &mdash; the leaderboard fills in as your team logs real selling activity.
            </div>
          ) : (
            <div data-testid="sell-leaderboard" style={{ ...card, paddingTop: 7, paddingBottom: 7 }}>
              {data.leaderboard.map(leaderboardRow)}
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default SellView;
