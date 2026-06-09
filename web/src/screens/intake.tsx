// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// intake.jsx — universal "Intake" capture: paste/record a transcript or note,
// an agent files it to Memory, and routes it to the CRM / Calendar / Knowledge as needed.

const INTAKE_EXAMPLES = [
  "Log that I had a call with Riverside Plumbing — they're ready to move forward, just need the quote by Friday.",
  "Note for the team: our standard return window is 30 days, no receipt needed for store credit.",
  "Met a lead at the expo — Birch & Co. Roasters, wants a demo next week. Warm.",
  "Remember that Dana prefers texts over email and usually replies in the evening.",
];

// crude but believable routing: figure out where this note should go
function routeIntake(text, deals) {
  const t = text.toLowerCase();
  const routes = [];
  // 1) always → memory
  routes.push({ id: "memory", icon: "spark", tone: "indigo", dest: "Memory", note: "Embedded for recall by your agents", to: "knowledge" });

  // 2) CRM — match an existing deal by company name
  let matchedDeal = null;
  for (const d of deals) {
    const co = d.co.toLowerCase();
    const short = co.split(/[^a-z]/)[0];
    if (co && (t.includes(co) || (short.length > 3 && t.includes(short)))) { matchedDeal = d; break; }
  }
  const opp = /\b(deal|opportunity|prospect|lead|quote|proposal|demo|pricing|move forward|ready to|sign|close)\b/.test(t);
  if (matchedDeal) {
    routes.push({ id: "crm", icon: "users", tone: "rose", dest: "Uplift CRM", note: `Logged to ${matchedDeal.co}'s timeline`, to: "crm", deal: matchedDeal });
  } else if (opp && /\b(met|new|lead|prospect)\b/.test(t)) {
    routes.push({ id: "crm-new", icon: "users", tone: "rose", dest: "Uplift CRM", note: "Flagged a new opportunity to add", to: "crm" });
  }

  // 3) Calendar — a follow-up / scheduling intent
  if (/\b(next week|tomorrow|follow up|follow-up|schedule|book|call them|meeting|monday|tuesday|wednesday|thursday|friday|by friday|demo)\b/.test(t)) {
    routes.push({ id: "cal", icon: "calendar", tone: "amber", dest: "Calendar", note: "Drafted a follow-up reminder", to: "calendar" });
  }

  // 4) Knowledge — durable facts / policy / how-we-do-it
  if (/\b(policy|return|refund|hours|price|pricing|sop|process|how we|standard|always|warranty|guarantee|prefers|usually)\b/.test(t)) {
    routes.push({ id: "kb", icon: "doc", tone: "green", dest: "Knowledge", note: "Added to your Business Brain", to: "knowledge" });
  }
  return routes;
}

