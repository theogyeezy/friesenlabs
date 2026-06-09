// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// email.jsx — Email campaigns: build lists from your leads, send, track
// deliverability/opens/clicks, and segment smartly (by city, industry, source, or AI segments).

const EM_STATUS = { sent: ["Sent", "var(--green-soft)", "oklch(0.42 0.12 152)"], scheduled: ["Scheduled", "var(--amber-soft)", "oklch(0.5 0.12 60)"], draft: ["Draft", "var(--surface-2)", "var(--ink-4)"] };

function emAudience(deals) {
  const { EMAIL_CITIES } = window.FL_DATA;
  return deals.filter((d) => d.email).map((d) => ({
    id: d.id, name: d.person, co: d.co, email: d.email, color: d.coColor,
    city: EMAIL_CITIES[d.id % EMAIL_CITIES.length], industry: d.industry, source: d.source, heat: d.heat, stage: d.stage,
    init: (d.person || d.co).split(" ").map((w) => w[0]).slice(0, 2).join(""),
  }));
}
function uniq(arr) { return [...new Set(arr)]; }
const pct = (n, d) => d ? Math.round(n / d * 100) : 0;

function Email({ agents, onNavigate }) {
  const deals = useStore((s) => s.deals);
  const campaigns = useStore((s) => s.campaigns);
  const { EMAIL_SEED } = window.FL_DATA;
  const audience = emAudience(deals);
  const [tab, setTab] = useState("campaigns");
  const [filters, setFilters] = useState({ city: "all", industry: "all", source: "all", heat: "all" });
  const [compose, setCompose] = useState(null); // {segment, count}
  const [toast, setToast] = useState(null);
  const note = (m) => { setToast(m); setTimeout(() => setToast(null), 2600); };

  const cities = uniq(audience.map((a) => a.city));
  const industries = uniq(audience.map((a) => a.industry));
  const sources = uniq(audience.map((a) => a.source));
  const matched = audience.filter((a) =>
    (filters.city === "all" || a.city === filters.city) &&
    (filters.industry === "all" || a.industry === filters.industry) &&
    (filters.source === "all" || a.source === filters.source) &&
    (filters.heat === "all" || a.heat === filters.heat));

  // aggregate performance across sent campaigns
  const sent = campaigns.filter((c) => c.status === "sent");
  const agg = sent.reduce((a, c) => ({ sent: a.sent + c.sent, delivered: a.delivered + c.delivered, opens: a.opens + c.opens, clicks: a.clicks + c.clicks, replies: a.replies + c.replies }), { sent: 0, delivered: 0, opens: 0, clicks: 0, replies: 0 });

  const segLabel = () => {
    const parts = [];
    if (filters.city !== "all") parts.push(filters.city);
    if (filters.industry !== "all") parts.push(filters.industry);
    if (filters.source !== "all") parts.push(filters.source);
    if (filters.heat !== "all") parts.push(filters.heat + " leads");
    return parts.length ? parts.join(" · ") : "All contacts";
  };

  return (
    <div className="screen screen-anim">
      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 7 }}>Reach your customers</div>
          <h2 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.03em" }}>Email</h2>
          <p style={{ color: "var(--ink-2)", fontSize: 14.5, marginTop: 5 }}>Build lists from your leads, let an agent write and send, and watch deliverability, opens and replies.</p>
        </div>
        <div style={{ marginLeft: "auto" }}>
          <button className="btn btn-primary" onClick={() => setCompose({ segment: "All contacts", count: audience.length })}><Icon name="plus" size={16} sw={2.2} />New campaign</button>
        </div>
      </div>

      <div className="stat-grid" style={{ marginBottom: "var(--gap)" }}>
        {[["mail", "indigo", "Delivered", agg.sent ? pct(agg.delivered, agg.sent) + "%" : "—", agg.delivered + " of " + agg.sent], ["search", "amber", "Open rate", agg.delivered ? pct(agg.opens, agg.delivered) + "%" : "—", agg.opens + " opens"], ["link", "green", "Click rate", agg.opens ? pct(agg.clicks, agg.opens) + "%" : "—", agg.clicks + " clicks"], ["spark", "rose", "Replies", agg.replies, "across " + sent.length + " sends"]].map(([ic, tone, label, val, sub]) => {
          const tt = { indigo: ["var(--accent-soft)", "var(--accent-ink)"], amber: ["var(--amber-soft)", "oklch(0.5 0.12 60)"], green: ["var(--green-soft)", "oklch(0.42 0.12 152)"], rose: ["var(--rose-soft)", "oklch(0.48 0.14 18)"] }[tone];
          return <div className="stat" key={label}><div className="stat-top"><div className="stat-ico" style={{ background: tt[0], color: tt[1] }}><Icon name={ic} size={17} /></div></div><div className="stat-val" style={{ fontSize: 23 }}>{val}</div><div className="stat-label">{label}</div><div style={{ fontSize: 11, color: "var(--ink-4)" }}>{sub}</div></div>;
        })}
      </div>

      <div className="seg" style={{ marginBottom: "var(--gap)" }}>
        <button className={tab === "campaigns" ? "active" : ""} onClick={() => setTab("campaigns")}><Icon name="mail" size={15} />Campaigns</button>
        <button className={tab === "audiences" ? "active" : ""} onClick={() => setTab("audiences")}><Icon name="users" size={15} />Audiences</button>
      </div>

      {tab === "campaigns" ? (
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr><th>Campaign</th><th>Audience</th><th className="num">Sent</th><th className="num">Delivered</th><th className="num">Opens</th><th className="num">Clicks</th><th className="num">Replies</th><th>Status</th></tr></thead>
            <tbody>
              {campaigns.map((c) => { const [lbl, bg, fg] = EM_STATUS[c.status]; return (
                <tr key={c.id}>
                  <td><b style={{ fontWeight: 650 }}>{c.name}</b><div style={{ fontSize: 11, color: "var(--ink-4)" }}>{c.when}</div></td>
                  <td style={{ color: "var(--ink-3)", fontSize: 12.5 }}>{c.segment}</td>
                  <td className="num" style={{ fontFamily: "var(--mono)" }}>{c.sent || "—"}</td>
                  <td className="num">{c.sent ? <span style={{ fontWeight: 600 }}>{pct(c.delivered, c.sent)}%</span> : "—"}</td>
                  <td className="num">{c.delivered ? <span style={{ color: "var(--accent-ink)", fontWeight: 600 }}>{pct(c.opens, c.delivered)}%</span> : "—"}</td>
                  <td className="num">{c.opens ? pct(c.clicks, c.opens) + "%" : "—"}</td>
                  <td className="num" style={{ fontFamily: "var(--mono)" }}>{c.replies || "—"}</td>
                  <td><span className="chip" style={{ background: bg, color: fg, height: 22 }}>{lbl}</span></td>
                </tr>
              ); })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="dash-grid">
          {/* segment builder */}
          <div className="card">
            <div className="card-head"><h3>Build a segment</h3><span className="sub" style={{ marginLeft: "auto" }}>from {audience.length} contacts</span></div>
            <div className="card-pad" style={{ display: "flex", flexDirection: "column", gap: 13 }}>
              {[["city", "City", cities], ["industry", "Industry", industries], ["source", "Lead source", sources], ["heat", "Temperature", ["hot", "warm", "cold"]]].map(([key, label, opts]) => (
                <div key={key} className="wf-field">
                  <label>{label}</label>
                  <select value={filters[key]} onChange={(e) => setFilters((f) => ({ ...f, [key]: e.target.value }))}>
                    <option value="all">Any {label.toLowerCase()}</option>
                    {opts.map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                </div>
              ))}
              <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 14px", background: "var(--accent-soft)", borderRadius: "var(--r-md)" }}>
                <div style={{ flex: 1 }}><div style={{ fontSize: 22, fontWeight: 780, color: "var(--accent-ink)" }}>{matched.length}</div><div style={{ fontSize: 12, color: "var(--ink-3)" }}>contacts match · {segLabel()}</div></div>
                <button className="btn btn-primary btn-sm" disabled={!matched.length} onClick={() => setCompose({ segment: segLabel(), count: matched.length })}><Icon name="mail" size={14} />Email them</button>
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {matched.slice(0, 6).map((a) => <span key={a.id} className="chip" style={{ height: 24 }}><span className="avatar" style={{ background: a.color, width: 16, height: 16, fontSize: 8 }}>{a.init}</span>{a.name}</span>)}
                {matched.length > 6 && <span className="chip" style={{ height: 24 }}>+{matched.length - 6} more</span>}
              </div>
            </div>
          </div>

          {/* smart segments */}
          <div className="card" style={{ alignSelf: "start" }}>
            <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="spark" size={15} /></div><h3>Smart segments</h3><span className="sub" style={{ marginLeft: "auto" }}>found by your agent</span></div>
            <div style={{ padding: "4px var(--pad) 12px", display: "flex", flexDirection: "column" }}>
              {EMAIL_SEED.smart.map((s) => (
                <div key={s.id} style={{ display: "flex", alignItems: "center", gap: 11, padding: "11px 0", borderBottom: "1px solid var(--line-2)" }}>
                  <div className="feed-ico" style={{ width: 30, height: 30, background: "var(--amber-soft)", color: "oklch(0.5 0.12 60)", flexShrink: 0 }}><Icon name={s.icon} size={15} /></div>
                  <div style={{ flex: 1, minWidth: 0 }}><b style={{ fontSize: 13, fontWeight: 650 }}>{s.name}</b><div style={{ fontSize: 11.5, color: "var(--ink-4)" }}>{s.desc}</div></div>
                  <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--ink-3)" }}>{s.count}</span>
                  <button className="btn btn-ghost btn-sm" onClick={() => setCompose({ segment: s.name, count: s.count })}>Email</button>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {compose && <ComposeModal compose={compose} agents={agents} onClose={() => setCompose(null)} onNote={note} />}
      {toast && (
        <div style={{ position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)", zIndex: 70, background: "var(--ink)", color: "var(--bg)", borderRadius: "var(--r-md)", padding: "12px 18px", display: "flex", alignItems: "center", gap: 10, boxShadow: "var(--shadow-xl)", animation: "feed-in .3s both", maxWidth: "90vw" }}>
          <Icon name="checkCircle" size={18} /><span style={{ fontSize: 13.5, fontWeight: 600 }}>{toast}</span>
        </div>
      )}
    </div>
  );
}

function ComposeModal({ compose, agents, onClose, onNote }) {
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [drafting, setDrafting] = useState(false);
  const draft = () => {
    setDrafting(true);
    setTimeout(() => {
      setSubject("A quick idea for your team");
      setBody("Hi {first},\n\nI noticed {company} has been growing — congrats. We help businesses like yours win back hours every week by putting agents on the busywork.\n\nWorth a quick 15-minute look this week?\n\n— Jordan");
      setDrafting(false);
    }, 900);
  };
  const send = (schedule) => {
    FLStore.sendCampaign({ name: subject || "Untitled campaign", segment: compose.segment, count: compose.count, schedule });
    onClose(); onNote && onNote(schedule ? "Campaign scheduled" : `Sent to ${compose.count} contacts`);
  };
  return (
    <div className="cmdk-scrim show" onClick={onClose} style={{ alignItems: "center", paddingTop: 0 }}>
      <div className="cmdk" style={{ maxWidth: 500 }} onClick={(e) => e.stopPropagation()}>
        <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--line)", display: "flex", alignItems: "center", gap: 11 }}>
          <div className="feed-ico" style={{ width: 32, height: 32, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="mail" size={16} /></div>
          <div style={{ flex: 1 }}><b style={{ fontSize: 16, fontWeight: 720 }}>New campaign</b><div style={{ fontSize: 12, color: "var(--ink-4)" }}>To {compose.count} contacts · {compose.segment}</div></div>
          <button className="icon-btn" onClick={onClose}><Icon name="x" size={18} /></button>
        </div>
        <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 13 }}>
          <div className="wf-field"><label>Subject</label><input autoFocus value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="Your subject line" /></div>
          <div className="wf-field">
            <label style={{ display: "flex", alignItems: "center" }}>Body <button className="btn btn-ghost btn-sm" style={{ marginLeft: "auto", height: 24 }} onClick={draft} disabled={drafting}><Icon name="spark" size={12} />{drafting ? "Writing…" : "Draft with agent"}</button></label>
            <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={6} placeholder="Write your message, or let your agent draft it. Use {first} and {company} to personalize." style={{ width: "100%", resize: "vertical", border: "1px solid var(--line)", borderRadius: "var(--r-sm)", padding: "10px 12px", fontSize: 13, lineHeight: 1.5, background: "var(--bg)", color: "var(--ink)", fontFamily: "inherit", outline: "none" }} />
          </div>
          <div style={{ fontSize: 11.5, color: "var(--ink-4)", display: "flex", alignItems: "center", gap: 7 }}><Icon name="shield" size={13} />Sent from your verified domain · unsubscribe handled automatically</div>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn-ghost" style={{ flex: 1 }} disabled={!subject.trim()} onClick={() => send(true)}><Icon name="calendar" size={15} />Schedule</button>
            <button className="btn btn-primary" style={{ flex: 1 }} disabled={!subject.trim()} onClick={() => send(false)}><Icon name="send" size={15} />Send now</button>
          </div>
        </div>
      </div>
    </div>
  );
}

window.Email = Email;
