// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// import-data.jsx, Uplift data-import tutorials + "make your tools agentic" layer
const IMPORT_PROVIDERS = [
  { id: "hubspot", name: "HubSpot", letter: "H", color: "#ff7a59", steps: [
    "In HubSpot, open Contacts → Contacts (or Deals).",
    "Click the Actions dropdown above the table and choose Export.",
    "Pick the properties (columns) you want and select CSV.",
    "Click Export, HubSpot emails you a secure download link.",
    "Download the file and drop it into Uplift below." ] },
  { id: "salesforce", name: "Salesforce", letter: "S", color: "#00a1e0", steps: [
    "Click the gear icon → Setup, then search “Data Export.”",
    "Choose Export Now and tick Leads, Contacts & Opportunities.",
    "Start the export, Salesforce builds a .zip of CSV files.",
    "Download the .zip when the email arrives and unzip it.",
    "Upload the CSVs to Uplift below." ] },
  { id: "pipedrive", name: "Pipedrive", letter: "P", color: "#1a1a1a", steps: [
    "Open Settings (your avatar) → Export data.",
    "Select Deals, Persons and Organizations.",
    "Click Export to CSV.",
    "Download the files from the Exports list.",
    "Drop them into Uplift below." ] },
  { id: "google", name: "Google Contacts", letter: "G", color: "#4285f4", steps: [
    "Go to contacts.google.com and select the contacts to move.",
    "Click the … (More) menu → Export.",
    "Choose Google CSV and export.",
    "Download the file.",
    "Upload it to Uplift below." ] },
  { id: "mailchimp", name: "Mailchimp", letter: "M", color: "#ffe01b", dark: true, steps: [
    "Open Audience → All contacts.",
    "Click Export Audience (top right).",
    "Mailchimp prepares a zip, click Export As CSV.",
    "Download the file.",
    "Upload it to Uplift below." ] },
  { id: "square", name: "Square", letter: "S", color: "#0a0a0a", steps: [
    "Open the Square Dashboard → Customers → Directory.",
    "Click Import/Export → Export Customers.",
    "Download the generated CSV.",
    "Upload it to Uplift below." ] },
  { id: "quickbooks", name: "QuickBooks", letter: "Q", color: "#2ca01c", steps: [
    "Go to Sales → Customers (or Reports → Customer Contact List).",
    "Click the Export icon → Export to Excel.",
    "Save the sheet as .CSV.",
    "Upload it to Uplift below." ] },
  { id: "csv", name: "Any spreadsheet (CSV)", letter: "↦", color: "oklch(0.56 0.17 277)", steps: [
    "Open your spreadsheet (Excel, Numbers, Sheets).",
    "Make sure there's a row per contact with columns like Company, Contact, Value, Stage.",
    "Save / export it as .CSV.",
    "Drop it in below, we auto-map the columns for you." ] },
];

function StepShot({ provider, n }) {
  return (
    <div style={{ width: 140, height: 90, borderRadius: "var(--r-sm)", flexShrink: 0, border: "1px solid var(--line)", background: "var(--surface)", overflow: "hidden", boxShadow: "var(--shadow-sm)" }}>
      <div style={{ height: 18, display: "flex", alignItems: "center", gap: 4, padding: "0 7px", borderBottom: "1px solid var(--line-2)", background: "var(--surface-2)" }}>
        <span style={{ width: 6, height: 6, borderRadius: 99, background: "var(--rose)" }} /><span style={{ width: 6, height: 6, borderRadius: 99, background: "var(--amber)" }} /><span style={{ width: 6, height: 6, borderRadius: 99, background: "var(--green)" }} />
        <span style={{ marginLeft: 4, fontFamily: "var(--mono)", fontSize: 7.5, color: "var(--ink-4)" }}>{provider.toLowerCase()}.com</span>
      </div>
      <div style={{ padding: 8, display: "flex", flexDirection: "column", gap: 4 }}>
        <div style={{ height: 7, width: "55%", borderRadius: 3, background: "var(--line)" }} />
        <div style={{ height: 5, width: "85%", borderRadius: 3, background: "var(--line-2)" }} />
        <div style={{ height: 5, width: "70%", borderRadius: 3, background: "var(--line-2)" }} />
        <div style={{ marginTop: 3, alignSelf: "flex-start", height: 13, padding: "0 8px", borderRadius: 4, background: "var(--accent)", display: "grid", placeItems: "center", color: "#fff", fontSize: 7, fontWeight: 700, fontFamily: "var(--mono)" }}>STEP {n}</div>
      </div>
    </div>
  );
}

