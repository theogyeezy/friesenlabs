// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// tour.jsx — first-run guided product tour

const TOUR_STEPS = [
  { route: "dashboard", title: "Welcome to Friesen Labs 👋", body: "This is your Command Center, the whole business at a glance. Switch time ranges, pick a view, or just ask it anything. Start here every morning." },
  { route: "crm", title: "Uplift, your agentic CRM", body: "Every deal has an agent working it. Drag deals across the pipeline; the Performance tab tracks reps, forecast and win/loss. Use it to run your whole sales pipeline." },
  { route: "contacts", title: "Contacts & companies", body: "Every person and company you work with, with their deals and full history. Use it to look someone up before a call." },
  { route: "billing", title: "Billing, quote to cash", body: "Send a quote, get it e-signed, turn it into an invoice and collect, agents chase what's overdue. Use it whenever it's time to get paid." },
  { route: "calendar", title: "Calendar & booking", body: "Share a booking link customers self-schedule on; agents send reminders. Use it to fill your week without the back-and-forth." },
  { route: "reviews", title: "Reputation", body: "Agents ask happy customers for a review at the right moment and track referrals. Use it to turn wins into word-of-mouth." },
  { route: "templates", title: "Templates & sequences", body: "Saved email/SMS templates and multi-step cadences your agents personalize. Use it so outreach is never from scratch." },
  { route: "frontline", title: "Frontline support desk", body: "Pip answers routine tickets automatically and routes the rest to you. Use it to keep support fast without hiring." },
  { route: "workflows", title: "Workflows you build by talking", body: "Drag steps to compose automations, or describe what you want and the AI builds it. Use it to automate anything repetitive." },
  { route: "approvals", title: "Greenlight keeps you in control", body: "Nothing risky happens without you. Review, tweak and approve in one tap. Use it to stay in command while agents do the work." },
  { route: "agents", title: "Agents & Studio", body: "Hire ready-made agents or build your own in the visual Studio, give them skills and set their autonomy. Use it to grow your team." },
  { route: "knowledge", title: "Knowledge", body: "Upload your handbook, pricing and docs; we index them so every agent grounds its answers on what your business knows." },
  { route: "cortex", title: "Cortex, private intelligence", body: "Grounds agents on your knowledge and gets sharper with every decision, with optional plugins to train private models. That's your moat." },
  { route: "integrations", title: "Switchboard & Sidecar", body: "Connect HubSpot, Gmail, Stripe and more, and Sidecar puts agents on top of the tools you already use. Use it to keep your stack." },
  { route: "security", title: "Security & control", body: "One kill switch, guardrails, and a full audit trail. Use it any time you want to pause or review what agents are doing." },
  { route: "dashboard", title: "You're all set 🎉", body: "Everything's reachable from the left sidebar, grouped by Workspace, Uplift CRM, Agents and more. Your agents are already working, you just steer. Welcome aboard!" },
];

function ProductTour({ onNavigate, onClose }) {
  const [i, setI] = useState(0);
  const step = TOUR_STEPS[i];
  useEffect(() => { if (step.route && onNavigate) onNavigate(step.route); }, [i]);
  useEffect(() => {
    const k = (e) => { if (e.key === "Escape") onClose(); if (e.key === "ArrowRight") next(); if (e.key === "ArrowLeft") setI((x) => Math.max(0, x - 1)); };
    window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k);
  });
  const next = () => { if (i >= TOUR_STEPS.length - 1) onClose(); else setI((x) => x + 1); };
  const last = i === TOUR_STEPS.length - 1;

  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 95, display: "grid", placeItems: "end center", paddingBottom: "6vh", pointerEvents: "none" }}>
      <div style={{ position: "absolute", inset: 0, background: "oklch(0.2 0.02 60 / .28)", pointerEvents: "auto" }} onClick={onClose} />
      <div style={{ position: "relative", pointerEvents: "auto", width: "min(460px, 92vw)", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-xl)", boxShadow: "var(--shadow-xl)", padding: 24, animation: "onb-in .3s both" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
          <div className="brand-mark" style={{ width: 30, height: 30 }}><Logo size={18} /></div>
          <div style={{ flex: 1, display: "flex", gap: 5 }}>
            {TOUR_STEPS.map((_, j) => <span key={j} style={{ flex: 1, height: 4, borderRadius: 99, background: j <= i ? "var(--accent)" : "var(--line)", transition: "background .3s" }} />)}
          </div>
          <button className="icon-btn" style={{ width: 28, height: 28 }} onClick={onClose}><Icon name="x" size={16} /></button>
        </div>
        <h2 style={{ fontSize: 20, fontWeight: 760, letterSpacing: "-.02em" }}>{step.title}</h2>
        <p style={{ fontSize: 14, color: "var(--ink-2)", lineHeight: 1.6, marginTop: 8 }}>{step.body}</p>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 20 }}>
          <span style={{ fontSize: 12, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>{i + 1} / {TOUR_STEPS.length}</span>
          <div style={{ flex: 1 }} />
          {!last && <button className="btn btn-ghost btn-sm" onClick={onClose}>Skip tour</button>}
          {i > 0 && <button className="btn btn-ghost btn-sm" onClick={() => setI((x) => x - 1)}>Back</button>}
          <button className="btn btn-primary btn-sm" onClick={next}>{last ? "Start exploring" : "Next"}<Icon name="arrowRight" size={14} sw={2.2} /></button>
        </div>
      </div>
    </div>
  );
}

window.ProductTour = ProductTour;
