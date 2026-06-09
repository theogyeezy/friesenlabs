// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// sidecar.jsx, Sidecar: the agentic layer that rides on top of the tools you already use

const SIDECAR_TOOLS = [
  ["HubSpot", "#ff7a59", "H"], ["Salesforce", "#00a1e0", "S"], ["Gmail", "#ea4335", "G"],
  ["Outlook", "#0072c6", "O"], ["Pipedrive", "#1a1a1a", "P"], ["Zendesk", "#03363d", "Z"],
  ["Shopify", "#5e8e3e", "S"], ["Any website", "var(--accent)", "+"],
];
const SIDECAR_DOES = [
  ["plug", "Works on your connected tools", "Connect a tool in Switchboard and Sidecar's agents go to work on top of it, no migration, your data stays put."],
  ["spark", "Agents do the work", "They enrich records, draft replies, score leads and advance deals across everything you've connected."],
  ["check", "You stay in the loop", "Anything sensitive routes to Greenlight for your one-tap approval, every action surfaces inside Friesen."],
  ["shield", "Same guardrails", "Every Sidecar action respects the autonomy levels, policies and kill switch as the rest of your suite."],
];

function SidecarFlow() {
  const tools = [["HubSpot", "#ff7a59", "H"], ["Gmail", "#ea4335", "G"], ["Stripe", "#635bff", "S"], ["Zendesk", "#03363d", "Z"]];
  const acts = [
    ["🦊", "Nadia", "Drafted a follow-up to Dana, from your HubSpot"],
    ["🦉", "Scout", "Scored a new Gmail lead 88/100"],
    ["🦝", "Margo", "Prepared a quote, sent to Greenlight"],
  ];
  return (
    <div className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div>
        <div className="eyebrow" style={{ marginBottom: 9 }}>Your connected tools</div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {tools.map(([n, c, l]) => (
            <div key={n} style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 12px 7px 8px", border: "1px solid var(--line)", borderRadius: 99, background: "var(--surface)" }}>
              <div style={{ width: 22, height: 22, borderRadius: 6, background: c, color: "#fff", display: "grid", placeItems: "center", fontWeight: 800, fontSize: 11, fontFamily: "var(--mono)" }}>{l}</div>
              <b style={{ fontSize: 12.5 }}>{n}</b>
            </div>
          ))}
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, color: "var(--ink-3)" }}>
        <div style={{ flex: 1, height: 1, background: "var(--line)" }} />
        <span style={{ fontSize: 11, fontFamily: "var(--mono)", display: "flex", alignItems: "center", gap: 6 }}><Icon name="plug" size={13} />via Switchboard</span>
        <div style={{ flex: 1, height: 1, background: "var(--line)" }} />
      </div>
      <div>
        <div className="eyebrow" style={{ marginBottom: 9, display: "flex", alignItems: "center", gap: 7 }}>Sidecar agents at work <span className="live-dot" style={{ width: 6, height: 6 }} /></div>
        <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
          {acts.map(([f, who, txt], i) => (
            <div key={i} className="fox-msg" style={{ animation: "feed-in .5s both", animationDelay: (i * 90) + "ms" }}>
              <div style={{ width: 28, height: 28, borderRadius: 8, background: "var(--accent-softer)", display: "grid", placeItems: "center", fontSize: 15, flexShrink: 0 }}>{f}</div>
              <div style={{ flex: 1, minWidth: 0 }}><b style={{ fontSize: 12.5 }}>{who}</b><span style={{ display: "block", fontSize: 11.5, color: "var(--ink-3)" }}>{txt}</span></div>
              <span className="chip indigo" style={{ height: 19 }}>in Friesen</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function SidecarMockUnused() {
  return (
    <div style={{ borderRadius: "var(--r-lg)", overflow: "hidden", border: "1px solid var(--line)", boxShadow: "var(--shadow-md)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 7, padding: "10px 14px", background: "var(--surface-2)", borderBottom: "1px solid var(--line)" }}>
        <span style={{ width: 11, height: 11, borderRadius: 99, background: "#e0653f" }} /><span style={{ width: 11, height: 11, borderRadius: 99, background: "#e8a33d" }} /><span style={{ width: 11, height: 11, borderRadius: 99, background: "#2ca05a" }} />
        <div style={{ marginLeft: 10, flex: 1, height: 26, borderRadius: 99, background: "var(--surface)", border: "1px solid var(--line)", fontSize: 12, color: "var(--ink-4)", display: "flex", alignItems: "center", padding: "0 13px", fontFamily: "var(--mono)" }}>app.hubspot.com/contacts</div>
        <div style={{ width: 26, height: 26, borderRadius: 7, background: "linear-gradient(145deg, var(--accent), var(--accent-press))", display: "grid", placeItems: "center" }}><Icon name="layers" size={15} style={{ color: "#fff" }} /></div>
      </div>
      <div style={{ display: "flex", height: 280 }}>
        <div style={{ flex: 1, background: "repeating-linear-gradient(135deg, var(--surface-2) 0 10px, var(--surface) 10px 20px)", display: "grid", placeItems: "center" }}>
          <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--ink-4)" }}>your CRM, unchanged</span>
        </div>
        <div style={{ width: 230, borderLeft: "1px solid var(--line)", background: "var(--surface)", padding: 15, display: "flex", flexDirection: "column", gap: 11 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, fontWeight: 700 }}><span style={{ fontSize: 16 }}>🦊</span>Sidecar <span className="live-dot" style={{ width: 6, height: 6, marginLeft: "auto" }} /></div>
          <div style={{ background: "var(--accent-softer)", borderRadius: "var(--r-sm)", padding: 11, fontSize: 11.5, color: "var(--accent-ink)", lineHeight: 1.45 }}>Nadia drafted a follow-up for <b>Dana Okafor</b> referencing her last reply.</div>
          <button className="btn btn-primary btn-sm" style={{ fontSize: 11.5 }}><Icon name="check" size={13} sw={2.4} />Approve &amp; send</button>
          <div style={{ background: "var(--surface-2)", borderRadius: "var(--r-sm)", padding: 11, fontSize: 11.5, color: "var(--ink-2)", lineHeight: 1.45 }}><b>🦉 Scout</b> scored this lead <b>88/100</b>, strong fit.</div>
          <div style={{ background: "var(--surface-2)", borderRadius: "var(--r-sm)", padding: 11, fontSize: 11.5, color: "var(--ink-2)", lineHeight: 1.45 }}>2 similar deals closed in 9 days on average.</div>
        </div>
      </div>
    </div>
  );
}

function Sidecar({ agents, onNavigate }) {
  const [added, setAdded] = useState(false);
  return (
    <div className="screen screen-anim">
      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 7 }}>The agentic layer of your suite</div>
          <h2 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.03em" }}>Sidecar</h2>
          <p style={{ color: "var(--ink-2)", fontSize: 14.5, marginTop: 5, maxWidth: 580 }}>
            Keep your whole stack. Sidecar is software in your suite that puts your agents to work on top of the tools you've connected in Switchboard, no migration, your tools stay the system of record.
          </p>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 9 }}>
          <button className="btn btn-primary" onClick={() => onNavigate && onNavigate("integrations")}>
            <Icon name="plug" size={16} />Connect your tools
          </button>
        </div>
      </div>

      <div className="dash-grid">
        <SidecarFlow />
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--gap)" }}>
          <div className="card">
            <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="layers" size={15} /></div><h3>Part of your suite</h3></div>
            <div className="card-pad">
              <p style={{ fontSize: 13, color: "var(--ink-2)", lineHeight: 1.55, marginBottom: 13 }}>Sidecar is software in your Friesen workspace, no install, no plugin. It puts your agents to work on top of the tools you've already connected.</p>
              <button className="btn btn-soft btn-sm" onClick={() => onNavigate && onNavigate("integrations")}><Icon name="plug" size={13} />Connect a tool</button>
            </div>
          </div>
          <div className="card">
            <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--green-soft)", color: "oklch(0.42 0.12 152)" }}><Icon name="shield" size={15} /></div><h3>You're in control</h3></div>
            <div className="card-pad">
              <p style={{ fontSize: 13, color: "var(--ink-2)", lineHeight: 1.55, marginBottom: 13 }}>Agents run server-side against your connected tools, and anything sensitive routes to Greenlight for your one-tap approval.</p>
              <button className="btn btn-ghost btn-sm" onClick={() => onNavigate && onNavigate("approvals")}><Icon name="checkCircle" size={13} />Open Greenlight</button>
            </div>
          </div>
        </div>
      </div>

      <div className="rg2 section-gap">
        {SIDECAR_DOES.map(([ico, t, d]) => (
          <div className="card card-pad" key={t} style={{ display: "flex", gap: 13 }}>
            <div className="feed-ico" style={{ width: 34, height: 34, background: "var(--accent-soft)", color: "var(--accent-ink)", flexShrink: 0 }}><Icon name={ico} size={17} /></div>
            <div><b style={{ fontSize: 14, fontWeight: 680, display: "block", marginBottom: 3 }}>{t}</b><p style={{ fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.5 }}>{d}</p></div>
          </div>
        ))}
      </div>

      <div className="card section-gap">
        <div className="card-head"><h3>Works on top of</h3><span className="sub" style={{ marginLeft: "auto" }}>and any app you use</span></div>
        <div className="card-pad rg4">
          {SIDECAR_TOOLS.map(([n, c, l]) => (
            <div key={n} style={{ display: "flex", alignItems: "center", gap: 10, padding: "11px 13px", border: "1px solid var(--line)", borderRadius: "var(--r-md)", background: "var(--surface)" }}>
              <div style={{ width: 32, height: 32, borderRadius: 9, background: c, color: "#fff", display: "grid", placeItems: "center", fontWeight: 800, fontFamily: "var(--mono)", flexShrink: 0 }}>{l}</div>
              <b style={{ fontSize: 13 }}>{n}</b>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

window.Sidecar = Sidecar;
