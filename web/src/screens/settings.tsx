// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// settings.jsx, workspace, team, agent defaults, notifications, billing

const SECTIONS = [
  { id: "workspace", label: "Workspace", icon: "building" },
  { id: "products", label: "Products", icon: "layers" },
  { id: "team", label: "Team", icon: "users" },
  { id: "agents", label: "Agent defaults", icon: "spark" },
  { id: "notify", label: "Notifications", icon: "bell" },
  { id: "billing", label: "Plan & billing", icon: "trend" },
];

// per-product feature toggles. default:true unless noted. `range` = special control
const PRODUCT_CONFIG = [
  { id: "dashboard", name: "Command Center", icon: "gauge", tone: "indigo", wired: true,
    features: [
      { key: "range", kind: "range", label: "Default time range", desc: "Which window the dashboard opens on." },
      { key: "posture", label: "Security posture strip", desc: "Show the live security posture bar up top." },
      { key: "reps", label: "Rep performance panel", desc: "Show the CRM rep & agent leaderboard." },
      { key: "support", label: "Support desk snapshot", desc: "Show the Frontline deflection card." },
    ] },
  { id: "crm", name: "Uplift (CRM)", icon: "users", tone: "rose",
    features: [
      { key: "kanban", label: "Drag-and-drop pipeline", desc: "Let reps drag deals across stages." },
      { key: "scoring", label: "Lead fit scores", desc: "Show agent-generated fit scores on cards." },
      { key: "slideover", label: "Slide-over deal detail", desc: "Open deals in a side panel." },
      { key: "gamify", label: "Gamified selling", desc: "Points, streaks and confetti in Uplift & Sell." },
    ] },
  { id: "workflows", name: "Workflows", icon: "workflow", tone: "amber",
    features: [
      { key: "ai", label: "Build with AI", desc: "Describe a workflow and have it drafted." },
      { key: "templates", label: "Template gallery", desc: "Offer ready-made workflow starts." },
      { key: "history", label: "Run history", desc: "Log every workflow run." },
      { key: "schedule", label: "Scheduled triggers", desc: "Allow time-based workflow triggers." },
    ] },
  { id: "greenlight", name: "Greenlight", icon: "inbox", tone: "amber",
    features: [
      { key: "autoapprove", label: "Auto-approve under threshold", desc: "Low-risk actions run without you." },
      { key: "batch", label: "Batch approvals", desc: "Approve multiple items at once." },
      { key: "risk", label: "Risk badges", desc: "Flag each item's risk level." },
    ] },
  { id: "agents", name: "Agents", icon: "spark", tone: "indigo",
    features: [
      { key: "studio", label: "Agent Studio", desc: "Let users build agents in the visual studio." },
      { key: "market", label: "Skill & agent marketplace", desc: "Browse, hire and install from the market." },
      { key: "feed", label: "Live activity feed", desc: "Stream what agents are doing in real time." },
    ] },
  { id: "frontline", name: "Frontline", icon: "inbox", tone: "green",
    features: [
      { key: "deflect", label: "Auto-deflection", desc: "Answer routine tickets from your docs." },
      { key: "drafts", label: "Drafted replies", desc: "Prepare replies for human review." },
      { key: "csat", label: "CSAT tracking", desc: "Measure satisfaction on resolved tickets." },
    ] },
  { id: "cortex", name: "Cortex", icon: "network", tone: "indigo",
    features: [
      { key: "knowledge", label: "Knowledge grounding", desc: "Ground agents on your business docs." },
      { key: "models", label: "Private models", desc: "Fine-tune private models on your data." },
      { key: "flywheel", label: "Decision flywheel", desc: "Learn from every prediction-to-outcome loop." },
    ] },
];
// read a flag with its configured default
window.FLflag = (flags, pid, key, def = true) => { const k = pid + "." + key; return flags[k] === undefined ? def : flags[k]; };
const TEAM = [
  { name: "Jordan Reyes", email: "jordan@reyesco.com", role: "Owner", color: "oklch(0.56 0.17 277)", init: "JR" },
  { name: "Sam Lee", email: "sam@reyesco.com", role: "Admin", color: "oklch(0.62 0.15 18)", init: "SL" },
  { name: "Pat Kim", email: "pat@reyesco.com", role: "Member", color: "oklch(0.62 0.13 152)", init: "PK" },
];
const INVOICES = [
  ["May 2026", "$179.00", "Paid"], ["Apr 2026", "$179.00", "Paid"], ["Mar 2026", "$129.00", "Paid"],
];

