// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// knowledge.jsx — standalone Knowledge product: hosted, RAG-indexed knowledge bases that ground every product

const KB_TONES = { indigo: ["var(--accent-soft)", "var(--accent-ink)"], amber: ["var(--amber-soft)", "oklch(0.5 0.12 60)"], green: ["var(--green-soft)", "oklch(0.42 0.12 152)"], rose: ["var(--rose-soft)", "oklch(0.48 0.14 18)"] };
const kbTone = (t) => KB_TONES[t] || KB_TONES.indigo;
const SRC_ICO = { pdf: "doc", doc: "doc", csv: "sheets", md: "doc", zip: "layers", txt: "doc", url: "plug" };
const kbChunks = (kb) => kb.sources.reduce((n, s) => n + (s.chunks || 0), 0);

// turn each indexed source into a readable article (curated content by keyword, generic otherwise)
function articleFor(src, kbName) {
  const base = src.name.replace(/\.[a-z0-9]+$/i, "");
  const t = base.toLowerCase();
  const mk = (sections) => ({ id: src.name, title: base, type: src.type, chunks: src.chunks, sections });
  if (/handbook/.test(t)) return mk([
    ["Working hours", "Standard hours are Monday to Friday, 9:00am to 5:30pm. Flexible start times between 8 and 10am are available with manager approval. Remote work is offered up to two days a week."],
    ["Time off & leave", "Full-time staff accrue 15 days of paid time off per year plus public holidays. Submit requests at least two weeks in advance through the workspace. Sick leave does not require advance notice."],
    ["Code of conduct", "Treat customers and teammates with respect, protect confidential information, and disclose any conflicts of interest. Violations are reviewed case by case with HR."]]);
  if (/sop|opening|closing|procedure/.test(t)) return mk([
    ["Opening checklist", "Disarm the alarm, switch on the lights and equipment, count the till float to the standard amount, and check the overnight inbox for urgent orders before unlocking the doors."],
    ["Closing checklist", "Reconcile the register, back up the day's transactions, set the alarm, and confirm all equipment is powered down. Log any incidents in the daily report."]]);
  if (/vendor/.test(t)) return mk([
    ["Approved vendors", "Use only vendors on the approved list for recurring supplies. New vendors must be vetted for pricing, lead time and insurance before their first order."],
    ["Ordering process", "Purchase orders over $1,000 require owner approval. Keep all invoices in the shared drive for reconciliation."]]);
  if (/safety/.test(t)) return mk([
    ["General safety", "Know where the extinguishers, first-aid kit and exits are. Report hazards immediately and never bypass equipment guards."],
    ["Incident response", "For any injury, administer first aid, log the incident the same day, and notify a manager. Serious incidents are escalated to the owner at once."]]);
  if (/price|pricing/.test(t)) return mk([
    ["Current pricing", "The 2026 price book lists every service and package with its standard rate. Prices are reviewed quarterly; agents always quote from the live book."],
    ["Quoting rules", "Bundle discounts apply automatically at checkout. Custom quotes over the listed rate need a one-line justification."]]);
  if (/package/.test(t)) return mk([
    ["Service packages", "Three tiers, Starter, Standard and Premium, each with a defined scope and turnaround. Premium includes priority scheduling and a dedicated point of contact."],
    ["What's included", "Every package includes onboarding, support and a satisfaction guarantee. Add-ons are billed separately and listed in the price book."]]);
  if (/discount/.test(t)) return mk([
    ["Discount policy", "Standard discounts cap at 15% without approval. Seasonal promotions are pre-approved and published in advance. Stacking promotions is not permitted."]]);
  if (/return|refund/.test(t)) return mk([
    ["Returns", "Items can be returned within 30 days with proof of purchase for a full refund. Opened consumables are non-returnable unless defective."],
    ["Refund handling", "Refunds process to the original payment method within 5 business days. Refund requests are routed to a human via Greenlight before completion."]]);
  if (/troubleshoot/.test(t)) return mk([
    ["Common issues", "Start with the basics: restart, check connections, confirm the latest version. Most reported issues resolve at this step."],
    ["When to escalate", "If the issue persists after the basic steps or involves data loss, escalate to a human with the customer's details and what was tried."]]);
  if (/help|faq|script/.test(t)) return mk([
    ["Overview", `Articles indexed from ${base}. Agents answer routine questions directly from this content and cite it back to the customer.`],
    ["Coverage", "Hours, location, policies, common how-tos and troubleshooting. Gaps the agent can't answer are flagged to add here."]]);
  return mk([
    ["Overview", `Indexed from ${base} into the "${kbName}" knowledge base. The full document was chunked and embedded so agents can retrieve the most relevant passages on demand.`],
    ["How it's used", `Across the suite, agents search this content and ground their answers and drafts on it, citing it where relevant. ${src.chunks} searchable chunks.`]]);
}
const kbArticleList = (kb) => kb.sources.map((s) => articleFor(s, kb.name));

