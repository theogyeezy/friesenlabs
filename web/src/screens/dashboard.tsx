// @ts-nocheck
import React from "react";
import "../globals";
import { SafeHtml } from "../lib/SafeHtml";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// dashboard.jsx, Command Center

function ToneIco({ tone, name, big }) {
  const map = {
    indigo: ["var(--accent-soft)", "var(--accent-ink)"],
    amber:  ["var(--amber-soft)", "oklch(0.5 0.12 60)"],
    green:  ["var(--green-soft)", "oklch(0.42 0.12 152)"],
    rose:   ["var(--rose-soft)", "oklch(0.48 0.14 18)"],
  };
  const [bg, fg] = map[tone] || map.indigo;
  return (
    <div className="feed-ico" style={{ background: bg, color: fg, width: big ? 34 : 30, height: big ? 34 : 30 }}>
      <Icon name={name} size={big ? 17 : 15} />
    </div>
  );
}

function StatCard({ icon, tone, label, value, prefix = "", suffix = "", delta, deltaDir, spark, sparkColor, fmt, deltaLabel = "vs last week" }) {
  const map = {
    indigo: ["var(--accent-soft)", "var(--accent-ink)"],
    amber:  ["var(--amber-soft)", "oklch(0.5 0.12 60)"],
    green:  ["var(--green-soft)", "oklch(0.42 0.12 152)"],
    rose:   ["var(--rose-soft)", "oklch(0.48 0.14 18)"],
  };
  const [bg, fg] = map[tone];
  return (
    <div className="stat fade-up">
      <div className="stat-top">
        <div className="stat-ico" style={{ background: bg, color: fg }}><Icon name={icon} size={17} /></div>
        <span className="stat-label">{label}</span>
      </div>
      <div className="stat-val">{prefix}<CountUp value={value} format={fmt || ((n)=>Math.round(n).toLocaleString())} />{suffix}</div>
      <div className="stat-foot">
        <span className={"delta " + (deltaDir === "down" ? "down" : "up")}>
          <Icon name={deltaDir === "down" ? "arrowDown" : "arrowUp"} size={13} sw={2.4} />{delta}
        </span>
        <span className="muted">{deltaLabel}</span>
      </div>
      <div className="stat-spark"><Sparkline data={spark} color={sparkColor || "var(--accent)"} /></div>
    </div>
  );
}

const GL_TYPE = { email: ["mail", "indigo"], quote: ["doc", "amber"], discount: ["trend", "green"], invoice: ["doc", "green"], schedule: ["calendar", "indigo"], task: ["check", "indigo"] };
const GL_POL = { within: ["green", "Within policy"], review: ["amber", "Needs review"], exceeds: ["rose", "Exceeds limit"] };

function GreenlightMiniCard({ g, agents, onResolve }) {
  const [resolving, setResolving] = useState(false);
  const ag = agents[g.agent];
  const [ico, tone] = GL_TYPE[g.type] || ["inbox", "indigo"];
  const [pcls, plabel] = GL_POL[g.policy] || ["amber", "Review"];
  const act = (decision) => { setResolving(true); setTimeout(() => onResolve(g.id, decision), 360); };
  return (
    <div className={"approval" + (resolving ? " resolving" : "")}>
      <div className="approval-top">
        <ToneIco tone={tone} name={ico} big />
        <div className="approval-q">
          <b>{g.title}</b>
          <span>{g.company} · {window.fmtMoney(g.value)}</span>
        </div>
        <span className={"chip " + pcls} style={{ height: 20 }}>{plabel}</span>
      </div>
      <div className="approval-preview"><span className="pf">{ag.name}'s draft</span>{(g.draft || g.body).slice(0, 140)}…</div>
      <div className="approval-actions">
        <button className="btn btn-primary btn-sm" onClick={() => act("approved")}><Icon name="check" size={14} sw={2.4} />Approve &amp; run</button>
        <button className="btn btn-ghost btn-sm" style={{ marginLeft: "auto" }} onClick={() => act("declined")}><Icon name="x" size={14} sw={2.4} />Decline</button>
      </div>
    </div>
  );
}

