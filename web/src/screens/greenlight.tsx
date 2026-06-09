// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// greenlight.jsx, sign-off queue: triage list + inline edit + bulk approve + history

const GTYPE = {
  email:    { label: "Email",      ico: "mail",     tone: "indigo", bodyLabel: "Email body",        verb: "Approve & send" },
  quote:    { label: "Quote",      ico: "doc",      tone: "amber",  bodyLabel: "Cover note",        verb: "Approve & send" },
  discount: { label: "Discount",   ico: "trend",    tone: "green",  bodyLabel: "Justification",     verb: "Approve discount" },
  invoice:  { label: "Invoice",    ico: "doc",      tone: "green",  bodyLabel: "Note to customer",  verb: "Approve & send" },
  schedule: { label: "Scheduling", ico: "calendar", tone: "indigo", bodyLabel: "Note",              verb: "Confirm booking" },
  task:     { label: "Task",       ico: "check",    tone: "indigo", bodyLabel: "Summary",           verb: "Approve" },
};
const GTONE = {
  indigo: ["var(--accent-soft)", "var(--accent-ink)"],
  amber:  ["var(--amber-soft)", "oklch(0.5 0.12 60)"],
  green:  ["var(--green-soft)", "oklch(0.42 0.12 152)"],
};
const POLICY = {
  within:  { cls: "green", label: "Within policy" },
  review:  { cls: "amber", label: "Needs review" },
  exceeds: { cls: "rose",  label: "Exceeds limit" },
};
const RISK = { low: "var(--green)", med: "var(--amber)", high: "var(--rose)" };

