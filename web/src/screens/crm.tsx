// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// crm.jsx, Agentic CRM: kanban DnD + table + slide-over

const fmtMoney = (n) => "$" + n.toLocaleString();
const HEAT = {
  hot:  { label: "Hot",  cls: "rose",  ico: "flame" },
  warm: { label: "Warm", cls: "amber", ico: "trend" },
};

function DealCard({ deal, agents, onPointerDown, dragging, onOpen }) {
  const a = agents[deal.agent];
  const heat = HEAT[deal.heat];
  return (
    <div className={"deal-card" + (dragging ? " dragging" : "")}
      onPointerDown={(e) => onPointerDown(e, deal)}
      data-deal={deal.id}>
      <div className="deal-top">
        <div className="deal-co" style={{ background: deal.coColor }}>{deal.init}</div>
        <div className="deal-name">
          <b>{deal.co}</b>
          <span>{deal.person}</span>
        </div>
        <span className={"chip " + heat.cls} style={{ height: 20, padding: "0 7px" }}>
          <Icon name={heat.ico} size={11} sw={2.2} />{heat.label}
        </span>
      </div>
      <div className="deal-value">{fmtMoney(deal.value)}</div>
      <div className="deal-agent-line">
        <div className="avatar" style={{ background: a.color }}>{a.init}</div>
        <p><b>{a.name}</b> {deal.agentNote}</p>
      </div>
      <div className="deal-foot">
        <span style={{ fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)", display: "flex", alignItems: "center", gap: 6 }}>
          {deal.human && (() => { const h = (window.FLStore.getState().team || []).find((m) => m.id === deal.human); return h ? <span className="avatar" title={"Owner: " + h.name} style={{ background: h.color, width: 18, height: 18, fontSize: 8 }}>{h.init}</span> : null; })()}
          {deal.days === 0 ? "today" : deal.days + "d ago"}
        </span>
        <button className="btn btn-soft btn-sm" style={{ height: 26 }}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); onOpen(deal); }}>
          Open<Icon name="arrowRight" size={13} sw={2.2} />
        </button>
      </div>
    </div>
  );
}

