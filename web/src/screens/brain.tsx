// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// brain.jsx — Business Brain: founder interview (typed or voice memo) + memory shelf + save-to-memory

// canned transcript fragments so the voice-memo demo feels real (user edits after)
const BRAIN_TRANSCRIPTS = {
  what: "We're a family-run landscaping shop. We design and maintain outdoor spaces for homeowners and a few small commercial clients around the county.",
  who: "Our best customers are homeowners who care about their yard and have the budget to maintain it year-round. One-off cheap mow jobs are not a fit for us.",
  diff: "People stick with us because we actually show up when we say we will, and the same crew comes every time, so they know your property.",
  voice: "Warm and neighborly. First names, no corporate stiffness. We talk like a person who genuinely cares about your yard.",
  why: "I started this after years working for someone who cut corners. I wanted a crew that does it right and treats customers like neighbors.",
  proud: "We rebuilt an elderly couple's garden after a storm wiped it out, stayed late for a week, and they cried when they saw it. That's the standard.",
};

function MemoryComposer({ onClose, defaults }) {
  const [text, setText] = useState(defaults && defaults.text || "");
  const [tag, setTag] = useState(defaults && defaults.tag || "Note");
  const TAGS = ["Note", "Voice", "Policy", "Account", "Preference", "Win"];
  return (
    <div className="cmdk-scrim show" onClick={onClose} style={{ alignItems: "center", paddingTop: 0, zIndex: 120 }}>
      <div className="cmdk" style={{ maxWidth: 440 }} onClick={(e) => e.stopPropagation()}>
        <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--line)", display: "flex", alignItems: "center", gap: 11 }}>
          <div className="feed-ico" style={{ width: 32, height: 32, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="spark" size={16} /></div>
          <div style={{ flex: 1 }}><b style={{ fontSize: 15.5, fontWeight: 720 }}>Save to memory</b><div style={{ fontSize: 11.5, color: "var(--ink-4)" }}>{defaults && defaults.source ? "From " + defaults.source : "Remembered for future recall"}</div></div>
          <button className="icon-btn" onClick={onClose}><Icon name="x" size={18} /></button>
        </div>
        <div style={{ padding: 18, display: "flex", flexDirection: "column", gap: 13 }}>
          <div className="wf-field"><label>What should we remember?</label>
            <textarea autoFocus value={text} onChange={(e) => setText(e.target.value)} rows={3} placeholder="e.g. This customer always prefers a phone call over email."
              style={{ width: "100%", resize: "vertical", border: "1px solid var(--line)", borderRadius: "var(--r-sm)", padding: "10px 12px", fontSize: 13.5, lineHeight: 1.5, background: "var(--bg)", color: "var(--ink)", fontFamily: "inherit", outline: "none" }} />
          </div>
          <div className="wf-field"><label>Tag</label>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {TAGS.map((t) => <button key={t} className="chip" style={{ cursor: "pointer", height: 26, background: tag === t ? "var(--accent-soft)" : "var(--surface-2)", color: tag === t ? "var(--accent-ink)" : "var(--ink-3)", border: tag === t ? "none" : "1px solid var(--line)" }} onClick={() => setTag(t)}>{tag === t && <Icon name="check" size={11} sw={2.6} />}{t}</button>)}
            </div>
          </div>
          <button className="btn btn-primary" disabled={!text.trim()} onClick={() => { FLStore.saveMemory({ text: text.trim(), tag, source: (defaults && defaults.source) || "Manual", agent: defaults && defaults.agent }); onClose(); }}>
            <Icon name="spark" size={15} />Save to Business Brain
          </button>
          <p style={{ fontSize: 11, color: "var(--ink-4)", textAlign: "center" }}>Embedded into your knowledge so every agent can recall it.</p>
        </div>
      </div>
    </div>
  );
}

// reusable inline button used in chats, deals, anywhere
function SaveToMemoryBtn({ source, agent, getText, small, label = "Save to memory" }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button className={"btn btn-ghost " + (small ? "btn-sm" : "")} onClick={() => setOpen(true)} title="Save to memory for future recall">
        <Icon name="spark" size={small ? 13 : 15} />{label}
      </button>
      {open && <MemoryComposer onClose={() => setOpen(false)} defaults={{ text: getText ? getText() : "", source, agent }} />}
    </>
  );
}

