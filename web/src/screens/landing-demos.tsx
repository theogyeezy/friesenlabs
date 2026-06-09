// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton } = window as any;
// landing-demos.jsx, interactive product demos for the marketing site

const TONE2 = {
  indigo: ["var(--accent-soft)", "var(--accent-ink)"],
  amber:  ["var(--amber-soft)", "oklch(0.5 0.12 60)"],
  green:  ["var(--green-soft)", "oklch(0.42 0.12 152)"],
  rose:   ["var(--rose-soft)", "oklch(0.48 0.14 18)"],
};

/* ---------- Agents demo: Nadia the fox sends outbound + books demos ---------- */
const FOX_TARGETS = [
  ["Birch & Co. Roasters", "Dana"], ["Tidewater Dental", "Marcus"], ["North Loop Cycles", "Sam"],
  ["Cedar Street Yoga", "Priya"], ["Maple Grove Vet", "Tom"], ["Lantern Bakehouse", "Aisha"],
  ["Riverside Plumbing", "Gus"], ["Quill & Press", "Bea"], ["Sundial Landscaping", "Owen"],
];
function FoxDemo() {
  const [msgs, setMsgs] = useState([]);
  const [sent, setSent] = useState(28);
  const [booked, setBooked] = useState(6);
  const [working, setWorking] = useState(true);
  const idx = useRef(0);

  const fire = useCallback(() => {
    const [co, name] = FOX_TARGETS[idx.current % FOX_TARGETS.length];
    const isBooked = idx.current % 3 === 2;
    idx.current++;
    setSent((s) => s + 1);
    if (isBooked) setBooked((b) => b + 1);
    setMsgs((m) => [{ id: Date.now() + Math.random(), co, name, kind: isBooked ? "booked" : "sent" }, ...m].slice(0, 5));
  }, []);

  useEffect(() => {
    if (!working) return;
    const iv = setInterval(fire, 1600);
    return () => clearInterval(iv);
  }, [working, fire]);

  return (
    <div className="fox-stage">
      <div className="fox-head">
        <div className={"fox-orb" + (working ? " working" : "")}>🦊</div>
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <b style={{ fontSize: 16, fontWeight: 720 }}>Nadia</b>
            <span className="chip green" style={{ height: 20 }}><span className="cdot" style={{ background: "var(--green)" }} />{working ? "Working" : "Paused"}</span>
          </div>
          <p style={{ fontSize: 12.5, color: "var(--ink-3)", marginTop: 2 }}>Outreach agent · booking demos on autopilot</p>
        </div>
        <button className="btn btn-ghost btn-sm" onClick={() => setWorking((w) => !w)}>
          <Icon name={working ? "pause" : "play"} size={14} />{working ? "Pause" : "Resume"}
        </button>
      </div>

      <div className="fox-counter">
        <div><div className="v"><CountUp value={sent} /></div><div className="l">Messages sent</div></div>
        <div><div className="v" style={{ color: "var(--green)" }}><CountUp value={booked} /></div><div className="l">Demos booked</div></div>
        <div style={{ marginLeft: "auto", alignSelf: "center" }}>
          <button className="btn btn-soft btn-sm" onClick={() => { fire(); setTimeout(fire, 180); setTimeout(fire, 360); }}><Icon name="send" size={13} />Send a burst</button>
        </div>
      </div>

      <div className="fox-msgs">
        {msgs.length === 0 && <p style={{ fontSize: 12.5, color: "var(--ink-4)", padding: "10px 2px" }}>Watching Nadia work…</p>}
        {msgs.map((m) => {
          const [bg, fg] = m.kind === "booked" ? TONE2.green : TONE2.indigo;
          return (
            <div className="fox-msg" key={m.id}>
              <div className="fm-ico" style={{ background: bg, color: fg }}><Icon name={m.kind === "booked" ? "calendar" : "send"} size={14} /></div>
              <div style={{ flex: 1 }}>
                <b>{m.kind === "booked" ? `Demo booked with ${m.co}` : `Outbound sent to ${m.name} at ${m.co}`}</b>
                <span style={{ display: "block" }}>{m.kind === "booked" ? "Added to your calendar · reminder set" : "Personalized intro + booking link"}</span>
              </div>
              <span className="kbd" style={{ fontSize: 10 }}>{m.kind === "booked" ? "↳ reply" : "now"}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ---------- Uplift demo: mini drag-and-drop kanban ---------- */
const MK_STAGES = [["lead", "New", "oklch(0.66 0.12 235)"], ["qualified", "Qualified", "oklch(0.56 0.17 277)"], ["won", "Won", "oklch(0.62 0.13 152)"]];
const MK_NOTES = ["🦉 scoring fit…", "🦊 drafting intro…", "🦝 building quote…", "🦜 following up…", "🦫 reconciling…", "🦊 sending email…"];
function KanbanDemo() {
  const [cards, setCards] = useState([
    { id: 1, co: "Birch & Co.", note: "🦉 scored 88", stage: "lead", color: "oklch(0.56 0.17 277)", value: 18500 },
    { id: 2, co: "Tidewater", note: "🦉 enriching…", stage: "lead", color: "oklch(0.66 0.12 235)", value: 7200 },
    { id: 3, co: "Cedar Yoga", note: "🦜 booked call", stage: "qualified", color: "oklch(0.62 0.15 18)", value: 6800 },
    { id: 4, co: "Lantern Bake", note: "🦝 quote sent", stage: "qualified", color: "oklch(0.66 0.14 50)", value: 15700 },
    { id: 5, co: "Sundial", note: "🦫 signed", stage: "won", color: "oklch(0.62 0.13 152)", value: 13900 },
  ]);
  const [drag, setDrag] = useState(null);
  const [over, setOver] = useState(null);
  const [working, setWorking] = useState(null);
  const start = useRef(null);
  const moved = useRef(false);

  useEffect(() => {
    const iv = setInterval(() => {
      if (start.current) return;
      setCards((cs) => {
        if (!cs.length) return cs;
        const i = Math.floor(Math.random() * cs.length);
        const card = cs[i];
        setWorking(card.id); setTimeout(() => setWorking(null), 1100);
        const order = ["lead", "qualified", "won"];
        const adv = Math.random() < 0.45 && card.stage !== "won";
        return cs.map((c) => c.id === card.id ? { ...c, note: MK_NOTES[Math.floor(Math.random() * MK_NOTES.length)], stage: adv ? order[order.indexOf(c.stage) + 1] : c.stage } : c);
      });
    }, 2800);
    return () => clearInterval(iv);
  }, []);

  const down = (e, card) => {
    const rect = e.currentTarget.getBoundingClientRect();
    start.current = { card, offX: e.clientX - rect.left, offY: e.clientY - rect.top };
    moved.current = false;
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const move = useCallback((e) => {
    const s = start.current; if (!s) return;
    moved.current = true;
    setDrag({ id: s.card.id, co: s.card.co, color: s.card.color, x: e.clientX - s.offX, y: e.clientY - s.offY });
    const el = document.elementFromPoint(e.clientX, e.clientY);
    const col = el && el.closest("[data-mk]");
    setOver(col ? col.getAttribute("data-mk") : null);
  }, []);
  const up = useCallback((e) => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    const s = start.current;
    if (s && moved.current) {
      const el = document.elementFromPoint(e.clientX, e.clientY);
      const col = el && el.closest("[data-mk]");
      const target = col && col.getAttribute("data-mk");
      if (target) setCards((cs) => cs.map((c) => c.id === s.card.id ? { ...c, stage: target } : c));
    }
    setDrag(null); setOver(null); start.current = null;
  }, [move]);

  const pipeline = cards.reduce((t, c) => t + c.value, 0);

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="fox-head" style={{ alignItems: "center" }}>
        <div style={{ flex: 1 }}>
          <b style={{ fontSize: 15, fontWeight: 720, display: "flex", alignItems: "center", gap: 8 }}>Your pipeline <span className="live-dot" style={{ width: 6, height: 6 }} /></b>
          <p style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 2 }}>Agents working {cards.length} deals · drag a card to move it</p>
        </div>
        <div style={{ textAlign: "right" }}><div style={{ fontSize: 20, fontWeight: 760, letterSpacing: "-.02em" }}>${(pipeline / 1000).toFixed(1)}k</div><div style={{ fontSize: 11, color: "var(--ink-3)" }}>in pipeline</div></div>
      </div>
      <div className="mk-board" style={{ flex: 1 }}>
        {MK_STAGES.map(([id, label, color]) => (
          <div className="mk-col" data-mk={id} key={id} style={{ outline: over === id ? "2px solid var(--accent)" : "none" }}>
            <h5><span style={{ width: 8, height: 8, borderRadius: 99, background: color }} />{label}</h5>
            {cards.filter((c) => c.stage === id).map((c) => (
              <div className="mk-card" key={c.id} onPointerDown={(e) => down(e, c)} style={{ opacity: drag && drag.id === c.id ? .3 : 1, borderColor: working === c.id ? "var(--accent)" : undefined, boxShadow: working === c.id ? "0 0 0 2px var(--accent-soft)" : undefined, transition: "box-shadow .2s, border-color .2s" }}>
                <b>{c.co}</b><span>{c.note}</span>
              </div>
            ))}
          </div>
        ))}
        {drag && (<div className="mk-card mk-ghost" style={{ left: drag.x, top: drag.y }}><b>{drag.co}</b><span>moving…</span></div>)}
      </div>
    </div>
  );
}

/* ---------- Workflows demo: live node graph with a traveling packet ---------- */
const WF_NODES = [
  { t: "New lead", ico: "bolt", tone: "amber", x: 8, y: 6 },
  { t: "🦉 Scout enriches", ico: "spark", tone: "indigo", x: 152, y: 82 },
  { t: "Fit > 80?", ico: "target", tone: "indigo", x: 8, y: 158 },
  { t: "🦊 Nadia emails", ico: "mail", tone: "indigo", x: 152, y: 234 },
  { t: "You approve", ico: "inbox", tone: "amber", x: 8, y: 310 },
];
const WF_EDGES = [[0, 1], [1, 2], [2, 3], [3, 4]];
const NW = 146, NH = 46;
const wfCtr = (n) => ({ x: n.x + NW / 2, y: n.y + NH / 2 });
function WorkflowDemo() {
  const [firing, setFiring] = useState(-1);
  const [running, setRunning] = useState(true);
  const [processed, setProcessed] = useState(128);
  const [dot, setDot] = useState(null);
  const [result, setResult] = useState(false);
  const runRef = useRef(true);
  useEffect(() => { runRef.current = running; }, [running]);
  useEffect(() => {
    let t, i = 0;
    const tick = () => {
      if (!runRef.current) { t = setTimeout(tick, 400); return; }
      if (i >= WF_NODES.length) { setFiring(-1); setDot(null); setResult(true); setProcessed((p) => p + 1); i = 0; t = setTimeout(() => { setResult(false); tick(); }, 1600); return; }
      setFiring(i); setDot(wfCtr(WF_NODES[i])); i++; t = setTimeout(tick, 580);
    };
    t = setTimeout(tick, 500);
    return () => clearTimeout(t);
  }, []);
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 8 }}>
      <div className="fox-head">
        <div style={{ flex: 1 }}>
          <b style={{ fontSize: 15, fontWeight: 720, display: "flex", alignItems: "center", gap: 8 }}>New website lead <span className="live-dot" style={{ width: 6, height: 6 }} /></b>
          <p style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 2 }}><b style={{ color: "var(--ink)" }}><CountUp value={processed} /></b> leads processed automatically</p>
        </div>
        <button className="btn btn-ghost btn-sm" onClick={() => setRunning((r) => !r)}><Icon name={running ? "pause" : "play"} size={14} />{running ? "Pause" : "Run"}</button>
      </div>
      <div style={{ position: "relative", width: 300, height: 360, alignSelf: "center" }}>
        <svg width="300" height="360" style={{ position: "absolute", inset: 0, overflow: "visible" }}>
          {WF_EDGES.map(([a, b], k) => { const A = wfCtr(WF_NODES[a]), B = wfCtr(WF_NODES[b]); const active = running && firing === b; const mx = (A.x + B.x) / 2; return <path key={k} d={`M${A.x} ${A.y} C ${mx} ${A.y}, ${mx} ${B.y}, ${B.x} ${B.y}`} fill="none" stroke={active ? "var(--accent)" : "var(--line)"} strokeWidth={active ? 2.5 : 2} style={{ transition: "stroke .25s, stroke-width .25s" }} />; })}
        </svg>
        {WF_NODES.map((n, i) => { const [bg, fg] = TONE2[n.tone]; const on = firing === i; return (
          <div key={i} style={{ position: "absolute", left: n.x, top: n.y, width: NW, height: NH, display: "flex", alignItems: "center", gap: 8, background: "var(--surface)", border: "1.5px solid " + (on ? "var(--accent)" : "var(--line)"), borderRadius: "var(--r-md)", padding: "0 11px", boxShadow: on ? "0 0 0 3px var(--accent-soft), var(--shadow-md)" : "var(--shadow-sm)", transition: "all .25s", zIndex: 2 }}>
            <div style={{ width: 26, height: 26, borderRadius: 7, background: bg, color: fg, display: "grid", placeItems: "center", flexShrink: 0 }}><Icon name={n.ico} size={14} /></div>
            <b style={{ fontSize: 11.5, fontWeight: 650, lineHeight: 1.12 }}>{n.t}</b>
          </div>
        ); })}
        {dot && <div style={{ position: "absolute", left: dot.x - 6, top: dot.y - 6, width: 12, height: 12, borderRadius: 99, background: "var(--accent)", boxShadow: "0 0 12px var(--accent)", transition: "left .55s cubic-bezier(.4,0,.2,1), top .55s cubic-bezier(.4,0,.2,1)", zIndex: 3 }} />}
      </div>
      <div style={{ height: 22, textAlign: "center" }}>{result && <span className="chip green" style={{ animation: "feed-in .3s both" }}><Icon name="checkCircle" size={12} />Lead processed → demo booked</span>}</div>
    </div>
  );
}

