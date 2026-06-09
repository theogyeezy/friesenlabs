// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// reports.jsx, outcomes & ROI dashboard

function Reports({ agents }) {
  const { THROUGHPUT, STAGES, AGENT_CFG, RANGES } = window.FL_DATA;
  const [schedule, setSchedule] = useState(false);
  const [schToast, setSchToast] = useState(null);
  const deals = useStore((s) => s.deals);
  const [range, setRange] = useState("30d");
  const mult = range === "7d" ? 0.25 : range === "90d" ? 3 : 1;
  const M = (n) => Math.round(n * mult);
  const rangeLabel = range === "7d" ? "Last 7 days" : range === "90d" ? "Last 90 days" : "Last 30 days";
  const cmpLabel = range === "7d" ? "vs prior week" : range === "90d" ? "vs prior quarter" : "vs prior month";
  const tput = (RANGES.find((r) => r.id === (range === "90d" ? "yoy" : range)) || {}).throughput || THROUGHPUT;

  const pipelineSlices = STAGES.map((st) => ({ stage: st.id, label: st.name, color: st.color, val: deals.filter((d) => d.stage === st.id).reduce((t, d) => t + d.value, 0) }));
  const totalPipeline = pipelineSlices.reduce((s, x) => s + x.val, 0);
  const wonRevenue = deals.filter((d) => d.stage === "won").reduce((t, d) => t + d.value, 0);

  const leaderboard = Object.entries(AGENT_CFG)
    .map(([id, c]) => ({ id, ...c, agent: agents[id] }))
    .filter((r) => r.agent)
    .sort((a, b) => b.tasks - a.tasks);

  // funnel conversion
  const funnel = [
    { label: "New leads", val: M(248), color: "oklch(0.66 0.12 235)" },
    { label: "Qualified", val: M(156), color: "oklch(0.56 0.17 277)" },
    { label: "Proposal sent", val: M(84), color: "oklch(0.66 0.14 50)" },
    { label: "Won", val: M(41), color: "oklch(0.62 0.13 152)" },
  ];
  const fMax = funnel[0].val;

  return (
    <div className="screen screen-anim">
      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 7 }}>Outcomes</div>
          <h2 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.03em" }}>Reports</h2>
          <p style={{ color: "var(--ink-2)", fontSize: 14.5, marginTop: 5 }}>What your agents actually moved the needle on.</p>
        </div>
        <div className="seg" style={{ marginLeft: "auto" }}>
          {["7d", "30d", "90d"].map((r) => (
            <button key={r} className={range === r ? "active" : ""} onClick={() => setRange(r)}>{r === "7d" ? "7 days" : r === "30d" ? "30 days" : "90 days"}</button>
          ))}
        </div>
        <button className="btn btn-ghost" onClick={() => {
          const rows = [["Agent", "Tasks", "Success %", "Hours saved"], ...leaderboard.map((r) => [r.agent.name, M(r.tasks), r.success, M(r.hours)])];
          const csv = rows.map((r) => r.join(",")).join("\n");
          const a = document.createElement("a");
          a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
          a.download = "friesen-report.csv"; a.click();
        }}><Icon name="doc" size={16} />Export</button>
        <button className="btn btn-ghost" onClick={() => setSchedule(true)}><Icon name="mail" size={16} />Schedule</button>
      </div>

      {/* goal vs actual */}
      <div className="card" style={{ marginBottom: "var(--gap)" }}>
        <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="target" size={15} /></div><h3>Goal vs actual</h3><span className="sub" style={{ marginLeft: "auto" }}>{rangeLabel.toLowerCase()}</span></div>
        <div className="card-pad rg2" style={{ gap: 14 }}>
          {[["Revenue influenced", 284, 300, "$", "k"], ["Tasks automated", 1284, 1200, "", ""], ["Hours saved", 188, 160, "", "h"], ["Auto-approval", 86, 80, "", "%"]].map(([label, act, goal, pre, suf]) => {
            const pct = Math.round(act / goal * 100); const hit = act >= goal;
            return (
              <div key={label} style={{ padding: 14, border: "1px solid var(--line)", borderRadius: "var(--r-md)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                  <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--ink-2)" }}>{label}</span>
                  <span style={{ fontSize: 11.5, fontWeight: 700, fontFamily: "var(--mono)", color: hit ? "var(--green)" : "oklch(0.5 0.12 60)" }}>{pct}%</span>
                </div>
                <div style={{ fontSize: 19, fontWeight: 770, letterSpacing: "-.02em", marginTop: 4 }}>{pre}{M(act)}{suf} <span style={{ fontSize: 12, fontWeight: 500, color: "var(--ink-4)" }}>/ {pre}{goal}{suf} goal</span></div>
                <div className="meter" style={{ height: 7, marginTop: 8 }}><span style={{ width: Math.min(100, pct) + "%", background: hit ? "var(--green)" : "var(--accent)" }} /></div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="stat-grid">
        <StatCard icon="trend" tone="green" label="Revenue influenced" value={M(284000)} prefix="$" delta={range === "7d" ? "8%" : range === "90d" ? "41%" : "22%"} deltaDir="up" deltaLabel={cmpLabel}
          spark={[40,52,48,60,72,80,96]} sparkColor="var(--green)" fmt={(n) => (n / 1000).toFixed(0) + "k"} />
        <StatCard icon="bolt" tone="indigo" label="Tasks automated" value={M(1284)} delta={range === "7d" ? "6%" : range === "90d" ? "37%" : "18%"} deltaDir="up" deltaLabel={cmpLabel} spark={[20,28,24,40,38,52,61]} />
        <StatCard icon="clock" tone="amber" label="Hours saved" value={M(188)} suffix="h" delta={range === "7d" ? "5%" : range === "90d" ? "29%" : "14%"} deltaDir="up" deltaLabel={cmpLabel} spark={[30,34,31,38,42,40,47]} sparkColor="var(--amber)" />
        <StatCard icon="checkCircle" tone="indigo" label="Auto-approval rate" value={range === "90d" ? 89 : 86} suffix="%" delta="4%" deltaDir="up" deltaLabel={cmpLabel} spark={[70,74,72,78,80,83,86]} />
      </div>

      <div className="dash-grid section-gap">
        <div className="card">
          <div className="card-head">
            <h3>Work handled, agents vs you</h3>
            <span className="sub">{rangeLabel}</span>
            <div style={{ display: "flex", gap: 14, marginLeft: "auto" }}>
              <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11.5, color: "var(--ink-2)", fontWeight: 600 }}><i style={{ width: 9, height: 9, borderRadius: 2, background: "var(--accent)" }} />Agent</span>
              <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11.5, color: "var(--ink-2)", fontWeight: 600 }}><i style={{ width: 9, height: 3, borderRadius: 2, background: "var(--ink-4)" }} />You</span>
            </div>
          </div>
          <div className="card-pad"><AreaChart key={range} data={tput} /></div>
        </div>

        <div className="card">
          <div className="card-head"><h3>Pipeline by stage</h3></div>
          <div className="card-pad" style={{ display: "flex", alignItems: "center", gap: 20 }}>
            <div style={{ position: "relative", flexShrink: 0 }}>
              <Donut slices={pipelineSlices} />
              <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", textAlign: "center" }}>
                <div><div style={{ fontSize: 18, fontWeight: 760, letterSpacing: "-.02em" }}>${(totalPipeline / 1000).toFixed(0)}k</div><div style={{ fontSize: 10.5, color: "var(--ink-3)", fontFamily: "var(--mono)" }}>total</div></div>
              </div>
            </div>
            <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 9 }}>
              {pipelineSlices.map((p) => (
                <div key={p.stage} style={{ display: "flex", alignItems: "center", gap: 9 }}>
                  <span style={{ width: 9, height: 9, borderRadius: 3, background: p.color }} />
                  <span style={{ fontSize: 12.5, color: "var(--ink-2)", fontWeight: 550, flex: 1 }}>{p.label}</span>
                  <span style={{ fontSize: 12.5, fontWeight: 700, fontFamily: "var(--mono)" }}>${(p.val / 1000).toFixed(1)}k</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="dash-grid section-gap">
        {/* leaderboard */}
        <div className="card">
          <div className="card-head"><h3>Agent leaderboard</h3><span className="sub" style={{ marginLeft: "auto" }}>by tasks handled</span></div>
          <table className="tbl">
            <thead><tr><th>Agent</th><th style={{ textAlign: "right" }}>Tasks</th><th style={{ textAlign: "right" }}>Success</th><th style={{ textAlign: "right" }}>Hrs saved</th><th>Trend</th></tr></thead>
            <tbody>
              {leaderboard.map((r) => (
                <tr key={r.id}>
                  <td><span className="agent-tag"><div className="avatar" style={{ background: r.agent.color }}>{r.agent.init}</div>{r.agent.name}</span></td>
                  <td className="num" style={{ textAlign: "right" }}>{M(r.tasks)}</td>
                  <td className="num" style={{ textAlign: "right" }}>{r.success}%</td>
                  <td className="num" style={{ textAlign: "right" }}>{M(r.hours)}h</td>
                  <td><Sparkline data={r.trend} color={r.agent.color} w={70} h={24} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* funnel */}
        <div className="card">
          <div className="card-head"><h3>Conversion funnel</h3><span className="sub" style={{ marginLeft: "auto" }}>{rangeLabel.toLowerCase()}</span></div>
          <div className="card-pad" style={{ display: "flex", flexDirection: "column", gap: 15 }}>
            {funnel.map((f, i) => (
              <div key={f.label}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                  <span style={{ fontSize: 12.5, fontWeight: 600 }}>{f.label}</span>
                  <span style={{ fontSize: 12.5, fontFamily: "var(--mono)", color: "var(--ink-3)" }}>{f.val}{i > 0 && <span style={{ marginLeft: 7, color: "var(--ink-4)" }}>{Math.round((f.val / funnel[i - 1].val) * 100)}%</span>}</span>
                </div>
                <div className="meter" style={{ height: 11 }}><span style={{ width: (f.val / fMax * 100) + "%", background: f.color }} /></div>
              </div>
            ))}
            <div style={{ marginTop: 6, padding: "13px 15px", background: "var(--accent-softer)", borderRadius: "var(--r-md)", fontSize: 13, color: "var(--accent-ink)", lineHeight: 1.5 }}>
              <b>16.5% lead-to-win</b>, up from 11% before agents. Scout's scoring is filtering out low-fit leads earlier.
            </div>
          </div>
        </div>
      </div>

      {schedule && (
        <div className="cmdk-scrim show" onClick={() => setSchedule(false)} style={{ alignItems: "center", paddingTop: 0 }}>
          <div className="cmdk" style={{ maxWidth: 440 }} onClick={(e) => e.stopPropagation()}>
            <div style={{ padding: "18px 20px", borderBottom: "1px solid var(--line)", display: "flex", alignItems: "center", gap: 11 }}>
              <div className="feed-ico" style={{ width: 32, height: 32, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="mail" size={16} /></div>
              <div style={{ flex: 1 }}><b style={{ fontSize: 16, fontWeight: 720 }}>Schedule this report</b><div style={{ fontSize: 12, color: "var(--ink-4)" }}>Emailed automatically, wired to your 7am brief</div></div>
              <button className="icon-btn" onClick={() => setSchedule(false)}><Icon name="x" size={18} /></button>
            </div>
            <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 13 }}>
              <div className="wf-field"><label>Frequency</label><select defaultValue="Weekly · Monday 7am"><option>Daily · 7am brief</option><option>Weekly · Monday 7am</option><option>Monthly · 1st at 7am</option></select></div>
              <div className="wf-field"><label>Send to</label><input defaultValue="jordan@reyesco.com, team@reyesco.com" /></div>
              <div className="wf-field"><label>Format</label><div className="seg" style={{ width: "fit-content" }}><button className="active">PDF</button><button>CSV</button><button>Both</button></div></div>
              <button className="btn btn-primary" onClick={() => { setSchedule(false); setSchToast("Report scheduled · arrives with your 7am brief"); setTimeout(() => setSchToast(null), 3000); }}><Icon name="check" size={16} sw={2.2} />Schedule report</button>
            </div>
          </div>
        </div>
      )}
      {schToast && (
        <div style={{ position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)", zIndex: 70, background: "var(--ink)", color: "var(--bg)", borderRadius: "var(--r-md)", padding: "12px 18px", display: "flex", alignItems: "center", gap: 10, boxShadow: "var(--shadow-xl)", animation: "feed-in .3s both", maxWidth: "90vw" }}>
          <Icon name="checkCircle" size={18} /><span style={{ fontSize: 13.5, fontWeight: 600 }}>{schToast}</span>
        </div>
      )}
    </div>
  );
}

window.Reports = Reports;