function IntakeModal({ open, onClose, onNavigate, onOpenDeal }) {
  const deals = useStore((s) => s.deals);
  const [text, setText] = useState("");
  const [mode, setMode] = useState("type");
  const [rec, setRec] = useState(false);
  const [phase, setPhase] = useState("input"); // input | processing | done
  const [stepI, setStepI] = useState(0);
  const [routes, setRoutes] = useState([]);
  const taRef = useRef(null);
  const recTimer = useRef(null);

  useEffect(() => { if (open) { setText(""); setPhase("input"); setRoutes([]); setMode("type"); setRec(false); setStepI(0); } }, [open]);
  useEffect(() => () => clearInterval(recTimer.current), []);
  if (!open) return null;

  const PROC_STEPS = ["Transcribing & cleaning up", "Understanding what happened", "Deciding where it belongs", "Filing it across your workspace"];

  const record = () => {
    if (rec) { clearInterval(recTimer.current); setRec(false); return; }
    setMode("record"); setRec(true); setText("");
    const sample = "Just wrapped a call with Riverside Plumbing. Gus is ready to move forward, he just needs the updated quote by Friday. He also asked about our standard warranty — let him know it's 12 months. Should follow up early next week to confirm.";
    let i = 0;
    recTimer.current = setInterval(() => {
      i += 4; setText(sample.slice(0, i));
      if (i >= sample.length) { clearInterval(recTimer.current); setRec(false); }
    }, 28);
  };

  const process = () => {
    if (!text.trim()) return;
    const r = routeIntake(text, deals);
    setRoutes(r); setPhase("processing"); setStepI(0);
    let s = 0;
    const iv = setInterval(() => { s++; setStepI(s); if (s >= PROC_STEPS.length) { clearInterval(iv); commit(r); setPhase("done"); } }, 620);
  };

  const commit = (r) => {
    // actually file it
    FLStore.saveMemory({ text: text.trim(), kind: "intake", source: mode === "record" ? "Voice memo" : "Note", agent: "scout" });
    const crm = r.find((x) => x.deal);
    if (crm && FLStore.logDealActivity) FLStore.logDealActivity(crm.deal.id, "Intake: " + text.trim().slice(0, 80) + (text.length > 80 ? "…" : ""), "note", "indigo");
  };

  const dealRoute = routes.find((x) => x.deal);

  return (
    <div className="cmdk-scrim show" onClick={onClose} style={{ alignItems: "flex-start", paddingTop: "8vh" }}>
      <div className="cmdk intake-modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 560 }}>
        <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--line)", display: "flex", alignItems: "center", gap: 11 }}>
          <div className="feed-ico" style={{ width: 32, height: 32, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="plus" size={17} sw={2.4} /></div>
          <div style={{ flex: 1 }}><b style={{ fontSize: 16, fontWeight: 720 }}>Intake</b><div style={{ fontSize: 12, color: "var(--ink-4)" }}>Capture anything — your agent files it where it belongs</div></div>
          <button className="icon-btn" onClick={onClose}><Icon name="x" size={18} /></button>
        </div>

        {phase === "input" && (
          <div style={{ padding: 20 }}>
            <div className="seg" style={{ marginBottom: 13 }}>
              <button className={mode === "type" ? "active" : ""} onClick={() => setMode("type")}><Icon name="doc" size={14} />Type or paste</button>
              <button className={mode === "record" ? "active" : ""} onClick={record}><Icon name={rec ? "pause" : "phone"} size={14} />{rec ? "Recording…" : "Record memo"}</button>
            </div>
            <textarea ref={taRef} autoFocus value={text} onChange={(e) => setText(e.target.value)} rows={5}
              placeholder="e.g. Log that I had a call with Acme — they're ready to move forward, send the quote by Friday."
              style={{ width: "100%", resize: "vertical", border: "1px solid var(--line)", borderRadius: "var(--r-md)", padding: "12px 14px", fontSize: 14, lineHeight: 1.55, background: "var(--bg)", color: "var(--ink)", fontFamily: "inherit", outline: "none" }} />
            {rec && <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 9, fontSize: 12.5, color: "var(--rose)" }}><span className="live-dot" style={{ background: "var(--rose)" }} />Transcribing your voice memo…</div>}
            <div style={{ marginTop: 13 }}>
              <div style={{ fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)", textTransform: "uppercase", letterSpacing: ".05em", marginBottom: 8 }}>Try one</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {INTAKE_EXAMPLES.map((ex) => (
                  <button key={ex} onClick={() => { setMode("type"); setText(ex); }} className="intake-eg">{ex}</button>
                ))}
              </div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 16 }}>
              <span style={{ fontSize: 12, color: "var(--ink-4)", flex: 1 }}>Filed privately to your instance and embedded for recall.</span>
              <button className="btn btn-primary" disabled={!text.trim()} onClick={process}><Icon name="spark" size={15} />File it for me</button>
            </div>
          </div>
        )}

        {phase === "processing" && (
          <div style={{ padding: "30px 24px" }}>
            {PROC_STEPS.map((s, i) => (
              <div key={s} style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 0", opacity: i <= stepI ? 1 : 0.4, transition: "opacity .3s" }}>
                <div style={{ width: 24, height: 24, borderRadius: 99, display: "grid", placeItems: "center", background: i < stepI ? "var(--green)" : i === stepI ? "var(--accent)" : "var(--surface-2)", color: i <= stepI ? "#fff" : "var(--ink-4)" }}>
                  {i < stepI ? <Icon name="check" size={13} sw={3} /> : i === stepI ? <span className="intake-spin" /> : <span style={{ fontSize: 11, fontFamily: "var(--mono)" }}>{i + 1}</span>}
                </div>
                <span style={{ fontSize: 14, fontWeight: i === stepI ? 650 : 500 }}>{s}</span>
              </div>
            ))}
          </div>
        )}

        {phase === "done" && (
          <div style={{ padding: 20 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
              <div className="feed-ico" style={{ width: 30, height: 30, background: "var(--green-soft)", color: "oklch(0.42 0.12 152)" }}><Icon name="check" size={16} sw={2.6} /></div>
              <b style={{ fontSize: 15, fontWeight: 700 }}>Filed across your workspace</b>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
              {routes.map((r) => { const tt = { indigo: ["var(--accent-soft)", "var(--accent-ink)"], rose: ["var(--rose-soft)", "oklch(0.48 0.14 18)"], amber: ["var(--amber-soft)", "oklch(0.5 0.12 60)"], green: ["var(--green-soft)", "oklch(0.42 0.12 152)"] }[r.tone]; return (
                <div key={r.id} style={{ display: "flex", alignItems: "center", gap: 11, padding: "11px 13px", border: "1px solid var(--line)", borderRadius: "var(--r-md)" }}>
                  <div className="feed-ico" style={{ width: 30, height: 30, background: tt[0], color: tt[1], flexShrink: 0 }}><Icon name={r.icon} size={15} /></div>
                  <div style={{ flex: 1, minWidth: 0 }}><b style={{ fontSize: 13, fontWeight: 650, display: "block" }}>{r.dest}</b><span style={{ fontSize: 11.5, color: "var(--ink-4)" }}>{r.note}</span></div>
                  <Icon name="check" size={15} sw={2.4} style={{ color: "var(--green)" }} />
                </div>
              ); })}
            </div>
            <div style={{ display: "flex", gap: 8, marginTop: 18 }}>
              {dealRoute && <button className="btn btn-soft" style={{ flex: 1 }} onClick={() => { onClose(); onNavigate("crm"); onOpenDeal && onOpenDeal(dealRoute.deal); }}><Icon name="users" size={15} />View in CRM</button>}
              <button className="btn btn-soft" style={{ flex: 1 }} onClick={() => { onClose(); onNavigate("knowledge"); }}><Icon name="spark" size={15} />View memory</button>
              <button className="btn btn-primary" onClick={() => { setText(""); setPhase("input"); setRoutes([]); }}>Log another</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

window.IntakeModal = IntakeModal;