/* ---------- Greenlight demo: live triage queue ---------- */
const GL_POOL = [
  { face: "🦜", title: "Follow-up to North Loop Cycles", sub: "3rd touch · Echo", body: "You've opened my last note a few times, so the timing might just be off…" },
  { face: "🦫", title: "Send invoice #INV-2051, Maple Grove", sub: "$9,300 · Ledger", body: "Annual plan invoice, Net 30. Payment link included." },
  { face: "🦉", title: "Add 12 enriched leads to your pipeline", sub: "from website · Scout", body: "Scored and ready to work, top fit is 89/100." },
];
function GreenlightDemo() {
  const seed = [
    { id: 1, face: "🦝", title: "Send quote to Lantern Bakehouse", sub: "$15,700 · Margo", body: "Growth tier fits your 4 locations and includes priority support…" },
    { id: 2, face: "🦊", title: "Intro email to Hollow Pine Cabins", sub: "first touch · Nadia", body: "Saw you're opening two cabins this spring, we automate booking follow-ups…" },
    { id: 3, face: "🦫", title: "Apply 8% loyalty discount, Sundial", sub: "within policy · Ledger", body: "3-year customer, zero late payments. Within your 10% cap." },
  ];
  const [queue, setQueue] = useState(seed);
  const [approved, setApproved] = useState(42);
  const [leaving, setLeaving] = useState(null);
  const pool = useRef(0);
  useEffect(() => {
    const iv = setInterval(() => { setQueue((q) => q.length >= 5 ? q : [...q, { ...GL_POOL[pool.current % GL_POOL.length], id: 100 + pool.current++ }]); }, 6500);
    return () => clearInterval(iv);
  }, []);
  const act = (id, ok) => {
    setLeaving({ id, ok });
    setTimeout(() => { setQueue((q) => { const n = q.filter((x) => x.id !== id); return n.length ? n : seed.map((s) => ({ ...s })); }); setLeaving(null); if (ok) setApproved((a) => a + 1); }, 340);
  };
  const top = queue[0]; const rest = queue.slice(1, 4);
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 13 }}>
      <div className="fox-head">
        <div style={{ flex: 1 }}>
          <b style={{ fontSize: 15, fontWeight: 720, display: "flex", alignItems: "center", gap: 8 }}>Greenlight <span className="live-dot" style={{ width: 6, height: 6 }} /></b>
          <p style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 2 }}>Agent actions waiting on your one-tap sign-off</p>
        </div>
        <div className="fox-counter" style={{ padding: 0, gap: 18 }}>
          <div><div className="v" style={{ color: "var(--green)" }}><CountUp value={approved} /></div><div className="l">Approved</div></div>
          <div><div className="v">{queue.length}</div><div className="l">In queue</div></div>
        </div>
      </div>
      {top && (
        <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-md)", padding: 15, boxShadow: "var(--shadow-md)", transition: "opacity .34s, transform .34s", opacity: leaving ? 0 : 1, transform: leaving ? `translateX(${leaving.ok ? 44 : -44}px)` : "none" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 11, marginBottom: 10 }}>
            <div style={{ width: 38, height: 38, borderRadius: 11, background: "var(--accent-softer)", display: "grid", placeItems: "center", fontSize: 20 }}>{top.face}</div>
            <div style={{ flex: 1 }}><b style={{ fontSize: 13.5, fontWeight: 680, display: "block" }}>{top.title}</b><span style={{ fontSize: 11.5, color: "var(--ink-3)" }}>{top.sub}</span></div>
          </div>
          <div style={{ background: "var(--surface-2)", border: "1px solid var(--line-2)", borderRadius: "var(--r-sm)", padding: "9px 11px", fontSize: 12, color: "var(--ink-2)", marginBottom: 12, lineHeight: 1.45 }}>{top.body}</div>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn-primary btn-sm" onClick={() => act(top.id, true)}><Icon name="check" size={13} sw={2.4} />Approve &amp; run</button>
            <button className="btn btn-ghost btn-sm">Edit</button>
            <button className="btn btn-ghost btn-sm" style={{ marginLeft: "auto" }} onClick={() => act(top.id, false)}><Icon name="x" size={13} sw={2.4} />Decline</button>
          </div>
        </div>
      )}
      {rest.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
          <span style={{ fontSize: 10.5, fontFamily: "var(--mono)", fontWeight: 600, letterSpacing: ".05em", color: "var(--ink-4)", textTransform: "uppercase" }}>Up next</span>
          {rest.map((r) => (
            <div key={r.id} style={{ display: "flex", alignItems: "center", gap: 10, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-sm)", padding: "8px 11px", animation: "feed-in .4s both" }}>
              <span style={{ fontSize: 16 }}>{r.face}</span>
              <b style={{ fontSize: 12.5, fontWeight: 600, flex: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.title}</b>
              <span style={{ fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>{r.sub.split(" · ")[0]}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------- Command Center demo: live agentic activity ---------- */
const CMD_FEED = [
  { face: "🦫", txt: "Approved & sent invoice #INV-2049", tone: "green", ico: "checkCircle" },
  { face: "🦝", txt: "Generated a 3-tier quote for Lantern Bakehouse", tone: "amber", ico: "doc" },
  { face: "🦉", txt: "Re-scored Maple Grove Vet to 91/100", tone: "indigo", ico: "target" },
  { face: "🦜", txt: "Booked a discovery call with Cedar Street Yoga", tone: "indigo", ico: "calendar" },
  { face: "🦊", txt: "Sent a follow-up to North Loop Cycles", tone: "indigo", ico: "mail" },
  { face: "🦉", txt: "Enriched Birch & Co. with 9 data points", tone: "indigo", ico: "spark" },
];
function CommandDemo() {
  const [feed, setFeed] = useState(CMD_FEED.slice(0, 3).map((f, i) => ({ ...f, k: i })));
  const [tasks, setTasks] = useState(342);
  const idx = useRef(3);
  useEffect(() => {
    const iv = setInterval(() => {
      const ev = CMD_FEED[idx.current % CMD_FEED.length]; idx.current++;
      setFeed((f) => [{ ...ev, k: Date.now() }, ...f].slice(0, 4));
      setTasks((t) => t + 1);
    }, 2200);
    return () => clearInterval(iv);
  }, []);
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="fox-head">
        <div style={{ flex: 1 }}>
          <b style={{ fontSize: 15, fontWeight: 720, display: "flex", alignItems: "center", gap: 8 }}>Command Center <span className="live-dot" style={{ width: 6, height: 6 }} /></b>
          <p style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 2 }}>Your agents, working right now</p>
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10 }}>
        {[["Tasks today", tasks, ""], ["Pipeline", 124.8, "k$"], ["Hours saved", 47, "h"]].map(([l, v, u]) => (
          <div key={l} style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-md)", padding: "12px 13px", boxShadow: "var(--shadow-sm)" }}>
            <div style={{ fontSize: 21, fontWeight: 760, letterSpacing: "-.02em" }}>{u === "k$" ? "$" : ""}<CountUp value={v} format={u === "k$" ? (n) => n.toFixed(1) : (n) => Math.round(n).toLocaleString()} />{u === "h" ? "h" : u === "k$" ? "k" : ""}</div>
            <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 2 }}>{l}</div>
          </div>
        ))}
      </div>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 9 }}>
        {feed.map((m) => { const [bg, fg] = TONE2[m.tone]; return (
          <div className="fox-msg" key={m.k}>
            <div className="fm-ico" style={{ background: bg, color: fg }}><Icon name={m.ico} size={14} /></div>
            <div style={{ flex: 1 }}><b>{m.txt}</b><span style={{ display: "block" }}>{m.face} agent · just now</span></div>
          </div>
        ); })}
      </div>
    </div>
  );
}

