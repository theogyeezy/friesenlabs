// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// studio.jsx — Agent Studio: a delightful, visual agent builder + skill market/builder

const STU_STEPS = [
  { id: "identity", label: "Identity", ico: "spark" },
  { id: "skills", label: "Skills", ico: "puzzle" },
  { id: "brain", label: "Brain", ico: "network" },
  { id: "guardrails", label: "Guardrails", ico: "shield" },
  { id: "launch", label: "Launch", ico: "bolt" },
];
const STU_COLORS = ["oklch(0.56 0.17 277)", "oklch(0.62 0.15 18)", "oklch(0.62 0.13 152)", "oklch(0.66 0.12 235)", "oklch(0.66 0.14 50)", "oklch(0.58 0.16 300)", "oklch(0.6 0.15 200)"];
const STU_AUTO = [
  { id: 0, label: "Suggest", desc: "Drafts everything, you send" },
  { id: 1, label: "Ask first", desc: "Acts after your approval" },
  { id: 2, label: "Autonomous", desc: "Acts within guardrails" },
];
const STU_KNOWLEDGE = ["Employee handbook", "Pricing & packages", "Product docs", "Past winning deals", "Support FAQs", "Brand voice guide"];
const SKTONE = { indigo: ["var(--accent-soft)", "var(--accent-ink)"], amber: ["var(--amber-soft)", "oklch(0.5 0.12 60)"], green: ["var(--green-soft)", "oklch(0.42 0.12 152)"], rose: ["var(--rose-soft)", "oklch(0.48 0.14 18)"] };
const tone2 = (t) => SKTONE[t] || SKTONE.indigo;

/* ---- live preview card ---- */
function AgentPreview({ a, skills }) {
  return (
    <div style={{ width: "100%", maxWidth: 320 }}>
      <div className="card" style={{ overflow: "hidden", boxShadow: "var(--shadow-lg)" }}>
        <div style={{ height: 74, background: `linear-gradient(135deg, ${a.color}, color-mix(in oklch, ${a.color} 55%, #000))`, position: "relative" }}>
          <div style={{ position: "absolute", left: 20, bottom: -26, width: 60, height: 60, borderRadius: 18, background: "var(--surface)", display: "grid", placeItems: "center", fontSize: 30, boxShadow: "var(--shadow-md)", border: "3px solid var(--surface)" }}>{a.init}</div>
        </div>
        <div style={{ padding: "34px 20px 20px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
            <b style={{ fontSize: 18, fontWeight: 760, letterSpacing: "-.02em" }}>{a.name || "Your agent"}</b>
            <span className="live-dot" style={{ width: 6, height: 6 }} />
          </div>
          <div style={{ fontSize: 12.5, color: "var(--ink-3)", fontWeight: 600, marginTop: 2 }}>{a.role || "Pick a focus"}</div>
          <p style={{ fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.5, marginTop: 10 }}>{a.line || "Describe what this agent should do, and it'll come to life here."}</p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginTop: 13 }}>
            {skills.slice(0, 5).map((s) => <span key={s.id} className="chip" style={{ height: 22, fontSize: 11 }}>{s.name}</span>)}
            {skills.length > 5 && <span className="chip" style={{ height: 22, fontSize: 11 }}>+{skills.length - 5}</span>}
            {skills.length === 0 && <span style={{ fontSize: 11.5, color: "var(--ink-4)" }}>No skills yet</span>}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 7, marginTop: 14, paddingTop: 13, borderTop: "1px solid var(--line-2)", fontSize: 11.5, color: "var(--ink-3)" }}>
            <Icon name="shield" size={13} />{STU_AUTO[a.autonomy].label} · {a.runtime.split(" ")[0]}
          </div>
        </div>
      </div>
      <p style={{ fontSize: 11, color: "var(--ink-4)", textAlign: "center", marginTop: 12, fontFamily: "var(--mono)" }}>LIVE PREVIEW</p>
    </div>
  );
}

