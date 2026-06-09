// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// workflow.jsx, visual node-based workflow builder

const NODE_W = 210, NODE_H = 96;

const NTYPE = {
  trigger:   { label: "Trigger",   tone: "amber",  ico: "bolt" },
  data:      { label: "Data source", tone: "sky",  ico: "plug" },
  knowledge: { label: "Knowledge", tone: "violet", ico: "spark" },
  agent:     { label: "Agent",     tone: "indigo", ico: "spark" },
  condition: { label: "Condition", tone: "sky",    ico: "target" },
  approval:  { label: "Approval",  tone: "amber",  ico: "inbox" },
  action:    { label: "Action",    tone: "green",  ico: "send" },
};
const TONE = {
  amber:  ["var(--amber-soft)", "oklch(0.5 0.12 60)"],
  indigo: ["var(--accent-soft)", "var(--accent-ink)"],
  green:  ["var(--green-soft)", "oklch(0.42 0.12 152)"],
  sky:    ["oklch(0.93 0.04 235)", "oklch(0.45 0.12 235)"],
  violet: ["oklch(0.93 0.05 300)", "oklch(0.45 0.15 300)"],
};

let nid = 100;
const PALETTE = [
  { grp: "Triggers", items: [
    { type: "trigger", title: "New lead arrives", body: "When a lead hits your site or inbox" },
    { type: "trigger", title: "Form submitted", body: "A contact fills out a form" },
    { type: "trigger", title: "On a schedule", body: "Every day at 8:00am" },
  ]},
  { grp: "Logic", items: [
    { type: "condition", title: "If condition", body: "Branch on a rule" },
    { type: "approval", title: "Wait for approval", body: "Pause for your sign-off" },
  ]},
  { grp: "Actions", items: [
    { type: "action", title: "Send email", body: "Deliver the message", agent: "nadia" },
    { type: "action", title: "Create task", body: "Add to your queue" },
    { type: "action", title: "Update CRM", body: "Advance the deal stage" },
  ]},
];

const SEED_NODES = [
  { id: 1, type: "trigger",   title: "New lead arrives",  body: "From your website or inbox", x: 300, y: 40 },
  { id: 2, type: "agent",     title: "Scout enriches",    body: "Research + score fit 0–100", x: 300, y: 196, agent: "scout" },
  { id: 3, type: "condition", title: "Fit score > 80?",   body: "High-intent leads only",     x: 300, y: 352 },
  { id: 4, type: "agent",     title: "Nadia reaches out",  body: "Draft a personalized intro", x: 540, y: 508, agent: "nadia" },
  { id: 5, type: "approval",  title: "You approve",       body: "Review before it sends",     x: 540, y: 664 },
  { id: 6, type: "action",    title: "Send email",        body: "Delivered + tracked",        x: 540, y: 820, agent: "nadia" },
];
const SEED_EDGES = [
  { from: 1, to: 2 }, { from: 2, to: 3 }, { from: 3, to: 4 }, { from: 4, to: 5 }, { from: 5, to: 6 },
];

function edgePath(s, t, hs, ht) {
  const sx = s.x + NODE_W / 2, sy = s.y + (hs || NODE_H);
  const tx = t.x + NODE_W / 2, ty = t.y;
  const k = Math.max(38, Math.abs(ty - sy) / 2);
  return `M ${sx} ${sy} C ${sx} ${sy + k}, ${tx} ${ty - k}, ${tx} ${ty}`;
}

function WorkflowAIPanel({ onGenerate, onClose }) {
  const [msgs, setMsgs] = useState([{ who: "bot", text: "Tell me what you want to automate and I'll design the workflow for you." }]);
  const [draft, setDraft] = useState("");
  const bodyRef = useRef(null);
  const sugg = [
    "When a new lead comes in, have Scout enrich it then Nadia email them, ask me before sending",
    "Every morning, follow up on quotes that opened but got no reply",
    "When a form is submitted, qualify the lead and book a demo",
  ];
  useEffect(() => { if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight; }, [msgs]);
  const send = (text) => {
    const body = (text || draft).trim(); if (!body) return; setDraft("");
    setMsgs((m) => [...m, { who: "me", text: body }, { who: "bot", typing: true }]);
    setTimeout(() => {
      const n = onGenerate(body);
      setMsgs((m) => { const c = [...m]; c[c.length - 1] = { who: "bot", text: `Done, I designed a ${n}-step workflow and dropped it on the canvas. Edit any step, then hit Run.` }; return c; });
    }, 950);
  };
  return (
    <div className="wf-ai" style={{ position: "absolute", bottom: 16, right: 16, width: 350, maxHeight: "min(470px, 80%)", display: "flex", flexDirection: "column", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-lg)", boxShadow: "var(--shadow-xl)", zIndex: 25, overflow: "hidden" }} onPointerDown={(e) => e.stopPropagation()}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "13px 15px", borderBottom: "1px solid var(--line)" }}>
        <div className="feed-ico" style={{ width: 28, height: 28, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="spark" size={15} /></div>
        <b style={{ fontSize: 13.5, fontWeight: 700, flex: 1 }}>Build with AI</b>
        <button className="icon-btn" style={{ width: 28, height: 28 }} onClick={onClose}><Icon name="x" size={15} /></button>
      </div>
      <div ref={bodyRef} style={{ flex: 1, overflowY: "auto", padding: 15, display: "flex", flexDirection: "column", gap: 12 }}>
        {msgs.map((m, i) => (
          <div key={i} className={"msg " + (m.who === "me" ? "me" : "agent")}>
            {m.who === "bot" && <div className="avatar m-av" style={{ background: "linear-gradient(145deg, var(--accent), var(--accent-press))", width: 26, height: 26, fontSize: 12 }}>✦</div>}
            <div className="bubble">{m.typing ? <span className="typing"><i /><i /><i /></span> : m.text}</div>
          </div>
        ))}
      </div>
      {msgs.length <= 1 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: "0 15px 10px" }}>
          {sugg.map((s) => <button key={s} className="sugg" onClick={() => send(s)}>{s}</button>)}
        </div>
      )}
      <div className="chat-input" style={{ padding: 12 }}>
        <textarea rows={1} value={draft} placeholder="Describe an automation…" onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        <button className="chat-send" disabled={!draft.trim()} onClick={() => send()}><Icon name="send" size={17} /></button>
      </div>
    </div>
  );
}