function Greenlight({ agents }) {
  const items = useStore((s) => s.greenlight);
  const [tab, setTab] = useState("pending");
  const [selId, setSelId] = useState(window.FL_DATA.GREENLIGHT_SEED[0].id);
  const [checked, setChecked] = useState({});
  const [resolving, setResolving] = useState({});
  const [toast, setToast] = useState(null);
  const [declineFor, setDeclineFor] = useState(null);
  const DECLINE_REASONS = ["Wrong tone / off-brand", "Price or terms too aggressive", "Bad timing", "Factually incorrect", "Needs my personal touch", "Not a fit"];

  const inTab = useMemo(() => items.filter((i) => i.status === tab), [items, tab]);
  const pending = items.filter((i) => i.status === "pending");
  const pendingValue = pending.reduce((s, i) => s + i.value, 0);
  const sel = items.find((i) => i.id === selId);

  const checkedIds = Object.keys(checked).filter((k) => checked[k] && items.find((i) => i.id === k && i.status === "pending"));

  const pickNext = (excludeIds) => {
    const ex = new Set(excludeIds);
    const next = items.find((i) => i.status === "pending" && !ex.has(i.id));
    setSelId(next ? next.id : (excludeIds[0] || null));
  };

  const resolve = (ids, decision) => {
    const r = {}; ids.forEach((id) => (r[id] = true));
    setResolving((p) => ({ ...p, ...r }));
    setToast({ decision, n: ids.length });
    setTimeout(() => setToast(null), 3000);
    setTimeout(() => {
      FLStore.resolveGreenlight(ids, decision);
      setResolving({});
      setChecked({});
      if (ids.includes(selId)) pickNext(ids);
    }, 380);
  };

  const editDraft = (id, val) => FLStore.editDraft(id, val);
  const toggleCheck = (id) => setChecked((c) => ({ ...c, [id]: !c[id] }));
  const allChecked = pending.length > 0 && pending.every((i) => checked[i.id]);
  const toggleAll = () => { if (allChecked) setChecked({}); else { const c = {}; pending.forEach((i) => (c[i.id] = true)); setChecked(c); } };
  const approveLowRisk = () => { const ids = pending.filter((i) => i.risk === "low").map((i) => i.id); if (ids.length) resolve(ids, "approved"); };

  const tabs = [
    { id: "pending", label: "Pending", n: pending.length },
    { id: "approved", label: "Approved", n: items.filter((i) => i.status === "approved").length },
    { id: "declined", label: "Declined", n: items.filter((i) => i.status === "declined").length },
  ];

  return (
    <div className="gl">
      <div className="gl-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 7 }}>Human-in-the-loop</div>
          <h2 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.03em" }}>Greenlight</h2>
          <p style={{ color: "var(--ink-2)", fontSize: 14.5, marginTop: 5 }}>Every agent action that needs your sign-off, in one queue.</p>
        </div>
        <div className="gl-stats">
          <div className="gl-stat"><b>{pending.length}</b><span>Pending</span></div>
          <div className="gl-stat"><b>${(pendingValue / 1000).toFixed(1)}k</b><span>Value at stake</span></div>
          <div className="gl-stat"><b>86%</b><span>Auto-approved</span></div>
        </div>
      </div>

      <div className="gl-main">
        {/* queue list */}
        <div className="gl-list">
          <div className="gl-tabs">
            <div className="seg" style={{ width: "100%" }}>
              {tabs.map((t) => (
                <button key={t.id} className={tab === t.id ? "active" : ""} style={{ flex: 1, justifyContent: "center" }} onClick={() => { setTab(t.id); const f = items.find((i) => i.status === t.id); setSelId(f ? f.id : null); }}>
                  {t.label}<span style={{ fontFamily: "var(--mono)", opacity: .7, marginLeft: 2 }}>{t.n}</span>
                </button>
              ))}
            </div>
          </div>

          {tab === "pending" && (
            <div className="gl-list-head">
              <div className={"gl-check" + (allChecked ? " on" : "")} onClick={toggleAll}><Icon name="check" size={13} sw={3} /></div>
              {checkedIds.length > 0
                ? <><span className="lh-label">{checkedIds.length} selected</span>
                    <button className="btn btn-primary btn-sm" style={{ marginLeft: "auto" }} onClick={() => resolve(checkedIds, "approved")}><Icon name="check" size={13} sw={2.4} />Approve {checkedIds.length}</button></>
                : <><span className="lh-label">Select all</span>
                    <button className="btn btn-soft btn-sm" style={{ marginLeft: "auto" }} onClick={approveLowRisk}><Icon name="bolt" size={13} />Approve low-risk</button></>}
            </div>
          )}

          <div className="gl-scroll">
            {inTab.length === 0 && (
              <div style={{ textAlign: "center", padding: "40px 16px", color: "var(--ink-3)" }}>
                <div style={{ width: 44, height: 44, borderRadius: 12, background: "var(--green-soft)", color: "var(--green)", display: "grid", placeItems: "center", margin: "0 auto 12px" }}><Icon name="check" size={22} sw={2.4} /></div>
                <p style={{ fontSize: 13.5, fontWeight: 600, color: "var(--ink)" }}>{tab === "pending" ? "Inbox zero" : "Nothing here yet"}</p>
                <p style={{ fontSize: 12.5, marginTop: 3 }}>{tab === "pending" ? "Your agents are running autonomously." : `No ${tab} items.`}</p>
              </div>
            )}
            {inTab.map((i) => {
              const ag = agents[i.agent], meta = GTYPE[i.type], [bg, fg] = GTONE[meta.tone], pol = POLICY[i.policy];
              return (
                <div key={i.id} className={"gl-item" + (selId === i.id ? " sel" : "") + (resolving[i.id] ? " resolving" : "")} onClick={() => setSelId(i.id)}>
                  {i.status === "pending" && (
                    <div className={"gl-check" + (checked[i.id] ? " on" : "")} onClick={(e) => { e.stopPropagation(); toggleCheck(i.id); }}><Icon name="check" size={12} sw={3} /></div>
                  )}
                  <div className="gl-type-ico" style={{ background: bg, color: fg }}><Icon name={meta.ico} size={17} /></div>
                  <div className="gl-it-body">
                    <div className="gl-it-title">{i.title}</div>
                    <div className="gl-it-meta">
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                        <span className="avatar" style={{ background: ag.color, width: 15, height: 15, fontSize: 7 }}>{ag.init}</span>{ag.name}
                      </span>
                      <span>· {i.status === "pending" ? i.ago : i.resolvedAgo}</span>
                    </div>
                    <div style={{ marginTop: 8 }}>
                      {i.status === "pending"
                        ? <span className={"chip " + pol.cls} style={{ height: 20 }}><span className="cdot" style={{ background: RISK[i.risk] }} />{pol.label}</span>
                        : <span className={"chip " + (i.status === "approved" ? "green" : "rose")} style={{ height: 20 }}><Icon name={i.status === "approved" ? "check" : "x"} size={11} sw={2.6} />{i.status === "approved" ? "Approved" : "Declined"}</span>}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* detail */}
        <div className="gl-detail" key={selId || "empty"}>
          {!sel ? (
            <div className="gl-empty">
              <div>
                <Icon name="inbox" size={30} style={{ opacity: .4 }} />
                <p style={{ marginTop: 10, fontSize: 14 }}>Select an item to review.</p>
              </div>
            </div>
          ) : (() => {
            const ag = agents[sel.agent], meta = GTYPE[sel.type], [bg, fg] = GTONE[meta.tone], pol = POLICY[sel.policy];
            const resolved = sel.status !== "pending";
            return (
              <div className="screen-anim">
                {resolved && (
                  <div className={"gl-resolved-banner " + sel.status}>
                    <Icon name={sel.status === "approved" ? "checkCircle" : "xCircle"} size={18} />
                    {sel.status === "approved" ? `Approved ${sel.resolvedAgo}, ${ag.name} executed this action.` : `Declined ${sel.resolvedAgo}.`}
                  </div>
                )}
                <div className="gl-d-hero">
                  <div className="gl-type-ico" style={{ background: bg, color: fg, width: 46, height: 46, borderRadius: 12 }}><Icon name={meta.ico} size={22} /></div>
                  <div style={{ flex: 1 }}>
                    <h2>{sel.title}</h2>
                    <p style={{ fontSize: 13, color: "var(--ink-3)", marginTop: 3 }}>{meta.label} · {sel.company} · {window.fmtMoney(sel.value)}{!resolved && <span style={{ color: /h|day/.test(sel.ago || "") && !/min|sec/.test(sel.ago || "") ? "var(--amber)" : "var(--ink-4)", marginLeft: 8 }}>· waiting {sel.ago}</span>}</p>
                  </div>
                </div>

                <div className="gl-badges">
                  <span className="agent-tag"><div className="avatar" style={{ background: ag.color }}>{ag.init}</div>Proposed by {ag.name}</span>
                  <span className={"chip " + pol.cls}><span className="cdot" style={{ background: RISK[sel.risk] }} />{pol.label}</span>
                  <span className="chip" style={{ textTransform: "capitalize" }}>{sel.risk === "med" ? "Medium" : sel.risk} risk</span>
                </div>

                <div className="gl-why">
                  <div className="w-ico"><Icon name="spark" size={15} /></div>
                  <div><p><b style={{ fontWeight: 700 }}>Why {ag.name} flagged this, </b>{sel.why}</p></div>
                </div>

                <div className="kv" style={{ marginBottom: 22, gridTemplateColumns: "110px 1fr" }}>
                  {sel.rows.map(([k, v], idx) => (<React.Fragment key={idx}><span className="k">{k}</span><span className="v" style={{ fontWeight: 550 }}>{v}</span></React.Fragment>))}
                </div>

                <div className="so-section-label" style={{ marginBottom: 9 }}>{meta.bodyLabel}{resolved ? "" : " · editable"}</div>
                <div className="gl-edit-wrap">
                  {sel.edited && !resolved && <span className="gl-edited-tag"><Icon name="note" size={12} />Edited</span>}
                  <textarea className="gl-edit" value={sel.draft} disabled={resolved} onChange={(e) => editDraft(sel.id, e.target.value)} />
                </div>

                {!resolved && (
                  <div className="gl-actions">
                    <button className="btn btn-primary" onClick={() => resolve([sel.id], "approved")}><Icon name="check" size={16} sw={2.4} />{sel.edited ? "Approve edited" : meta.verb}</button>
                    <button className="btn btn-ghost" onClick={() => editDraft(sel.id, sel.body)} disabled={!sel.edited}><Icon name="refresh" size={15} />Reset</button>
                    <button className="btn btn-ghost" style={{ marginLeft: "auto", color: "var(--rose)" }} onClick={() => setDeclineFor([sel.id])}><Icon name="x" size={16} sw={2.4} />Decline</button>
                  </div>
                )}
              </div>
            );
          })()}
        </div>
      </div>

      {declineFor && (
        <div className="cmdk-scrim show" onClick={() => setDeclineFor(null)} style={{ alignItems: "center", paddingTop: 0 }}>
          <div className="cmdk" style={{ maxWidth: 420 }} onClick={(e) => e.stopPropagation()}>
            <div style={{ padding: "18px 20px", borderBottom: "1px solid var(--line)", display: "flex", alignItems: "center", gap: 11 }}>
              <div className="gl-type-ico" style={{ background: "var(--rose-soft)", color: "oklch(0.48 0.14 18)", width: 32, height: 32 }}><Icon name="x" size={16} sw={2.4} /></div>
              <div style={{ flex: 1 }}><b style={{ fontSize: 16, fontWeight: 720 }}>Why decline?</b><div style={{ fontSize: 12, color: "var(--ink-4)" }}>Your reason teaches the agent, it won't repeat the mistake</div></div>
              <button className="icon-btn" onClick={() => setDeclineFor(null)}><Icon name="x" size={18} /></button>
            </div>
            <div style={{ padding: 18, display: "flex", flexDirection: "column", gap: 8 }}>
              {DECLINE_REASONS.map((r) => (
                <button key={r} className="btn btn-ghost" style={{ justifyContent: "flex-start", width: "100%" }} onClick={() => { window.FLStore && window.FLStore.pushFeed && window.FLStore.pushFeed({ agent: (sel && sel.agent) || "scout", ico: "spark", tone: "indigo", html: `Learned from a decline: <b>${r}</b>`, meta: "just now · Greenlight" }); resolve(declineFor, "declined"); setDeclineFor(null); }}>
                  <Icon name="bolt" size={14} style={{ color: "var(--ink-4)" }} />{r}
                </button>
              ))}
              <button className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-end", marginTop: 4 }} onClick={() => { resolve(declineFor, "declined"); setDeclineFor(null); }}>Decline without a reason</button>
            </div>
          </div>
        </div>
      )}
      {toast && (
        <div style={{ position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)", zIndex: 70,
          background: "var(--ink)", color: "var(--bg)", borderRadius: "var(--r-md)", padding: "12px 18px",
          display: "flex", alignItems: "center", gap: 10, boxShadow: "var(--shadow-xl)", animation: "feed-in .3s both" }}>
          <Icon name={toast.decision === "approved" ? "checkCircle" : "xCircle"} size={18} />
          <span style={{ fontSize: 13.5, fontWeight: 600 }}>{toast.n > 1 ? `${toast.n} actions ${toast.decision}` : (toast.decision === "approved" ? "Approved & sent" : "Declined")}</span>
        </div>
      )}
    </div>
  );
}

window.Greenlight = Greenlight;
