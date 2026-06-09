// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// salesdesk.jsx — manager sales analytics inside Sell: revenue/close-rate by period,
// editable KPI goals, today/tomorrow calls, EOD reports, rep calendars, Ask Sales box

const SD_PERIODS = [["daily", "Daily"], ["weekly", "Weekly"], ["monthly", "Monthly"], ["quarterly", "Quarterly"], ["yoy", "Year"]];
const SD_SCALE = { daily: 0.2, weekly: 1, monthly: 4.3, quarterly: 13, yoy: 52 };
const sdMoney = (n) => n >= 1000 ? "$" + (n / 1000).toFixed(n >= 100000 ? 0 : 1) + "k" : "$" + Math.round(n);

// deterministic small variance so close rate differs a touch per period
function sdVar(name, pi) { let h = pi * 7; for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 13; return h - 6; }
function sdRevenue(rep, p) { return Math.round(rep.closedVal * SD_SCALE[p]); }
function sdDeals(rep, p) { return Math.max(0, Math.round(rep.closed * SD_SCALE[p])); }
function sdClose(rep, p) { const pi = SD_PERIODS.findIndex((x) => x[0] === p); return Math.max(8, Math.min(72, rep.winRate + sdVar(rep.name, pi))); }
function sdCallsWk(rep) { return rep.kind === "agent" ? 0 : Math.round(rep.activities * 0.12); }

function repList() {
  return (window.FL_DATA.REP_STATS || []).map((r) => ({ ...r, label: r.you ? "You" : r.name }));
}