// RAG ingest pipeline stages shown while a source is indexed
const RAG_STAGES = ["Uploading", "Extracting text", "Chunking", "Embedding", "Indexing"];

function RagProgress({ onDone, label }) {
  const [stage, setStage] = useState(0);
  useEffect(() => {
    if (stage >= RAG_STAGES.length) { const t = setTimeout(onDone, 400); return () => clearTimeout(t); }
    const t = setTimeout(() => setStage((s) => s + 1), 520);
    return () => clearTimeout(t);
  }, [stage]);
  return (
    <div style={{ padding: "14px 4px" }}>
      <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 11, display: "flex", alignItems: "center", gap: 8 }}><Icon name="refresh" size={14} className="spin" />Indexing {label}…</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
        {RAG_STAGES.map((s, i) => (
          <div key={s} style={{ display: "flex", alignItems: "center", gap: 9, fontSize: 12.5, color: i <= stage ? "var(--ink)" : "var(--ink-4)" }}>
            <span style={{ width: 18, height: 18, borderRadius: 99, display: "grid", placeItems: "center", background: i < stage ? "var(--green)" : i === stage ? "var(--accent)" : "var(--surface-2)", color: i <= stage ? "#fff" : "var(--ink-4)", flexShrink: 0 }}>
              {i < stage ? <Icon name="check" size={11} sw={3} /> : i === stage ? <span className="live-dot" style={{ width: 6, height: 6, background: "#fff" }} /> : i + 1}
            </span>
            {s}
          </div>
        ))}
      </div>
    </div>
  );
}