/* ---------- Integration Hub demo: connect tools, agents sync ---------- */
const INTG_DEMO = [["HubSpot", "#ff7a59", "H"], ["Gmail", "#ea4335", "G"], ["Stripe", "#635bff", "S"], ["Slack", "#4a154b", "S"], ["Calendar", "#4285f4", "C"], ["QuickBooks", "#2ca01c", "Q"]];
function IntegrationDemo() {
  const [conn, setConn] = useState({ HubSpot: true, Gmail: true });
  const [busy, setBusy] = useState(null);
  const connect = (n) => { if (conn[n] || busy) return; setBusy(n); setTimeout(() => { setBusy(null); setConn((c) => ({ ...c, [n]: true })); }, 1100); };
  const count = Object.values(conn).filter(Boolean).length;
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="fox-head">
        <div style={{ flex: 1 }}>
          <b style={{ fontSize: 15, fontWeight: 720, display: "flex", alignItems: "center", gap: 8 }}>Switchboard <span className="live-dot" style={{ width: 6, height: 6 }} /></b>
          <p style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 2 }}>{count} connected · your agents read & write to each</p>
        </div>
      </div>
      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, alignContent: "start" }}>
        {INTG_DEMO.map(([n, c, l]) => {
          const on = conn[n], loading = busy === n;
          return (
            <div key={n} style={{ background: "var(--surface)", border: "1px solid " + (on ? "var(--green)" : "var(--line)"), borderRadius: "var(--r-md)", padding: 13, display: "flex", alignItems: "center", gap: 10, boxShadow: "var(--shadow-sm)", transition: "border-color .2s" }}>
              <div style={{ width: 32, height: 32, borderRadius: 9, background: c, color: "#fff", display: "grid", placeItems: "center", fontWeight: 800, fontFamily: "var(--mono)", flexShrink: 0 }}>{l}</div>
              <b style={{ fontSize: 13, flex: 1 }}>{n}</b>
              {on ? <span className="chip green" style={{ height: 22 }}><Icon name="check" size={11} sw={2.6} />Synced</span>
                : <button className={"btn btn-sm " + (loading ? "btn-soft" : "btn-primary")} style={{ height: 28 }} onClick={() => connect(n)}>{loading ? <><Icon name="refresh" size={12} className="spin" />…</> : "Connect"}</button>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ---------- Frontline demo: Pip auto-deflects support tickets ---------- */
const SUPPORT_TICKETS = [
  { cust: "Dana O.", q: "Where's my order #4821?", kind: "deflected", intent: "Order status" },
  { cust: "Marcus L.", q: "Any same-day appointments?", kind: "deflected", intent: "Booking" },
  { cust: "Priya N.", q: "How do I reset my password?", kind: "deflected", intent: "Account" },
  { cust: "Gus H.", q: "Are you open this weekend?", kind: "deflected", intent: "Hours" },
  { cust: "Bea C.", q: "I was double-charged in May", kind: "needs", intent: "Refund" },
  { cust: "Sam P.", q: "Can I upgrade my plan?", kind: "deflected", intent: "Billing" },
  { cust: "Renee V.", q: "My unit arrived damaged", kind: "needs", intent: "Returns" },
];
function SupportDemo() {
  const [msgs, setMsgs] = useState([]);
  const [deflected, setDeflected] = useState(186);
  const [needs, setNeeds] = useState(2);
  const [working, setWorking] = useState(true);
  const idx = useRef(0);
  const fire = useCallback(() => {
    const t = SUPPORT_TICKETS[idx.current % SUPPORT_TICKETS.length]; idx.current++;
    if (t.kind === "deflected") setDeflected((d) => d + 1); else setNeeds((n) => n + 1);
    setMsgs((m) => [{ id: Date.now() + Math.random(), ...t }, ...m].slice(0, 5));
  }, []);
  useEffect(() => { if (!working) return; const iv = setInterval(fire, 1700); return () => clearInterval(iv); }, [working, fire]);
  const total = deflected + needs;
  const rate = Math.round((deflected / total) * 100);
  return (
    <div className="fox-stage">
      <div className="fox-head">
        <div className="fox-orb" style={{ background: "oklch(0.6 0.13 200 / .16)" }}>🐧</div>
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <b style={{ fontSize: 16, fontWeight: 720 }}>Pip</b>
            <span className="chip green" style={{ height: 20 }}><span className="cdot" style={{ background: "var(--green)" }} />{working ? "Working" : "Paused"}</span>
          </div>
          <p style={{ fontSize: 12.5, color: "var(--ink-3)", marginTop: 2 }}>Support agent · deflecting tickets on autopilot</p>
        </div>
        <button className="btn btn-ghost btn-sm" onClick={() => setWorking((w) => !w)}><Icon name={working ? "pause" : "play"} size={14} />{working ? "Pause" : "Resume"}</button>
      </div>
      <div className="fox-counter">
        <div><div className="v" style={{ color: "var(--green)" }}><CountUp value={rate} />%</div><div className="l">Deflection rate</div></div>
        <div><div className="v"><CountUp value={deflected} /></div><div className="l">Auto-resolved</div></div>
        <div><div className="v" style={{ color: "var(--amber)" }}>{needs}</div><div className="l">Sent to you</div></div>
      </div>
      <div className="fox-msgs">
        {msgs.length === 0 && <p style={{ fontSize: 12.5, color: "var(--ink-4)", padding: "10px 2px" }}>Watching Pip work the inbox…</p>}
        {msgs.map((m) => {
          const def = m.kind === "deflected"; const [bg, fg] = def ? TONE2.green : TONE2.amber;
          return (
            <div className="fox-msg" key={m.id}>
              <div className="fm-ico" style={{ background: bg, color: fg }}><Icon name={def ? "checkCircle" : "users"} size={14} /></div>
              <div style={{ flex: 1 }}>
                <b>{m.cust}: “{m.q}”</b>
                <span style={{ display: "block" }}>{def ? `Auto-resolved · ${m.intent}` : `Routed to you · ${m.intent} needs a human`}</span>
              </div>
              <span className="kbd" style={{ fontSize: 10 }}>{def ? "✓" : "→ you"}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ---------- Security demo: posture modes + kill switch ---------- */
const SEC_DEMO_MODES = [
  { id: "auto", label: "Live", color: "var(--green)", ico: "bolt", desc: "Agents work autonomously within your guardrails. Risky actions still need approval." },
  { id: "semi", label: "Analyze only", color: "var(--amber)", ico: "inbox", desc: "Agents read and draft, but execute nothing until you approve it." },
  { id: "paused", label: "Kill switch", color: "var(--rose)", ico: "pause", desc: "A full dead stop. Agents stop reading and acting immediately." },
];
const SEC_DEMO_GUARDS = ["Require approval above spend cap", "Redact sensitive data (PII)", "Block bulk sends over 25", "Two-person approval for refunds"];
function SecurityDemo() {
  const [mode, setMode] = useState("auto");
  const [guards, setGuards] = useState([true, true, true, false]);
  const m = SEC_DEMO_MODES.find((x) => x.id === mode);
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="fox-head">
        <div style={{ width: 46, height: 46, borderRadius: 13, display: "grid", placeItems: "center", background: "color-mix(in oklch, " + m.color + " 16%, transparent)", color: m.color, flexShrink: 0 }}><Icon name="shield" size={22} /></div>
        <div style={{ flex: 1 }}>
          <b style={{ fontSize: 15, fontWeight: 720, display: "flex", alignItems: "center", gap: 8 }}>Security posture <span style={{ width: 8, height: 8, borderRadius: 99, background: m.color }} /></b>
          <p style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 2 }}>One switch controls everything your agents can do</p>
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8 }}>
        {SEC_DEMO_MODES.map((x) => (
          <button key={x.id} onClick={() => setMode(x.id)} style={{ padding: "11px 8px", borderRadius: "var(--r-md)", border: "1.5px solid " + (mode === x.id ? x.color : "var(--line)"), background: mode === x.id ? "color-mix(in oklch, " + x.color + " 12%, transparent)" : "var(--surface)", display: "flex", flexDirection: "column", alignItems: "center", gap: 5, cursor: "pointer" }}>
            <Icon name={x.ico} size={17} style={{ color: mode === x.id ? x.color : "var(--ink-3)" }} />
            <b style={{ fontSize: 12, fontWeight: 650 }}>{x.label}</b>
          </button>
        ))}
      </div>
      <div style={{ padding: "11px 13px", borderRadius: "var(--r-sm)", background: "var(--surface-2)", fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.5, display: "flex", gap: 9, alignItems: "flex-start" }}>
        <Icon name={m.ico} size={15} style={{ color: m.color, flexShrink: 0, marginTop: 1 }} />{m.desc}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
        {SEC_DEMO_GUARDS.map((g, i) => (
          <div key={g} onClick={() => setGuards((p) => p.map((v, j) => j === i ? !v : v))} style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 12px", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-sm)", cursor: "pointer" }}>
            <Icon name="shield" size={14} style={{ color: guards[i] ? "var(--accent)" : "var(--ink-4)" }} />
            <span style={{ flex: 1, fontSize: 12.5, fontWeight: 550 }}>{g}</span>
            <div className={"tog" + (guards[i] ? " on" : "")} style={{ transform: "scale(.85)" }} />
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---------- Sidecar demo: agent side-panel riding on top of your CRM ---------- */
const SIDECAR_SUGGESTIONS = [
  { tool: "HubSpot", tc: "#ff7a59", face: "🦊", who: "Nadia", txt: "Drafted a follow-up to Dana referencing her last reply.", cta: "Approve & send" },
  { tool: "Gmail", tc: "#ea4335", face: "🦉", who: "Scout", txt: "This sender is a 91/100 fit, want me to log them as a lead?", cta: "Log as lead" },
  { tool: "HubSpot", tc: "#ff7a59", face: "🦝", who: "Margo", txt: "Prepared a 3-tier quote for Lantern Bakehouse.", cta: "Review quote" },
  { tool: "Zendesk", tc: "#03363d", face: "🐧", who: "Pip", txt: "Wrote a reply to this ticket from your help docs.", cta: "Approve & send" },
];
function SidecarDemo() {
  const [i, setI] = useState(0);
  const [done, setDone] = useState(0);
  const s = SIDECAR_SUGGESTIONS[i % SIDECAR_SUGGESTIONS.length];
  const act = () => { setDone((d) => d + 1); setTimeout(() => setI((x) => x + 1), 250); };
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 12 }}>
      <div className="fox-head">
        <div style={{ flex: 1 }}>
          <b style={{ fontSize: 15, fontWeight: 720, display: "flex", alignItems: "center", gap: 8 }}>Sidecar <span className="live-dot" style={{ width: 6, height: 6 }} /></b>
          <p style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 2 }}>Working on your connected tools · {done} action{done === 1 ? "" : "s"} taken</p>
        </div>
      </div>
      <div style={{ borderRadius: "var(--r-md)", border: "1px solid var(--line)", boxShadow: "var(--shadow-sm)", flex: 1, display: "flex", flexDirection: "column", padding: 16, background: "var(--surface)", justifyContent: "center", gap: 13 }}>
        <div key={i} style={{ animation: "fox-fly .4s both", display: "flex", flexDirection: "column", gap: 11 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
            <div style={{ width: 34, height: 34, borderRadius: 9, background: "var(--accent-softer)", display: "grid", placeItems: "center", fontSize: 18 }}>{s.face}</div>
            <div style={{ flex: 1 }}>
              <b style={{ fontSize: 13.5, fontWeight: 700 }}>{s.who}</b>
              <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: "var(--ink-3)", marginTop: 1 }}>
                <span style={{ width: 14, height: 14, borderRadius: 4, background: s.tc, color: "#fff", display: "grid", placeItems: "center", fontWeight: 800, fontSize: 8, fontFamily: "var(--mono)" }}>{s.tool[0]}</span>
                from {s.tool}
              </div>
            </div>
            <span className="chip indigo" style={{ height: 20 }}>surfaced in Friesen</span>
          </div>
          <div style={{ background: "var(--surface-2)", border: "1px solid var(--line-2)", borderRadius: "var(--r-sm)", padding: "11px 13px", fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.45 }}>{s.txt}</div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn btn-primary btn-sm" onClick={act}><Icon name="check" size={13} sw={2.4} />{s.cta}</button>
          <button className="btn btn-ghost btn-sm" onClick={() => setI((x) => x + 1)}>Skip</button>
        </div>
      </div>
    </div>
  );
}

/* ---------- Cortex demo: the flywheel that compounds ---------- */
const CORTEX_STEPS = [["target", "Predict", "An agent scores a record"], ["doc", "Log", "Prediction saved as a decision trace"], ["checkCircle", "Resolve", "Real outcome backfills the trace"], ["refresh", "Retrain", "Model learns from every closed loop"]];
function CortexDemo() {
  const [acc, setAcc] = useState(82);
  const [ver, setVer] = useState(3);
  const [traces, setTraces] = useState(1240);
  const [active, setActive] = useState(-1);
  const [running, setRunning] = useState(false);
  const runCycle = useCallback(() => {
    if (running) return; setRunning(true);
    let s = 0;
    const tick = () => {
      setActive(s); s++;
      if (s <= 3) { setTimeout(tick, 480); }
      else {
        setTimeout(() => {
          setActive(-1); setRunning(false);
          setAcc((a) => Math.min(96, +(a + (Math.random() * 1.4 + 0.6)).toFixed(1)));
          setVer((v) => v + 1);
          setTraces((t) => t + Math.floor(Math.random() * 40 + 20));
        }, 420);
      }
    };
    tick();
  }, [running]);
  useEffect(() => { const iv = setInterval(() => runCycle(), 4200); return () => clearInterval(iv); }, [runCycle]);
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="fox-head">
        <div style={{ flex: 1 }}>
          <b style={{ fontSize: 15, fontWeight: 720, display: "flex", alignItems: "center", gap: 8 }}>Cortex <span className="live-dot" style={{ width: 6, height: 6 }} /></b>
          <p style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 2 }}>Your private model · v{ver} · {traces.toLocaleString()} decision traces</p>
        </div>
        <button className="btn btn-soft btn-sm" onClick={runCycle} disabled={running}><Icon name="refresh" size={13} className={running ? "spin" : ""} />Run a cycle</button>
      </div>
      <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-md)", padding: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", marginBottom: 8 }}>
          <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--ink-2)" }}>Model accuracy</span>
          <span style={{ fontSize: 24, fontWeight: 800, letterSpacing: "-.03em", color: "var(--green)" }}>{acc}%</span>
        </div>
        <div className="meter" style={{ height: 9 }}><span style={{ width: acc + "%", background: "var(--green)" }} /></div>
        <p style={{ fontSize: 11, color: "var(--ink-4)", marginTop: 7 }}>Climbs every cycle, sharper precisely where your business is.</p>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 9 }}>
        {CORTEX_STEPS.map(([ic, t, d], i) => (
          <div key={t} style={{ display: "flex", gap: 10, alignItems: "center", padding: "11px 13px", borderRadius: "var(--r-sm)", border: "1.5px solid " + (active === i ? "var(--accent)" : "var(--line)"), background: active === i ? "var(--accent-softer)" : "var(--surface)", boxShadow: active === i ? "0 0 0 3px var(--accent-soft)" : "none", transition: "all .2s" }}>
            <div style={{ width: 28, height: 28, borderRadius: 99, border: "1.5px solid " + (active === i ? "var(--accent)" : "var(--line)"), display: "grid", placeItems: "center", fontSize: 12, fontWeight: 700, fontFamily: "var(--mono)", color: active === i ? "var(--accent-ink)" : "var(--ink-3)", flexShrink: 0 }}>{i + 1}</div>
            <div style={{ minWidth: 0 }}><b style={{ fontSize: 12.5, fontWeight: 650 }}>{t}</b><span style={{ display: "block", fontSize: 10.5, color: "var(--ink-3)", lineHeight: 1.3 }}>{d}</span></div>
          </div>
        ))}
      </div>
      <p style={{ fontSize: 11.5, color: "var(--ink-4)", textAlign: "center" }}>Every closed loop is a labeled example no competitor has, that's the moat.</p>
    </div>
  );
}

Object.assign(window, { FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo });
