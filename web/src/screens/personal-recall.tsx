// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// personal-recall.jsx — premium add-on: private semantic recall over your message history

const RECALL_STAGES = ["Reading your messages", "Organizing conversations", "Embedding for recall", "Building your private index", "Ready"];
const RECALL_PRIVACY = [
  ["lock", "Stored encrypted in your private, single-tenant instance", "Never pooled with other customers."],
  ["spark", "Embedded for your recall only", "Never used to train shared models, never sold, never shared."],
  ["shield", "Processed inside your own workspace", "Export it or permanently delete it anytime, in one click."],
  ["users", "Only you and the agents you allow can query it", "You decide who gets access, down to each agent."],
];

// ---- the locked add-on card (Knowledge grid + Cortex tile) ----
function RecallAddonCard({ onAdd, compact }) {
  return (
    <div className="card recall-card" style={{ padding: compact ? 18 : 20, position: "relative", overflow: "hidden" }}>
      <div className="recall-glow" />
      <div style={{ position: "relative" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
          <div className="feed-ico" style={{ width: 40, height: 40, background: "linear-gradient(135deg, var(--accent), color-mix(in oklch, var(--accent) 60%, #000))", color: "#fff", borderRadius: 13 }}><Icon name="spark" size={20} /></div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
              <b style={{ fontSize: 15.5, fontWeight: 740, letterSpacing: "-.02em" }}>Personal Recall</b>
              <span className="chip" style={{ height: 19, fontSize: 9.5, background: "var(--accent-soft)", color: "var(--accent-ink)", textTransform: "uppercase", letterSpacing: ".04em", fontWeight: 700 }}>Add-on</span>
            </div>
            <span style={{ fontSize: 11.5, color: "var(--ink-4)" }}>Your private second brain</span>
          </div>
          <Icon name="lock" size={17} style={{ color: "var(--ink-4)" }} />
        </div>
        <p style={{ fontSize: 13.5, color: "var(--ink-2)", lineHeight: 1.55, marginTop: 13 }}>Give your assistant total recall of your conversations. Upload your message history and search your whole life in plain language.</p>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginTop: 15 }}>
          <span style={{ fontSize: 24, fontWeight: 780, letterSpacing: "-.03em" }}>$149</span><span style={{ fontSize: 13, color: "var(--ink-3)" }}>/mo</span>
          <span style={{ fontSize: 12, color: "var(--ink-4)" }}>+ one-time indexing</span>
        </div>
        <button className="btn btn-primary" style={{ width: "100%", marginTop: 14 }} onClick={onAdd}><Icon name="spark" size={15} />Add Personal Recall</button>
      </div>
    </div>
  );
}

// ---- result KB card once indexed ----
function RecallResultCard({ recall, onOpen }) {
  const src = recall.sources[0] || {};
  return (
    <button className="card kb-card recall-result" onClick={onOpen}>
      <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
        <div className="feed-ico" style={{ width: 38, height: 38, background: "linear-gradient(135deg, var(--accent), color-mix(in oklch, var(--accent) 60%, #000))", color: "#fff" }}><Icon name="spark" size={19} /></div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <b style={{ fontSize: 14.5, fontWeight: 700, display: "block" }}>Personal Recall</b>
          <span style={{ fontSize: 11.5, color: "var(--ink-4)" }}>updated just now</span>
        </div>
        <span className="chip" style={{ height: 20, fontSize: 10, background: "var(--green-soft)", color: "oklch(0.42 0.12 152)", gap: 4 }}><Icon name="lock" size={10} />Private · encrypted</span>
      </div>
      <div style={{ display: "flex", gap: 16, marginTop: 14 }}>
        <div><div style={{ fontSize: 18, fontWeight: 760, letterSpacing: "-.02em" }}>{(src.messages || 0).toLocaleString()}</div><div style={{ fontSize: 11, color: "var(--ink-4)" }}>messages</div></div>
        <div><div style={{ fontSize: 18, fontWeight: 760, letterSpacing: "-.02em" }}>{(src.memories || 0).toLocaleString()}</div><div style={{ fontSize: 11, color: "var(--ink-4)" }}>memories</div></div>
        <div><div style={{ fontSize: 18, fontWeight: 760, letterSpacing: "-.02em" }}>{src.conversations || 0}</div><div style={{ fontSize: 11, color: "var(--ink-4)" }}>conversations</div></div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 7, marginTop: 13, paddingTop: 12, borderTop: "1px solid var(--line-2)", fontSize: 12, color: "var(--accent-ink)", fontWeight: 600 }}>
        <Icon name="search" size={13} />Ask your memory anything
      </div>
    </button>
  );
}