function WorkflowHero({ onGenerate, onTemplates }) {
  const [draft, setDraft] = useState("");
  const kbs = (window.FLStore.getState().knowledgeBases) || [];
  const connected = ((window.FL_DATA && window.FL_DATA.INTEGRATIONS) || []).filter((i) => i.connected);
  const sugg = [
    "When a new HubSpot lead comes in, have Scout enrich it, ground on our pricing, then Nadia drafts an intro, ask me before sending",
    "Every morning, review quotes that opened but got no reply and have Echo chase them",
    "When a form is submitted, qualify the lead against our ideal-customer docs and book a demo",
  ];
  const go = (text) => { const b = (text || draft).trim(); if (b) onGenerate(b); };
  return (
    <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", pointerEvents: "none", padding: 20, overflowY: "auto" }}>
      <div style={{ width: "min(620px, 94%)", pointerEvents: "auto", textAlign: "center", padding: "24px 0" }}>
        <div className="feed-ico" style={{ width: 46, height: 46, background: "linear-gradient(145deg, var(--accent), var(--accent-press))", color: "#fff", borderRadius: 14, margin: "0 auto 14px" }}><Icon name="spark" size={23} /></div>
        <h3 style={{ fontSize: 21, fontWeight: 760, letterSpacing: "-.02em" }}>Design a multi-agent workflow</h3>
        <p style={{ fontSize: 13.5, color: "var(--ink-3)", marginTop: 6, lineHeight: 1.5, maxWidth: 460, marginInline: "auto" }}>Describe the outcome in plain words. I'll wire the right agents together, pull from your connected tools, and ground them on your knowledge bases.</p>
        <div className="wf-hero-box">
          <textarea autoFocus rows={3} value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="e.g. When a high-value lead comes in from HubSpot, have Scout research them, ground on our SOPs, then Margo drafts a quote and routes it to me to approve…"
            onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); go(); } }} />
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }}>
            <span style={{ fontSize: 11, color: "var(--ink-4)", flex: 1, textAlign: "left" }}>⌘↵ to design · then edit any step on the canvas</span>
            <button className="btn btn-primary btn-sm" disabled={!draft.trim()} onClick={() => go()}><Icon name="spark" size={14} />Design it</button>
          </div>
        </div>
        {(connected.length > 0 || kbs.length > 0) && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, justifyContent: "center", marginTop: 14 }}>
            <span style={{ fontSize: 11, color: "var(--ink-4)", alignSelf: "center" }}>Can draw on:</span>
            {connected.slice(0, 3).map((i) => <span key={i.id} className="chip" style={{ height: 24, fontSize: 11, gap: 5 }}><span style={{ width: 7, height: 7, borderRadius: 99, background: "var(--green)" }} />{i.name}</span>)}
            {kbs.slice(0, 2).map((k) => <span key={k.id} className="chip" style={{ height: 24, fontSize: 11, gap: 5 }}><Icon name="spark" size={11} style={{ color: "var(--accent-ink)" }} />{k.name}</span>)}
          </div>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 16, maxWidth: 520, marginInline: "auto" }}>
          {sugg.map((s) => <button key={s} className="sugg" style={{ textAlign: "left" }} onClick={() => go(s)}>{s}</button>)}
        </div>
        <button className="btn btn-ghost btn-sm" style={{ marginTop: 14 }} onClick={onTemplates}><Icon name="layers" size={14} />Or start from a template</button>
      </div>
    </div>
  );
}

function stepsToGraph(steps) {
  const list = steps && steps.length ? steps : [];
  const nodes = list.map((s, i) => ({ id: i + 1, type: s.type, title: s.title, body: s.body, agent: s.agent, x: 300 + (i % 2) * 130, y: 40 + i * 152 }));
  const edges = nodes.slice(1).map((n, i) => ({ from: nodes[i].id, to: n.id }));
  return { nodes, edges };
}

// flatten the current graph back into ordered steps for the store
function graphToSteps(nodes, edges) {
  if (!nodes.length) return [];
  const start = nodes.find((n) => n.type === "trigger") || nodes[0];
  const order = []; const seen = new Set();
  const walk = (id) => { if (seen.has(id)) return; seen.add(id); const n = nodes.find((x) => x.id === id); if (n) order.push(n); edges.filter((e) => e.from === id).forEach((e) => walk(e.to)); };
  walk(start.id);
  nodes.forEach((n) => { if (!seen.has(n.id)) order.push(n); });
  return order.map((n) => ({ type: n.type, title: n.title, body: n.body, agent: n.agent }));
}

const WF_TEMPLATES = [
  { name: "Lead → enrich → outreach", ico: "spark", tone: "indigo", desc: "Scout enriches new leads, Nadia drafts an intro, you approve before send.",
    steps: [{ type: "trigger", title: "New lead arrives", body: "From your site or inbox" }, { type: "agent", title: "Scout enriches", body: "Research + score fit", agent: "scout" }, { type: "condition", title: "Fit score > 80?", body: "High-intent only" }, { type: "agent", title: "Nadia reaches out", body: "Draft a personalized intro", agent: "nadia" }, { type: "approval", title: "You approve", body: "Review before send" }, { type: "action", title: "Send email", body: "Deliver + track", agent: "nadia" }] },
  { name: "Quote follow-up chaser", ico: "trend", tone: "amber", desc: "When a quote goes quiet, Echo nudges and books a call.",
    steps: [{ type: "trigger", title: "Quote opened, no reply", body: "After 48 hours" }, { type: "agent", title: "Echo follows up", body: "Friendly nudge", agent: "echo" }, { type: "action", title: "Book the meeting", body: "Calendar invite", agent: "echo" }] },
  { name: "New customer onboarding", ico: "checkCircle", tone: "green", desc: "On a closed deal, kick off onboarding and a 30-day check-in.",
    steps: [{ type: "trigger", title: "Deal marked won", body: "Stage = Won" }, { type: "action", title: "Start onboarding", body: "Welcome sequence" }, { type: "action", title: "Schedule check-in", body: "30 days out" }] },
  { name: "Support triage", ico: "inbox", tone: "rose", desc: "New tickets get deflected by Pip; refunds route to you.",
    steps: [{ type: "trigger", title: "New ticket", body: "Email, chat or form" }, { type: "agent", title: "Pip drafts a reply", body: "From your help docs", agent: "echo" }, { type: "condition", title: "Sensitive? (refund)", body: "Needs a human" }, { type: "approval", title: "You approve", body: "For risky actions" }] },
  { name: "Blank canvas", ico: "plus", tone: "indigo", desc: "Start from scratch and build your own.", steps: [] },
];