function ImportData({ open, onClose }) {
  const [pid, setPid] = useState(null);
  const [agent, setAgent] = useState(false);
  const [done, setDone] = useState(false);
  // custom-export agent
  const [msgs, setMsgs] = useState([{ who: "bot", text: "Which tool are you exporting from? Tell me the platform (e.g. “Zoho”, “Keap”, “Monday”) and I'll give you the exact steps." }]);
  const [draft, setDraft] = useState("");
  const [steps, setSteps] = useState(null);
  const bodyRef = useRef(null);
  useEffect(() => { if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight; }, [msgs, steps]);
  useEffect(() => { if (!open) return; const k = (e) => { if (e.key === "Escape") onClose(); }; window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k); }, [open, onClose]);

  if (!open) return null;
  const prov = IMPORT_PROVIDERS.find((p) => p.id === pid);

  const runImport = () => {
    ["Imported Co.", "Northwind Trading", "Acme Holdings"].forEach((co, i) => window.FLStore.addDeal({ co, person: "Imported contact", value: 6000 + i * 2500 }));
    setDone(true);
  };

  const askExport = async (text) => {
    const body = (text || draft).trim(); if (!body) return; setDraft("");
    setMsgs((m) => [...m, { who: "me", text: body }, { who: "bot", typing: true }]);
    const fallback = `Here's the usual way to export from ${body}: 1) Open your contacts/records list. 2) Look for an Export or Download option (often under a ⋯ or Settings menu). 3) Choose CSV. 4) Download the file, then upload it to Uplift. If you get stuck, our team can do the migration for you.`;
    const out = await askClaude(`A small-business owner wants to export their customer/CRM data from "${body}" to import elsewhere. Give clear numbered steps (max 5) to export a CSV from ${body}. Plain text, no markdown.`, fallback);
    setSteps(out);
    setMsgs((m) => { const c = [...m]; c[c.length - 1] = { who: "bot", text: `Here are the steps to export from ${body}:` }; return c; });
  };

  const reset = () => { setPid(null); setAgent(false); setDone(false); setSteps(null); setMsgs([msgs[0]]); };

  return (
    <div className="cmdk-scrim show" onClick={onClose} style={{ alignItems: "center", paddingTop: 0 }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: "min(720px, 94vw)", height: "min(660px, 90vh)", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-xl)", boxShadow: "var(--shadow-xl)", display: "flex", flexDirection: "column", overflow: "hidden", animation: "onb-in .3s both" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "16px 20px", borderBottom: "1px solid var(--line)" }}>
          {(pid || agent) && <button className="icon-btn" style={{ width: 30, height: 30 }} onClick={reset}><Icon name="chevL" size={16} /></button>}
          <div className="feed-ico" style={{ width: 34, height: 34, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="layers" size={17} /></div>
          <div style={{ flex: 1 }}><b style={{ fontSize: 16.5, fontWeight: 730, letterSpacing: "-.02em" }}>{prov ? `Import from ${prov.name}` : agent ? "Export from another tool" : "Import your data"}</b><div style={{ fontSize: 12, color: "var(--ink-3)" }}>Test-drive Uplift with your real data, no wiring required</div></div>
          <button className="icon-btn" onClick={onClose}><Icon name="x" size={18} /></button>
        </div>

        <div ref={bodyRef} style={{ flex: 1, overflowY: "auto", padding: 20 }}>
          {/* provider picker */}
          {!pid && !agent && (
            <>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 12 }}>
                {IMPORT_PROVIDERS.map((p) => (
                  <button key={p.id} className="intg-card" style={{ padding: 15, textAlign: "left", cursor: "pointer", flexDirection: "row", alignItems: "center", gap: 12, display: "flex" }} onClick={() => { setPid(p.id); setDone(false); }}>
                    <div className="intg-mark" style={{ width: 40, height: 40, fontSize: 17, background: p.color, color: p.dark ? "#1a1a1a" : "#fff" }}>{p.letter}</div>
                    <div><b style={{ fontSize: 14, fontWeight: 680, display: "block" }}>{p.name}</b><span style={{ fontSize: 11.5, color: "var(--ink-3)" }}>{p.steps.length}-step guide</span></div>
                  </button>
                ))}
              </div>
              <button className="card" style={{ width: "100%", marginTop: 14, padding: 16, display: "flex", alignItems: "center", gap: 13, cursor: "pointer", border: "1.5px dashed var(--accent-soft)", background: "var(--accent-softer)" }} onClick={() => setAgent(true)}>
                <div className="feed-ico" style={{ width: 36, height: 36, background: "var(--surface)", color: "var(--accent-ink)" }}><Icon name="spark" size={17} /></div>
                <div style={{ textAlign: "left", flex: 1 }}><b style={{ fontSize: 14, fontWeight: 680, color: "var(--accent-ink)", display: "block" }}>My tool isn't listed</b><span style={{ fontSize: 12, color: "var(--accent-ink)", opacity: .8 }}>An agent will walk you through exporting from any platform</span></div>
                <Icon name="arrowRight" size={16} style={{ color: "var(--accent-ink)" }} />
              </button>
            </>
          )}

          {/* provider tutorial */}
          {prov && !done && (
            <>
              <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                {prov.steps.map((s, i) => (
                  <div key={i} style={{ display: "flex", gap: 13, alignItems: "center" }}>
                    <div style={{ width: 26, height: 26, borderRadius: 99, background: "var(--accent)", color: "#fff", display: "grid", placeItems: "center", fontSize: 13, fontWeight: 700, fontFamily: "var(--mono)", flexShrink: 0 }}>{i + 1}</div>
                    <p style={{ flex: 1, fontSize: 13.5, lineHeight: 1.5 }}>{s}</p>
                    <StepShot provider={prov.name} n={i + 1} />
                  </div>
                ))}
              </div>
              <div style={{ marginTop: 18, border: "1.5px dashed var(--line)", borderRadius: "var(--r-md)", padding: "22px 16px", textAlign: "center", background: "var(--surface-2)" }}>
                <Icon name="layers" size={26} style={{ color: "var(--ink-4)" }} />
                <p style={{ fontSize: 13.5, fontWeight: 600, marginTop: 8 }}>Drop your exported file here</p>
                <p style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 3 }}>CSV or .zip, we map the columns and Scout enriches everything</p>
                <button className="btn btn-primary" style={{ marginTop: 14 }} onClick={runImport}><Icon name="check" size={15} sw={2.2} />Upload &amp; import</button>
              </div>
            </>
          )}

          {/* import success */}
          {done && (
            <div style={{ textAlign: "center", padding: "30px 10px" }}>
              <div className="lp-prov-check" style={{ width: 64, height: 64, borderRadius: 18 }}><Icon name="check" size={30} sw={2.6} style={{ color: "#fff" }} /></div>
              <h3 style={{ fontSize: 19, fontWeight: 730, marginTop: 16 }}>Imported into Uplift</h3>
              <p style={{ fontSize: 13.5, color: "var(--ink-2)", marginTop: 8, maxWidth: 380, margin: "8px auto 0", lineHeight: 1.5 }}>Your records are on the board and Scout is already enriching and scoring them. (Demo import added a few sample deals.)</p>
              <button className="btn btn-primary" style={{ marginTop: 18 }} onClick={onClose}>See them in Uplift<Icon name="arrowRight" size={15} sw={2.2} /></button>
            </div>
          )}

          {/* custom-export agent */}
          {agent && (
            <div style={{ display: "flex", flexDirection: "column", gap: 13 }}>
              {msgs.map((m, i) => (
                <div key={i} className={"msg " + (m.who === "me" ? "me" : "agent")} style={{ maxWidth: "82%" }}>
                  {m.who === "bot" && <div className="avatar m-av" style={{ background: "linear-gradient(145deg, var(--accent), var(--accent-press))", width: 26, height: 26, fontSize: 12 }}>✦</div>}
                  <div className="bubble">{m.typing ? <span className="typing"><i /><i /><i /></span> : m.text}</div>
                </div>
              ))}
              {steps && (
                <div className="card" style={{ padding: "14px 16px", fontSize: 13.5, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{steps}</div>
              )}
            </div>
          )}
        </div>

        {agent && (
          <div className="chat-input">
            <textarea rows={1} value={draft} placeholder="Name your platform (e.g. Zoho, Keap, Monday)…" onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); askExport(); } }} />
            <button className="chat-send" disabled={!draft.trim()} onClick={() => askExport()}><Icon name="send" size={17} /></button>
          </div>
        )}
      </div>
    </div>
  );
}