// voice-memo recorder (simulated capture → editable transcript)
function VoiceRecorder({ qid, onTranscript }) {
  const [phase, setPhase] = useState("idle"); // idle | rec | transcribing
  const [secs, setSecs] = useState(0);
  const timer = useRef(null);
  useEffect(() => () => clearInterval(timer.current), []);
  const start = () => { setPhase("rec"); setSecs(0); timer.current = setInterval(() => setSecs((s) => s + 1), 1000); };
  const stop = () => {
    clearInterval(timer.current); setPhase("transcribing");
    setTimeout(() => { setPhase("idle"); onTranscript(BRAIN_TRANSCRIPTS[qid] || "(Transcribed memo. Edit this to match what you said.)"); }, 1400);
  };
  const mmss = `${String(Math.floor(secs / 60)).padStart(2, "0")}:${String(secs % 60).padStart(2, "0")}`;
  if (phase === "transcribing") return <span style={{ display: "inline-flex", alignItems: "center", gap: 7, fontSize: 12.5, color: "var(--ink-3)" }}><Icon name="refresh" size={14} className="spin" />Transcribing…</span>;
  if (phase === "rec") return (
    <button className="btn btn-sm" onClick={stop} style={{ background: "var(--rose-soft)", color: "oklch(0.48 0.14 18)" }}>
      <span className="live-dot" style={{ width: 7, height: 7, background: "oklch(0.55 0.18 18)" }} />Recording {mmss} · tap to stop
    </button>
  );
  return <button className="btn btn-ghost btn-sm" onClick={start}><Icon name="phone" size={13} />Record a memo</button>;
}

