// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// agent-market.jsx, Agent marketplace (hire) + Build-an-agent chat
const RUNTIMES = ["Managed (recommended)", "AWS Bedrock", "LangChain", "Self-hosted"];
const SKILL_LIBRARY = ["Read & write your CRM", "Send email", "Draft replies", "Score & qualify leads", "Generate quotes", "Book meetings", "Summarize calls", "Search the web", "Look up order status", "Issue refunds", "Update spreadsheets", "Post to Slack"];
const AUTO_LEVELS = [
  { id: 0, label: "Suggest only", desc: "Drafts everything, you send" },
  { id: 1, label: "Ask first", desc: "Acts after your approval" },
  { id: 2, label: "Autonomous", desc: "Acts within guardrails" },
];

function Stars({ r }) {
  return <span style={{ color: "var(--amber)", fontSize: 12, fontWeight: 700 }}>★ {r.toFixed(1)}</span>;
}

function MarketCard({ m, owned, onAdd }) {
  return (
    <div className="intg-card" style={{ padding: 16 }}>
      <div className="intg-top" style={{ marginBottom: 10 }}>
        <div className="intg-mark" style={{ background: m.color, fontSize: 22 }}>{m.init}</div>
        <div className="meta">
          <b style={{ display: "flex", alignItems: "center", gap: 5 }}>{m.name}{m.verified && <Icon name="checkCircle" size={13} style={{ color: "var(--accent)" }} />}</b>
          <span className="cat">{m.role}</span>
        </div>
        {m.price === 0 ? <span className="chip green" style={{ height: 20 }}>Free</span> : <span className="chip" style={{ height: 20, fontFamily: "var(--mono)" }}>${m.price}/mo</span>}
      </div>
      <div className="intg-desc" style={{ marginBottom: 12 }}>{m.blurb}</div>
      <div className="intg-foot">
        <span style={{ fontSize: 11.5, color: "var(--ink-3)" }}>by {m.author}</span>
        <span style={{ fontSize: 11.5, color: "var(--ink-4)", marginLeft: 8 }}>· {m.installs} hires</span>
        <Stars r={m.rating} />
        {owned
          ? <span className="intg-status" style={{ marginLeft: "auto" }}><Icon name="check" size={14} sw={2.4} />Hired</span>
          : <button className={"btn btn-sm " + (m.price === 0 ? "btn-primary" : "btn-soft")} style={{ marginLeft: "auto" }} onClick={() => onAdd(m)}>{m.price === 0 ? "Hire, free" : `Get · $${m.price}`}</button>}
      </div>
    </div>
  );
}

function buildSpecFallback(desc) {
  const t = desc.toLowerCase();
  const names = ["Atlas", "Nova", "Sage", "Quill", "Iris", "Pax", "Juno", "Rivet", "Echo", "Ledger"];
  const role = /invoice|pay|book|account/.test(t) ? "Bookkeeping" : /review|reput/.test(t) ? "Review requests" : /social|post/.test(t) ? "Social posts" : /follow|chase|nurtur/.test(t) ? "Follow-ups" : /quote|proposal|price/.test(t) ? "Proposals" : /book|demo|schedul|appoint/.test(t) ? "Scheduling" : /support|ticket|help/.test(t) ? "Support" : "Outreach";
  let skills = ["Read & write your CRM", "Draft replies"];
  if (/email|reach|follow|nurtur/.test(t)) skills.push("Send email");
  if (/quote|proposal|price/.test(t)) skills.push("Generate quotes");
  if (/book|demo|schedul|appoint/.test(t)) skills.push("Book meetings");
  if (/lead|qualif|score/.test(t)) skills.push("Score & qualify leads");
  if (/invoice|refund|pay/.test(t)) skills.push("Issue refunds");
  if (/support|ticket|order/.test(t)) skills.push("Look up order status");
  const instructions = `You are a ${role.toLowerCase()} agent for a small business. ${desc.trim().replace(/^./, (c) => c.toUpperCase())}. Be warm, concise and on-brand. Always confirm before anything irreversible or money-related. Escalate anything sensitive to a human via Greenlight.`;
  return { name: names[Math.floor(Math.random() * names.length)], role, line: "Handles it end-to-end and checks with you on the judgment calls.", skills: [...new Set(skills)], instructions };
}

