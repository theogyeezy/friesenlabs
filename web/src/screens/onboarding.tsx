// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// onboarding.jsx, multi-step setup overlay

const BIZ_TYPES = [
  { id: "retail",  icon: "building", label: "Retail & food", sub: "Cafés, shops, restaurants" },
  { id: "home",    icon: "bolt",     label: "Home services", sub: "Plumbing, HVAC, landscaping" },
  { id: "health",  icon: "spark",    label: "Health & wellness", sub: "Clinics, studios, salons" },
  { id: "pro",     icon: "doc",      label: "Professional", sub: "Agencies, consultants, legal" },
  { id: "hosp",    icon: "calendar", label: "Hospitality", sub: "Lodging, events, tours" },
  { id: "other",   icon: "grid",     label: "Something else", sub: "We'll adapt to you" },
];

const TOOLS = [
  { id: "email", icon: "mail",     name: "Email inbox",     sub: "Send & track outreach", color: "oklch(0.62 0.15 18)" },
  { id: "cal",   icon: "calendar", name: "Calendar",        sub: "Book & schedule",       color: "oklch(0.56 0.17 277)" },
  { id: "pay",   icon: "trend",    name: "Payments",        sub: "Invoices & billing",    color: "oklch(0.62 0.13 152)" },
  { id: "acct",  icon: "doc",      name: "Accounting",      sub: "Sync the books",        color: "oklch(0.66 0.14 50)" },
  { id: "msg",   icon: "send",     name: "Team messaging",  sub: "Notify your crew",      color: "oklch(0.66 0.12 235)" },
  { id: "phone", icon: "phone",    name: "Business phone",  sub: "Calls & texts",         color: "oklch(0.55 0.15 330)" },
];

const PRODUCT_GUIDE = [
  { group: "Run the business", items: [
    ["grid", "Command Center", "Your morning overview, what agents did, what needs you"],
    ["users", "Uplift CRM", "Pipeline, contacts, billing, scheduling, reputation & outreach, one agentic CRM"],
    ["inbox", "Frontline", "An autonomous support desk that deflects the routine"],
    ["workflow", "Workflows", "Automate anything, drag blocks or just describe it"],
    ["checkCircle", "Greenlight", "Approve what agents do, one tap, you stay in control"],
  ] },
  { group: "Your workforce & brains", items: [
    ["spark", "Agents & Studio", "Hire ready-made agents or build your own with skills"],
    ["doc", "Knowledge", "Upload what your business knows; agents ground on it"],
    ["network", "Cortex", "Private intelligence that sharpens with every decision"],
  ] },
  { group: "Connect & control", items: [
    ["plug", "Switchboard", "Connect HubSpot, Gmail, Stripe and 18+ tools"],
    ["layers", "Sidecar", "Put agents on top of the tools you already use"],
    ["shield", "Security", "Kill switch, guardrails and a full audit trail"],
  ] },
];

