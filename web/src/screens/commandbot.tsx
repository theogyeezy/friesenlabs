// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// commandbot.jsx, Command Center assistant: answers, shows views, navigates, toggles product areas

function dataReply(text) {
  const t = text.toLowerCase();
  const pending = window.FLStore ? window.FLStore.getState().greenlight.filter((i) => i.status === "pending").length : 0;
  if (/pipeline|revenue|worth|value/.test(t)) return "You've got $124.8k in pipeline across 11 active deals. Proposal stage holds the most at $43.2k, Riverside Plumbing and Lantern Bakehouse are the biggest.";
  if (/approv|greenlight|pending|sign.?off|waiting/.test(t)) return `${pending} action${pending === 1 ? "" : "s"} ${pending === 1 ? "is" : "are"} waiting in Greenlight right now. Most are low-risk, want me to approve everything under your policy?`;
  if (/agent|doing|working|team/.test(t)) return "All 5 agents are live and handled 342 tasks today. Scout's the busiest (148), then Nadia (96). Echo is paused, want me to wake it up?";
  if (/hour|saved|time/.test(t)) return "Your agents saved you roughly 47 hours this week, about 18% more than last week.";
  if (/close|likely|win|hot|best/.test(t)) return "Most likely to close: Riverside Plumbing ($22.1k), the quote was opened 6×. Then Lantern Bakehouse ($15.7k) and Maple Grove Vet (re-scored to 91).";
  if (/follow|cold|risk|slip|stuck/.test(t)) return "3 deals are going quiet: North Loop Cycles (4 days), Quill & Press (proposal expires in 3 days), and Tidewater Dental. Echo can chase them, should I?";
  if (/hello|hi|hey|help|what can/.test(t)) return "Ask me anything about your business, or tell me to show a product, change the time range, or hide a panel, and I'll do it.";
  return "I can pull from your live data or reorganize your view, try asking about your pipeline, or say things like \"show me Workflows\", \"switch to year over year\", or \"hide the support snapshot\".";
}

// product routes the assistant can open
const NAV_MAP = [
  { re: /\b(uplift|crm|pipeline|deals|leads|kanban)\b/, route: "crm", label: "Open Uplift" },
  { re: /\b(workflow|automat)/, route: "workflows", label: "Open Workflows" },
  { re: /\b(greenlight|approv|sign.?off)/, route: "approvals", label: "Open Greenlight" },
  { re: /\b(agent|studio|skill)/, route: "agents", label: "Open Agents" },
  { re: /\b(frontline|support|ticket|desk)/, route: "frontline", label: "Open Frontline" },
  { re: /\b(cortex|knowledge|model|memory)/, route: "cortex", label: "Open Cortex" },
  { re: /\b(report|analytic|metric)/, route: "reports", label: "Open Reports" },
  { re: /\b(securit|guardrail|kill ?switch|posture)/, route: "security", label: "Open Security" },
  { re: /\b(integration|connect|hubspot|salesforce|gmail)/, route: "integrations", label: "Open Integration Hub" },
  { re: /\b(marketplace|hire|buy)/, route: "marketplace", label: "Open Marketplace" },
  { re: /\b(setting|configure|workspace)/, route: "settings", label: "Open Settings" },
];

const RANGE_MAP = [
  { re: /\b(year over year|yoy|annual|past year|12 ?months?|year)\b/, id: "yoy", label: "Year over year" },
  { re: /\b(months?|30 ?days?|monthly)\b/, id: "30d", label: "Last 30 days" },
  { re: /\b(weeks?|7 ?days?|weekly)\b/, id: "7d", label: "Last 7 days" },
  { re: /\b(today|24 ?hours?|hourly|day)\b/, id: "24h", label: "Last 24 hours" },
];

// dashboard panels + a few product areas the assistant can toggle
const PANEL_MAP = [
  { re: /\b(rep|performance|leaderboard|scoreboard)\b/, pid: "dashboard", key: "reps", name: "Rep performance panel" },
  { re: /\b(support|deflection|frontline|desk)\b/, pid: "dashboard", key: "support", name: "Support desk snapshot" },
  { re: /\b(security|posture|guardrail)\b/, pid: "dashboard", key: "posture", name: "Security posture strip" },
];

