// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// chat.jsx, "Ask your agents" chat-to-instruct panel

const SUGGESTIONS = [
  "Draft follow-ups for all my warm leads",
  "Build a workflow for new website leads",
  "Which deals are most likely to close?",
  "Summarize what the agents did this week",
];

// canned agent replies keyed by keyword match
function planReply(text, agents) {
  const t = text.toLowerCase();
  if (t.includes("follow") || t.includes("warm")) return {
    agent: "nadia",
    text: "On it. I found 4 warm leads with no reply in 3+ days. I've drafted personalized follow-ups for each, referencing their last activity.",
    action: { ico: "mail", tone: "indigo", title: "Send 4 follow-up emails", body: "North Loop Cycles, Quill & Press, Hollow Pine Cabins, Cedar Street Yoga, staggered over 6 hours so they don't look automated." }
  };
  if (t.includes("workflow") || t.includes("automat") || t.includes("build")) return {
    agent: "scout",
    text: "Good call. Here's a workflow I can set up: when a new lead hits your site, I enrich it, score fit, and if it's above 80 I hand it to Nadia for outreach, pausing for your approval before anything sends.",
    action: { ico: "workflow", tone: "amber", title: "Create \u201cNew website lead\u201d workflow", body: "5 steps · Trigger → Scout enrich → Condition (fit > 80) → Nadia outreach → Your approval. Opens in the builder." }
  };
  if (t.includes("close") || t.includes("likely") || t.includes("pipeline") || t.includes("deal")) return {
    agent: "scout",
    text: "Top 3 by close probability: Riverside Plumbing ($22.1k, 78%), quote opened 6×; Lantern Bakehouse ($15.7k, 71%); Maple Grove Vet ($9.3k, re-scored to 91). I'd prioritize Riverside today.",
    action: null
  };
  if (t.includes("summar") || t.includes("week") || t.includes("report")) return {
    agent: "ledger",
    text: "This week your agents handled 1,284 tasks, saved you ~47 hours, advanced 6 deals a stage, and auto-approved 86% of routine actions. Pipeline grew 12% to $124.8k.",
    action: null
  };
  return {
    agent: "scout",
    text: "Got it, I can take that on. Want me to draft the steps and pause for your approval before anything goes out, or just handle it end-to-end?",
    action: { ico: "spark", tone: "indigo", title: "Plan this task", body: "I'll break it into steps and show you exactly what each agent will do." }
  };
}

function Bubble({ m, agents }) {
  const [done, setDone] = useState(false);
  if (m.who === "me") return (
    <div className="msg me">
      <div className="avatar m-av" style={{ background: "linear-gradient(145deg, var(--accent), var(--accent-press))" }}>JR</div>
      <div><div className="bubble">{m.text}</div></div>
    </div>
  );
  const a = agents[m.agent];
  return (
    <div className="msg agent">
      <div className="avatar m-av" style={{ background: a.color }}>{a.init}</div>
      <div>
        <div className="m-name">{a.name} · {a.role}</div>
        <div className="bubble">
          {m.typing ? <span className="typing"><i /><i /><i /></span> : m.text}
          {m.action && !m.typing && (
            <div className="action-card">
              <div className="ac-top">
                <div className="feed-ico" style={{ width: 24, height: 24, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name={m.action.ico} size={13} /></div>
                {m.action.title}
              </div>
              <div className="ac-body">{m.action.body}</div>
              {done === true ? (
                <div style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12.5, fontWeight: 600, color: "var(--green)" }}>
                  <Icon name="checkCircle" size={15} />Running, added to your activity feed
                </div>
              ) : done === "adjust" ? null : (
                <div style={{ display: "flex", gap: 7 }}>
                  <button className="btn btn-primary btn-sm" onClick={() => {
                    window.FLStore && window.FLStore.pushFeed({ agent: m.agent, ico: "checkCircle", tone: "green", html: `Executed: <b>${m.action.title}</b>`, meta: "just now · via chat" });
                    setDone(true);
                  }}><Icon name="check" size={13} sw={2.4} />Approve &amp; run</button>
                  <button className="btn btn-ghost btn-sm" onClick={() => setDone("adjust")}><Icon name="note" size={13} />Adjust</button>
                </div>
              )}
              {done === "adjust" && (
                <div style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12.5, fontWeight: 600, color: "var(--ink-3)", marginTop: 8 }}>
                  <Icon name="note" size={15} />Tell me what to change below and I'll redo it.
                </div>
              )}
            </div>
          )}
        </div>
        {!m.typing && window.SaveToMemoryBtn && (
          <div className="msg-actions">
            <SaveToMemoryBtn small label="Save to memory" source={"Agent chat · " + a.name} agent={m.agent} getText={() => m.text} />
          </div>
        )}
      </div>
    </div>
  );
}

function AgentChat({ open, agents, onClose }) {
  const [msgs, setMsgs] = useState([
    { who: "agent", agent: "scout", text: "Morning, Jordan 👋 Your team's been busy, pipeline is up 12% this week. Tell me what you'd like done and I'll route it to the right agent." },
  ]);
  const [draft, setDraft] = useState("");
  const bodyRef = useRef(null);

  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [msgs, open]);

  useEffect(() => {
    if (!open) return;
    const k = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [open, onClose]);

  const send = (text) => {
    const body = (text || draft).trim();
    if (!body) return;
    setDraft("");
    const plan = planReply(body, agents);
    setMsgs((m) => [...m, { who: "me", text: body }, { who: "agent", agent: plan.agent, typing: true }]);
    (async () => {
      const finalText = plan.action ? plan.text : await askClaude(bizContext() + "\n\nUser: " + body + "\n\nReply as the agent, first person, warm and brief:", plan.text);
      setMsgs((m) => {
        const copy = [...m];
        copy[copy.length - 1] = { who: "agent", agent: plan.agent, text: finalText, action: plan.action };
        return copy;
      });
    })();
  };

  return (
    <>
      <div className={"scrim" + (open ? " show" : "")} style={{ pointerEvents: open ? "auto" : "none" }} onClick={onClose} />
      <div className={"chat" + (open ? " show" : "")}>
        <div className="chat-head">
          <div className="av-stack">
            {Object.values(agents).slice(0, 3).map((a) => (
              <div key={a.id} className="avatar" style={{ background: a.color }}>{a.init}</div>
            ))}
          </div>
          <div style={{ flex: 1 }}>
            <b style={{ fontSize: 14.5, fontWeight: 700, display: "flex", alignItems: "center", gap: 7 }}>Ask your agents <span className="live-dot" style={{ width: 6, height: 6 }} /></b>
            <span style={{ fontSize: 11.5, color: "var(--ink-3)" }}>5 agents online · instant routing</span>
          </div>
          <button className="icon-btn" onClick={onClose}><Icon name="x" size={18} /></button>
        </div>

        <div className="chat-body" ref={bodyRef}>
          {msgs.map((m, i) => <Bubble key={i} m={m} agents={agents} />)}
        </div>

        {msgs.length <= 1 && (
          <div className="chat-suggest">
            {SUGGESTIONS.map((s) => <button key={s} className="sugg" onClick={() => send(s)}>{s}</button>)}
          </div>
        )}

        <div className="chat-input">
          <textarea rows={1} value={draft} placeholder="Tell your agents what to do…"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
          <button className="chat-send" disabled={!draft.trim()} onClick={() => send()}><Icon name="send" size={17} /></button>
        </div>
      </div>
    </>
  );
}

window.AgentChat = AgentChat;
