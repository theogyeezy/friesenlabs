// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// frontline.jsx, autonomous support desk

const TStatus = {
  deflected:   { label: "Auto-resolved", cls: "green", ico: "checkCircle" },
  resolved:    { label: "Resolved", cls: "green", ico: "check" },
  drafted:     { label: "Reply drafted", cls: "indigo", ico: "note" },
  needs_human: { label: "Needs you", cls: "amber", ico: "users" },
};

function genReply(t) {
  const map = {
    "Order status": `Hi ${t.cust.split(" ")[0]}, your order #4821 shipped Wednesday and is currently in transit, arriving Friday. Here's live tracking: [link]. Sorry for the quiet stretch, carriers sometimes pause scans in transit. Anything else I can help with?`,
    "Booking": `Hi ${t.cust.split(" ")[0]}! Yes, we have same-day openings at 1:00, 2:30 and 4:00 today. Want me to grab one for you? Just reply with a time and you're booked.`,
    "Account": `Hi ${t.cust.split(" ")[0]}, I just re-sent your password reset to the email on file and it should land within a minute (check spam too). If it still doesn't arrive, I can send a one-time sign-in link instead.`,
    "Hours": `Hey! We're open Monday 9am–5pm over the holiday weekend. 🙌`,
    "Billing": `Hi ${t.cust.split(" ")[0]}, moving from Growth to Everything is one click and prorates automatically. I can switch it now and you'll only pay the difference this cycle. Want me to go ahead?`,
    "Refund": `Hi ${t.cust.split(" ")[0]}, I see the duplicate May charge and I'm sorry about that. A refund of that amount needs a teammate's sign-off, so I've queued it in Greenlight for approval and it'll process within 1–2 business days.`,
    "Returns": `Hi ${t.cust.split(" ")[0]}, so sorry your unit arrived damaged. I've logged the photos and started a replacement. Because it involves a credit, a teammate will confirm shortly.`,
  };
  return map[t.intent] || `Hi ${t.cust.split(" ")[0]}, thanks for reaching out! I'm looking into this and will have an answer for you in just a moment.`;
}

