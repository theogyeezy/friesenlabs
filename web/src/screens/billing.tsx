// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// billing.jsx — Quotes → e-sign → invoice → payment

const fmtB = (n) => "$" + n.toLocaleString();

const Q_STATUS = { draft: ["Draft", "var(--ink-4)", "var(--surface-2)"], sent: ["Sent · awaiting e-sign", "oklch(0.5 0.12 60)", "var(--amber-soft)"], signed: ["Signed", "oklch(0.42 0.12 152)", "var(--green-soft)"] };
const I_STATUS = { due: ["Due", "oklch(0.5 0.12 60)", "var(--amber-soft)"], paid: ["Paid", "oklch(0.42 0.12 152)", "var(--green-soft)"], overdue: ["Overdue", "oklch(0.48 0.14 18)", "var(--rose-soft)"] };
const REV_TREND = [["Jan", 18], ["Feb", 24], ["Mar", 29], ["Apr", 26], ["May", 37], ["Jun", 43]];
// aging bucket from the invoice's due text / status
function aging(inv) {
  if (inv.status === "paid") return null;
  const m = (inv.due || "").match(/(\d+)/); const n = m ? +m[1] : 0;
  if (inv.status === "overdue") return n > 60 ? "61-90" : n > 30 ? "31-60" : "1-30";
  return "current";
}
const DUNNING = { "1-30": "Reminder 1 sent", "31-60": "Reminder 2 · escalating", "61-90": "Final notice", current: "On track" };

