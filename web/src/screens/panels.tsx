// @ts-nocheck
import React from "react";
// panels.jsx, slide-over deal detail + command palette
const { useState, useEffect, useRef, useMemo } = React;

function tlNode(tone, name) {
  const map = {
    indigo: ["var(--accent-soft)", "var(--accent-ink)"],
    amber:  ["var(--amber-soft)", "oklch(0.5 0.12 60)"],
    green:  ["var(--green-soft)", "oklch(0.42 0.12 152)"],
  };
  const [bg, fg] = map[tone] || map.indigo;
  return <div className="tl-node" style={{ background: bg, color: fg }}><Icon name={name} size={13} /></div>;
}

function buildTimeline(deal, agents) {
  const a = agents[deal.agent];
  return [
    { tone: "green",  ico: "checkCircle", who: a.name, t: "just now",   txt: deal.agentNote },
    { tone: "indigo", ico: "mail",        who: "Echo", t: "2h ago",     txt: `Sent a personalized follow-up to ${deal.person.split(" ")[0]} referencing their recent site activity.` },
    { tone: "indigo", ico: "target",      who: "Scout",t: "Yesterday",  txt: `Scored the account ${Math.min(99, 70 + (deal.value % 30))}/100 on fit and enriched 9 firmographic fields.` },
    { tone: "amber",  ico: "spark",       who: "Scout",t: "2 days ago", txt: `Detected ${deal.co} as a new inbound lead from your website and assigned a working agent.` },
  ];
}