function AgentMarket({ open, onClose }) {
  const market = useStore((s) => s.market);
  const agents = useStore((s) => s.agents);
  const allSkills = useStore((s) => s.skills);
  const [tab, setTab] = useState("hire");
  const [q, setQ] = useState("");
  const [added, setAdded] = useState({});
  const faces = window.FL_DATA.AGENT_FACES;
  const COLORS = ["oklch(0.58 0.16 300)", "oklch(0.6 0.15 200)", "oklch(0.62 0.14 130)", "oklch(0.64 0.14 40)", "oklch(0.6 0.15 350)", "oklch(0.56 0.17 277)"];

  // build chat
  const [msgs, setMsgs] = useState([{ who: "bot", text: "Tell me what you want your agent to do and I'll set it up. e.g. \u201cchase customers who haven't paid their invoice\u201d or \u201cwelcome every new client.\u201d" }]);
  const [draft, setDraft] = useState("");
  const [spec, setSpec] = useState(null);
  const bodyRef = useRef(null);
  useEffect(() => { if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight; }, [msgs, spec, tab]);
  useEffect(() => { if (!open) return; const k = (e) => { if (e.key === "Escape") onClose(); }; window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k); }, [open, onClose]);

  if (!open) return null;

  const hire = (m) => {
    const id = window.FLStore.addAgent({ name: m.name, role: m.role, init: m.init, color: m.color });
    setAdded((a) => ({ ...a, [m.id]: true }));
    window.FLStore.pushFeed({ agent: id, ico: "spark", tone: "indigo", html: `Hired <b>${m.name}</b> from the marketplace`, meta: "just now" });
  };

  const describe = async (text) => {
    const body = (text || draft).trim(); if (!body) return; setDraft("");
    setMsgs((m) => [...m, { who: "me", text: body }, { who: "bot", typing: true }]);
    let s = buildSpecFallback(body);
    try {
      const out = await askClaude(`Design an AI worker agent for a small business that does this: "${body}". Reply ONLY as JSON: {"name":"<one word>","role":"<2-3 words>","line":"<one sentence>","instructions":"<2-3 sentence system prompt in second person>","skills":["<3-6 short capability phrases>"]}`, "");
      const mt = out && out.match(/\{[\s\S]*\}/);
      if (mt) { const j = JSON.parse(mt[0]); if (j.name) s = { name: j.name, role: j.role || s.role, line: j.line || s.line, instructions: j.instructions || s.instructions, skills: Array.isArray(j.skills) && j.skills.length ? j.skills : s.skills }; }
    } catch (e) {}
    const full = { ...s, init: faces[Math.floor(Math.random() * faces.length)], color: COLORS[Math.floor(Math.random() * COLORS.length)], visibility: "private", runtime: RUNTIMES[0], autonomy: 1, guardCap: true, guardHours: false };
    setSpec(full);
    setMsgs((m) => { const c = [...m]; c[c.length - 1] = { who: "bot", text: `Meet ${full.init} ${full.name}, a ${full.role.toLowerCase()} agent. ${full.line} I've drafted its instructions, skills and guardrails below, tweak anything, then bring it to life.` }; return c; });
  };
  const toggleSkill = (sk) => setSpec((p) => ({ ...p, skills: p.skills.includes(sk) ? p.skills.filter((x) => x !== sk) : [...p.skills, sk] }));

  const create = () => {
    const id = window.FLStore.addAgent({ name: spec.name, role: spec.role, init: spec.init, color: spec.color, instructions: spec.instructions, skills: spec.skills, autonomy: spec.autonomy, runtime: spec.runtime });
    if (spec.visibility === "public") window.FLStore.addMarketListing({ id: "u" + Date.now(), name: spec.name, init: spec.init, role: spec.role, author: "You", verified: false, price: 0, installs: "new", rating: 5, color: spec.color, blurb: spec.line });
    window.FLStore.pushFeed({ agent: id, ico: "spark", tone: "indigo", html: `Built a new agent: <b>${spec.name}</b>${spec.visibility === "public" ? " · published to the marketplace" : ""}`, meta: "just now" });
    setMsgs((m) => [...m, { who: "bot", text: `🎉 ${spec.name} is live on your Agents page${spec.visibility === "public" ? " and published to the marketplace for other businesses to hire" : ""}. You can fine-tune its instructions, autonomy and guardrails anytime.` }]);
    setSpec(null);
  };

  const owned = {}; Object.values(agents).forEach((a) => { owned[a.name] = true; });
  const filtered = market.filter((m) => !q || (m.name + m.role + m.author).toLowerCase().includes(q.toLowerCase()));
  const skillList = allSkills.filter((s) => !q || (s.name + s.blurb + s.author + s.cat).toLowerCase().includes(q.toLowerCase()));

  return (
    <div className="cmdk-scrim show" onClick={onClose} style={{ alignItems: "center", paddingTop: 0 }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: "min(820px, 94vw)", height: "min(640px, 88vh)", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-xl)", boxShadow: "var(--shadow-xl)", display: "flex", flexDirection: "column", overflow: "hidden", animation: "onb-in .3s both" }}>
        <div className="am-head" style={{ display: "flex", alignItems: "center", gap: 12, padding: "16px 20px", borderBottom: "1px solid var(--line)" }}>
          <div className="feed-ico" style={{ width: 34, height: 34, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="spark" size={17} /></div>
          <div style={{ flex: 1 }}><b style={{ fontSize: 17, fontWeight: 730, letterSpacing: "-.02em" }}>Agent marketplace</b><div style={{ fontSize: 12, color: "var(--ink-3)" }}>Hire a ready-made agent, or build a robust one from scratch</div></div>
          <div className="seg">
            <button className={tab === "hire" ? "active" : ""} onClick={() => setTab("hire")}><Icon name="users" size={14} />Agents</button>
            <button className={tab === "skills" ? "active" : ""} onClick={() => setTab("skills")}><Icon name="puzzle" size={14} />Skills</button>
            <button className={tab === "build" ? "active" : ""} onClick={() => setTab("build")}><Icon name="spark" size={14} />Build</button>
          </div>
          <button className="icon-btn" onClick={onClose}><Icon name="x" size={18} /></button>
        </div>

        {tab === "skills" ? (
          <div style={{ flex: 1, overflowY: "auto", padding: 20 }}>
            <div className="search-trigger" style={{ marginBottom: 16, cursor: "text" }}>
              <Icon name="search" size={15} />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search skills…" style={{ border: "none", outline: "none", background: "none", flex: 1, fontSize: 13, color: "var(--ink)" }} />
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 12 }}>
              {market.length === 0 && <p style={{ fontSize: 13, color: "var(--ink-3)" }}>Loading skills…</p>}
              {skillList.map((s) => {
                const tt = { indigo: ["var(--accent-soft)", "var(--accent-ink)"], amber: ["var(--amber-soft)", "oklch(0.5 0.12 60)"], green: ["var(--green-soft)", "oklch(0.42 0.12 152)"], rose: ["var(--rose-soft)", "oklch(0.48 0.14 18)"] };
                const [bg, fg] = tt[s.tone] || tt.indigo;
                return (
                  <div key={s.id} className="intg-card" style={{ padding: 15 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 11, marginBottom: 9 }}>
                      <div className="feed-ico" style={{ width: 34, height: 34, background: bg, color: fg }}><Icon name={s.ico} size={16} /></div>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <b style={{ fontSize: 13.5, fontWeight: 680, display: "flex", alignItems: "center", gap: 5 }}>{s.name}{s.verified && <Icon name="checkCircle" size={12} style={{ color: "var(--accent)" }} />}</b>
                        <span style={{ fontSize: 11, color: "var(--ink-4)" }}>by {s.author} · {s.installs} · ★ {s.rating}</span>
                      </div>
                      {s.installed
                        ? <span className="chip green" style={{ height: 24 }}><Icon name="check" size={12} sw={2.4} />Installed</span>
                        : <button className={"btn btn-sm " + (s.price ? "btn-soft" : "btn-primary")} onClick={() => window.FLStore.installSkill(s.id)}>{s.price ? `Get · $${s.price}/mo` : "Install"}</button>}
                    </div>
                    <p style={{ fontSize: 12, color: "var(--ink-2)", lineHeight: 1.45 }}>{s.blurb}</p>
                  </div>
                );
              })}
            </div>
            <p style={{ fontSize: 12, color: "var(--ink-3)", textAlign: "center", marginTop: 16 }}>Add skills to an agent in <b style={{ color: "var(--ink)" }}>Build</b> or in Agent Studio.</p>
          </div>
        ) : tab === "hire" ? (
          <div style={{ flex: 1, overflowY: "auto", padding: 20 }}>
            <div className="search-trigger" style={{ marginBottom: 16, cursor: "text" }}>
              <Icon name="search" size={15} />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search the marketplace…" style={{ border: "none", outline: "none", background: "none", flex: 1, fontSize: 13, color: "var(--ink)" }} />
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 14 }}>
              {filtered.map((m) => <MarketCard key={m.id} m={m} owned={!!added[m.id] || !!owned[m.name]} onAdd={hire} />)}
            </div>
          </div>
        ) : (
          <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
            <div ref={bodyRef} style={{ flex: 1, overflowY: "auto", padding: 20, display: "flex", flexDirection: "column", gap: 13 }}>
              {msgs.map((m, i) => (
                <div key={i} className={"msg " + (m.who === "me" ? "me" : "agent")} style={{ maxWidth: "78%" }}>
                  {m.who === "bot" && <div className="avatar m-av" style={{ background: "linear-gradient(145deg, var(--accent), var(--accent-press))", width: 26, height: 26, fontSize: 12 }}>✦</div>}
                  <div className="bubble">{m.typing ? <span className="typing"><i /><i /><i /></span> : m.text}</div>
                </div>
              ))}
              {spec && (
                <div className="card" style={{ padding: 16, alignSelf: "stretch" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 11, marginBottom: 14 }}>
                    <div className="avatar" style={{ background: spec.color, width: 42, height: 42, fontSize: 20 }}>{spec.init}</div>
                    <div style={{ flex: 1 }}>
                      <input value={spec.name} onChange={(e) => setSpec({ ...spec, name: e.target.value })} style={{ fontSize: 16, fontWeight: 720, border: "none", background: "none", outline: "none", color: "var(--ink)", width: "100%" }} />
                      <div style={{ fontSize: 12.5, color: "var(--ink-3)" }}>{spec.role}</div>
                    </div>
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 14 }}>
                    {faces.slice(0, 10).map((f) => <button key={f} onClick={() => setSpec({ ...spec, init: f })} style={{ width: 30, height: 30, borderRadius: 8, fontSize: 16, display: "grid", placeItems: "center", border: "1.5px solid " + (spec.init === f ? "var(--accent)" : "var(--line)"), background: spec.init === f ? "var(--accent-softer)" : "var(--surface)" }}>{f}</button>)}
                  </div>
                  <div className="wf-field" style={{ marginBottom: 12 }}>
                    <label>Instructions <span style={{ color: "var(--ink-4)", fontWeight: 400 }}>· what it should do and how</span></label>
                    <textarea value={spec.instructions} onChange={(e) => setSpec({ ...spec, instructions: e.target.value })} rows={3} style={{ width: "100%", resize: "vertical", border: "1px solid var(--line)", borderRadius: "var(--r-sm)", padding: "9px 11px", fontSize: 12.5, lineHeight: 1.5, background: "var(--bg)", color: "var(--ink)", fontFamily: "inherit", outline: "none" }} />
                  </div>
                  <div className="wf-field" style={{ marginBottom: 12 }}>
                    <label>Skills <span style={{ color: "var(--ink-4)", fontWeight: 400 }}>· tap to add or remove</span></label>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                      {[...new Set([...spec.skills, ...SKILL_LIBRARY])].map((sk) => {
                        const on = spec.skills.includes(sk);
                        return <button key={sk} onClick={() => toggleSkill(sk)} className="chip" style={{ cursor: "pointer", height: 26, background: on ? "var(--accent-soft)" : "var(--surface-2)", color: on ? "var(--accent-ink)" : "var(--ink-3)", border: on ? "none" : "1px solid var(--line)" }}>{on && <Icon name="check" size={11} sw={2.6} />}{sk}</button>;
                      })}
                    </div>
                  </div>
                  <div className="wf-field" style={{ marginBottom: 12 }}>
                    <label>Autonomy</label>
                    <div className="seg" style={{ width: "100%" }}>
                      {AUTO_LEVELS.map((lv) => <button key={lv.id} className={spec.autonomy === lv.id ? "active" : ""} style={{ flex: 1, justifyContent: "center", flexDirection: "column", gap: 1, height: "auto", padding: "7px 4px" }} onClick={() => setSpec({ ...spec, autonomy: lv.id })} title={lv.desc}><span style={{ fontSize: 12, fontWeight: 650 }}>{lv.label}</span></button>)}
                    </div>
                    <p style={{ fontSize: 11.5, color: "var(--ink-4)", marginTop: 5 }}>{AUTO_LEVELS[spec.autonomy].desc}</p>
                  </div>
                  <div className="wf-field" style={{ marginBottom: 12 }}>
                    <label>Guardrails</label>
                    <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
                      <label style={{ display: "flex", alignItems: "center", gap: 9, fontSize: 12.5, fontWeight: 500, cursor: "pointer" }}>
                        <div className={"tog" + (spec.guardCap ? " on" : "")} onClick={() => setSpec({ ...spec, guardCap: !spec.guardCap })} style={{ transform: "scale(.85)" }} />Require approval for anything over your spend cap
                      </label>
                      <label style={{ display: "flex", alignItems: "center", gap: 9, fontSize: 12.5, fontWeight: 500, cursor: "pointer" }}>
                        <div className={"tog" + (spec.guardHours ? " on" : "")} onClick={() => setSpec({ ...spec, guardHours: !spec.guardHours })} style={{ transform: "scale(.85)" }} />Only act during business hours
                      </label>
                    </div>
                  </div>
                  <div className="wf-field" style={{ marginBottom: 12 }}>
                    <label>Visibility</label>
                    <div className="seg" style={{ width: "100%" }}>
                      <button className={spec.visibility === "private" ? "active" : ""} style={{ flex: 1, justifyContent: "center" }} onClick={() => setSpec({ ...spec, visibility: "private" })}><Icon name="shield" size={13} />Private to my workspace</button>
                      <button className={spec.visibility === "public" ? "active" : ""} style={{ flex: 1, justifyContent: "center" }} onClick={() => setSpec({ ...spec, visibility: "public" })}><Icon name="users" size={13} />Publish to marketplace</button>
                    </div>
                  </div>
                  <div className="wf-field" style={{ marginBottom: 14 }}>
                    <label>Runtime</label>
                    <select value={spec.runtime} onChange={(e) => setSpec({ ...spec, runtime: e.target.value })}>{RUNTIMES.map((r) => <option key={r}>{r}</option>)}</select>
                  </div>
                  <button className="btn btn-primary" style={{ width: "100%" }} onClick={create}><Icon name="bolt" size={16} />Bring {spec.name} to life</button>
                </div>
              )}
            </div>
            {msgs.length <= 1 && !spec && (
              <div className="chat-suggest">
                {["Chase unpaid invoices", "Welcome every new client", "Ask happy customers for reviews"].map((s) => <button key={s} className="sugg" onClick={() => describe(s)}>{s}</button>)}
              </div>
            )}
            <div className="chat-input">
              <textarea rows={1} value={draft} placeholder="Describe what your agent should do…" onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); describe(); } }} />
              <button className="chat-send" disabled={!draft.trim()} onClick={() => describe()}><Icon name="send" size={17} /></button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

window.AgentMarket = AgentMarket;