function AgenticLayer({ open, onClose }) {
  const [added, setAdded] = useState(false);
  useEffect(() => { if (!open) return; const k = (e) => { if (e.key === "Escape") onClose(); }; window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k); }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="cmdk-scrim show" onClick={onClose} style={{ alignItems: "center", paddingTop: 0 }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: "min(720px, 94vw)", maxHeight: "90vh", overflowY: "auto", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-xl)", boxShadow: "var(--shadow-xl)", animation: "onb-in .3s both" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "18px 22px", borderBottom: "1px solid var(--line)" }}>
          <div className="feed-ico" style={{ width: 34, height: 34, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="spark" size={17} /></div>
          <div style={{ flex: 1 }}><b style={{ fontSize: 17, fontWeight: 730, letterSpacing: "-.02em" }}>Make your existing tools agentic</b><div style={{ fontSize: 12, color: "var(--ink-3)" }}>Keep your stack, add a Friesen agent layer on top of it</div></div>
          <button className="icon-btn" onClick={onClose}><Icon name="x" size={18} /></button>
        </div>

        {/* extension mock */}
        <div style={{ padding: 22 }}>
          <div style={{ borderRadius: "var(--r-lg)", overflow: "hidden", border: "1px solid var(--line)", boxShadow: "var(--shadow-md)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 7, padding: "9px 13px", background: "var(--surface-2)", borderBottom: "1px solid var(--line)" }}>
              <span style={{ width: 10, height: 10, borderRadius: 99, background: "#e0653f" }} /><span style={{ width: 10, height: 10, borderRadius: 99, background: "#e8a33d" }} /><span style={{ width: 10, height: 10, borderRadius: 99, background: "#2ca05a" }} />
              <div style={{ marginLeft: 8, flex: 1, height: 22, borderRadius: 99, background: "var(--surface)", border: "1px solid var(--line)", fontSize: 11, color: "var(--ink-4)", display: "flex", alignItems: "center", padding: "0 11px", fontFamily: "var(--mono)" }}>app.your-crm.com</div>
              <div style={{ width: 22, height: 22, borderRadius: 6, background: "linear-gradient(145deg, var(--accent), var(--accent-press))", display: "grid", placeItems: "center" }}><Icon name="layers" size={13} style={{ color: "#fff" }} /></div>
            </div>
            <div style={{ display: "flex", height: 168 }}>
              <div style={{ flex: 1, background: "repeating-linear-gradient(135deg, var(--surface-2) 0 9px, var(--surface) 9px 18px)", display: "grid", placeItems: "center" }}><span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-4)" }}>your CRM, unchanged</span></div>
              <div style={{ width: 168, borderLeft: "1px solid var(--line)", background: "var(--surface)", padding: 12, display: "flex", flexDirection: "column", gap: 9 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 11.5, fontWeight: 700 }}><span style={{ fontSize: 14 }}>🦊</span>Friesen <span className="live-dot" style={{ width: 5, height: 5 }} /></div>
                <div style={{ background: "var(--accent-softer)", borderRadius: "var(--r-sm)", padding: 9, fontSize: 11, color: "var(--accent-ink)", lineHeight: 1.4 }}>Nadia drafted a follow-up for this contact.</div>
                <button className="btn btn-primary btn-sm" style={{ fontSize: 11, height: 27 }}>Approve &amp; send</button>
                <div style={{ background: "var(--surface-2)", borderRadius: "var(--r-sm)", padding: 9, fontSize: 11, color: "var(--ink-3)", lineHeight: 1.4 }}>Scout scored this lead 88/100.</div>
              </div>
            </div>
          </div>

          <div className="rg2" style={{ marginTop: 18 }}>
            <div className="card" style={{ padding: 18 }}>
              <div className="feed-ico" style={{ width: 36, height: 36, background: "var(--accent-soft)", color: "var(--accent-ink)", marginBottom: 12 }}><Icon name="plug" size={17} /></div>
              <b style={{ fontSize: 15, fontWeight: 700 }}>Embedded panel</b>
              <p style={{ fontSize: 13, color: "var(--ink-2)", lineHeight: 1.5, margin: "7px 0 14px" }}>Sidecar integrates an agent panel right into HubSpot, Salesforce, Gmail, anywhere you work. No app, no extension. Agents read the screen and suggest the next move.</p>
              <button className={"btn btn-sm " + (added ? "btn-soft" : "btn-primary")} onClick={() => setAdded(true)}>{added ? <><Icon name="check" size={13} sw={2.4} />Enabled</> : <><Icon name="layers" size={13} />Enable Sidecar</>}</button>
            </div>
            <div className="card" style={{ padding: 18 }}>
              <div className="feed-ico" style={{ width: 36, height: 36, background: "var(--green-soft)", color: "oklch(0.42 0.12 152)", marginBottom: 12 }}><Icon name="link" size={17} /></div>
              <b style={{ fontSize: 15, fontWeight: 700 }}>Direct integration</b>
              <p style={{ fontSize: 13, color: "var(--ink-2)", lineHeight: 1.5, margin: "7px 0 14px" }}>Connect via API so agents run server-side against your system of record, nothing to install, fully automated.</p>
              <button className="btn btn-ghost btn-sm" onClick={onClose}><Icon name="link" size={13} />Connect in Switchboard</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

window.ImportData = ImportData;
window.AgenticLayer = AgenticLayer;