function SlideOver({ deal, agents, stages, onClose }) {
  const [mounted, setMounted] = useState(false);
  const [soToast, setSoToast] = useState(null);
  const fire = (msg, pts) => { setSoToast(msg); if (pts) window.FLStore.addPoints(pts, { kind: "followup" }); setTimeout(() => setSoToast(null), 2400); };
  useEffect(() => {
    if (deal) { requestAnimationFrame(() => setMounted(true)); }
    else setMounted(false);
  }, [deal]);
  useEffect(() => {
    const k = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);

  if (!deal) return null;
  const a = agents[deal.agent], st = stages.find((s) => s.id === deal.stage), heat = window.HEAT[deal.heat];
  const tl = buildTimeline(deal, agents);
  const team = (window.FLStore.getState().team) || [];
  const human = deal.human ? team.find((m) => m.id === deal.human) : null;

  return (
    <>
      <div className={"scrim" + (mounted ? " show" : "")} onClick={onClose} />
      <div className={"slideover" + (mounted ? " show" : "")}>
        <div className="so-head">
          <button className="icon-btn" style={{ position: "absolute", top: 14, right: 14 }} onClick={onClose}><Icon name="x" size={18} /></button>
          <div style={{ display: "flex", alignItems: "center", gap: 13, marginBottom: 14, paddingRight: 40 }}>
            <div className="deal-co" style={{ background: deal.coColor, width: 46, height: 46, fontSize: 16, borderRadius: 12 }}>{deal.init}</div>
            <div>
              <h2 style={{ fontSize: 19, fontWeight: 720, letterSpacing: "-.02em" }}>{deal.co}</h2>
              <p style={{ fontSize: 13, color: "var(--ink-3)", marginTop: 2 }}>{deal.person}</p>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span className="chip"><span className="cdot" style={{ background: st.color }} />{st.name}</span>
            <span className={"chip " + heat.cls}><Icon name={heat.ico} size={11} sw={2.2} />{heat.label}</span>
            <span style={{ marginLeft: "auto", fontSize: 19, fontWeight: 760, letterSpacing: "-.02em" }}>{window.fmtMoney(deal.value)}</span>
          </div>
        </div>

        <div className="so-body">
          {/* assignees, agent + human */}
          <div>
            <div className="so-section-label">Assigned to</div>
            <div className="card" style={{ background: "var(--accent-softer)", border: "1px solid var(--accent-soft)", boxShadow: "none" }}>
              <div style={{ padding: 13, display: "flex", alignItems: "center", gap: 11 }}>
                <div className="avatar" style={{ background: a.color, width: 38, height: 38, fontSize: 13 }}>{a.init}</div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 7 }}><b style={{ fontSize: 13.5, fontWeight: 700 }}>{a.name}</b><span className="live-dot" style={{ width: 6, height: 6 }} /></div>
                  <p style={{ fontSize: 11.5, color: "var(--accent-ink)", marginTop: 1 }}>{a.role} agent · actively working</p>
                </div>
                <select value={deal.agent} onChange={(e) => window.FLStore.assignDeal(deal.id, { agent: e.target.value })}
                  style={{ height: 32, border: "1px solid var(--accent-soft)", borderRadius: "var(--r-sm)", padding: "0 9px", background: "var(--surface)", color: "var(--ink)", fontSize: 12, fontWeight: 600, maxWidth: 120 }}>
                  {Object.values(agents).map((ag) => <option key={ag.id} value={ag.id}>{ag.init} {ag.name}</option>)}
                </select>
              </div>
              <div style={{ borderTop: "1px solid var(--accent-soft)", padding: 13, display: "flex", alignItems: "center", gap: 11 }}>
                {human
                  ? <><div className="avatar" style={{ background: human.color, width: 38, height: 38, fontSize: 12 }}>{human.init}</div>
                      <div style={{ flex: 1, minWidth: 0 }}><b style={{ fontSize: 13.5, fontWeight: 700 }}>{human.name}</b><p style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 1 }}>{human.role} · human owner</p></div></>
                  : <><div style={{ width: 38, height: 38, borderRadius: 99, border: "1.5px dashed var(--ink-4)", display: "grid", placeItems: "center", color: "var(--ink-4)" }}><Icon name="users" size={16} /></div>
                      <div style={{ flex: 1 }}><b style={{ fontSize: 13.5, fontWeight: 600, color: "var(--ink-3)" }}>No human assigned</b><p style={{ fontSize: 11.5, color: "var(--ink-4)", marginTop: 1 }}>Optional teammate owner</p></div></>}
                <select value={deal.human || ""} onChange={(e) => window.FLStore.assignDeal(deal.id, { human: e.target.value || null })}
                  style={{ height: 32, border: "1px solid var(--line)", borderRadius: "var(--r-sm)", padding: "0 9px", background: "var(--surface)", color: "var(--ink)", fontSize: 12, fontWeight: 600, maxWidth: 120 }}>
                  <option value="">Unassigned</option>
                  {team.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                </select>
              </div>
            </div>
          </div>

          {/* what the agent is doing next */}
          <div>
            <div className="so-section-label">Agent's next move</div>
            <div className="approval-preview" style={{ marginBottom: 0 }}>
              <span className="pf">{a.name} suggests</span>
              {deal.stage === "won"
                ? "Kick off onboarding sequence and schedule a 30-day check-in call automatically."
                : `Send a tailored follow-up to ${deal.person.split(" ")[0]} within 24h, then advance to the next stage if they reply positively.`}
              <div style={{ display: "flex", gap: 8, marginTop: 11 }}>
                <button className="btn btn-primary btn-sm" onClick={() => { fire(`${a.name} is on it, you'll see the next step in your feed`, 8); window.FLStore.pushFeed && window.FLStore.pushFeed({ agent: deal.agent, ico: "spark", tone: "indigo", html: `Picking up the next step on <b>${deal.co}</b>`, meta: "just now · you approved" }); }}><Icon name="check" size={14} sw={2.4} />Let {a.name} proceed</button>
                <button className="btn btn-ghost btn-sm" onClick={() => fire("Opening this step to adjust…")}><Icon name="note" size={14} />Adjust</button>
              </div>
            </div>
          </div>

          {/* deal properties (HubSpot-style) */}
          <div>
            <div className="so-section-label">Deal properties</div>
            <div className="kv">
              <span className="k">Deal owner</span><span className="v">{a.name} (agent){human ? " · " + human.name : ""}</span>
              <span className="k">Deal type</span><span className="v">{deal.dealType || "New business"}</span>
              <span className="k">Priority</span><span className="v" style={{ display: "flex", alignItems: "center", gap: 6 }}><span style={{ width: 7, height: 7, borderRadius: 99, background: deal.priority === "High" ? "var(--rose)" : deal.priority === "Medium" ? "var(--amber)" : "var(--ink-4)" }} />{deal.priority || "Medium"}</span>
              {deal.persuadable > 0 && <><span className="k">Persuadable score</span><span className="v" style={{ display: "flex", alignItems: "center", gap: 8 }}><span style={{ flex: "0 0 70px", maxWidth: 70 }}><span className="meter" style={{ height: 6, display: "block" }}><span style={{ width: deal.persuadable + "%", background: deal.persuadable >= 60 ? "var(--green)" : deal.persuadable >= 40 ? "var(--amber)" : "var(--ink-4)" }} /></span></span><b style={{ fontFamily: "var(--mono)", fontSize: 12.5 }}>{deal.persuadable}</b><span style={{ fontSize: 11, color: "var(--ink-4)" }}>{deal.persuadable >= 60 ? "act now" : deal.persuadable >= 40 ? "winnable" : "low lift"}</span></span></>}
              <span className="k">Lead source</span><span className="v">{deal.source || "Direct traffic"}</span>
              <span className="k">Expected close</span><span className="v">{deal.closeDate || "in ~2 wks"}</span>
              <span className="k">Deal age</span><span className="v">{deal.createdDays != null ? deal.createdDays + " days" : "new"}</span>
              <span className="k">Last activity</span><span className="v" style={{ display: "flex", alignItems: "center", gap: 7 }}>{deal.lastActivity || (deal.days === 0 ? "today" : deal.days + "d ago")}{deal.stage !== "won" && deal.stage !== "lost" && (deal.stageDays != null ? deal.stageDays : deal.days) >= 5 && <span className="chip" style={{ height: 18, fontSize: 10, background: "var(--rose-soft)", color: "oklch(0.48 0.14 18)" }}><Icon name="bolt" size={10} />Stalled</span>}</span>
              <span className="k">Times contacted</span><span className="v">{deal.timesContacted != null ? deal.timesContacted : "—"}</span>
            </div>
          </div>

          {/* contact & company */}
          <div>
            <div className="so-section-label">Contact &amp; company</div>
            <div className="kv">
              <span className="k">Contact</span><span className="v">{deal.person}{deal.title ? " · " + deal.title : ""}</span>
              <span className="k">Email</span><span className="v">{deal.email}</span>
              <span className="k">Phone</span><span className="v">{deal.phone}</span>
              <span className="k">Company</span><span className="v">{deal.co}</span>
              <span className="k">Domain</span><span className="v">{deal.domain || "—"}</span>
              <span className="k">Industry</span><span className="v">{deal.industry || "—"}</span>
              <span className="k">Employees</span><span className="v">{deal.employees || "—"}</span>
            </div>
          </div>

          {/* line items */}
          {deal.lineItems && deal.lineItems.length > 0 && (
            <div>
              <div className="so-section-label">Line items</div>
              <div style={{ border: "1px solid var(--line-2)", borderRadius: "var(--r-sm)", overflow: "hidden" }}>
                {deal.lineItems.map((li, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 12px", borderBottom: "1px solid var(--line-2)" }}>
                    <Icon name="layers" size={14} style={{ color: "var(--ink-4)", flexShrink: 0 }} />
                    <span style={{ flex: 1, fontSize: 13 }}>{li.name}</span>
                    <span style={{ fontSize: 11.5, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>×{li.qty}</span>
                    <span style={{ fontSize: 13, fontWeight: 650, fontFamily: "var(--mono)" }}>{window.fmtMoney(li.price)}</span>
                  </div>
                ))}
                <div style={{ display: "flex", alignItems: "center", padding: "10px 12px", background: "var(--surface-2)" }}>
                  <span style={{ flex: 1, fontSize: 12.5, fontWeight: 600, color: "var(--ink-2)" }}>Total</span>
                  <span style={{ fontSize: 14, fontWeight: 760, fontFamily: "var(--mono)" }}>{window.fmtMoney(deal.value)}</span>
                </div>
              </div>
            </div>
          )}

          {/* tasks & next steps */}
          <div>
            <div className="so-section-label">Tasks &amp; next steps</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {(deal.tasks || []).map((t) => (
                <div key={t.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 11px", border: "1px solid var(--line-2)", borderRadius: "var(--r-sm)" }}>
                  <button onClick={() => window.FLStore.toggleDealTask(deal.id, t.id)} style={{ width: 19, height: 19, borderRadius: 6, border: "1.5px solid " + (t.done ? "var(--green)" : "var(--ink-4)"), background: t.done ? "var(--green)" : "transparent", display: "grid", placeItems: "center", flexShrink: 0 }}>{t.done && <Icon name="check" size={12} sw={3} style={{ color: "#fff" }} />}</button>
                  <span style={{ flex: 1, fontSize: 13, textDecoration: t.done ? "line-through" : "none", color: t.done ? "var(--ink-4)" : "var(--ink)" }}>{t.title}</span>
                  {!t.done && <span className="chip" style={{ height: 18, fontSize: 10, background: t.due === "overdue" ? "var(--rose-soft)" : "var(--surface-2)", color: t.due === "overdue" ? "oklch(0.48 0.14 18)" : "var(--ink-3)" }}>{t.due}</span>}
                </div>
              ))}
              {(deal.tasks || []).length === 0 && <p style={{ fontSize: 12.5, color: "var(--ink-4)" }}>No open tasks.</p>}
              <button className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start", marginTop: 2 }} onClick={() => { const title = prompt && prompt("New task / next step"); if (title) { window.FLStore.addDealTask(deal.id, title); fire("Task added", 0); } }}><Icon name="plus" size={13} sw={2.2} />Add task</button>
            </div>
          </div>

          {/* activity timeline */}
          <div>
            <div className="so-section-label">Activity timeline</div>
            <div className="timeline">
              {(deal.timeline && deal.timeline.length ? deal.timeline : tl).map((item, i, arr) => (
                <div className="tl-item" key={i}>
                  <div className="tl-rail">{tlNode(item.tone, item.ico)}{i < arr.length - 1 && <div className="tl-line" />}</div>
                  <div className="tl-content">
                    <p>{item.who ? <><b style={{ fontWeight: 650 }}>{item.who}</b> {item.txt}</> : item.txt}</p>
                    <div className="tl-time">{item.t}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="so-foot">
          <button className="btn btn-primary" style={{ flex: 1 }} onClick={() => fire(`Drafting an email to ${deal.person.split(" ")[0]}…`, 5)}><Icon name="mail" size={16} />Email {deal.person.split(" ")[0]}</button>
          <button className="btn btn-ghost btn-icon-only" title="Call" onClick={() => fire(`Calling ${deal.phone || deal.person.split(" ")[0]}…`, 5)}><Icon name="phone" size={16} /></button>
          <button className="btn btn-ghost btn-icon-only" title="Schedule" onClick={() => fire("Opening your calendar…", 5)}><Icon name="calendar" size={16} /></button>
          {deal.stage !== "won" && deal.stage !== "lost" && (
            <button className="btn btn-ghost btn-icon-only" title="Mark lost" onClick={() => { const reasons = ["Price", "Went silent", "Chose a competitor", "Bad timing", "Not a fit"]; const r = prompt && prompt("Why was this deal lost?\n(Price, Went silent, Chose a competitor, Bad timing, Not a fit)", "Price"); if (r) { window.FLStore.markDealLost(deal.id, r); onClose(); } }}><Icon name="x" size={16} sw={2.2} style={{ color: "var(--rose)" }} /></button>
          )}
        </div>
      </div>
      {soToast && (
        <div style={{ position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)", zIndex: 80, background: "var(--ink)", color: "var(--bg)", borderRadius: "var(--r-md)", padding: "12px 18px", display: "flex", alignItems: "center", gap: 10, boxShadow: "var(--shadow-xl)", animation: "feed-in .3s both", maxWidth: "90vw" }}>
          <Icon name="checkCircle" size={18} /><span style={{ fontSize: 13.5, fontWeight: 600 }}>{soToast}</span>
        </div>
      )}
    </>
  );
}

function CommandPalette({ open, onClose, onNavigate, onChat, onSetup, onTour }) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const inputRef = useRef(null);

  const groups = useMemo(() => [
    { label: "Navigate", items: [
      { id: "go-dashboard", icon: "grid", title: "Command Center", sub: "Dashboard overview", act: () => onNavigate("dashboard") },
      { id: "go-crm", icon: "users", title: "Uplift", sub: "Pipeline & deals", act: () => onNavigate("crm") },
      { id: "go-workflows", icon: "workflow", title: "Workflow builder", sub: "Compose an automation", act: () => onNavigate("workflows") },
      { id: "go-approvals", icon: "inbox", title: "Greenlight", sub: "Sign-off queue · 3 pending", act: () => onNavigate("approvals") },
    ]},
    { label: "Actions", items: [
      { id: "ask", icon: "bolt", title: "Ask your agents to do something", sub: "Open the agent chat", act: () => onChat && onChat() },
      { id: "new-wf", icon: "spark", title: "Create a new workflow", sub: "Drag nodes to compose it", act: () => onNavigate("workflows") },
      { id: "new-deal", icon: "plus", title: "Add a deal", sub: "Scout will enrich it", act: () => onNavigate("crm") },
      { id: "setup", icon: "layers", title: "Run setup again", sub: "Replay onboarding", act: () => onSetup && onSetup() },
      { id: "tour", icon: "spark", title: "Take the product tour", sub: "A quick guided walkthrough", act: () => onTour && onTour() },
    ]},
    { label: "Agents", items: [
      { id: "ag-scout", icon: "target", title: "Scout, Lead research", sub: "148 tasks today", act: () => onNavigate("dashboard") },
      { id: "ag-nadia", icon: "mail", title: "Nadia, Outreach", sub: "96 tasks today", act: () => onNavigate("dashboard") },
      { id: "ag-margo", icon: "doc", title: "Margo, Quoting", sub: "64 tasks today", act: () => onNavigate("dashboard") },
    ]},
  ], [onNavigate]);

  const flat = useMemo(() => {
    const f = [];
    groups.forEach((g) => g.items.forEach((it) => {
      if (!q || (it.title + it.sub).toLowerCase().includes(q.toLowerCase())) f.push({ ...it, group: g.label });
    }));
    return f;
  }, [groups, q]);

  useEffect(() => { setSel(0); }, [q]);
  useEffect(() => {
    if (open) { setQ(""); setTimeout(() => inputRef.current && inputRef.current.focus(), 40); }
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const k = (e) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(flat.length - 1, s + 1)); }
      else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(0, s - 1)); }
      else if (e.key === "Enter") { e.preventDefault(); const it = flat[sel]; if (it) { it.act(); onClose(); } }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [open, flat, sel, onClose]);

  if (!open) return null;
  let idx = -1;
  return (
    <div className={"cmdk-scrim show"} onClick={onClose}>
      <div className="cmdk" onClick={(e) => e.stopPropagation()}>
        <div className="cmdk-input">
          <Icon name="search" size={19} style={{ color: "var(--ink-3)" }} />
          <input ref={inputRef} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search or ask your agents to do something…" />
          <span className="kbd">ESC</span>
        </div>
        <div className="cmdk-list">
          {flat.length === 0 && <div style={{ padding: "28px 12px", textAlign: "center", color: "var(--ink-3)", fontSize: 13 }}>No results for “{q}”</div>}
          {groups.map((g) => {
            const items = g.items.filter((it) => !q || (it.title + it.sub).toLowerCase().includes(q.toLowerCase()));
            if (items.length === 0) return null;
            return (
              <div key={g.label}>
                <div className="cmdk-group-label">{g.label}</div>
                {items.map((it) => {
                  idx++;
                  const myIdx = idx;
                  return (
                    <div key={it.id} className={"cmdk-item" + (myIdx === sel ? " sel" : "")}
                      onMouseEnter={() => setSel(myIdx)}
                      onClick={() => { it.act(); onClose(); }}>
                      <div className="cmdk-ico"><Icon name={it.icon} size={17} /></div>
                      <div className="cmdk-item-txt"><b>{it.title}</b><span>{it.sub}</span></div>
                      {myIdx === sel && <Icon name="arrowRight" size={15} style={{ color: "var(--accent-ink)" }} />}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
        <div className="cmdk-foot">
          <span><span className="kbd">↑</span><span className="kbd">↓</span> navigate</span>
          <span><span className="kbd">↵</span> select</span>
          <span style={{ marginLeft: "auto", fontFamily: "var(--mono)" }}>Friesen Labs</span>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { SlideOver, CommandPalette });
