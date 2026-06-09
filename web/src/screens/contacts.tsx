// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// contacts.jsx — Contacts & Companies: first-class records derived from deals

const fmtM = (n) => "$" + n.toLocaleString();
const cScore = (d) => d.persuadable || (d.heat === "hot" ? 82 : d.heat === "warm" ? 58 : 33);
const cTags = (d) => { const t = [d.source]; if (d.heat === "hot") t.push("Hot"); if (d.dealType === "Existing business") t.push("Customer"); else t.push("Prospect"); if (d.priority === "High") t.push("Priority"); return t.slice(0, 3); };

function deriveCompanies(deals) {
  const map = {};
  deals.forEach((d) => {
    if (!map[d.co]) map[d.co] = { name: d.co, init: d.init, color: d.coColor, domain: d.domain, industry: d.industry, employees: d.employees, deals: [], contacts: new Set() };
    map[d.co].deals.push(d);
    map[d.co].contacts.add(d.person);
  });
  return Object.values(map).map((c) => ({ ...c, contacts: [...c.contacts],
    value: c.deals.reduce((s, d) => s + d.value, 0),
    won: c.deals.filter((d) => d.stage === "won").length,
    open: c.deals.filter((d) => d.stage !== "won" && d.stage !== "lost").length }));
}
function deriveContacts(deals) {
  return deals.map((d) => ({ id: d.id, name: d.person, title: d.title, email: d.email, phone: d.phone, co: d.co, coColor: d.coColor,
    heat: d.heat, agent: d.agent, value: d.value, stage: d.stage, lastActivity: d.lastActivity, source: d.source, deal: d }));
}