function NewKBModal({ onClose, onCreated }) {
  const { KB_STARTERS } = window.FL_DATA;
  const [name, setName] = useState("");
  const [picked, setPicked] = useState([]);
  const [phase, setPhase] = useState("setup"); // setup | indexing | done
  const toggle = (s) => setPicked((p) => p.includes(s) ? p.filter((x) => x !== s) : [...p, s]);
  const create = () => {
    const id = FLStore.addKB({ name: name.trim() || "New knowledge base", icon: "doc", tone: "indigo" });
    picked.forEach((s, i) => FLStore.addKBSource(id, { name: s + ".pdf", type: "pdf", chunks: 40 + Math.floor(Math.random() * 120) }));
    FLStore.pushFeed && FLStore.pushFeed({ agent: "scout", ico: "doc", tone: "indigo", html: `Indexed a new knowledge base: <b>${name.trim() || "New knowledge base"}</b>`, meta: "just now · Knowledge" });
    setPhase("done"); setTimeout(() => { onCreated ? onCreated(id) : onClose(); }, 1100);
  };
  return (
    <div className="cmdk-scrim show" onClick={onClose} style={{ alignItems: "center", paddingTop: 0 }}>
      <div className="cmdk" style={{ maxWidth: 480 }} onClick={(e) => e.stopPropagation()}>
        <div style={{ padding: "18px 20px", borderBottom: "1px solid var(--line)", display: "flex", alignItems: "center", gap: 11 }}>
          <div className="feed-ico" style={{ width: 32, height: 32, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="doc" size={16} /></div>
          <b style={{ fontSize: 16, fontWeight: 720, flex: 1 }}>New knowledge base</b>
          <button className="icon-btn" onClick={onClose}><Icon name="x" size={18} /></button>
        </div>
        {phase === "setup" && (
          <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 14 }}>
            <div className="wf-field"><label>Name</label><input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Operations & SOPs" /></div>
            <div className="wf-field"><label>Add sources <span style={{ color: "var(--ink-4)", fontWeight: 400 }}>· pick a few, or upload your own</span></label>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {KB_STARTERS.map(([n]) => { const on = picked.includes(n); return <button key={n} className="chip" style={{ cursor: "pointer", height: 28, background: on ? "var(--accent-soft)" : "var(--surface-2)", color: on ? "var(--accent-ink)" : "var(--ink-3)", border: on ? "none" : "1px solid var(--line)" }} onClick={() => toggle(n)}>{on && <Icon name="check" size={11} sw={2.6} />}{n}</button>; })}
              </div>
            </div>
            <div style={{ border: "1.5px dashed var(--line)", borderRadius: "var(--r-md)", padding: "20px 16px", textAlign: "center", background: "var(--surface-2)" }}>
              <p style={{ fontSize: 12.5, color: "var(--ink-3)" }}>Drop files here, or click to choose · .pdf .docx .csv .md .txt</p>
            </div>
            <button className="btn btn-primary" disabled={!name.trim() && picked.length === 0} onClick={() => setPhase("indexing")}><Icon name="spark" size={16} />Create &amp; index</button>
            <p style={{ fontSize: 11.5, color: "var(--ink-4)", textAlign: "center" }}>Hosted &amp; private to your instance. We chunk and embed it into a searchable index your agents and products use as context.</p>
          </div>
        )}
        {phase === "indexing" && <div style={{ padding: 20 }}><RagProgress label={name.trim() || "your sources"} onDone={create} /></div>}
        {phase === "done" && (
          <div style={{ padding: "30px 20px", textAlign: "center" }}>
            <div className="lp-prov-check" style={{ width: 56, height: 56, borderRadius: 16, margin: "0 auto" }}><Icon name="check" size={28} sw={2.6} style={{ color: "#fff" }} /></div>
            <h3 style={{ fontSize: 18, fontWeight: 730, marginTop: 13 }}>Knowledge base is live</h3>
            <p style={{ fontSize: 13, color: "var(--ink-2)", marginTop: 6 }}>Indexed and ready. Every product can now ground on it.</p>
          </div>
        )}
      </div>
    </div>
  );
}

