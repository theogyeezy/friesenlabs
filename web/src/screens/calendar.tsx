// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// calendar.jsx — Booking & calendar: upcoming meetings + a shareable booking link

const SLOTS = ["9:00 AM", "10:30 AM", "1:00 PM", "2:30 PM", "4:00 PM"];
const CAL_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"];
const CAL_HOURS = ["9a", "10a", "11a", "12p", "1p", "2p", "3p", "4p"];
const CAL_REPS = [["You", "oklch(0.56 0.17 277)"], ["Sam Lee", "oklch(0.62 0.15 18)"], ["Pat Kim", "oklch(0.62 0.13 152)"]];

function Calendar({ agents, onNavigate }) {
  const meetings = useStore((s) => s.meetings);
  const [toast, setToast] = useState(null);
  const [book, setBook] = useState(false);
  const [view, setView] = useState("week");
  const note = (m) => { setToast(m); setTimeout(() => setToast(null), 2600); };
  const groups = {};
  meetings.forEach((m) => { (groups[m.when] = groups[m.when] || []).push(m); });
  const order = ["Today", "Tomorrow", "Thu", "Fri"];
  const days = Object.keys(groups).sort((a, b) => (order.indexOf(a) + 99) % 99 - (order.indexOf(b) + 99) % 99);
  // deterministic week grid: place meetings + a couple seeded blocks
  const weekBlocks = [
    { day: 0, h: 1, dur: 1, co: "Riverside Plumbing", rep: 1, type: "Quote review", color: "oklch(0.62 0.15 18)" },
    { day: 0, h: 5, dur: 1, co: "Birch & Co.", rep: 0, type: "Discovery", color: "oklch(0.56 0.17 277)" },
    { day: 1, h: 0, dur: 1, co: "Maple Grove Vet", rep: 1, type: "Demo", color: "oklch(0.62 0.15 18)" },
    { day: 1, h: 4, dur: 2, co: "Cedar Street Yoga", rep: 2, type: "Onboarding", color: "oklch(0.62 0.13 152)" },
    { day: 2, h: 2, dur: 1, co: "Lantern Bakehouse", rep: 0, type: "Check-in", color: "oklch(0.56 0.17 277)" },
    { day: 3, h: 1, dur: 1, co: "Hollow Pine", rep: 1, type: "Intro", color: "oklch(0.62 0.15 18)", noshow: true },
    { day: 3, h: 6, dur: 1, co: "Quill & Press", rep: 2, type: "Proposal", color: "oklch(0.62 0.13 152)" },
    { day: 4, h: 3, dur: 1, co: "North Loop", rep: 0, type: "Follow-up", color: "oklch(0.56 0.17 277)" },
  ];

  return (
    <div className="screen screen-anim">
      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 7 }}>Meetings &amp; booking</div>
          <h2 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.03em" }}>Calendar</h2>
          <p style={{ color: "var(--ink-2)", fontSize: 14.5, marginTop: 5 }}>Your week, the team's availability, and a booking link customers self-schedule on.</p>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 9, flexWrap: "wrap" }}>
          <div className="seg"><button className={view === "week" ? "active" : ""} onClick={() => setView("week")}>Week</button><button className={view === "list" ? "active" : ""} onClick={() => setView("list")}>List</button></div>
          <button className="btn btn-ghost" onClick={() => { navigator.clipboard && navigator.clipboard.writeText("friesen.app/book/reyesco"); note("Booking link copied · friesen.app/book/reyesco"); }}><Icon name="link" size={16} />Copy link</button>
          <button className="btn btn-primary" onClick={() => setBook(true)}><Icon name="plus" size={16} sw={2.2} />Book</button>
        </div>
      </div>

      {view === "week" && (
        <>
          <div className="card" style={{ marginBottom: "var(--gap)", overflow: "hidden" }}>
            <div className="card-head"><h3>This week</h3><span className="sub" style={{ marginLeft: "auto" }}>{weekBlocks.length} booked · 1 no-show</span></div>
            <div style={{ overflowX: "auto", padding: "4px var(--pad) 14px" }}>
              <div className="cal-grid" style={{ minWidth: 620 }}>
                <div />
                {CAL_DAYS.map((d) => <div key={d} className="cal-dayhead">{d}</div>)}
                {CAL_HOURS.map((hr, hi) => (
                  <React.Fragment key={hr}>
                    <div className="cal-hour">{hr}</div>
                    {CAL_DAYS.map((d, di) => {
                      const b = weekBlocks.find((x) => x.day === di && x.h === hi);
                      return <div key={d} className="cal-cell">{b && (
                        <button className="cal-block" style={{ background: b.noshow ? "var(--rose-soft)" : "color-mix(in oklch, " + b.color + " 16%, var(--surface))", borderLeft: "3px solid " + (b.noshow ? "var(--rose)" : b.color), height: (b.dur * 38 - 4) + "px" }}
                          onClick={() => note(b.noshow ? b.co + " was a no-show · Echo can reschedule" : "Opening " + b.co + " · " + b.type)}>
                          <b style={{ fontSize: 11, fontWeight: 650, display: "block", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{b.co}</b>
                          <span style={{ fontSize: 9.5, color: b.noshow ? "oklch(0.48 0.14 18)" : "var(--ink-4)" }}>{b.noshow ? "no-show" : b.type}</span>
                        </button>
                      )}</div>;
                    })}
                  </React.Fragment>
                ))}
              </div>
            </div>
          </div>
          <div className="dash-grid">
            <div className="card">
              <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="users" size={15} /></div><h3>Team availability</h3><span className="sub" style={{ marginLeft: "auto" }}>today</span></div>
              <div className="card-pad" style={{ display: "flex", flexDirection: "column", gap: 11 }}>
                {CAL_REPS.map(([name, color], i) => { const booked = weekBlocks.filter((b) => b.rep === i && b.day === 0).length; const free = SLOTS.length - booked; return (
                  <div key={name} style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <div className="avatar" style={{ background: color, width: 26, height: 26, fontSize: 10 }}>{name === "You" ? "JR" : name.split(" ").map((w) => w[0]).join("")}</div>
                    <span style={{ flex: 1, fontSize: 13, fontWeight: 600 }}>{name}</span>
                    <span className="rep-bar" style={{ maxWidth: 120 }}><span style={{ width: (booked / SLOTS.length * 100) + "%", background: color }} /></span>
                    <span style={{ fontSize: 11.5, color: "var(--ink-4)", fontFamily: "var(--mono)", width: 64, textAlign: "right" }}>{free} slots free</span>
                  </div>
                ); })}
              </div>
            </div>
            <div className="card" style={{ alignSelf: "start" }}>
              <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--rose-soft)", color: "oklch(0.48 0.14 18)" }}><Icon name="bolt" size={15} /></div><h3>No-shows &amp; reschedules</h3></div>
              <div className="card-pad" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <div style={{ display: "flex", gap: 18 }}>
                  <div><div style={{ fontSize: 22, fontWeight: 770, color: "var(--rose)" }}>1</div><div style={{ fontSize: 11, color: "var(--ink-4)" }}>no-show this week</div></div>
                  <div><div style={{ fontSize: 22, fontWeight: 770 }}>3</div><div style={{ fontSize: 11, color: "var(--ink-4)" }}>rescheduled</div></div>
                  <div><div style={{ fontSize: 22, fontWeight: 770, color: "var(--green)" }}>92%</div><div style={{ fontSize: 11, color: "var(--ink-4)" }}>show rate</div></div>
                </div>
                <button className="btn btn-soft btn-sm" style={{ alignSelf: "flex-start" }} onClick={() => note("Echo is reaching out to rebook Hollow Pine…")}><Icon name="calendar" size={13} />Auto-rebook no-shows</button>
              </div>
            </div>
          </div>
        </>
      )}

      {view === "list" && (
      <div className="dash-grid">
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--gap)" }}>
          {days.map((day) => (
            <div className="card" key={day}>
              <div className="card-head"><h3>{day}</h3><span className="sub" style={{ marginLeft: "auto" }}>{groups[day].length} meeting{groups[day].length === 1 ? "" : "s"}</span></div>
              <div style={{ padding: "4px var(--pad) 12px", display: "flex", flexDirection: "column" }}>
                {groups[day].map((m) => { const a = agents[m.agent]; return (
                  <div key={m.id} style={{ display: "flex", alignItems: "center", gap: 13, padding: "12px 0", borderBottom: "1px solid var(--line-2)" }}>
                    <div style={{ textAlign: "center", minWidth: 64 }}><div style={{ fontSize: 14, fontWeight: 730 }}>{m.time}</div><div style={{ fontSize: 10.5, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>{m.dur}</div></div>
                    <div style={{ width: 3, alignSelf: "stretch", borderRadius: 99, background: m.color }} />
                    <div className="deal-co" style={{ background: m.color, width: 34, height: 34, fontSize: 12, borderRadius: 10 }}>{m.init}</div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <b style={{ fontSize: 13.5, fontWeight: 650 }}>{m.type} · {m.co}</b>
                      <div style={{ fontSize: 11.5, color: "var(--ink-4)", display: "flex", alignItems: "center", gap: 6 }}><Icon name={m.mode === "Phone" ? "phone" : "spark"} size={11} />{m.mode} · with {m.who}{a && <> · booked by {a.name}</>}</div>
                    </div>
                    <button className="btn btn-ghost btn-sm" onClick={() => note("Joining " + m.type + "…")}>{m.mode === "Phone" ? "Call" : "Join"}</button>
                    <button className="icon-btn" style={{ width: 30, height: 30 }} title="Cancel" onClick={() => { FLStore.cancelMeeting(m.id); note("Meeting canceled"); }}><Icon name="x" size={15} /></button>
                  </div>
                ); })}
              </div>
            </div>
          ))}
          {meetings.length === 0 && <div className="empty-state" style={{ padding: "50px 20px" }}><div className="es-ico"><Icon name="calendar" size={24} /></div><h4>No meetings scheduled</h4><p>Book one, or share your link and let customers self-schedule.</p></div>}
        </div>

        {/* booking link card */}
        <div className="card" style={{ alignSelf: "start" }}>
          <div className="card-head"><div className="feed-ico" style={{ width: 30, height: 30, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="link" size={15} /></div><h3>Your booking page</h3></div>
          <div className="card-pad" style={{ display: "flex", flexDirection: "column", gap: 13 }}>
            <p style={{ fontSize: 13, color: "var(--ink-2)", lineHeight: 1.5 }}>Customers pick a time and your agents handle reminders and follow-ups automatically.</p>
            <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 12px", background: "var(--surface-2)", borderRadius: "var(--r-sm)", fontFamily: "var(--mono)", fontSize: 12.5 }}>
              <Icon name="link" size={14} style={{ color: "var(--ink-4)" }} /><span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis" }}>friesen.app/book/reyesco</span>
              <button className="btn btn-ghost btn-sm" onClick={() => { navigator.clipboard && navigator.clipboard.writeText("friesen.app/book/reyesco"); note("Link copied"); }}>Copy</button>
            </div>
            <div>
              <div style={{ fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)", textTransform: "uppercase", letterSpacing: ".05em", marginBottom: 8 }}>Open slots today</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 7 }}>
                {SLOTS.map((s) => <button key={s} className="chip" style={{ height: 30, cursor: "pointer" }} onClick={() => { FLStore.addMeeting({ co: "New booking", who: "Customer", init: "NB", color: "oklch(0.56 0.17 277)", type: "Meeting", when: "Today", time: s, dur: "30 min", agent: "echo", mode: "Video" }); note("Booked " + s + " · reminders set"); }}>{s}</button>)}
              </div>
            </div>
          </div>
        </div>
      </div>
      )}

      {book && <BookModal agents={agents} onClose={() => setBook(false)} onNote={note} />}
      {toast && (
        <div style={{ position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)", zIndex: 70, background: "var(--ink)", color: "var(--bg)", borderRadius: "var(--r-md)", padding: "12px 18px", display: "flex", alignItems: "center", gap: 10, boxShadow: "var(--shadow-xl)", animation: "feed-in .3s both", maxWidth: "90vw" }}>
          <Icon name="checkCircle" size={18} /><span style={{ fontSize: 13.5, fontWeight: 600 }}>{toast}</span>
        </div>
      )}
    </div>
  );
}

function BookModal({ agents, onClose, onNote }) {
  const [co, setCo] = useState("");
  const [type, setType] = useState("Discovery call");
  const [time, setTime] = useState("2:00 PM");
  const [when, setWhen] = useState("Today");
  return (
    <div className="cmdk-scrim show" onClick={onClose} style={{ alignItems: "center", paddingTop: 0 }}>
      <div className="cmdk" style={{ maxWidth: 420 }} onClick={(e) => e.stopPropagation()}>
        <div style={{ padding: "18px 20px", borderBottom: "1px solid var(--line)", display: "flex", alignItems: "center", gap: 11 }}>
          <div className="feed-ico" style={{ width: 32, height: 32, background: "var(--accent-soft)", color: "var(--accent-ink)" }}><Icon name="calendar" size={16} /></div>
          <b style={{ fontSize: 16, fontWeight: 720, flex: 1 }}>Book a meeting</b>
          <button className="icon-btn" onClick={onClose}><Icon name="x" size={18} /></button>
        </div>
        <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 13 }}>
          <div className="wf-field"><label>Customer / company</label><input autoFocus value={co} onChange={(e) => setCo(e.target.value)} placeholder="e.g. Birch & Co." /></div>
          <div className="wf-field"><label>Meeting type</label><select value={type} onChange={(e) => setType(e.target.value)}><option>Discovery call</option><option>Demo</option><option>Quote review</option><option>Check-in</option></select></div>
          <div style={{ display: "flex", gap: 10 }}>
            <div className="wf-field" style={{ flex: 1 }}><label>Day</label><select value={when} onChange={(e) => setWhen(e.target.value)}><option>Today</option><option>Tomorrow</option><option>Thu</option><option>Fri</option></select></div>
            <div className="wf-field" style={{ flex: 1 }}><label>Time</label><select value={time} onChange={(e) => setTime(e.target.value)}>{SLOTS.map((s) => <option key={s}>{s}</option>)}</select></div>
          </div>
          <button className="btn btn-primary" disabled={!co.trim()} onClick={() => { FLStore.addMeeting({ co: co.trim(), who: co.trim(), init: co.trim().slice(0, 2).toUpperCase(), color: "oklch(0.56 0.17 277)", type, when, time, dur: "30 min", agent: "echo", mode: "Video" }); onClose(); onNote && onNote("Meeting booked · agent will send a reminder"); }}><Icon name="check" size={16} sw={2.2} />Book &amp; set reminders</button>
        </div>
      </div>
    </div>
  );
}

window.Calendar = Calendar;
