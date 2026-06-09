// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// sell.jsx — gamified sales hub: streaks, daily goals, quests, leaderboard, power hour, badges

function PowerHourBtn() {
  const g = useStore((s) => s.gamify);
  const [, tick] = useState(0);
  useEffect(() => { const iv = setInterval(() => tick((x) => x + 1), 1000); return () => clearInterval(iv); }, []);
  const active = g.powerHourEndsAt && Date.now() < g.powerHourEndsAt;
  if (active) {
    const left = Math.max(0, g.powerHourEndsAt - Date.now());
    const mm = String(Math.floor(left / 60000)).padStart(2, "0");
    const ss = String(Math.floor((left % 60000) / 1000)).padStart(2, "0");
    return <span className="chip amber" style={{ height: 38, padding: "0 14px", fontFamily: "var(--mono)", fontWeight: 700 }}><Icon name="bolt" size={15} />{g.multiplier}× · {mm}:{ss}</span>;
  }
  return <button className="btn btn-soft" onClick={() => FLStore.startPowerHour()}><Icon name="bolt" size={16} />Start power hour</button>;
}

function Sell({ agents, onOpenDeal, onNavigate, gamifyOn = true }) {
  const points = useStore((s) => s.points);
  const g = useStore((s) => s.gamify);
  const deals = useStore((s) => s.deals);
  const team = useStore((s) => s.team);
  const last = useStore((s) => s.lastAward);
  const prevK = useRef(null);
  const [view, setView] = useState("desk");
  const { NUDGES, WEEKLY_RECAP } = window.FL_DATA;
  const [dismissed, setDismissed] = useState({});
  const [recap, setRecap] = useState(false);
  const nudges = NUDGES.filter((n) => !dismissed[n.id]);
  const doNudge = (n) => {
    if (n.action === "followup") { FLStore.addPoints(8, { kind: "followup" }); FLStore.pushFeed({ agent: n.agent, ico: n.ico, tone: n.tone, html: "Acting on a next-best-action nudge", meta: "just now · you approved" }); }
    setDismissed((d) => ({ ...d, [n.id]: true }));
  };

  // celebrate quest completions
  useEffect(() => {
    if (last && last.k !== prevK.current) {
      prevK.current = last.k;
      if (last.celebrate && window.confettiBurst) window.confettiBurst(window.innerWidth / 2, 180);
    }
  }, [last]);

  const level = Math.floor(points / 500) + 1;
  const into = points % 500, pct = into / 500 * 100;
  const wins = deals.filter((d) => d.stage === "won").length;
  const goalPct = Math.min(100, Math.round(g.goalDone / g.goalTarget * 100));

  // leaderboard: you + teammates + top agents, by points
  const board = [
    { name: "You", kind: "you", pts: points, color: "var(--accent)", init: "JR" },
    ...team.filter((m) => m.kind === "human" && m.name !== "Jordan Reyes").slice(0, 3).map((m, i) => ({ name: m.name, kind: "human", pts: Math.round(points * (0.92 - i * 0.18)), color: m.color, init: m.init })),
    ...Object.values(agents).slice(0, 2).map((a, i) => ({ name: a.name + " (agent)", kind: "agent", pts: Math.round(points * (0.8 - i * 0.25)), color: a.color, init: a.init })),
  ].sort((a, b) => b.pts - a.pts);

  const badges = [
    { emoji: "🎯", label: "First close", earned: wins >= 1 },
    { emoji: "🔥", label: `${g.streak}-day streak`, earned: g.streak >= 3 },
    { emoji: "💸", label: "Closer (5 wins)", earned: wins >= 5 },
    { emoji: "⚡", label: "Power user", earned: points >= 1500 },
    { emoji: "🏆", label: "Level 5", earned: level >= 5 },
    { emoji: "📈", label: "On a roll", earned: g.goalDone >= 10 },
  ];

  return (
    <div className="screen screen-anim">
      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 7 }}>{view === "desk" ? "Track the team" : "Make selling fun"}</div>
          <h2 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.03em" }}>Sell</h2>
          <p style={{ color: "var(--ink-2)", fontSize: 14.5, marginTop: 5 }}>{view === "desk" ? "Revenue, close rates, calls and reports across your reps and agents." : "Every follow-up, every close earns points. Keep your streak alive and top the board."}</p>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 9, alignItems: "center", flexWrap: "wrap" }}>
          {gamifyOn && (
            <div className="sd-toggle">
              <button className={view === "desk" ? "active" : ""} onClick={() => setView("desk")}>Sales desk</button>
              <button className={view === "motivation" ? "active" : ""} onClick={() => setView("motivation")}>Motivation</button>
            </div>
          )}
          {view === "motivation" && <button className="btn btn-ghost" onClick={() => setRecap(true)}><Icon name="trend" size={16} />Weekly recap</button>}
          {view === "motivation" && <PowerHourBtn />}
        </div>
      </div>

      {view === "desk" && <SalesDesk agents={agents} onOpenDeal={onOpenDeal} onNavigate={onNavigate} />}

      {view === "motivation" && (<React.Fragment>
      {/* agent next-best-action nudges */}
      {nudges.length > 0 && (
        <div className="card" style={{ marginBottom: "var(--gap)", overflow: "hidden" }}>
          <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="spark" size={15} /></div><h3>Your agents suggest</h3><span className="sub" style={{ marginLeft: "auto" }}>next best actions</span></div>
          <div style={{ display: "flex", gap: 12, padding: "14px var(--pad)", overflowX: "auto" }}>
            {nudges.map((n) => {
              const a = agents[n.agent]; const TT = { indigo: ["var(--accent-soft)", "var(--accent-ink)"], amber: ["var(--amber-soft)", "oklch(0.5 0.12 60)"], green: ["var(--green-soft)", "oklch(0.42 0.12 152)"], rose: ["var(--rose-soft)", "oklch(0.48 0.14 18)"] }; const [bg, fg] = TT[n.tone] || TT.indigo;
              return (
                <div key={n.id} style={{ minWidth: 270, flex: "0 0 auto", border: "1px solid var(--line)", borderRadius: "var(--r-md)", padding: 14, background: "var(--surface)", display: "flex", flexDirection: "column", gap: 10 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
                    <div className="avatar" style={{ background: a ? a.color : "var(--accent)", width: 26, height: 26, fontSize: 11 }}>{a ? a.init : "✦"}</div>
                    <b style={{ fontSize: 12.5, fontWeight: 650 }}>{a ? a.name : "Agent"}</b>
                    <button className="icon-btn" style={{ width: 24, height: 24, marginLeft: "auto" }} onClick={() => setDismissed((d) => ({ ...d, [n.id]: true }))}><Icon name="x" size={14} /></button>
                  </div>
                  <p style={{ fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.45, flex: 1 }}>{n.text}</p>
                  <button className="btn btn-soft btn-sm" style={{ justifyContent: "center" }} onClick={() => doNudge(n)}><Icon name={n.ico} size={13} />{n.cta}</button>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* hero row: level + streak + daily goal */}
      <div className="rg3" style={{ marginBottom: "var(--gap)" }}>
        <div className="card card-pad" style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div style={{ position: "relative", width: 64, height: 64, flexShrink: 0 }}>
            <svg width="64" height="64" viewBox="0 0 64 64" style={{ transform: "rotate(-90deg)" }}>
              <circle cx="32" cy="32" r="27" fill="none" stroke="var(--surface-2)" strokeWidth="6" />
              <circle cx="32" cy="32" r="27" fill="none" stroke="var(--accent)" strokeWidth="6" strokeLinecap="round" strokeDasharray={`${2 * Math.PI * 27 * pct / 100} ${2 * Math.PI * 27}`} style={{ transition: "stroke-dasharray .6s" }} />
            </svg>
            <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", fontSize: 18, fontWeight: 800 }}>{level}</div>
          </div>
          <div>
            <div style={{ fontSize: 12.5, color: "var(--ink-3)", fontWeight: 600 }}>Level {level}</div>
            <div style={{ fontSize: 20, fontWeight: 780, letterSpacing: "-.02em" }}>{points.toLocaleString()} <span style={{ fontSize: 13, color: "var(--ink-3)", fontWeight: 600 }}>pts</span></div>
            <div style={{ fontSize: 11.5, color: "var(--ink-4)", marginTop: 2 }}>{500 - into} to level {level + 1}</div>
          </div>
        </div>
        <div className="card card-pad" style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div style={{ fontSize: 42, lineHeight: 1 }}>🔥</div>
          <div>
            <div style={{ fontSize: 12.5, color: "var(--ink-3)", fontWeight: 600 }}>Daily streak</div>
            <div style={{ fontSize: 26, fontWeight: 800, letterSpacing: "-.03em" }}>{g.streak} days</div>
            <div style={{ fontSize: 11.5, color: "var(--ink-4)", marginTop: 2 }}>Act today to keep it alive</div>
          </div>
        </div>
        <div className="card card-pad">
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
            <span style={{ fontSize: 12.5, color: "var(--ink-3)", fontWeight: 600 }}>Today's goal</span>
            <span style={{ fontSize: 12.5, fontFamily: "var(--mono)", fontWeight: 700 }}>{g.goalDone}/{g.goalTarget}</span>
          </div>
          <div className="meter" style={{ height: 11 }}><span style={{ width: goalPct + "%", background: goalPct >= 100 ? "var(--green)" : "var(--accent)" }} /></div>
          <div style={{ fontSize: 11.5, color: "var(--ink-4)", marginTop: 9 }}>{goalPct >= 100 ? "🎉 Goal smashed! Bonus points unlocked." : `${g.goalTarget - g.goalDone} more actions to hit your goal`}</div>
        </div>
      </div>

      <div className="dash-grid">
        {/* quests */}
        <div className="card">
          <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="target" size={15} /></div><h3>Today's quests</h3><span className="sub" style={{ marginLeft: "auto" }}>{g.quests.filter((q) => q.done).length}/{g.quests.length} done</span></div>
          <div className="card-pad" style={{ display: "flex", flexDirection: "column", gap: 11 }}>
            {g.quests.map((q) => (
              <div key={q.id} style={{ display: "flex", alignItems: "center", gap: 13, padding: "12px 14px", borderRadius: "var(--r-md)", border: "1px solid var(--line)", background: q.done ? "var(--green-soft)" : "var(--surface)", opacity: q.done ? 0.85 : 1 }}>
                <div style={{ width: 30, height: 30, borderRadius: 99, display: "grid", placeItems: "center", background: q.done ? "var(--green)" : "var(--accent-softer)", color: q.done ? "#fff" : "var(--accent-ink)", flexShrink: 0 }}>{q.done ? <Icon name="check" size={15} sw={3} /> : <Icon name="target" size={15} />}</div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <b style={{ fontSize: 13.5, fontWeight: 650 }}>{q.label}</b>
                  <div className="meter" style={{ height: 6, marginTop: 6 }}><span style={{ width: (q.progress / q.goal * 100) + "%", background: q.done ? "var(--green)" : "var(--accent)" }} /></div>
                </div>
                <span className="chip amber" style={{ height: 22 }}>+{q.reward}</span>
              </div>
            ))}
            <p style={{ fontSize: 11.5, color: "var(--ink-4)", textAlign: "center", marginTop: 2 }}>Quests advance as you work deals in Uplift. Complete one for a bonus.</p>
          </div>
        </div>

        {/* leaderboard + badges */}
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--gap)" }}>
          <div className="card">
            <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--amber-soft)", color: "oklch(0.5 0.12 60)" }}><Icon name="trophy" size={15} /></div><h3>Leaderboard</h3><span className="sub" style={{ marginLeft: "auto" }}>this week</span></div>
            <div className="card-pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {board.map((r, i) => (
                <div key={r.name} style={{ display: "flex", alignItems: "center", gap: 11, padding: "9px 11px", borderRadius: "var(--r-sm)", background: r.kind === "you" ? "var(--accent-softer)" : "transparent" }}>
                  <span style={{ width: 20, fontWeight: 800, fontFamily: "var(--mono)", fontSize: 13, color: i === 0 ? "var(--amber)" : "var(--ink-4)", textAlign: "center" }}>{i === 0 ? "🥇" : i + 1}</span>
                  <div className="avatar" style={{ background: r.color, width: 28, height: 28, fontSize: 11 }}>{r.init}</div>
                  <b style={{ fontSize: 13, fontWeight: r.kind === "you" ? 750 : 600, flex: 1 }}>{r.name}</b>
                  <span style={{ fontSize: 13, fontWeight: 700, fontFamily: "var(--mono)" }}>{r.pts.toLocaleString()}</span>
                </div>
              ))}
            </div>
          </div>
          <div className="card">
            <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="spark" size={15} /></div><h3>Badges</h3></div>
            <div className="card-pad" style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10 }}>
              {badges.map((b) => (
                <div key={b.label} style={{ textAlign: "center", padding: "13px 6px", borderRadius: "var(--r-md)", border: "1px solid var(--line)", background: b.earned ? "var(--surface)" : "var(--surface-2)", opacity: b.earned ? 1 : 0.45, filter: b.earned ? "none" : "grayscale(1)" }}>
                  <div style={{ fontSize: 26 }}>{b.emoji}</div>
                  <div style={{ fontSize: 11, fontWeight: 600, marginTop: 5, lineHeight: 1.25 }}>{b.label}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
      </React.Fragment>)}

      {recap && (() => { const R = WEEKLY_RECAP; const ta = agents[R.topAgent];
        return (
          <div className="cmdk-scrim show" onClick={() => setRecap(false)} style={{ alignItems: "center", paddingTop: 0 }}>
            <div onClick={(e) => e.stopPropagation()} style={{ width: "min(560px, 94vw)", maxHeight: "86vh", overflowY: "auto", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-xl)", boxShadow: "var(--shadow-xl)", animation: "onb-in .3s both" }}>
              <div style={{ padding: "22px 24px", borderBottom: "1px solid var(--line)", background: "var(--accent-softer)" }}>
                <div className="eyebrow" style={{ marginBottom: 6 }}>Weekly recap · {R.range}</div>
                <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
                  <div style={{ fontSize: 30 }}>🎉</div>
                  <div style={{ flex: 1 }}><h2 style={{ fontSize: 21, fontWeight: 760, letterSpacing: "-.02em" }}>{R.headline}</h2></div>
                  <button className="icon-btn" onClick={() => setRecap(false)}><Icon name="x" size={18} /></button>
                </div>
              </div>
              <div style={{ padding: 22 }}>
                <div className="rg2" style={{ gap: 12, marginBottom: 18 }}>
                  {R.stats.map((s) => (
                    <div key={s.label} style={{ padding: 14, border: "1px solid var(--line)", borderRadius: "var(--r-md)", background: "var(--surface)" }}>
                      <div style={{ fontSize: 22, fontWeight: 780, letterSpacing: "-.03em" }}>{s.val}</div>
                      <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 3 }}>
                        <span style={{ fontSize: 12, color: "var(--ink-3)", flex: 1 }}>{s.label}</span>
                        <span style={{ fontSize: 11.5, fontWeight: 700, color: s.up ? "var(--green)" : "var(--rose)" }}>{s.delta}</span>
                      </div>
                    </div>
                  ))}
                </div>
                <div className="so-section-label" style={{ marginBottom: 9 }}>Wins this week</div>
                <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 18 }}>
                  {R.wins.map((w) => <div key={w} style={{ display: "flex", gap: 9, fontSize: 13.5, alignItems: "center" }}><Icon name="checkCircle" size={16} style={{ color: "var(--green)", flexShrink: 0 }} />{w}</div>)}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 11, padding: "13px 15px", background: "var(--surface-2)", borderRadius: "var(--r-md)", marginBottom: 14 }}>
                  <div className="avatar" style={{ background: ta ? ta.color : "var(--accent)", width: 34, height: 34, fontSize: 13 }}>{ta ? ta.init : "✦"}</div>
                  <div style={{ fontSize: 13, color: "var(--ink-2)" }}><b style={{ color: "var(--ink)" }}>{ta ? ta.name : "Scout"}</b> was your MVP agent this week.</div>
                </div>
                <div style={{ display: "flex", gap: 10, padding: "13px 15px", background: "var(--accent-softer)", borderRadius: "var(--r-md)" }}>
                  <Icon name="spark" size={17} style={{ color: "var(--accent-ink)", flexShrink: 0, marginTop: 1 }} />
                  <div style={{ fontSize: 13, color: "var(--accent-ink)", lineHeight: 1.5 }}><b style={{ fontWeight: 700 }}>Next week:</b> {R.nextWeek}</div>
                </div>
              </div>
            </div>
          </div>
        ); })()}
    </div>
  );
}

window.Sell = Sell;