function KBDetail({ kb, agents, onClose }) {
  const [q, setQ] = useState("");
  const [answer, setAnswer] = useState(null);
  const [searching, setSearching] = useState(false);
  const [adding, setAdding] = useState(false);
  const [bumpName, setBumpName] = useState("");
  const [bg, fg] = kbTone(kb.tone);
  const total = kbChunks(kb);

  const ask = async () => {
    const body = q.trim(); if (!body || searching) return; setSearching(true); setAnswer(null);
    const hits = kb.sources.slice(0, Math.min(kb.topK, kb.sources.length)).map((s) => s.name);
    let text = "";
    try { text = await askClaude(`You are answering from a business knowledge base named "${kb.name}". Give a concise 2-3 sentence answer to: "${body}". If you don't truly know, say it would be grounded on the indexed sources.`, "Based on the indexed sources, here's the most relevant guidance for your question."); } catch (e) { text = "Based on the indexed sources, here's the most relevant guidance for your question."; }
    setAnswer({ text, hits }); setSearching(false);
  };

  return (
    <div className="cmdk-scrim show" onClick={onClose} style={{ alignItems: "stretch", justifyContent: "flex-end", paddingTop: 0 }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: "min(520px, 96vw)", height: "100%", background: "var(--surface)", borderLeft: "1px solid var(--line)", boxShadow: "var(--shadow-xl)", display: "flex", flexDirection: "column", animation: "slide-in .25s both" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "16px 20px", borderBottom: "1px solid var(--line)" }}>
          <div className="feed-ico" style={{ width: 36, height: 36, background: bg, color: fg }}><Icon name={kb.icon} size={18} /></div>
          <div style={{ flex: 1, minWidth: 0 }}><b style={{ fontSize: 16, fontWeight: 720, display: "block" }}>{kb.name}</b><span style={{ fontSize: 12, color: "var(--ink-3)" }}>{kb.sources.length} sources · {total.toLocaleString()} chunks · {kb.visibility}</span></div>
          <button className="icon-btn" onClick={onClose}><Icon name="x" size={18} /></button>
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: 20, display: "flex", flexDirection: "column", gap: 22 }}>
          {/* test retrieval */}
          <div>
            <div className="ad-sec-label"><Icon name="search" size={14} />Test retrieval</div>
            <div className="search-trigger" style={{ cursor: "text" }}>
              <Icon name="search" size={15} />
              <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") ask(); }} placeholder="Ask this knowledge base a question…" style={{ border: "none", outline: "none", background: "none", flex: 1, fontSize: 13, color: "var(--ink)" }} />
              <button className="btn btn-primary btn-sm" onClick={ask} disabled={searching || !q.trim()}>{searching ? "…" : "Ask"}</button>
            </div>
            {answer && (
              <div style={{ marginTop: 12, animation: "feed-in .3s both" }}>
                <div style={{ background: "var(--accent-softer)", border: "1px solid var(--accent-soft)", borderRadius: "var(--r-md)", padding: "12px 14px", fontSize: 13, color: "var(--ink)", lineHeight: 1.55 }}>{answer.text}</div>
                <div style={{ fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)", margin: "9px 0 5px" }}>RETRIEVED FROM</div>
                <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                  {answer.hits.map((h, i) => <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--ink-2)" }}><Icon name={SRC_ICO[kb.sources[i] && kb.sources[i].type] || "doc"} size={13} style={{ color: fg }} />{h}<span style={{ marginLeft: "auto", fontFamily: "var(--mono)", color: "var(--ink-4)", fontSize: 11 }}>0.{92 - i * 4}</span></div>)}
                </div>
              </div>
            )}
          </div>

          {/* sources */}
          <div>
            <div className="ad-sec-label" style={{ justifyContent: "space-between" }}><span style={{ display: "flex", gap: 8, alignItems: "center" }}><Icon name="layers" size={14} />Sources <span style={{ fontWeight: 500, color: "var(--ink-4)" }}>· {kb.sources.length}</span></span>{!adding && <button className="btn btn-soft btn-sm" onClick={() => setAdding(true)}><Icon name="plus" size={13} sw={2.2} />Add source</button>}</div>
            {adding && (bumpName ? <RagProgress label={bumpName} onDone={() => { FLStore.addKBSource(kb.id, { name: bumpName + ".pdf", type: "pdf", chunks: 40 + Math.floor(Math.random() * 120) }); setBumpName(""); setAdding(false); }} /> : (
              <div style={{ display: "flex", gap: 7, margin: "4px 0 10px" }}>
                <input autoFocus placeholder="Document name" onKeyDown={(e) => { if (e.key === "Enter" && e.target.value.trim()) setBumpName(e.target.value.trim()); }} style={{ flex: 1, font: "inherit", fontSize: 13, border: "1px solid var(--line)", borderRadius: 8, padding: "7px 10px", background: "var(--bg)", color: "var(--ink)", outline: "none" }} />
                <button className="btn btn-ghost btn-sm" onClick={() => setAdding(false)}>Cancel</button>
              </div>
            ))}
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {kb.sources.map((s, i) => (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 11px", border: "1px solid var(--line-2)", borderRadius: "var(--r-sm)" }}>
                  <Icon name={SRC_ICO[s.type] || "doc"} size={15} style={{ color: fg, flexShrink: 0 }} />
                  <span style={{ fontSize: 13, fontWeight: 550, flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.name}</span>
                  <span style={{ fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>{s.chunks} chunks</span>
                </div>
              ))}
            </div>
          </div>

          {/* who uses it */}
          <div>
            <div className="ad-sec-label"><Icon name="spark" size={14} />Grounds these agents</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 7 }}>
              {Object.values(agents).map((a) => { const on = kb.agents.includes(a.id); return (
                <button key={a.id} className="chip" style={{ cursor: "pointer", height: 30, gap: 7, background: on ? "var(--accent-soft)" : "var(--surface-2)", color: on ? "var(--accent-ink)" : "var(--ink-3)", border: on ? "none" : "1px solid var(--line)" }} onClick={() => FLStore.toggleKBAgent(kb.id, a.id)}>
                  <span className="avatar" style={{ background: a.color, width: 18, height: 18, fontSize: 9 }}>{a.init}</span>{a.name}{on && <Icon name="check" size={11} sw={2.6} />}
                </button>
              ); })}
            </div>
            <p style={{ fontSize: 12, color: "var(--ink-4)", marginTop: 9, lineHeight: 1.5 }}>This base is also available as context across Uplift, Workflows, Frontline and Cortex.</p>
          </div>

          {/* settings */}
          <div>
            <div className="ad-sec-label"><Icon name="sliders" size={14} />Settings</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}><span style={{ fontSize: 13, flex: 1 }}>Embedding model</span><span className="chip" style={{ height: 24, fontFamily: "var(--mono)" }}>{kb.embModel}</span></div>
              <div><div style={{ display: "flex", justifyContent: "space-between", marginBottom: 5 }}><span style={{ fontSize: 13 }}>Retrieval depth (top-k)</span><span style={{ fontSize: 12.5, fontFamily: "var(--mono)", fontWeight: 700 }}>{kb.topK}</span></div>
                <input type="range" min="3" max="12" value={kb.topK} onChange={(e) => FLStore.setKBField(kb.id, { topK: +e.target.value })} style={{ width: "100%" }} /></div>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}><span style={{ fontSize: 13, flex: 1 }}>Visibility</span>
                <div className="seg"><button className={kb.visibility === "private" ? "active" : ""} onClick={() => FLStore.setKBField(kb.id, { visibility: "private" })} style={{ height: 26, padding: "0 10px", fontSize: 12 }}>Private</button><button className={kb.visibility === "shared" ? "active" : ""} onClick={() => FLStore.setKBField(kb.id, { visibility: "shared" })} style={{ height: 26, padding: "0 10px", fontSize: 12 }}>Shared</button></div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 9, padding: "10px 12px", background: "var(--surface-2)", borderRadius: "var(--r-sm)", fontSize: 12, color: "var(--ink-3)" }}><Icon name="shield" size={14} style={{ color: "var(--green)", flexShrink: 0 }} />Hosted by Friesen, encrypted and private to your instance. Nothing trains shared models.</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function KBPage({ kb, agents, onBack, onManage }) {
  const articles = kbArticleList(kb);
  const [sel, setSel] = useState(0);
  const [q, setQ] = useState("");
  const [bg, fg] = kbTone(kb.tone);
  const filtered = articles.filter((a) => !q || (a.title + a.sections.map((s) => s.h + s.p).join(" ")).toLowerCase().includes(q.toLowerCase()));
  const art = filtered[sel] || filtered[0];

  return (
    <div className="screen screen-anim">
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <button className="btn btn-ghost btn-sm" onClick={onBack}><Icon name="chevL" size={15} sw={2.2} />Knowledge</button>
        <div className="feed-ico" style={{ width: 34, height: 34, background: bg, color: fg }}><Icon name={kb.icon} size={17} /></div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 style={{ fontSize: 20, fontWeight: 740, letterSpacing: "-.02em" }}>{kb.name}</h2>
          <span style={{ fontSize: 12.5, color: "var(--ink-3)" }}>{kb.sources.length} articles · {kbChunks(kb).toLocaleString()} chunks · updated {kb.updated}</span>
        </div>
        <span className="chip green" style={{ height: 28 }}><span className="cdot" style={{ background: "var(--green)" }} />Indexed &amp; live</span>
        <button className="btn btn-ghost btn-sm" onClick={onManage}><Icon name="sliders" size={15} />Manage base</button>
      </div>

      {articles.length === 0 ? (
        <div className="empty-state" style={{ padding: "60px 20px" }}><div className="es-ico"><Icon name="doc" size={24} /></div><h4>Nothing indexed yet</h4><p>Add a source to this base and it'll appear here as a browsable article.</p><button className="btn btn-primary btn-sm" style={{ marginTop: 14 }} onClick={onManage}><Icon name="plus" size={13} sw={2.2} />Add a source</button></div>
      ) : (
        <div className="kb-doc">
          <aside className="kb-side">
            <div className="search-trigger" style={{ cursor: "text", marginBottom: 12 }}>
              <Icon name="search" size={15} />
              <input value={q} onChange={(e) => { setQ(e.target.value); setSel(0); }} placeholder="Search articles…" style={{ border: "none", outline: "none", background: "none", flex: 1, fontSize: 13, color: "var(--ink)" }} />
            </div>
            <div style={{ fontSize: 10.5, fontWeight: 650, textTransform: "uppercase", letterSpacing: ".05em", color: "var(--ink-4)", padding: "2px 8px 7px" }}>Articles · {filtered.length}</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              {filtered.map((a, i) => (
                <button key={a.id} className={"kb-nav" + (art && a.id === art.id ? " sel" : "")} onClick={() => setSel(i)}>
                  <Icon name={SRC_ICO[a.type] || "doc"} size={14} style={{ color: art && a.id === art.id ? fg : "var(--ink-4)", flexShrink: 0 }} />
                  <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.title}</span>
                </button>
              ))}
              {filtered.length === 0 && <div style={{ fontSize: 12.5, color: "var(--ink-4)", padding: "8px" }}>No articles match.</div>}
            </div>
          </aside>
          <article className="kb-reader" key={art ? art.id : "none"}>
            {art && (
              <>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                  <span className="chip" style={{ height: 20, fontSize: 10, textTransform: "uppercase", fontFamily: "var(--mono)", background: bg, color: fg }}>{art.type}</span>
                  <span style={{ fontSize: 11.5, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>{art.chunks} chunks indexed</span>
                </div>
                <h1 style={{ fontSize: 27, fontWeight: 760, letterSpacing: "-.03em", lineHeight: 1.15 }}>{art.title}</h1>
                <div style={{ marginTop: 18, display: "flex", flexDirection: "column", gap: 20 }}>
                  {art.sections.map((s, i) => (
                    <section key={i}>
                      <h3 style={{ fontSize: 16, fontWeight: 700, letterSpacing: "-.01em", marginBottom: 7 }}>{s.h}</h3>
                      <p style={{ fontSize: 14.5, lineHeight: 1.7, color: "var(--ink-2)" }}>{s.p}</p>
                    </section>
                  ))}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 9, marginTop: 26, padding: "13px 15px", background: "var(--accent-softer)", borderRadius: "var(--r-md)", fontSize: 12.5, color: "var(--accent-ink)", lineHeight: 1.5 }}>
                  <Icon name="spark" size={15} style={{ flexShrink: 0 }} />
                  Your agents ground answers on this article across Uplift, Frontline and Workflows, and cite it back when they use it.
                </div>
              </>
            )}
          </article>
        </div>
      )}
    </div>
  );
}

