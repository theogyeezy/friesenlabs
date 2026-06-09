// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// agents.jsx, Agents console: roster + autonomy + guardrails + activity

function tlNodeA(tone, name) {
  const map = {
    indigo: ["var(--accent-soft)", "var(--accent-ink)"],
    amber:  ["var(--amber-soft)", "oklch(0.5 0.12 60)"],
    green:  ["var(--green-soft)", "oklch(0.42 0.12 152)"],
  };
  const [bg, fg] = map[tone] || map.indigo;
  return <div className="tl-node" style={{ background: bg, color: fg }}><Icon name={name} size={13} /></div>;
}

function AgentsConsole({ agents }) {
  const { AGENT_CFG, AUTONOMY_LEVELS, INTEGRATIONS, AGENT_FACES } = window.FL_DATA;
  const agentList = Object.values(agents);
  const agFlags = useStore((s) => s.productFlags);
  const fStudio = window.FLflag(agFlags, "agents", "studio", true);
  const fMarket = window.FLflag(agFlags, "agents", "market", true);
  const [cfg, setCfg] = useState(() => JSON.parse(JSON.stringify(AGENT_CFG)));
  const [selId, setSelId] = useState("scout");
  const [editing, setEditing] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [hire, setHire] = useState(false);
  const [studio, setStudio] = useState(false);
  const [hName, setHName] = useState("");
  const [hRole, setHRole] = useState("Outreach");
  const [hFace, setHFace] = useState("🦊");
  const [agToast, setAgToast] = useState(null);
  const [skillPick, setSkillPick] = useState(false);
  const allSkills = useStore((s) => s.skills);
  const agNote = (m) => { setAgToast(m); setTimeout(() => setAgToast(null), 2400); };

  const intgMap = {}; INTEGRATIONS.forEach((i) => (intgMap[i.id] = i));
  const DEFAULT_CFG = { status: "active", autonomy: 1, tasks: 0, success: 100, hours: 0, trend: [1, 1, 1, 1, 1, 1, 1], tools: [], skills: ["Newly hired, set me up below"], guardrails: [{ id: "g1", label: "Always ask before first contact", on: true }], activity: [] };
  const a = agents[selId] || agentList[0];
  const c = (a && cfg[a.id]) || DEFAULT_CFG;
  const aSkills = (a && a.skills) || c.skills || [];
  const installed = allSkills.filter((s) => s.installed);
  const setSkills = (next) => FLStore.updateAgent(a.id, { skills: next });

  const setStatus = (id, status) => setCfg((p) => ({ ...p, [id]: { ...(p[id] || DEFAULT_CFG), status } }));
  const setAutonomy = (id, lvl) => setCfg((p) => ({ ...p, [id]: { ...(p[id] || DEFAULT_CFG), autonomy: lvl } }));
  const toggleGuard = (id, gid) => setCfg((p) => { const cur = p[id] || DEFAULT_CFG; return { ...p, [id]: { ...cur, guardrails: cur.guardrails.map((g) => g.id === gid ? { ...g, on: !g.on } : g) } }; });

  const activeCount = agentList.filter((ag) => (cfg[ag.id] || DEFAULT_CFG).status === "active").length;
  const totalTasks = agentList.reduce((s, ag) => s + (cfg[ag.id] ? cfg[ag.id].tasks : 0), 0);

  return (
    <div className="agents">
      <div className="agents-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 7 }}>{activeCount} active · {totalTasks} tasks today</div>
          <h2 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.03em" }}>Agents</h2>
          <p style={{ color: "var(--ink-2)", fontSize: 14.5, marginTop: 5 }}>Your always-on team. Set how much each one can do on its own.</p>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 9 }}>
          <button className="btn btn-ghost" onClick={() => setHire(true)} style={{ display: fMarket ? undefined : "none" }}><Icon name="users" size={16} />Hire an agent</button>
          <button className="btn btn-primary" onClick={() => setStudio(true)} style={{ display: fStudio ? undefined : "none" }}><Icon name="spark" size={16} />Build in Studio</button>
        </div>
      </div>

      <AgentMarket open={hire} onClose={() => setHire(false)} />
      <AgentStudio open={studio} agents={agents} onClose={() => setStudio(false)} />

      <div className="agents-main">
        {/* roster rail */}
        <div className="agents-rail">
          {agentList.map((ag) => {
            const ac = cfg[ag.id] || DEFAULT_CFG;
            return (
              <div key={ag.id} className={"agent-li" + (selId === ag.id ? " sel" : "")} onClick={() => { setSelId(ag.id); setEditing(false); }}>
                <div className="avatar" style={{ background: ag.color, width: 36, height: 36, fontSize: 12 }}>{ag.init}</div>
                <div className="info">
                  <b>{ag.name} <span className={"st-dot " + ac.status} /></b>
                  <span>{ag.role}</span>
                </div>
                <div className="li-tasks">{ac.tasks}<br />tasks</div>
              </div>
            );
          })}
        </div>

        {/* detail */}
        <div className="agent-detail" key={selId}>
          <div className="screen-anim">
            <div className="ad-hero">
              <div className="avatar" style={{ background: a.color }}>{a.init}</div>
              <div style={{ flex: 1 }}>
                {editing ? (
                  <div style={{ maxWidth: 360 }}>
                    <input className="gl-edit" style={{ minHeight: 0, height: 40, padding: "0 13px", fontSize: 18, fontWeight: 700, marginBottom: 10 }}
                      value={draftName} autoFocus onChange={(e) => setDraftName(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter") { FLStore.updateAgent(selId, { name: draftName.trim() || a.name }); setEditing(false); } }} />
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 7 }}>
                      {AGENT_FACES.map((f) => (
                        <button key={f} onClick={() => FLStore.updateAgent(selId, { init: f })}
                          style={{ width: 36, height: 36, borderRadius: 9, fontSize: 19, display: "grid", placeItems: "center",
                            border: "1.5px solid " + (a.init === f ? "var(--accent)" : "var(--line)"),
                            background: a.init === f ? "var(--accent-softer)" : "var(--surface)" }}>{f}</button>
                      ))}
                    </div>
                  </div>
                ) : (
                  <>
                    <h2>{a.name}</h2>
                    <p>{a.role} agent</p>
                  </>
                )}
              </div>
              {editing ? (
                <button className="btn btn-primary" onClick={() => { FLStore.updateAgent(selId, { name: draftName.trim() || a.name }); setEditing(false); }}>
                  <Icon name="check" size={16} sw={2.2} />Done
                </button>
              ) : (
                <>
                  <button className="btn btn-ghost" onClick={() => { setDraftName(a.name); setEditing(true); }}><Icon name="sliders" size={16} />Edit</button>
                  <button className={"btn " + (c.status === "active" ? "btn-ghost" : "btn-primary")} onClick={() => setStatus(selId, c.status === "active" ? "paused" : "active")}>
                    <Icon name={c.status === "active" ? "pause" : "play"} size={16} />{c.status === "active" ? "Pause" : "Activate"}
                  </button>
                  <button className="btn btn-ghost btn-icon-only" title="Delete agent" onClick={() => {
                    if (agentList.length <= 1) { agNote("Keep at least one agent"); return; }
                    const others = agentList.filter((x) => x.id !== selId);
                    FLStore.pushFeed({ agent: others[0].id, ico: "x", tone: "rose", html: `Removed agent <b>${a.name}</b>`, meta: "just now" });
                    FLStore.removeAgent(selId);
                    setSelId(others[0].id); setEditing(false);
                  }}><Icon name="x" size={16} sw={2.2} style={{ color: "var(--rose)" }} /></button>
                </>
              )}
            </div>

            {/* stats */}
            <div className="ad-stats">
              <div className="ad-stat"><div className="v"><CountUp value={c.tasks} /></div><div className="l">Tasks today</div></div>
              <div className="ad-stat"><div className="v"><CountUp value={c.success} />%</div><div className="l">Success rate</div></div>
              <div className="ad-stat"><div className="v"><CountUp value={c.hours} />h</div><div className="l">Hours saved / wk</div></div>
              <div className="ad-stat">
                <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between" }}>
                  <div className="v" style={{ fontSize: 15, marginBottom: 4 }}>{AUTONOMY_LEVELS[c.autonomy].label}</div>
                  <Sparkline data={c.trend} color={a.color} w={64} h={26} />
                </div>
                <div className="l">Autonomy · 7-day trend</div>
              </div>
            </div>

            {/* autonomy */}
            <div className="ad-section">
              <div className="ad-sec-label"><Icon name="gauge" size={14} />Autonomy level</div>
              <div className="auto-seg">
                {AUTONOMY_LEVELS.map((lv) => (
                  <button key={lv.id} className={"auto-opt" + (c.autonomy === lv.id ? " sel" : "")} onClick={() => setAutonomy(selId, lv.id)}>
                    <span className="ao-step">Level {lv.id}</span>
                    <b>{lv.label}</b>
                  </button>
                ))}
              </div>
              <div className="auto-desc"><b style={{ fontWeight: 650 }}>{AUTONOMY_LEVELS[c.autonomy].label}.</b> {AUTONOMY_LEVELS[c.autonomy].desc}</div>
            </div>

            {/* guardrails */}
            <div className="ad-section">
              <div className="ad-sec-label"><Icon name="shield" size={14} />Guardrails</div>
              {c.guardrails.map((g) => (
                <div className="guard-row" key={g.id}>
                  <div className="g-ico"><Icon name="check" size={15} sw={2.4} /></div>
                  <span className="g-label">{g.label}</span>
                  <div className={"tog" + (g.on ? " on" : "")} onClick={() => toggleGuard(selId, g.id)} />
                </div>
              ))}
            </div>

            {/* tools + skills */}
            <div className="rg2">
              <div className="ad-section">
                <div className="ad-sec-label"><Icon name="plug" size={14} />Connected tools</div>
                <div className="tool-chips">
                  {c.tools.map((tid) => { const it = intgMap[tid]; if (!it) return null; return (
                    <span className="tool-chip" key={tid}>
                      <span className="tc-mark" style={{ background: it.color, color: it.dark ? "#1a1a1a" : "#fff" }}>{it.letter}</span>{it.name}
                    </span>
                  ); })}
                  <span className="tool-chip add" style={{ cursor: "pointer" }} onClick={() => agNote(`Connect a tool for ${a.name} in Switchboard`)}><Icon name="plus" size={13} sw={2.2} />Add tool</span>
                </div>
              </div>
              <div className="ad-section">
                <div className="ad-sec-label"><Icon name="spark" size={14} />Skills · what {a.name} can do</div>
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {aSkills.map((s, i) => (
                    <div key={i} style={{ display: "flex", alignItems: "center", gap: 9, fontSize: 13.5 }}>
                      <Icon name="check" size={15} sw={2.4} style={{ color: a.color, flexShrink: 0 }} />
                      <span style={{ flex: 1 }}>{s}</span>
                      <button className="icon-btn" style={{ width: 24, height: 24 }} title="Remove skill" onClick={() => setSkills(aSkills.filter((x) => x !== s))}><Icon name="x" size={13} /></button>
                    </div>
                  ))}
                  {aSkills.length === 0 && <p style={{ fontSize: 12.5, color: "var(--ink-4)" }}>No skills yet. Add one below.</p>}
                  <div style={{ position: "relative" }}>
                    <button className="btn btn-soft btn-sm" style={{ marginTop: 4 }} onClick={() => setSkillPick((v) => !v)}><Icon name="plus" size={13} sw={2.2} />Add skill</button>
                    {skillPick && (
                      <>
                        <div style={{ position: "fixed", inset: 0, zIndex: 30 }} onClick={() => setSkillPick(false)} />
                        <div style={{ position: "absolute", bottom: 40, left: 0, width: 260, maxHeight: 280, overflowY: "auto", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-md)", boxShadow: "var(--shadow-lg)", zIndex: 31, padding: 6, animation: "feed-in .15s both" }}>
                          {installed.filter((s) => !aSkills.includes(s.name)).map((s) => (
                            <div key={s.id} className="wf-menu-act" onClick={() => { setSkills([...aSkills, s.name]); setSkillPick(false); agNote(`Added ${s.name} to ${a.name}`); }}>
                              <Icon name={s.ico} size={14} style={{ color: "var(--ink-3)" }} /><span style={{ flex: 1 }}>{s.name}</span>
                            </div>
                          ))}
                          {installed.filter((s) => !aSkills.includes(s.name)).length === 0 && <div style={{ padding: "10px 12px", fontSize: 12, color: "var(--ink-4)" }}>All installed skills added. Get more in the Marketplace.</div>}
                          <div style={{ borderTop: "1px solid var(--line-2)", margin: "5px 0" }} />
                          <div className="wf-menu-act" style={{ color: "var(--accent-ink)" }} onClick={() => { setSkillPick(false); agNote("Browse the Marketplace to install more skills"); }}><Icon name="puzzle" size={14} />Get more skills…</div>
                        </div>
                      </>
                    )}
                  </div>
                </div>
              </div>
            </div>

            {/* activity */}
            <div className="ad-section" style={{ marginTop: "var(--gap)" }}>
              <div className="ad-sec-label"><Icon name="clock" size={14} />Recent activity</div>
              <div className="timeline">
                {c.activity.map((item, i) => (
                  <div className="tl-item" key={i}>
                    <div className="tl-rail">{tlNodeA(item.tone, item.ico)}<div className="tl-line" /></div>
                    <div className="tl-content">
                      <p><b style={{ fontWeight: 650 }}>{item.who}</b> {item.txt}</p>
                      <div className="tl-time">{item.t}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* credit / cost usage */}
            <div className="ad-section" style={{ marginTop: "var(--gap)" }}>
              <div className="ad-sec-label"><Icon name="bolt" size={14} />Credit usage · this month</div>
              {(() => { const credits = Math.round((c.tasks || 40) * 3.2 + (a.id ? a.id.length * 17 : 30)); const cost = (credits * 0.012).toFixed(2); const cap = 2000; return (
                <>
                  <div style={{ display: "flex", gap: 16, marginBottom: 12 }}>
                    <div><div style={{ fontSize: 22, fontWeight: 770, letterSpacing: "-.02em" }}>{credits.toLocaleString()}</div><div style={{ fontSize: 11, color: "var(--ink-4)" }}>credits used</div></div>
                    <div><div style={{ fontSize: 22, fontWeight: 770, letterSpacing: "-.02em" }}>${cost}</div><div style={{ fontSize: 11, color: "var(--ink-4)" }}>est. cost</div></div>
                    <div><div style={{ fontSize: 22, fontWeight: 770, letterSpacing: "-.02em", color: "var(--green)" }}>{c.tasks || 0}</div><div style={{ fontSize: 11, color: "var(--ink-4)" }}>tasks done</div></div>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11.5, color: "var(--ink-4)", marginBottom: 5 }}><span>of {cap.toLocaleString()} credit budget</span><span style={{ fontFamily: "var(--mono)" }}>{Math.round(credits / cap * 100)}%</span></div>
                  <div className="meter"><span style={{ width: Math.min(100, credits / cap * 100) + "%", background: credits / cap > 0.85 ? "var(--rose)" : "var(--accent)" }} /></div>
                  <p style={{ fontSize: 11.5, color: "var(--ink-4)", marginTop: 9, display: "flex", gap: 7 }}><Icon name="trend" size={13} style={{ flexShrink: 0, marginTop: 1 }} />Most credits go to research and drafting. Tune autonomy or guardrails above to control spend.</p>
                </>
              ); })()}
            </div>
          </div>
        </div>
      </div>
      {agToast && (
        <div style={{ position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)", zIndex: 70, background: "var(--ink)", color: "var(--bg)", borderRadius: "var(--r-md)", padding: "12px 18px", display: "flex", alignItems: "center", gap: 10, boxShadow: "var(--shadow-xl)", animation: "feed-in .3s both", maxWidth: "90vw" }}>
          <Icon name="check" size={18} sw={2.4} /><span style={{ fontSize: 13.5, fontWeight: 600 }}>{agToast}</span>
        </div>
      )}
    </div>
  );
}

window.AgentsConsole = AgentsConsole;