function Billing({ agents, onNavigate }) {
  const quotes = useStore((s) => s.quotes);
  const invoices = useStore((s) => s.invoices);
  const [tab, setTab] = useState("quotes");
  const [toast, setToast] = useState(null);
  const [preview, setPreview] = useState(null);
  const note = (m) => { setToast(m); setTimeout(() => setToast(null), 2600); };

  const outstanding = invoices.filter((i) => i.status !== "paid").reduce((s, i) => s + i.amount, 0);
  const paid = invoices.filter((i) => i.status === "paid").reduce((s, i) => s + i.amount, 0);
  const quoted = quotes.reduce((s, q) => s + q.amount, 0);

  return (
    <div className="screen screen-anim">
      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 7 }}>Get paid, from quote to cash</div>
          <h2 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.03em" }}>Billing</h2>
          <p style={{ color: "var(--ink-2)", fontSize: 14.5, marginTop: 5 }}>Quotes, e-signature, invoices and payments, your agents draft them and chase what's owed.</p>
        </div>
        <div style={{ marginLeft: "auto" }}>
          <button className="btn btn-primary" onClick={() => note("Margo is drafting a quote from your latest deal…")}><Icon name="plus" size={16} sw={2.2} />New quote</button>
        </div>
      </div>

      <div className="stat-grid" style={{ marginBottom: "var(--gap)" }}>
        {[["doc", "amber", "Open quotes", fmtB(quoted), quotes.length + " quotes"], ["trend", "rose", "Outstanding", fmtB(outstanding), invoices.filter((i) => i.status !== "paid").length + " invoices"], ["checkCircle", "green", "Collected", fmtB(paid), "this period"]].map(([ic, tone, label, val, sub]) => {
          const tt = { amber: ["var(--amber-soft)", "oklch(0.5 0.12 60)"], rose: ["var(--rose-soft)", "oklch(0.48 0.14 18)"], green: ["var(--green-soft)", "oklch(0.42 0.12 152)"] }[tone];
          return <div className="stat" key={label}><div className="stat-top"><div className="stat-ico" style={{ background: tt[0], color: tt[1] }}><Icon name={ic} size={17} /></div></div><div className="stat-val" style={{ fontSize: 24 }}>{val}</div><div className="stat-label">{label}</div><div style={{ fontSize: 11, color: "var(--ink-4)" }}>{sub}</div></div>;
        })}
      </div>

      <div className="seg" style={{ marginBottom: "var(--gap)" }}>
        <button className={tab === "quotes" ? "active" : ""} onClick={() => setTab("quotes")}><Icon name="doc" size={15} />Quotes</button>
        <button className={tab === "invoices" ? "active" : ""} onClick={() => setTab("invoices")}><Icon name="trend" size={15} />Invoices</button>
      </div>

      {tab === "quotes" ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 11 }}>
          {quotes.map((q) => { const [lbl, fg, bg] = Q_STATUS[q.status]; return (
            <div className="card" key={q.id} style={{ padding: 16 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                <div className="deal-co" style={{ background: q.color, width: 38, height: 38, fontSize: 13, borderRadius: 11 }}>{q.init}</div>
                <div style={{ flex: 1, minWidth: 140 }}><b style={{ fontSize: 14.5, fontWeight: 700 }}>{q.co}</b><div style={{ fontSize: 12, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>{q.id} · {q.created}</div></div>
                <span className="chip" style={{ background: bg, color: fg, height: 24 }}>{lbl}</span>
                <span style={{ fontSize: 17, fontWeight: 760, fontFamily: "var(--mono)", minWidth: 90, textAlign: "right" }}>{fmtB(q.amount)}</span>
              </div>
              <div style={{ display: "flex", gap: 7, marginTop: 13, flexWrap: "wrap" }}>
                {q.status === "draft" && <button className="btn btn-primary btn-sm" onClick={() => { FLStore.sendQuote(q.id); note("Quote sent for e-signature"); }}><Icon name="send" size={13} />Send for e-sign</button>}
                {q.status === "sent" && <button className="btn btn-primary btn-sm" onClick={() => { const inv = FLStore.convertQuote(q.id); note("Signed · created invoice " + inv); }}><Icon name="check" size={13} sw={2.4} />Mark signed → invoice</button>}
                {q.status === "signed" && <span style={{ fontSize: 12.5, color: "var(--green)", fontWeight: 600, display: "flex", alignItems: "center", gap: 6 }}><Icon name="checkCircle" size={14} />Signed &amp; invoiced</span>}
                <button className="btn btn-ghost btn-sm" onClick={() => setPreview(q)}><Icon name="doc" size={13} />Preview</button>
              </div>
            </div>
          ); })}
        </div>
      ) : (
        <>
          <div className="dash-grid" style={{ marginBottom: "var(--gap)" }}>
            <div className="card">
              <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--green-soft)", color: "oklch(0.42 0.12 152)" }}><Icon name="trend" size={15} /></div><h3>Revenue trend</h3><span className="sub" style={{ marginLeft: "auto" }}>collected, last 6 months</span></div>
              <div className="card-pad">
                <div style={{ display: "flex", alignItems: "flex-end", gap: 10, height: 120 }}>
                  {REV_TREND.map(([m, v], i) => { const max = Math.max(...REV_TREND.map((x) => x[1])); return (
                    <div key={m} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
                      <div style={{ fontSize: 10.5, fontFamily: "var(--mono)", color: "var(--ink-4)" }}>${v}k</div>
                      <div style={{ width: "100%", height: (v / max * 84) + "px", borderRadius: "6px 6px 0 0", background: i === REV_TREND.length - 1 ? "var(--green)" : "var(--accent-soft)", transition: "height .5s cubic-bezier(.2,.7,.2,1)" }} />
                      <div style={{ fontSize: 11, color: "var(--ink-3)" }}>{m}</div>
                    </div>
                  ); })}
                </div>
              </div>
            </div>
            <div className="card" style={{ alignSelf: "start" }}>
              <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--rose-soft)", color: "oklch(0.48 0.14 18)" }}><Icon name="clock" size={15} /></div><h3>Aging</h3><span className="sub" style={{ marginLeft: "auto" }}>overdue receivables</span></div>
              <div className="card-pad" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {["1-30", "31-60", "61-90"].map((bk) => { const items = invoices.filter((i) => aging(i) === bk); const amt = items.reduce((s, i) => s + i.amount, 0); return (
                  <div key={bk} style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 12.5 }}>
                    <span style={{ width: 52, color: "var(--ink-2)", fontFamily: "var(--mono)" }}>{bk}d</span>
                    <span className="rep-bar"><span style={{ width: (items.length ? Math.max(12, amt / outstanding * 100) : 0) + "%", background: bk === "61-90" ? "var(--rose)" : bk === "31-60" ? "var(--amber)" : "var(--ink-4)" }} /></span>
                    <span style={{ fontFamily: "var(--mono)", fontWeight: 650, minWidth: 56, textAlign: "right" }}>{fmtB(amt)}</span>
                  </div>
                ); })}
                <p style={{ fontSize: 11.5, color: "var(--ink-4)", marginTop: 2 }}>Ledger auto-chases each bucket on its own cadence.</p>
              </div>
            </div>
          </div>
          <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr><th>Invoice</th><th>Customer</th><th className="num">Amount</th><th>Status</th><th>Aging</th><th>Chasing</th><th></th></tr></thead>
            <tbody>
              {invoices.map((inv) => { const [lbl, fg, bg] = I_STATUS[inv.status]; const ag = aging(inv); return (
                <tr key={inv.id}>
                  <td style={{ fontFamily: "var(--mono)", fontWeight: 600 }}>{inv.id}</td>
                  <td><span className="agent-tag"><div className="avatar" style={{ background: inv.color, fontSize: 10 }}>{inv.init}</div>{inv.co}</span></td>
                  <td className="num" style={{ fontWeight: 700 }}>{fmtB(inv.amount)}</td>
                  <td><span className="chip" style={{ background: bg, color: fg, height: 22 }}>{lbl}</span><div style={{ fontSize: 11, color: "var(--ink-4)", marginTop: 2 }}>{inv.due}</div></td>
                  <td>{ag && ag !== "current" ? <span className="chip" style={{ height: 20, fontSize: 10.5, background: ag === "61-90" ? "var(--rose-soft)" : "var(--amber-soft)", color: ag === "61-90" ? "oklch(0.48 0.14 18)" : "oklch(0.5 0.12 60)" }}>{ag} days</span> : <span style={{ fontSize: 12, color: "var(--ink-4)" }}>—</span>}</td>
                  <td>{inv.status !== "paid" ? <span style={{ fontSize: 11.5, color: "var(--ink-3)", display: "flex", alignItems: "center", gap: 5 }}><span className="avatar" style={{ background: "oklch(0.66 0.14 50)", width: 16, height: 16, fontSize: 8 }}>🦫</span>{DUNNING[ag] || "On track"}</span> : <span style={{ fontSize: 12, color: "var(--ink-4)" }}>—</span>}</td>
                  <td style={{ textAlign: "right" }}>
                    {inv.status !== "paid"
                      ? <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
                          {inv.status === "overdue" && <button className="btn btn-ghost btn-sm" onClick={() => note("Ledger is sending a payment reminder…")}><Icon name="mail" size={13} />Chase</button>}
                          <button className="btn btn-primary btn-sm" onClick={() => FLStore.markInvoicePaid(inv.id)}><Icon name="check" size={13} sw={2.4} />Mark paid</button>
                        </div>
                      : <span style={{ fontSize: 12.5, color: "var(--green)", fontWeight: 600 }}>Collected</span>}
                  </td>
                </tr>
              ); })}
            </tbody>
          </table>
          </div>
        </>
      )}

      {preview && (
        <div className="cmdk-scrim show" onClick={() => setPreview(null)} style={{ alignItems: "center", paddingTop: 0 }}>
          <div className="cmdk" style={{ maxWidth: 460 }} onClick={(e) => e.stopPropagation()}>
            <div style={{ padding: "18px 20px", borderBottom: "1px solid var(--line)", display: "flex", alignItems: "center", gap: 11 }}>
              <div className="deal-co" style={{ background: preview.color, width: 34, height: 34, fontSize: 12, borderRadius: 10 }}>{preview.init}</div>
              <div style={{ flex: 1 }}><b style={{ fontSize: 16, fontWeight: 720 }}>Quote {preview.id}</b><div style={{ fontSize: 12, color: "var(--ink-4)" }}>{preview.co}</div></div>
              <button className="icon-btn" onClick={() => setPreview(null)}><Icon name="x" size={18} /></button>
            </div>
            <div style={{ padding: 20 }}>
              <div style={{ border: "1px solid var(--line-2)", borderRadius: "var(--r-md)", overflow: "hidden" }}>
                {preview.items.map(([name, amt], i) => (
                  <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, padding: "11px 14px", borderBottom: "1px solid var(--line-2)" }}>
                    <Icon name="layers" size={14} style={{ color: "var(--ink-4)" }} /><span style={{ flex: 1, fontSize: 13 }}>{name}</span><span style={{ fontSize: 13, fontWeight: 650, fontFamily: "var(--mono)" }}>{fmtB(amt)}</span>
                  </div>
                ))}
                <div style={{ display: "flex", alignItems: "center", padding: "12px 14px", background: "var(--surface-2)" }}><span style={{ flex: 1, fontSize: 13, fontWeight: 600 }}>Total</span><span style={{ fontSize: 15, fontWeight: 760, fontFamily: "var(--mono)" }}>{fmtB(preview.amount)}</span></div>
              </div>
              <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
                {preview.status === "draft" && <button className="btn btn-primary btn-sm" onClick={() => { FLStore.sendQuote(preview.id); setPreview(null); note("Quote sent for e-signature"); }}><Icon name="send" size={13} />Send for e-sign</button>}
                <button className="btn btn-ghost btn-sm" onClick={() => setPreview(null)}>Close</button>
              </div>
            </div>
          </div>
        </div>
      )}
      {toast && (
        <div style={{ position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)", zIndex: 70, background: "var(--ink)", color: "var(--bg)", borderRadius: "var(--r-md)", padding: "12px 18px", display: "flex", alignItems: "center", gap: 10, boxShadow: "var(--shadow-xl)", animation: "feed-in .3s both", maxWidth: "90vw" }}>
          <Icon name="checkCircle" size={18} /><span style={{ fontSize: 13.5, fontWeight: 600 }}>{toast}</span>
        </div>
      )}
    </div>
  );
}

window.Billing = Billing;