/* ---- skill market (browse + install) ---- */
function SkillMarketModal({ onClose, selected, onToggle, onBuild }) {
  const skills = useStore((s) => s.skills);
  const { SKILL_CATS } = window.FL_DATA;
  const [cat, setCat] = useState("All");
  const [q, setQ] = useState("");
  const list = skills.filter((s) => (cat === "All" || s.cat === cat) && (!q || (s.name + s.blurb + s.author).toLowerCase().includes(q.toLowerCase())));
  return (
    <div className="cmdk-scrim show" onClick={onClose} style={{ alignItems: "center", paddingTop: 0, zIndex: 110 }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: "min(760px, 94vw)", height: "min(620px, 88vh)", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-xl)", boxShadow: "var(--shadow-xl)", display: "flex", flexDirection: "column", overflow: "hidden", animation: "onb-in .3s both" }}>
        <div className="am-head" style={{ display: "flex", alignItems: "center", gap: 12, padding: "16px 20px", borderBottom: "1px solid var(--line)" }}>
          <div className="feed-ico" style={{ width: 34, height: 34, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="puzzle" size={17} /></div>
          <div style={{ flex: 1 }}><b style={{ fontSize: 17, fontWeight: 730, letterSpacing: "-.02em" }}>Skill marketplace</b><div style={{ fontSize: 12, color: "var(--ink-3)" }}>Composable capabilities, install and add to any agent</div></div>
          <button className="btn btn-soft btn-sm" onClick={onBuild}><Icon name="plus" size={13} sw={2.2} />Build a skill</button>
          <button className="icon-btn" onClick={onClose}><Icon name="x" size={18} /></button>
        </div>
        <div style={{ padding: "14px 20px 0", display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <div className="search-trigger" style={{ flex: 1, minWidth: 180, cursor: "text" }}><Icon name="search" size={15} /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search skills…" style={{ border: "none", outline: "none", background: "none", flex: 1, fontSize: 13, color: "var(--ink)" }} /></div>
        </div>
        <div className="cat-row" style={{ padding: "12px 20px 4px" }}>
          {SKILL_CATS.map((c) => <button key={c} className={"cat-pill" + (cat === c ? " active" : "")} onClick={() => setCat(c)}>{c}</button>)}
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: 20, display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(330px, 1fr))", gap: 12 }}>
          {list.map((s) => {
            const [bg, fg] = tone2(s.tone); const on = selected.some((x) => x.id === s.id);
            return (
              <div key={s.id} className="intg-card" style={{ padding: 15 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 11, marginBottom: 9 }}>
                  <div className="feed-ico" style={{ width: 34, height: 34, background: bg, color: fg }}><Icon name={s.ico} size={16} /></div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <b style={{ fontSize: 13.5, fontWeight: 680, display: "flex", alignItems: "center", gap: 5 }}>{s.name}{s.verified && <Icon name="checkCircle" size={12} style={{ color: "var(--accent)" }} />}</b>
                    <span style={{ fontSize: 11, color: "var(--ink-4)" }}>by {s.author} · {s.installs} · ★ {s.rating}</span>
                  </div>
                  <button className={"btn btn-sm " + (on ? "btn-soft" : "btn-ghost")} onClick={() => { if (!s.installed) window.FLStore.installSkill(s.id); onToggle(s); }}>{on ? <><Icon name="check" size={13} sw={2.4} />Added</> : (s.installed ? "Add" : (s.price ? `Get · $${s.price}` : "Install & add"))}</button>
                </div>
                <p style={{ fontSize: 12, color: "var(--ink-2)", lineHeight: 1.45 }}>{s.blurb}</p>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function SkillBuilderModal({ onClose, onCreate }) {
  const [name, setName] = useState("");
  const [blurb, setBlurb] = useState("");
  const [cat, setCat] = useState("Sales");
  const [ico, setIco] = useState("spark");
  const icons = ["spark", "mail", "doc", "calendar", "search", "trend", "bell", "send", "users", "inbox"];
  return (
    <div className="cmdk-scrim show" onClick={onClose} style={{ alignItems: "center", paddingTop: 0, zIndex: 120 }}>
      <div onClick={(e) => e.stopPropagation()} className="cmdk" style={{ maxWidth: 460 }}>
        <div style={{ padding: "18px 20px", borderBottom: "1px solid var(--line)", display: "flex", alignItems: "center", gap: 11 }}>
          <div className="feed-ico" style={{ width: 32, height: 32, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="puzzle" size={16} /></div>
          <b style={{ fontSize: 16, fontWeight: 720, flex: 1 }}>Build a skill</b>
          <button className="icon-btn" onClick={onClose}><Icon name="x" size={18} /></button>
        </div>
        <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 13 }}>
          <div className="wf-field"><label>Skill name</label><input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Win-back outreach" /></div>
          <div className="wf-field"><label>What it does</label><input value={blurb} onChange={(e) => setBlurb(e.target.value)} placeholder="One line, what the agent can now do" /></div>
          <div className="wf-field"><label>Category</label><select value={cat} onChange={(e) => setCat(e.target.value)}>{window.FL_DATA.SKILL_CATS.filter((c) => c !== "All").map((c) => <option key={c}>{c}</option>)}</select></div>
          <div className="wf-field"><label>Icon</label><div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>{icons.map((ic) => <button key={ic} onClick={() => setIco(ic)} style={{ width: 36, height: 36, borderRadius: 9, display: "grid", placeItems: "center", border: "1.5px solid " + (ico === ic ? "var(--accent)" : "var(--line)"), background: ico === ic ? "var(--accent-softer)" : "var(--surface)", color: "var(--ink-2)" }}><Icon name={ic} size={16} /></button>)}</div></div>
          <button className="btn btn-primary" disabled={!name.trim()} onClick={() => onCreate({ name: name.trim(), blurb: blurb.trim() || "A custom skill", cat, ico, tone: "indigo", verified: false })}><Icon name="check" size={16} sw={2.2} />Create &amp; add skill</button>
          <p style={{ fontSize: 11.5, color: "var(--ink-4)", textAlign: "center" }}>Saved to your workspace, publish to the marketplace anytime.</p>
        </div>
      </div>
    </div>
  );
}

function AgentStudio({ open, agents, onClose }) {
  const [step, setStep] = useState(0);
  const [a, setA] = useState(null);
  const [picked, setPicked] = useState([]); // skill objects
  const [knowledge, setKnowledge] = useState(["Brand voice guide"]);
  const [skillMarket, setSkillMarket] = useState(false);
  const [skillBuilder, setSkillBuilder] = useState(false);
  const faces = window.FL_DATA.AGENT_FACES;
  const storeSkills = useStore((s) => s.skills);

  useEffect(() => {
    if (open) { setStep(0); setPicked([]); setKnowledge(["Brand voice guide"]);
      setA({ name: "", role: "", line: "", init: "🦊", color: STU_COLORS[0], instructions: "", autonomy: 1, runtime: "Managed (recommended)", guardCap: true, guardHours: false }); }
  }, [open]);
  useEffect(() => { if (!open) return; const k = (e) => { if (e.key === "Escape" && !skillMarket && !skillBuilder) onClose(); }; window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k); }, [open, skillMarket, skillBuilder, onClose]);

  if (!open || !a) return null;
  const set = (patch) => setA((p) => ({ ...p, ...patch }));
  const toggleSkill = (s) => setPicked((p) => p.some((x) => x.id === s.id) ? p.filter((x) => x.id !== s.id) : [...p, s]);

  const launch = () => {
    const id = window.FLStore.addAgent({ name: a.name || "New agent", role: a.role || "Generalist", init: a.init, color: a.color, instructions: a.instructions, skills: picked.map((s) => s.name), autonomy: a.autonomy, runtime: a.runtime });
    window.FLStore.pushFeed({ agent: id, ico: "spark", tone: "indigo", html: `Built <b>${a.name}</b> in Agent Studio with ${picked.length} skills`, meta: "just now" });
    if (window.confettiBurst && window.FLStore.getState().gamifyOn) window.confettiBurst(window.innerWidth / 2, window.innerHeight / 2);
    onClose();
  };

  const canNext = step === 0 ? (a.name.trim() && a.role.trim()) : true;

  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 100, background: "var(--bg)", display: "flex", flexDirection: "column", animation: "feed-in .25s both" }}>
      {/* header */}
      <div style={{ display: "flex", alignItems: "center", gap: 14, padding: "16px 22px", borderBottom: "1px solid var(--line)", flexShrink: 0 }}>
        <div className="brand-mark" style={{ width: 32, height: 32 }}><Logo size={19} /></div>
        <div style={{ flex: 1 }}><b style={{ fontSize: 16, fontWeight: 740, letterSpacing: "-.02em" }}>Agent Studio</b><div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>Design a new agent for your team</div></div>
        {/* stepper */}
        <div className="stu-steps">
          {STU_STEPS.map((s, i) => (
            <button key={s.id} className={"stu-step" + (i === step ? " active" : "") + (i < step ? " done" : "")} onClick={() => i < step && setStep(i)}>
              <span className="stu-step-dot">{i < step ? <Icon name="check" size={12} sw={3} /> : i + 1}</span>
              <span className="stu-step-label">{s.label}</span>
            </button>
          ))}
        </div>
        <button className="icon-btn" onClick={onClose}><Icon name="x" size={19} /></button>
      </div>

      {/* body: form + live preview */}
      <div className="stu-body">
        <div className="stu-form">
          {step === 0 && (
            <div className="stu-pane">
              <h2 className="stu-h">Give your agent an identity</h2>
              <p className="stu-sub">Name it, give it a face, and tell it what it's here to do.</p>
              <div className="wf-field"><label>Name</label><input autoFocus value={a.name} onChange={(e) => set({ name: e.target.value })} placeholder="e.g. Atlas" style={{ fontSize: 15, fontWeight: 600 }} /></div>
              <div className="wf-field"><label>Focus / role</label><input value={a.role} onChange={(e) => set({ role: e.target.value })} placeholder="e.g. Outbound sales" /></div>
              <div className="wf-field"><label>One-liner</label><input value={a.line} onChange={(e) => set({ line: e.target.value })} placeholder="What it does in a sentence" /></div>
              <div className="wf-field"><label>Avatar</label><div style={{ display: "flex", flexWrap: "wrap", gap: 7 }}>{faces.map((f) => <button key={f} onClick={() => set({ init: f })} className="stu-face" style={{ borderColor: a.init === f ? "var(--accent)" : "var(--line)", background: a.init === f ? "var(--accent-softer)" : "var(--surface)" }}>{f}</button>)}</div></div>
              <div className="wf-field"><label>Accent</label><div style={{ display: "flex", gap: 9 }}>{STU_COLORS.map((c) => <button key={c} onClick={() => set({ color: c })} style={{ width: 32, height: 32, borderRadius: 9, background: c, boxShadow: a.color === c ? "0 0 0 2px var(--surface), 0 0 0 4px " + c : "var(--shadow-sm)" }} />)}</div></div>
            </div>
          )}

          {step === 1 && (
            <div className="stu-pane">
              <h2 className="stu-h">Give it skills</h2>
              <p className="stu-sub">Skills are capabilities your agent can use. Mix and match, or build your own.</p>
              <div style={{ display: "flex", gap: 9, marginBottom: 16 }}>
                <button className="btn btn-primary btn-sm" onClick={() => setSkillMarket(true)}><Icon name="puzzle" size={14} />Browse skill market</button>
                <button className="btn btn-ghost btn-sm" onClick={() => setSkillBuilder(true)}><Icon name="plus" size={14} sw={2.2} />Build a skill</button>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 10 }}>
                {storeSkills.slice(0, 10).map((s) => {
                  const [bg, fg] = tone2(s.tone); const on = picked.some((x) => x.id === s.id);
                  return (
                    <button key={s.id} onClick={() => toggleSkill(s)} className="stu-skill" style={{ borderColor: on ? "var(--accent)" : "var(--line)", background: on ? "var(--accent-softer)" : "var(--surface)" }}>
                      <div className="feed-ico" style={{ width: 30, height: 30, background: bg, color: fg, flexShrink: 0 }}><Icon name={s.ico} size={15} /></div>
                      <div style={{ minWidth: 0, flex: 1, textAlign: "left" }}><b style={{ fontSize: 12.5, fontWeight: 650, display: "block" }}>{s.name}</b><span style={{ fontSize: 11, color: "var(--ink-4)" }}>{s.cat}</span></div>
                      {on && <Icon name="checkCircle" size={16} style={{ color: "var(--accent)" }} />}
                    </button>
                  );
                })}
              </div>
              <p style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 14 }}>{picked.length} skill{picked.length === 1 ? "" : "s"} selected</p>
            </div>
          )}

          {step === 2 && (
            <div className="stu-pane">
              <h2 className="stu-h">Shape its brain</h2>
              <p className="stu-sub">Write its instructions and ground it on what your business knows (via Cortex). The Managed runtime turns these into a tuned, tool-using agent automatically.</p>
              <div className="wf-field"><label>Instructions</label><textarea value={a.instructions} onChange={(e) => set({ instructions: e.target.value })} rows={5} placeholder="How should it behave? What's its tone, what should it never do?" style={{ width: "100%", resize: "vertical", border: "1px solid var(--line)", borderRadius: "var(--r-sm)", padding: "10px 12px", fontSize: 13, lineHeight: 1.55, background: "var(--bg)", color: "var(--ink)", fontFamily: "inherit", outline: "none" }} /></div>
              <button className="btn btn-ghost btn-sm" style={{ marginBottom: 16 }} onClick={async () => { const out = await askClaude(`Write a 2-3 sentence system prompt (second person) for an AI agent named ${a.name || "this agent"} whose job is ${a.role || "to help a small business"}. Plain text only.`, `You are ${a.name || "an agent"}, a ${(a.role || "helpful").toLowerCase()} agent. Be warm, concise and on-brand. Confirm before anything irreversible, and escalate sensitive actions to a human via Greenlight.`); set({ instructions: out }); }}><Icon name="spark" size={14} />Draft with AI</button>
              <div className="wf-field"><label>Knowledge (from Cortex)</label>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {STU_KNOWLEDGE.map((k) => { const on = knowledge.includes(k); return <button key={k} onClick={() => setKnowledge((p) => on ? p.filter((x) => x !== k) : [...p, k])} className="chip" style={{ cursor: "pointer", height: 28, background: on ? "var(--accent-soft)" : "var(--surface-2)", color: on ? "var(--accent-ink)" : "var(--ink-3)", border: on ? "none" : "1px solid var(--line)" }}>{on && <Icon name="check" size={11} sw={2.6} />}{k}</button>; })}
                </div>
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="stu-pane">
              <h2 className="stu-h">Set the guardrails</h2>
              <p className="stu-sub">Decide how much it can do on its own, and where it runs. You're always in control.</p>
              <div className="wf-field"><label>Autonomy</label>
                <div className="seg" style={{ width: "100%" }}>{STU_AUTO.map((lv) => <button key={lv.id} className={a.autonomy === lv.id ? "active" : ""} style={{ flex: 1, justifyContent: "center" }} onClick={() => set({ autonomy: lv.id })}>{lv.label}</button>)}</div>
                <p style={{ fontSize: 12, color: "var(--ink-4)", marginTop: 6 }}>{STU_AUTO[a.autonomy].desc}</p>
              </div>
              <div className="wf-field"><label>Safety guardrails</label>
                <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
                  <label className="stu-guard"><div className={"tog" + (a.guardCap ? " on" : "")} onClick={() => set({ guardCap: !a.guardCap })} style={{ transform: "scale(.85)" }} />Require approval over your spend cap</label>
                  <label className="stu-guard"><div className={"tog" + (a.guardHours ? " on" : "")} onClick={() => set({ guardHours: !a.guardHours })} style={{ transform: "scale(.85)" }} />Only act during business hours</label>
                </div>
              </div>
              <div className="wf-field"><label>Runtime</label><select value={a.runtime} onChange={(e) => set({ runtime: e.target.value })}>{["Managed (recommended)", "AWS Bedrock", "LangChain", "Self-hosted"].map((r) => <option key={r}>{r}</option>)}</select>
                {a.runtime.startsWith("Managed") ? (
                  <div style={{ marginTop: 10, padding: "13px 15px", background: "var(--accent-softer)", border: "1px solid var(--accent-soft)", borderRadius: "var(--r-md)" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 9 }}>
                      <Icon name="spark" size={15} style={{ color: "var(--accent-ink)" }} />
                      <b style={{ fontSize: 13, fontWeight: 700, color: "var(--accent-ink)" }}>Fully managed, optimized out of the box</b>
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                      {["Best-fit model picked per task, upgraded automatically", "Native tool-use wires your skills in with no glue code", "Grounded on your Cortex knowledge + memory", "Guardrails & Greenlight enforced at the runtime", "Nothing to host, scale or patch"].map((t) => (
                        <div key={t} style={{ display: "flex", gap: 8, fontSize: 12.5, color: "var(--accent-ink)", lineHeight: 1.4 }}><Icon name="check" size={14} sw={2.6} style={{ flexShrink: 0, marginTop: 1 }} />{t}</div>
                      ))}
                    </div>
                  </div>
                ) : (
                  <p style={{ fontSize: 12, color: "var(--ink-4)", marginTop: 6, display: "flex", gap: 7, lineHeight: 1.45 }}><Icon name="link" size={14} style={{ flexShrink: 0, marginTop: 1 }} />Bring your own runtime, you manage hosting, models and tool wiring. You can switch to Managed anytime for the optimized path.</p>
                )}
              </div>
            </div>
          )}

          {step === 4 && (
            <div className="stu-pane">
              <h2 className="stu-h">Ready to bring {a.name || "your agent"} to life? 🎉</h2>
              <p className="stu-sub">Here's the recap. Launch it and it joins your team right away.</p>
              <div className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 11 }}>
                <div className="stu-recap"><span>Focus</span><b>{a.role || "Generalist"}</b></div>
                <div className="stu-recap"><span>Skills</span><b>{picked.length ? picked.map((s) => s.name).join(", ") : "None yet"}</b></div>
                <div className="stu-recap"><span>Knowledge</span><b>{knowledge.length} sources</b></div>
                <div className="stu-recap"><span>Autonomy</span><b>{STU_AUTO[a.autonomy].label}</b></div>
                <div className="stu-recap"><span>Runtime</span><b>{a.runtime}{a.runtime.startsWith("Managed") ? " · optimized" : ""}</b></div>
              </div>
            </div>
          )}
        </div>

        {/* live preview */}
        <div className="stu-preview">
          <AgentPreview a={a} skills={picked} />
        </div>
      </div>

      {/* footer nav */}
      <div className="stu-foot">
        <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
        <div style={{ flex: 1 }} />
        {step > 0 && <button className="btn btn-ghost" onClick={() => setStep((s) => s - 1)}><Icon name="chevL" size={15} sw={2.2} />Back</button>}
        {step < STU_STEPS.length - 1
          ? <button className="btn btn-primary" disabled={!canNext} onClick={() => setStep((s) => s + 1)}>Continue<Icon name="arrowRight" size={15} sw={2.2} /></button>
          : <button className="btn btn-primary" onClick={launch}><Icon name="bolt" size={16} />Launch {a.name || "agent"}</button>}
      </div>

      {skillMarket && <SkillMarketModal onClose={() => setSkillMarket(false)} selected={picked} onToggle={toggleSkill} onBuild={() => { setSkillBuilder(true); }} />}
      {skillBuilder && <SkillBuilderModal onClose={() => setSkillBuilder(false)} onCreate={(sk) => { const id = window.FLStore.addSkill(sk); const full = { ...sk, id }; setPicked((p) => [...p, full]); setSkillBuilder(false); }} />}
    </div>
  );
}

window.AgentStudio = AgentStudio;