function Dashboard({ agents, onNavigate }) {
  const { THROUGHPUT, AGENT_LOAD, STAGES, RANGES, REP_STATS, REP_SCALE } = window.FL_DATA;
  const feed = useStore((s) => s.feed);
  const dashRange = useStore((s) => s.dashRange);
  const range = RANGES.find((r) => r.id === dashRange) || RANGES[1];
  const rs = range.stats;
  const dashViews = useStore((s) => s.dashViews);
  const activeDashView = useStore((s) => s.activeDashView);
  const repFilter = useStore((s) => s.dashRepFilter);
  const setRepFilter = (f) => FLStore.setDashRepFilter(f);
  const [viewMenu, setViewMenu] = useState(false);
  const [savingView, setSavingView] = useState(false);
  const [newViewName, setNewViewName] = useState("");
  const activeView = dashViews.find((v) => v.id === activeDashView);
  const repScale = REP_SCALE[dashRange] || 1;
  const reps = REP_STATS
    .filter((r) => repFilter === "all" || (repFilter === "people" ? r.kind === "human" : r.kind === "agent"))
    .map((r) => ({ ...r, closedV: Math.max(0, Math.round(r.closed * repScale)), pipeV: Math.round(r.pipeline * repScale), actV: Math.round(r.activities * repScale) }))
    .sort((a, b) => b.pipeV - a.pipeV);
  const repMaxPipe = Math.max(...reps.map((r) => r.pipeV), 1);
  const flags = useStore((s) => s.productFlags);
  const showPosture = window.FLflag(flags, "dashboard", "posture", true);
  const showReps = window.FLflag(flags, "dashboard", "reps", true);
  const showSupport = window.FLflag(flags, "dashboard", "support", true);
  const pending = useStore((s) => s.greenlight.filter((i) => i.status === "pending"));
  const deals = useStore((s) => s.deals);
  const stats = useStore((s) => s.stats);
  const security = useStore((s) => s.security);
  const tickets = useStore((s) => s.tickets);
  const [toast, setToast] = useState(null);

  const SEC_MODE = { auto: ["Live", "var(--green)", "bolt"], semi: ["Analyze only", "var(--amber)", "inbox"], paused: ["Kill switch", "var(--rose)", "pause"] };
  const [secLabel, secColor, secIco] = SEC_MODE[security.mode] || SEC_MODE.auto;
  const guardsOn = Object.values(security.guardrails).filter(Boolean).length;
  const agentsPaused = Object.values(security.agentPaused).filter(Boolean).length;
  const ticketsNeedYou = tickets.filter((t) => t.status === "needs_human" || t.status === "drafted").length;
  const ticketsResolved = tickets.filter((t) => t.status === "deflected" || t.status === "resolved").length;
  const deflectRate = tickets.length ? Math.round((tickets.filter((t) => t.status === "deflected").length / tickets.length) * 100) : 0;

  const PIPELINE_BY_STAGE = STAGES.map((st) => ({ stage: st.id, label: st.name, val: deals.filter((d) => d.stage === st.id).reduce((t, d) => t + d.value, 0) }));
  const openPipeline = deals.filter((d) => d.stage !== "won").reduce((t, d) => t + d.value, 0);

  const resolveApproval = (id, decision) => {
    const g = FLStore.getState().greenlight.find((x) => x.id === id);
    FLStore.resolveGreenlight([id], decision);
    setToast({ verb: decision === "approved" ? "Approved & sent" : "Declined", title: g ? g.title : "", decision });
    setTimeout(() => setToast(null), 3000);
  };

  const pipelineSlices = PIPELINE_BY_STAGE.map(p => ({ ...p, color: STAGES.find(s => s.id === p.stage).color }));
  const totalPipeline = pipelineSlices.reduce((s, x) => s + x.val, 0);
  const now = new Date();
  const hour = now.getHours();
  const greet = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  const dateLabel = now.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
  const agentCount = Object.keys(agents).length;

  return (
    <div className="screen screen-anim">
      {/* greeting */}
      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 7 }}>{dateLabel}</div>
          <h2 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.03em" }}>{greet}, Jordan</h2>
          <p style={{ color: "var(--ink-2)", fontSize: 14.5, marginTop: 5 }}>
            Your {agentCount} agents handled <b style={{ color: "var(--ink)" }}>{rs.tasks.toLocaleString()} tasks</b> {range.greet}.{" "}
            {pending.length > 0
              ? <>{pending.length} thing{pending.length > 1 ? "s" : ""} need{pending.length > 1 ? "" : "s"} your eyes.</>
              : <>You're all caught up.</>}
          </p>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 9, alignItems: "center", flexWrap: "wrap" }}>
          <div style={{ position: "relative" }}>
            <button className="btn btn-ghost" onClick={() => setViewMenu((o) => !o)} style={{ gap: 7 }}>
              <Icon name="layers" size={16} />{activeView ? activeView.name : (activeDashView === "custom" ? "Custom view" : "View")}<Icon name="chevDown" size={14} />
            </button>
            {viewMenu && (
              <>
                <div style={{ position: "fixed", inset: 0, zIndex: 40 }} onClick={() => { setViewMenu(false); setSavingView(false); }} />
                <div style={{ position: "absolute", top: 42, left: 0, minWidth: 240, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-md)", boxShadow: "var(--shadow-xl)", zIndex: 41, padding: 6, animation: "feed-in .15s both" }}>
                  <div style={{ fontSize: 10.5, fontWeight: 650, textTransform: "uppercase", letterSpacing: ".05em", color: "var(--ink-4)", padding: "8px 10px 5px" }}>Dashboard views</div>
                  {dashViews.map((v) => (
                    <div key={v.id} className="wf-menu-act" style={{ background: v.id === activeDashView ? "var(--accent-softer)" : "transparent" }} onClick={() => { FLStore.applyDashView(v.id); setViewMenu(false); }}>
                      <Icon name="gauge" size={14} style={{ color: "var(--ink-3)" }} />
                      <span style={{ flex: 1 }}>{v.name}</span>
                      {v.id === activeDashView && <Icon name="check" size={13} sw={2.4} style={{ color: "var(--accent)" }} />}
                      {!v.builtin && v.id !== activeDashView && <button className="icon-btn" style={{ width: 22, height: 22 }} onClick={(e) => { e.stopPropagation(); FLStore.deleteDashView(v.id); }}><Icon name="x" size={12} /></button>}
                    </div>
                  ))}
                  <div style={{ borderTop: "1px solid var(--line-2)", margin: "5px 0" }} />
                  {savingView ? (
                    <div style={{ padding: "4px 6px", display: "flex", gap: 6 }}>
                      <input autoFocus value={newViewName} onChange={(e) => setNewViewName(e.target.value)} placeholder="View name"
                        onKeyDown={(e) => { if (e.key === "Enter" && newViewName.trim()) { FLStore.saveDashView(newViewName.trim(), { range: dashRange, panels: { posture: showPosture, reps: showReps, support: showSupport }, repFilter }); setSavingView(false); setNewViewName(""); setViewMenu(false); } }}
                        style={{ flex: 1, font: "inherit", fontSize: 13, border: "1px solid var(--accent)", borderRadius: 7, padding: "5px 9px", outline: "none", background: "var(--bg)", color: "var(--ink)" }} />
                      <button className="btn btn-primary btn-sm" onClick={() => { if (newViewName.trim()) { FLStore.saveDashView(newViewName.trim(), { range: dashRange, panels: { posture: showPosture, reps: showReps, support: showSupport }, repFilter }); setSavingView(false); setNewViewName(""); setViewMenu(false); } }}><Icon name="check" size={13} sw={2.4} /></button>
                    </div>
                  ) : (
                    <div className="wf-menu-act" style={{ color: "var(--accent-ink)" }} onClick={() => setSavingView(true)}><Icon name="plus" size={14} sw={2.2} />Save current as view…</div>
                  )}
                </div>
              </>
            )}
          </div>
          <div className="seg dash-range" role="tablist" aria-label="Time range">
            {RANGES.map((r) => (
              <button key={r.id} role="tab" aria-selected={dashRange === r.id} className={dashRange === r.id ? "active" : ""} onClick={() => FLStore.setDashRange(r.id)}>{r.label}</button>
            ))}
          </div>
          <button className="btn btn-ghost" onClick={() => onNavigate && onNavigate("reports")}><Icon name="trend" size={16} />View reports</button>
          <button className="btn btn-primary" onClick={() => onNavigate && onNavigate("workflows")}><Icon name="spark" size={16} />New workflow</button>
        </div>
      </div>

      {/* security posture strip */}
      {showPosture && (
      <div style={{ display: "flex", alignItems: "center", gap: 14, padding: "12px 16px", borderRadius: "var(--r-md)", border: "1px solid var(--line)", background: "var(--surface)", marginBottom: "var(--gap)", boxShadow: "var(--shadow-sm)", flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
          <div style={{ width: 32, height: 32, borderRadius: 9, display: "grid", placeItems: "center", background: "color-mix(in oklch, " + secColor + " 18%, transparent)", color: secColor }}><Icon name="shield" size={17} /></div>
          <div>
            <div style={{ fontSize: 11, color: "var(--ink-3)", fontWeight: 600, textTransform: "uppercase", letterSpacing: ".05em", fontFamily: "var(--mono)" }}>Security posture</div>
            <div style={{ display: "flex", alignItems: "center", gap: 7, marginTop: 1 }}>
              <span style={{ width: 8, height: 8, borderRadius: 99, background: secColor }} />
              <b style={{ fontSize: 14, fontWeight: 700 }}>{secLabel}</b>
            </div>
          </div>
        </div>
        <div style={{ height: 26, width: 1, background: "var(--line)" }} />
        <div style={{ display: "flex", gap: 18, fontSize: 12.5, color: "var(--ink-2)" }}>
          <span><b style={{ fontWeight: 700 }}>{guardsOn}</b> guardrails on</span>
          <span><b style={{ fontWeight: 700 }}>{agentsPaused}</b> agents paused</span>
          <span className="muted" style={{ display: "flex", alignItems: "center", gap: 5 }}><Icon name="check" size={13} sw={2.4} style={{ color: "var(--green)" }} />Greenlight active</span>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 7 }}>
          {security.mode !== "paused"
            ? <button className="btn btn-ghost btn-sm" onClick={() => FLStore.setSecurityMode("paused")}><Icon name="pause" size={14} />Kill switch</button>
            : <button className="btn btn-primary btn-sm" onClick={() => FLStore.setSecurityMode("auto")}><Icon name="bolt" size={14} />Resume agents</button>}
          <button className="btn btn-ghost btn-sm" onClick={() => onNavigate && onNavigate("security")}>Manage<Icon name="chevR" size={13} sw={2.2} /></button>
        </div>
      </div>
      )}

      {/* stats */}
      <div className="stat-grid">
        <StatCard icon="bolt" tone="indigo" label="Tasks auto-handled" value={rs.tasks} delta={rs.tasksD} deltaDir="up" deltaLabel={range.cmp}
          spark={rs.taskSpark} />
        <StatCard icon="trend" tone="green" label="Pipeline value" value={openPipeline} prefix="$" delta={rs.pipelineD} deltaDir="up" deltaLabel={range.cmp}
          spark={rs.pipeSpark} sparkColor="var(--green)" fmt={(n)=>(n/1000).toFixed(1)+"k"} />
        <StatCard icon="clock" tone="amber" label="Hours saved" value={rs.hours} suffix="h" delta={rs.hoursD} deltaDir="up" deltaLabel={range.cmp}
          spark={rs.hourSpark} sparkColor="var(--amber)" />
        <StatCard icon="checkCircle" tone="indigo" label="Auto-approval rate" value={rs.approval} suffix="%" delta={rs.apprD} deltaDir="up" deltaLabel={range.cmp}
          spark={rs.apprSpark} />
      </div>

      {/* main grid */}
      <div className="dash-grid section-gap">
        {/* left col */}
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--gap)" }}>
          <div className="card">
            <div className="card-head">
              <h3>Workflow throughput</h3>
              <span className="sub">{range.sub}</span>
              <div style={{ display: "flex", gap: 14, marginLeft: "auto" }}>
                <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11.5, color: "var(--ink-2)", fontWeight: 600 }}>
                  <i style={{ width: 9, height: 9, borderRadius: 2, background: "var(--accent)" }} />Agent</span>
                <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11.5, color: "var(--ink-2)", fontWeight: 600 }}>
                  <i style={{ width: 9, height: 3, borderRadius: 2, background: "var(--ink-4)" }} />Human</span>
              </div>
            </div>
            <div className="card-pad"><AreaChart key={dashRange} data={range.throughput} /></div>
          </div>

          {/* live agent feed */}
          <div className="card">
            <div className="card-head">
              <span className="live-dot" />
              <h3>Live agent activity</h3>
              <span className="sub" style={{ marginLeft: "auto" }}>auto-refreshing</span>
            </div>
            <div className="feed">
              {feed.map((f, i) => (
                <div className="feed-item" key={f._k || i}>
                  <div className="feed-rail">
                    <ToneIco tone={f.tone} name={f.ico} />
                  </div>
                  <div className="feed-body">
                    <SafeHtml as="p" html={f.html} />
                    <div className="feed-meta">
                      <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
                        <span style={{ width: 6, height: 6, borderRadius: 99, background: agents[f.agent].color }} />
                        {agents[f.agent].name}
                      </span>· {f.meta}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* right col */}
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--gap)" }}>
          {/* approvals, live Greenlight queue */}
          <div className="card">
            <div className="card-head">
              <div className="feed-ico" style={{ background: "var(--amber-soft)", color: "oklch(0.5 0.12 60)", width: 30, height: 30 }}><Icon name="inbox" size={15} /></div>
              <h3>Greenlight</h3>
              <span className="chip amber" style={{ marginLeft: "auto" }}>{pending.length} pending</span>
            </div>
            <div className="card-pad">
              {pending.length === 0 ? (
                <div style={{ textAlign: "center", padding: "26px 10px", color: "var(--ink-3)" }}>
                  <div style={{ width: 44, height: 44, borderRadius: 12, background: "var(--green-soft)", color: "var(--green)", display: "grid", placeItems: "center", margin: "0 auto 12px" }}><Icon name="check" size={22} sw={2.4} /></div>
                  <p style={{ fontSize: 13.5, fontWeight: 600, color: "var(--ink)" }}>All clear</p>
                  <p style={{ fontSize: 12.5, marginTop: 3 }}>Your agents are running autonomously.</p>
                </div>
              ) : (
                <>
                  {pending.slice(0, 3).map((g) => (
                    <GreenlightMiniCard key={g.id} g={g} agents={agents} onResolve={resolveApproval} />
                  ))}
                  <button className="btn btn-ghost btn-sm" style={{ width: "100%", justifyContent: "center", marginTop: 2 }} onClick={() => onNavigate && onNavigate("approvals")}>
                    Open Greenlight{pending.length > 3 ? ` · ${pending.length - 3} more` : ""}<Icon name="arrowRight" size={14} sw={2.2} />
                  </button>
                </>
              )}
            </div>
          </div>

          {/* pipeline donut */}
          <div className="card">
            <div className="card-head"><h3>Pipeline by stage</h3></div>
            <div className="card-pad" style={{ display: "flex", alignItems: "center", gap: 20 }}>
              <div style={{ position: "relative", flexShrink: 0 }}>
                <Donut slices={pipelineSlices} />
                <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", textAlign: "center" }}>
                  <div>
                    <div style={{ fontSize: 18, fontWeight: 760, letterSpacing: "-.02em" }}>${(totalPipeline/1000).toFixed(0)}k</div>
                    <div style={{ fontSize: 10.5, color: "var(--ink-3)", fontFamily: "var(--mono)" }}>total</div>
                  </div>
                </div>
              </div>
              <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 9 }}>
                {pipelineSlices.map((p) => (
                  <div key={p.stage} style={{ display: "flex", alignItems: "center", gap: 9 }}>
                    <span style={{ width: 9, height: 9, borderRadius: 3, background: p.color }} />
                    <span style={{ fontSize: 12.5, color: "var(--ink-2)", fontWeight: 550, flex: 1 }}>{p.label}</span>
                    <span style={{ fontSize: 12.5, fontWeight: 700, fontFamily: "var(--mono)" }}>${(p.val/1000).toFixed(1)}k</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* agent load */}
          <div className="card">
            <div className="card-head"><h3>Agent workload</h3><span className="sub" style={{ marginLeft: "auto" }}>today</span></div>
            <div className="card-pad"><LoadBars rows={AGENT_LOAD} agents={agents} /></div>
          </div>

          {/* frontline support snapshot */}
          {showSupport && (
          <div className="card">
            <div className="card-head">
              <div className="feed-ico" style={{ width: 30, height: 30, background: "var(--green-soft)", color: "oklch(0.42 0.12 152)" }}><Icon name="inbox" size={15} /></div>
              <h3>Support desk</h3>
              <span className="sub" style={{ marginLeft: "auto" }}>🐧 Pip</span>
            </div>
            <div className="card-pad" style={{ display: "flex", flexDirection: "column", gap: 13 }}>
              <div style={{ display: "flex", gap: 18 }}>
                <div><div style={{ fontSize: 24, fontWeight: 770, letterSpacing: "-.03em", color: "var(--green)" }}>{deflectRate}%</div><div style={{ fontSize: 11, color: "var(--ink-3)" }}>deflected</div></div>
                <div><div style={{ fontSize: 24, fontWeight: 770, letterSpacing: "-.03em" }}>{ticketsResolved}</div><div style={{ fontSize: 11, color: "var(--ink-3)" }}>resolved today</div></div>
                <div><div style={{ fontSize: 24, fontWeight: 770, letterSpacing: "-.03em", color: ticketsNeedYou ? "var(--amber)" : "var(--ink)" }}>{ticketsNeedYou}</div><div style={{ fontSize: 11, color: "var(--ink-3)" }}>need you</div></div>
              </div>
              <button className="btn btn-ghost btn-sm" style={{ width: "100%", justifyContent: "center" }} onClick={() => onNavigate && onNavigate("frontline")}>
                {ticketsNeedYou ? `Review ${ticketsNeedYou} ticket${ticketsNeedYou > 1 ? "s" : ""}` : "Open Frontline"}<Icon name="arrowRight" size={14} sw={2.2} />
              </button>
            </div>
          </div>
          )}
        </div>
      </div>

      {/* CRM rep performance */}
      {showReps && (
      <div className="card section-gap">
        <div className="card-head">
          <div className="feed-ico" style={{ width: 30, height: 30, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="trend" size={15} /></div>
          <h3>Rep performance</h3>
          <span className="sub">{range.sub}</span>
          <div className="seg" style={{ marginLeft: "auto" }} role="tablist" aria-label="Rep filter">
            {[["all", "All"], ["people", "People"], ["agents", "Agents"]].map(([id, label]) => (
              <button key={id} role="tab" aria-selected={repFilter === id} className={repFilter === id ? "active" : ""} onClick={() => setRepFilter(id)}>{label}</button>
            ))}
          </div>
          <button className="btn btn-ghost btn-sm" onClick={() => onNavigate && onNavigate("sell")}>Open Sell<Icon name="arrowRight" size={13} sw={2.2} /></button>
        </div>
        <div className="rep-table" role="table">
          <div className="rep-row rep-head" role="row">
            <span role="columnheader">Rep</span>
            <span role="columnheader" style={{ textAlign: "right" }}>Closed</span>
            <span role="columnheader">Pipeline</span>
            <span role="columnheader" style={{ textAlign: "right" }}>Win rate</span>
            <span role="columnheader" style={{ textAlign: "right" }}>Activities</span>
          </div>
          {reps.map((r, i) => (
            <div className="rep-row" role="row" key={r.name}>
              <span role="cell" style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
                <span style={{ fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)", width: 14 }}>{i + 1}</span>
                <span className="avatar" style={{ background: r.color, width: 28, height: 28, fontSize: r.kind === "agent" ? 14 : 11 }}>{r.init}</span>
                <span style={{ minWidth: 0 }}>
                  <b style={{ fontSize: 13, fontWeight: 650, display: "flex", alignItems: "center", gap: 6, whiteSpace: "nowrap" }}>{r.you ? "You" : r.name}{r.kind === "agent" && <span className="chip" style={{ height: 17, fontSize: 9.5, padding: "0 6px" }}>agent</span>}</b>
                </span>
              </span>
              <span role="cell" style={{ textAlign: "right", fontWeight: 700, fontFamily: "var(--mono)", fontSize: 13 }}>{r.closedV}</span>
              <span role="cell" style={{ display: "flex", alignItems: "center", gap: 9 }}>
                <span className="rep-bar"><span style={{ width: (r.pipeV / repMaxPipe * 100) + "%", background: r.color }} /></span>
                <span style={{ fontSize: 12.5, fontWeight: 650, fontFamily: "var(--mono)", whiteSpace: "nowrap" }}>${(r.pipeV / 1000).toFixed(1)}k</span>
              </span>
              <span role="cell" style={{ textAlign: "right", fontSize: 12.5, fontWeight: 600 }}>{r.winRate}%</span>
              <span role="cell" style={{ textAlign: "right", fontSize: 12.5, color: "var(--ink-2)", fontFamily: "var(--mono)" }}>{r.actV.toLocaleString()}</span>
            </div>
          ))}
        </div>
      </div>
      )}

      {/* toast */}
      {toast && (
        <div style={{ position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)", zIndex: 70,
          background: "var(--ink)", color: "var(--bg)", borderRadius: "var(--r-md)", padding: "12px 18px",
          display: "flex", alignItems: "center", gap: 11, boxShadow: "var(--shadow-xl)", animation: "feed-in .3s both", maxWidth: "90vw" }}>
          <Icon name={toast.decision === "approved" ? "checkCircle" : toast.decision === "declined" ? "xCircle" : "note"} size={18} />
          <span style={{ fontSize: 13.5, fontWeight: 600 }}>{toast.verb}</span>
          <span style={{ fontSize: 13, opacity: .7, maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{toast.title}</span>
        </div>
      )}
    </div>
  );
}

window.Dashboard = Dashboard;
window.StatCard = StatCard;
window.ToneIco = ToneIco;