function Onboarding({ agents, onDone }) {
  const [step, setStep] = useState(0);
  const [biz, setBiz] = useState("home");
  const [picked, setPicked] = useState({ scout: true, nadia: true, margo: true, echo: false, ledger: true });
  const [tools, setTools] = useState({ email: true, cal: true });
  const steps = ["business", "agents", "tools", "suite", "ready"];
  const last = steps.length - 1;
  const agentList = Object.values(agents);

  const next = () => step < last ? setStep(step + 1) : onDone();
  const enabledAgents = agentList.filter((a) => picked[a.id]);

  return (
    <div className="onb-scrim">
      <div className="onb-top">
        <div className="brand-mark" style={{ width: 30, height: 30 }}><Logo size={18} /></div>
        <b style={{ fontSize: 15, letterSpacing: "-.02em" }}>Friesen Labs</b>
        <div className="onb-steps">
          {steps.map((s, i) => (
            <span key={s} className={"onb-step-dot" + (i === step ? " active" : i < step ? " done" : "")} />
          ))}
        </div>
        <button className="onb-skip" onClick={onDone}>Skip setup →</button>
      </div>

      <div className="onb-body">
        {step === 0 && (
          <div className="onb-panel" key="s0">
            <div className="onb-eyebrow">Step 1 · Tell us about you</div>
            <h1 className="onb-h">What kind of business<br />are you running?</h1>
            <p className="onb-sub">Your agents tune themselves to your industry, the right follow-ups, the right cadence, the right tone.</p>
            <div className="onb-choice-grid">
              {BIZ_TYPES.map((b) => (
                <button key={b.id} className={"onb-choice" + (biz === b.id ? " sel" : "")} onClick={() => setBiz(b.id)}>
                  <div className="ch-ico"><Icon name={b.icon} size={19} /></div>
                  <div><b>{b.label}</b></div>
                  <span>{b.sub}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {step === 1 && (
          <div className="onb-panel" key="s1">
            <div className="onb-eyebrow">Step 2 · Build your team</div>
            <h1 className="onb-h">Pick your agents</h1>
            <p className="onb-sub">Each agent owns a job and works around the clock. Toggle the ones you want, you can always add more later.</p>
            <div style={{ marginTop: 26 }}>
              {agentList.map((a) => (
                <div key={a.id} className={"onb-agent-row" + (picked[a.id] ? " on" : "")} onClick={() => setPicked((p) => ({ ...p, [a.id]: !p[a.id] }))}>
                  <div className="avatar" style={{ background: a.color, width: 40, height: 40, fontSize: 14 }}>{a.init}</div>
                  <div className="info"><b>{a.name}</b><span>{a.role} agent</span></div>
                  <div className={"tog" + (picked[a.id] ? " on" : "")} />
                </div>
              ))}
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="onb-panel" key="s2">
            <div className="onb-eyebrow">Step 3 · Switchboard</div>
            <h1 className="onb-h">Plug in your tools</h1>
            <p className="onb-sub">Switchboard lets your agents work inside the tools you already use. Connect a couple to start, nothing is sent without your say-so.</p>
            <div className="onb-tool-grid">
              {TOOLS.map((t) => (
                <div className="onb-tool" key={t.id}>
                  <div className="tmark" style={{ background: t.color }}><Icon name={t.icon} size={18} /></div>
                  <div className="tinfo"><b>{t.name}</b><span>{t.sub}</span></div>
                  <button className={"btn btn-sm " + (tools[t.id] ? "btn-soft" : "btn-ghost")}
                    onClick={() => setTools((x) => ({ ...x, [t.id]: !x[t.id] }))}>
                    {tools[t.id] ? <><Icon name="check" size={13} sw={2.4} />Connected</> : "Connect"}
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {step === 3 && (
          <div className="onb-panel" key="s3">
            <div className="onb-eyebrow">Step 4 · Your suite</div>
            <h1 className="onb-h">Everything in your workspace</h1>
            <p className="onb-sub">Here's what you've got and when to reach for it. Explore any of these from the left sidebar anytime.</p>
            <div style={{ display: "flex", flexDirection: "column", gap: 18, marginTop: 22 }}>
              {PRODUCT_GUIDE.map((g) => (
                <div key={g.group}>
                  <div style={{ fontSize: 11, fontWeight: 650, textTransform: "uppercase", letterSpacing: ".05em", color: "var(--ink-4)", marginBottom: 9 }}>{g.group}</div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 9 }}>
                    {g.items.map(([ic, name, how]) => (
                      <div key={name} style={{ display: "flex", gap: 11, padding: "11px 13px", border: "1px solid var(--line)", borderRadius: "var(--r-md)", background: "var(--surface)" }}>
                        <div className="feed-ico" style={{ width: 30, height: 30, flexShrink: 0, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name={ic} size={15} /></div>
                        <div style={{ minWidth: 0 }}><b style={{ fontSize: 13, fontWeight: 680, display: "block" }}>{name}</b><span style={{ fontSize: 11.5, color: "var(--ink-3)", lineHeight: 1.4, display: "block" }}>{how}</span></div>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {step === 4 && (
          <div className="onb-panel onb-finale" key="s4">
            <div className="big-check"><Icon name="check" size={38} sw={2.6} style={{ color: "#fff" }} /></div>
            <div className="onb-eyebrow">You're all set</div>
            <h1 className="onb-h" style={{ marginTop: 8 }}>Your team is on the clock</h1>
            <p className="onb-sub" style={{ margin: "0 auto" }}>
              {enabledAgents.length} agents are live and your first workflow, <b style={{ color: "var(--ink)" }}>New lead → enrich → outreach → your approval</b>, is ready to run.
            </p>
            <div style={{ display: "flex", justifyContent: "center", gap: 8, marginTop: 26, flexWrap: "wrap" }}>
              {enabledAgents.map((a) => (
                <span key={a.id} className="agent-tag" style={{ height: 30, padding: "0 12px 0 4px" }}>
                  <div className="avatar" style={{ background: a.color, width: 22, height: 22, fontSize: 9 }}>{a.init}</div>{a.name}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="onb-foot">
        {step > 0 && <button className="btn btn-ghost" onClick={() => setStep(step - 1)}><Icon name="chevL" size={15} sw={2.2} />Back</button>}
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 14 }}>
          <span style={{ fontSize: 12.5, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>{step + 1} / {steps.length}</span>
          <button className="btn btn-primary" onClick={next} style={{ minWidth: 140 }}>
            {step === last ? <><Icon name="bolt" size={16} />Enter Friesen Labs</> : <>Continue<Icon name="arrowRight" size={15} sw={2.2} /></>}
          </button>
        </div>
      </div>
    </div>
  );
}

window.Onboarding = Onboarding;