function Knowledge({ agents, onNavigate }) {
  const kbs = useStore((s) => s.knowledgeBases);
  const memories = useStore((s) => s.memories);
  const brainAnswers = useStore((s) => s.brainAnswers);
  const [neu, setNeu] = useState(false);
  const [openId, setOpenId] = useState(null);
  const [manage, setManage] = useState(false);
  const [brain, setBrain] = useState(false);
  const [memCompose, setMemCompose] = useState(false);
  const recall = useStore((s) => s.recall);
  const [recallFlow, setRecallFlow] = useState(false);
  const [recallSearch, setRecallSearch] = useState(false);
  const openKB = kbs.find((k) => k.id === openId);
  const totalSources = kbs.reduce((n, k) => n + k.sources.length, 0);
  const totalChunks = kbs.reduce((n, k) => n + kbChunks(k), 0);
  const brainCount = Object.values(brainAnswers).filter((a) => a && a.text).length;
  const brainTotal = (window.FL_DATA.BRAIN_QUESTIONS || []).reduce((n, g) => n + g.items.length, 0);

  // when a KB is open, show its full browsable page
  if (openKB) return (
    <>
      <KBPage kb={openKB} agents={agents} onBack={() => setOpenId(null)} onManage={() => setManage(true)} />
      {manage && <KBDetail kb={openKB} agents={agents} onClose={() => setManage(false)} />}
    </>
  );

  return (
    <div className="screen screen-anim">
      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 7 }}>Your hosted context layer</div>
          <h2 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.03em" }}>Knowledge</h2>
          <p style={{ color: "var(--ink-2)", fontSize: 14.5, marginTop: 5, maxWidth: 620 }}>Upload what your business knows and we turn it into hosted, searchable knowledge bases. Every product and agent grounds its answers on them.</p>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 9 }}>
          <button className="btn btn-ghost" onClick={() => onNavigate && onNavigate("integrations")}><Icon name="plug" size={16} />Connect a source</button>
          <button className="btn btn-primary" onClick={() => setNeu(true)}><Icon name="plus" size={16} sw={2.2} />New knowledge base</button>
        </div>
      </div>

      <div className="stat-grid" style={{ marginBottom: "var(--gap)" }}>
        {[["doc", "indigo", "Knowledge bases", kbs.length], ["layers", "amber", "Sources indexed", totalSources], ["network", "green", "Searchable chunks", totalChunks.toLocaleString()], ["shield", "indigo", "Hosting", "Private"]].map(([ic, tone, label, val]) => {
          const [bg, fg] = kbTone(tone);
          return (
            <div className="stat" key={label}>
              <div className="stat-top"><div className="stat-ico" style={{ background: bg, color: fg }}><Icon name={ic} size={17} /></div></div>
              <div className="stat-val" style={{ fontSize: 26 }}>{val}</div>
              <div className="stat-label">{label}</div>
            </div>
          );
        })}
      </div>

      {/* Business Brain hero */}
      <div className="card brain-hero" style={{ marginBottom: "var(--gap)", overflow: "hidden", position: "relative" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 18, padding: "20px 22px", flexWrap: "wrap" }}>
          <div className="feed-ico" style={{ width: 48, height: 48, background: "var(--accent)", color: "#fff", borderRadius: 14, flexShrink: 0 }}><Icon name="spark" size={24} /></div>
          <div style={{ flex: 1, minWidth: 220 }}>
            <b style={{ fontSize: 18, fontWeight: 740, letterSpacing: "-.02em" }}>Business Brain</b>
            <p style={{ fontSize: 13.5, color: "var(--ink-2)", lineHeight: 1.5, marginTop: 4, maxWidth: 540 }}>Answer a few questions about your business, your customers, and what you care about, by voice or text. We embed it so every agent understands you and sounds like you.</p>
            {brainCount > 0 && (
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 12, maxWidth: 320 }}>
                <div className="meter" style={{ flex: 1 }}><span style={{ width: (brainCount / brainTotal * 100) + "%", background: "var(--accent)" }} /></div>
                <span style={{ fontSize: 11.5, color: "var(--ink-4)", fontFamily: "var(--mono)", whiteSpace: "nowrap" }}>{brainCount}/{brainTotal} answered</span>
              </div>
            )}
          </div>
          <button className="btn btn-primary btn-lg" onClick={() => setBrain(true)}><Icon name="spark" size={16} />{brainCount > 0 ? "Keep building my brain" : "Build my brain"}</button>
        </div>
      </div>

      {/* Memory shelf */}
      <div className="card" style={{ marginBottom: "var(--gap)" }}>
        <div className="card-head">
          <div className="feed-ico" style={{ width: 30, height: 30, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="spark" size={15} /></div>
          <h3>Memory</h3>
          <span className="sub">things your agents remember</span>
          <button className="btn btn-soft btn-sm" style={{ marginLeft: "auto" }} onClick={() => setMemCompose(true)}><Icon name="plus" size={13} sw={2.2} />Add memory</button>
        </div>
        <div style={{ padding: "4px var(--pad) 14px", display: "flex", flexDirection: "column", gap: 8 }}>
          {[...memories].sort((a, b) => (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0)).map((m) => { const a = agents[m.agent]; return (
            <div key={m.id} className="mem-row">
              <button className="mem-pin" title={m.pinned ? "Unpin" : "Pin"} onClick={() => FLStore.toggleMemoryPin(m.id)} style={{ color: m.pinned ? "var(--accent)" : "var(--ink-4)" }}><Icon name={m.pinned ? "star" : "starOff"} size={15} /></button>
              <div style={{ flex: 1, minWidth: 0 }}>
                <p style={{ fontSize: 13, color: "var(--ink)", lineHeight: 1.5 }}>{m.text}</p>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 5, fontSize: 11, color: "var(--ink-4)" }}>
                  <span className="chip" style={{ height: 17, fontSize: 9.5, padding: "0 7px" }}>{m.tag}</span>
                  <span>from {m.source}</span>
                  {a && <span style={{ display: "flex", alignItems: "center", gap: 4 }}>· <span className="avatar" style={{ background: a.color, width: 14, height: 14, fontSize: 7 }}>{a.init}</span>{a.name}</span>}
                </div>
              </div>
              <button className="icon-btn" style={{ width: 28, height: 28 }} title="Forget" onClick={() => FLStore.deleteMemory(m.id)}><Icon name="x" size={14} /></button>
            </div>
          ); })}
          {memories.length === 0 && <div className="empty-state" style={{ padding: "30px 20px" }}><div className="es-ico"><Icon name="spark" size={20} /></div><h4>No memories yet</h4><p>Hit "Save to memory" anywhere in the app, or add one here.</p></div>}
        </div>
      </div>

      <div className="kb-grid">
        {kbs.map((kb) => {
          const [bg, fg] = kbTone(kb.tone); const total = kbChunks(kb);
          return (
            <button key={kb.id} className="card kb-card" onClick={() => setOpenId(kb.id)}>
              <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
                <div className="feed-ico" style={{ width: 38, height: 38, background: bg, color: fg }}><Icon name={kb.icon} size={19} /></div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <b style={{ fontSize: 14.5, fontWeight: 700, display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{kb.name}</b>
                  <span style={{ fontSize: 11.5, color: "var(--ink-4)" }}>updated {kb.updated}</span>
                </div>
                <span className="chip" style={{ height: 20, fontSize: 10, background: kb.visibility === "shared" ? "var(--amber-soft)" : "var(--surface-2)", color: kb.visibility === "shared" ? "oklch(0.5 0.12 60)" : "var(--ink-3)" }}>{kb.visibility}</span>
              </div>
              <div style={{ display: "flex", gap: 16, marginTop: 14 }}>
                <div><div style={{ fontSize: 18, fontWeight: 760, letterSpacing: "-.02em" }}>{kb.sources.length}</div><div style={{ fontSize: 11, color: "var(--ink-4)" }}>sources</div></div>
                <div><div style={{ fontSize: 18, fontWeight: 760, letterSpacing: "-.02em" }}>{total.toLocaleString()}</div><div style={{ fontSize: 11, color: "var(--ink-4)" }}>chunks</div></div>
                <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: -6 }}>
                  {kb.agents.slice(0, 4).map((aid, i) => agents[aid] && <span key={aid} className="avatar" style={{ background: agents[aid].color, width: 24, height: 24, fontSize: 10, marginLeft: i ? -6 : 0, border: "2px solid var(--surface)" }}>{agents[aid].init}</span>)}
                </div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 7, marginTop: 13, paddingTop: 12, borderTop: "1px solid var(--line-2)", fontSize: 12, color: "var(--green)", fontWeight: 600 }}>
                <span className="live-dot" style={{ width: 6, height: 6, background: "var(--green)" }} />Indexed &amp; grounding {kb.agents.length} agent{kb.agents.length === 1 ? "" : "s"}
              </div>
            </button>
          );
        })}
        <button className="card kb-card kb-new" onClick={() => setNeu(true)}>
          <div className="es-ico" style={{ margin: "0 auto 10px" }}><Icon name="plus" size={22} /></div>
          <b style={{ fontSize: 14, fontWeight: 650 }}>New knowledge base</b>
          <p style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 4, textAlign: "center" }}>Upload docs, connect a tool, or start from a template</p>
        </button>
        {recall.indexed
          ? <RecallResultCard recall={recall} onOpen={() => setRecallSearch(true)} />
          : <RecallAddonCard onAdd={() => setRecallFlow(true)} />}
      </div>

      {neu && <NewKBModal onClose={() => setNeu(false)} onCreated={(id) => { setNeu(false); setOpenId(id); }} />}
      {brain && <BrainInterview onClose={() => setBrain(false)} />}
      {memCompose && <MemoryComposer onClose={() => setMemCompose(false)} defaults={{ source: "Knowledge" }} />}
      {recallFlow && <RecallFlow onClose={() => setRecallFlow(false)} />}
      {recallSearch && <RecallSearch onClose={() => setRecallSearch(false)} />}
    </div>
  );
}

window.Knowledge = Knowledge;
