// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// templates.jsx — Email/SMS templates & multi-step sequences

// derived performance per template (deterministic from id+uses)
function tplPerf(t) {
  const seed = (t.id || "").split("").reduce((n, c) => n + c.charCodeAt(0), t.uses || 0);
  const open = t.channel === "SMS" ? 92 - (seed % 7) : 48 + (seed % 22);
  const reply = 18 + (seed % 19);
  const conv = 6 + (seed % 11);
  return { open, reply, conv, ab: seed % 3 === 0 };
}

function Templates({ agents, onNavigate }) {
  const templates = useStore((s) => s.emailTemplates);
  const sequences = useStore((s) => s.sequences);
  const [tab, setTab] = useState("templates");
  const [toast, setToast] = useState(null);
  const [neu, setNeu] = useState(false);
  const [edit, setEdit] = useState(null);
  const note = (m) => { setToast(m); setTimeout(() => setToast(null), 2600); };

  return (
    <div className="screen screen-anim">
      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 7 }}>Outreach, ready to send</div>
          <h2 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.03em" }}>Templates</h2>
          <p style={{ color: "var(--ink-2)", fontSize: 14.5, marginTop: 5 }}>Saved email &amp; SMS templates and multi-step sequences your agents personalize and send.</p>
        </div>
        <div style={{ marginLeft: "auto" }}>
          {tab === "templates" && <button className="btn btn-primary" onClick={() => setNeu(true)}><Icon name="plus" size={16} sw={2.2} />New template</button>}
        </div>
      </div>

      <div className="seg" style={{ marginBottom: "var(--gap)" }}>
        <button className={tab === "templates" ? "active" : ""} onClick={() => setTab("templates")}><Icon name="note" size={15} />Templates <span style={{ opacity: .6, fontFamily: "var(--mono)", fontSize: 11 }}>{templates.length}</span></button>
        <button className={tab === "sequences" ? "active" : ""} onClick={() => setTab("sequences")}><Icon name="workflow" size={15} />Sequences <span style={{ opacity: .6, fontFamily: "var(--mono)", fontSize: 11 }}>{sequences.length}</span></button>
      </div>

      {tab === "templates" ? (
        <div className="kb-grid">
          {templates.map((t) => { const p = tplPerf(t); return (
            <div className="card" key={t.id} style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
                <div className="feed-ico" style={{ width: 30, height: 30, background: t.channel === "SMS" ? "var(--green-soft)" : "var(--accent-soft)", color: t.channel === "SMS" ? "oklch(0.42 0.12 152)" : "var(--accent-ink)" }}><Icon name={t.channel === "SMS" ? "phone" : "mail"} size={15} /></div>
                <div style={{ flex: 1, minWidth: 0 }}><b style={{ fontSize: 13.5, fontWeight: 680, display: "flex", alignItems: "center", gap: 6 }}>{t.name}{p.ab && <span className="chip" style={{ height: 16, fontSize: 9, padding: "0 5px", background: "var(--accent-soft)", color: "var(--accent-ink)" }}>A/B</span>}</b><div style={{ fontSize: 11, color: "var(--ink-4)" }}>{t.channel} · used {t.uses}×</div></div>
              </div>
              <p style={{ fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.5, background: "var(--surface-2)", borderRadius: "var(--r-sm)", padding: "10px 12px", flex: 1 }}>{t.body}</p>
              <div style={{ display: "flex", gap: 6 }}>
                {[["Open", p.open], ["Reply", p.reply], ["Conv", p.conv]].map(([l, v]) => (
                  <div key={l} style={{ flex: 1, textAlign: "center", padding: "7px 4px", background: "var(--surface-2)", borderRadius: "var(--r-sm)" }}>
                    <div style={{ fontSize: 14, fontWeight: 740, fontFamily: "var(--mono)", color: l === "Conv" ? "var(--green)" : "var(--ink)" }}>{v}%</div>
                    <div style={{ fontSize: 9.5, color: "var(--ink-4)", textTransform: "uppercase", letterSpacing: ".04em" }}>{l}</div>
                  </div>
                ))}
              </div>
              {p.ab && <div style={{ fontSize: 11, color: "var(--ink-4)", display: "flex", alignItems: "center", gap: 5 }}><Icon name="spark" size={11} style={{ color: "var(--accent-ink)" }} />Variant B winning by {2 + (t.uses % 5)}% reply rate</div>}
              <div style={{ display: "flex", gap: 7 }}>
                <button className="btn btn-soft btn-sm" onClick={() => note(t.name + " queued, your agent will personalize each {field}")}><Icon name="send" size={13} />Use</button>
                <button className="btn btn-ghost btn-sm" onClick={() => setEdit(t)}><Icon name="note" size={13} />Edit</button>
              </div>
            </div>
          ); })}
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 11 }}>
          {sequences.map((s) => (
            <div className="card" key={s.id} style={{ padding: 16 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                <div className="feed-ico" style={{ width: 34, height: 34, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="workflow" size={16} /></div>
                <div style={{ flex: 1, minWidth: 160 }}>
                  <b style={{ fontSize: 14.5, fontWeight: 700 }}>{s.name}</b>
                  <div style={{ fontSize: 12, color: "var(--ink-4)" }}>{s.steps} steps · {s.enrolled} enrolled</div>
                </div>
                <button className={"chip " + (s.active ? "green" : "")} style={{ height: 26, cursor: "pointer", border: s.active ? "none" : "1px solid var(--line)" }} onClick={() => { FLStore.toggleSequence(s.id); note(s.active ? s.name + " paused" : s.name + " activated"); }}>
                  <span className="cdot" style={{ background: s.active ? "var(--green)" : "var(--ink-4)" }} />{s.active ? "Active" : "Paused"}
                </button>
              </div>
              <p style={{ fontSize: 12.5, color: "var(--ink-2)", marginTop: 10 }}>{s.desc}</p>
            </div>
          ))}
        </div>
      )}

      {neu && <NewTemplateModal onClose={() => setNeu(false)} onNote={note} />}
      {edit && <NewTemplateModal edit={edit} onClose={() => setEdit(null)} onNote={note} />}
      {toast && (
        <div style={{ position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)", zIndex: 70, background: "var(--ink)", color: "var(--bg)", borderRadius: "var(--r-md)", padding: "12px 18px", display: "flex", alignItems: "center", gap: 10, boxShadow: "var(--shadow-xl)", animation: "feed-in .3s both", maxWidth: "90vw" }}>
          <Icon name="checkCircle" size={18} /><span style={{ fontSize: 13.5, fontWeight: 600 }}>{toast}</span>
        </div>
      )}
    </div>
  );
}

function NewTemplateModal({ onClose, onNote, edit }) {
  const [name, setName] = useState(edit ? edit.name : "");
  const [channel, setChannel] = useState(edit ? edit.channel : "Email");
  const [body, setBody] = useState(edit ? edit.body : "");
  return (
    <div className="cmdk-scrim show" onClick={onClose} style={{ alignItems: "center", paddingTop: 0 }}>
      <div className="cmdk" style={{ maxWidth: 460 }} onClick={(e) => e.stopPropagation()}>
        <div style={{ padding: "18px 20px", borderBottom: "1px solid var(--line)", display: "flex", alignItems: "center", gap: 11 }}>
          <div className="feed-ico" style={{ width: 32, height: 32, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="note" size={16} /></div>
          <b style={{ fontSize: 16, fontWeight: 720, flex: 1 }}>{edit ? "Edit template" : "New template"}</b>
          <button className="icon-btn" onClick={onClose}><Icon name="x" size={18} /></button>
        </div>
        <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 13 }}>
          <div className="wf-field"><label>Name</label><input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Renewal nudge" /></div>
          <div className="wf-field"><label>Channel</label><div className="seg" style={{ width: "fit-content" }}><button className={channel === "Email" ? "active" : ""} onClick={() => setChannel("Email")}>Email</button><button className={channel === "SMS" ? "active" : ""} onClick={() => setChannel("SMS")}>SMS</button></div></div>
          <div className="wf-field"><label>Body <span style={{ color: "var(--ink-4)", fontWeight: 400 }}>· use {"{first}"}, {"{company}"} for merge fields</span></label><textarea value={body} onChange={(e) => setBody(e.target.value)} rows={4} placeholder="Hi {first}, …" style={{ width: "100%", resize: "vertical", border: "1px solid var(--line)", borderRadius: "var(--r-sm)", padding: "10px 12px", fontSize: 13, lineHeight: 1.5, background: "var(--bg)", color: "var(--ink)", fontFamily: "inherit", outline: "none" }} /></div>
          <button className="btn btn-primary" disabled={!name.trim() || !body.trim()} onClick={() => { if (edit) { FLStore.updateTemplate(edit.id, { name: name.trim(), channel, body: body.trim() }); onNote && onNote("Template updated"); } else { FLStore.addTemplate({ name: name.trim(), channel, body: body.trim() }); onNote && onNote("Template saved"); } onClose(); }}><Icon name="check" size={16} sw={2.2} />{edit ? "Save changes" : "Save template"}</button>
        </div>
      </div>
    </div>
  );
}

window.Templates = Templates;