// compose a custom dashboard view spec from natural language
function buildViewSpec(t, wantsOff) {
  const st = window.FLStore ? window.FLStore.getState() : { productFlags: {}, dashRange: "7d", dashRepFilter: "all" };
  const cur = (k, d) => window.FLflag(st.productFlags, "dashboard", k, d);
  let panels = { posture: cur("posture", true), reps: cur("reps", true), support: cur("support", true) };
  const mentioned = {}; PANEL_MAP.forEach((p) => { if (p.re.test(t)) mentioned[p.key] = true; });
  const only = /\b(only|just|nothing but|everything else|hide everything)\b/.test(t);
  if (only && Object.keys(mentioned).length) {
    panels = { posture: !!mentioned.posture, reps: !!mentioned.reps, support: !!mentioned.support };
  } else {
    PANEL_MAP.forEach((p) => { if (p.re.test(t)) panels[p.key] = !wantsOff; });
  }
  let range = st.dashRange; const rm = RANGE_MAP.find((x) => x.re.test(t)); if (rm) range = rm.id;
  let repFilter = st.dashRepFilter;
  if (/\b(people|team|human|reps?)\b/.test(t)) repFilter = "people";
  else if (/\bagents?\b/.test(t)) repFilter = "agents";
  else if (/\b(all|everyone|both)\b/.test(t)) repFilter = "all";
  let name = null;
  const m = t.match(/(?:call (?:it|this)|nam(?:e|ed)(?: it)?)\s+["']?([a-z0-9 &]{2,24})/);
  if (m) name = m[1].trim();
  if (!name) {
    const on = Object.keys(panels).filter((k) => panels[k]);
    name = on.length === 1 ? (on[0] === "reps" ? "Rep focus" : on[0] === "support" ? "Support focus" : "Security focus") : "Custom view";
  }
  name = name.replace(/\b\w/g, (c) => c.toUpperCase());
  return { range, panels, repFilter, name };
}

// returns an action object or null
function parseCommand(text) {
  const t = " " + text.toLowerCase() + " ";
  const wantsOff = /\b(hide|remove|turn off|switch off|disable|drop|get rid|take (?:off|out)|don'?t show)\b/.test(t);
  const wantsOn = /\b(show|add|turn on|enable|bring back|put back|display)\b/.test(t);

  // dashboard view switch
  if (/\bview\b/.test(t) || /\b(sales focus|operations|executive|full overview)\b/.test(t)) {
    const views = (window.FLStore ? window.FLStore.getState().dashViews : []) || [];
    const v = views.find((x) => t.includes(x.name.toLowerCase())) ||
      (/\bsales\b/.test(t) ? views.find((x) => /sales/i.test(x.name)) :
       /\bops|operation/.test(t) ? views.find((x) => /operation/i.test(x.name)) :
       /\bexec/.test(t) ? views.find((x) => /exec/i.test(x.name)) :
       /\boverview|default|everything|full\b/.test(t) ? views.find((x) => /overview/i.test(x.name)) : null);
    if (v) return { kind: "view", id: v.id, name: v.name };
  }

  // record-level data (leads/deals/tickets...) is an Uplift/Frontline concept, not a Command Center panel
  const entityRe = /\b(lead|leads|deal|deals|contact|contacts|customer|customers|client|clients|account|accounts|ticket|tickets|invoice|invoices|opportunit|prospect|company|companies)\b/;
  const listCtx = /\b(list|all |filter|over \$|under \$|above|below|named|where|status|stage|cold|hot|won|lost|open|closed|overdue|unpaid|by (?:state|city|region|owner|rep)|in [a-z])\b/;
  const viewWord = /\b(view|page|list|report|screen|table|dashboard of)\b/.test(t);
  if (entityRe.test(t) && (listCtx.test(t) || viewWord) && /\b(show|make|build|create|view|give me|want|pull up|see|list|page|a )\b/.test(t)) {
    const isTicket = /\b(ticket|tickets|support|refund|case)\b/.test(t);
    return { kind: "rejectData", route: isTicket ? "frontline" : "crm", label: isTicket ? "Open Frontline" : "Open Uplift",
      what: isTicket ? "support tickets" : "records like leads, deals and contacts" };
  }

  // build a CUSTOM dashboard view (panels + range + rep filter) the user can save
  const buildVerb = /\b(make|create|build|set up|new|custom|give me|want|configure|put together|design)\b/.test(t);
  const mentionsPanel = PANEL_MAP.some((x) => x.re.test(t)) || /\b(only|just|nothing but|everything else)\b/.test(t);
  if (buildVerb && (mentionsPanel || RANGE_MAP.some((x) => x.re.test(t)))) {
    return { kind: "buildview", spec: buildViewSpec(t, wantsOff) };
  }

  // gamification toggle
  if (/\b(gamif|points|streak|confetti)\b/.test(t) && (wantsOff || wantsOn)) {
    return { kind: "gamify", on: !wantsOff };
  }
  // panel / area toggle (needs an explicit on/off verb)
  if (wantsOff || wantsOn) {
    const p = PANEL_MAP.find((x) => x.re.test(t));
    if (p) return { kind: "toggle", pid: p.pid, key: p.key, name: p.name, on: !wantsOff };
  }
  // time range
  const rangeAsk = /\b(range|show|switch|change|view|see|last|over|past|this)\b/.test(t) || /\b(yoy|year over year)\b/.test(t);
  if (rangeAsk) { const rm = RANGE_MAP.find((x) => x.re.test(t)); if (rm) return { kind: "range", id: rm.id, label: rm.label }; }
  // navigation / show a view
  if (/\b(show|open|go to|take me|jump to|view|pull up|see)\b/.test(t)) {
    const nv = NAV_MAP.find((x) => x.re.test(t));
    if (nv) return { kind: "nav", route: nv.route, label: nv.label, crm: nv.route === "crm" && /\blead|deal|list|page|view\b/.test(t) };
  }
  return null;
}

function DataAssistant({ onNavigate, surface = "dashboard" }) {
  const [open, setOpen] = useState(false);
  const greet = surface === "reports"
    ? "Hi Jordan 👋 Ask me anything about your numbers and I'll pull it from your live data."
    : "Hi Jordan 👋 I can answer questions, show you any product, change the time range, or hide a panel. Just ask.";
  const [msgs, setMsgs] = useState([{ who: "bot", text: greet }]);
  const [draft, setDraft] = useState("");
  const bodyRef = useRef(null);
  const pending = useStore((s) => s.greenlight.filter((i) => i.status === "pending").length);
  const nav = onNavigate || (() => {});

  useEffect(() => { if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight; }, [msgs, open]);
  const sugg = surface === "reports"
    ? ["How's my pipeline looking?", "What needs my approval?", "Which deals are likely to close?"]
    : ["Show me the Sales focus view", "Switch to year over year", "Hide the support snapshot", "Which deals are likely to close?"];

  const runAction = (a) => {
    if (a.kind === "range") { FLStore.setDashRange(a.id); return { text: `Done, the Command Center is now showing ${a.label.toLowerCase()}. The charts and stats have reorganized.` }; }
    if (a.kind === "view") { FLStore.applyDashView(a.id); return { text: `Switched to your "${a.name}" view, I've reorganized the panels, time range and rep filter to match.` }; }
    if (a.kind === "rejectData") { return { text: `The Command Center shows your whole business at a glance, not record-by-record lists, so a view of ${a.what} lives over in ${a.route === "frontline" ? "Frontline" : "Uplift"}. I can take you there, and you can ask it to build that exact view.`, nav: [[a.label, a.route]] }; }
    if (a.kind === "buildview") { FLStore.previewDashView(a.spec); const on = Object.keys(a.spec.panels).filter((k) => a.spec.panels[k]); const names = on.map((k) => k === "reps" ? "rep performance" : k === "support" ? "support snapshot" : "security posture"); const rl = (RANGE_MAP.find((x) => x.id === a.spec.range) || {}).label || a.spec.range; return { text: `Here's a custom "${a.spec.name}" layout, showing ${names.length ? names.join(", ") : "just the stats"} over ${(rl || "").toLowerCase()}. Want to keep it for next time?`, saveSpec: a.spec }; }
    if (a.kind === "toggle") { FLStore.setProductFlag(a.pid + "." + a.key, a.on); return { text: `${a.on ? "Showing" : "Hidden"}, I've turned ${a.on ? "on" : "off"} the ${a.name} on your Command Center.`, undo: { pid: a.pid, key: a.key, on: !a.on, name: a.name } }; }
    if (a.kind === "gamify") { FLStore.setGamifyOn(a.on); return { text: `Gamification is now ${a.on ? "on" : "off"} for the workspace, ${a.on ? "points, streaks and confetti are back" : "the Sell hub and rewards are hidden"}.` }; }
    if (a.kind === "nav") { return { text: a.crm ? "Opening Uplift, if that exact list doesn't exist yet, ask Uplift there and it'll build the view for you." : `Here you go.`, nav: [[a.label, a.route]] }; }
    return null;
  };

  const send = (text) => {
    const body = (text || draft).trim(); if (!body) return; setDraft("");
    setMsgs((m) => [...m, { who: "me", text: body }, { who: "bot", typing: true }]);
    const action = surface === "dashboard" ? parseCommand(body) : null;
    (async () => {
      let r;
      if (action) r = runAction(action);
      if (!r) { const ans = await askClaude(bizContext() + "\n\nUser: " + body + "\n\nAnswer:", dataReply(body)); r = { text: ans }; }
      setMsgs((m) => { const c = [...m]; c[c.length - 1] = { who: "bot", text: r.text, nav: r.nav, undo: r.undo, saveSpec: r.saveSpec }; return c; });
    })();
  };

  const label = surface === "reports" ? "Data assistant" : "Command assistant";
  const btnLabel = surface === "reports" ? "Ask your data" : "Ask Command Center";

  if (!open) return (
    <button onClick={() => setOpen(true)} title={btnLabel} style={{ position: "fixed", right: 22, bottom: 22, zIndex: 55, height: 52, padding: "0 18px 0 14px", borderRadius: 99, background: "var(--accent)", color: "#fff", display: "flex", alignItems: "center", gap: 9, boxShadow: "var(--shadow-lg)", fontWeight: 650, fontSize: 14 }}>
      <span style={{ fontSize: 20 }}>✦</span>{btnLabel}
      {pending > 0 && <span style={{ background: "#fff", color: "var(--accent-ink)", borderRadius: 99, fontSize: 11, fontWeight: 700, fontFamily: "var(--mono)", padding: "1px 7px" }}>{pending}</span>}
    </button>
  );

  return (
    <div style={{ position: "fixed", right: 22, bottom: 22, zIndex: 55, width: "min(372px, 92vw)", height: "min(540px, 80vh)", display: "flex", flexDirection: "column", background: "var(--bg)", border: "1px solid var(--line)", borderRadius: "var(--r-lg)", boxShadow: "var(--shadow-xl)", overflow: "hidden", animation: "onb-in .25s both" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "13px 15px", borderBottom: "1px solid var(--line)", background: "var(--surface)" }}>
        <div className="avatar" style={{ background: "linear-gradient(145deg, var(--accent), var(--accent-press))", width: 30, height: 30, fontSize: 14 }}>✦</div>
        <div style={{ flex: 1 }}>
          <b style={{ fontSize: 13.5, fontWeight: 700, display: "flex", alignItems: "center", gap: 7 }}>{label} <span className="live-dot" style={{ width: 6, height: 6 }} /></b>
          <span style={{ fontSize: 11, color: "var(--ink-3)" }}>{surface === "reports" ? "Reading your live workspace" : "Answers · shows views · toggles areas"}</span>
        </div>
        <button className="icon-btn" style={{ width: 30, height: 30, fontSize: 20, fontWeight: 700 }} title="Minimize" onClick={() => setOpen(false)}>−</button>
      </div>
      <div ref={bodyRef} style={{ flex: 1, overflowY: "auto", padding: 15, display: "flex", flexDirection: "column", gap: 12 }}>
        {msgs.map((m, i) => (
          <div key={i} className={"msg " + (m.who === "me" ? "me" : "agent")}>
            {m.who === "bot" && <div className="avatar m-av" style={{ background: "linear-gradient(145deg, var(--accent), var(--accent-press))", width: 26, height: 26, fontSize: 12 }}>✦</div>}
            <div>
              <div className="bubble">{m.typing ? <span className="typing"><i /><i /><i /></span> : m.text}</div>
              {m.nav && <div style={{ display: "flex", gap: 7, marginTop: 8, flexWrap: "wrap" }}>{m.nav.map(([lbl, route]) => <button key={route} className="btn btn-primary btn-sm" onClick={() => { nav(route); setOpen(false); }}>{lbl}<Icon name="arrowRight" size={13} sw={2.2} /></button>)}</div>}
              {m.saveSpec && <div style={{ display: "flex", gap: 7, marginTop: 8 }}><button className="btn btn-primary btn-sm" onClick={() => { FLStore.saveDashView(m.saveSpec.name, m.saveSpec); setMsgs((c) => [...c, { who: "bot", text: `Saved, "${m.saveSpec.name}" is now in your View menu on the Command Center.` }]); }}><Icon name="check" size={13} sw={2.4} />Save view</button><button className="btn btn-ghost btn-sm" onClick={() => { FLStore.applyDashView("overview"); setMsgs((c) => [...c, { who: "bot", text: "No problem, I put your overview back." }]); }}>Discard</button></div>}
              {m.undo && <div style={{ marginTop: 8 }}><button className="btn btn-ghost btn-sm" onClick={() => { FLStore.setProductFlag(m.undo.pid + "." + m.undo.key, m.undo.on); setMsgs((c) => [...c, { who: "bot", text: `Reverted, the ${m.undo.name} is ${m.undo.on ? "back" : "hidden"}.` }]); }}><Icon name="chevL" size={13} sw={2.2} />Undo</button></div>}
            </div>
          </div>
        ))}
      </div>
      {msgs.length <= 1 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 7, padding: "0 15px 10px" }}>
          {sugg.map((s) => <button key={s} className="sugg" onClick={() => send(s)}>{s}</button>)}
        </div>
      )}
      <div className="chat-input" style={{ padding: 12 }}>
        <textarea rows={1} value={draft} placeholder={surface === "reports" ? "Ask about your data…" : "Ask, show a view, or toggle an area…"} onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        <button className="chat-send" disabled={!draft.trim()} onClick={() => send()}><Icon name="send" size={17} /></button>
      </div>
    </div>
  );
}

window.DataAssistant = DataAssistant;