// ---- add / upload / processing flow ----
function RecallFlow({ onClose }) {
  const recall = useStore((s) => s.recall);
  const [phase, setPhase] = useState(recall.added ? "upload" : "pitch");
  const [file, setFile] = useState(false);
  const [drag, setDrag] = useState(false);

  return (
    <div className="cmdk-scrim show" onClick={onClose} style={{ alignItems: "center", paddingTop: 0, zIndex: 110 }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: "min(680px, 95vw)", maxHeight: "92vh", overflowY: "auto", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-xl)", boxShadow: "var(--shadow-xl)", animation: "onb-in .3s both" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "18px 22px", borderBottom: "1px solid var(--line)" }}>
          <div className="feed-ico" style={{ width: 34, height: 34, background: "linear-gradient(135deg, var(--accent), color-mix(in oklch, var(--accent) 60%, #000))", color: "#fff" }}><Icon name="spark" size={17} /></div>
          <div style={{ flex: 1 }}><b style={{ fontSize: 16.5, fontWeight: 730, letterSpacing: "-.02em" }}>Personal Recall</b><div style={{ fontSize: 12, color: "var(--ink-3)" }}>Your private second brain</div></div>
          <button className="icon-btn" onClick={onClose}><Icon name="x" size={18} /></button>
        </div>

        {phase === "pitch" && (
          <div style={{ padding: 24 }}>
            <h2 style={{ fontSize: 22, fontWeight: 760, letterSpacing: "-.03em", lineHeight: 1.2 }}>Give your assistant total recall of your conversations</h2>
            <p style={{ fontSize: 14, color: "var(--ink-2)", lineHeight: 1.6, marginTop: 10 }}>Upload your message history and we privately embed it into a searchable index. Ask things like "what did Matt say about the Chicago trip?" or "when did I last talk to my landlord?", and your assistant answers with full personal context.</p>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 18 }}>
              {[["search", "Total semantic recall", "Every conversation, searchable in plain language"], ["spark", "Grounds your assistant", "It answers with your real history, not guesses"], ["lock", "Private by design", "Single-tenant, encrypted, never shared"], ["layers", "Add more later", "Slack export, email and more, same private flow"]].map(([ic, t, d]) => (
                <div key={t} style={{ display: "flex", gap: 10, padding: "12px 13px", border: "1px solid var(--line)", borderRadius: "var(--r-md)" }}>
                  <Icon name={ic} size={17} style={{ color: "var(--accent-ink)", flexShrink: 0, marginTop: 1 }} />
                  <div><b style={{ fontSize: 12.5, fontWeight: 680, display: "block" }}>{t}</b><span style={{ fontSize: 11.5, color: "var(--ink-3)", lineHeight: 1.4 }}>{d}</span></div>
                </div>
              ))}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 14, marginTop: 20, padding: "16px 18px", background: "var(--accent-softer)", borderRadius: "var(--r-md)", flexWrap: "wrap" }}>
              <div><div style={{ fontSize: 26, fontWeight: 800, letterSpacing: "-.03em" }}>$149<span style={{ fontSize: 14, fontWeight: 500, color: "var(--ink-3)" }}>/mo</span></div><div style={{ fontSize: 12, color: "var(--ink-3)" }}>flagship add-on</div></div>
              <div style={{ width: 1, alignSelf: "stretch", background: "var(--line)" }} />
              <div><div style={{ fontSize: 26, fontWeight: 800, letterSpacing: "-.03em" }}>$299</div><div style={{ fontSize: 12, color: "var(--ink-3)" }}>one-time indexing</div></div>
              <button className="btn btn-primary btn-lg" style={{ marginLeft: "auto" }} onClick={() => { FLStore.addPersonalRecall(); setPhase("upload"); }}><Icon name="spark" size={16} />Add Personal Recall</button>
            </div>
          </div>
        )}

        {phase === "upload" && (
          <div style={{ padding: 22, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 }}>
            <div>
              <div className="ad-sec-label"><Icon name="layers" size={14} />Upload your messages</div>
              <div className={"recall-drop" + (drag ? " drag" : "") + (file ? " has" : "")}
                onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
                onDrop={(e) => { e.preventDefault(); setDrag(false); setFile(true); }} onClick={() => setFile(true)}>
                {file ? (
                  <><div className="feed-ico" style={{ width: 38, height: 38, background: "var(--green-soft)", color: "oklch(0.42 0.12 152)", margin: "0 auto 10px" }}><Icon name="checkCircle" size={19} /></div>
                  <b style={{ fontSize: 13.5 }}>chat.db</b><div style={{ fontSize: 11.5, color: "var(--ink-4)", marginTop: 2 }}>1.4 GB · ready to process</div></>
                ) : (
                  <><div className="es-ico" style={{ margin: "0 auto 10px" }}><Icon name="spark" size={22} /></div>
                  <b style={{ fontSize: 13.5 }}>Drop your <span style={{ fontFamily: "var(--mono)" }}>chat.db</span> here</b>
                  <div style={{ fontSize: 11.5, color: "var(--ink-4)", marginTop: 4, lineHeight: 1.45 }}>or click to choose · your iMessage database</div></>
                )}
              </div>
              <details style={{ marginTop: 12 }}>
                <summary style={{ fontSize: 12, color: "var(--accent-ink)", cursor: "pointer", fontWeight: 600 }}>Where do I find chat.db?</summary>
                <p style={{ fontSize: 11.5, color: "var(--ink-3)", lineHeight: 1.5, marginTop: 7 }}>On your Mac, open Finder and press Cmd+Shift+G, then paste <span style={{ fontFamily: "var(--mono)", background: "var(--surface-2)", padding: "1px 5px", borderRadius: 4 }}>~/Library/Messages</span>. The file named <span style={{ fontFamily: "var(--mono)" }}>chat.db</span> is your message history.</p>
              </details>
              <button className="btn btn-primary" style={{ width: "100%", marginTop: 14 }} disabled={!file} onClick={() => setPhase("processing")}><Icon name="spark" size={15} />Process privately</button>
            </div>

            <div className="recall-privacy">
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 13 }}>
                <Icon name="shield" size={17} style={{ color: "var(--green)" }} />
                <b style={{ fontSize: 14, fontWeight: 720 }}>Your privacy</b>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 13 }}>
                {RECALL_PRIVACY.map(([ic, t, d]) => (
                  <div key={t} style={{ display: "flex", gap: 10 }}>
                    <div style={{ width: 26, height: 26, borderRadius: 8, background: "var(--green-soft)", color: "oklch(0.42 0.12 152)", display: "grid", placeItems: "center", flexShrink: 0 }}><Icon name={ic} size={14} /></div>
                    <div><b style={{ fontSize: 12.5, fontWeight: 650, display: "block", lineHeight: 1.35 }}>{t}</b><span style={{ fontSize: 11.5, color: "var(--ink-3)", lineHeight: 1.4 }}>{d}</span></div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {phase === "processing" && <div style={{ padding: 28 }}><RecallProcessing onDone={() => setPhase("done")} /></div>}

        {phase === "done" && (
          <div style={{ padding: "32px 24px", textAlign: "center" }}>
            <div className="lp-prov-check" style={{ width: 58, height: 58, borderRadius: 17, margin: "0 auto" }}><Icon name="check" size={29} sw={2.6} style={{ color: "#fff" }} /></div>
            <h2 style={{ fontSize: 21, fontWeight: 760, marginTop: 15 }}>Your private index is ready</h2>
            <p style={{ fontSize: 13.5, color: "var(--ink-2)", marginTop: 7 }}>Indexed 14 conversations · 82,000 messages · 78,000 searchable memories.</p>
            <button className="btn btn-primary btn-lg" style={{ marginTop: 20 }} onClick={onClose}><Icon name="search" size={16} />Start recalling</button>
          </div>
        )}
      </div>
    </div>
  );
}

function RecallProcessing({ onDone }) {
  const [stage, setStage] = useState(0);
  useEffect(() => {
    if (stage >= RECALL_STAGES.length) {
      FLStore.indexPersonalRecall({ messages: 82000, memories: 78000, conversations: 14, chunks: 78000 });
      const t = setTimeout(onDone, 500); return () => clearTimeout(t);
    }
    const t = setTimeout(() => setStage((s) => s + 1), 640);
    return () => clearTimeout(t);
  }, [stage]);
  return (
    <div>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 16, display: "flex", alignItems: "center", gap: 8 }}><Icon name="refresh" size={15} className="spin" />Building your private second brain…</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 11 }}>
        {RECALL_STAGES.map((s, i) => (
          <div key={s} style={{ display: "flex", alignItems: "center", gap: 11, fontSize: 13.5, color: i <= stage ? "var(--ink)" : "var(--ink-4)" }}>
            <span style={{ width: 22, height: 22, borderRadius: 99, display: "grid", placeItems: "center", background: i < stage ? "var(--green)" : i === stage ? "var(--accent)" : "var(--surface-2)", color: i <= stage ? "#fff" : "var(--ink-4)", flexShrink: 0 }}>
              {i < stage ? <Icon name="check" size={12} sw={3} /> : i === stage ? <span className="live-dot" style={{ width: 7, height: 7, background: "#fff" }} /> : i + 1}
            </span>
            {s}
          </div>
        ))}
      </div>
    </div>
  );
}