function BrainInterview({ onClose }) {
  const { BRAIN_QUESTIONS } = window.FL_DATA;
  const saved = useStore((s) => s.brainAnswers);
  const flat = BRAIN_QUESTIONS.flatMap((g) => g.items.map((it) => ({ ...it, group: g.group })));
  const [started, setStarted] = useState(false);
  const [i, setI] = useState(0);
  const [draft, setDraft] = useState("");
  const cur = flat[i];
  const answeredCount = flat.filter((q) => saved[q.id] && saved[q.id].text).length;

  useEffect(() => { setDraft(saved[cur.id] && saved[cur.id].text || ""); }, [i]);

  const commit = (skip) => {
    if (!skip && draft.trim()) FLStore.saveBrainAnswer(cur.id, cur.q, draft.trim());
    if (i >= flat.length - 1) { onClose(true); return; }
    setI(i + 1);
  };

  return (
    <div className="cmdk-scrim show" onClick={() => onClose(false)} style={{ alignItems: "center", paddingTop: 0, zIndex: 100 }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: "min(620px, 95vw)", maxHeight: "90vh", overflowY: "auto", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-xl)", boxShadow: "var(--shadow-xl)", animation: "onb-in .3s both" }}>
        {!started ? (
          <div style={{ padding: "34px 34px 30px" }}>
            <div className="feed-ico" style={{ width: 50, height: 50, background: "var(--accent-soft)", color: "var(--accent-ink)", borderRadius: 15 }}><Icon name="spark" size={25} /></div>
            <h2 style={{ fontSize: 24, fontWeight: 770, letterSpacing: "-.03em", marginTop: 16 }}>Let's build your business brain</h2>
            <p style={{ fontSize: 14.5, color: "var(--ink-2)", lineHeight: 1.6, marginTop: 10 }}>A few questions about your business, your customers, and what you care about. Your answers get embedded into your private knowledge so every agent truly understands your business and sounds like you.</p>
            <div style={{ display: "flex", gap: 10, marginTop: 18, padding: "13px 15px", background: "var(--accent-softer)", borderRadius: "var(--r-md)" }}>
              <Icon name="checkCircle" size={18} style={{ color: "var(--accent-ink)", flexShrink: 0, marginTop: 1 }} />
              <div style={{ fontSize: 13, color: "var(--accent-ink)", lineHeight: 1.55 }}><b style={{ fontWeight: 700 }}>You don't need to answer everything.</b> Skip anything you like and come back later. The more you share, the sharper your agents get, but even a few answers help.</div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 16, marginTop: 18, fontSize: 12.5, color: "var(--ink-3)" }}>
              <span style={{ display: "flex", alignItems: "center", gap: 6 }}><Icon name="phone" size={14} />Record a memo, we transcribe it</span>
              <span style={{ display: "flex", alignItems: "center", gap: 6 }}><Icon name="note" size={14} />Or just type</span>
            </div>
            <div style={{ display: "flex", gap: 9, marginTop: 24 }}>
              <button className="btn btn-primary btn-lg" onClick={() => setStarted(true)}><Icon name="spark" size={16} />{answeredCount > 0 ? "Continue building" : "Start"}</button>
              <button className="btn btn-ghost btn-lg" onClick={() => onClose(false)}>Maybe later</button>
            </div>
            {answeredCount > 0 && <p style={{ fontSize: 12, color: "var(--ink-4)", marginTop: 12 }}>{answeredCount} of {flat.length} answered so far.</p>}
          </div>
        ) : (
          <div style={{ padding: "22px 28px 26px" }}>
            {/* progress */}
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 20 }}>
              <button className="icon-btn" style={{ width: 30, height: 30 }} onClick={() => i > 0 ? setI(i - 1) : setStarted(false)}><Icon name="chevL" size={17} /></button>
              <div style={{ flex: 1, display: "flex", gap: 4 }}>
                {flat.map((q, j) => <span key={q.id} style={{ flex: 1, height: 4, borderRadius: 99, background: saved[q.id] && saved[q.id].text ? "var(--green)" : j === i ? "var(--accent)" : "var(--line)", transition: "background .3s" }} />)}
              </div>
              <button className="icon-btn" style={{ width: 30, height: 30 }} onClick={() => onClose(false)}><Icon name="x" size={18} /></button>
            </div>

            <div className="eyebrow" style={{ marginBottom: 9 }}>{cur.group} · {i + 1} of {flat.length}</div>
            <h2 style={{ fontSize: 21, fontWeight: 740, letterSpacing: "-.02em", lineHeight: 1.25 }}>{cur.q}</h2>
            <p style={{ fontSize: 13, color: "var(--ink-4)", marginTop: 7 }}>{cur.hint}</p>

            <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={5} placeholder="Type your answer, or record a memo below…"
              style={{ width: "100%", resize: "vertical", border: "1px solid var(--line)", borderRadius: "var(--r-md)", padding: "13px 15px", fontSize: 14.5, lineHeight: 1.6, background: "var(--bg)", color: "var(--ink)", fontFamily: "inherit", outline: "none", marginTop: 16 }} />

            <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 12 }}>
              <VoiceRecorder qid={cur.id} onTranscript={(t) => setDraft((d) => d ? d + " " + t : t)} />
              <div style={{ flex: 1 }} />
              <button className="btn btn-ghost" onClick={() => commit(true)}>Skip</button>
              <button className="btn btn-primary" onClick={() => commit(false)}>{i >= flat.length - 1 ? "Finish" : (draft.trim() ? "Save & next" : "Next")}<Icon name="arrowRight" size={15} sw={2.2} /></button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

window.BrainInterview = BrainInterview;
window.MemoryComposer = MemoryComposer;
window.SaveToMemoryBtn = SaveToMemoryBtn;