function Contacts({ agents, onNavigate, onOpenDeal }) {
  const deals = useStore((s) => s.deals);
  const [tab, setTab] = useState("contacts");
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(null); // {kind, data}
  const companies = deriveCompanies(deals);
  const contacts = deriveContacts(deals);
  const HEAT = window.HEAT || {};

  const fc = contacts.filter((c) => !q || (c.name + c.co + c.email + (c.title || "")).toLowerCase().includes(q.toLowerCase()));
  const fco = companies.filter((c) => !q || (c.name + (c.industry || "") + (c.domain || "")).toLowerCase().includes(q.toLowerCase()));

  return (
    <div className="screen screen-anim">
      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 7 }}>People &amp; organizations</div>
          <h2 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.03em" }}>Contacts</h2>
          <p style={{ color: "var(--ink-2)", fontSize: 14.5, marginTop: 5 }}>Every person and company you work with, with their deals and history in one place.</p>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 9 }}>
          <button className="btn btn-ghost" onClick={() => onNavigate && onNavigate("crm")}><Icon name="users" size={16} />Open pipeline</button>
        </div>
      </div>

      <div className="crm-toolbar">
        <div className="seg">
          <button className={tab === "contacts" ? "active" : ""} onClick={() => setTab("contacts")}><Icon name="users" size={15} />Contacts <span style={{ opacity: .6, fontFamily: "var(--mono)", fontSize: 11 }}>{contacts.length}</span></button>
          <button className={tab === "companies" ? "active" : ""} onClick={() => setTab("companies")}><Icon name="building" size={15} />Companies <span style={{ opacity: .6, fontFamily: "var(--mono)", fontSize: 11 }}>{companies.length}</span></button>
        </div>
        <div className="search-trigger" style={{ cursor: "text", marginLeft: "auto", minWidth: 200 }}>
          <Icon name="search" size={15} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={"Search " + tab + "…"} style={{ border: "none", outline: "none", background: "none", flex: 1, fontSize: 13, color: "var(--ink)" }} />
        </div>
      </div>

      {tab === "contacts" ? (
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr><th>Name</th><th>Company</th><th className="num">Score</th><th>Owner agent</th><th>Tags</th><th className="num">Deal</th><th>Last activity</th></tr></thead>
            <tbody>
              {fc.map((c) => { const a = agents[c.agent]; const sc = cScore(c.deal); return (
                <tr key={c.id} onClick={() => setSel({ kind: "contact", data: c })} style={{ cursor: "pointer" }}>
                  <td><span className="agent-tag"><div className="avatar" style={{ background: c.coColor, fontSize: 10 }}>{c.name.split(" ").map((w) => w[0]).slice(0, 2).join("")}</div><b style={{ fontWeight: 650 }}>{c.name}</b></span></td>
                  <td style={{ color: "var(--ink-2)" }}>{c.co}</td>
                  <td className="num"><span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontFamily: "var(--mono)", fontWeight: 700, color: sc >= 65 ? "var(--green)" : sc >= 45 ? "oklch(0.5 0.12 60)" : "var(--ink-3)" }}><span style={{ width: 6, height: 6, borderRadius: 99, background: sc >= 65 ? "var(--green)" : sc >= 45 ? "var(--amber)" : "var(--ink-4)" }} />{sc}</span></td>
                  <td>{a ? <span className="agent-tag"><div className="avatar" style={{ background: a.color, fontSize: 9, width: 20, height: 20 }}>{a.init}</div>{a.name}</span> : "—"}</td>
                  <td><div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>{cTags(c.deal).map((t) => <span key={t} className="chip" style={{ height: 18, fontSize: 9.5, padding: "0 6px" }}>{t}</span>)}</div></td>
                  <td className="num" style={{ fontWeight: 650 }}>{fmtM(c.value)}</td>
                  <td style={{ color: "var(--ink-3)", fontSize: 12.5 }}>{c.lastActivity}</td>
                </tr>
              ); })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="kb-grid">
          {fco.map((c) => (
            <button key={c.name} className="card kb-card" onClick={() => setSel({ kind: "company", data: c })}>
              <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
                <div className="deal-co" style={{ background: c.color, width: 38, height: 38, fontSize: 14, borderRadius: 11 }}>{c.init}</div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <b style={{ fontSize: 14.5, fontWeight: 700, display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.name}</b>
                  <span style={{ fontSize: 11.5, color: "var(--ink-4)" }}>{c.industry} · {c.employees}</span>
                </div>
              </div>
              <div style={{ display: "flex", gap: 16, marginTop: 14 }}>
                <div><div style={{ fontSize: 17, fontWeight: 760 }}>{fmtM(c.value)}</div><div style={{ fontSize: 11, color: "var(--ink-4)" }}>lifetime</div></div>
                <div><div style={{ fontSize: 17, fontWeight: 760 }}>{c.open}</div><div style={{ fontSize: 11, color: "var(--ink-4)" }}>open</div></div>
                <div><div style={{ fontSize: 17, fontWeight: 760 }}>{c.contacts.length}</div><div style={{ fontSize: 11, color: "var(--ink-4)" }}>contacts</div></div>
              </div>
            </button>
          ))}
        </div>
      )}

      {sel && <RecordDrawer rec={sel} agents={agents} onClose={() => setSel(null)} onOpenDeal={onOpenDeal} />}
    </div>
  );
}