function WorkflowBuilder({ agents }) {
  const workflows = useStore((s) => s.workflows);
  const activeId = useStore((s) => s.activeWorkflowId);
  const wfFlags = useStore((s) => s.productFlags);
  const fAI = window.FLflag(wfFlags, "workflows", "ai", true);
  const fTemplates = window.FLflag(wfFlags, "workflows", "templates", true);
  const fHistory = window.FLflag(wfFlags, "workflows", "history", true);
  const initGraph = stepsToGraph((FLStore.getState().workflows.find((w) => w.id === FLStore.getState().activeWorkflowId) || { steps: [] }).steps);
  const [nodes, setNodes] = useState(initGraph.nodes);
  const [edges, setEdges] = useState(initGraph.edges);
  const [sel, setSel] = useState(null);
  const [heights, setHeights] = useState({});
  const [drag, setDrag] = useState(null);
  const [link, setLink] = useState(null); // {from, x, y}
  const [firing, setFiring] = useState(null);
  const [running, setRunning] = useState(false);
  const [toast, setToast] = useState(null);
  const [zoom, setZoom] = useState(1);
  const [builder, setBuilder] = useState(false);
  const [wfMenu, setWfMenu] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [renameVal, setRenameVal] = useState("");
  const [showTemplates, setShowTemplates] = useState(false);
  const [showRuns, setShowRuns] = useState(false);
  const activeWf = workflows.find((w) => w.id === activeId) || workflows[0];

  const canvasRef = useRef(null);
  const nodeEls = useRef({});
  const movedRef = useRef(false);
  const dragRef = useRef(null);
  const zoomRef = useRef(1);

  useLayoutEffect(() => {
    const h = {};
    nodes.forEach((n) => { const el = nodeEls.current[n.id]; if (el) h[n.id] = el.offsetHeight; });
    setHeights((prev) => {
      const same = nodes.every((n) => prev[n.id] === h[n.id]) && Object.keys(prev).length === Object.keys(h).length;
      return same ? prev : h;
    });
  }, [nodes]);

  const cpos = (e) => {
    const r = canvasRef.current.getBoundingClientRect();
    const z = zoomRef.current;
    return { x: (e.clientX - r.left) / z, y: (e.clientY - r.top) / z };
  };

  // load a workflow from the store when the active one changes (e.g. created by an assistant)
  const firstWf = useRef(true);
  useEffect(() => {
    if (firstWf.current) { firstWf.current = false; return; }
    const w = workflows.find((x) => x.id === activeId);
    if (w) { const g = stepsToGraph(w.steps); setNodes(g.nodes); setEdges(g.edges); setSel(null); }
  }, [activeId]);

  // persist canvas edits back to the active workflow (debounced)
  const saveTimer = useRef(null);
  useEffect(() => {
    if (firstWf.current) return;
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => { FLStore.saveWorkflowSteps(activeId, graphToSteps(nodes, edges)); }, 700);
    return () => clearTimeout(saveTimer.current);
  }, [nodes, edges]);

  const applyTemplate = (tpl) => {
    const id = FLStore.addWorkflow({ name: tpl.name, steps: tpl.steps.map((s) => ({ ...s })) });
    setShowTemplates(false);
  };

  // ---- node dragging ----
  const onNodeDown = (e, node) => {
    if (e.target.closest(".wf-handle")) return;
    e.stopPropagation();
    const p = cpos(e);
    dragRef.current = { id: node.id, dx: p.x - node.x, dy: p.y - node.y };
    movedRef.current = false;
    window.addEventListener("pointermove", onNodeMove);
    window.addEventListener("pointerup", onNodeUp);
  };
  const onNodeMove = useCallback((e) => {
    const d = dragRef.current; if (!d) return;
    const p = cpos(e);
    if (Math.hypot(p.x - d.dx - (nodes.find(n=>n.id===d.id)?.x||0), 0) > 0) movedRef.current = true;
    movedRef.current = true;
    setDrag(d.id);
    setNodes((ns) => ns.map((n) => n.id === d.id ? { ...n, x: Math.max(0, p.x - d.dx), y: Math.max(0, p.y - d.dy) } : n));
  }, [nodes]);
  const onNodeUp = useCallback((e) => {
    window.removeEventListener("pointermove", onNodeMove);
    window.removeEventListener("pointerup", onNodeUp);
    const d = dragRef.current;
    if (d && !movedRef.current) setSel(d.id);
    setDrag(null); dragRef.current = null;
  }, [onNodeMove]);

  // ---- linking ----
  const onHandleDown = (e, node) => {
    e.stopPropagation();
    const p = cpos(e);
    setLink({ from: node.id, x: p.x, y: p.y });
    window.addEventListener("pointermove", onLinkMove);
    window.addEventListener("pointerup", onLinkUp);
  };
  const onLinkMove = useCallback((e) => {
    const p = cpos(e);
    setLink((l) => l ? { ...l, x: p.x, y: p.y } : l);
  }, []);
  const onLinkUp = useCallback((e) => {
    window.removeEventListener("pointermove", onLinkMove);
    window.removeEventListener("pointerup", onLinkUp);
    const el = document.elementFromPoint(e.clientX, e.clientY);
    const host = el && el.closest("[data-nodein]");
    setLink((l) => {
      if (l && host) {
        const to = +host.getAttribute("data-nodein");
        if (to !== l.from) {
          setEdges((es) => {
            if (es.some((x) => x.from === l.from && x.to === to)) return es;
            const fromNode = nodes.find((n) => n.id === l.from);
            let label;
            if (fromNode && fromNode.type === "condition") {
              const existing = es.filter((x) => x.from === l.from).length;
              label = existing === 0 ? "Yes" : existing === 1 ? "No" : null;
            }
            return [...es, { from: l.from, to, label }];
          });
        }
      }
      return null;
    });
  }, [onLinkMove, nodes]);

  const addNode = (item) => {
    const id = ++nid;
    const n = { id, type: item.type, title: item.title, body: item.body, agent: item.agent,
      x: 60 + (nodes.length % 3) * 30, y: 60 + (nodes.length % 5) * 26 };
    setNodes((ns) => [...ns, n]);
    setSel(id);
  };

  const deleteNode = (id) => {
    setNodes((ns) => ns.filter((n) => n.id !== id));
    setEdges((es) => es.filter((e) => e.from !== id && e.to !== id));
    setSel(null);
  };

  const updateNode = (id, patch) => setNodes((ns) => ns.map((n) => n.id === id ? { ...n, ...patch } : n));

  // ---- run ----
  const [trace, setTrace] = useState([]);
  const run = () => {
    if (running) return;
    // order by following edges from a trigger
    const start = nodes.find((n) => n.type === "trigger") || nodes[0];
    if (!start) return;
    const order = []; const seen = new Set();
    const walk = (id) => { if (seen.has(id)) return; seen.add(id); order.push(id);
      edges.filter((e) => e.from === id).forEach((e) => walk(e.to)); };
    walk(start.id);
    nodes.forEach((n) => { if (!seen.has(n.id)) order.push(n.id); });
    setRunning(true); setSel(null); setTrace([]);
    const traceLine = (n) => {
      const ag = n.agent && agents[n.agent];
      if (n.type === "trigger") return "Triggered: " + n.title;
      if (n.type === "condition") return "Checked: " + n.title + " → Yes";
      if (n.type === "approval") return "Paused for your approval";
      if (ag) return ag.name + ": " + n.body;
      return n.title;
    };
    let i = 0;
    const step = () => {
      if (i >= order.length) { setFiring(null); setRunning(false);
        window.FLStore && window.FLStore.pushFeed({ agent: "scout", ico: "workflow", tone: "amber", html: `Workflow <b>${activeWf ? activeWf.name : "automation"}</b> ran end-to-end`, meta: "just now · automation" });
        const added = window.FLStore && window.FLStore.addGreenlight({ id: "wf-lead", agent: "nadia", type: "email",
          title: "Approve outreach to a new website lead", company: "Inbound lead", value: 5200, risk: "low", policy: "review", ago: "just now",
          rows: [["To", "New lead via your website"], ["Subject", "Thanks for reaching out"]],
          body: "Hi there, thanks for getting in touch through our site. I'd love to learn what you're hoping to solve and share how we can help. Do you have 15 minutes this week?",
          why: "Generated by your 'New website lead' workflow. It paused at the approval step before sending, your guardrail for first contact." });
        const result = added ? "1 lead processed, sent to Greenlight" : "1 lead processed end-to-end";
        FLStore.logWorkflowRun(activeId, result, true);
        setToast(added ? "Workflow ran · sent 1 action to Greenlight" : "Workflow ran · 1 lead processed"); setTimeout(() => setToast(null), 3600); return; }
      const n = nodes.find((x) => x.id === order[i]);
      setFiring(order[i]);
      if (n) setTrace((tr) => [...tr, { id: n.id, line: traceLine(n), ok: true }]);
      i++; setTimeout(step, 720);
    };
    step();
  };

  const selNode = nodes.find((n) => n.id === sel);
  const agentList = Object.values(agents);
  const palette = [PALETTE[0], { grp: "Your agents", items: agentList.map((a) => ({ type: "agent", title: a.name, body: a.role, agent: a.id })) }, PALETTE[1], PALETTE[2]];

  // ---- drag-to-pan + zoom (transform-based; toolbar stays put) ----
  const stageRef = useRef(null);
  const panRef = useRef(null);
  const panPos = useRef({ x: 0, y: 0 });
  const applyPan = (smooth) => {
    if (!canvasRef.current) return;
    canvasRef.current.style.transition = smooth ? "transform .3s cubic-bezier(.3,.8,.2,1)" : "none";
    canvasRef.current.style.transformOrigin = "0 0";
    canvasRef.current.style.transform = `translate(${panPos.current.x}px, ${panPos.current.y}px) scale(${zoomRef.current})`;
  };
  const onPanMove = useCallback((e) => {
    const p = panRef.current; if (!p) return;
    panPos.current = { x: p.px + (e.clientX - p.x), y: p.py + (e.clientY - p.y) };
    applyPan(false);
  }, []);
  const onPanUp = useCallback(() => {
    window.removeEventListener("pointermove", onPanMove);
    window.removeEventListener("pointerup", onPanUp);
    panRef.current = null;
    if (stageRef.current) stageRef.current.classList.remove("panning");
  }, [onPanMove]);
  const onStageDown = (e) => {
    if (e.target.closest(".wf-node") || e.target.closest(".wf-handle") || e.target.closest(".wf-toolbar") || e.target.closest(".wf-inspector") || e.target.closest(".wf-ai") || e.target.closest(".wf-zoom")) return;
    setSel(null);
    panRef.current = { x: e.clientX, y: e.clientY, px: panPos.current.x, py: panPos.current.y };
    if (stageRef.current) stageRef.current.classList.add("panning");
    window.addEventListener("pointermove", onPanMove);
    window.addEventListener("pointerup", onPanUp);
  };
  const setZoomAbout = (z2, cx, cy) => {
    const z1 = zoomRef.current;
    z2 = Math.max(0.4, Math.min(2, z2));
    panPos.current = { x: cx - (cx - panPos.current.x) * (z2 / z1), y: cy - (cy - panPos.current.y) * (z2 / z1) };
    zoomRef.current = z2; setZoom(z2); applyPan(false);
  };
  const onWheel = (e) => {
    if (e.target.closest(".wf-ai") || e.target.closest(".wf-inspector")) return;
    e.preventDefault();
    const rect = stageRef.current.getBoundingClientRect();
    setZoomAbout(zoomRef.current * (e.deltaY < 0 ? 1.12 : 0.89), e.clientX - rect.left, e.clientY - rect.top);
  };
  const zoomBtn = (dir) => {
    const rect = stageRef.current.getBoundingClientRect();
    setZoomAbout(zoomRef.current + dir * 0.18, rect.width / 2, rect.height / 2);
  };
  const fitView = () => { panPos.current = { x: 0, y: 0 }; zoomRef.current = 1; setZoom(1); applyPan(true); };

  // ---- AI workflow builder ----
  const buildFromPrompt = (text) => {
    const t = " " + text.toLowerCase() + " ";
    const kbs = (window.FLStore.getState().knowledgeBases) || [];
    const connected = ((window.FL_DATA && window.FL_DATA.INTEGRATIONS) || []).filter((i) => i.connected);
    const steps = [{ type: "trigger", title: /form/.test(t) ? "Form submitted" : /schedule|every day|daily|each morning/.test(t) ? "On a schedule" : "New lead arrives", body: "Trigger" }];
    // pull from a connected tool when the prompt references one (or default to the first connected source)
    let src = connected.find((i) => t.includes(" " + i.name.toLowerCase()));
    if (!src && /crm|contact|deal|lead|customer|hubspot|salesforce|sync|record/.test(t)) src = connected[0];
    if (src) steps.push({ type: "data", title: `Pull from ${src.name}`, body: `Live ${src.cat.toLowerCase()} via Switchboard`, source: src.id });
    // ground the agents on a relevant knowledge base
    let kb = kbs.find((k) => t.includes(k.name.toLowerCase().split(" ")[0]));
    if (!kb && /knowledge|docs|policy|handbook|sop|brand|pricing|ground|context/.test(t)) kb = kbs[0];
    if (kb) steps.push({ type: "knowledge", title: `Ground on ${kb.name}`, body: `Retrieve context for every agent`, kb: kb.id });
    const mentions = [];
    agentList.forEach((a) => { const idx = t.indexOf(" " + a.name.toLowerCase()); if (idx >= 0) mentions.push({ idx, a }); });
    mentions.sort((x, y) => x.idx - y.idx);
    if (mentions.length) {
      mentions.forEach(({ a }) => steps.push({ type: "agent", title: `${a.name}, ${a.role.toLowerCase()}`, body: `Hand this step to ${a.name}`, agent: a.id }));
    } else {
      if (/enrich|research|score|qualif/.test(t)) steps.push({ type: "agent", title: "Scout enriches", body: "Research + score fit", agent: "scout" });
      if (/email|reach|outreach|contact|message|intro/.test(t)) steps.push({ type: "agent", title: "Nadia reaches out", body: "Draft personalized outreach", agent: "nadia" });
      if (/quote|price|proposal/.test(t)) steps.push({ type: "agent", title: "Margo quotes", body: "Generate a tailored quote", agent: "margo" });
      if (/follow|chase|remind|nudge/.test(t)) steps.push({ type: "agent", title: "Echo follows up", body: "Chase a no-reply", agent: "echo" });
    }
    if (/approv|sign[ -]?off|ask me|review|greenlight|check with me/.test(t)) steps.push({ type: "approval", title: "You approve", body: "Pause for your sign-off" });
    if (/book|demo|meeting|call|schedule a/.test(t)) steps.push({ type: "action", title: "Book the meeting", body: "Calendar invite + reminder", agent: "echo" });
    else if (/send|email/.test(t)) steps.push({ type: "action", title: "Send it", body: "Deliver + track", agent: "nadia" });
    if (/crm|uplift|update|stage|pipeline/.test(t)) steps.push({ type: "action", title: "Update Uplift", body: "Advance the deal stage" });
    if (steps.length < 2) steps.push({ type: "agent", title: "Scout handles it", body: "Research + act", agent: "scout" });
    return steps;
  };
  const generateWorkflow = (prompt) => {
    const steps = buildFromPrompt(prompt);
    const words = prompt.trim().split(/\s+/).slice(0, 4).join(" ");
    const name = words ? words[0].toUpperCase() + words.slice(1) : "AI workflow";
    FLStore.addWorkflow({ name, steps });
    fitView();
    return steps.length;
  };

  return (
    <div className="wf">
      {/* palette */}
      <div className="wf-palette">
        {palette.map((g) => (
          <div key={g.grp}>
            <h4>{g.grp}</h4>
            {g.items.map((it, i) => {
              const meta = NTYPE[it.type]; const [bg, fg] = TONE[meta.tone];
              return (
                <button key={i} className="pal-node" onClick={() => addNode(it)}>
                  <div className="pn-ico" style={{ background: bg, color: fg }}>
                    {it.agent ? <span style={{ fontSize: 10, fontWeight: 700, fontFamily: "var(--mono)", color: fg }}>{agents[it.agent].init}</span> : <Icon name={meta.ico} size={16} />}
                  </div>
                  <div style={{ minWidth: 0 }}><b>{it.title}</b><span>{it.body}</span></div>
                </button>
              );
            })}
          </div>
        ))}
      </div>

      {/* stage */}
      <div className="wf-stage" ref={stageRef} onPointerDown={onStageDown} onWheel={onWheel}>
        {/* toolbar */}
        <div className="wf-toolbar">
          <div className="wf-card" style={{ display: "flex", alignItems: "center", gap: 6, position: "relative" }}>
            {renaming ? (
              <input autoFocus value={renameVal}
                onChange={(e) => setRenameVal(e.target.value)}
                onBlur={() => { if (renameVal.trim()) FLStore.renameWorkflow(activeId, renameVal.trim()); setRenaming(false); }}
                onKeyDown={(e) => { if (e.key === "Enter") { if (renameVal.trim()) FLStore.renameWorkflow(activeId, renameVal.trim()); setRenaming(false); } if (e.key === "Escape") setRenaming(false); }}
                style={{ font: "inherit", fontSize: 13.5, fontWeight: 700, border: "1px solid var(--accent)", borderRadius: 7, padding: "3px 8px", outline: "none", maxWidth: 180, background: "var(--bg)", color: "var(--ink)" }} />
            ) : (
              <button onClick={() => setWfMenu((o) => !o)} style={{ display: "flex", alignItems: "center", gap: 7, font: "inherit", fontSize: 13.5, fontWeight: 700, letterSpacing: "-.01em", color: "var(--ink)", maxWidth: 200 }}>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{activeWf ? activeWf.name : "Workflow"}</span>
                <Icon name="chevDown" size={14} />
              </button>
            )}
            <span style={{ fontSize: 11.5, color: "var(--ink-3)" }}>· {nodes.length} steps</span>
            {wfMenu && (
              <>
                <div style={{ position: "fixed", inset: 0, zIndex: 30 }} onClick={() => setWfMenu(false)} />
                <div style={{ position: "absolute", top: 38, left: 0, minWidth: 230, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-md)", boxShadow: "var(--shadow-lg)", zIndex: 31, padding: 6, animation: "feed-in .15s both" }}>
                  <div style={{ fontSize: 10.5, fontWeight: 650, textTransform: "uppercase", letterSpacing: ".05em", color: "var(--ink-4)", padding: "6px 10px 4px" }}>Your workflows</div>
                  {workflows.map((w) => (
                    <div key={w.id} onClick={() => { FLStore.setActiveWorkflow(w.id); setWfMenu(false); }} style={{ display: "flex", alignItems: "center", gap: 9, padding: "8px 10px", borderRadius: "var(--r-sm)", cursor: "pointer", background: w.id === activeId ? "var(--accent-softer)" : "transparent" }}>
                      <Icon name="workflow" size={14} style={{ color: "var(--ink-3)" }} />
                      <span style={{ flex: 1, fontSize: 13, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{w.name}</span>
                      {!w.active && <span style={{ fontSize: 10, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>paused</span>}
                      {w.id === activeId && <Icon name="check" size={13} sw={2.4} style={{ color: "var(--accent)" }} />}
                    </div>
                  ))}
                  <div style={{ borderTop: "1px solid var(--line-2)", margin: "5px 0" }} />
                  <div className="wf-menu-act" onClick={() => { setShowTemplates(true); setWfMenu(false); }}><Icon name="layers" size={14} />New from template…</div>
                  <div className="wf-menu-act" onClick={() => { setRenameVal(activeWf ? activeWf.name : ""); setRenaming(true); setWfMenu(false); }}><Icon name="note" size={14} />Rename</div>
                  <div className="wf-menu-act" onClick={() => { FLStore.duplicateWorkflow(activeId); setWfMenu(false); }}><Icon name="layers" size={14} />Duplicate</div>
                  <div className="wf-menu-act" style={{ color: "var(--rose)" }} onClick={() => { if (workflows.length > 1) FLStore.deleteWorkflow(activeId); setWfMenu(false); }}><Icon name="x" size={14} sw={2.2} />Delete</div>
                </div>
              </>
            )}
          </div>
          <button className={"chip " + (activeWf && activeWf.active ? "green" : "")} onClick={() => FLStore.toggleWorkflowActive(activeId)} style={{ height: 28, cursor: "pointer", border: activeWf && activeWf.active ? "none" : "1px solid var(--line)" }} title="Toggle active">
            <span className="cdot" style={{ background: activeWf && activeWf.active ? "var(--green)" : "var(--ink-4)" }} />{activeWf && activeWf.active ? "Live" : "Paused"}
          </button>
          <div style={{ flex: 1 }} />
          <button className="btn btn-ghost btn-sm" onClick={() => setShowRuns(true)} style={{ display: fHistory ? undefined : "none" }}><Icon name="clock" size={15} />Runs{activeWf && activeWf.runs && activeWf.runs.length ? ` · ${activeWf.runs.length}` : ""}</button>
          <button className="btn btn-ghost btn-sm" onClick={() => setShowTemplates(true)} style={{ display: fTemplates ? undefined : "none" }}><Icon name="layers" size={15} />Templates</button>
          <button className={"btn btn-sm " + (builder ? "btn-primary" : "btn-ghost")} onClick={() => setBuilder((b) => !b)} style={{ display: fAI ? undefined : "none" }}><Icon name="spark" size={15} />Build with AI</button>
          <button className="btn btn-primary btn-sm" onClick={run} disabled={running}>
            <Icon name={running ? "spark" : "bolt"} size={15} />{running ? "Running…" : "Run"}
          </button>
        </div>

        <div className="wf-canvas" ref={canvasRef} style={{ width: 1280, height: 1080 }}>
          {/* edges */}
          <svg className="wf-edges" width="1280" height="1080">
            <defs>
              <marker id="arrow" markerWidth="9" markerHeight="9" refX="6" refY="3" orient="auto">
                <path d="M0,0 L6,3 L0,6 Z" fill="var(--ink-4)" />
              </marker>
            </defs>
            {edges.map((e, i) => {
              const s = nodes.find((n) => n.id === e.from), t = nodes.find((n) => n.id === e.to);
              if (!s || !t) return null;
              const active = running && firing === t.id;
              const sx = s.x + NODE_W / 2, sy = s.y + (heights[s.id] || NODE_H);
              const tx = t.x + NODE_W / 2, ty = t.y;
              const mx = (sx + tx) / 2, my = (sy + ty) / 2;
              return (
                <g key={i}>
                  <path d={edgePath(s, t, heights[s.id], heights[t.id])} fill="none"
                    stroke={active ? "var(--accent)" : "var(--ink-4)"} strokeWidth={active ? 3 : 2}
                    markerEnd="url(#arrow)" style={{ transition: "stroke .2s, stroke-width .2s" }} />
                  {e.label && (
                    <g transform={`translate(${mx}, ${my})`}>
                      <rect x="-17" y="-11" width="34" height="22" rx="11" fill="var(--surface)" stroke={e.label === "Yes" ? "var(--green)" : "var(--rose)"} strokeWidth="1.5" />
                      <text x="0" y="4" textAnchor="middle" fontSize="11" fontWeight="700" fontFamily="var(--mono)" fill={e.label === "Yes" ? "oklch(0.42 0.12 152)" : "oklch(0.48 0.14 18)"}>{e.label}</text>
                    </g>
                  )}
                </g>
              );
            })}
            {link && (() => {
              const s = nodes.find((n) => n.id === link.from); if (!s) return null;
              const sx = s.x + NODE_W / 2, sy = s.y + (heights[s.id] || NODE_H);
              return <path d={`M ${sx} ${sy} C ${sx} ${sy + 40}, ${link.x} ${link.y - 40}, ${link.x} ${link.y}`}
                fill="none" stroke="var(--accent)" strokeWidth="2.5" strokeDasharray="5 4" />;
            })()}
          </svg>

          {/* nodes */}
          {nodes.map((n) => {
            const meta = NTYPE[n.type]; const [bg, fg] = TONE[meta.tone];
            const ag = n.agent && agents[n.agent];
            return (
              <div key={n.id} ref={(el) => (nodeEls.current[n.id] = el)}
                className={"wf-node" + (sel === n.id ? " sel" : "") + (drag === n.id ? " dragging" : "") + (firing === n.id ? " firing" : "")}
                style={{ left: n.x, top: n.y }}
                onPointerDown={(e) => onNodeDown(e, n)}>
                <div className="wf-handle in" data-nodein={n.id} />
                <div className="wf-node-head">
                  <div className="wf-node-ico" style={{ background: bg, color: fg }}>
                    {ag ? <span style={{ fontSize: 11, fontWeight: 700, fontFamily: "var(--mono)" }}>{ag.init}</span> : <Icon name={meta.ico} size={16} />}
                  </div>
                  <div style={{ minWidth: 0 }}>
                    <b>{n.title}</b>
                    <span className="wf-type">{ag ? ag.name : meta.label}</span>
                  </div>
                </div>
                <div className="wf-node-body">{n.body}</div>
                <div className="wf-handle out" onPointerDown={(e) => onHandleDown(e, n)} />
              </div>
            );
          })}
        </div>

        {/* inspector */}
        {selNode && (
          <div className="wf-inspector" onPointerDown={(e) => e.stopPropagation()}>
            <div className="wf-insp-head">
              {(() => { const [bg, fg] = TONE[NTYPE[selNode.type].tone];
                return <div className="wf-node-ico" style={{ background: bg, color: fg, width: 28, height: 28 }}><Icon name={NTYPE[selNode.type].ico} size={14} /></div>; })()}
              <b style={{ fontSize: 13.5, fontWeight: 650, flex: 1 }}>{NTYPE[selNode.type].label} step</b>
              <button className="icon-btn" style={{ width: 28, height: 28 }} onClick={() => setSel(null)}><Icon name="x" size={15} /></button>
            </div>
            <div className="wf-insp-body">
              <div className="wf-field">
                <label>Step name</label>
                <input value={selNode.title} onChange={(e) => updateNode(selNode.id, { title: e.target.value })} />
              </div>
              <div className="wf-field">
                <label>Description</label>
                <input value={selNode.body} onChange={(e) => updateNode(selNode.id, { body: e.target.value })} />
              </div>
              {(selNode.type === "agent" || selNode.type === "action") && (
                <div className="wf-field">
                  <label>Assigned agent</label>
                  <select value={selNode.agent || ""} onChange={(e) => updateNode(selNode.id, { agent: e.target.value })}>
                    <option value="">No agent</option>
                    {agentList.map((a) => <option key={a.id} value={a.id}>{a.name}, {a.role}</option>)}
                  </select>
                </div>
              )}
              {selNode.type === "condition" && (
                <>
                  <div className="wf-field">
                    <label>Rule</label>
                    <input value={selNode.rule || "fit_score > 80"} onChange={(e) => updateNode(selNode.id, { rule: e.target.value })} />
                  </div>
                  <div style={{ display: "flex", gap: 8, padding: "10px 12px", background: "var(--surface-2)", borderRadius: "var(--r-sm)", fontSize: 12, color: "var(--ink-3)", lineHeight: 1.45 }}>
                    <Icon name="target" size={15} style={{ flexShrink: 0, marginTop: 1, color: "var(--accent-ink)" }} />
                    Drag two links from this step, the first becomes the <b style={{ color: "oklch(0.42 0.12 152)" }}>Yes</b> path, the second the <b style={{ color: "oklch(0.48 0.14 18)" }}>No</b> path.
                  </div>
                </>
              )}
              {selNode.type === "trigger" && /schedul|every day|daily|morning/i.test(selNode.title) && (
                <>
                  <div className="wf-field">
                    <label>Run frequency</label>
                    <select value={selNode.freq || "daily"} onChange={(e) => updateNode(selNode.id, { freq: e.target.value })}>
                      <option value="hourly">Every hour</option>
                      <option value="daily">Every day</option>
                      <option value="weekdays">Weekdays only</option>
                      <option value="weekly">Every week</option>
                    </select>
                  </div>
                  <div className="wf-field">
                    <label>At time</label>
                    <input type="time" value={selNode.atTime || "08:00"} onChange={(e) => updateNode(selNode.id, { atTime: e.target.value })} />
                  </div>
                  <div style={{ display: "flex", gap: 8, padding: "10px 12px", background: "var(--accent-softer)", borderRadius: "var(--r-sm)", fontSize: 12, color: "var(--accent-ink)", lineHeight: 1.45 }}>
                    <Icon name="clock" size={15} style={{ flexShrink: 0, marginTop: 1 }} />
                    Runs {selNode.freq === "hourly" ? "every hour" : selNode.freq === "weekdays" ? "every weekday" : selNode.freq === "weekly" ? "weekly" : "daily"} at {selNode.atTime || "08:00"}.
                  </div>
                </>
              )}
              <button className="btn btn-ghost btn-sm" style={{ color: "var(--rose)", justifyContent: "center" }} onClick={() => deleteNode(selNode.id)}>
                <Icon name="x" size={14} sw={2.2} />Delete step
              </button>
            </div>
          </div>
        )}

        {/* zoom controls */}
        <div className="wf-zoom" style={{ position: "absolute", left: 16, bottom: 16, zIndex: 25, display: "flex", alignItems: "center", gap: 2, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-md)", boxShadow: "var(--shadow-sm)", padding: 3 }} onPointerDown={(e) => e.stopPropagation()}>
          <button className="icon-btn" style={{ width: 32, height: 32, fontSize: 19, fontWeight: 600 }} onClick={() => zoomBtn(-1)}>−</button>
          <span style={{ fontSize: 12, fontFamily: "var(--mono)", fontWeight: 600, color: "var(--ink-2)", minWidth: 42, textAlign: "center" }}>{Math.round(zoom * 100)}%</span>
          <button className="icon-btn" style={{ width: 32, height: 32, fontSize: 18, fontWeight: 600 }} onClick={() => zoomBtn(1)}>+</button>
          <div style={{ width: 1, height: 20, background: "var(--line)", margin: "0 2px" }} />
          <button className="icon-btn" style={{ width: 32, height: 32 }} title="Fit to view" onClick={fitView}><Icon name="target" size={16} /></button>
        </div>

        {builder && <WorkflowAIPanel onGenerate={generateWorkflow} onClose={() => setBuilder(false)} />}

        {trace.length > 0 && (
          <div className="wf-trace" style={{ position: "absolute", right: 16, top: 70, width: 250, maxHeight: "calc(100% - 100px)", overflowY: "auto", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-md)", boxShadow: "var(--shadow-lg)", zIndex: 24, padding: 12 }} onPointerDown={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 10 }}>
              {running ? <span className="live-dot" style={{ width: 7, height: 7 }} /> : <Icon name="checkCircle" size={14} style={{ color: "var(--green)" }} />}
              <b style={{ fontSize: 12, fontWeight: 700, flex: 1, fontFamily: "var(--mono)", textTransform: "uppercase", letterSpacing: ".04em" }}>{running ? "Running" : "Run complete"}</b>
              {!running && <button className="icon-btn" style={{ width: 22, height: 22 }} onClick={() => setTrace([])}><Icon name="x" size={13} /></button>}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
              {trace.map((t, i) => (
                <div key={i} style={{ display: "flex", gap: 9, animation: "feed-in .3s both" }}>
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
                    <div style={{ width: 18, height: 18, borderRadius: 99, background: "var(--green)", color: "#fff", display: "grid", placeItems: "center", flexShrink: 0 }}><Icon name="check" size={11} sw={3} /></div>
                    {i < trace.length - 1 && <div style={{ width: 2, flex: 1, minHeight: 12, background: "var(--line)" }} />}
                  </div>
                  <div style={{ fontSize: 11.5, color: "var(--ink-2)", lineHeight: 1.4, paddingBottom: 11 }}>{t.line}</div>
                </div>
              ))}
              {running && <div style={{ fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)", paddingLeft: 27 }}>working…</div>}
            </div>
          </div>
        )}

        {nodes.length === 0 && !showTemplates && (
          fAI
            ? <WorkflowHero onGenerate={(p) => generateWorkflow(p)} onTemplates={() => setShowTemplates(true)} />
            : <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", pointerEvents: "none" }}>
                <div style={{ textAlign: "center", pointerEvents: "auto" }}>
                  <div className="es-ico" style={{ margin: "0 auto 14px" }}><Icon name="workflow" size={26} /></div>
                  <h4 style={{ fontSize: 16, fontWeight: 700 }}>This workflow is empty</h4>
                  <p style={{ fontSize: 13, color: "var(--ink-3)", marginTop: 5, maxWidth: 300, marginInline: "auto", lineHeight: 1.5 }}>Add steps from the palette or start from a template.</p>
                  <div style={{ display: "flex", gap: 9, justifyContent: "center", marginTop: 16 }}>
                    <button className="btn btn-primary btn-sm" onClick={() => setShowTemplates(true)}><Icon name="layers" size={14} />Browse templates</button>
                  </div>
                </div>
              </div>
        )}

        {/* templates gallery */}
        {showTemplates && (
          <div className="cmdk-scrim show" onClick={() => setShowTemplates(false)} style={{ alignItems: "center", paddingTop: 0 }}>
            <div onClick={(e) => e.stopPropagation()} style={{ width: "min(680px, 94vw)", maxHeight: "86vh", overflowY: "auto", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-xl)", boxShadow: "var(--shadow-xl)", animation: "onb-in .3s both" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "18px 22px", borderBottom: "1px solid var(--line)" }}>
                <div className="feed-ico" style={{ width: 34, height: 34, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="layers" size={17} /></div>
                <div style={{ flex: 1 }}><b style={{ fontSize: 16.5, fontWeight: 730, letterSpacing: "-.02em" }}>Start from a template</b><div style={{ fontSize: 12, color: "var(--ink-3)" }}>Proven automations, ready to customize</div></div>
                <button className="icon-btn" onClick={() => setShowTemplates(false)}><Icon name="x" size={18} /></button>
              </div>
              <div style={{ padding: 18, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                {WF_TEMPLATES.map((tpl) => {
                  const [bg, fg] = TONE[tpl.tone] || TONE.indigo;
                  return (
                    <button key={tpl.name} className="card" style={{ padding: 16, textAlign: "left", cursor: "pointer", display: "flex", flexDirection: "column", gap: 9 }} onClick={() => applyTemplate(tpl)}>
                      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <div className="feed-ico" style={{ width: 32, height: 32, background: bg, color: fg }}><Icon name={tpl.ico} size={16} /></div>
                        <b style={{ fontSize: 13.5, fontWeight: 680, flex: 1 }}>{tpl.name}</b>
                        {tpl.steps.length > 0 && <span style={{ fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>{tpl.steps.length}</span>}
                      </div>
                      <p style={{ fontSize: 12, color: "var(--ink-3)", lineHeight: 1.45 }}>{tpl.desc}</p>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        )}

        {/* run history */}
        {showRuns && (
          <div className="cmdk-scrim show" onClick={() => setShowRuns(false)} style={{ alignItems: "center", paddingTop: 0 }}>
            <div onClick={(e) => e.stopPropagation()} style={{ width: "min(520px, 94vw)", maxHeight: "82vh", overflowY: "auto", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-xl)", boxShadow: "var(--shadow-xl)", animation: "onb-in .3s both" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "18px 22px", borderBottom: "1px solid var(--line)" }}>
                <div className="feed-ico" style={{ width: 34, height: 34, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="clock" size={17} /></div>
                <div style={{ flex: 1 }}><b style={{ fontSize: 16.5, fontWeight: 730, letterSpacing: "-.02em" }}>Run history</b><div style={{ fontSize: 12, color: "var(--ink-3)" }}>{activeWf ? activeWf.name : ""}</div></div>
                <button className="icon-btn" onClick={() => setShowRuns(false)}><Icon name="x" size={18} /></button>
              </div>
              <div style={{ padding: 16 }}>
                {(!activeWf || !activeWf.runs || activeWf.runs.length === 0) ? (
                  <div className="empty-state"><div className="es-ico"><Icon name="clock" size={22} /></div><h4>No runs yet</h4><p>Hit Run to execute this workflow, its history will show up here.</p></div>
                ) : (() => {
                  const runs = activeWf.runs; const okN = runs.filter((r) => r.ok).length; const failN = runs.length - okN;
                  const br = activeWf.branches; const brTot = br ? (br.Yes + br.No) : 0;
                  return (
                  <>
                    {failN > 0 && (
                      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "11px 13px", background: "var(--rose-soft)", borderRadius: "var(--r-md)", marginBottom: 13 }}>
                        <Icon name="bolt" size={16} style={{ color: "oklch(0.48 0.14 18)", flexShrink: 0 }} />
                        <span style={{ fontSize: 12.5, color: "oklch(0.42 0.12 18)", flex: 1 }}>{failN} failed run{failN > 1 ? "s" : ""} recently. Most common: an enrichment timeout at step 2.</span>
                        <button className="btn btn-sm" style={{ background: "oklch(0.48 0.14 18)", color: "#fff" }} onClick={() => setToast("Failure alerts will ping you in Slack + email")}>Alert me</button>
                      </div>
                    )}
                    <div style={{ display: "flex", gap: 16, marginBottom: 14 }}>
                      <div><div style={{ fontSize: 20, fontWeight: 770, color: "var(--green)" }}>{Math.round(okN / runs.length * 100)}%</div><div style={{ fontSize: 11, color: "var(--ink-4)" }}>success rate</div></div>
                      <div><div style={{ fontSize: 20, fontWeight: 770 }}>{okN}</div><div style={{ fontSize: 11, color: "var(--ink-4)" }}>succeeded</div></div>
                      <div><div style={{ fontSize: 20, fontWeight: 770, color: failN ? "var(--rose)" : "var(--ink)" }}>{failN}</div><div style={{ fontSize: 11, color: "var(--ink-4)" }}>failed</div></div>
                    </div>
                    {br && (
                      <div style={{ marginBottom: 16 }}>
                        <div className="so-section-label" style={{ marginBottom: 8 }}>Branch conversion</div>
                        <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
                          {[["Yes", br.Yes, "var(--green)"], ["No", br.No, "var(--rose)"]].map(([l, n, c]) => (
                            <div key={l} style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 12.5 }}>
                              <span style={{ width: 30, fontWeight: 600 }}>{l}</span>
                              <span className="rep-bar"><span style={{ width: (n / brTot * 100) + "%", background: c }} /></span>
                              <span style={{ fontFamily: "var(--mono)", fontWeight: 650, width: 64, textAlign: "right" }}>{n} · {Math.round(n / brTot * 100)}%</span>
                            </div>
                          ))}
                        </div>
                        <p style={{ fontSize: 11.5, color: "var(--ink-4)", marginTop: 7 }}>The "Yes" path (fit &gt; 80) books {Math.round(br.Yes / brTot * 100)}% of demos.</p>
                      </div>
                    )}
                    <div className="so-section-label" style={{ marginBottom: 8 }}>Recent runs</div>
                    {runs.map((r, i) => (
                      <div key={i} style={{ display: "flex", alignItems: "center", gap: 12, padding: "11px 4px", borderBottom: i < runs.length - 1 ? "1px solid var(--line-2)" : "none" }}>
                        <div className="feed-ico" style={{ width: 30, height: 30, background: r.ok ? "var(--green-soft)" : "var(--rose-soft)", color: r.ok ? "oklch(0.42 0.12 152)" : "oklch(0.48 0.14 18)" }}><Icon name={r.ok ? "checkCircle" : "x"} size={15} /></div>
                        <div style={{ flex: 1, minWidth: 0 }}><b style={{ fontSize: 13, fontWeight: 600 }}>{r.result}</b><div style={{ fontSize: 11.5, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>{r.when}{r.dur ? " · " + r.dur : ""}</div></div>
                        <button className="btn btn-ghost btn-sm" onClick={() => { setShowRuns(false); run(); }} title="Replay this run"><Icon name="refresh" size={13} />Replay</button>
                      </div>
                    ))}
                  </>
                  ); })()}
              </div>
            </div>
          </div>
        )}

        {toast && (
          <div style={{ position: "absolute", bottom: 22, left: "50%", transform: "translateX(-50%)", zIndex: 50,
            background: "var(--ink)", color: "var(--bg)", borderRadius: "var(--r-md)", padding: "12px 18px",
            display: "flex", alignItems: "center", gap: 10, boxShadow: "var(--shadow-xl)", animation: "feed-in .3s both" }}>
            <Icon name="checkCircle" size={18} /><span style={{ fontSize: 13.5, fontWeight: 600 }}>{toast}</span>
          </div>
        )}
      </div>
    </div>
  );
}

window.WorkflowBuilder = WorkflowBuilder;