function Board({ deals, agents, stages, setDeals, onOpen }) {
  const [drag, setDrag] = useState(null); // {id, x, y, w, deal}
  const [overCol, setOverCol] = useState(null);
  const startRef = useRef(null);
  const movedRef = useRef(false);

  const onPointerDown = (e, deal) => {
    if (e.button !== undefined && e.button !== 0) return;
    const card = e.currentTarget;
    const rect = card.getBoundingClientRect();
    startRef.current = { x: e.clientX, y: e.clientY, deal, offX: e.clientX - rect.left, offY: e.clientY - rect.top, w: rect.width };
    movedRef.current = false;
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const onMove = useCallback((e) => {
    const s = startRef.current;
    if (!s) return;
    const dist = Math.hypot(e.clientX - s.x, e.clientY - s.y);
    if (!movedRef.current && dist > 6) movedRef.current = true;
    if (movedRef.current) {
      setDrag({ id: s.deal.id, deal: s.deal, x: e.clientX - s.offX, y: e.clientY - s.offY, w: s.w });
      const el = document.elementFromPoint(e.clientX, e.clientY);
      const col = el && el.closest("[data-col]");
      setOverCol(col ? col.getAttribute("data-col") : null);
    }
  }, []);

  const onUp = useCallback((e) => {
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
    const s = startRef.current;
    if (s && movedRef.current) {
      const el = document.elementFromPoint(e.clientX, e.clientY);
      const col = el && el.closest("[data-col]");
      const target = col && col.getAttribute("data-col");
      if (target) {
        const won = window.FLStore.moveDeal(s.deal.id, target);
        if (won && window.confettiBurst && window.FLStore.getState().gamifyOn) window.confettiBurst(e.clientX, e.clientY);
      }
    }
    setDrag(null); setOverCol(null); startRef.current = null; movedRef.current = false;
  }, [onMove]);

  return (
    <div style={{ overflowX: "auto", paddingBottom: 4 }}>
      <div className="board">
        {stages.map((st) => {
          const items = deals.filter((d) => d.stage === st.id);
          const sum = items.reduce((s, d) => s + d.value, 0);
          return (
            <div className="col" key={st.id}>
              <div className="col-head">
                <span className="cdot" style={{ background: st.color }} />
                <b>{st.name}</b>
                <span className="count">{items.length}</span>
                <span className="col-sum">{fmtMoney(sum)}</span>
              </div>
              <div className={"col-drop" + (overCol === st.id ? " drag-over" : "")} data-col={st.id}>
                {items.map((d) => (
                  <DealCard key={d.id} deal={d} agents={agents} onPointerDown={onPointerDown}
                    dragging={drag && drag.id === d.id} onOpen={onOpen} />
                ))}
                {items.length === 0 && (
                  <div style={{ padding: 14, textAlign: "center", fontSize: 12, color: "var(--ink-4)", border: "1.5px dashed var(--line)", borderRadius: "var(--r-sm)" }}>
                    Drop deals here
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
      {drag && (
        <div className="deal-card ghost" style={{ left: drag.x, top: drag.y, width: drag.w }}>
          <div className="deal-top">
            <div className="deal-co" style={{ background: drag.deal.coColor }}>{drag.deal.init}</div>
            <div className="deal-name"><b>{drag.deal.co}</b><span>{drag.deal.person}</span></div>
          </div>
          <div className="deal-value">{fmtMoney(drag.deal.value)}</div>
        </div>
      )}
    </div>
  );
}

function DealTable({ deals, agents, stages, onOpen }) {
  const [sort, setSort] = useState({ key: "value", dir: "desc" });
  const sorted = [...deals].sort((a, b) => {
    let av = a[sort.key], bv = b[sort.key];
    if (typeof av === "string") { av = av.toLowerCase(); bv = bv.toLowerCase(); }
    return (av < bv ? -1 : av > bv ? 1 : 0) * (sort.dir === "asc" ? 1 : -1);
  });
  const th = (key, label, extra) => (
    <th onClick={() => setSort((s) => ({ key, dir: s.key === key && s.dir === "desc" ? "asc" : "desc" }))} style={extra}>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
        {label}{sort.key === key && <Icon name={sort.dir === "asc" ? "arrowUp" : "arrowDown"} size={12} sw={2.4} />}
      </span>
    </th>
  );
  return (
    <div className="tbl-wrap fade-up">
      <table className="tbl">
        <thead>
          <tr>
            {th("co", "Company")}
            {th("stage", "Stage")}
            {th("value", "Value", { textAlign: "right" })}
            {th("agent", "Assigned")}
            <th>Latest agent action</th>
            {th("heat", "Heat")}
          </tr>
        </thead>
        <tbody>
          {sorted.map((d) => {
            const a = agents[d.agent], st = stages.find((s) => s.id === d.stage), heat = HEAT[d.heat];
            return (
              <tr key={d.id} onClick={() => onOpen(d)}>
                <td>
                  <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <div className="deal-co" style={{ background: d.coColor, width: 26, height: 26, fontSize: 10, borderRadius: 7 }}>{d.init}</div>
                    <div><div style={{ fontWeight: 650 }}>{d.co}</div><div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>{d.person}</div></div>
                  </div>
                </td>
                <td><span className="chip"><span className="cdot" style={{ background: st.color }} />{st.name}</span></td>
                <td className="num" style={{ textAlign: "right" }}>{fmtMoney(d.value)}</td>
                <td>
                  <span className="agent-tag"><div className="avatar" style={{ background: a.color }}>{a.init}</div>{a.name}</span>
                  {d.human && (() => { const h = (window.FLStore.getState().team || []).find((m) => m.id === d.human); return h ? <span className="avatar" title={"Owner: " + h.name} style={{ background: h.color, width: 22, height: 22, fontSize: 9, marginLeft: 6, display: "inline-grid", verticalAlign: "middle" }}>{h.init}</span> : null; })()}
                </td>
                <td style={{ color: "var(--ink-2)", fontSize: 12.5, maxWidth: 280 }}>
                  <span style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.agentNote}</span>
                </td>
                <td><span className={"chip " + heat.cls} style={{ height: 20 }}><Icon name={heat.ico} size={11} sw={2.2} />{heat.label}</span></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function CRM({ agents, onOpen, onNavigate }) {
  const { STAGES } = window.FL_DATA;
  const deals = useStore((s) => s.deals);
  const [view, setView] = useState("board");
  const [q, setQ] = useState("");
  const [asst, setAsst] = useState(false);
  const [fAgent, setFAgent] = useState("all");
  const [fHeat, setFHeat] = useState("all");
  const [add, setAdd] = useState(false);
  const [imp, setImp] = useState(false);
  const [views, setViews] = useState([]);
  const [viewsMenu, setViewsMenu] = useState(false);

  const filtered = deals.filter((d) =>
    d.stage !== "lost" &&
    (!q || d.co.toLowerCase().includes(q.toLowerCase()) || d.person.toLowerCase().includes(q.toLowerCase())) &&
    (fAgent === "all" || d.agent === fAgent) &&
    (fHeat === "all" || d.heat === fHeat));
  const matchFilter = (d, f) => (!f.stage || d.stage === f.stage) && (!f.heat || d.heat === f.heat) && (!f.agent || d.agent === f.agent);
  const activeView = views.find((v) => v.id === view);
  const shown = activeView ? deals.filter((d) => matchFilter(d, activeView.filter)) : filtered;
  const createView = (spec) => { const id = "v" + Date.now(); setViews((v) => [...v, { ...spec, id, temp: true }]); setView(id); return deals.filter((d) => matchFilter(d, spec.filter)).length; };
  const saveView = (id) => setViews((v) => v.map((x) => x.id === id ? { ...x, temp: false } : x));
  const dismissView = (id) => { setViews((v) => v.filter((x) => x.id !== id)); setView("board"); };

  const totalVal = filtered.reduce((s, d) => s + d.value, 0);

  return (
    <div className="screen screen-anim" style={{ maxWidth: view === "board" ? "none" : 1440 }}>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 7 }}>Pipeline · {filtered.length} active deals</div>
          <h2 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.03em" }}>Uplift</h2>
          <p style={{ color: "var(--ink-2)", fontSize: 14.5, marginTop: 5 }}>
            Every deal has an agent working it. <b style={{ color: "var(--ink)" }}>{fmtMoney(totalVal)}</b> in motion.
          </p>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 9 }}>
          <button className="btn btn-ghost" onClick={() => setImp(true)}><Icon name="layers" size={16} />Import</button>
          <button className="btn btn-primary" onClick={() => setAdd(true)}><Icon name="plus" size={16} sw={2.2} />Add deal</button>
        </div>
      </div>

      <div className="crm-toolbar">
        <div className="seg">
          <button className={view === "board" ? "active" : ""} onClick={() => setView("board")}><Icon name="layers" size={15} />Board</button>
          <button className={view === "table" ? "active" : ""} onClick={() => setView("table")}><Icon name="grid" size={15} />Table</button>
          <button className={view === "performance" ? "active" : ""} onClick={() => setView("performance")}><Icon name="trend" size={15} />Performance</button>
        </div>
        {views.filter((v) => v.temp).map((v) => (
          <button key={v.id} className="filter-pill" onClick={() => setView(v.id)} style={{ borderColor: view === v.id ? "var(--accent)" : "var(--amber)", borderStyle: "dashed", color: view === v.id ? "var(--accent-ink)" : "var(--ink-2)" }}>
            <Icon name="grid" size={13} />{v.name}
            <span onClick={(e) => { e.stopPropagation(); dismissView(v.id); }} style={{ marginLeft: 3, opacity: .55, fontSize: 13 }}>✕</span>
          </button>
        ))}
        {true && (
          <div style={{ position: "relative" }}>
            <button className="filter-pill" onClick={() => setViewsMenu((o) => !o)} style={{ borderColor: activeView && !activeView.temp ? "var(--accent)" : "var(--line)", color: activeView && !activeView.temp ? "var(--accent-ink)" : "var(--ink-2)" }}>
              <Icon name="layers" size={14} />{activeView && !activeView.temp ? activeView.name : "Views"}
              <span style={{ fontFamily: "var(--mono)", fontSize: 11, fontWeight: 700, opacity: .7 }}>{views.filter((v) => !v.temp).length}</span>
              <Icon name="chevDown" size={13} />
            </button>
            {viewsMenu && (
              <>
                <div style={{ position: "fixed", inset: 0, zIndex: 30 }} onClick={() => setViewsMenu(false)} />
                <div style={{ position: "absolute", top: 44, left: 0, minWidth: 240, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-md)", boxShadow: "var(--shadow-lg)", zIndex: 31, padding: 6, animation: "feed-in .15s both" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 9, padding: "8px 10px", borderRadius: "var(--r-sm)", cursor: "pointer", background: view === "board" ? "var(--accent-softer)" : "transparent" }} onClick={() => { setView("board"); setViewsMenu(false); }}>
                    <Icon name="layers" size={14} style={{ color: "var(--ink-3)" }} /><span style={{ flex: 1, fontSize: 13, fontWeight: 600 }}>All deals (Board)</span><span style={{ fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>{deals.length}</span>
                  </div>
                  <div style={{ fontSize: 10.5, fontWeight: 650, textTransform: "uppercase", letterSpacing: ".05em", color: "var(--ink-4)", padding: "8px 10px 4px" }}>Saved views</div>
                  {views.filter((v) => !v.temp).length === 0 && (
                    <div style={{ padding: "6px 10px 8px", fontSize: 12, color: "var(--ink-4)", lineHeight: 1.45 }}>No saved views yet. Ask Uplift to build one and hit <b>Save view</b> to keep it here.</div>
                  )}
                  {views.filter((v) => !v.temp).map((v) => (
                    <div key={v.id} onClick={() => { setView(v.id); setViewsMenu(false); }} style={{ display: "flex", alignItems: "center", gap: 9, padding: "8px 10px", borderRadius: "var(--r-sm)", cursor: "pointer", background: view === v.id ? "var(--accent-softer)" : "transparent" }}>
                      <Icon name="grid" size={14} style={{ color: "var(--ink-3)" }} />
                      <span style={{ flex: 1, fontSize: 13, fontWeight: 600 }}>{v.name}</span>
                      <span style={{ fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>{deals.filter((d) => matchFilter(d, v.filter)).length}</span>
                      <span onClick={(e) => { e.stopPropagation(); dismissView(v.id); }} style={{ opacity: .5, fontSize: 13 }}>✕</span>
                    </div>
                  ))}
                  <div style={{ borderTop: "1px solid var(--line-2)", margin: "5px 0" }} />
                  <div style={{ display: "flex", alignItems: "center", gap: 9, padding: "8px 10px", borderRadius: "var(--r-sm)", cursor: "pointer", color: "var(--accent-ink)" }} onClick={() => { setViewsMenu(false); setAsst(true); }}>
                    <Icon name="spark" size={14} /><span style={{ fontSize: 13, fontWeight: 600 }}>Ask Uplift to build a view…</span>
                  </div>
                </div>
              </>
            )}
          </div>
        )}
        <div className="search-trigger" style={{ minWidth: 200, cursor: "text" }}>
          <Icon name="search" size={15} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search deals…"
            style={{ border: "none", outline: "none", background: "none", flex: 1, fontSize: 13, color: "var(--ink)" }} />
        </div>
        <select className="filter-pill" value={fAgent} onChange={(e) => setFAgent(e.target.value)} style={{ cursor: "pointer", appearance: "none" }}>
          <option value="all">All agents</option>
          {Object.values(agents).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <select className="filter-pill" value={fHeat} onChange={(e) => setFHeat(e.target.value)} style={{ cursor: "pointer", appearance: "none" }}>
          <option value="all">Any heat</option>
          <option value="hot">Hot</option>
          <option value="warm">Warm</option>
        </select>
        <button className="btn btn-soft btn-sm" style={{ height: 36 }} onClick={() => setAsst(true)}><Icon name="spark" size={15} />Ask Uplift</button>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, color: "var(--ink-3)" }}>
          <span className="live-dot" />5 agents working this pipeline
        </div>
      </div>

      {activeView && activeView.temp && (
        <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 16px", borderRadius: "var(--r-md)", background: "var(--accent-softer)", border: "1px solid var(--accent-soft)", marginBottom: "var(--gap)", flexWrap: "wrap" }}>
          <div className="feed-ico" style={{ width: 30, height: 30, background: "var(--surface)", color: "var(--accent-ink)" }}><Icon name="spark" size={15} /></div>
          <p style={{ flex: 1, minWidth: 180, fontSize: 13, color: "var(--accent-ink)", lineHeight: 1.45 }}>I built this <b>{activeView.name}</b> view for you. Want to keep it permanently?</p>
          <button className="btn btn-primary btn-sm" onClick={() => saveView(activeView.id)}><Icon name="check" size={13} sw={2.4} />Save view</button>
          <button className="btn btn-ghost btn-sm" onClick={() => dismissView(activeView.id)}>Dismiss</button>
        </div>
      )}
      {view === "board" ? <Board deals={filtered} agents={agents} stages={STAGES} onOpen={onOpen} />
        : view === "performance" ? <CRMPerformance deals={deals} agents={agents} stages={STAGES} onNavigate={onNavigate} />
        : <DealTable deals={shown} agents={agents} stages={STAGES} onOpen={onOpen} />}

      <UpliftAssistant open={asst} agents={agents} onClose={() => setAsst(false)} onNavigate={onNavigate || (() => {})} onLayout={setView} onCreateView={createView} />
      {add && <AddDealModal onClose={() => setAdd(false)} />}
      <ImportData open={imp} onClose={() => setImp(false)} />
    </div>
  );
}

function CRMPerformance({ deals, agents, stages, onNavigate }) {
  const { REP_STATS, REP_SCALE } = window.FL_DATA;
  const [period, setPeriod] = useState("30d");
  const [board, setBoard] = useState("all");
  const scale = (REP_SCALE && REP_SCALE[period]) || 1;
  const periods = [["7d", "7 days"], ["30d", "30 days"], ["yoy", "Year"]];

  const won = deals.filter((d) => d.stage === "won");
  const lost = deals.filter((d) => d.stage === "lost");
  const wonVal = won.reduce((s, d) => s + d.value, 0);
  const decided = won.length + lost.length;
  const wlRate = decided ? Math.round((won.length / decided) * 100) : 0;
  const reasons = {};
  lost.forEach((d) => { const r = d.lostReason || "Other"; reasons[r] = (reasons[r] || 0) + 1; });
  const reasonRows = Object.entries(reasons).sort((a, b) => b[1] - a[1]);
  // lead source attribution across all deals
  const srcMap = {};
  deals.forEach((d) => { const s = d.source || "Other"; if (!srcMap[s]) srcMap[s] = { n: 0, won: 0 }; srcMap[s].n++; if (d.stage === "won") srcMap[s].won++; });
  const srcRows = Object.entries(srcMap).sort((a, b) => b[1].n - a[1].n);
  const srcMax = Math.max(...srcRows.map(([, v]) => v.n), 1);
  // stalled deals: open, no activity 7+ days in current stage
  const stalled = deals.filter((d) => d.stage !== "won" && d.stage !== "lost" && (d.stageDays != null ? d.stageDays : d.days) >= 7).sort((a, b) => (b.stageDays || b.days) - (a.stageDays || a.days));
  const openDeals = deals.filter((d) => d.stage !== "won");
  const openVal = openDeals.reduce((s, d) => s + d.value, 0);
  const winRate = deals.length ? Math.round((won.length / deals.length) * 100) : 0;
  const avgDeal = won.length ? Math.round(wonVal / won.length) : 0;
  // weighted forecast by stage probability
  const PROB = { lead: 0.1, qualified: 0.3, proposal: 0.6, won: 1 };
  const forecast = deals.reduce((s, d) => s + d.value * (PROB[d.stage] || 0.2), 0);

  const byStage = stages.map((st) => ({ ...st, val: deals.filter((d) => d.stage === st.id).reduce((t, d) => t + d.value, 0), n: deals.filter((d) => d.stage === st.id).length }));
  const maxStage = Math.max(...byStage.map((s) => s.val), 1);

  const reps = (REP_STATS || [])
    .filter((r) => board === "all" || (board === "people" ? r.kind === "human" : r.kind === "agent"))
    .map((r) => ({ ...r, closedV: Math.max(0, Math.round(r.closed * scale)), pipeV: Math.round(r.pipeline * scale), actV: Math.round(r.activities * scale), attain: r.quota ? Math.round((r.closedVal / r.quota) * 100) : null }))
    .sort((a, b) => b.pipeV - a.pipeV);
  const maxPipe = Math.max(...reps.map((r) => r.pipeV), 1);

  const fmt = (n) => n >= 1000 ? "$" + (n / 1000).toFixed(1) + "k" : "$" + n;
  const KPIS = [
    ["checkCircle", "green", "Won this period", fmt(wonVal), `${won.length} deals`],
    ["trend", "indigo", "Open pipeline", fmt(openVal), `${openDeals.length} active`],
    ["target", "amber", "Win rate", winRate + "%", "of all deals"],
    ["spark", "indigo", "Avg deal size", fmt(avgDeal), "won deals"],
    ["bolt", "amber", "Weighted forecast", fmt(Math.round(forecast)), "probability-adjusted"],
  ];
  const tone2 = (t) => ({ indigo: ["var(--accent-soft)", "var(--accent-ink)"], amber: ["var(--amber-soft)", "oklch(0.5 0.12 60)"], green: ["var(--green-soft)", "oklch(0.42 0.12 152)"], rose: ["var(--rose-soft)", "oklch(0.48 0.14 18)"] }[t] || ["var(--accent-soft)", "var(--accent-ink)"]);

  return (
    <div className="screen-anim" style={{ display: "flex", flexDirection: "column", gap: "var(--gap)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <div className="seg">{periods.map(([id, l]) => <button key={id} className={period === id ? "active" : ""} onClick={() => setPeriod(id)}>{l}</button>)}</div>
        <div style={{ marginLeft: "auto", fontSize: 12.5, color: "var(--ink-3)", display: "flex", alignItems: "center", gap: 7 }}><span className="live-dot" />Tracked live from your pipeline</div>
      </div>

      {/* KPIs */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12 }}>
        {KPIS.map(([ic, tone, label, val, sub]) => { const [bg, fg] = tone2(tone); return (
          <div className="card" key={label} style={{ padding: 15 }}>
            <div className="feed-ico" style={{ width: 30, height: 30, background: bg, color: fg, marginBottom: 10 }}><Icon name={ic} size={15} /></div>
            <div style={{ fontSize: 23, fontWeight: 780, letterSpacing: "-.03em" }}>{val}</div>
            <div style={{ fontSize: 12, color: "var(--ink-2)", fontWeight: 600, marginTop: 2 }}>{label}</div>
            <div style={{ fontSize: 11, color: "var(--ink-4)" }}>{sub}</div>
          </div>
        ); })}
      </div>

      <div className="dash-grid">
        {/* leaderboard */}
        <div className="card">
          <div className="card-head">
            <h3>Rep &amp; agent performance</h3>
            <div className="seg" style={{ marginLeft: "auto" }}>{[["all", "All"], ["people", "People"], ["agents", "Agents"]].map(([id, l]) => <button key={id} className={board === id ? "active" : ""} onClick={() => setBoard(id)} style={{ height: 26, padding: "0 10px", fontSize: 12 }}>{l}</button>)}</div>
          </div>
          <div className="rep-table">
            <div className="rep-row rep-head"><span>Rep</span><span style={{ textAlign: "right" }}>Closed</span><span>Pipeline</span><span style={{ textAlign: "right" }}>Win</span><span style={{ textAlign: "right" }}>Activity</span></div>
            {reps.map((r, i) => (
              <div className="rep-row" key={r.name}>
                <span style={{ display: "flex", alignItems: "center", gap: 9, minWidth: 0 }}>
                  <span style={{ fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)", width: 13 }}>{i + 1}</span>
                  <span className="avatar" style={{ background: r.color, width: 26, height: 26, fontSize: r.kind === "agent" ? 13 : 10 }}>{r.init}</span>
                  <b style={{ fontSize: 12.5, fontWeight: 650, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.you ? "You" : r.name}{r.kind === "agent" && <span className="chip" style={{ height: 16, fontSize: 9, padding: "0 5px", marginLeft: 5 }}>agent</span>}</b>
                </span>
                <span style={{ textAlign: "right", fontWeight: 700, fontFamily: "var(--mono)", fontSize: 12.5 }}>{r.closedV}</span>
                <span style={{ display: "flex", alignItems: "center", gap: 8 }}><span className="rep-bar"><span style={{ width: (r.pipeV / maxPipe * 100) + "%", background: r.color }} /></span><span style={{ fontSize: 12, fontFamily: "var(--mono)", fontWeight: 650, whiteSpace: "nowrap" }}>{fmt(r.pipeV)}</span></span>
                <span style={{ textAlign: "right", fontSize: 12.5, fontWeight: 600 }}>{r.winRate}%</span>
                <span style={{ textAlign: "right", fontSize: 12, color: "var(--ink-3)", fontFamily: "var(--mono)" }}>{r.actV.toLocaleString()}</span>
              </div>
            ))}
          </div>
        </div>

        {/* pipeline by stage */}
        <div className="card" style={{ alignSelf: "start" }}>
          <div className="card-head"><h3>Pipeline by stage</h3></div>
          <div className="card-pad" style={{ display: "flex", flexDirection: "column", gap: 13 }}>
            {byStage.map((s) => (
              <div key={s.id}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 5, fontSize: 12.5 }}><span style={{ fontWeight: 600, display: "flex", alignItems: "center", gap: 7 }}><span style={{ width: 9, height: 9, borderRadius: 3, background: s.color }} />{s.name}<span style={{ color: "var(--ink-4)", fontWeight: 400 }}>· {s.n}</span></span><span style={{ fontFamily: "var(--mono)", fontWeight: 700 }}>{fmt(s.val)}</span></div>
                <div className="meter"><span style={{ width: (s.val / maxStage * 100) + "%", background: s.color }} /></div>
              </div>
            ))}
            <button className="btn btn-ghost btn-sm" style={{ justifyContent: "center", marginTop: 4 }} onClick={() => onNavigate && onNavigate("reports")}><Icon name="trend" size={14} />Full reports<Icon name="arrowRight" size={13} sw={2.2} /></button>
          </div>
        </div>

        {/* win / loss */}
        <div className="card" style={{ alignSelf: "start" }}>
          <div className="card-head"><h3>Win / loss</h3><span className="sub" style={{ marginLeft: "auto" }}>{decided} closed</span></div>
          <div className="card-pad" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <div style={{ display: "flex", gap: 18 }}>
              <div><div style={{ fontSize: 24, fontWeight: 780, letterSpacing: "-.03em", color: "var(--green)" }}>{won.length}</div><div style={{ fontSize: 11, color: "var(--ink-3)" }}>won</div></div>
              <div><div style={{ fontSize: 24, fontWeight: 780, letterSpacing: "-.03em", color: "var(--rose)" }}>{lost.length}</div><div style={{ fontSize: 11, color: "var(--ink-3)" }}>lost</div></div>
              <div style={{ marginLeft: "auto", textAlign: "right" }}><div style={{ fontSize: 24, fontWeight: 780, letterSpacing: "-.03em" }}>{wlRate}%</div><div style={{ fontSize: 11, color: "var(--ink-3)" }}>win rate</div></div>
            </div>
            <div className="meter" style={{ height: 9 }}><span style={{ width: wlRate + "%", background: "var(--green)" }} /></div>
            {reasonRows.length > 0 && (
              <div>
                <div style={{ fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)", textTransform: "uppercase", letterSpacing: ".05em", margin: "2px 0 8px" }}>Why deals were lost</div>
                <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
                  {reasonRows.map(([r, n]) => (
                    <div key={r} style={{ display: "flex", alignItems: "center", gap: 9, fontSize: 12.5 }}>
                      <span style={{ flex: 1, color: "var(--ink-2)" }}>{r}</span>
                      <span className="rep-bar" style={{ maxWidth: 90 }}><span style={{ width: (n / lost.length * 100) + "%", background: "var(--rose)" }} /></span>
                      <span style={{ fontFamily: "var(--mono)", fontWeight: 650, width: 14, textAlign: "right" }}>{n}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* quota attainment */}
        <div className="card" style={{ alignSelf: "start" }}>
          <div className="card-head"><h3>Quota attainment</h3><span className="sub" style={{ marginLeft: "auto" }}>this period</span></div>
          <div className="card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {reps.filter((r) => r.attain != null).map((r) => (
              <div key={r.name}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 5 }}>
                  <span className="avatar" style={{ background: r.color, width: 22, height: 22, fontSize: r.kind === "agent" ? 12 : 9 }}>{r.init}</span>
                  <span style={{ fontSize: 12.5, fontWeight: 600, flex: 1 }}>{r.you ? "You" : r.name}</span>
                  <span style={{ fontSize: 12.5, fontFamily: "var(--mono)", fontWeight: 700, color: r.attain >= 100 ? "var(--green)" : "var(--ink)" }}>{r.attain}%</span>
                </div>
                <div className="meter"><span style={{ width: Math.min(100, r.attain) + "%", background: r.attain >= 100 ? "var(--green)" : r.color }} /></div>
              </div>
            ))}
          </div>
        </div>

        {/* lead source attribution */}
        <div className="card" style={{ alignSelf: "start" }}>
          <div className="card-head"><h3>Lead source</h3><span className="sub" style={{ marginLeft: "auto" }}>where deals come from</span></div>
          <div className="card-pad" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {srcRows.map(([s, v]) => (
              <div key={s} style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 12.5 }}>
                <span style={{ flex: 1, color: "var(--ink-2)" }}>{s}</span>
                <span className="rep-bar" style={{ maxWidth: 90 }}><span style={{ width: (v.n / srcMax * 100) + "%", background: "var(--accent)" }} /></span>
                <span style={{ fontFamily: "var(--mono)", width: 18, textAlign: "right", fontWeight: 650 }}>{v.n}</span>
                <span style={{ fontSize: 11, color: v.won ? "var(--green)" : "var(--ink-4)", width: 52, textAlign: "right", fontWeight: 600 }}>{Math.round(v.won / v.n * 100)}% win</span>
              </div>
            ))}
          </div>
        </div>

        {/* stalled deals — needs attention */}
        <div className="card" style={{ alignSelf: "start" }}>
          <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--rose-soft)", color: "oklch(0.48 0.14 18)" }}><Icon name="bolt" size={15} /></div><h3>Needs attention</h3><span className="sub" style={{ marginLeft: "auto" }}>no activity 7d+</span></div>
          <div className="card-pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {stalled.slice(0, 6).map((d) => (
              <button key={d.id} onClick={() => onOpen && onOpen(d)} style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 11px", border: "1px solid var(--line-2)", borderRadius: "var(--r-sm)", textAlign: "left", cursor: "pointer", background: "var(--surface)" }}>
                <div className="deal-co" style={{ background: d.coColor, width: 26, height: 26, fontSize: 10, borderRadius: 8 }}>{d.init}</div>
                <span style={{ flex: 1, minWidth: 0, fontSize: 13, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{d.co}</span>
                <span className="chip" style={{ height: 19, fontSize: 10, background: "var(--rose-soft)", color: "oklch(0.48 0.14 18)" }}>{d.stageDays != null ? d.stageDays : d.days}d quiet</span>
                <span style={{ fontFamily: "var(--mono)", fontSize: 12, fontWeight: 650 }}>{fmt(d.value)}</span>
              </button>
            ))}
            {stalled.length === 0 && <p style={{ fontSize: 12.5, color: "var(--ink-4)", padding: "8px 2px" }}>Nothing stalled, every open deal had activity this week. 🎉</p>}
          </div>
        </div>
      </div>
    </div>
  );
}

function AddDealModal({ onClose }) {
  const [value, setValue] = useState(8000);
  return (
    <div className="cmdk-scrim show" onClick={onClose}>
      <div className="cmdk" style={{ maxWidth: 440 }} onClick={(e) => e.stopPropagation()}>
        <div style={{ padding: "18px 20px", borderBottom: "1px solid var(--line)", display: "flex", alignItems: "center", gap: 11 }}>
          <div className="feed-ico" style={{ width: 32, height: 32, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="plus" size={16} sw={2.2} /></div>
          <b style={{ fontSize: 16, fontWeight: 720, flex: 1 }}>Add a deal</b>
          <button className="icon-btn" onClick={onClose}><Icon name="x" size={18} /></button>
        </div>
        <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 13 }}>
          <div className="wf-field"><label>Company</label><input autoFocus value={co} onChange={(e) => setCo(e.target.value)} placeholder="Acme Co." /></div>
          <div className="wf-field"><label>Contact</label><input value={person} onChange={(e) => setPerson(e.target.value)} placeholder="Jordan Smith" /></div>
          <div className="wf-field"><label>Estimated value</label><input type="number" value={value} onChange={(e) => setValue(+e.target.value)} /></div>
          <p style={{ fontSize: 12, color: "var(--ink-3)", lineHeight: 1.5 }}>Scout will enrich it and start working the lead automatically.</p>
          <button className="btn btn-primary" disabled={!co.trim()} onClick={() => { window.FLStore.addDeal({ co: co.trim(), person: person.trim(), value }); onClose(); }}>
            <Icon name="spark" size={16} />Add &amp; hand to Scout
          </button>
        </div>
      </div>
    </div>
  );
}

const UA_NAMES = ["Atlas", "Nova", "Sage", "Quill", "Iris", "Pax", "Juno", "Rivet"];
const UA_COLORS = ["oklch(0.58 0.16 300)", "oklch(0.6 0.15 200)", "oklch(0.62 0.14 130)", "oklch(0.64 0.14 40)", "oklch(0.6 0.15 350)"];
function uaBuild(prompt) {
  const t = " " + prompt.toLowerCase() + " ";
  if (/automat|workflow|every time|when a |whenever|auto |follow ?up|set up|build me|create a|nurtur/.test(t)) {
    const faces = window.FL_DATA.AGENT_FACES;
    const name = UA_NAMES[Math.floor(Math.random() * UA_NAMES.length)];
    const role = /follow/.test(t) ? "Follow-ups" : /onboard|welcome/.test(t) ? "Onboarding" : /quote|price/.test(t) ? "Quoting" : /book|demo|schedul/.test(t) ? "Scheduling" : "Outreach";
    const agent = { name, role, color: UA_COLORS[Math.floor(Math.random() * UA_COLORS.length)], init: faces[Math.floor(Math.random() * faces.length)] };
    const agentId = window.FLStore.addAgent(agent);
    const approval = /approv|ask me|sign ?off|review/.test(t);
    const steps = [
      { type: "trigger", title: /won|closed/.test(t) ? "Deal marked won" : /new deal|new customer/.test(t) ? "New deal created" : "New lead arrives", body: "Automation trigger" },
      { type: "agent", title: `${name}, ${role.toLowerCase()}`, body: `Hand this step to ${name}`, agent: agentId },
    ];
    if (approval) steps.push({ type: "approval", title: "You approve", body: "Sign off before it runs" });
    steps.push({ type: "action", title: /book|demo/.test(t) ? "Book the meeting" : /email|message|reach|nurtur/.test(t) ? "Send the message" : "Update Uplift", body: "Execute", agent: agentId });
    const w = prompt.trim().split(/\s+/).slice(0, 4).join(" ");
    window.FLStore.addWorkflow({ name: w ? w[0].toUpperCase() + w.slice(1) : "New automation", steps });
    return { text: `Done! I hired ${agent.init} ${name} (${role}) and built a ${steps.length}-step workflow. ${name} is on your Agents page now, and the workflow is live on the Workflows page.`, nav: [["Meet " + name, "agents"], ["Open workflow", "workflows"]] };
  }
  if (/board|kanban/.test(t)) return { text: "Switched you to the board view.", layout: "board" };
  if (/table|list|spreadsheet|sort|group|reorgani|organi|rearrange|by value|by agent/.test(t)) return { text: "Reorganized your pipeline into a sortable table, click any column header to sort.", layout: "table" };
  if (/pipeline|revenue|worth|total|how much/.test(t)) return { text: "Your pipeline is $124.8k across 11 active deals, Proposal stage is heaviest at $43.2k." };
  if (/close|likely|hot|win|best/.test(t)) return { text: "Most likely to close: Riverside Plumbing ($22.1k), Lantern Bakehouse ($15.7k), and Maple Grove Vet (re-scored to 91)." };
  if (/cold|stuck|slip|risk/.test(t)) return { text: "North Loop Cycles and Quill & Press are going quiet. Say 'automate follow-ups' and I'll build an agent + workflow to chase them." };
  return { text: "I can answer about your pipeline, reorganize the layout (try 'show as a table'), or build automations (try 'automate follow-ups for cold leads'), which create a real agent and workflow you'll see on those pages." };
}

function parseView(t, agents) {
  const filter = {};
  if (/\blead/.test(t)) filter.stage = "lead";
  else if (/qualif/.test(t)) filter.stage = "qualified";
  else if (/proposal/.test(t)) filter.stage = "proposal";
  else if (/\bwon\b|closed/.test(t)) filter.stage = "won";
  if (/\bhot\b/.test(t)) filter.heat = "hot";
  else if (/\bwarm\b/.test(t)) filter.heat = "warm";
  Object.values(agents).forEach((a) => { if (t.includes(a.name.toLowerCase())) filter.agent = a.id; });
  const labels = { lead: "Leads", qualified: "Qualified", proposal: "Proposals", won: "Won deals" };
  const parts = [];
  if (filter.heat) parts.push(filter.heat[0].toUpperCase() + filter.heat.slice(1));
  parts.push(filter.stage ? labels[filter.stage] : "All deals");
  let name = parts.join(" ");
  if (filter.agent) name = agents[filter.agent].name + "\u2019s " + name.toLowerCase();
  return { name, filter };
}

function UpliftAssistant({ open, agents, onClose, onNavigate, onLayout, onCreateView }) {
  const [msgs, setMsgs] = useState([{ who: "bot", text: "Hi! I'm your Uplift assistant. Ask about your pipeline, tell me to reorganize the view, or describe an automation and I'll build the agent + workflow for you." }]);
  const [draft, setDraft] = useState("");
  const bodyRef = useRef(null);
  const sugg = ["Show my pipeline as a table", "Which deals are likely to close?", "Automate follow-ups for cold leads"];
  useEffect(() => { if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight; }, [msgs, open]);
  useEffect(() => { if (!open) return; const k = (e) => { if (e.key === "Escape") onClose(); }; window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k); }, [open, onClose]);
  const send = async (text) => {
    const body = (text || draft).trim(); if (!body) return; setDraft("");
    setMsgs((m) => [...m, { who: "me", text: body }, { who: "bot", typing: true }]);
    const t = " " + body.toLowerCase() + " ";
    const isAction = /automat|workflow|every time|when a |whenever|auto |follow ?up|set up|build me|create a|nurtur|board|kanban|table|list|spreadsheet|reorgani|organi|rearrange/.test(t);
    const viewIntent = /\b(page|view|dashboard|report|screen|list|tab)\b/.test(t) && !/workflow|automat|\bagent/.test(t);
    let r;
    if (viewIntent && onCreateView) {
      const spec = parseView(t, agents); const n = onCreateView(spec);
      r = { viewBtn: true, text: n > 0
        ? `You didn’t have a “${spec.name}” page, so I built one, ${n} record${n === 1 ? "" : "s"} in it. It’s temporary for now, hit “Save view” on the page to keep it.`
        : `You don’t have any matching records yet, I pinned a temporary “${spec.name}” view that’ll fill as they come in.` };
    } else if (isAction) { r = uaBuild(body); }
    else { const ans = await askClaude(bizContext() + "\n\nUser: " + body + "\n\nAnswer:", uaBuild(body).text); r = { text: ans }; }
    if (r.layout) onLayout(r.layout);
    setMsgs((m) => { const c = [...m]; c[c.length - 1] = { who: "bot", text: r.text, nav: r.nav, viewBtn: r.viewBtn }; return c; });
  };
  return (
    <>
      <div className={"scrim" + (open ? " show" : "")} style={{ pointerEvents: open ? "auto" : "none" }} onClick={onClose} />
      <div className={"chat" + (open ? " show" : "")}>
        <div className="chat-head">
          <div className="avatar" style={{ background: "linear-gradient(145deg, var(--accent), var(--accent-press))", width: 30, height: 30, fontSize: 14 }}>✦</div>
          <div style={{ flex: 1 }}><b style={{ fontSize: 14.5, fontWeight: 700 }}>Uplift assistant</b><div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>Answers · reorganizes · builds automations</div></div>
          <button className="icon-btn" onClick={onClose}><Icon name="x" size={18} /></button>
        </div>
        <div className="chat-body" ref={bodyRef}>
          {msgs.map((m, i) => (
            <div key={i} className={"msg " + (m.who === "me" ? "me" : "agent")}>
              {m.who === "bot" && <div className="avatar m-av" style={{ background: "linear-gradient(145deg, var(--accent), var(--accent-press))" }}>✦</div>}
              <div>
                <div className="bubble">{m.typing ? <span className="typing"><i /><i /><i /></span> : m.text}</div>
                {m.viewBtn && <div style={{ marginTop: 8 }}><button className="btn btn-primary btn-sm" onClick={onClose}><Icon name="arrowRight" size={13} sw={2.2} />View the page</button></div>}
                {m.nav && <div style={{ display: "flex", gap: 7, marginTop: 8, flexWrap: "wrap" }}>{m.nav.map(([label, route]) => <button key={route} className="btn btn-soft btn-sm" onClick={() => { onNavigate(route); onClose(); }}>{label}<Icon name="arrowRight" size={13} sw={2.2} /></button>)}</div>}
              </div>
            </div>
          ))}
        </div>
        {msgs.length <= 1 && <div className="chat-suggest">{sugg.map((s) => <button key={s} className="sugg" onClick={() => send(s)}>{s}</button>)}</div>}
        <div className="chat-input">
          <textarea rows={1} value={draft} placeholder="Ask, reorganize, or automate…" onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
          <button className="chat-send" disabled={!draft.trim()} onClick={() => send()}><Icon name="send" size={17} /></button>
        </div>
      </div>
    </>
  );
}

window.CRM = CRM;
window.HEAT = HEAT;
window.fmtMoney = fmtMoney;