function RecordDrawer({ rec, agents, onClose, onOpenDeal }) {
  const HEAT = window.HEAT || {};
  const isCo = rec.kind === "company";
  const d = rec.data;
  const deals = isCo ? d.deals : [d.deal];
  return (
    <div className="cmdk-scrim show" onClick={onClose} style={{ alignItems: "stretch", justifyContent: "flex-end", paddingTop: 0 }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: "min(480px, 96vw)", height: "100%", background: "var(--surface)", borderLeft: "1px solid var(--line)", boxShadow: "var(--shadow-xl)", display: "flex", flexDirection: "column", animation: "slide-in .25s both" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "18px 20px", borderBottom: "1px solid var(--line)" }}>
          <div className="deal-co" style={{ background: isCo ? d.color : d.coColor, width: 44, height: 44, fontSize: 15, borderRadius: 12 }}>{isCo ? d.init : d.name.split(" ").map((w) => w[0]).slice(0, 2).join("")}</div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <b style={{ fontSize: 17, fontWeight: 730, display: "block" }}>{isCo ? d.name : d.name}</b>
            <span style={{ fontSize: 12.5, color: "var(--ink-3)" }}>{isCo ? `${d.industry} · ${d.employees} employees` : `${d.title} · ${d.co}`}</span>
          </div>
          <button className="icon-btn" onClick={onClose}><Icon name="x" size={18} /></button>
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: 20, display: "flex", flexDirection: "column", gap: 22 }}>
          <div>
            <div className="so-section-label">{isCo ? "Company details" : "Contact details"}</div>
            <div className="kv">
              {isCo ? <>
                <span className="k">Domain</span><span className="v">{d.domain}</span>
                <span className="k">Industry</span><span className="v">{d.industry}</span>
                <span className="k">Size</span><span className="v">{d.employees} employees</span>
                <span className="k">Lifetime value</span><span className="v">{fmtM(d.value)}</span>
                <span className="k">Contacts</span><span className="v">{d.contacts.join(", ")}</span>
              </> : <>
                <span className="k">Lead score</span><span className="v" style={{ display: "flex", alignItems: "center", gap: 8 }}><span className="meter" style={{ height: 6, flex: "0 0 64px", maxWidth: 64 }}><span style={{ width: cScore(d.deal) + "%", background: cScore(d.deal) >= 65 ? "var(--green)" : cScore(d.deal) >= 45 ? "var(--amber)" : "var(--ink-4)" }} /></span><b style={{ fontFamily: "var(--mono)" }}>{cScore(d.deal)}</b></span>
                <span className="k">Email</span><span className="v">{d.email}</span>
                <span className="k">Phone</span><span className="v">{d.phone}</span>
                <span className="k">Company</span><span className="v">{d.co}</span>
                <span className="k">Lead source</span><span className="v">{d.source}</span>
                <span className="k">Owner agent</span><span className="v">{agents[d.agent] ? agents[d.agent].name : "—"}</span>
                <span className="k">Last activity</span><span className="v">{d.lastActivity}</span>
              </>}
            </div>
            {!isCo && <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginTop: 10 }}>{cTags(d.deal).map((t) => <span key={t} className="chip" style={{ height: 22, fontSize: 11 }}>{t}</span>)}</div>}
          </div>

          {!isCo && d.deal.timeline && (
            <div>
              <div className="so-section-label">Recent activity</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
                {d.deal.timeline.slice(0, 5).map((item, i, arr) => (
                  <div key={i} style={{ display: "flex", gap: 11 }}>
                    <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
                      <div style={{ width: 24, height: 24, borderRadius: 99, background: "var(--accent-soft)", color: "var(--accent-ink)", display: "grid", placeItems: "center", flexShrink: 0 }}><Icon name={item.ico || "spark"} size={12} /></div>
                      {i < Math.min(arr.length, 5) - 1 && <div style={{ width: 2, flex: 1, minHeight: 14, background: "var(--line)" }} />}
                    </div>
                    <div style={{ paddingBottom: 13 }}><p style={{ fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.45 }}>{item.txt}</p><div style={{ fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)", marginTop: 2 }}>{item.t}</div></div>
                  </div>
                ))}
              </div>
            </div>
          )}
          <div>
            <div className="so-section-label">{isCo ? "Deals" : "Deal"} · {deals.length}</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
              {deals.map((dl) => { const h = HEAT[dl.heat] || {}; return (
                <button key={dl.id} onClick={() => { onClose(); onOpenDeal && onOpenDeal(dl); }} style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 12px", border: "1px solid var(--line-2)", borderRadius: "var(--r-sm)", textAlign: "left", cursor: "pointer" }}>
                  <span style={{ width: 8, height: 8, borderRadius: 99, background: dl.stage === "won" ? "var(--green)" : dl.stage === "lost" ? "var(--rose)" : "var(--amber)", flexShrink: 0 }} />
                  <span style={{ flex: 1, minWidth: 0 }}><b style={{ fontSize: 13, fontWeight: 600, display: "block" }}>{dl.co}</b><span style={{ fontSize: 11.5, color: "var(--ink-4)", textTransform: "capitalize" }}>{dl.stage}</span></span>
                  <span style={{ fontSize: 13, fontWeight: 650, fontFamily: "var(--mono)" }}>{fmtM(dl.value)}</span>
                  <Icon name="chevR" size={14} style={{ color: "var(--ink-4)" }} />
                </button>
              ); })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

window.Contacts = Contacts;