function Frontline({ agents }) {
  const { TICKET_CHANNELS, KB_GAPS, SUPPORT_STATS } = window.FL_DATA;
  const tickets = useStore((s) => s.tickets);
  const [filter, setFilter] = useState("all");
  const [selId, setSelId] = useState(null);
  const [reply, setReply] = useState("");
  const [gaps, setGaps] = useState(KB_GAPS);
  const [toast, setToast] = useState(null);
  const flToast = (msg) => { setToast(msg); setTimeout(() => setToast(null), 2600); };
  const pip = agents.pip || { name: "Pip", color: "oklch(0.6 0.13 200)", init: "🐧" };

  const open = tickets.filter((t) => t.status === "needs_human" || t.status === "drafted");
  const deflectedToday = tickets.filter((t) => t.status === "deflected").length;
  const live = tickets.filter((t) => filter === "all" ? true : filter === "open" ? (t.status === "needs_human" || t.status === "drafted") : t.status === filter);
  const sel = tickets.find((t) => t.id === selId);

  useEffect(() => { if (sel) setReply(genReply(sel)); }, [selId]);

  const deflectRate = Math.round((tickets.filter((t) => t.status === "deflected").length / tickets.length) * 100);

  return (
    <div className="screen screen-anim">
      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 7 }}>Autonomous support desk</div>
          <h2 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.03em" }}>Frontline</h2>
          <p style={{ color: "var(--ink-2)", fontSize: 14.5, marginTop: 5 }}>
            <b style={{ color: "var(--ink)" }}>{pip.init} {pip.name}</b> handles the routine so you only touch what matters. <b style={{ color: "var(--ink)" }}>{open.length}</b> need you right now.
          </p>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 9 }}>
          <button className="btn btn-ghost" onClick={() => flToast("Opening your help center…")}><Icon name="doc" size={16} />Help center</button>
          <button className="btn btn-primary" onClick={() => flToast("Chat widget snippet copied to clipboard")}><Icon name="spark" size={16} />Embed chat widget</button>
        </div>
      </div>

      {/* stats */}
      <div className="stat-grid">
        <div className="stat fade-up">
          <div className="stat-top"><div className="stat-ico" style={{ background: "var(--green-soft)", color: "oklch(0.42 0.12 152)" }}><Icon name="checkCircle" size={17} /></div><span className="stat-label">Deflection rate</span></div>
          <div className="stat-val"><CountUp value={deflectRate} />%</div>
          <div className="stat-foot"><span className="delta up"><Icon name="arrowUp" size={13} sw={2.4} />14%</span><span className="muted">resolved with no human</span></div>
        </div>
        <div className="stat fade-up">
          <div className="stat-top"><div className="stat-ico" style={{ background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="clock" size={17} /></div><span className="stat-label">Avg response</span></div>
          <div className="stat-val"><CountUp value={SUPPORT_STATS.avgResponse} format={(n) => n.toFixed(1)} />m</div>
          <div className="stat-foot"><span className="delta up"><Icon name="arrowDown" size={13} sw={2.4} />31%</span><span className="muted">faster than last week</span></div>
        </div>
        <div className="stat fade-up">
          <div className="stat-top"><div className="stat-ico" style={{ background: "var(--amber-soft)", color: "oklch(0.5 0.12 60)" }}><Icon name="spark" size={17} /></div><span className="stat-label">CSAT</span></div>
          <div className="stat-val"><CountUp value={SUPPORT_STATS.csat} format={(n) => n.toFixed(1)} /><span style={{ fontSize: 16, color: "var(--ink-3)" }}>/5</span></div>
          <div className="stat-foot"><span className="delta up"><Icon name="arrowUp" size={13} sw={2.4} />0.3</span><span className="muted">last 30 days</span></div>
        </div>
        <div className="stat fade-up">
          <div className="stat-top"><div className="stat-ico" style={{ background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="inbox" size={17} /></div><span className="stat-label">Resolved today</span></div>
          <div className="stat-val"><CountUp value={SUPPORT_STATS.resolvedToday + deflectedToday} /></div>
          <div className="stat-foot"><span className="muted">{open.length} still open</span></div>
        </div>
      </div>

      <div className="dash-grid section-gap" style={{ gridTemplateColumns: sel ? "1fr 1fr" : "1.7fr 1fr" }}>
        {/* inbox */}
        <div className="card">
          <div className="card-head">
            <h3>Shared inbox</h3>
            <div className="seg" style={{ marginLeft: "auto" }}>
              {[["all", "All"], ["open", "Needs you"], ["deflected", "Auto-resolved"]].map(([id, label]) => (
                <button key={id} className={filter === id ? "active" : ""} onClick={() => setFilter(id)}>{label}</button>
              ))}
            </div>
          </div>
          <div style={{ maxHeight: 520, overflowY: "auto" }}>
            {live.map((t) => {
              const st = TStatus[t.status]; const [cico, clabel] = TICKET_CHANNELS[t.channel];
              return (
                <div key={t.id} onClick={() => setSelId(t.id)} style={{ display: "flex", gap: 12, padding: "13px var(--pad)", borderBottom: "1px solid var(--line-2)", cursor: "pointer", background: selId === t.id ? "var(--surface-2)" : "transparent" }}>
                  <div className="avatar" style={{ background: t.color, width: 36, height: 36, fontSize: 12, flexShrink: 0 }}>{t.init}</div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <b style={{ fontSize: 13.5, fontWeight: 650 }}>{t.cust}</b>
                      <span className="chip" style={{ height: 18, padding: "0 6px", fontSize: 10.5 }}><Icon name={cico} size={10} />{clabel}</span>
                      <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>{t.ago}</span>
                    </div>
                    <p style={{ fontSize: 13, fontWeight: 600, marginTop: 3 }}>{t.subject}</p>
                    <p style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.preview}</p>
                    <div style={{ display: "flex", alignItems: "center", gap: 7, marginTop: 7 }}>
                      <span className={"chip " + st.cls} style={{ height: 20 }}><Icon name={st.ico} size={11} sw={2.2} />{st.label}</span>
                      <span style={{ fontSize: 11, color: "var(--ink-3)" }}>{t.intent}</span>
                      <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 5, fontSize: 11, color: t.conf >= 0.8 ? "var(--green)" : t.conf >= 0.65 ? "var(--amber)" : "var(--rose)", fontWeight: 600, fontFamily: "var(--mono)" }}>
                        <span style={{ width: 6, height: 6, borderRadius: 99, background: "currentColor" }} />{Math.round(t.conf * 100)}% sure
                      </span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* right: detail or knowledge */}
        {sel ? (
          <div className="card">
            <div className="card-head">
              <div className="avatar" style={{ background: sel.color, width: 30, height: 30, fontSize: 11 }}>{sel.init}</div>
              <h3>{sel.cust}</h3>
              <button className="icon-btn" style={{ marginLeft: "auto", width: 30, height: 30 }} onClick={() => setSelId(null)}><Icon name="x" size={16} /></button>
            </div>
            <div className="card-pad" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              <div>
                <div className="so-section-label">Customer wrote</div>
                <div className="approval-preview" style={{ marginBottom: 0 }}><b style={{ display: "block", marginBottom: 4 }}>{sel.subject}</b>{sel.preview}</div>
              </div>
              <div>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                  <div className="avatar" style={{ background: pip.color, width: 22, height: 22, fontSize: 10 }}>{pip.init}</div>
                  <span className="so-section-label" style={{ margin: 0 }}>{pip.name}'s draft reply</span>
                  <span className="chip green" style={{ height: 19, marginLeft: "auto" }}>{Math.round(sel.conf * 100)}% confident</span>
                </div>
                <textarea value={reply} onChange={(e) => setReply(e.target.value)} style={{ width: "100%", minHeight: 150, border: "1px solid var(--line)", borderRadius: "var(--r-md)", padding: 12, fontSize: 13, fontFamily: "var(--sans)", lineHeight: 1.55, color: "var(--ink)", background: "var(--surface-2)", outline: "none", resize: "vertical" }} />
              </div>
              {(sel.intent === "Refund" || sel.intent === "Returns") && (
                <div style={{ display: "flex", alignItems: "center", gap: 9, padding: "10px 12px", background: "var(--amber-soft)", borderRadius: "var(--r-sm)", fontSize: 12, color: "oklch(0.5 0.12 60)" }}>
                  <Icon name="shield" size={15} />This touches money, so it routes through Greenlight for approval before sending.
                </div>
              )}
              <div style={{ display: "flex", gap: 8 }}>
                <button className="btn btn-primary" style={{ flex: 1 }} onClick={() => { window.FLStore.sendTicketReply(sel.id); setSelId(null); }}><Icon name="send" size={15} />Send reply</button>
                <button className="btn btn-ghost" onClick={() => window.FLStore.escalateTicket(sel.id)}><Icon name="users" size={15} />Assign to me</button>
              </div>
            </div>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--gap)" }}>
            {/* needs-you queue */}
            <div className="card">
              <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--amber-soft)", color: "oklch(0.5 0.12 60)" }}><Icon name="users" size={15} /></div><h3>Needs your touch</h3><span className="chip amber" style={{ marginLeft: "auto" }}>{open.length}</span></div>
              <div className="card-pad" style={{ display: "flex", flexDirection: "column", gap: 9 }}>
                {open.length === 0 && <p style={{ fontSize: 13, color: "var(--ink-3)", textAlign: "center", padding: 14 }}>All caught up, Pip's got it.</p>}
                {open.map((t) => (
                  <button key={t.id} onClick={() => setSelId(t.id)} style={{ display: "flex", alignItems: "center", gap: 10, padding: 11, borderRadius: "var(--r-sm)", border: "1px solid var(--line)", background: "var(--surface)", textAlign: "left", cursor: "pointer" }}>
                    <div className="avatar" style={{ background: t.color, width: 30, height: 30, fontSize: 11 }}>{t.init}</div>
                    <div style={{ flex: 1, minWidth: 0 }}><b style={{ fontSize: 12.5, fontWeight: 650, display: "block" }}>{t.subject}</b><span style={{ fontSize: 11, color: "var(--ink-3)" }}>{t.cust} · {t.intent}</span></div>
                    <Icon name="chevR" size={15} style={{ color: "var(--ink-4)" }} />
                  </button>
                ))}
              </div>
            </div>
            {/* knowledge gaps */}
            <div className="card">
              <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="doc" size={15} /></div><h3>Knowledge gaps</h3><span className="sub" style={{ marginLeft: "auto" }}>Pip flagged</span></div>
              <div className="card-pad" style={{ display: "flex", flexDirection: "column", gap: 9 }}>
                <p style={{ fontSize: 12, color: "var(--ink-3)", lineHeight: 1.45, marginBottom: 2 }}>Questions customers ask that aren't in your help center yet. Add an answer and Pip handles them next time.</p>
                {gaps.map((g) => (
                  <div key={g.q} style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 12px", borderRadius: "var(--r-sm)", background: "var(--surface-2)" }}>
                    <div style={{ flex: 1 }}><b style={{ fontSize: 12.5, fontWeight: 600 }}>{g.q}</b><span style={{ fontSize: 11, color: "var(--ink-4)", display: "block", marginTop: 1 }}>asked {g.asks}× this week</span></div>
                    <button className="btn btn-soft btn-sm" onClick={() => { setGaps((gs) => gs.filter((x) => x.q !== g.q)); flToast("Added to your help center, Pip will handle it next time"); }}><Icon name="plus" size={12} sw={2.2} />Add</button>
                  </div>
                ))}
                {gaps.length === 0 && (
                  <div className="empty-state" style={{ padding: "24px 12px" }}>
                    <div className="es-ico" style={{ width: 42, height: 42 }}><Icon name="check" size={20} sw={2.2} /></div>
                    <h4>No gaps right now</h4>
                    <p>Pip has an answer for everything customers are asking.</p>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
      {toast && (
        <div style={{ position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)", zIndex: 70, background: "var(--ink)", color: "var(--bg)", borderRadius: "var(--r-md)", padding: "12px 18px", display: "flex", alignItems: "center", gap: 10, boxShadow: "var(--shadow-xl)", animation: "feed-in .3s both", maxWidth: "90vw" }}>
          <Icon name="checkCircle" size={18} /><span style={{ fontSize: 13.5, fontWeight: 600 }}>{toast}</span>
        </div>
      )}
    </div>
  );
}

window.Frontline = Frontline;