// ---- ask your memory search ----
function RecallSearch({ onClose }) {
  const { RECALL_HITS } = window.FL_DATA;
  const [q, setQ] = useState("");
  const [results, setResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const chips = ["what did I promise Allie last week?", "find that restaurant Matt recommended", "when did I last talk to my landlord?"];
  const run = (text) => {
    const body = (text || q).trim(); if (!body) return; setQ(body); setSearching(true); setResults(null);
    setTimeout(() => {
      const t = body.toLowerCase();
      let hits = RECALL_HITS.filter((h) => (h.who + h.text + h.tag).toLowerCase().split(/\W+/).some((w) => w && t.includes(w) && w.length > 3));
      if (hits.length === 0) hits = RECALL_HITS.slice(0, 3);
      setResults(hits); setSearching(false);
    }, 650);
  };
  return (
    <div className="cmdk-scrim show" onClick={onClose} style={{ alignItems: "stretch", justifyContent: "flex-end", paddingTop: 0, zIndex: 110 }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: "min(560px, 96vw)", height: "100%", background: "var(--surface)", borderLeft: "1px solid var(--line)", boxShadow: "var(--shadow-xl)", display: "flex", flexDirection: "column", animation: "slide-in .25s both" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "16px 20px", borderBottom: "1px solid var(--line)" }}>
          <div className="feed-ico" style={{ width: 36, height: 36, background: "linear-gradient(135deg, var(--accent), color-mix(in oklch, var(--accent) 60%, #000))", color: "#fff" }}><Icon name="spark" size={18} /></div>
          <div style={{ flex: 1, minWidth: 0 }}><b style={{ fontSize: 16, fontWeight: 720 }}>Ask your memory</b><div style={{ fontSize: 12, color: "var(--ink-3)", display: "flex", alignItems: "center", gap: 5 }}><Icon name="lock" size={11} />Private · 78,000 searchable memories</div></div>
          <button className="icon-btn" onClick={onClose}><Icon name="x" size={18} /></button>
        </div>
        <div style={{ padding: 20, flex: 1, overflowY: "auto" }}>
          <div className="search-trigger" style={{ cursor: "text" }}>
            <Icon name="search" size={15} />
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") run(); }} placeholder="Search your whole conversation history…" style={{ border: "none", outline: "none", background: "none", flex: 1, fontSize: 13.5, color: "var(--ink)" }} />
            <button className="btn btn-primary btn-sm" onClick={() => run()} disabled={searching || !q.trim()}>{searching ? "…" : "Recall"}</button>
          </div>
          {!results && !searching && (
            <div style={{ marginTop: 14 }}>
              <div style={{ fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)", textTransform: "uppercase", letterSpacing: ".05em", marginBottom: 9 }}>Try asking</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 7 }}>
                {chips.map((c) => <button key={c} className="sugg" onClick={() => run(c)}>{c}</button>)}
              </div>
            </div>
          )}
          {searching && <div style={{ marginTop: 18, fontSize: 13, color: "var(--ink-3)", display: "flex", alignItems: "center", gap: 8 }}><Icon name="refresh" size={15} className="spin" />Searching your memories…</div>}
          {results && (
            <div style={{ marginTop: 16, animation: "feed-in .3s both" }}>
              <div style={{ fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)", marginBottom: 9 }}>{results.length} MEMORIES FOUND</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {results.map((h, i) => (
                  <div key={i} style={{ border: "1px solid var(--line-2)", borderRadius: "var(--r-md)", padding: "13px 15px", background: "var(--surface)" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 7 }}>
                      <div className="avatar" style={{ background: "var(--accent)", width: 24, height: 24, fontSize: 10 }}>{h.who.split(" ").map((w) => w[0]).slice(0, 2).join("")}</div>
                      <b style={{ fontSize: 13, fontWeight: 650 }}>{h.who}</b>
                      <span className="chip" style={{ height: 17, fontSize: 9.5, padding: "0 7px" }}>{h.tag}</span>
                      <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>{h.date}</span>
                    </div>
                    <p style={{ fontSize: 13.5, color: "var(--ink)", lineHeight: 1.55 }}>“{h.text}”</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

window.RecallAddonCard = RecallAddonCard;
window.RecallResultCard = RecallResultCard;
window.RecallFlow = RecallFlow;
window.RecallSearch = RecallSearch;
