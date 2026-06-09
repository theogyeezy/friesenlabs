// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// cortex.jsx, Cortex: the private, compounding intelligence layer

const CX_STEPS = [
  ["target", "Predict", "An agent scores a record with the champion model"],
  ["doc", "Log", "The prediction + features + model version is saved as a decision trace"],
  ["checkCircle", "Resolve", "When the real outcome lands, it backfills the trace, a labeled example"],
  ["refresh", "Retrain", "Scheduled retrain learns from every closed loop; champion only changes if metrics improve"],
];
const CX_KNOWLEDGE = [
  ["Employee handbook", "doc"], ["SOPs & playbooks", "layers"], ["Pricing & packages", "trend"],
  ["Contracts & templates", "doc"], ["FAQs & scripts", "inbox"], ["Product docs", "spark"],
];

function Cortex({ agents, onNavigate }) {
  const [tab, setTab] = useState("knowledge");
  const [acc, setAcc] = useState(82.4);
  const [ver, setVer] = useState(3);
  const [traces, setTraces] = useState(1240);
  const [active, setActive] = useState(-1);
  const [running, setRunning] = useState(false);
  const [docs, setDocs] = useState(0);
  const [plugins, setPlugins] = useState({ flywheel: false, finetune: false });
  const [cxToast, setCxToast] = useState(null);
  const note = (m) => { setCxToast(m); setTimeout(() => setCxToast(null), 2400); };
  const activatePlugin = (id, name) => { setPlugins((p) => ({ ...p, [id]: true })); note(`${name} plugin activated · added to your plan`); };

  const runCycle = useCallback(() => {
    if (running) return; setRunning(true);
    let s = 0;
    const tick = () => {
      setActive(s); s++;
      if (s <= 3) setTimeout(tick, 520);
      else setTimeout(() => {
        setActive(-1); setRunning(false);
        setAcc((a) => Math.min(96.5, +(a + (Math.random() * 1.3 + 0.5)).toFixed(1)));
        setVer((v) => v + 1);
        setTraces((t) => t + Math.floor(Math.random() * 40 + 20));
        note("Retrain complete, champion model promoted");
      }, 460);
    };
    tick();
  }, [running]);

  return (
    <div className="screen screen-anim">
      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 7 }}>Your private intelligence layer</div>
          <h2 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.03em" }}>Cortex</h2>
          <p style={{ color: "var(--ink-2)", fontSize: 14.5, marginTop: 5, maxWidth: 600 }}>
            Your private intelligence layer. <b>Knowledge</b> is included, ground every agent on what your business knows. Add the <b>Flywheel</b> and <b>Fine-tuning</b> plugins to compound and train private models.
          </p>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 9 }}>
          <button className="btn btn-primary" onClick={() => onNavigate && onNavigate("integrations")}><Icon name="plug" size={16} />Connect your data</button>
        </div>
      </div>

      <div className="seg" style={{ marginBottom: "var(--gap)" }}>
        {[["knowledge", "Knowledge", "doc", false], ["flywheel", "Flywheel", "refresh", true], ["finetune", "Fine-tuning", "network", true]].map(([id, label, ic, plugin]) => (
          <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}><Icon name={ic} size={15} />{label}{plugin && <span className="chip" style={{ height: 16, fontSize: 9, padding: "0 5px", marginLeft: 5, background: "var(--amber-soft)", color: "oklch(0.5 0.12 60)" }}>{plugins[id] ? "on" : "plugin"}</span>}</button>
        ))}
      </div>

      {tab === "flywheel" && (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 13, padding: "13px 16px", borderRadius: "var(--r-md)", border: "1px solid " + (plugins.flywheel ? "var(--green)" : "var(--amber-soft)"), background: plugins.flywheel ? "var(--green-soft)" : "var(--amber-soft)", marginBottom: "var(--gap)" }}>
            <Icon name={plugins.flywheel ? "checkCircle" : "refresh"} size={18} style={{ color: plugins.flywheel ? "oklch(0.42 0.12 152)" : "oklch(0.5 0.12 60)", flexShrink: 0 }} />
            <div style={{ flex: 1, fontSize: 13, color: plugins.flywheel ? "oklch(0.36 0.1 152)" : "oklch(0.46 0.11 60)", lineHeight: 1.45 }}>
              <b>Flywheel plugin{plugins.flywheel ? " · active" : " · $49/mo"}</b> {plugins.flywheel ? "Compounding on every closed loop." : "A Cortex add-on that turns every decision into private, compounding model gains."}
            </div>
            {!plugins.flywheel && <button className="btn btn-primary btn-sm" onClick={() => activatePlugin("flywheel", "Flywheel")}><Icon name="plus" size={13} sw={2.2} />Add plugin</button>}
          </div>
        <div className="dash-grid">
          <div className="card">
            <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--amber-soft)", color: "oklch(0.5 0.12 60)" }}><Icon name="refresh" size={15} /></div><h3>The flywheel</h3><span className="sub" style={{ marginLeft: "auto" }}>model v{ver}</span></div>
            <div className="card-pad" style={{ display: "flex", flexDirection: "column", gap: 13 }}>
              <div style={{ background: "var(--surface-2)", border: "1px solid var(--line-2)", borderRadius: "var(--r-md)", padding: 15 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", marginBottom: 8 }}>
                  <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--ink-2)" }}>Champion accuracy</span>
                  <span style={{ fontSize: 26, fontWeight: 800, letterSpacing: "-.03em", color: "var(--green)" }}>{acc}%</span>
                </div>
                <div className="meter" style={{ height: 9 }}><span style={{ width: acc + "%", background: "var(--green)" }} /></div>
                <p style={{ fontSize: 11.5, color: "var(--ink-4)", marginTop: 7 }}>{traces.toLocaleString()} decision traces · climbs every closed loop</p>
                <div style={{ display: "flex", alignItems: "flex-end", gap: 4, height: 40, marginTop: 12 }}>
                  {[71, 74, 76, 78, 79, 81, acc].map((v, i, arr) => (
                    <div key={i} style={{ flex: 1, height: ((v - 65) / 25 * 100) + "%", borderRadius: "3px 3px 0 0", background: i === arr.length - 1 ? "var(--green)" : "var(--green-soft)" }} />
                  ))}
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10.5, color: "var(--ink-4)", fontFamily: "var(--mono)", marginTop: 5 }}><span>accuracy, 7 retrains</span><span>last retrained 2d ago · v{ver}</span></div>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
                {CX_STEPS.map(([ic, t, d], i) => (
                  <div key={t} style={{ display: "flex", gap: 11, alignItems: "center", padding: "11px 13px", borderRadius: "var(--r-sm)", border: "1.5px solid " + (active === i ? "var(--accent)" : "var(--line)"), background: active === i ? "var(--accent-softer)" : "var(--surface)", boxShadow: active === i ? "0 0 0 3px var(--accent-soft)" : "none", transition: "all .2s" }}>
                    <div style={{ width: 28, height: 28, borderRadius: 99, border: "1.5px solid " + (active === i ? "var(--accent)" : "var(--line)"), display: "grid", placeItems: "center", fontSize: 12, fontWeight: 700, fontFamily: "var(--mono)", color: active === i ? "var(--accent-ink)" : "var(--ink-3)", flexShrink: 0 }}>{i + 1}</div>
                    <div style={{ minWidth: 0 }}><b style={{ fontSize: 13, fontWeight: 650 }}>{t}</b><span style={{ display: "block", fontSize: 11.5, color: "var(--ink-3)", lineHeight: 1.35 }}>{d}</span></div>
                  </div>
                ))}
              </div>
              <button className="btn btn-primary" onClick={runCycle} disabled={running}><Icon name="refresh" size={15} className={running ? "spin" : ""} />{running ? "Running cycle…" : "Run a cycle"}</button>
            </div>
          </div>
          <div className="card" style={{ alignSelf: "start" }}>
            <div className="card-head"><h3>Why it compounds</h3></div>
            <div className="card-pad">
              <p style={{ fontSize: 13.5, color: "var(--ink-2)", lineHeight: 1.6 }}>Every record your agents run becomes a labeled example no competitor has. Models retrain on that growing history, so they get sharper precisely where <i>your</i> business is.</p>
              <div style={{ display: "flex", alignItems: "center", gap: 11, marginTop: 16, padding: "14px 16px", background: "var(--accent-softer)", borderRadius: "var(--r-md)" }}>
                <Icon name="network" size={20} style={{ color: "var(--accent-ink)", flexShrink: 0 }} />
                <p style={{ fontSize: 13, color: "var(--accent-ink)", lineHeight: 1.5 }}><b style={{ fontWeight: 700 }}>That accumulated decision history is the moat</b>, it can't be exported or rebuilt anywhere else. Data gravity.</p>
              </div>
            </div>
          </div>
        </div>
        </>
      )}

      {tab === "knowledge" && (
        <div className="card">
          <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="doc" size={15} /></div><h3>Knowledge</h3><span className="sub" style={{ marginLeft: "auto" }}>now its own product</span></div>
          <div className="card-pad">
            <p style={{ fontSize: 13.5, color: "var(--ink-2)", lineHeight: 1.55, marginBottom: 15 }}>Your hosted knowledge bases, handbook, SOPs, pricing, help center, are managed in the <b>Knowledge</b> product now, and Cortex grounds every model and agent on them. Upload once, and it becomes context everywhere.</p>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 9, marginBottom: 16 }}>
              {CX_KNOWLEDGE.map(([n, ic]) => (
                <span key={n} className="filter-pill" style={{ cursor: "default" }}><Icon name={ic} size={13} />{n}</span>
              ))}
            </div>
            <button className="btn btn-primary" onClick={() => onNavigate && onNavigate("knowledge")}><Icon name="doc" size={15} />Open Knowledge</button>
          </div>
        </div>
      )}

      {tab === "finetune" && (
        <div className="card">
          <div style={{ display: "flex", alignItems: "center", gap: 13, padding: "13px 16px", borderBottom: "1px solid var(--line)", background: plugins.finetune ? "var(--green-soft)" : "var(--amber-soft)" }}>
            <Icon name={plugins.finetune ? "checkCircle" : "network"} size={18} style={{ color: plugins.finetune ? "oklch(0.42 0.12 152)" : "oklch(0.5 0.12 60)", flexShrink: 0 }} />
            <div style={{ flex: 1, fontSize: 13, color: plugins.finetune ? "oklch(0.36 0.1 152)" : "oklch(0.46 0.11 60)", lineHeight: 1.45 }}>
              <b>Fine-tuning plugin{plugins.finetune ? " · active" : " · $99/mo"}</b> {plugins.finetune ? "Private model training is unlocked." : "A Cortex add-on to train and host private models on your own data."}
            </div>
            {!plugins.finetune && <button className="btn btn-primary btn-sm" onClick={() => activatePlugin("finetune", "Fine-tuning")}><Icon name="plus" size={13} sw={2.2} />Add plugin</button>}
          </div>
          <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--amber-soft)", color: "oklch(0.5 0.12 60)" }}><Icon name="network" size={15} /></div><h3>Fine-tune a private model</h3></div>
          <div className="card-pad">
            <p style={{ fontSize: 13.5, color: "var(--ink-2)", lineHeight: 1.55, marginBottom: 16 }}>Turn your own data into a private model that runs on your hardware. Agents inspect your table, pick a right-sized open base model, quantize it to fit your card, and hand you a deploy plan. Planning is free and read-only, nothing trains or sends until you connect a GPU.</p>
            <div className="rg5" style={{ marginBottom: 18 }}>
              {[["spark", "Pull your data", "the table you connected"], ["target", "Agents inspect it", "shape, columns, task"], ["network", "Pick a base model", "right size for the job"], ["bolt", "Quantize", "GGUF / int8 to fit"], ["check", "Deploy", "private, on your hardware"]].map(([ic, t, d], i) => (
                <div key={t} style={{ background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: "var(--r-md)", padding: 13 }}>
                  <div style={{ width: 30, height: 30, borderRadius: 8, background: "var(--surface)", color: "var(--accent-ink)", display: "grid", placeItems: "center", marginBottom: 9 }}><Icon name={ic} size={15} /></div>
                  <b style={{ fontSize: 12.5, fontWeight: 650, display: "block" }}>{t}</b>
                  <span style={{ fontSize: 11, color: "var(--ink-3)" }}>{d}</span>
                </div>
              ))}
            </div>
            <button className="btn btn-primary" onClick={() => note("Planning your model, free & read-only, nothing trains yet")}><Icon name="spark" size={15} />Plan my model</button>
            <span style={{ fontSize: 12, color: "var(--ink-4)", marginLeft: 12 }}>Free · read-only · no training runs yet</span>
          </div>
        </div>
      )}

      {cxToast && (
        <div style={{ position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)", zIndex: 70, background: "var(--ink)", color: "var(--bg)", borderRadius: "var(--r-md)", padding: "12px 18px", display: "flex", alignItems: "center", gap: 10, boxShadow: "var(--shadow-xl)", animation: "feed-in .3s both", maxWidth: "90vw" }}>
          <Icon name="checkCircle" size={18} /><span style={{ fontSize: 13.5, fontWeight: 600 }}>{cxToast}</span>
        </div>
      )}
    </div>
  );
}

window.Cortex = Cortex;
