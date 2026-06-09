// @ts-nocheck
import React from "react";
import "../globals";
import { SafeHtml } from "../lib/SafeHtml";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// security.jsx, Security & control center: kill switch, autonomy, guardrails, access, audit

const SEC_MODES = [
  { id: "auto",   label: "Live", desc: "Agents are working autonomously within your guardrails. Risky actions still go to Greenlight for approval.", tone: "green", ico: "bolt" },
  { id: "semi",   label: "Analyze only",  desc: "Agents keep reading and analyzing your data and prepare drafts, but execute nothing until you approve it.", tone: "amber", ico: "inbox" },
  { id: "paused", label: "Kill switch",     desc: "A full dead stop. Agents stop reading, analyzing and acting immediately, nothing runs until you flip it back on.", tone: "rose", ico: "pause" },
];

const GUARDRAILS = [
  { key: "approveOverCap", label: "Require approval above spend cap", desc: "Anything costing more than your cap waits for sign-off." },
  { key: "businessHours",  label: "Only act during business hours",  desc: "No autonomous actions outside 8am–6pm, Mon–Fri." },
  { key: "noBulk",         label: "Block bulk sends",                 desc: "Stop an agent from emailing more than 25 contacts at once." },
  { key: "piiRedact",      label: "Redact sensitive data (PII)",      desc: "Mask card, SSN & bank details in anything agents read or write." },
  { key: "twoPerson",      label: "Two-person approval for high-risk","desc": "Discounts, refunds & contracts need a second teammate." },
  { key: "blockExternalShare", label: "Block sharing data externally", desc: "Agents can't push customer data to unconnected tools." },
];

const ACCESS = [
  { key: "twoFA", label: "Two-factor authentication", desc: "Required for every teammate on the workspace." },
  { key: "sso", label: "Single sign-on (SSO)", desc: "Sign in through your Google or Microsoft workspace." },
  { key: "sessionTimeout", label: "Auto sign-out after 30 min idle", desc: "Protect unattended sessions." },
  { key: "ipAllowlist", label: "Restrict to approved IP addresses", desc: "Only your office / VPN ranges can sign in." },
];

function Tog({ on, onClick }) { return <div className={"tog" + (on ? " on" : "")} onClick={onClick} />; }

function Security({ agents }) {
  const sec = useStore((s) => s.security);
  const feed = useStore((s) => s.feed);
  const integrations = useStore((s) => s.integrations || []);
  const team = useStore((s) => s.team);
  const agentList = Object.values(agents);
  const pausedCount = agentList.filter((a) => sec.agentPaused[a.id]).length;
  const [tab, setTab] = useState("controls");
  const openAnoms = sec.anomalies.length;

  const TABS = [
    { id: "controls", label: "Controls", ico: "shield" },
    { id: "access", label: "Roles & access", ico: "users" },
    { id: "connections", label: "Connections", ico: "plug" },
    { id: "anomalies", label: "Monitoring", ico: "gauge", badge: openAnoms },
    { id: "audit", label: "Audit log", ico: "history" },
  ];

  return (
    <div className="screen screen-anim">
      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 7 }}>Workspace security</div>
          <h2 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.03em" }}>Security &amp; control</h2>
          <p style={{ color: "var(--ink-2)", fontSize: 14.5, marginTop: 5 }}>You're always in charge. Pause anything, set the rules, and see every action your agents take.</p>
        </div>
        <div className="chip" style={{ marginLeft: "auto", height: 30 }}>
          <span className="cdot" style={{ background: sec.mode === "paused" ? "var(--rose)" : sec.mode === "semi" ? "var(--amber)" : "var(--green)" }} />
          {sec.mode === "paused" ? "Stopped" : sec.mode === "semi" ? "Analyze only" : "Live"}
        </div>
      </div>

      <div className="seg" style={{ marginBottom: "var(--gap)", flexWrap: "wrap", width: "fit-content", maxWidth: "100%" }}>
        {TABS.map((tb) => (
          <button key={tb.id} className={tab === tb.id ? "active" : ""} onClick={() => setTab(tb.id)}>
            <Icon name={tb.ico} size={15} />{tb.label}
            {tb.badge > 0 && <span className="nav-badge amber" style={{ marginLeft: 2 }}>{tb.badge}</span>}
          </button>
        ))}
      </div>

      {tab === "controls" && <><SecControls sec={sec} agentList={agentList} /><SecGuardrails sec={sec} /></>}
      {tab === "access" && <SecAccess sec={sec} team={team} />}
      {tab === "connections" && <SecConnections sec={sec} integrations={integrations} agents={agents} />}
      {tab === "anomalies" && <SecMonitoring sec={sec} agents={agents} />}
      {tab === "audit" && <SecAudit sec={sec} feed={feed} agents={agents} />}
    </div>
  );
}

