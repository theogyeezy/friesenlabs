// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// reviews.jsx — Reputation: review requests, collected reviews, referrals

function RvStars({ n, size = 14 }) {
  return <span style={{ display: "inline-flex", gap: 1 }}>{[1, 2, 3, 4, 5].map((i) => <span key={i} style={{ color: i <= n ? "oklch(0.7 0.14 65)" : "var(--line)", fontSize: size }}>★</span>)}</span>;
}
const RV_TREND = [["Jan", 4.4], ["Feb", 4.5], ["Mar", 4.6], ["Apr", 4.6], ["May", 4.8], ["Jun", 4.9]];
const RV_SOURCES = [["Google", 38, 4.8], ["Yelp", 14, 4.6], ["Facebook", 9, 4.9], ["Direct", 6, 5.0]];
const RV_THEMES = [["Reliability & on-time", 41, "pos"], ["Friendly crew", 33, "pos"], ["Quality of work", 28, "pos"], ["Pricing clarity", 9, "neg"], ["Scheduling lead time", 6, "neg"]];

function Reviews({ agents, onNavigate }) {
  const reviews = useStore((s) => s.reviews);
  const referrals = useStore((s) => s.referrals);
  const [tab, setTab] = useState("reviews");
  const [toast, setToast] = useState(null);
  const [msg, setMsg] = useState(null);
  const [resolve, setResolve] = useState(null);
  const note = (m) => { setToast(m); setTimeout(() => setToast(null), 2600); };

  const posted = reviews.filter((r) => r.status === "posted");
  const avg = posted.length ? (posted.reduce((s, r) => s + r.rating, 0) / posted.length).toFixed(1) : "—";
  const pending = reviews.filter((r) => r.status === "requested");

  return (
    <div className="screen screen-anim">
      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 7 }}>Win the word-of-mouth game</div>
          <h2 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.03em" }}>Reputation</h2>
          <p style={{ color: "var(--ink-2)", fontSize: 14.5, marginTop: 5 }}>Your agents ask happy customers for reviews at the right moment, and turn referrals into new deals.</p>
        </div>
      </div>

      <div className="stat-grid" style={{ marginBottom: "var(--gap)" }}>
        {[["spark", "amber", "Avg rating", avg + (avg !== "—" ? " ★" : ""), posted.length + " reviews"], ["mail", "indigo", "Requests out", pending.length, "awaiting response"], ["users", "green", "Referrals", referrals.length, referrals.filter((r) => r.status === "won").length + " converted"]].map(([ic, tone, label, val, sub]) => {
          const tt = { amber: ["var(--amber-soft)", "oklch(0.5 0.12 60)"], indigo: ["var(--accent-soft)", "var(--accent-ink)"], green: ["var(--green-soft)", "oklch(0.42 0.12 152)"] }[tone];
          return <div className="stat" key={label}><div className="stat-top"><div className="stat-ico" style={{ background: tt[0], color: tt[1] }}><Icon name={ic} size={17} /></div></div><div className="stat-val" style={{ fontSize: 24 }}>{val}</div><div className="stat-label">{label}</div><div style={{ fontSize: 11, color: "var(--ink-4)" }}>{sub}</div></div>;
        })}
      </div>

      <div className="seg" style={{ marginBottom: "var(--gap)" }}>
        <button className={tab === "reviews" ? "active" : ""} onClick={() => setTab("reviews")}><Icon name="spark" size={15} />Reviews</button>
        <button className={tab === "referrals" ? "active" : ""} onClick={() => setTab("referrals")}><Icon name="users" size={15} />Referrals</button>
      </div>

      {tab === "reviews" ? (
        <>
          <div className="dash-grid" style={{ marginBottom: "var(--gap)" }}>
            <div className="card">
              <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--amber-soft)", color: "oklch(0.5 0.12 60)" }}><Icon name="trend" size={15} /></div><h3>Rating trend</h3><span className="sub" style={{ marginLeft: "auto" }}>6 months</span></div>
              <div className="card-pad">
                <div style={{ display: "flex", alignItems: "flex-end", gap: 10, height: 110 }}>
                  {RV_TREND.map(([m, v], i) => (
                    <div key={m} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 5 }}>
                      <div style={{ fontSize: 10, fontFamily: "var(--mono)", color: "var(--ink-4)" }}>{v}</div>
                      <div style={{ width: "100%", height: ((v - 4) / 1 * 70 + 10) + "px", borderRadius: "6px 6px 0 0", background: i === RV_TREND.length - 1 ? "oklch(0.7 0.14 65)" : "var(--amber-soft)", transition: "height .5s cubic-bezier(.2,.7,.2,1)" }} />
                      <div style={{ fontSize: 10.5, color: "var(--ink-3)" }}>{m}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
            <div className="card" style={{ alignSelf: "start" }}>
              <div className="card-head"><h3>By source</h3></div>
              <div className="card-pad" style={{ display: "flex", flexDirection: "column", gap: 9 }}>
                {RV_SOURCES.map(([s, n, r]) => (
                  <div key={s} style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 12.5 }}>
                    <span style={{ flex: 1, color: "var(--ink-2)" }}>{s}</span>
                    <RvStars n={Math.round(r)} size={11} />
                    <span style={{ fontFamily: "var(--mono)", fontWeight: 650, width: 40, textAlign: "right" }}>{r}</span>
                    <span style={{ fontSize: 11, color: "var(--ink-4)", width: 42, textAlign: "right" }}>{n}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
          <div className="card" style={{ marginBottom: "var(--gap)" }}>
            <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="spark" size={15} /></div><h3>What customers mention</h3><span className="sub" style={{ marginLeft: "auto" }}>sentiment themes</span></div>
            <div className="card-pad" style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {RV_THEMES.map(([t, n, s]) => (
                <span key={t} className="chip" style={{ height: 30, gap: 7, background: s === "pos" ? "var(--green-soft)" : "var(--rose-soft)", color: s === "pos" ? "oklch(0.42 0.12 152)" : "oklch(0.48 0.14 18)" }}>
                  <Icon name={s === "pos" ? "checkCircle" : "bolt"} size={12} />{t}<b style={{ fontFamily: "var(--mono)" }}>{n}</b>
                </span>
              ))}
            </div>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 11 }}>
          {reviews.map((r) => (
            <div className="card" key={r.id} style={{ padding: 16 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                <div className="deal-co" style={{ background: r.color, width: 36, height: 36, fontSize: 12, borderRadius: 10 }}>{r.init}</div>
                <div style={{ flex: 1, minWidth: 140 }}><b style={{ fontSize: 14, fontWeight: 700 }}>{r.co}</b><div style={{ fontSize: 12, color: "var(--ink-4)" }}>{r.who} · {r.source}</div></div>
                {r.status === "posted"
                  ? <RvStars n={r.rating} size={15} />
                  : <span className="chip" style={{ background: "var(--amber-soft)", color: "oklch(0.5 0.12 60)", height: 22 }}>Requested</span>}
              </div>
              {r.status === "posted"
                ? <>
                    <p style={{ fontSize: 13.5, color: "var(--ink-2)", lineHeight: 1.55, marginTop: 11, fontStyle: "italic" }}>“{r.text}”</p>
                    {r.rating <= 3 && <div style={{ display: "flex", alignItems: "center", gap: 9, marginTop: 11, padding: "10px 13px", background: "var(--rose-soft)", borderRadius: "var(--r-sm)", flexWrap: "wrap" }}><Icon name="bolt" size={15} style={{ color: "oklch(0.48 0.14 18)", flexShrink: 0 }} /><span style={{ fontSize: 12.5, color: "oklch(0.42 0.12 18)", flex: 1 }}>Low rating, resolve privately before it spreads.</span><button className="btn btn-sm" style={{ background: "oklch(0.48 0.14 18)", color: "#fff" }} onClick={() => setResolve(r)}><Icon name="mail" size={13} />Resolve privately</button></div>}
                  </>
                : <div style={{ display: "flex", gap: 7, marginTop: 12 }}>
                    <button className="btn btn-ghost btn-sm" onClick={() => note("Resent review request to " + r.co)}><Icon name="mail" size={13} />Resend ask</button>
                    <button className="btn btn-ghost btn-sm" onClick={() => setMsg(r)}>View message</button>
                  </div>}
            </div>
          ))}
          </div>
        </>
      ) : (
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr><th>Referred by</th><th>New prospect</th><th>Status</th><th>Reward</th></tr></thead>
            <tbody>
              {referrals.map((r) => (
                <tr key={r.id}>
                  <td><b style={{ fontWeight: 650 }}>{r.from}</b><div style={{ fontSize: 11.5, color: "var(--ink-4)" }}>{r.who}</div></td>
                  <td style={{ color: "var(--ink-2)" }}>{r.referred}</td>
                  <td><span className="chip" style={{ height: 22, background: r.status === "won" ? "var(--green-soft)" : "var(--amber-soft)", color: r.status === "won" ? "oklch(0.42 0.12 152)" : "oklch(0.5 0.12 60)" }}>{r.status}</span></td>
                  <td style={{ fontWeight: 600, fontSize: 12.5 }}>{r.reward}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {msg && (
        <div className="cmdk-scrim show" onClick={() => setMsg(null)} style={{ alignItems: "center", paddingTop: 0 }}>
          <div className="cmdk" style={{ maxWidth: 440 }} onClick={(e) => e.stopPropagation()}>
            <div style={{ padding: "18px 20px", borderBottom: "1px solid var(--line)", display: "flex", alignItems: "center", gap: 11 }}>
              <div className="feed-ico" style={{ width: 32, height: 32, background: "var(--amber-soft)", color: "oklch(0.5 0.12 60)" }}><Icon name="mail" size={16} /></div>
              <b style={{ fontSize: 16, fontWeight: 720, flex: 1 }}>Review request</b>
              <button className="icon-btn" onClick={() => setMsg(null)}><Icon name="x" size={18} /></button>
            </div>
            <div style={{ padding: 20 }}>
              <div style={{ fontSize: 12, color: "var(--ink-4)", marginBottom: 4 }}>To {msg.who} · via {msg.source} · SMS</div>
              <div style={{ background: "var(--surface-2)", borderRadius: "var(--r-md)", padding: "14px 16px", fontSize: 13.5, lineHeight: 1.55, color: "var(--ink)" }}>Hi {msg.who.split(" ")[0]}, thanks so much for choosing us! If you have 30 seconds, a quick {msg.source} review would mean the world and helps other local businesses find us: <span style={{ color: "var(--accent-ink)" }}>friesen.app/r/{msg.init.toLowerCase()}</span></div>
              <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
                <button className="btn btn-primary btn-sm" onClick={() => { setMsg(null); note("Review request resent to " + msg.co); }}><Icon name="send" size={13} />Resend now</button>
                <button className="btn btn-ghost btn-sm" onClick={() => setMsg(null)}>Close</button>
              </div>
            </div>
          </div>
        </div>
      )}
      {resolve && (
        <div className="cmdk-scrim show" onClick={() => setResolve(null)} style={{ alignItems: "center", paddingTop: 0 }}>
          <div className="cmdk" style={{ maxWidth: 460 }} onClick={(e) => e.stopPropagation()}>
            <div style={{ padding: "18px 20px", borderBottom: "1px solid var(--line)", display: "flex", alignItems: "center", gap: 11 }}>
              <div className="feed-ico" style={{ width: 32, height: 32, background: "var(--rose-soft)", color: "oklch(0.48 0.14 18)" }}><Icon name="bolt" size={16} /></div>
              <div style={{ flex: 1 }}><b style={{ fontSize: 16, fontWeight: 720 }}>Resolve privately</b><div style={{ fontSize: 12, color: "var(--ink-4)" }}>{resolve.co} · {resolve.rating}★ on {resolve.source}</div></div>
              <button className="icon-btn" onClick={() => setResolve(null)}><Icon name="x" size={18} /></button>
            </div>
            <div style={{ padding: 20 }}>
              <p style={{ fontSize: 12.5, color: "var(--ink-3)", marginBottom: 10 }}>Echo drafted a private outreach to make it right before asking them to update the review.</p>
              <div style={{ background: "var(--surface-2)", borderRadius: "var(--r-md)", padding: "14px 16px", fontSize: 13.5, lineHeight: 1.55, color: "var(--ink)" }}>Hi {resolve.who.split(" ")[0]}, I saw your note and I'm sorry we missed the mark. I'd really like to make this right, can I call you today? We'll fix it and take care of you. — The team</div>
              <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
                <button className="btn btn-primary btn-sm" onClick={() => { setResolve(null); note("Private resolution sent to " + resolve.co); }}><Icon name="send" size={13} />Send &amp; track resolution</button>
                <button className="btn btn-ghost btn-sm" onClick={() => setResolve(null)}>Cancel</button>
              </div>
            </div>
          </div>
        </div>
      )}
      {toast && (        <div style={{ position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)", zIndex: 70, background: "var(--ink)", color: "var(--bg)", borderRadius: "var(--r-md)", padding: "12px 18px", display: "flex", alignItems: "center", gap: 10, boxShadow: "var(--shadow-xl)", animation: "feed-in .3s both", maxWidth: "90vw" }}>
          <Icon name="checkCircle" size={18} /><span style={{ fontSize: 13.5, fontWeight: 600 }}>{toast}</span>
        </div>
      )}
    </div>
  );
}

window.Reviews = Reviews;