function Field({ label, children }) {
  return <div className="wf-field" style={{ maxWidth: 420 }}><label>{label}</label>{children}</div>;
}
function GuardRow({ label, on, onClick }) {
  return (
    <div className="guard-row" style={{ maxWidth: 560 }}>
      <div className="g-ico"><Icon name="check" size={15} sw={2.4} /></div>
      <span className="g-label">{label}</span>
      <div className={"tog" + (on ? " on" : "")} onClick={onClick} />
    </div>
  );
}

function Settings({ agents, onNavigate }) {
  const { AUTONOMY_LEVELS, RANGES } = window.FL_DATA;
  const team = useStore((s) => s.team);
  const productFlags = useStore((s) => s.productFlags);
  const dashRange = useStore((s) => s.dashRange);
  const [openProd, setOpenProd] = useState("dashboard");
  const [sec, setSec] = useState("workspace");
  const [invite, setInvite] = useState(false);
  const [toast, setToast] = useState(false);
  const gamifyOn = useStore((s) => s.gamifyOn);
  // gamify flag mirrors the workspace-wide gamifyOn so the two stay in sync
  const flagVal = (pid, key, def) => (pid === "crm" && key === "gamify") ? gamifyOn : window.FLflag(productFlags, pid, key, def);
  const setFlag = (pid, key, val) => { if (pid === "crm" && key === "gamify") { FLStore.setGamifyOn(val); } else { FLStore.setProductFlag(pid + "." + key, val); } };
  const [autonomy, setAutonomy] = useState(2);
  const [threshold, setThreshold] = useState(1000);
  const [defaults, setDefaults] = useState({ businessHours: true, ccFirst: true, noWeekend: false });
  const [notify, setNotify] = useState({ digest: true, slack: true, pings: true, weekly: false });
  const [freq, setFreq] = useState("Hourly");

  const save = () => { setToast("Settings saved"); setTimeout(() => setToast(false), 2400); };
  const note = (m) => { setToast(m); setTimeout(() => setToast(false), 2400); };

  return (
    <div className="agents">
      <div className="agents-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 7 }}>Reyes &amp; Co. workspace</div>
          <h2 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.03em" }}>Settings</h2>
          <p style={{ color: "var(--ink-2)", fontSize: 14.5, marginTop: 5 }}>Manage your workspace, team, and how your agents behave by default.</p>
        </div>
      </div>

      <div className="agents-main">
        <div className="agents-rail">
          {SECTIONS.map((s) => (
            <div key={s.id} className={"agent-li" + (sec === s.id ? " sel" : "")} onClick={() => setSec(s.id)}>
              <div className="g-ico" style={{ width: 34, height: 34, background: sec === s.id ? "var(--accent-soft)" : "var(--surface-2)", color: sec === s.id ? "var(--accent-ink)" : "var(--ink-3)" }}><Icon name={s.icon} size={16} /></div>
              <div className="info"><b>{s.label}</b></div>
            </div>
          ))}
        </div>

        <div className="agent-detail" key={sec}>
          <div className="screen-anim" style={{ maxWidth: 720 }}>
            {sec === "workspace" && (
              <>
                <div className="ad-sec-label"><Icon name="building" size={14} />Workspace profile</div>
                <Field label="Workspace name"><input defaultValue="Reyes & Co." /></Field>
                <Field label="Industry"><select defaultValue="home"><option value="home">Home services</option><option value="retail">Retail & food</option><option value="health">Health & wellness</option><option value="pro">Professional services</option></select></Field>
                <Field label="Time zone"><select defaultValue="ct"><option value="pt">Pacific (PT)</option><option value="ct">Central (CT)</option><option value="et">Eastern (ET)</option></select></Field>
                <Field label="Website"><input defaultValue="reyesco.com" /></Field>
                <button className="btn btn-primary" style={{ marginTop: 8 }} onClick={save}><Icon name="check" size={16} sw={2.2} />Save changes</button>
              </>
            )}

            {sec === "products" && (
              <>
                <div className="ad-sec-label"><Icon name="layers" size={14} />Product configuration</div>
                <p style={{ fontSize: 13.5, color: "var(--ink-2)", margin: "0 0 18px", maxWidth: 560, lineHeight: 1.55 }}>Turn features on or off for everyone in your workspace. Command Center changes apply live; others take effect for new sessions.</p>
                {PRODUCT_CONFIG.map((p) => {
                  const tt = { indigo: ["var(--accent-soft)", "var(--accent-ink)"], amber: ["var(--amber-soft)", "oklch(0.5 0.12 60)"], green: ["var(--green-soft)", "oklch(0.42 0.12 152)"], rose: ["var(--rose-soft)", "oklch(0.48 0.14 18)"] };
                  const [bg, fg] = tt[p.tone] || tt.indigo;
                  const open = openProd === p.id;
                  const onCount = p.features.filter((f) => f.kind !== "range" && flagVal(p.id, f.key, true)).length;
                  const total = p.features.filter((f) => f.kind !== "range").length;
                  return (
                    <div key={p.id} className="prod-set" style={{ maxWidth: 620 }}>
                      <button className="prod-set-head" onClick={() => setOpenProd(open ? null : p.id)}>
                        <div className="feed-ico" style={{ width: 34, height: 34, background: bg, color: fg }}><Icon name={p.icon} size={17} /></div>
                        <div style={{ flex: 1, textAlign: "left" }}>
                          <b style={{ fontSize: 14, fontWeight: 680, display: "flex", alignItems: "center", gap: 8 }}>{p.name}{p.wired && <span className="chip" style={{ height: 18, fontSize: 9.5, padding: "0 7px", background: "var(--green-soft)", color: "oklch(0.42 0.12 152)" }}>live</span>}</b>
                          <span style={{ fontSize: 12, color: "var(--ink-4)" }}>{onCount}/{total} features on</span>
                        </div>
                        <Icon name="chevDown" size={16} style={{ color: "var(--ink-3)", transform: open ? "rotate(180deg)" : "none", transition: "transform .2s" }} />
                      </button>
                      {open && (
                        <div className="prod-set-body">
                          {p.features.map((f) => f.kind === "range" ? (
                            <div key={f.key} className="prod-feat">
                              <div className="g-label" style={{ flex: 1 }}><div style={{ fontWeight: 600 }}>{f.label}</div><div style={{ fontSize: 12, color: "var(--ink-4)", fontWeight: 400 }}>{f.desc}</div></div>
                              <div className="seg">{RANGES.map((r) => <button key={r.id} className={dashRange === r.id ? "active" : ""} onClick={() => FLStore.setDashRange(r.id)} style={{ height: 26, padding: "0 9px", fontSize: 11.5 }}>{r.label}</button>)}</div>
                            </div>
                          ) : (
                            <div key={f.key} className="prod-feat">
                              <div className="g-label" style={{ flex: 1 }}><div style={{ fontWeight: 600 }}>{f.label}</div><div style={{ fontSize: 12, color: "var(--ink-4)", fontWeight: 400 }}>{f.desc}</div></div>
                              <div className={"tog" + (flagVal(p.id, f.key, true) ? " on" : "")} onClick={() => setFlag(p.id, f.key, !flagVal(p.id, f.key, true))} />
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </>
            )}

            {sec === "team" && (
              <>
                <div className="ad-sec-label" style={{ justifyContent: "space-between" }}><span style={{ display: "flex", gap: 8, alignItems: "center" }}><Icon name="users" size={14} />Team members <span style={{ fontWeight: 500, color: "var(--ink-4)" }}>· {team.length}</span></span><button className="btn btn-soft btn-sm" onClick={() => setInvite(true)}><Icon name="plus" size={13} sw={2.2} />Add user</button></div>
                {team.map((m) => (
                  <div className="guard-row" key={m.id} style={{ maxWidth: 620 }}>
                    <div className="avatar" style={{ background: m.color, width: 34, height: 34, fontSize: 12 }}>{m.init}</div>
                    <div className="g-label" style={{ flex: 1 }}><div style={{ fontWeight: 650 }}>{m.name}</div><div style={{ fontSize: 12, color: "var(--ink-3)", fontWeight: 400 }}>{m.email || "invite pending"}</div></div>
                    <select value={m.role} disabled={m.role === "Owner"} onChange={(e) => FLStore.setMemberRole(m.id, e.target.value)} style={{ height: 34, border: "1px solid var(--line)", borderRadius: "var(--r-sm)", padding: "0 10px", background: "var(--bg)", color: "var(--ink)", fontSize: 12.5, fontWeight: 600 }}>
                      <option>Owner</option><option>Admin</option><option>Member</option>
                    </select>
                    {m.role !== "Owner" && <button className="icon-btn" style={{ width: 32, height: 32 }} title="Remove" onClick={() => FLStore.removeMember(m.id)}><Icon name="x" size={16} /></button>}
                  </div>
                ))}
                <p style={{ fontSize: 12.5, color: "var(--ink-3)", marginTop: 6, maxWidth: 560, lineHeight: 1.5 }}>People you add here can be assigned to deals in Uplift alongside your agents.</p>
              </>
            )}

            {sec === "agents" && (
              <>
                <div className="ad-sec-label"><Icon name="gauge" size={14} />Default autonomy for new agents</div>
                <div className="auto-seg" style={{ marginBottom: 11 }}>
                  {AUTONOMY_LEVELS.map((lv) => (
                    <button key={lv.id} className={"auto-opt" + (autonomy === lv.id ? " sel" : "")} onClick={() => setAutonomy(lv.id)}>
                      <span className="ao-step">Level {lv.id}</span><b>{lv.label}</b>
                    </button>
                  ))}
                </div>
                <div className="auto-desc" style={{ marginBottom: 24 }}>{AUTONOMY_LEVELS[autonomy].desc}</div>

                <div className="ad-sec-label"><Icon name="shield" size={14} />Global guardrails</div>
                <GuardRow label="Agents only act during business hours" on={defaults.businessHours} onClick={() => setDefaults((d) => ({ ...d, businessHours: !d.businessHours }))} />
                <GuardRow label="Always CC me on first contact with a new lead" on={defaults.ccFirst} onClick={() => setDefaults((d) => ({ ...d, ccFirst: !d.ccFirst }))} />
                <GuardRow label="No outreach on weekends" on={defaults.noWeekend} onClick={() => setDefaults((d) => ({ ...d, noWeekend: !d.noWeekend }))} />

                <div className="ad-sec-label" style={{ marginTop: 24 }}><Icon name="trophy" size={14} />Gamification</div>
                <GuardRow label="Reward reps with points, streaks, quests & leaderboards" on={gamifyOn} onClick={() => { FLStore.setGamifyOn(!gamifyOn); note(gamifyOn ? "Gamification turned off for the workspace" : "Gamification turned on"); }} />
                <p style={{ fontSize: 12.5, color: "var(--ink-3)", margin: "2px 0 0", maxWidth: 560, lineHeight: 1.5 }}>Turns the Sell hub, XP badge, confetti and point rewards on or off for everyone in the workspace.</p>

                <div className="ad-sec-label" style={{ marginTop: 24 }}><Icon name="sliders" size={14} />Spend approval threshold</div>
                <div style={{ maxWidth: 560 }}>
                  <input type="range" min="0" max="5000" step="100" value={threshold} onChange={(e) => setThreshold(+e.target.value)} style={{ width: "100%" }} />
                  <p style={{ fontSize: 13, color: "var(--ink-2)", marginTop: 6 }}>Actions under <b style={{ fontFamily: "var(--mono)" }}>${threshold.toLocaleString()}</b> run automatically. Anything above lands in Greenlight.</p>
                </div>
              </>
            )}

            {sec === "notify" && (
              <>
                <div className="ad-sec-label"><Icon name="bell" size={14} />How we reach you</div>
                <GuardRow label="Daily email digest of agent activity" on={notify.digest} onClick={() => setNotify((n) => ({ ...n, digest: !n.digest }))} />
                <GuardRow label="Slack alerts when something needs approval" on={notify.slack} onClick={() => setNotify((n) => ({ ...n, slack: !n.slack }))} />
                <GuardRow label="Push notifications for Greenlight items" on={notify.pings} onClick={() => setNotify((n) => ({ ...n, pings: !n.pings }))} />
                <GuardRow label="Weekly outcomes report" on={notify.weekly} onClick={() => setNotify((n) => ({ ...n, weekly: !n.weekly }))} />
                <div className="ad-sec-label" style={{ marginTop: 24 }}><Icon name="clock" size={14} />Approval reminders</div>
                <div className="seg" style={{ width: "fit-content" }}>
                  {["Realtime", "Hourly", "Daily"].map((f) => <button key={f} className={freq === f ? "active" : ""} onClick={() => setFreq(f)}>{f}</button>)}
                </div>
              </>
            )}

            {sec === "billing" && (
              <>
                <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "13px 15px", borderRadius: "var(--r-md)", border: "1px solid var(--accent-soft)", background: "var(--accent-softer)", marginBottom: 20, maxWidth: 620 }}>
                  <Icon name="trend" size={17} style={{ color: "var(--accent-ink)", flexShrink: 0 }} />
                  <div style={{ flex: 1, fontSize: 12.5, color: "var(--accent-ink)", lineHeight: 1.45 }}>This is your <b>Friesen subscription</b>. To send customer quotes &amp; invoices and collect payments, use the <b>Billing</b> product.</div>
                  <button className="btn btn-soft btn-sm" onClick={() => onNavigate && onNavigate("billing")}>Open Billing<Icon name="arrowRight" size={13} sw={2.2} /></button>
                </div>
                <div className="ad-sec-label"><Icon name="trend" size={14} />Your plan</div>
                <div className="card" style={{ padding: 18, marginBottom: 22, display: "flex", alignItems: "center", gap: 16, maxWidth: 620 }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 16, fontWeight: 720 }}>Growth Suite</div>
                    <div style={{ fontSize: 13, color: "var(--ink-3)", marginTop: 2 }}>Command Center · Uplift · Workflows · Greenlight · Agents</div>
                  </div>
                  <div style={{ textAlign: "right" }}><div style={{ fontSize: 22, fontWeight: 760, letterSpacing: "-.02em" }}>$179<span style={{ fontSize: 13, color: "var(--ink-3)", fontWeight: 500 }}>/mo</span></div></div>
                  <button className="btn btn-ghost btn-sm" onClick={() => note("Compare plans, opening pricing…")}>Change plan</button>
                </div>

                <div className="ad-sec-label"><Icon name="gauge" size={14} />Usage this cycle</div>
                <div style={{ maxWidth: 620, marginBottom: 22, display: "flex", flexDirection: "column", gap: 14 }}>
                  {[["Agent credits used", 1284, 3000], ["Active agents", 5, 10], ["Connected tools", 6, 25]].map(([l, v, max]) => (
                    <div key={l}>
                      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 5 }}><span style={{ fontSize: 12.5, fontWeight: 600 }}>{l}</span><span style={{ fontSize: 12, fontFamily: "var(--mono)", color: "var(--ink-3)" }}>{v.toLocaleString()} / {max.toLocaleString()}</span></div>
                      <div className="meter"><span style={{ width: (v / max * 100) + "%" }} /></div>
                    </div>
                  ))}
                </div>

                <div className="ad-sec-label"><Icon name="doc" size={14} />Invoices</div>
                <div className="tbl-wrap" style={{ maxWidth: 620 }}>
                  <table className="tbl"><tbody>
                    {INVOICES.map(([m, amt, st]) => (
                      <tr key={m}><td style={{ fontWeight: 600 }}>{m}</td><td className="num">{amt}</td><td><span className="chip green" style={{ height: 20 }}>{st}</span></td><td style={{ textAlign: "right" }}><button className="btn btn-ghost btn-sm" onClick={() => note(`Downloading invoice, ${m}`)}>Download</button></td></tr>
                    ))}
                  </tbody></table>
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      {toast && (
        <div style={{ position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)", zIndex: 70, background: "var(--ink)", color: "var(--bg)", borderRadius: "var(--r-md)", padding: "12px 18px", display: "flex", alignItems: "center", gap: 10, boxShadow: "var(--shadow-xl)", animation: "feed-in .3s both" }}>
          <Icon name="checkCircle" size={18} /><span style={{ fontSize: 13.5, fontWeight: 600 }}>{toast === true ? "Settings saved" : toast}</span>
        </div>
      )}

      {invite && <InviteModal onClose={() => setInvite(false)} />}
    </div>
  );
}

function InviteModal({ onClose }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("Member");
  return (
    <div className="cmdk-scrim show" onClick={onClose}>
      <div className="cmdk" style={{ maxWidth: 440 }} onClick={(e) => e.stopPropagation()}>
        <div style={{ padding: "18px 20px", borderBottom: "1px solid var(--line)", display: "flex", alignItems: "center", gap: 11 }}>
          <div className="feed-ico" style={{ width: 32, height: 32, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="users" size={16} /></div>
          <b style={{ fontSize: 16, fontWeight: 720, flex: 1 }}>Add a team member</b>
          <button className="icon-btn" onClick={onClose}><Icon name="x" size={18} /></button>
        </div>
        <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 13 }}>
          <div className="wf-field"><label>Full name</label><input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Jordan Smith" /></div>
          <div className="wf-field"><label>Work email</label><input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="jordan@company.com" /></div>
          <div className="wf-field"><label>Role</label>
            <select value={role} onChange={(e) => setRole(e.target.value)}><option>Admin</option><option>Member</option></select>
          </div>
          <p style={{ fontSize: 12, color: "var(--ink-3)", lineHeight: 1.5 }}>They'll get an invite and can be assigned to deals in Uplift right away.</p>
          <button className="btn btn-primary" disabled={!name.trim()} onClick={() => { FLStore.addMember({ name: name.trim(), email: email.trim(), role }); onClose(); }}>
            <Icon name="send" size={16} />Send invite
          </button>
        </div>
      </div>
    </div>
  );
}

window.Settings = Settings;
