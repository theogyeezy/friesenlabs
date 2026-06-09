// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// integrations.jsx, Switchboard: connect the tools you already use

function IntegrationHub({ agents, onNavigate }) {
  const { INTEGRATIONS, INTG_CATS } = window.FL_DATA;
  const [list, setList] = useState(INTEGRATIONS);
  const [cat, setCat] = useState("All");
  const [q, setQ] = useState("");
  const [connecting, setConnecting] = useState({});

  const connectedCount = list.filter((i) => i.connected).length;

  const toggle = (id) => {
    const item = list.find((i) => i.id === id);
    if (item.connected) {
      setList((l) => l.map((i) => i.id === id ? { ...i, connected: false } : i));
      return;
    }
    setConnecting((c) => ({ ...c, [id]: true }));
    setTimeout(() => {
      setConnecting((c) => { const n = { ...c }; delete n[id]; return n; });
      setList((l) => l.map((i) => i.id === id ? { ...i, connected: true } : i));
    }, 1300);
  };

  const filtered = list.filter((i) =>
    (cat === "All" || i.cat === cat) &&
    (!q || i.name.toLowerCase().includes(q.toLowerCase()) || i.desc.toLowerCase().includes(q.toLowerCase())));

  return (
    <div className="screen screen-anim">
      <div className="ihub-banner">
        <div className="ib-ico"><Icon name="plug" size={24} /></div>
        <div style={{ flex: 1 }}>
          <h2 style={{ fontSize: 19, fontWeight: 720, letterSpacing: "-.02em" }}>Connect your stack</h2>
          <p style={{ fontSize: 13.5, color: "var(--ink-2)", marginTop: 3, lineHeight: 1.5 }}>
            Your agents work inside the tools you already run on. <b style={{ color: "var(--ink)" }}>{connectedCount} of {list.length}</b> connected, nothing is sent without your say-so.
          </p>
        </div>
        <div className="search-trigger" style={{ minWidth: 220, cursor: "text" }}>
          <Icon name="search" size={15} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search integrations…"
            style={{ border: "none", outline: "none", background: "none", flex: 1, fontSize: 13, color: "var(--ink)" }} />
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 14, padding: "16px 20px", borderRadius: "var(--r-lg)", border: "1px solid var(--accent-soft)", background: "linear-gradient(120deg, var(--accent-softer), var(--surface))", marginBottom: "var(--gap)" }}>
        <div style={{ width: 40, height: 40, borderRadius: 11, background: "var(--accent)", color: "#fff", display: "grid", placeItems: "center", flexShrink: 0 }}><Icon name="spark" size={19} /></div>
        <div style={{ flex: 1 }}>
          <b style={{ fontSize: 14.5, fontWeight: 700 }}>Not ready to move? Make your current tools agentic.</b>
          <p style={{ fontSize: 12.5, color: "var(--ink-2)", marginTop: 2, lineHeight: 1.45 }}><b style={{ color: "var(--ink)" }}>Sidecar</b> rides on top of HubSpot, Salesforce or Gmail, putting your agents right where you work, via browser extension or direct API.</p>
        </div>
        <button className="btn btn-primary btn-sm" onClick={() => onNavigate && onNavigate("sidecar")}>Open Sidecar<Icon name="arrowRight" size={13} sw={2.2} /></button>
      </div>

      <div className="cat-row">
        {INTG_CATS.map((c) => (
          <button key={c} className={"cat-pill" + (cat === c ? " active" : "")} onClick={() => setCat(c)}>
            {c}{c !== "All" && <span style={{ opacity: .6, marginLeft: 6 }}>{list.filter((i) => i.cat === c).length}</span>}
          </button>
        ))}
      </div>

      {(cat === "All" || cat === "CRM & Marketing") && (
        <div style={{ display: "flex", alignItems: "center", gap: 13, padding: "14px 18px", borderRadius: "var(--r-md)", border: "1px dashed var(--accent-soft)", background: "var(--accent-softer)", marginBottom: "var(--gap)" }}>
          <div style={{ width: 34, height: 34, borderRadius: 9, background: "var(--surface)", color: "var(--accent-ink)", display: "grid", placeItems: "center", flexShrink: 0 }}><Icon name="link" size={17} /></div>
          <p style={{ fontSize: 13, color: "var(--accent-ink)", lineHeight: 1.5, flex: 1 }}>
            <b style={{ fontWeight: 700 }}>Keeping your current CRM?</b> Connect HubSpot, Salesforce or Pipedrive as your system of record, your agents, workflows and Greenlight approvals all read and write to it directly. No need for Uplift.
          </p>
        </div>
      )}

      <div className="intg-grid">
        {filtered.map((i) => {
          const isConnecting = !!connecting[i.id];
          return (
            <div key={i.id} className={"intg-card" + (i.connected ? " on" : "")}>
              <div className="intg-top">
                <div className="intg-mark" style={{ background: i.color, color: i.dark ? "#1a1a1a" : "#fff" }}>{i.letter}</div>
                <div className="meta">
                  <b>{i.name}</b>
                  <span className="cat">{i.cat}</span>
                </div>
                {i.connected && <Icon name="checkCircle" size={20} style={{ color: "var(--green)" }} />}
              </div>
              <div className="intg-desc">{i.desc}</div>
              <div className="intg-foot">
                {i.connected ? (
                  <>
                    {i.agents.length > 0 ? (
                      <div className="intg-users" title="Agents using this">
                        {i.agents.map((aid) => <div key={aid} className="avatar" style={{ background: agents[aid].color }}>{agents[aid].init}</div>)}
                      </div>
                    ) : <span className="intg-status"><span className="st-dot active" />Connected</span>}
                    <button className="btn btn-ghost btn-sm" style={{ marginLeft: "auto" }} onClick={() => toggle(i.id)}>Disconnect</button>
                  </>
                ) : (
                  <button className={"btn btn-sm " + (isConnecting ? "btn-soft connecting" : "btn-primary")}
                    style={{ marginLeft: "auto" }} onClick={() => toggle(i.id)}>
                    {isConnecting ? <><Icon name="refresh" size={14} className="spin" />Connecting…</> : <><Icon name="link" size={14} />Connect</>}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
      {filtered.length === 0 && (
        <div style={{ textAlign: "center", padding: "60px 0", color: "var(--ink-3)" }}>
          <Icon name="search" size={28} style={{ opacity: .5 }} />
          <p style={{ marginTop: 10, fontSize: 14 }}>No integrations match “{q}”.</p>
        </div>
      )}
    </div>
  );
}

window.IntegrationHub = IntegrationHub;