function SecControls({ sec, agentList }) {
  const pausedCount = agentList.filter((a) => sec.agentPaused[a.id]).length;
  return (
    <>
      {/* master control */}
      <div className="card">
        <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="shield" size={16} /></div><h3>Autonomy level</h3><span className="sub" style={{ marginLeft: "auto" }}>applies to all agents at once</span></div>
        <div className="card-pad">
          <div className="rg3">
            {SEC_MODES.map((m) => {
              const on = sec.mode === m.id;
              const tone = { green: ["var(--green-soft)", "var(--green)"], amber: ["var(--amber-soft)", "var(--amber)"], rose: ["var(--rose-soft)", "var(--rose)"] }[m.tone];
              return (
                <button key={m.id} onClick={() => FLStore.setSecurityMode(m.id)} style={{ textAlign: "left", padding: 16, borderRadius: "var(--r-md)", border: "1.5px solid " + (on ? tone[1] : "var(--line)"), background: on ? tone[0] : "var(--surface)", transition: "all .15s" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 8 }}>
                    <div style={{ width: 30, height: 30, borderRadius: 8, background: on ? "var(--surface)" : "var(--surface-2)", color: tone[1], display: "grid", placeItems: "center" }}><Icon name={m.ico} size={16} /></div>
                    <b style={{ fontSize: 14, fontWeight: 700 }}>{m.label}</b>
                    {on && <Icon name="check" size={16} sw={2.6} style={{ marginLeft: "auto", color: tone[1] }} />}
                  </div>
                  <p style={{ fontSize: 12, color: "var(--ink-2)", lineHeight: 1.45 }}>{m.desc}</p>
                </button>
              );
            })}
          </div>
          {sec.mode === "paused" && (
            <div style={{ marginTop: 14, display: "flex", alignItems: "center", gap: 11, padding: "12px 14px", background: "var(--rose-soft)", borderRadius: "var(--r-md)", color: "oklch(0.48 0.14 18)" }}>
              <Icon name="pause" size={17} /><span style={{ fontSize: 13, fontWeight: 600, flex: 1 }}>Kill switch engaged. No agent will read, analyze or act until you switch back to Live.</span>
              <button className="btn btn-sm" style={{ background: "var(--surface)", color: "var(--ink)" }} onClick={() => FLStore.setSecurityMode("auto")}><Icon name="play" size={13} />Go Live</button>
            </div>
          )}
        </div>
      </div>

      {/* per-agent control */}
      <div className="card section-gap">
        <div className="card-head"><h3>Per-agent control</h3><span className="sub" style={{ marginLeft: "auto" }}>{pausedCount} paused</span></div>
        <div style={{ padding: "6px 0" }}>
          {agentList.map((a) => {
            const paused = !!sec.agentPaused[a.id];
            return (
              <div key={a.id} style={{ display: "flex", alignItems: "center", gap: 12, padding: "11px var(--pad)", borderBottom: "1px solid var(--line-2)" }}>
                <div className="avatar" style={{ background: a.color, width: 34, height: 34, fontSize: 12, opacity: paused ? .5 : 1 }}>{a.init}</div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <b style={{ fontSize: 13.5, fontWeight: 650 }}>{a.name}</b>
                  <p style={{ fontSize: 11.5, color: paused ? "var(--rose)" : "var(--ink-3)", marginTop: 1 }}>{paused ? "Paused, taking no actions" : (sec.mode === "paused" ? "Paused by kill switch" : a.role + " · active")}</p>
                </div>
                <button className={"btn btn-sm " + (paused ? "btn-primary" : "btn-ghost")} onClick={() => FLStore.toggleAgentPause(a.id)} style={{ minWidth: 92, justifyContent: "center" }}>
                  <Icon name={paused ? "play" : "pause"} size={13} />{paused ? "Resume" : "Pause"}
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}

function SecGuardrails({ sec }) {
  return (
    <>
      {/* guardrails */}
      <div className="card">
        <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="shield" size={16} /></div><h3>Guardrails</h3><span className="sub" style={{ marginLeft: "auto" }}>the rules agents can never break</span></div>
        <div className="card-pad rg2 sec-grid2">
          {GUARDRAILS.map((g) => {
            const on = sec.guardrails[g.key];
            return (
              <div key={g.key} style={{ display: "flex", gap: 12, padding: 14, borderRadius: "var(--r-md)", border: "1px solid var(--line)", background: on ? "var(--surface)" : "var(--surface-2)" }}>
                <div style={{ width: 32, height: 32, borderRadius: 8, flexShrink: 0, display: "grid", placeItems: "center", background: on ? "var(--accent-soft)" : "var(--surface)", color: on ? "var(--accent-ink)" : "var(--ink-4)" }}><Icon name={on ? "lock" : "unlock"} size={15} /></div>
                <div style={{ flex: 1 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}><b style={{ fontSize: 13, fontWeight: 650, flex: 1 }}>{g.label}</b><Tog on={on} onClick={() => FLStore.setGuardrail(g.key, !on)} /></div>
                  <p style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 4, lineHeight: 1.45 }}>{g.desc}</p>
                </div>
              </div>
            );
          })}
        </div>
        <div style={{ padding: "0 var(--pad) var(--pad)" }}>
          <div style={{ padding: 16, borderRadius: "var(--r-md)", border: "1px solid var(--line)", background: "var(--surface-2)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 9 }}>
              <b style={{ fontSize: 13, fontWeight: 650 }}>Spend approval cap</b>
              <span style={{ fontFamily: "var(--mono)", fontSize: 13, fontWeight: 700, color: "var(--accent-ink)" }}>${sec.spendCap.toLocaleString()}</span>
            </div>
            <input type="range" min="0" max="5000" step="100" value={sec.spendCap} onChange={(e) => FLStore.setSpendCap(+e.target.value)} style={{ width: "100%" }} />
            <p style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 6 }}>Actions under <b style={{ fontFamily: "var(--mono)" }}>${sec.spendCap.toLocaleString()}</b> run automatically; anything above lands in Greenlight.</p>
          </div>
        </div>
      </div>
    </>
  );
}

const ROLE_LIST = ["Owner", "Admin", "Member"];
function SecAccess({ sec, team }) {
  const { ROLE_PERMS } = window.FL_DATA;
  const counts = {}; ROLE_LIST.forEach((r) => (counts[r] = team.filter((m) => m.role === r).length));
  return (
    <>
      {/* role-based permissions */}
      <div className="card">
        <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="users" size={15} /></div><h3>Role permissions</h3><span className="sub" style={{ marginLeft: "auto" }}>least-privilege by default</span></div>
        <div style={{ overflowX: "auto" }}>
          <table className="tbl">
            <thead><tr><th>Capability</th>{ROLE_LIST.map((r) => <th key={r} style={{ textAlign: "center" }}>{r}<div style={{ fontSize: 10, color: "var(--ink-4)", fontWeight: 500, textTransform: "none", letterSpacing: 0 }}>{counts[r]} {counts[r] === 1 ? "person" : "people"}</div></th>)}</tr></thead>
            <tbody>
              {ROLE_PERMS.map((p) => (
                <tr key={p.key}>
                  <td style={{ fontWeight: 550 }}>{p.label}</td>
                  {ROLE_LIST.map((r) => {
                    const on = sec.roles[r][p.key]; const locked = r === "Owner";
                    return (
                      <td key={r} style={{ textAlign: "center" }}>
                        <button disabled={locked} onClick={() => FLStore.setRolePerm(r, p.key, !on)} title={locked ? "Owners always have full access" : ""}
                          style={{ width: 26, height: 26, borderRadius: 7, display: "inline-grid", placeItems: "center", cursor: locked ? "default" : "pointer",
                            background: on ? "var(--green-soft)" : "var(--surface-2)", color: on ? "oklch(0.42 0.12 152)" : "var(--ink-4)", opacity: locked ? .7 : 1 }}>
                          <Icon name={on ? "check" : "x"} size={14} sw={2.6} />
                        </button>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="dash-grid section-gap">
        {/* access & sign-in */}
        <div className="card">
          <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="lock" size={15} /></div><h3>Access &amp; sign-in</h3></div>
          <div style={{ padding: "6px 0" }}>
            {ACCESS.map((ac) => {
              const on = sec.access[ac.key];
              return (
                <div key={ac.key} style={{ display: "flex", gap: 12, alignItems: "center", padding: "12px var(--pad)", borderBottom: "1px solid var(--line-2)" }}>
                  <div style={{ flex: 1 }}><b style={{ fontSize: 13, fontWeight: 650 }}>{ac.label}</b><p style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 2, lineHeight: 1.4 }}>{ac.desc}</p></div>
                  <Tog on={on} onClick={() => FLStore.setAccess(ac.key, !on)} />
                </div>
              );
            })}
          </div>
        </div>

        {/* sessions & devices */}
        <div className="card">
          <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--surface-2)", color: "var(--ink-3)" }}><Icon name="gauge" size={15} /></div><h3>Active sessions</h3>
            {sec.sessions.length > 1 && <button className="btn btn-ghost btn-sm" style={{ marginLeft: "auto" }} onClick={() => FLStore.revokeAllSessions()}>Sign out all others</button>}
          </div>
          <div style={{ padding: "6px 0" }}>
            {sec.sessions.map((s) => (
              <div key={s.id} style={{ display: "flex", gap: 12, alignItems: "center", padding: "12px var(--pad)", borderBottom: "1px solid var(--line-2)" }}>
                <div style={{ width: 32, height: 32, borderRadius: 8, background: "var(--surface-2)", color: "var(--ink-3)", display: "grid", placeItems: "center", flexShrink: 0 }}><Icon name="building" size={15} /></div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <b style={{ fontSize: 13, fontWeight: 650 }}>{s.device}</b>
                  <p style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 1 }}>{s.where} · {s.ago}</p>
                </div>
                {s.current ? <span className="chip green" style={{ height: 22 }}>This device</span>
                  : <button className="btn btn-ghost btn-sm" style={{ color: "var(--rose)" }} onClick={() => FLStore.revokeSession(s.id)}>Sign out</button>}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* data & compliance */}
      <div className="card section-gap">
        <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--green-soft)", color: "oklch(0.42 0.12 152)" }}><Icon name="checkCircle" size={15} /></div><h3>Data &amp; compliance</h3></div>
        <div className="card-pad">
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 16 }}>
            {["SOC 2 Type II", "GDPR", "CCPA", "Encrypted at rest", "Encrypted in transit"].map((b) => (
              <span key={b} className="chip green" style={{ height: 26 }}><Icon name="check" size={12} sw={2.6} />{b}</span>
            ))}
          </div>
          <div className="kv" style={{ gridTemplateColumns: "130px 1fr" }}>
            <span className="k">Data residency</span><span className="v">United States</span>
            <span className="k">Your data</span><span className="v">Never used to train models</span>
          </div>
        </div>
      </div>
    </>
  );
}

function SecConnections({ sec, integrations, agents }) {
  const { INTEGRATIONS } = window.FL_DATA;
  const list = (integrations && integrations.length ? integrations : INTEGRATIONS);
  const connected = list.filter((i) => i.connected);
  const SCOPES = { read: "Read records", write: "Create & update", send: "Send on your behalf", delete: "Delete records" };
  return (
    <div className="card">
      <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="plug" size={15} /></div><h3>Connected tools &amp; permissions</h3><span className="sub" style={{ marginLeft: "auto" }}>exactly what each tool can do</span></div>
      <div className="card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {connected.length === 0 && <p style={{ fontSize: 13, color: "var(--ink-3)", textAlign: "center", padding: 20 }}>No tools connected yet. Connect from Switchboard.</p>}
        {connected.map((i) => {
          const scopes = i.id === "stripe" ? ["read", "write"] : i.id === "gmail" ? ["read", "send"] : ["read", "write", "send"];
          return (
            <div key={i.id} style={{ border: "1px solid var(--line)", borderRadius: "var(--r-md)", padding: 15 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
                <div className="intg-mark" style={{ width: 38, height: 38, fontSize: 16, background: i.color, color: i.dark ? "#1a1a1a" : "#fff", borderRadius: 9, display: "grid", placeItems: "center", fontWeight: 800, fontFamily: "var(--mono)" }}>{i.letter}</div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <b style={{ fontSize: 14, fontWeight: 680 }}>{i.name}</b>
                  <p style={{ fontSize: 11.5, color: "var(--ink-3)" }}>{(i.agents || []).map((id) => agents[id] && agents[id].name).filter(Boolean).join(", ") || "No agents"} · using this connection</p>
                </div>
                <button className="btn btn-ghost btn-sm" style={{ color: "var(--rose)" }} onClick={() => window.FLStore.toggleConnect ? window.FLStore.toggleConnect(i.id) : null}><Icon name="x" size={13} sw={2.2} />Revoke</button>
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 7 }}>
                {Object.entries(SCOPES).map(([k, label]) => {
                  const granted = scopes.includes(k);
                  return <span key={k} className={"chip" + (granted ? " green" : "")} style={{ height: 24, opacity: granted ? 1 : .55 }}><Icon name={granted ? "check" : "x"} size={11} sw={2.4} />{label}</span>;
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function SecMonitoring({ sec, agents }) {
  const SEV = { high: ["var(--rose-soft)", "var(--rose)"], med: ["var(--amber-soft)", "var(--amber)"], low: ["var(--surface-2)", "var(--ink-3)"] };
  return (
    <>
      <div className="dash-grid">
        {/* watch settings */}
        <div className="card">
          <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="gauge" size={15} /></div><h3>Anomaly detection</h3></div>
          <div style={{ padding: "6px 0" }}>
            <div style={{ display: "flex", gap: 12, alignItems: "center", padding: "12px var(--pad)", borderBottom: "1px solid var(--line-2)" }}>
              <div style={{ flex: 1 }}><b style={{ fontSize: 13, fontWeight: 650 }}>Watch for unusual behavior</b><p style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 2, lineHeight: 1.4 }}>Spikes in volume, off-hours activity, new recipients.</p></div>
              <Tog on={sec.anomalyWatch} onClick={() => FLStore.setAnomalyWatch(!sec.anomalyWatch)} />
            </div>
            <div style={{ display: "flex", gap: 12, alignItems: "center", padding: "12px var(--pad)" }}>
              <div style={{ flex: 1 }}><b style={{ fontSize: 13, fontWeight: 650 }}>Auto-pause on high severity</b><p style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 2, lineHeight: 1.4 }}>Instantly stop an agent that trips a high-risk alert.</p></div>
              <Tog on={sec.autoPause} onClick={() => FLStore.setAutoPause(!sec.autoPause)} />
            </div>
          </div>
        </div>

        {/* rate limits */}
        <div className="card">
          <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="clock" size={15} /></div><h3>Action rate limits</h3><div style={{ marginLeft: "auto" }}><Tog on={sec.rateLimits.enabled} onClick={() => FLStore.setRateLimit("enabled", !sec.rateLimits.enabled)} /></div></div>
          <div className="card-pad" style={{ opacity: sec.rateLimits.enabled ? 1 : .5, pointerEvents: sec.rateLimits.enabled ? "auto" : "none", display: "flex", flexDirection: "column", gap: 16 }}>
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}><b style={{ fontSize: 13, fontWeight: 650 }}>Max emails / hour</b><span style={{ fontFamily: "var(--mono)", fontSize: 13, fontWeight: 700, color: "var(--accent-ink)" }}>{sec.rateLimits.emailsPerHour}</span></div>
              <input type="range" min="10" max="200" step="5" value={sec.rateLimits.emailsPerHour} onChange={(e) => FLStore.setRateLimit("emailsPerHour", +e.target.value)} style={{ width: "100%" }} />
            </div>
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}><b style={{ fontSize: 13, fontWeight: 650 }}>Max actions / hour</b><span style={{ fontFamily: "var(--mono)", fontSize: 13, fontWeight: 700, color: "var(--accent-ink)" }}>{sec.rateLimits.actionsPerHour}</span></div>
              <input type="range" min="20" max="500" step="10" value={sec.rateLimits.actionsPerHour} onChange={(e) => FLStore.setRateLimit("actionsPerHour", +e.target.value)} style={{ width: "100%" }} />
            </div>
          </div>
        </div>
      </div>

      {/* anomaly list */}
      <div className="card section-gap">
        <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--amber-soft)", color: "oklch(0.5 0.12 60)" }}><Icon name="bell" size={15} /></div><h3>Flagged activity</h3><span className="sub" style={{ marginLeft: "auto" }}>{sec.anomalies.length} open</span></div>
        <div style={{ padding: "6px 0" }}>
          {sec.anomalies.length === 0 && <div style={{ textAlign: "center", padding: "26px", color: "var(--ink-3)" }}><Icon name="checkCircle" size={26} style={{ color: "var(--green)" }} /><p style={{ fontSize: 13, fontWeight: 600, marginTop: 8 }}>Nothing unusual</p><p style={{ fontSize: 12, marginTop: 2 }}>Your agents are behaving normally.</p></div>}
          {sec.anomalies.map((a) => {
            const [bg, fg] = SEV[a.sev]; const ag = agents[a.agent];
            return (
              <div key={a.id} style={{ display: "flex", gap: 12, padding: "13px var(--pad)", borderBottom: "1px solid var(--line-2)" }}>
                <div style={{ width: 34, height: 34, borderRadius: 9, background: bg, color: fg, display: "grid", placeItems: "center", flexShrink: 0 }}><Icon name="gauge" size={16} /></div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                    <b style={{ fontSize: 13.5, fontWeight: 650 }}>{a.title}</b>
                    <span className="chip" style={{ height: 19, background: bg, color: fg, border: "none" }}>{a.sev === "high" ? "High" : a.sev === "med" ? "Medium" : "Low"}</span>
                    {a.autopaused && <span className="chip rose" style={{ height: 19 }}><Icon name="pause" size={10} sw={2.4} />Auto-paused</span>}
                  </div>
                  <p style={{ fontSize: 12.5, color: "var(--ink-2)", marginTop: 3, lineHeight: 1.45 }}>{a.detail}</p>
                  <div className="feed-meta" style={{ marginTop: 3 }}>{ag ? ag.name : "System"} · {a.action} · {a.ago}</div>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {a.autopaused && <button className="btn btn-soft btn-sm" onClick={() => { FLStore.toggleAgentPause(a.agent); FLStore.resolveAnomaly(a.id); }}><Icon name="play" size={12} />Resume</button>}
                  <button className="btn btn-ghost btn-sm" onClick={() => FLStore.resolveAnomaly(a.id)}>Dismiss</button>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}

function SecAudit({ sec, feed, agents }) {
  const exportLog = () => {
    const rows = [["Actor", "Action", "When"], ...feed.map((f) => [agents[f.agent] ? agents[f.agent].name : "System", f.html.replace(/<[^>]+>/g, ""), f.meta])];
    const csv = rows.map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\n");
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" })); a.download = "friesen-audit-log.csv"; a.click();
  };
  return (
    <div className="card">
      <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--surface-2)", color: "var(--ink-3)" }}><Icon name="history" size={15} /></div><h3>Audit log</h3>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 12, color: "var(--ink-3)" }}>Retain</span>
          <select value={sec.retentionDays} onChange={(e) => FLStore.setRetention(+e.target.value)} style={{ height: 32, border: "1px solid var(--line)", borderRadius: "var(--r-sm)", padding: "0 9px", background: "var(--bg)", color: "var(--ink)", fontSize: 12.5, fontWeight: 600 }}>
            <option value={90}>90 days</option><option value={365}>1 year</option><option value={730}>2 years</option><option value={2555}>7 years</option>
          </select>
          <button className="btn btn-ghost btn-sm" onClick={exportLog}><Icon name="doc" size={14} />Export CSV</button>
        </div>
      </div>
      <div className="feed">
        {feed.map((f, i) => (
          <div className="feed-item" key={f._k || i}>
            <div className="feed-rail"><span style={{ width: 7, height: 7, borderRadius: 99, background: agents[f.agent] ? agents[f.agent].color : "var(--accent)", marginTop: 6 }} /></div>
            <div className="feed-body"><SafeHtml as="p" html={f.html} /><div className="feed-meta">{agents[f.agent] ? agents[f.agent].name : "System"} · {f.meta}</div></div>
          </div>
        ))}
      </div>
      <div style={{ padding: "12px var(--pad)", borderTop: "1px solid var(--line-2)", fontSize: 12, color: "var(--ink-4)", display: "flex", alignItems: "center", gap: 7 }}>
        <Icon name="lock" size={13} />Logs are immutable and retained for {sec.retentionDays >= 365 ? (sec.retentionDays / 365) + (sec.retentionDays === 365 ? " year" : " years") : sec.retentionDays + " days"}.
      </div>
    </div>
  );
}

window.Security = Security;