function SalesDesk({ agents, onOpenDeal, onNavigate }) {
  const goals = useStore((s) => s.salesGoals);
  const storeDeals = useStore((s) => s.deals);
  const { SALES_CALLS } = window.FL_DATA;
  const reps = repList();
  const [period, setPeriod] = useState("weekly");
  const [on, setOn] = useState(() => { const o = {}; reps.forEach((r) => (o[r.name] = true)); return o; });
  const [sort, setSort] = useState({ key: "revenue", dir: -1 });
  const [callDay, setCallDay] = useState("today");
  const [editGoal, setEditGoal] = useState(null);
  const [goalDraft, setGoalDraft] = useState("");
  const [report, setReport] = useState(null);
  const periodLabel = SD_PERIODS.find((p) => p[0] === period)[1].toLowerCase();

  const shown = reps.filter((r) => on[r.name]);
  const human = shown.filter((r) => r.kind !== "agent");

  // weekly actuals for the editable KPI targets (driven by which reps are toggled on)
  const wkRevenue = shown.reduce((s, r) => s + r.closedVal, 0);
  const wkCalls = human.reduce((s, r) => s + sdCallsWk(r), 0);
  const wkMeetings = Math.round(wkCalls * 0.4);
  const wkClose = shown.length ? Math.round(shown.reduce((s, r) => s + r.winRate, 0) / shown.length) : 0;

  const KPIS = [
    { key: "revenue", icon: "trend", tone: "green", label: "Revenue closed", actual: wkRevenue, goal: goals.revenue, fmt: sdMoney },
    { key: "calls", icon: "phone", tone: "indigo", label: "Calls made", actual: wkCalls, goal: goals.calls, fmt: (n) => n },
    { key: "meetings", icon: "calendar", tone: "amber", label: "Meetings", actual: wkMeetings, goal: goals.meetings, fmt: (n) => n },
    { key: "closeRate", icon: "target", tone: "rose", label: "Close rate", actual: wkClose, goal: goals.closeRate, fmt: (n) => n + "%" },
  ];
  const tone2 = (t) => ({ green: ["var(--green-soft)", "oklch(0.42 0.12 152)"], indigo: ["var(--accent-soft)", "var(--accent-ink)"], amber: ["var(--amber-soft)", "oklch(0.5 0.12 60)"], rose: ["var(--rose-soft)", "oklch(0.48 0.14 18)"] }[t]);

  // revenue table rows for selected period, sortable
  const rows = shown.map((r) => ({ name: r.label, kind: r.kind, init: r.init, color: r.color,
    revenue: sdRevenue(r, period), deals: sdDeals(r, period), close: sdClose(r, period) }))
    .sort((a, b) => (a[sort.key] < b[sort.key] ? 1 : -1) * sort.dir);
  const maxRev = Math.max(...rows.map((r) => r.revenue), 1);
  const totalRev = rows.reduce((s, r) => s + r.revenue, 0);
  const setSortKey = (key) => setSort((s) => s.key === key ? { key, dir: -s.dir } : { key, dir: -1 });
  const sortCaret = (key) => sort.key === key ? (sort.dir === -1 ? " ↓" : " ↑") : "";

  const calls = SALES_CALLS.filter((c) => c.when === callDay && (on[c.rep] || (c.rep === "You" && on["Jordan Reyes"])));
  const openCall = (c) => { const d = storeDeals.find((x) => x.co === c.co); if (d && onOpenDeal) onOpenDeal(d); };

  const commitGoal = () => { const v = parseInt(goalDraft.replace(/[^0-9]/g, ""), 10); if (!isNaN(v)) FLStore.setSalesGoal(editGoal, v); setEditGoal(null); };

  const reports = sdReports(reps);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--gap)" }}>
      {/* KPI targets (editable goals) */}
      <div>
        <div className="sd-row-head"><h3>Targets <span style={{ color: "var(--ink-4)", fontWeight: 500, fontSize: 12 }}>· this week · tap a goal to edit</span></h3></div>
        <div className="stat-grid">
          {KPIS.map((k) => {
            const [bg, fg] = tone2(k.tone);
            const pct = Math.min(100, Math.round(k.actual / k.goal * 100));
            return (
              <div className="stat" key={k.key}>
                <div className="stat-top"><div className="stat-ico" style={{ background: bg, color: fg }}><Icon name={k.icon} size={17} /></div><span style={{ fontSize: 11, fontWeight: 700, color: pct >= 100 ? "var(--green)" : "var(--ink-4)", fontFamily: "var(--mono)" }}>{pct}%</span></div>
                <div className="stat-val" style={{ fontSize: 23 }}>{k.fmt(k.actual)}</div>
                <div className="stat-label">{k.label}</div>
                <div className="meter" style={{ height: 6, margin: "8px 0 6px" }}><span style={{ width: pct + "%", background: pct >= 100 ? "var(--green)" : fg }} /></div>
                {editGoal === k.key ? (
                  <input autoFocus value={goalDraft} onChange={(e) => setGoalDraft(e.target.value)} onBlur={commitGoal}
                    onKeyDown={(e) => { if (e.key === "Enter") commitGoal(); if (e.key === "Escape") setEditGoal(null); }}
                    style={{ width: "100%", font: "inherit", fontSize: 12, fontWeight: 600, border: "1px solid var(--accent)", borderRadius: 6, padding: "2px 7px", outline: "none", background: "var(--bg)", color: "var(--ink)" }} />
                ) : (
                  <button onClick={() => { setEditGoal(k.key); setGoalDraft(String(k.goal)); }} style={{ fontSize: 11.5, color: "var(--ink-4)", display: "flex", alignItems: "center", gap: 5 }}>Goal: {k.fmt(k.goal)} <Icon name="note" size={11} /></button>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* rep filter + period */}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <div className="seg">{SD_PERIODS.map(([id, l]) => <button key={id} className={period === id ? "active" : ""} onClick={() => setPeriod(id)}>{l}</button>)}</div>
        <div style={{ flex: 1 }} />
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {reps.map((r) => (
            <button key={r.name} onClick={() => setOn((o) => ({ ...o, [r.name]: !o[r.name] }))}
              className="chip" style={{ height: 28, cursor: "pointer", background: on[r.name] ? "var(--accent-soft)" : "var(--surface-2)", color: on[r.name] ? "var(--accent-ink)" : "var(--ink-4)", border: on[r.name] ? "none" : "1px solid var(--line)" }}>
              <span className="avatar" style={{ background: r.color, width: 17, height: 17, fontSize: r.kind === "agent" ? 9 : 8 }}>{r.init}</span>{r.label}
            </button>
          ))}
        </div>
      </div>

      {/* revenue by rep (sortable) */}
      <div className="card">
        <div className="card-head"><h3>Revenue by rep</h3><span className="sub" style={{ marginLeft: "auto" }}>{periodLabel} · {sdMoney(totalRev)} total</span></div>
        <div className="rep-table">
          <div className="rep-row rep-head" style={{ cursor: "default" }}>
            <span>Rep</span>
            <button className="sd-sort" onClick={() => setSortKey("revenue")} style={{ textAlign: "right" }}>Revenue{sortCaret("revenue")}</button>
            <span>Share</span>
            <button className="sd-sort" onClick={() => setSortKey("deals")} style={{ textAlign: "right" }}>Deals{sortCaret("deals")}</button>
            <button className="sd-sort" onClick={() => setSortKey("close")} style={{ textAlign: "right" }}>Close{sortCaret("close")}</button>
          </div>
          {rows.map((r) => (
            <div className="rep-row" key={r.name}>
              <span style={{ display: "flex", alignItems: "center", gap: 9, minWidth: 0 }}>
                <span className="avatar" style={{ background: r.color, width: 26, height: 26, fontSize: r.kind === "agent" ? 13 : 10 }}>{r.init}</span>
                <b style={{ fontSize: 12.5, fontWeight: 650, whiteSpace: "nowrap" }}>{r.name}{r.kind === "agent" && <span className="chip" style={{ height: 15, fontSize: 9, padding: "0 5px", marginLeft: 5 }}>agent</span>}</b>
              </span>
              <span style={{ textAlign: "right", fontWeight: 700, fontFamily: "var(--mono)", fontSize: 13 }}>{sdMoney(r.revenue)}</span>
              <span className="rep-bar"><span style={{ width: (r.revenue / maxRev * 100) + "%", background: r.color }} /></span>
              <span style={{ textAlign: "right", fontFamily: "var(--mono)", fontSize: 12.5 }}>{r.deals}</span>
              <span style={{ textAlign: "right", fontWeight: 600, fontSize: 12.5 }}>{r.close}%</span>
            </div>
          ))}
        </div>
      </div>

      {/* close rate across all periods */}
      <div className="card">
        <div className="card-head"><h3>Close rate by period</h3><span className="sub" style={{ marginLeft: "auto" }}>every rep, every window</span></div>
        <div style={{ overflowX: "auto" }}>
          <table className="tbl" style={{ minWidth: 520 }}>
            <thead><tr><th>Rep</th>{SD_PERIODS.map(([id, l]) => <th key={id} className="num">{l}</th>)}</tr></thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.name}>
                  <td><span className="agent-tag"><div className="avatar" style={{ background: r.color, fontSize: 10 }}>{r.init}</div>{r.label}</span></td>
                  {SD_PERIODS.map(([id]) => { const v = sdClose(r, id); return <td key={id} className="num" style={{ fontFamily: "var(--mono)", fontWeight: 600, color: v >= 40 ? "var(--green)" : v >= 25 ? "var(--ink)" : "var(--ink-3)" }}>{v}%</td>; })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="dash-grid">
        {/* calls today / tomorrow */}
        <div className="card">
          <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="phone" size={15} /></div><h3>Scheduled calls</h3>
            <div className="seg" style={{ marginLeft: "auto" }}><button className={callDay === "today" ? "active" : ""} onClick={() => setCallDay("today")} style={{ height: 26, padding: "0 11px", fontSize: 12 }}>Today</button><button className={callDay === "tomorrow" ? "active" : ""} onClick={() => setCallDay("tomorrow")} style={{ height: 26, padding: "0 11px", fontSize: 12 }}>Tomorrow</button></div>
          </div>
          <div style={{ padding: "4px var(--pad) 12px" }}>
            {calls.map((c) => (
              <button key={c.id} onClick={() => openCall(c)} className="sd-call">
                <div style={{ textAlign: "center", minWidth: 58 }}><div style={{ fontSize: 13, fontWeight: 730 }}>{c.time.split(" ")[0]}</div><div style={{ fontSize: 9.5, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>{c.time.split(" ")[1]}</div></div>
                <div style={{ width: 3, alignSelf: "stretch", borderRadius: 99, background: c.score >= 85 ? "var(--green)" : c.score >= 70 ? "var(--amber)" : "var(--ink-4)" }} />
                <div style={{ flex: 1, minWidth: 0, textAlign: "left" }}>
                  <b style={{ fontSize: 13, fontWeight: 650, display: "block", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{c.co}</b>
                  <span style={{ fontSize: 11, color: "var(--ink-4)" }}>{c.note}</span>
                </div>
                <span className="sd-score" title="Lead score" style={{ background: c.score >= 85 ? "var(--green-soft)" : c.score >= 70 ? "var(--amber-soft)" : "var(--surface-2)", color: c.score >= 85 ? "oklch(0.42 0.12 152)" : c.score >= 70 ? "oklch(0.5 0.12 60)" : "var(--ink-3)" }}>{c.score}</span>
                <span className="agent-tag" title={"Assigned to " + c.rep}><div className="avatar" style={{ background: c.repColor, width: 22, height: 22, fontSize: 9 }}>{c.repInit}</div></span>
                <Icon name="chevR" size={15} style={{ color: "var(--ink-4)", flexShrink: 0 }} />
              </button>
            ))}
            {calls.length === 0 && <p style={{ fontSize: 12.5, color: "var(--ink-4)", padding: "14px 4px" }}>No calls {callDay} for the selected reps.</p>}
          </div>
        </div>

        {/* EOD reports */}
        <div className="card" style={{ alignSelf: "start" }}>
          <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--amber-soft)", color: "oklch(0.5 0.12 60)" }}><Icon name="doc" size={15} /></div><h3>End-of-day reports</h3><span className="sub" style={{ marginLeft: "auto" }}>auto-generated</span></div>
          <div style={{ padding: "4px var(--pad) 12px", display: "flex", flexDirection: "column" }}>
            {reports.map((rp) => (
              <button key={rp.date} onClick={() => setReport(rp)} className="sd-call" style={{ gap: 12 }}>
                <div className="feed-ico" style={{ width: 30, height: 30, background: "var(--surface-2)", color: "var(--ink-3)", flexShrink: 0 }}><Icon name="doc" size={14} /></div>
                <div style={{ flex: 1, minWidth: 0, textAlign: "left" }}><b style={{ fontSize: 13, fontWeight: 650 }}>EOD · {rp.date}</b><div style={{ fontSize: 11, color: "var(--ink-4)" }}>{sdMoney(rp.revenue)} closed · {rp.calls} calls · {rp.meetings} meetings</div></div>
                <Icon name="chevR" size={15} style={{ color: "var(--ink-4)" }} />
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* rep calendars */}
      <div className="card">
        <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="calendar" size={15} /></div><h3>Rep calendars</h3><span className="sub" style={{ marginLeft: "auto" }}>today</span></div>
        <div className="card-pad sd-cal-grid">
          {human.map((r) => {
            const mine = SALES_CALLS.filter((c) => c.when === "today" && (c.rep === r.label || (r.you && c.rep === "You")));
            return (
              <div key={r.name} className="sd-cal-col">
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}><div className="avatar" style={{ background: r.color, width: 26, height: 26, fontSize: 10 }}>{r.init}</div><b style={{ fontSize: 13, fontWeight: 680 }}>{r.label}</b><span style={{ marginLeft: "auto", fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>{mine.length}</span></div>
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {mine.map((c) => (
                    <button key={c.id} onClick={() => openCall(c)} style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 9px", borderRadius: "var(--r-sm)", border: "1px solid var(--line-2)", background: "var(--surface)", textAlign: "left", cursor: "pointer" }}>
                      <span style={{ fontSize: 10.5, fontFamily: "var(--mono)", color: "var(--ink-3)", minWidth: 50 }}>{c.time}</span>
                      <span style={{ flex: 1, fontSize: 12, fontWeight: 550, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{c.co}</span>
                    </button>
                  ))}
                  {mine.length === 0 && <span style={{ fontSize: 11.5, color: "var(--ink-4)", padding: "4px 2px" }}>Open day</span>}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* EOD report modal */}
      {report && (
        <div className="cmdk-scrim show" onClick={() => setReport(null)} style={{ alignItems: "center", paddingTop: 0 }}>
          <div className="cmdk" style={{ maxWidth: 480 }} onClick={(e) => e.stopPropagation()}>
            <div style={{ padding: "18px 20px", borderBottom: "1px solid var(--line)", display: "flex", alignItems: "center", gap: 11 }}>
              <div className="feed-ico" style={{ width: 32, height: 32, background: "var(--amber-soft)", color: "oklch(0.5 0.12 60)" }}><Icon name="doc" size={16} /></div>
              <div style={{ flex: 1 }}><b style={{ fontSize: 16, fontWeight: 720 }}>End-of-day report</b><div style={{ fontSize: 12, color: "var(--ink-4)" }}>{report.date} · auto-generated by Scout</div></div>
              <button className="icon-btn" onClick={() => setReport(null)}><Icon name="x" size={18} /></button>
            </div>
            <div style={{ padding: 20 }}>
              <div className="rg2" style={{ gap: 10, marginBottom: 16 }}>
                {[["Revenue closed", sdMoney(report.revenue)], ["Calls made", report.calls], ["Meetings", report.meetings], ["Deals advanced", report.advanced]].map(([l, v]) => (
                  <div key={l} style={{ padding: 12, border: "1px solid var(--line)", borderRadius: "var(--r-md)" }}><div style={{ fontSize: 20, fontWeight: 770 }}>{v}</div><div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>{l}</div></div>
                ))}
              </div>
              <div className="so-section-label" style={{ marginBottom: 8 }}>Summary</div>
              <p style={{ fontSize: 13.5, lineHeight: 1.6, color: "var(--ink-2)" }}>{report.summary}</p>
              <div className="so-section-label" style={{ margin: "16px 0 8px" }}>Top rep</div>
              <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "11px 13px", background: "var(--surface-2)", borderRadius: "var(--r-md)" }}>
                <div className="avatar" style={{ background: report.topColor, width: 30, height: 30, fontSize: 11 }}>{report.topInit}</div>
                <div style={{ fontSize: 13, color: "var(--ink-2)" }}><b style={{ color: "var(--ink)" }}>{report.top}</b> led the day with {sdMoney(report.topRev)} closed.</div>
              </div>
            </div>
          </div>
        </div>
      )}

      <AskSales reps={reps} />
    </div>
  );
}

// deterministic recent EOD reports
function sdReports(reps) {
  const days = ["Jun 3 (today)", "Jun 2", "May 30", "May 29", "May 28"];
  return days.map((date, i) => {
    const f = 0.18 + (i % 3) * 0.05;
    const revenue = Math.round(reps.reduce((s, r) => s + r.closedVal, 0) * f);
    const calls = 18 + ((i * 5) % 11);
    const meetings = 4 + (i % 4);
    const advanced = 6 + ((i * 3) % 7);
    const top = i % 2 === 0 ? reps[0] : reps[1];
    return { date, revenue, calls, meetings, advanced, top: top.label, topInit: top.init, topColor: top.color, topRev: Math.round(revenue * 0.42),
      summary: `The team closed ${sdMoney(revenue)} across ${advanced} advancing deals, with ${calls} calls and ${meetings} meetings. Pipeline stayed healthy; ${top.label} carried the day and two proposals moved to verbal-yes. Tomorrow's focus: the high-score calls flagged on the board.` };
  });
}

// ---- Ask Sales floating box ----
function sdAnswer(q, reps) {
  const t = q.toLowerCase();
  const metric = /close|conversion|win/.test(t) ? "close" : /call/.test(t) ? "calls" : /meeting/.test(t) ? "meetings" : "revenue";
  // period
  let p = "weekly", plabel = "this week";
  const dm = t.match(/past (\d+)\s*day/);
  if (dm) { p = "daily"; plabel = `the past ${dm[1]} days`; }
  else if (/today/.test(t)) { p = "daily"; plabel = "today"; }
  else if (/week/.test(t)) { p = "weekly"; plabel = "this week"; }
  else if (/month/.test(t)) { p = "monthly"; plabel = "this month"; }
  else if (/quarter/.test(t)) { p = "quarterly"; plabel = "this quarter"; }
  else if (/year/.test(t)) { p = "yoy"; plabel = "this year"; }
  const mult = dm ? parseInt(dm[1], 10) : 1;
  // rep match
  const rep = reps.find((r) => { const n = r.name.toLowerCase(), f = n.split(" ")[0]; return t.includes(n) || t.includes(f) || (r.you && /\b(me|my|i )\b/.test(t)); });
  if (!rep && /\b(team|everyone|all|total|we )\b/.test(t)) {
    if (metric === "close") return `The team's average close rate ${plabel} is ${Math.round(reps.reduce((s, r) => s + r.winRate, 0) / reps.length)}%.`;
    if (metric === "calls") return `The team has ${reps.reduce((s, r) => s + sdCallsWk(r), 0)} calls scheduled this week.`;
    const rev = reps.reduce((s, r) => s + sdRevenue(r, p), 0) * (dm ? mult / 5 : 1);
    return `The team closed about ${sdMoney(Math.round(rev))} ${plabel}.`;
  }
  if (!rep) {
    const names = reps.map((r) => r.label).join(", ");
    return `I couldn't find that rep. Your reps are: ${names}. Try "close rate of Sam this month" or "revenue from Pat this quarter".`;
  }
  if (metric === "close") return `${rep.label}'s close rate ${plabel} is ${sdClose(rep, p)}%.`;
  if (metric === "calls") { const n = SALES_CALLS_FOR(rep); return `${rep.label} has ${n} call${n === 1 ? "" : "s"} scheduled across today and tomorrow.`; }
  if (metric === "meetings") return `${rep.label} has about ${Math.round(sdCallsWk(rep) * 0.4)} meetings booked this week.`;
  const rev = Math.round(sdRevenue(rep, p) * (dm ? mult / 5 : 1));
  return `${rep.label} closed ${sdMoney(rev)} ${plabel} (${sdDeals(rep, p)} deals).`;
}
function SALES_CALLS_FOR(rep) { return (window.FL_DATA.SALES_CALLS || []).filter((c) => c.rep === rep.label || (rep.you && c.rep === "You")).length; }

function AskSales({ reps }) {
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState([{ who: "bot", text: "Ask me about your team's numbers — revenue, close rate, calls or meetings, by rep and period." }]);
  const [draft, setDraft] = useState("");
  const bodyRef = useRef(null);
  useEffect(() => { if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight; }, [msgs, open]);
  const sugg = ["What was Sam's close rate this month?", "How much did Pat close this quarter?", "Team revenue this year"];
  const send = (text) => {
    const body = (text || draft).trim(); if (!body) return; setDraft("");
    const ans = sdAnswer(body, reps);
    setMsgs((m) => [...m, { who: "me", text: body }, { who: "bot", text: ans }]);
  };

  if (!open) return (
    <button onClick={() => setOpen(true)} title="Ask Sales" style={{ position: "fixed", right: 22, bottom: 22, zIndex: 55, height: 50, padding: "0 18px 0 14px", borderRadius: 99, background: "var(--accent)", color: "#fff", display: "flex", alignItems: "center", gap: 9, boxShadow: "var(--shadow-lg)", fontWeight: 650, fontSize: 14 }}>
      <span style={{ fontSize: 18 }}>📊</span>Ask Sales
    </button>
  );
  return (
    <div style={{ position: "fixed", right: 22, bottom: 22, zIndex: 55, width: "min(360px, 92vw)", height: "min(500px, 78vh)", display: "flex", flexDirection: "column", background: "var(--bg)", border: "1px solid var(--line)", borderRadius: "var(--r-lg)", boxShadow: "var(--shadow-xl)", overflow: "hidden", animation: "onb-in .25s both" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "13px 15px", borderBottom: "1px solid var(--line)", background: "var(--surface)" }}>
        <div className="avatar" style={{ background: "linear-gradient(145deg, var(--accent), var(--accent-press))", width: 30, height: 30, fontSize: 15 }}>📊</div>
        <div style={{ flex: 1 }}><b style={{ fontSize: 13.5, fontWeight: 700, display: "flex", alignItems: "center", gap: 7 }}>Ask Sales <span className="live-dot" style={{ width: 6, height: 6 }} /></b><span style={{ fontSize: 11, color: "var(--ink-3)" }}>Reads your live team numbers</span></div>
        <button className="icon-btn" style={{ width: 30, height: 30, fontSize: 20, fontWeight: 700 }} title="Minimize" onClick={() => setOpen(false)}>−</button>
      </div>
      <div ref={bodyRef} style={{ flex: 1, overflowY: "auto", padding: 15, display: "flex", flexDirection: "column", gap: 12 }}>
        {msgs.map((m, i) => (
          <div key={i} className={"msg " + (m.who === "me" ? "me" : "agent")}>
            {m.who === "bot" && <div className="avatar m-av" style={{ background: "linear-gradient(145deg, var(--accent), var(--accent-press))", width: 26, height: 26, fontSize: 12 }}>📊</div>}
            <div><div className="bubble">{m.text}</div></div>
          </div>
        ))}
      </div>
      {msgs.length <= 1 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 7, padding: "0 15px 10px" }}>
          {sugg.map((s) => <button key={s} className="sugg" onClick={() => send(s)}>{s}</button>)}
        </div>
      )}
      <div className="chat-input" style={{ padding: 12 }}>
        <textarea rows={1} value={draft} placeholder="e.g. close rate of Sam over the past 3 days" onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        <button className="chat-send" disabled={!draft.trim()} onClick={() => send()}><Icon name="send" size={17} /></button>
      </div>
    </div>
  );
}

window.SalesDesk = SalesDesk;
