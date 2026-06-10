// @ts-nocheck
import React from "react";
import { createPortal } from "react-dom";
import "../globals";
import mattPhoto from "../assets/matt-yee.jpg";
import nickPhoto from "../assets/nick-friesen.jpg";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;

// ── Bespoke editorial icon set ───────────────────────────────────────────────
// Original line glyphs on a 24-grid (rounded joins, a recurring filled "live dot" for the agentic
// pulse). Each is grounded in the product it marks, so the set reads as designed rather than a
// generic Lucide/Feather default. Names without a bespoke glyph fall back to the app's Icon.
function lpGlyph(name) {
  switch (name) {
    case "grid": return <><rect x="3" y="4.5" width="18" height="15" rx="2.6" /><path d="M3 9.2h18" /><path d="M6.5 13h4.5M6.5 16h8" /><circle cx="16.8" cy="13.6" r="1.4" fill="currentColor" stroke="none" /></>;
    case "users": return <><circle cx="8.6" cy="8" r="3" /><circle cx="16.2" cy="9.4" r="2.4" /><path d="M3.4 19a5.2 5.2 0 019.2-3.3" /><path d="M14.4 19a4.2 4.2 0 016.2-3.2" /></>;
    case "inbox": return <><path d="M4 7a3 3 0 013-3h10a3 3 0 013 3v6a3 3 0 01-3 3H9l-4.2 3.4a.5 .5 0 01-.8-.4V7z" /><circle cx="9" cy="10" r="1" fill="currentColor" stroke="none" /><circle cx="12.5" cy="10" r="1" fill="currentColor" stroke="none" /><circle cx="16" cy="10" r="1" fill="currentColor" stroke="none" /></>;
    case "workflow": return <><rect x="3" y="4" width="6" height="5" rx="1.5" /><rect x="15" y="4" width="6" height="5" rx="1.5" /><rect x="9" y="15" width="6" height="5" rx="1.5" /><path d="M6 9v3a2 2 0 002 2h1" /><path d="M18 9v3a2 2 0 01-2 2h-1" /></>;
    case "approve": return <><rect x="3.5" y="3.5" width="17" height="17" rx="4.2" /><path d="M7.8 12.2l2.9 2.9 5.7-5.8" /></>;
    case "spark": return <><path d="M12 3c.45 4.2 1.6 5.7 6 6-4.4 .3-5.55 1.95-6 6-.45-4.05-1.6-5.7-6-6 4.4-.3 5.55-1.8 6-6z" /><path d="M18.6 4.4c.12 1.1 .45 1.5 1.6 1.6-1.15 .1-1.48 .5-1.6 1.6-.12-1.1-.45-1.5-1.6-1.6 1.15-.1 1.48-.5 1.6-1.6z" fill="currentColor" stroke="none" /></>;
    case "plug": return <><circle cx="12" cy="12" r="3.1" /><path d="M12 8.9V4M12 20v-4.9M8.9 12H4M20 12h-4.9" /><circle cx="12" cy="4" r="1.1" fill="currentColor" stroke="none" /><circle cx="12" cy="20" r="1.1" fill="currentColor" stroke="none" /><circle cx="4" cy="12" r="1.1" fill="currentColor" stroke="none" /><circle cx="20" cy="12" r="1.1" fill="currentColor" stroke="none" /></>;
    case "layers": return <><rect x="3.5" y="6.5" width="11" height="13" rx="2.2" /><path d="M9 6.5V5.2A2.2 2.2 0 0111.2 3h7A2.2 2.2 0 0120.5 5.2v11A2.2 2.2 0 0118.3 18.4h-1.3" /></>;
    case "doc": return <><path d="M6.5 3.6h6.8L18 8.3V20.4H6.5z" /><path d="M13 3.6V8h4.6" /><path d="M9.4 12.2h5.2M9.4 15.4h5.2" /></>;
    case "network": return <><circle cx="12" cy="5" r="2" /><circle cx="5" cy="13" r="2" /><circle cx="19" cy="13" r="2" /><circle cx="12" cy="19.5" r="2" /><path d="M10.5 6.3L6.4 11.6M13.5 6.3l4.1 5.3M6.6 14.4l4 3.6M17.4 14.4l-4 3.6" /></>;
    case "shield": return <><path d="M12 3.4l7.2 2.6v4.5c0 4.9-3.1 7.9-7.2 9.7-4.1-1.8-7.2-4.8-7.2-9.7V6l7.2-2.6z" /><path d="M12 9.2v3.2" /><circle cx="12" cy="14.6" r="0.7" fill="currentColor" stroke="none" /></>;
    case "bolt": return <path d="M13.2 3.4l-7.4 9.6H11l-1.1 7.6 7.6-9.8H11l2.2-7.4z" />;
    case "check": return <path d="M5 12.5l4.4 4.4L19 7.2" />;
    case "checkCircle": return <><circle cx="12" cy="12" r="8.6" /><path d="M8.4 12.2l2.6 2.6 4.6-5.1" /></>;
    case "arrowRight": return <><path d="M4.5 12h13.5" /><path d="M13 6.3l5.7 5.7-5.7 5.7" /></>;
    case "link": return <><path d="M10 14.2l4-4.2" /><path d="M8.5 12.2l-1.9 2a3.2 3.2 0 004.6 4.4l1.9-2" /><path d="M15.5 11.8l1.9-2a3.2 3.2 0 00-4.6-4.4l-1.9 2" /></>;
    case "calendar": return <><rect x="3.5" y="5" width="17" height="15.5" rx="2.6" /><path d="M3.5 9.6h17M8 3v4M16 3v4" /></>;
    case "mail": return <><rect x="3.5" y="5.5" width="17" height="13" rx="2.4" /><path d="M4.6 7.8l7.4 5.4 7.4-5.4" /></>;
    case "send": return <><path d="M20.5 3.5L3.5 11l6.6 2.4L12.5 20 20.5 3.5z" /><path d="M10.1 13.4L20.5 3.5" /></>;
    case "trend": return <><path d="M4 16.5l5-5 3.5 2.5L20 6" /><path d="M15.5 6H20v4.5" /></>;
    case "clock": return <><circle cx="12" cy="12" r="8.4" /><path d="M12 7.4V12l3 2" /></>;
    case "quote": return <path d="M9.2 7.2c-2.5 1-3.9 2.9-3.9 5.6V17h4.7v-4.6H7.3c0-1.5 .8-2.6 2.5-3.3l-.6-1.9zM18.6 7.2c-2.5 1-3.9 2.9-3.9 5.6V17h4.7v-4.6h-2.7c0-1.5 .8-2.6 2.5-3.3l-.6-1.9z" fill="currentColor" stroke="none" />;
    case "plus": return <path d="M12 5v14M5 12h14" />;
    case "x": return <path d="M6 6l12 12M18 6L6 18" />;
    case "chevDown": return <path d="M5.5 9l6.5 6.6L18.5 9" />;
    case "chevL": return <path d="M14.5 5.5L8 12l6.5 6.5" />;
    case "megaphone": return <><path d="M4 10.5v3l11 4.6V6L4 10.5z" /><path d="M15 9.2a4 4 0 010 5.6" /><path d="M7 14.3v3.4a1.6 1.6 0 003.2 0V15.4" /></>;
    case "gauge": return <><path d="M4.5 16.5a8 8 0 1115 0" /><path d="M12 16.5l4.2-4.6" /><circle cx="12" cy="16.5" r="1.1" fill="currentColor" stroke="none" /></>;
    case "play": return <path d="M7.6 5.4l11 6.6-11 6.6z" />;
    default: return null;
  }
}
function onActivate(fn) { return (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fn(e); } }; }
function LpIcon(props) {
  const inner = lpGlyph(props && props.name);
  if (inner == null) return <Icon {...props} />;
  const size = (props && props.size) || 16;
  const sw = (props && props.sw) || 1.6;
  return <svg className={props && props.className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round" style={props && props.style} aria-hidden="true">{inner}</svg>;
}
// landing.jsx, Friesen Labs marketing site

const LP_PRODUCTS = [
  { id: "command", name: "Command Center", cat: "Governance", icon: "grid", tone: "indigo", blurb: "Every agent, approval and metric in one calm morning view.",
    long: "Your whole business in one calm morning view, what your agents did overnight, what needs your eyes, and how the numbers are trending.",
    features: [["Daily briefing", "A plain-English recap of overnight agent work"], ["Live activity feed", "Watch every agent action as it happens"], ["Approvals inline", "Greenlight items surface right on the dashboard"], ["Pipeline & workload", "Animated charts for pipeline and agent load"]] },
  { id: "uplift", name: "Uplift", cat: "CRM", icon: "users", tone: "rose", blurb: "The complete agentic CRM: contacts, pipeline, getting paid, scheduling, reputation and outreach.",
    long: "An agentic CRM where every deal has an agent working it, end to end: contacts and companies, a pipeline that moves itself, quotes through to payment, scheduling, reviews and outreach. Or keep your current CRM and the agents work inside it just the same.",
    features: [["Contacts and companies", "Every contact, company, deal, and interaction together"], ["Self-moving pipeline", "Move a deal and the agent picks up the next step"], ["Quote to paid", "Send quotes, e-sign, invoice, and collect while agents chase the overdue ones"], ["Scheduling & booking", "A link customers self-book, with auto reminders"], ["Reputation", "Agents ask happy customers for reviews; track referrals"], ["Templates & sequences", "Email/SMS outreach your agents personalize and run"], ["Gamified selling", "Streaks, quests, leaderboards & rewards reps love"], ["Bring your own CRM", "Works with HubSpot, Salesforce & more"]] },
  { id: "frontline", name: "Frontline", cat: "Support", icon: "inbox", tone: "green", blurb: "An autonomous support desk that deflects the routine and routes the rest to you.",
    long: "A shared inbox where a support agent answers the routine questions automatically, and only hands you the tickets that truly need a human.",
    features: [["AI deflection", "Routine tickets resolved with no human touch"], ["One shared inbox", "Email, chat, web form & social in one queue"], ["Drafts you approve", "Sensitive replies route through Greenlight"], ["Knowledge gaps", "Pip flags questions to add to your help center"]] },
  { id: "workflows", name: "Workflows", cat: "Automation", icon: "workflow", tone: "amber", blurb: "Compose agent automations by dragging blocks, or just describe them.",
    long: "Compose agent automations by dragging blocks, or just describe what you want and the AI builds the workflow for you.",
    features: [["Visual builder", "Drag, connect, zoom and pan a node canvas"], ["Prompt-to-build AI", "Describe it, the AI designs it"], ["Your agents as steps", "Drop any agent you've made into a flow"], ["Approval gates", "Pause for Greenlight anywhere in the flow"]] },
  { id: "greenlight", name: "Greenlight", cat: "Approvals", icon: "approve", tone: "amber", blurb: "Every agent action waits for your one-tap sign-off.",
    long: "Human-in-the-loop control for everything your agents do. Review, edit and approve in one tap, or let the routine stuff run itself.",
    features: [["One-tap approve", "Approve or decline from anywhere"], ["Inline editing", "Tweak the agent's draft before it sends"], ["Bulk approve", "Clear the routine queue in one go"], ["Policy & spend limits", "Auto-approve under your thresholds"]] },
  { id: "agents", name: "Agents", cat: "Workforce", icon: "spark", tone: "indigo", blurb: "Build agents in a visual Studio, give them skills, set their autonomy.",
    long: "Hire ready-made agents or build your own in a visual Studio: name them, give them composable skills, ground them on your knowledge, and set exactly how much they can do.",
    features: [["Agent Studio", "Build an agent and watch it work in a live preview before you ship it"], ["Skill marketplace", "Composable capabilities you mix, build & share"], ["Autonomy & guardrails", "Suggest-only to fully autonomous, with limits"], ["Managed runtime", "Optimized, hosted, nothing to set up"]] },
  { id: "integration", name: "Switchboard", cat: "Connect", icon: "plug", tone: "green", blurb: "Plug into HubSpot, Stripe, Gmail and 18+ tools you already use.",
    long: "Keep every tool you already pay for. Your CRM stays the source of truth, and your agents work right inside it, reading and writing as they go. No migration, no rip and replace.",
    features: [["18+ connectors", "CRM, email, calendar, payments, support"], ["System of record", "Your CRM stays the source of truth"], ["Two-way sync", "Nothing lives in two places"], ["Write-back", "Approved actions push back to your tools"]] },
  { id: "sidecar", name: "Sidecar", cat: "Agentic layer", icon: "layers", tone: "indigo", blurb: "Put your agents to work on the tools you already use, keep your whole stack.",
    long: "The agentic layer of your suite. Powered by your Switchboard connections, Sidecar's agents work on top of the tools you already use, enriching, drafting and advancing work, with no migration and your tools staying the system of record.",
    features: [["Part of your suite", "Software in your Friesen workspace, no install, no plugin"], ["Powered by Switchboard", "Works over your connected tools & system of record"], ["Agents do the work", "Enrich, draft, follow up and advance deals automatically"], ["Same guardrails", "Approvals, policy & kill switch apply everywhere"]] },
  { id: "knowledge", name: "Knowledge", cat: "Intelligence", icon: "doc", tone: "amber", blurb: "Upload what your business knows; we host it as searchable context for everything.",
    long: "Upload your handbook, SOPs, pricing and help center once, and every agent answers from your actual business instead of a generic guess. We turn your docs into private knowledge bases the whole suite grounds its answers on.",
    features: [["Hosted knowledge bases", "We host & index your docs, private to your instance"], ["RAG out of the box", "Chunked, embedded and searchable automatically"], ["Grounds everything", "Context for Uplift, Frontline, Workflows & agents"], ["Test retrieval", "Ask a base a question and see exactly what it returns"]] },
  { id: "cortex", name: "Cortex", cat: "Intelligence", icon: "network", tone: "amber", blurb: "Knowledge-grounded intelligence, with optional plugins to compound and train private models.",
    long: "The intelligence layer for your agents. Knowledge grounding is included, your agents answer from your own knowledge bases. Add the Flywheel and Fine-tuning plugins to compound on every decision and train private models on your own data, the moat no competitor can copy.",
    features: [["Knowledge (included)", "Agents ground every answer on your hosted knowledge"], ["Flywheel plugin", "Every prediction becomes an outcome that retrains the model, compounding over time"], ["Fine-tuning plugin", "Turn your data into a private model that runs on your hardware"], ["Data gravity", "Every decision your agents make sharpens a model only you own, a head start no competitor can copy"]] },
  { id: "security", name: "Security & Control", cat: "Trust", icon: "shield", tone: "indigo", blurb: "A kill switch, guardrails, approvals and a full audit trail, included free.",
    long: "One switch flips every agent between Live, Analyze-only and a full Kill switch, backed by guardrails, role permissions, anomaly monitoring and an audit log.",
    features: [["Kill switch", "One tap stops every agent instantly"], ["Granular guardrails", "Spend caps, PII redaction, bulk limits, two-person approval"], ["Roles & access", "2FA, SSO, IP allowlist, session limits"], ["Audit & monitoring", "Every action logged, anomalies auto-paused"]] },
];
const LP_TONE = {
  indigo: ["var(--accent-soft)", "var(--accent-ink)"], amber: ["var(--amber-soft)", "oklch(0.5 0.12 60)"],
  green: ["var(--green-soft)", "oklch(0.42 0.12 152)"], rose: ["var(--rose-soft)", "oklch(0.48 0.14 18)"],
};
const FEAT_VIVID = {
  indigo: "var(--accent)", amber: "oklch(0.7 0.14 65)", green: "oklch(0.62 0.13 152)", rose: "oklch(0.62 0.15 18)",
};
const LP_STACK = [
  { eyebrow: "Layer 5 · Oversight", fc: "var(--accent)", h: "You stay in command", desc: "Watch, approve and control everything from one place.", pills: [["Command Center", "grid", "indigo"], ["Greenlight", "approve", "amber"], ["Security & Control", "shield", "indigo"]] },
  { eyebrow: "Layer 4 · Where the work happens", fc: "oklch(0.62 0.15 18)", h: "Your business, run by agents", desc: "Sales, support and automations, done for you.", pills: [["Uplift", "users", "rose"], ["Frontline", "inbox", "green"], ["Workflows", "workflow", "amber"]] },
  { eyebrow: "Layer 3 · The workforce", fc: "var(--accent)", h: "Your agents", desc: "A crew you name, shape and set loose, working on top of everything below.", pills: [["Agents", "spark", "indigo"], ["Sidecar", "layers", "indigo"]] },
  { eyebrow: "Layer 2 · The intelligence", fc: "oklch(0.7 0.14 65)", h: "Knowledge & private brains", desc: "Hosted knowledge grounds every agent; optional plugins train private models and compound over time, your moat.", pills: [["Knowledge", "doc", "amber"], ["Cortex", "network", "amber"]] },
  { eyebrow: "Layer 1 · The foundation", fc: "oklch(0.62 0.13 152)", h: "Your tools & data", desc: "Connect what you already run on, your CRM stays the system of record, no migration.", pills: [["Switchboard", "plug", "green"], ["Your CRM, inbox, payments…", "link", "green"]] },
];
const LP_ROI = [
  { num: "1 owner", b: "The output of a team", p: "Run a bigger business without adding headcount, agents cover the work of several hires." },
  { num: "10+ hrs", b: "Back every week", p: "Hand off the busywork, research, follow-ups, quoting, triage, and get your time back." },
  { num: "~pennies", b: "Per task, not $25/hr", p: "Agents work for a fraction of the cost of the manual hours they replace." },
  { num: "24/7", b: "Never clocks out", p: "Agents work nights, weekends and holidays, your pipeline keeps moving while you rest." },
];
const LP_ENABLE_OWNER = ["Grow without growing payroll or overhead", "Stay in control and sign off on anything risky in Greenlight", "Open Command Center and see exactly what your agents did overnight", "Put saved time and money straight back into growth"];
const LP_ENABLE_TEAM = ["Agents take the busywork, data entry, follow-ups, ticket triage", "Your people focus on relationships, judgment and closing", "Everyone gets an agent teammate, not a pink slip", "Level up your team's output without burning them out"];
const LP_RESEARCH = [
  { tag: "Agents", date: "May 2026", readTime: "9 min", title: "Guardrails that small businesses actually trust",
    blurb: "How a one-tap kill switch and tiered autonomy change adoption of autonomous agents among non-technical owners.",
    abstract: "We study what makes a small-business owner comfortable letting an AI agent act on its own. Across a field deployment with 214 businesses, trust in autonomy was driven less by model quality than by the legibility and reversibility of control. A one-tap kill switch and a three-tier autonomy ladder raised the share of actions owners allowed agents to take unsupervised from 31% to 78% over eight weeks.",
    body: [
      { h: "Background", p: "Autonomy is the central promise of agentic software and its central fear. For non-technical owners, the question is rarely 'is the model good enough?' It is 'what happens when it's wrong, and can I stop it?' We hypothesized that perceived control, not raw accuracy, gates adoption." },
      { h: "Method", p: "We instrumented 214 small businesses on the Friesen platform over eight weeks. Each agent could run at one of three autonomy tiers: Suggest (drafts only), Ask-first (acts on approval), and Autonomous (acts within guardrails). We exposed a persistent, one-tap kill switch and logged every escalation, approval, reversal, and tier change." },
      { h: "Findings", p: "Owners who used the kill switch even once were 2.6x more likely to later promote an agent to full autonomy, the safety net encouraged exploration rather than discouraging it. Tiered autonomy outperformed a binary on/off control: the Ask-first tier acted as a trust on-ramp, with 64% of agents graduating to Autonomous within six weeks. Reversibility mattered more than accuracy in survey responses by a wide margin." },
      { h: "Implications", p: "Designers of agentic products should treat control surfaces as first-class features, not settings buried in a menu. A visible kill switch, graduated autonomy, and human-in-the-loop approval for sensitive actions convert fear into adoption. We ship all three as defaults." },
    ] },
  { tag: "Flywheel", date: "Apr 2026", readTime: "11 min", title: "Compounding intelligence from everyday decisions",
    blurb: "Turning each prediction-to-outcome loop into private, defensible model improvements, the data-gravity moat.",
    abstract: "We describe a closed-loop system that converts ordinary business decisions, a lead scored, a quote sent, a ticket resolved, into labeled training examples, and we measure how a per-business model improves as that loop runs. After 12 weeks, per-business fine-tuned models beat a strong generic baseline by 14.2 points of task accuracy, with gains concentrated exactly where each business is idiosyncratic.",
    body: [
      { h: "The loop", p: "Every agent prediction is logged with its features and the model version that produced it. When the real-world outcome lands (the deal closes, the customer replies, the refund is issued), it backfills the trace, producing a labeled example no competitor has access to. Scheduled retraining promotes a new champion only when held-out metrics improve." },
      { h: "Why it compounds", p: "Generic models are good on average and mediocre on the specific. A plumbing supplier and a yoga studio have different definitions of a 'good lead.' Because the flywheel trains on each business's own closed loops, accuracy climbs precisely where generic models are weakest. We observed monotonic improvement across 11 of 12 weeks." },
      { h: "Data gravity", p: "The accumulated decision history is not exportable as a feature, it is the moat. A competitor could copy the UI overnight but not the business's labeled outcome history. Switching cost rises with every closed loop, which is good for the business (better agents) and durable for the platform." },
      { h: "Safeguards", p: "We cap influence of any single example, require metric improvement before promotion, and keep a human-auditable trail of why each champion changed. Private models can run on the business's own hardware." },
    ] },
  { tag: "Support", date: "Mar 2026", readTime: "7 min", title: "Support deflection without the cold-robot feeling",
    blurb: "Measuring CSAT when an agent answers first: what tone, escalation and grounding move the needle.",
    abstract: "Automated first-response can wreck customer satisfaction or improve it. In a study of 96,000 support conversations across 38 small businesses, agent-first handling raised CSAT by 6 points when three conditions held: answers were grounded in the business's own docs, tone matched the brand, and escalation to a human was fast and obvious. Without grounding, CSAT fell.",
    body: [
      { h: "Setup", p: "We compared human-first and agent-first handling across 96,000 conversations. The support agent (Pip) drafted or auto-sent replies grounded in each business's help center, with sensitive actions (refunds, account changes) routed to a human via approval." },
      { h: "What helped", p: "Grounding was decisive: replies citing the business's own documentation scored 19% higher than ungrounded ones. A one-line, always-visible 'talk to a human' path raised satisfaction even among customers who never used it. Matching brand tone closed most of the perceived warmth gap." },
      { h: "What hurt", p: "Confidently wrong answers were far more damaging than 'let me get a teammate.' Latency theater (fake typing delays) annoyed users. Deflecting clearly emotional or high-stakes messages eroded trust, so we route those to humans automatically." },
      { h: "Takeaway", p: "Deflection is a quality strategy, not a cost strategy. Done with grounding, tone, and fast escalation, customers preferred it, deflected tickets resolved in a fraction of the time without a satisfaction penalty." },
    ] },
  { tag: "Adoption", date: "Feb 2026", readTime: "8 min", title: "Why reps abandon CRMs, and what fixes it",
    blurb: "A field study on gamification and agent-assisted selling lifting daily active usage by 3.4x.",
    abstract: "Salespeople famously avoid CRMs because logging work feels like overhead with no payoff. We tested whether removing the data-entry burden (agents log automatically) plus rewarding activity (points, streaks, leaderboards tied to real outcomes) would change behavior. Across 51 teams, daily active CRM usage rose 3.4x and logged follow-ups rose 2.1x over six weeks.",
    body: [
      { h: "The problem", p: "Traditional CRMs ask reps to feed the system so managers can report on it, value flows up, not back to the rep. Predictably, reps under-log, data rots, and forecasts suffer. We asked whether the incentive could be inverted." },
      { h: "Intervention", p: "Two changes: (1) agents handle the logging, enrichment and follow-up drafting automatically, removing busywork; (2) a gamification layer rewards real selling activity with points, daily streaks, quests and a leaderboard, with rewards tied to outcomes like closes, not vanity metrics." },
      { h: "Results", p: "Daily active usage rose 3.4x and logged follow-ups 2.1x. Crucially, gains persisted past the novelty window when rewards were tied to genuine outcomes; teams rewarded for vanity metrics regressed by week four. Reps reported the CRM felt like an ally rather than a tax." },
      { h: "Design notes", p: "Reward outcomes, not activity-for-its-own-sake. Make the agent remove work before you ask for engagement. Celebrate wins visibly. A CRM reps want to open is, on its own, a revenue intervention." },
    ] },
];
const LP_DEMOS = [
  { id: "command", tab: "Command Center", cat: "Command Center", title: "See your whole business in one morning view", desc: "Watch your agents work in real time, see what needs you, and track the numbers that matter, all in one calm morning view.", bullets: ["Live agent activity feed", "Animated metrics & pipeline", "Approvals surface right here"], Demo: () => <CommandDemo /> },
  { id: "agents", tab: "Agents", cat: "Agents", title: "Agents that actually do the work", desc: "Give each agent a name, a face and a job. They research, write, send and book, around the clock, and hand the judgment calls to you.", bullets: ["Name & re-skin any agent", "Set autonomy from suggest-only to fully autonomous", "Guardrails keep them on-policy"], Demo: () => <FoxDemo /> },
  { id: "uplift", tab: "Uplift CRM", cat: "Uplift", title: "A pipeline that moves itself", desc: "Drag a deal and the assigned agent picks up the next step. Or keep your current CRM, the agents work inside it just the same.", bullets: ["Drag-and-drop kanban", "An agent on every deal", "Bring your own CRM"], Demo: () => <KanbanDemo /> },
  { id: "frontline", tab: "Frontline", cat: "Frontline", title: "Support that handles itself", desc: "Pip answers the routine questions the moment they land, order status, hours, bookings, and only routes the tricky, sensitive ones to you.", bullets: ["Watch tickets auto-deflect live", "One inbox for every channel", "Refunds & returns route to a human"], Demo: () => <SupportDemo /> },
  { id: "workflows", tab: "Workflows", cat: "Workflows", title: "Automate anything, no code", desc: "Drag blocks to compose a workflow, or describe it in plain English and the AI builds it for you. Run it and watch the agents go.", bullets: ["Drag-and-drop or prompt-to-build", "Drop in your own agents", "Pause for approval anywhere"], Demo: () => <WorkflowDemo /> },
  { id: "greenlight", tab: "Greenlight", cat: "Greenlight", title: "You stay in control", desc: "Nothing risky happens without you. Review the agent's draft, edit it, and approve with one tap, or let the routine stuff run itself.", bullets: ["One-tap approve or decline", "Edit the draft inline", "Set spend & policy limits"], Demo: () => <GreenlightDemo /> },
  { id: "integration", tab: "Switchboard", cat: "Switchboard", title: "Plug into your whole stack", desc: "Connect the tools you already run on. Your agents read and write to each, and your CRM can stay the system of record.", bullets: ["18+ connectors", "Two-way sync & write-back", "Bring your own CRM"], Demo: () => <IntegrationDemo /> },
  { id: "sidecar", tab: "Sidecar", cat: "Sidecar", title: "Keep your stack. Add the agents.", desc: "Connect your tools in Switchboard and Sidecar's agents go to work on top of them, enriching, drafting and advancing deals, surfacing everything they do inside Friesen.", bullets: ["Works on your connected tools", "Powered by Switchboard", "Same guardrails everywhere"], Demo: () => <SidecarDemo /> },
  { id: "security", tab: "Security", cat: "Security & Control", title: "You're always in control", desc: "Flip every agent between Live, Analyze-only and a full Kill switch in one tap, and toggle the guardrails that keep them on-policy. Stop everything the moment you need to.", bullets: ["One-tap kill switch", "Granular guardrails", "Included free in every plan"], Demo: () => <SecurityDemo /> },
  { id: "cortex", tab: "Cortex", cat: "Cortex", title: "Intelligence that compounds.", desc: "Cortex grounds your agents on your data, trains private models, and gets sharper with every decision they make. Run a cycle and watch the accuracy climb.", bullets: ["Private models on your data", "Grounded on your knowledge", "Compounds into a moat"], Demo: () => <CortexDemo /> },
];
const LP_MODULES = [
  { id: "command", name: "Command Center", icon: "grid", tone: "indigo", price: 49, req: true, blurb: "The agentic command center" },
  { id: "agents", name: "Agents", icon: "spark", tone: "indigo", price: 39, blurb: "Studio, skills & your agent team" },
  { id: "workflows", name: "Workflows", icon: "workflow", tone: "amber", price: 39, blurb: "Automations, drag or by prompt" },
  { id: "greenlight", name: "Greenlight", icon: "approve", tone: "amber", price: 25, blurb: "Human-in-the-loop approvals" },
  { id: "frontline", name: "Frontline", icon: "inbox", tone: "green", price: 39, blurb: "Autonomous support desk" },
  { id: "uplift", name: "Uplift CRM", icon: "users", tone: "rose", price: 49, blurb: "Agentic CRM (optional)" },
  { id: "knowledge", name: "Knowledge", icon: "doc", tone: "amber", price: 25, blurb: "Hosted knowledge bases (RAG) for your whole suite" },
  { id: "cortex", name: "Cortex", icon: "network", tone: "amber", price: 45, blurb: "Knowledge grounding + Flywheel & Fine-tuning plugins" },
  { id: "integration", name: "Switchboard", icon: "plug", tone: "green", price: 29, blurb: "Connect 18+ tools incl. your CRM" },
  { id: "sidecar", name: "Sidecar", icon: "layers", tone: "indigo", price: 35, blurb: "Agents on top of your existing tools" },
];
const LP_PLANS = {
  keepcrm: { label: "Keep my CRM", mods: ["command", "agents", "workflows", "greenlight", "integration", "sidecar"], byo: true },
  growth: { label: "Growth Suite", mods: ["command", "agents", "workflows", "greenlight", "uplift"], byo: false },
  support: { label: "Sales + Support", mods: ["command", "agents", "workflows", "greenlight", "uplift", "frontline"], byo: false },
  everything: { label: "Everything", mods: ["command", "agents", "workflows", "greenlight", "uplift", "frontline", "knowledge", "cortex", "integration", "sidecar"], byo: false },
};

const LP_TESTIMONIALS = [
  { name: "Aisha Rahman", role: "Owner, Lantern Bakehouse", init: "LB", color: "oklch(0.66 0.14 50)", metric: "11 hrs/week saved", quote: "Margo quotes every catering inquiry before I've had my coffee. I just glance, approve, and it's sent. It feels like I hired a whole sales team." },
  { name: "Gus Hartley", role: "Owner, Riverside Plumbing", init: "RP", color: "oklch(0.62 0.13 152)", metric: "73% tickets deflected", quote: "Pip answers the 'are you open?' and 'where's my tech?' questions instantly. My phone stopped ringing off the hook and my customers are happier." },
  { name: "Priya Nair", role: "Founder, Cedar Street Yoga", init: "CS", color: "oklch(0.56 0.17 277)", metric: "2× more bookings", quote: "It books discovery calls while I'm teaching. I never lose a lead to a slow reply anymore, the agents just handle it and ask me before anything important." },
  { name: "Owen Reyes", role: "Owner, Sundial Landscaping", init: "SL", color: "oklch(0.62 0.13 152)", metric: "$42k influenced", quote: "I kept my old CRM and dropped Sidecar on top. Same tools, but now there's an agent in the corner telling me exactly who to call next. Wild." },
  { name: "Dana Okafor", role: "Birch & Co. Roasters", init: "BC", color: "oklch(0.62 0.15 18)", metric: "Live in a day", quote: "I was terrified of 'AI' breaking something. The kill switch and approvals meant I could let it run a little more each week. Now I trust it completely." },
  { name: "Marcus Liu", role: "Tidewater Dental", init: "TD", color: "oklch(0.7 0.14 65)", metric: "9 hrs/week saved", quote: "Friesen does the follow-ups I always meant to do and never did. The pipeline basically moves itself while I'm chairside." },
];
const LP_FOUNDERS = [
  { id: "matt", name: "Matt Yee", title: "Tinkerer of things",
    bio: "Rocket scientist turned AI/ML engineer, Matt has spent his career solving problems most people consider impossible. He managed satellite fleet operations for Amazon Kuiper's low-earth orbit constellation and built moonshot technology at Google X, bringing an aerospace-grade approach to designing agentic AI systems, LLM-powered copilots, and defense-grade cloud infrastructure. Today he's at ServiceNow, one of the leading AI autonomous companies, bringing agentic workflows to life for some of the largest enterprises in the world. Along the way, Matt was part of the New York Mets organization as a bullpen catcher and a catcher with the Cosmic Baseball organization, fueling his belief that the best AI doesn't just automate tasks, it gives people back the hours to do the work only they can do. He holds an active TS/SCI clearance, has led engineering teams of 30+, and is a Stanford University alumnus based in Austin, Texas.",
    linkedin: "https://www.linkedin.com/in/mattyee92/", instagram: "https://www.instagram.com/themattyee/" },
  { id: "nick", name: "Nick Friesen", title: "Machine Learning enthusiast",
    bio: "Nick doesn't wait for the future, he builds it. A self-taught machine learning engineer and serial founder, Nick has spent his career turning bold ideas into reality, from scaling photography businesses that redefine first impressions to training identity-preserving AI image models at Fibb AI that make photorealistic human likeness indistinguishable from the real thing. Today, Nick is pushing the boundaries of what AI can do, applying machine learning and agentic systems to some of the most fascinating frontiers imaginable: dog aging, piano composition, cinema, and personal context memory. He's a North Dakota State University alumnus based in Austin, Texas.",
    linkedin: "https://www.linkedin.com/in/nicholasfriesen/", instagram: "https://www.instagram.com/wanderinginatx/" },
];

// Founder photos — optimized (~32KB), imported as bundled, content-hashed assets so they ship in
// REAL builds too via Vite's asset pipeline (publicDir is dropped in real builds, so /public paths
// would 404 — these src imports don't depend on it). Already public in the repo, so no new exposure.
const LP_FOUNDER_PHOTOS = {
  matt: { src: mattPhoto, pos: "50% 12%" },
  nick: { src: nickPhoto, pos: "50% 35%" },
};

// ---- Friesen vs GoHighLevel (interactive comparison) ----
const VS_LENSES = [
  { id: "doit", label: "Do the work for me", rows: ["agents", "approvals", "support", "compound"] },
  { id: "control", label: "Keep me in control", rows: ["approvals", "kill", "audit"] },
  { id: "own", label: "Own my data & stack", rows: ["byocrm", "models", "knowledge"] },
];
const VS_ROWS = [
  { id: "agents", f: "Autonomous AI agents that research, draft, send, and book around the clock", g: "Drip templates and workflows you build and run yourself", fHas: true, gHas: "partial" },
  { id: "approvals", f: "Greenlight: every risky action waits for your one-tap approval", g: "No approval step. Automations just fire on their own", fHas: true, gHas: false },
  { id: "kill", f: "A one tap kill switch, and autonomy you dial from suggest to ask first to fully on its own", g: "Pause individual campaigns by hand", fHas: true, gHas: "partial" },
  { id: "byocrm", f: "Keep HubSpot, Salesforce, or Pipedrive. Agents work inside your own system of record", g: "Built around moving onto their CRM", fHas: true, gHas: false },
  { id: "models", f: "Private models that learn from your own outcomes, a moat nobody else can copy", g: "The same generic AI that every customer gets", fHas: true, gHas: false },
  { id: "knowledge", f: "Hosted knowledge bases ground every agent answer on YOUR docs", g: "Basic bot training on FAQs", fHas: true, gHas: "partial" },
  { id: "support", f: "Frontline: support desk that deflects routine tickets itself", g: "A shared inbox and a chatbot you script", fHas: true, gHas: "partial" },
  { id: "audit", f: "A full audit trail. Every agent action is logged and anomalies pause on their own", g: "Activity history on contacts", fHas: true, gHas: "partial" },
  { id: "compound", f: "Gets sharper every week as your own outcomes train the model", g: "The same product until their next release", fHas: true, gHas: false },
  { id: "funnels", f: "Ad dashboards, traffic analytics, and a content calendar, all on the roadmap", g: "Funnels, sites, and ad tools today", fHas: "partial", gHas: true },
];

function useHeroParallax() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const el = document.querySelector(".lp-hero-3d");
    if (!el) return;
    let raf = 0;
    const onScroll = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        const y = Math.min(window.scrollY, 700) * 0.06;
        el.style.transform = "translate3d(0," + y.toFixed(1) + "px,0)";
      });
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => { window.removeEventListener("scroll", onScroll); if (raf) cancelAnimationFrame(raf); };
  }, []);
}

function useReveal() {
  useEffect(() => {
    const wraps = document.querySelectorAll(".lp-section .lp-wrap, .lp-vs-row, .lp-fullbleed .lp-wrap");
    const cards = document.querySelectorAll(".lp-feat, .lp-layer, .lp-testi");
    if (!("IntersectionObserver" in window)) { [...wraps, ...cards].forEach((el) => el.classList.add("rv-in")); return; }
    const io = new IntersectionObserver((es) => es.forEach((e) => {
      if (!e.isIntersecting) return;
      e.target.classList.add("rv-in"); io.unobserve(e.target);
      // staggered cards drop the reveal classes after settling so their own hover transition returns
      if (e.target.classList.contains("rvc")) setTimeout(() => e.target.classList.remove("rv", "rvc"), 900);
    }), { threshold: 0.12 });
    wraps.forEach((el) => { el.classList.add("rv"); io.observe(el); });
    cards.forEach((el) => {
      const kin = el.parentElement ? [...el.parentElement.children].filter((n) => n.classList && (n.classList.contains("lp-feat") || n.classList.contains("lp-layer") || n.classList.contains("lp-testi"))) : [el];
      const i = Math.max(0, kin.indexOf(el));
      el.classList.add("rv", "rvc"); el.style.setProperty("--d", (i * 70) + "ms"); io.observe(el);
    });
    return () => io.disconnect();
  }, []);
}

function VsMark({ v }) {
  if (v === true) return <span className="vs-mark yes"><LpIcon name="check" size={15} sw={3} /></span>;
  if (v === "partial") return <span className="vs-mark part">~</span>;
  return <span className="vs-mark no">✕</span>;
}

// Capability radar — Friesen vs GoHighLevel across six axes. Draws on scroll.
const VS_AXES = [
  { k: "Autonomy", f: 95, g: 35 },
  { k: "Human control", f: 92, g: 30 },
  { k: "Own your data", f: 90, g: 25 },
  { k: "Gets smarter", f: 88, g: 20 },
  { k: "Keep your stack", f: 94, g: 28 },
  { k: "Funnels & sites", f: 45, g: 90 },
];
function radarPath(vals, R, cx, cy) {
  const n = vals.length;
  return vals.map((v, i) => {
    const a = (Math.PI * 2 * i) / n - Math.PI / 2;
    const r = (v / 100) * R;
    return `${i ? "L" : "M"}${(cx + r * Math.cos(a)).toFixed(1)},${(cy + r * Math.sin(a)).toFixed(1)}`;
  }).join(" ") + "Z";
}
function CapabilityRadar() {
  const ref = useRef(null);
  const [on, setOn] = useState(false);
  useEffect(() => {
    if (!ref.current || !("IntersectionObserver" in window)) { setOn(true); return; }
    const io = new IntersectionObserver((es) => es.forEach((e) => e.isIntersecting && setOn(true)), { threshold: 0.3 });
    io.observe(ref.current); return () => io.disconnect();
  }, []);
  const R = 96, cx = 130, cy = 124, n = VS_AXES.length;
  const fPath = radarPath(VS_AXES.map((a) => a.f), R, cx, cy);
  const gPath = radarPath(VS_AXES.map((a) => a.g), R, cx, cy);
  return (
    <div className="vs-radar tilt3d" data-tilt="7" ref={ref}>
      <svg viewBox="-72 -8 404 272" role="img" aria-label="Friesen vs GoHighLevel capability radar">
        {[0.25, 0.5, 0.75, 1].map((g) => (
          <polygon key={g} className="rad-grid" points={VS_AXES.map((_, i) => { const a = (Math.PI * 2 * i) / n - Math.PI / 2; return `${cx + R * g * Math.cos(a)},${cy + R * g * Math.sin(a)}`; }).join(" ")} />
        ))}
        {VS_AXES.map((ax, i) => { const a = (Math.PI * 2 * i) / n - Math.PI / 2; const lx = cx + (R + 16) * Math.cos(a), ly = cy + (R + 16) * Math.sin(a); return (
          <g key={ax.k}>
            <line className="rad-spoke" x1={cx} y1={cy} x2={cx + R * Math.cos(a)} y2={cy + R * Math.sin(a)} />
            <text className="rad-lab" x={lx} y={ly} textAnchor={Math.abs(Math.cos(a)) < 0.3 ? "middle" : lx > cx ? "start" : "end"} dominantBaseline="middle">{ax.k}</text>
          </g>
        ); })}
        <path className={"rad-them" + (on ? " in" : "")} d={gPath} />
        <path className={"rad-us" + (on ? " in" : "")} d={fPath} />
      </svg>
      <div className="rad-legend">
        <span className="rl us"><i />Friesen Labs</span>
        <span className="rl them"><i />GoHighLevel</span>
      </div>
    </div>
  );
}

function VsSection() {
  const [lens, setLens] = useState(null);
  const hot = lens ? VS_LENSES.find((l) => l.id === lens).rows : null;
  const fWins = VS_ROWS.filter((r) => r.fHas === true && r.gHas !== true).length;
  return (
    <section className="lp-section lp-vs" id="compare">
      <div className="lp-wrap">
        <div className="lp-eyebrow">Why not just use GoHighLevel?</div>
        <h2 className="lp-h2">Marketing automation sends the email.<br />Agents close the loop.</h2>
        <p className="lp-sub">GoHighLevel builds your funnels. Friesen does the work for you. See what changes when agents run your business.</p>
        <CapabilityRadar />
        <div className="vs-lenses">
          {VS_LENSES.map((l) => (
            <button key={l.id} className={"vs-lens" + (lens === l.id ? " active" : "")} onClick={() => setLens(lens === l.id ? null : l.id)}>{l.label}</button>
          ))}
        </div>
        <div className="vs-table" role="table">
          <div className="vs-head" role="row">
            <div className="vs-cell-f" role="columnheader"><div className="vs-brand us"><div className="brand-mark"><Logo size={15} /></div>Friesen Labs</div></div>
            <div className="vs-cell-g" role="columnheader"><div className="vs-brand them">GoHighLevel</div></div>
          </div>
          {VS_ROWS.map((r, i) => (
            <div key={r.id} className={"lp-vs-row vs-row" + (hot ? (hot.includes(r.id) ? " hot" : " dim") : "")} style={{ "--d": i * 45 + "ms" }} role="row">
              <div className="vs-cell-f" role="cell"><VsMark v={r.fHas} /><span>{r.f}</span></div>
              <div className="vs-cell-g" role="cell"><VsMark v={r.gHas} /><span>{r.g}</span></div>
            </div>
          ))}
        </div>
        <div className="vs-score">
          <CountUp value={fWins} /> of {VS_ROWS.length} rounds go to the agents. We&apos;ll say it plainly: if you want funnels today, GoHighLevel is still ahead <i>(ours are on the roadmap above)</i>.
        </div>
      </div>
    </section>
  );
}

// ---- "Nice to have" preview products (roadmap add-ons) ----
const LP_NICE = [
  { id: "ads", tab: "Advertising Hubs", icon: "megaphone", tone: "rose",
    title: "Every ad account, one honest dashboard",
    desc: "Pull Meta, Instagram, Google, YouTube and TikTok spend into one view. See true blended ROAS, what's working, and what to cut, without ten tabs.",
    bullets: ["Blended ROAS & cost-per-result across every platform", "Agents flag underperformers and draft new creative", "Budget pacing so you never overspend by Friday"] },
  { id: "traffic", tab: "Traffic", icon: "gauge", tone: "indigo",
    title: "Know exactly what your site visitors do",
    desc: "A privacy-first analytics layer for your website and landing pages. Drop one snippet in your <head> and get sessions, funnels and full session replays, your own DIY analytics, owned by you.",
    bullets: ["One tracking snippet, paste it once in your head tag", "Session replays, watch real visits end to end", "Funnels & events now, ML on sessions next"] },
  { id: "content", tab: "Content", icon: "play", tone: "green",
    title: "Grow every channel from one calendar",
    desc: "Track Instagram, TikTok, YouTube and LinkedIn in one view, plan and schedule posts, let an agent write the captions, and route a smart link-in-bio straight to your dashboards.",
    bullets: ["Followers & engagement trends across every channel", "Post planner + agent caption writer", "Smart link-in-bio that tracks clicks to your dashboards"] },
];

function NiceBars({ data, accent }) {
  const max = Math.max(...data.map((d) => d.v));
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 7, height: 96 }}>
      {data.map((d, i) => (
        <div key={i} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
          <div style={{ width: "100%", height: (d.v / max * 78) + 6, background: i === data.length - 1 ? accent : "var(--line)", borderRadius: 5, transition: "height .5s cubic-bezier(.2,.7,.2,1)" }} />
          <span style={{ fontSize: 9.5, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>{d.l}</span>
        </div>
      ))}
    </div>
  );
}

function NiceLine({ pts, accent }) {
  const max = Math.max(...pts), min = Math.min(...pts);
  const norm = pts.map((p, i) => [i / (pts.length - 1) * 100, 100 - (max === min ? 50 : (p - min) / (max - min) * 80 + 10)]);
  const d = norm.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  const area = d + ` L100 100 L0 100 Z`;
  return (
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{ width: "100%", height: 90 }}>
      <path d={area} fill={accent} opacity="0.1" />
      <path d={d} fill="none" stroke={accent} strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinecap="round" strokeLinejoin="round" />
      {norm.map((p, i) => i === norm.length - 1 && <circle key={i} cx={p[0]} cy={p[1]} r="2.6" fill={accent} vectorEffect="non-scaling-stroke" />)}
    </svg>
  );
}

function NiceAdsDemo({ accent }) {
  const plats = [["Meta + Instagram", "instagram", 4820, 3.8], ["Google", "search", 3110, 4.4], ["YouTube", "play", 1640, 2.9], ["TikTok", "play", 980, 5.1]];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="ntg-stats">
        {[["Ad spend", "$10.5k", "this month"], ["Blended ROAS", "4.1×", "+0.6 vs last"], ["Conversions", "312", "+18%"], ["Cost / result", "$33.7", "−12%"]].map(([l, v, s]) => (
          <div key={l} className="ntg-stat"><div className="ntg-stat-v">{v}</div><div className="ntg-stat-l">{l}</div><div className="ntg-stat-s">{s}</div></div>
        ))}
      </div>
      <div className="ntg-panel">
        <div className="ntg-panel-h"><b>By platform</b><span>spend · ROAS</span></div>
        {plats.map(([n, ic, sp, ro]) => (
          <div key={n} className="ntg-row">
            <span className="ntg-row-ico" style={{ background: accent + "22", color: accent }}><LpIcon name={ic} size={13} /></span>
            <span style={{ flex: 1, fontWeight: 600 }}>{n}</span>
            <span style={{ width: 110 }}><span className="ntg-bar"><span style={{ width: (sp / 4820 * 100) + "%", background: accent }} /></span></span>
            <span style={{ fontFamily: "var(--mono)", fontSize: 12, width: 52, textAlign: "right" }}>${(sp / 1000).toFixed(1)}k</span>
            <span style={{ fontFamily: "var(--mono)", fontSize: 12, fontWeight: 700, width: 38, textAlign: "right", color: ro >= 4 ? "var(--green)" : "var(--ink-2)" }}>{ro}×</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function NiceTrafficDemo({ accent }) {
  const replays = [["/pricing", "2m 14s", "Austin, TX", "92"], ["/ landing-a", "0m 48s", "Denver, CO", "61"], ["/book-demo", "3m 31s", "Remote", "88"]];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="ntg-stats">
        {[["Sessions", "8,420", "7 days"], ["Visitors", "5,910", "+9%"], ["Avg. time", "1m 52s", "+14s"], ["Bounce", "38%", "−4%"]].map(([l, v, s]) => (
          <div key={l} className="ntg-stat"><div className="ntg-stat-v">{v}</div><div className="ntg-stat-l">{l}</div><div className="ntg-stat-s">{s}</div></div>
        ))}
      </div>
      <div className="ntg-panel">
        <div className="ntg-panel-h"><b>Sessions</b><span>last 7 days</span></div>
        <NiceLine pts={[120, 180, 150, 240, 300, 260, 360]} accent={accent} />
      </div>
      <div className="ntg-code">
        <div className="ntg-code-h"><span style={{ display: "flex", alignItems: "center", gap: 7 }}><LpIcon name="doc" size={13} />Paste in your &lt;head&gt;</span><span className="ntg-copy">Copy</span></div>
        <code>&lt;script src="https://t.friesen.app/p.js" data-site="acme"&gt;&lt;/script&gt;</code>
      </div>
      <div className="ntg-panel">
        <div className="ntg-panel-h"><b>Recent session replays</b><span>watch real visits</span></div>
        {replays.map(([p, t, loc, sc]) => (
          <div key={p} className="ntg-row">
            <span className="ntg-row-ico" style={{ background: accent + "22", color: accent }}><LpIcon name="play" size={12} /></span>
            <span style={{ flex: 1, fontWeight: 600, fontFamily: "var(--mono)", fontSize: 12 }}>{p}</span>
            <span style={{ fontSize: 11.5, color: "var(--ink-4)" }}>{loc}</span>
            <span style={{ fontFamily: "var(--mono)", fontSize: 12, width: 52, textAlign: "right" }}>{t}</span>
            <span className="ntg-chip" style={{ background: accent + "18", color: accent }}>{sc}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function NiceContentDemo({ accent }) {
  const chans = [["Instagram", "instagram", "12.4k", "+320"], ["TikTok", "play", "28.1k", "+1.2k"], ["YouTube", "play", "6.8k", "+90"], ["LinkedIn", "linkedin", "4.2k", "+140"]];
  const plan = [["Mon", "Reel · behind the scenes", "IG · TikTok"], ["Wed", "Customer story", "LinkedIn · YT"], ["Fri", "Tip of the week", "All channels"]];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="ntg-stats">
        {chans.map(([n, ic, foll, gr]) => (
          <div key={n} className="ntg-stat">
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 3 }}><span className="ntg-row-ico" style={{ width: 22, height: 22, background: accent + "22", color: accent }}><LpIcon name={ic} size={12} /></span><span className="ntg-stat-v" style={{ fontSize: 17 }}>{foll}</span></div>
            <div className="ntg-stat-l">{n}</div><div className="ntg-stat-s" style={{ color: "var(--green)" }}>{gr} / wk</div>
          </div>
        ))}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }} className="ntg-2col">
        <div className="ntg-panel">
          <div className="ntg-panel-h"><b>Engagement</b><span>30 days</span></div>
          <NiceBars data={[{ l: "W1", v: 42 }, { l: "W2", v: 55 }, { l: "W3", v: 48 }, { l: "W4", v: 71 }]} accent={accent} />
        </div>
        <div className="ntg-panel">
          <div className="ntg-panel-h"><b>Smart link-in-bio</b><span>tracks to dashboards</span></div>
          <div style={{ display: "flex", flexDirection: "column", gap: 7, marginTop: 4 }}>
            {[["Book a table", "1.2k"], ["Shop the menu", "840"], ["Latest reel", "560"]].map(([l, c]) => (
              <div key={l} style={{ display: "flex", alignItems: "center", gap: 9, padding: "8px 10px", border: "1px solid var(--lp-line)", borderRadius: 10 }}>
                <LpIcon name="link" size={13} style={{ color: accent }} /><span style={{ flex: 1, fontSize: 12.5, fontWeight: 600 }}>{l}</span><span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-4)" }}>{c} clicks</span>
              </div>
            ))}
          </div>
        </div>
      </div>
      <div className="ntg-panel">
        <div className="ntg-panel-h"><b>This week's planner</b><span>agent writes the captions</span></div>
        {plan.map(([d, t, ch]) => (
          <div key={d} className="ntg-row">
            <span style={{ width: 34, fontWeight: 700, fontFamily: "var(--mono)", fontSize: 11, color: accent }}>{d}</span>
            <span style={{ flex: 1, fontWeight: 600 }}>{t}</span>
            <span className="ntg-chip" style={{ background: "var(--lp-surface-2)", color: "var(--ink-3)" }}>{ch}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function NiceToHave() {
  const [tab, setTab] = useState("ads");
  const active = LP_NICE.find((n) => n.id === tab);
  const [bg, fg] = LP_TONE[active.tone];
  const accent = fg;
  return (
    <section className="lp-section" id="nice">
      <div className="lp-wrap">
        <div className="lp-eyebrow">On the roadmap</div>
        <h2 className="lp-h2">More of your business, handled for you.</h2>
        <p className="lp-sub">Optional add ons your agents run as you grow. Same private instance, same crew, more taken off your plate.</p>
        <div className="lp-demo-tabs">
          {LP_NICE.map((n) => <button key={n.id} className={"lp-demo-tab" + (tab === n.id ? " active" : "")} onClick={() => setTab(n.id)}><LpIcon name={n.icon} size={15} style={{ marginRight: 7, verticalAlign: "-2px" }} />{n.tab}</button>)}
        </div>
        <div className="lp-demo-stage">
          <div className="lp-demo-canvas">
            {tab === "ads" && <NiceAdsDemo accent={accent} />}
            {tab === "traffic" && <NiceTrafficDemo accent={accent} />}
            {tab === "content" && <NiceContentDemo accent={accent} />}
          </div>
          <div className="lp-demo-side">
            <span className="cat" style={{ background: bg, color: fg }}>Roadmap</span>
            <h3>{active.title}</h3>
            <p>{active.desc}</p>
            <ul>{active.bullets.map((b) => <li key={b}><LpIcon name="check" size={16} sw={2.4} style={{ color: accent, flexShrink: 0, marginTop: 1 }} />{b}</li>)}</ul>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 7, marginTop: 4, fontSize: 12.5, fontWeight: 600, color: "var(--ink-4)" }}><LpIcon name="clock" size={14} />Coming as the suite grows</span>
          </div>
        </div>
      </div>
    </section>
  );
}

function ProductIco({ tone, icon, big }) {
  const [bg, fg] = LP_TONE[tone];
  return <div className="lp-prod-ico" style={{ background: bg, color: fg, width: big ? 46 : 38, height: big ? 46 : 38, marginBottom: 0, borderRadius: big ? 13 : 10 }}><LpIcon name={icon} size={big ? 22 : 18} /></div>;
}

/* ---------- modals ---------- */
function BookModal({ onClose }) {
  const days = ["Mon 2", "Tue 3", "Wed 4", "Thu 5", "Fri 6"];
  const slots = ["9:00", "10:30", "1:00", "2:30", "4:00"];
  const [day, setDay] = useState("Tue 3"); const [slot, setSlot] = useState("10:30"); const [done, setDone] = useState(false);
  return (
    <div className="lp-modal-scrim" onClick={onClose}>
      <div className="lp-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="lp-modal-head">
          <div className="lp-prod-ico" style={{ background: "var(--accent-soft)", color: "var(--accent-ink)", marginBottom: 0 }}><LpIcon name="calendar" size={20} /></div>
          <div style={{ flex: 1 }}><h3 style={{ fontSize: 19, fontWeight: 730, letterSpacing: "-.02em" }}>{done ? "You're booked!" : "Book a call"}</h3><p style={{ fontSize: 13, color: "var(--ink-3)", marginTop: 2 }}>{done ? "Check your inbox for the invite." : "15 minutes with a product specialist."}</p></div>
          <button className="icon-btn" aria-label="Close" onClick={onClose}><LpIcon name="x" size={18} /></button>
        </div>
        <div className="lp-modal-body">
          {done ? (
            <div style={{ textAlign: "center", padding: "16px 0" }}>
              <div className="lp-prov-check" style={{ width: 60, height: 60, borderRadius: 18 }}><LpIcon name="check" size={30} sw={2.6} style={{ color: "#fff" }} /></div>
              <p style={{ fontSize: 14, color: "var(--ink-2)", marginTop: 14 }}><b>{day} at {slot}</b>, we'll see you then.</p>
            </div>
          ) : (
            <>
              <label style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-3)" }}>Pick a day</label>
              <div className="lp-slot" style={{ margin: "9px 0 16px" }}>{days.map((d) => <button key={d} className={day === d ? "sel" : ""} onClick={() => setDay(d)}>{d}</button>)}</div>
              <label style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-3)" }}>Pick a time</label>
              <div className="lp-slot" style={{ margin: "9px 0 20px" }}>{slots.map((s) => <button key={s} className={slot === s ? "sel" : ""} onClick={() => setSlot(s)}>{s}</button>)}</div>
              <button className="btn btn-primary btn-lg" style={{ width: "100%" }} onClick={() => setDone(true)}><LpIcon name="check" size={16} sw={2.2} />Confirm {day} at {slot}</button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
function EmailModal({ onClose }) {
  const [done, setDone] = useState(false);
  return (
    <div className="lp-modal-scrim" onClick={onClose}>
      <div className="lp-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="lp-modal-head">
          <div className="lp-prod-ico" style={{ background: "var(--rose-soft)", color: "oklch(0.48 0.14 18)", marginBottom: 0 }}><LpIcon name="mail" size={20} /></div>
          <div style={{ flex: 1 }}><h3 style={{ fontSize: 19, fontWeight: 730, letterSpacing: "-.02em" }}>{done ? "Message sent" : "Email us"}</h3><p style={{ fontSize: 13, color: "var(--ink-3)", marginTop: 2 }}>{done ? "We'll reply within a few hours." : "Tell us about your business."}</p></div>
          <button className="icon-btn" aria-label="Close" onClick={onClose}><LpIcon name="x" size={18} /></button>
        </div>
        <div className="lp-modal-body">
          {done ? (
            <div style={{ textAlign: "center", padding: "16px 0" }}><div className="lp-prov-check" style={{ width: 60, height: 60, borderRadius: 18 }}><LpIcon name="check" size={30} sw={2.6} style={{ color: "#fff" }} /></div><p style={{ fontSize: 14, color: "var(--ink-2)", marginTop: 14 }}>Thanks, a human will get back to you soon.</p></div>
          ) : (
            <>
              <input className="lp-input" placeholder="Your name" />
              <input className="lp-input" placeholder="Work email" />
              <textarea className="lp-input" placeholder="What are you hoping to automate?" />
              <button className="btn btn-primary btn-lg" style={{ width: "100%", marginTop: 14 }} onClick={() => setDone(true)}><LpIcon name="send" size={16} />Send message</button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
function DonateModal({ onClose }) {
  const [amt, setAmt] = useState(25); const [done, setDone] = useState(false);
  return (
    <div className="lp-modal-scrim" onClick={onClose}>
      <div className="lp-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="lp-modal-head">
          <div className="lp-prod-ico" style={{ background: "var(--accent-soft)", color: "var(--accent-ink)", marginBottom: 0 }}><LpIcon name="spark" size={20} /></div>
          <div style={{ flex: 1 }}><h3 style={{ fontSize: 19, fontWeight: 730, letterSpacing: "-.02em" }}>{done ? "Thank you 💛" : "Support the mission"}</h3><p style={{ fontSize: 13, color: "var(--ink-3)", marginTop: 2 }}>{done ? "Your gift helps a small business get started." : "Help put agentic tools in more small businesses."}</p></div>
          <button className="icon-btn" aria-label="Close" onClick={onClose}><LpIcon name="x" size={18} /></button>
        </div>
        <div className="lp-modal-body">
          {done ? (
            <div style={{ textAlign: "center", padding: "16px 0" }}><div className="lp-prov-check" style={{ width: 60, height: 60, borderRadius: 18 }}><LpIcon name="check" size={30} sw={2.6} style={{ color: "#fff" }} /></div><p style={{ fontSize: 14, color: "var(--ink-2)", marginTop: 14 }}>A <b>${amt}</b> gift, thank you for backing the mission.</p></div>
          ) : (
            <>
              <label style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-3)" }}>Choose an amount</label>
              <div className="lp-slot" style={{ margin: "9px 0 18px" }}>{[10, 25, 50, 100, 250].map((a) => <button key={a} className={amt === a ? "sel" : ""} onClick={() => setAmt(a)}>${a}</button>)}</div>
              <button className="btn btn-primary btn-lg" style={{ width: "100%" }} onClick={() => setDone(true)}><LpIcon name="spark" size={16} />Donate ${amt}</button>
              <p style={{ fontSize: 11.5, color: "var(--ink-4)", textAlign: "center", marginTop: 12, lineHeight: 1.5 }}>Friesen Labs is a 501(c)(3) tax-exempt nonprofit (EIN 00-0000000). Your gift is tax-deductible to the extent allowed by law; no goods or services are provided in exchange. A receipt is emailed for your records.</p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function ProvisionModal({ selected, byo, onClose }) {
  const steps = ["Creating your workspace", "Securing your private instance", ...selected.filter((m) => !m.req).map((m) => `Activating ${m.name}`), byo ? "Connecting your CRM" : null, "Hiring your agent team", "Loading starter credits", "Workspace ready"].filter(Boolean);
  const [done, setDone] = useState(0);
  const finished = done >= steps.length;
  useEffect(() => {
    if (finished) return;
    const t = setTimeout(() => setDone((d) => d + 1), done === 0 ? 500 : 720);
    return () => clearTimeout(t);
  }, [done, finished]);
  return (
    <div className="lp-modal-scrim">
      <div className="lp-prov">
        {finished ? <div className="lp-prov-check"><LpIcon name="check" size={38} sw={2.6} style={{ color: "#fff" }} /></div> : <div className="lp-prov-ring" />}
        <div className="lp-eyebrow" style={{ textAlign: "center" }}>{finished ? "All set" : "Provisioning"}</div>
        <h2 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.03em", marginTop: 8 }}>{finished ? "Your instance is ready" : "Spinning up your instance"}</h2>
        <div className="lp-prov-steps">
          {steps.map((s, i) => (
            <div key={s} className={"lp-prov-step" + (i < done ? " done" : "")}>
              <div className="ps-box">{i < done && <LpIcon name="check" size={13} sw={3} />}</div>{s}
            </div>
          ))}
        </div>
        {finished && <a className="btn btn-primary btn-lg" href="index.html?onboard=1" style={{ marginTop: 24, width: "100%" }}><LpIcon name="bolt" size={16} />Enter Friesen Labs</a>}
      </div>
    </div>
  );
}

function ProductPage({ id, onClose, onAdd, onBook }) {
  const p = LP_PRODUCTS.find((x) => x.id === id);
  const demo = LP_DEMOS.find((d) => d.id === id);
  const [bg, fg] = LP_TONE[p.tone];
  const included = !LP_MODULES.some((m) => m.id === id);
  useEffect(() => { const k = (e) => { if (e.key === "Escape") onClose(); }; window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k); }, [onClose]);
  const Visual = () => {
    if (demo) return demo.Demo();
    if (id === "integration") return (
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 11, width: "100%" }}>
        {[["HubSpot", "#ff7a59", "H"], ["Salesforce", "#00a1e0", "S"], ["Stripe", "#635bff", "S"], ["Gmail", "#ea4335", "G"], ["QuickBooks", "#2ca01c", "Q"], ["Slack", "#4a154b", "S"]].map(([n, c, l]) => (
          <div key={n} style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-md)", padding: 14, display: "flex", alignItems: "center", gap: 10, boxShadow: "var(--shadow-sm)" }}>
            <div style={{ width: 34, height: 34, borderRadius: 9, background: c, color: "#fff", display: "grid", placeItems: "center", fontWeight: 800, fontFamily: "var(--mono)" }}>{l}</div>
            <b style={{ fontSize: 13 }}>{n}</b>
          </div>
        ))}
      </div>
    );
    return <FoxDemo />;
  };
  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 85, background: "var(--bg)", overflowY: "auto", animation: "screen-in .35s both" }}>
      <nav className="lp-nav"><div className="lp-nav-in">
        <button className="btn btn-ghost btn-sm" onClick={onClose}><LpIcon name="chevL" size={15} sw={2.2} />All products</button>
        <div className="lp-brand" style={{ marginLeft: 4 }}><div className="brand-mark" style={{ width: 28, height: 28 }}><Logo size={17} /></div><b>Friesen Labs</b></div>
        <div className="lp-nav-cta">{included
          ? <span className="chip green" style={{ height: 34, padding: "0 14px" }}><LpIcon name="check" size={13} sw={2.4} />Included free</span>
          : <button className="btn btn-primary" onClick={() => onAdd(id)}><LpIcon name="plus" size={15} sw={2.2} />Add to suite</button>}</div>
      </div></nav>

      <section className="lp-section" style={{ paddingBottom: 32 }}>
        <div className="lp-wrap lp-hero-grid" style={{ alignItems: "center" }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
              <div className="lp-prod-ico" style={{ background: bg, color: fg, width: 52, height: 52, marginBottom: 0, borderRadius: 14 }}><LpIcon name={p.icon} size={26} /></div>
              <span className="lp-eyebrow" style={{ textAlign: "left" }}>{p.cat}</span>
            </div>
            <h1 className="lp-h1" style={{ fontSize: 44 }}>{p.name}</h1>
            <p className="lp-lead">{p.long}</p>
            <div className="lp-hero-cta">
              {included
                ? <span className="btn btn-soft btn-lg" style={{ cursor: "default" }}><LpIcon name="check" size={16} sw={2.2} />Included free in every plan</span>
                : <button className="btn btn-primary btn-lg" onClick={() => onAdd(id)}><LpIcon name="plus" size={16} sw={2.2} />Add to my suite</button>}
              <button className="btn btn-ghost btn-lg" onClick={onBook}><LpIcon name="calendar" size={16} />Book a call</button>
            </div>
          </div>
          <div className="lp-demo-stage" style={{ gridTemplateColumns: "1fr", minHeight: 0, boxShadow: "var(--shadow-xl)" }}>
            <div className="lp-demo-canvas" style={{ borderRight: "none" }}><Visual /></div>
          </div>
        </div>
      </section>

      <section className="lp-section alt" style={{ paddingTop: 48 }}>
        <div className="lp-wrap">
          <div className="lp-eyebrow">Why owners love it</div>
          <h2 className="lp-h2" style={{ fontSize: 32 }}>Everything {p.name} does for you</h2>
          <div className="lp-feat-grid">
            {p.features.map(([t, d], i) => (
              <div className="lp-feat" key={t} style={{ "--fc": FEAT_VIVID[p.tone] || "var(--accent)" }}>
                <span className="lp-feat-num">{String(i + 1).padStart(2, "0")}</span>
                <h3>{t}</h3>
                <p>{d}</p>
              </div>
            ))}
          </div>
          <div style={{ textAlign: "center", marginTop: 44 }}>
            {included
              ? <button className="btn btn-primary btn-lg" onClick={onClose}><LpIcon name="bolt" size={16} />Explore the other products</button>
              : <button className="btn btn-primary btn-lg" onClick={() => onAdd(id)}><LpIcon name="bolt" size={16} />Add {p.name} &amp; build my suite</button>}
          </div>
        </div>
      </section>
    </div>
  );
}

// onSignIn: wired by main.tsx to the Cognito Hosted UI signIn() when the
// sign-in gate is active. Defaults to a no-op so the screen is render-safe
// standalone.
// Magnetic pull for primary CTAs — the button leans toward the cursor.
function useMagnetic() {
  useEffect(() => {
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    if (window.matchMedia && !window.matchMedia("(hover: hover)").matches) return;
    const btns = Array.from(document.querySelectorAll(".lp .btn-primary"));
    const move = (e) => { const b = e.currentTarget, r = b.getBoundingClientRect(); b.style.transform = `translate(${(e.clientX - r.left - r.width / 2) * 0.18}px, ${(e.clientY - r.top - r.height / 2) * 0.28}px)`; };
    const leave = (e) => { e.currentTarget.style.transform = ""; };
    btns.forEach((b) => { b.addEventListener("mousemove", move); b.addEventListener("mouseleave", leave); });
    return () => btns.forEach((b) => { b.removeEventListener("mousemove", move); b.removeEventListener("mouseleave", leave); });
  }, []);
}

// Cursor-driven 3D tilt for any `.tilt3d` element — the cinematic centerpiece. Desktop + non-
// reduced-motion only; on touch/phones it's a no-op so the mobile layout stays flat and fast.
function useTilt3d() {
  useEffect(() => {
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    if (window.matchMedia && !window.matchMedia("(hover: hover)").matches) return;
    const els = Array.from(document.querySelectorAll(".lp .tilt3d"));
    const move = (e) => {
      const el = e.currentTarget, r = el.getBoundingClientRect();
      const px = (e.clientX - r.left) / r.width - 0.5, py = (e.clientY - r.top) / r.height - 0.5;
      const max = +(el.dataset.tilt || 9);
      el.style.setProperty("--ry", (px * max).toFixed(2) + "deg");
      el.style.setProperty("--rx", (-py * max).toFixed(2) + "deg");
      el.style.setProperty("--gx", (px * 100 + 50).toFixed(1) + "%");
      el.style.setProperty("--gy", (py * 100 + 50).toFixed(1) + "%");
      el.classList.add("tilting");
    };
    const leave = (e) => { const el = e.currentTarget; el.classList.remove("tilting"); el.style.setProperty("--rx", "0deg"); el.style.setProperty("--ry", "0deg"); };
    els.forEach((el) => { el.addEventListener("mousemove", move); el.addEventListener("mouseleave", leave); });
    return () => els.forEach((el) => { el.removeEventListener("mousemove", move); el.removeEventListener("mouseleave", leave); });
  }, []);
}

// Fixed film-grain overlay — cinematic texture across the whole page.
function Grain() { return <div className="lp-grain" aria-hidden="true" />; }

// Live WebGL shader backdrop — the cinematic atmosphere the whole page sits in. A custom GLSL
// fragment field (domain-warped fbm) flows behind every (translucent) section and reacts to scroll
// + cursor. Raw WebGL (~no deps), rendered at 0.55x and CSS-upscaled (the field is soft, so it's
// cheap), DPR-light, paused when the tab is hidden. Reduced-motion or no-WebGL → renders nothing
// and the CSS gradient shows through (graceful). This is the real-3D layer; three.js geometry
// would be heavier for an atmosphere and is reserved for a possible hero showpiece.
function WebGLBackdrop() {
  const ref = useRef(null);
  useEffect(() => {
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const canvas = ref.current;
    const gl = canvas && canvas.getContext("webgl", { antialias: false, alpha: false, powerPreference: "low-power" });
    if (!gl) return;
    const VS = "attribute vec2 p; void main(){ gl_Position = vec4(p, 0.0, 1.0); }";
    const FS = [
      "precision highp float;",
      "uniform vec2 u_res; uniform float u_time; uniform float u_scroll; uniform vec2 u_mouse;",
      "float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1,311.7)))*43758.5453); }",
      "float noise(vec2 p){ vec2 i=floor(p), f=fract(p); f=f*f*(3.0-2.0*f);",
      "  float a=hash(i), b=hash(i+vec2(1.0,0.0)), c=hash(i+vec2(0.0,1.0)), d=hash(i+vec2(1.0,1.0));",
      "  return mix(mix(a,b,f.x), mix(c,d,f.x), f.y); }",
      "float fbm(vec2 p){ float v=0.0, a=0.5; for(int i=0;i<5;i++){ v+=a*noise(p); p=p*2.0+vec2(1.7,9.2); a*=0.5; } return v; }",
      "void main(){",
      "  vec2 uv = gl_FragCoord.xy/u_res.xy;",
      "  vec2 p = uv; p.x *= u_res.x/u_res.y;",
      "  float ang = -0.5; p = mat2(cos(ang),-sin(ang),sin(ang),cos(ang))*p; p *= 1.5;", // diagonal tilt
      "  float t = u_time*0.04;",
      "  vec2 q = vec2(fbm(p+vec2(0.0,t)), fbm(p+vec2(5.2,1.3)-t));",
      "  vec2 r = vec2(fbm(p+2.0*q+vec2(1.7+u_mouse.x*0.4,9.2)+t*0.5), fbm(p+2.0*q+vec2(8.3,2.8)+u_scroll*0.4));",
      "  float f = fbm(p+1.9*r);",
      "  vec3 base   = vec3(0.99,0.985,1.0);",   // near-white
      "  vec3 cBlue  = vec3(0.36,0.45,0.98);",   // indigo-blue
      "  vec3 cViolet= vec3(0.62,0.36,0.96);",   // violet
      "  vec3 cPink  = vec3(0.97,0.43,0.72);",   // pink
      "  vec3 cPeach = vec3(0.99,0.66,0.45);",   // peach
      "  vec3 cCyan  = vec3(0.40,0.82,0.95);",   // cyan
      "  vec3 col = base;",
      "  col = mix(col, cBlue,   clamp(f*f*1.6,0.0,1.0)*0.55);",
      "  col = mix(col, cViolet, clamp(q.y,0.0,1.0)*0.42);",
      "  col = mix(col, cPink,   clamp(r.x*r.x,0.0,1.0)*0.40);",
      "  col = mix(col, cPeach,  clamp(r.y,0.0,1.0)*0.30);",
      "  col = mix(col, cCyan,   clamp(q.x*0.8,0.0,1.0)*0.26);",
      "  float vig = smoothstep(1.3,0.3,length(uv-0.5));",
      "  col *= 0.94+0.1*vig;",
      "  gl_FragColor = vec4(col, 1.0);",
      "}",
    ].join("\n");
    const compile = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); return s; };
    const prog = gl.createProgram();
    gl.attachShader(prog, compile(gl.VERTEX_SHADER, VS));
    gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return;
    gl.useProgram(prog);
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, "p");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    const uRes = gl.getUniformLocation(prog, "u_res"), uTime = gl.getUniformLocation(prog, "u_time"),
      uScroll = gl.getUniformLocation(prog, "u_scroll"), uMouse = gl.getUniformLocation(prog, "u_mouse");
    const SCALE = 0.55;
    let raf, running = true, elapsed = 0, t0 = performance.now();
    let mouse = [0.5, 0.5], scroll = 0;
    const resize = () => { const w = Math.max(2, Math.floor(window.innerWidth * SCALE)), h = Math.max(2, Math.floor(window.innerHeight * SCALE)); canvas.width = w; canvas.height = h; gl.viewport(0, 0, w, h); };
    const onMove = (e) => { mouse = [e.clientX / window.innerWidth, 1 - e.clientY / window.innerHeight]; };
    const onScroll = () => { const m = document.documentElement.scrollHeight - window.innerHeight; scroll = m > 0 ? window.scrollY / m : 0; };
    const loop = () => { if (!running) return; elapsed = performance.now() - t0; gl.uniform2f(uRes, canvas.width, canvas.height); gl.uniform1f(uTime, elapsed / 1000); gl.uniform1f(uScroll, scroll); gl.uniform2f(uMouse, mouse[0], mouse[1]); gl.drawArrays(gl.TRIANGLES, 0, 3); raf = requestAnimationFrame(loop); };
    const onVis = () => { if (document.hidden) { running = false; cancelAnimationFrame(raf); } else if (!running) { running = true; t0 = performance.now() - elapsed; loop(); } };
    resize();
    window.addEventListener("resize", resize);
    window.addEventListener("mousemove", onMove, { passive: true });
    window.addEventListener("scroll", onScroll, { passive: true });
    document.addEventListener("visibilitychange", onVis);
    loop();
    return () => { running = false; cancelAnimationFrame(raf); window.removeEventListener("resize", resize); window.removeEventListener("mousemove", onMove); window.removeEventListener("scroll", onScroll); document.removeEventListener("visibilitychange", onVis); };
  }, []);
  return <canvas ref={ref} className="lp-webgl" aria-hidden="true" />;
}

// Section anchors shared by the desktop nav + the mobile menu.
const NAV_LINKS = [
  ["products", "Products"], ["demos", "See it work"], ["compare", "vs GHL"],
  ["roi", "ROI"], ["testimonials", "Customers"], ["pricing", "Pricing"],
  ["team", "Team"], ["research", "Research"], ["about", "About"],
];

// Conversion destinations (#120). The signup funnel (signup/SignupFlow.tsx) is
// mounted by main.tsx at the pre-auth ?view=signup seam — every "Get started" /
// "Build your suite" CTA routes there as a REAL link (href), so middle-click,
// keyboard and crawlers all work.
const SIGNUP_HREF = "/?view=signup";
// Href fallback for the Sign in anchors: a gated surface, which renders the
// focused sign-in gate when unauthenticated (main.tsx). The CLICK is always
// intercepted to run the SPA's PKCE signIn() — never a bare Hosted-UI URL,
// which would fail the state (CSRF) check on the callback.
const SIGNIN_HREF = "/?view=dashboard";

// Back-to-top button — appears once you've scrolled past the first screen.
function BackToTop() {
  const [show, setShow] = useState(false);
  useEffect(() => {
    const on = () => setShow(window.scrollY > 900);
    on(); window.addEventListener("scroll", on, { passive: true });
    return () => window.removeEventListener("scroll", on);
  }, []);
  return (
    <button className={"lp-totop" + (show ? " in" : "")} aria-label="Back to top" onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}>
      <LpIcon name="chevDown" size={18} sw={2.4} style={{ transform: "rotate(180deg)" }} />
    </button>
  );
}

// Sticky scroll-progress bar across the top of the marketing page.
function ScrollProgress() {
  const [p, setP] = useState(0);
  useEffect(() => {
    const on = () => { const h = document.documentElement; const max = h.scrollHeight - h.clientHeight; setP(max > 0 ? (h.scrollTop / max) * 100 : 0); };
    on(); window.addEventListener("scroll", on, { passive: true }); window.addEventListener("resize", on);
    return () => { window.removeEventListener("scroll", on); window.removeEventListener("resize", on); };
  }, []);
  return <div className="lp-progress" style={{ transform: `scaleX(${p / 100})` }} aria-hidden="true" />;
}

// Bold closing CTA band — the last beat before the footer.
function FinalCta({ onBook }) {
  return (
    <section className="lp-finalcta">
      <div className="lp-wrap">
        <div className="lp-eyebrow" style={{ color: "color-mix(in oklch, var(--accent) 55%, #fff)" }}>Your agents are waiting</div>
        <h2 className="fc-h">Stop doing the busywork.<br />Put a crew on it tonight.</h2>
        <p className="fc-sub">Build your suite in minutes, keep the CRM you love, and approve only what matters. Live by this afternoon.</p>
        <div className="fc-cta">
          <a className="btn btn-primary btn-lg" href={SIGNUP_HREF}><LpIcon name="bolt" size={17} />Build your suite</a>
          <button className="btn btn-glass btn-lg" onClick={onBook}><LpIcon name="calendar" size={16} />Book a 15-min call</button>
        </div>
        <div className="fc-trust">{["Live in a day", "Keep your CRM", "One-tap kill switch", "Cancel anytime"].map((t) => <span key={t}><LpIcon name="check" size={14} sw={2.6} />{t}</span>)}</div>
      </div>
    </section>
  );
}

// Interactive agent roster — click a crew member, the hero line rewrites to what they do.
const HERO_ROSTER = [
  { id: "margo", emoji: "💬", name: "Margo", role: "Sales", line: "quotes every inbound lead before your coffee's cold, then chases the follow-up." },
  { id: "pip", emoji: "🐧", name: "Pip", role: "Support", line: "answers the routine tickets the moment they land, and routes the tricky ones to you." },
  { id: "nadia", emoji: "📅", name: "Nadia", role: "Scheduling", line: "books discovery calls from a link customers self-serve, with reminders handled." },
  { id: "ledger", emoji: "🧾", name: "Ledger", role: "Billing", line: "sends quotes, invoices, and politely nudges the overdue ones until they're paid." },
  { id: "echo", emoji: "⭐", name: "Echo", role: "Reputation", line: "asks happy customers for reviews at exactly the right moment and tracks referrals." },
  { id: "scout", emoji: "🔎", name: "Scout", role: "Research", line: "enriches every new contact with the context your team needs before the first call." },
];
function HeroRoster() {
  const [active, setActive] = useState("margo");
  const a = HERO_ROSTER.find((r) => r.id === active);
  return (
    <div className="hero-roster">
      <div className="hero-roster-chips">
        {HERO_ROSTER.map((r) => (
          <button key={r.id} className={"hr-chip" + (active === r.id ? " on" : "")} onClick={() => setActive(r.id)} aria-pressed={active === r.id}>
            <span className="hr-mono">{r.name[0]}</span>{r.name}
          </button>
        ))}
      </div>
      <div className="hero-roster-line" key={active}>
        <b>{a.name}</b> <span className="hr-role">{a.role}</span> {a.line}
      </div>
    </div>
  );
}

// Live ROI calculator — sliders drive an animated monthly-savings readout + a bar race.
function RoiCalculator() {
  const [team, setTeam] = useState(4);
  const [rate, setRate] = useState(28);
  const [hrs, setHrs] = useState(12);
  const weekly = team * hrs;            // busywork hours / week across the team
  const manualMo = Math.round(weekly * 4.33 * rate);
  const friesenMo = Math.round(team * 49 + 199); // suite + agent credits, illustrative
  const saved = Math.max(0, manualMo - friesenMo);
  const pct = manualMo ? Math.round((saved / manualMo) * 100) : 0;
  const barF = manualMo ? Math.max(6, Math.round((friesenMo / manualMo) * 100)) : 6;
  return (
    <section className="lp-section lp-roi" id="roi">
      <div className="lp-wrap">
        <div className="lp-eyebrow">Run the numbers</div>
        <h2 className="lp-h2">What would a crew of agents save you?</h2>
        <p className="lp-sub">Move the sliders. See the busywork hours your agents take off your plate, and what those hours cost you today.</p>
        <div className="roi-grid">
          <div className="roi-controls">
            {[
              ["Team members doing busywork", team, setTeam, 1, 25, "", (v) => v],
              ["Avg. loaded hourly cost", rate, setRate, 12, 120, "$", (v) => v],
              ["Busywork hours / person / week", hrs, setHrs, 2, 30, "", (v) => v],
            ].map(([label, val, set, min, max, pre]) => (
              <label className="roi-ctl" key={label}>
                <span className="roi-ctl-top"><b>{label}</b><span className="roi-ctl-val">{pre}{val}</span></span>
                <input type="range" min={min} max={max} value={val} aria-label={label} aria-valuetext={`${pre}${val}`} onChange={(e) => set(+e.target.value)} style={{ "--p": ((val - min) / (max - min) * 100) + "%" }} />
              </label>
            ))}
          </div>
          <div className="roi-readout">
            <div className="roi-save"><span className="roi-save-pre">You'd reclaim</span><div className="roi-save-num">$<CountUp value={saved} format={(n)=>Math.round(n).toLocaleString()} /><span>/mo</span></div><span className="roi-save-pct">{pct}% lower than paying for the hours</span></div>
            <div className="roi-bars">
              <div className="roi-bar"><span className="roi-bar-lab">Doing it by hand</span><div className="roi-bar-track"><div className="roi-bar-fill manual" style={{ width: "100%" }}><b>${manualMo.toLocaleString()}</b></div></div></div>
              <div className="roi-bar"><span className="roi-bar-lab">With Friesen agents</span><div className="roi-bar-track"><div className="roi-bar-fill friesen" style={{ width: barF + "%" }}><b>${friesenMo.toLocaleString()}</b></div></div></div>
            </div>
            <div className="roi-foot">{(weekly * 4.33).toFixed(0)} hours of busywork a month, handed to agents that don't clock out. <i>Illustrative; your suite price depends on the modules you pick.</i></div>
          </div>
        </div>
      </div>
    </section>
  );
}

function Landing({ onSignIn = () => {} } = {}) {
  // The global app shell sets `body { overflow: hidden }` (it scrolls inside its
  // own panes). The marketing landing is a full-page document, so it must opt the
  // body back into scrolling via `body.lp-body` while mounted — without this the
  // whole page is scroll-locked in real (production) builds.
  useEffect(() => {
    document.body.classList.add("lp-body");
    return () => document.body.classList.remove("lp-body");
  }, []);
  useReveal();
  useHeroParallax();
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") { setModal(null); setPaper(null); setDoc(null); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useMagnetic();
  useTilt3d();
  const [demoTab, setDemoTab] = useState("agents");
  const [plan, setPlan] = useState("keepcrm");
  const [sel, setSel] = useState({ command: true, agents: true, workflows: true, greenlight: true, integration: true });
  const [byo, setByo] = useState(true);
  const [modal, setModal] = useState(null); // 'book' | 'email' | 'provision'
  const [openProduct, setOpenProduct] = useState(null);
  const [doc, setDoc] = useState(null);
  const [paper, setPaper] = useState(null);
  const [navOpen, setNavOpen] = useState(false);

  // Scroll to a section, reflect it in the URL hash, and close the mobile menu.
  const go = (id) => { setNavOpen(false); const el = document.getElementById(id); if (el) { el.scrollIntoView({ behavior: "smooth" }); window.history.replaceState(null, "", "#" + id); } };
  // Section-anchor props (#120): a REAL href (so no anchor is ever dead — the
  // native #id jump is the no-JS fallback) with the click intercepted for
  // smooth scrolling.
  const sectionLink = (id) => ({ href: "#" + id, onClick: (e) => { e.preventDefault(); go(id); } });
  // Anchors that act as buttons (sign-in, in-page modals): carry an href so
  // they're focusable + probe-visible, but the action runs in the SPA.
  const signInClick = (e) => { e.preventDefault(); setNavOpen(false); onSignIn(); };
  const actionLink = (fn) => ({ href: "#", role: "button", onClick: (e) => { e.preventDefault(); fn(); } });
  // Lock body scroll while the mobile menu is open.
  useEffect(() => {
    if (!navOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, [navOpen]);

  const addProduct = (pid) => { setSel((s) => ({ ...s, [pid]: true })); setPlan("custom"); if (pid === "uplift") setByo(false); setOpenProduct(null); setTimeout(() => document.getElementById("pricing") && document.getElementById("pricing").scrollIntoView({ behavior: "smooth" }), 80); };

  const applyPlan = (id) => { const p = LP_PLANS[id]; setPlan(id); const s = {}; p.mods.forEach((m) => (s[m] = true)); setSel(s); setByo(p.byo); };
  const toggleMod = (m) => {
    if (m.req) return;
    setPlan("custom");
    setSel((s) => { const n = { ...s, [m.id]: !s[m.id] }; if (m.id === "uplift" && n.uplift) setByo(false); return n; });
  };
  const setByoCrm = (v) => {
    setPlan("custom"); setByo(v);
    setSel((s) => ({ ...s, uplift: v ? false : s.uplift, integration: v ? true : s.integration }));
  };

  const selectedMods = LP_MODULES.filter((m) => sel[m.id]);
  const raw = selectedMods.reduce((t, m) => t + m.price, 0);
  const discount = selectedMods.length >= 4 ? 0.1 : 0;
  const total = Math.round(raw * (1 - discount));
  const credits = 1000 + selectedMods.length * 500;

  const activeDemo = LP_DEMOS.find((d) => d.id === demoTab);

  return (
    <div className="lp lp-cinematic">
      <a className="lp-skip" href="#main">Skip to content</a>
      <ScrollProgress />
      {/* nav */}
      <nav className="lp-nav">
        <div className="lp-nav-in">
          <div className="lp-brand">
            <div className="brand-mark"><Logo size={19} /></div>
            <b>Friesen Labs</b>
          </div>
          <div className="lp-nav-links">
            {NAV_LINKS.map(([id, label]) => <a key={id} {...sectionLink(id)}>{label}</a>)}
          </div>
          <div className="lp-nav-cta">
            <a className="lp-signin" href={SIGNIN_HREF} onClick={signInClick}>Sign in</a>
            <a className="btn btn-primary" href={SIGNUP_HREF}>Get started</a>
          </div>
          <button className="lp-burger" aria-label="Open menu" aria-expanded={navOpen} onClick={() => setNavOpen((v) => !v)}>
            <span /><span /><span />
          </button>
        </div>
      </nav>

      {/* mobile menu — portaled to <body> so it escapes the .lp stacking context entirely
          (a backdrop rule was trapping it behind the hero). Guaranteed on top. */}
      {createPortal(
      <div className={"lp-mnav" + (navOpen ? " open" : "")} onClick={() => setNavOpen(false)}>
        <div className="lp-mnav-panel" onClick={(e) => e.stopPropagation()}>
          <div className="lp-mnav-head">
            <div className="lp-brand"><div className="brand-mark"><Logo size={18} /></div><b>Friesen Labs</b></div>
            <button className="lp-mnav-x" aria-label="Close menu" onClick={() => setNavOpen(false)}><LpIcon name="x" size={20} /></button>
          </div>
          <div className="lp-mnav-links">
            {NAV_LINKS.map(([id, label]) => <a key={id} {...sectionLink(id)}>{label}<LpIcon name="arrowRight" size={15} sw={2} style={{ opacity: .4 }} /></a>)}
          </div>
          <div className="lp-mnav-cta">
            <a className="btn btn-primary btn-lg" href={SIGNUP_HREF}><LpIcon name="bolt" size={16} />Build your suite</a>
            <button className="btn btn-ghost btn-lg" onClick={() => { setNavOpen(false); setModal("book"); }}><LpIcon name="calendar" size={15} />Book a call</button>
            <a className="lp-mnav-signin" href={SIGNIN_HREF} onClick={signInClick}>Sign in</a>
          </div>
        </div>
      </div>,
      document.body)}

      {/* hero */}
      <section className="lp-hero" id="main" role="main">
        <div className="lp-wrap lp-hero-grid">
          <div>
            <span className="lp-pill"><span className="live-dot" style={{ width: 6, height: 6 }} />Meet your AI back office</span>
            <h1 className="lp-h1">Your business, run by <span className="accentword">agents</span>. Watched by you.</h1>
            <p className="lp-lead">Get a crew of AI agents that does the busywork for you. They research leads, send outreach, write quotes, chase follow ups, and book the meeting around the clock. You stay in control and approve anything important.</p>
            <div className="lp-hero-cta">
              <a className="btn btn-primary btn-lg" href={SIGNUP_HREF}><LpIcon name="bolt" size={17} />Build your suite</a>
              <button className="btn btn-ghost btn-lg" onClick={() => go("demos")}><LpIcon name="play" size={16} />See it in action</button>
            </div>
            <div className="lp-trust">
              <span><LpIcon name="check" size={14} sw={2.6} />No credit card to explore</span>
              <span><LpIcon name="check" size={14} sw={2.6} />Live in a day</span>
              <span><LpIcon name="check" size={14} sw={2.6} />Keep your CRM</span>
            </div>
            <div className="lp-hero-note"><LpIcon name="link" size={15} /><span>Already have a CRM? <b style={{ color: "var(--ink)" }}>Keep it</b>, we plug right into HubSpot, Salesforce &amp; more.</span></div>
            <HeroRoster />
          </div>
          <div className="lp-hero-3d">
            <div className="lp-demo-window tilt3d" data-tilt="11">
              <div className="tilt3d-glare" aria-hidden="true" />
              <div className="lp-demo-chrome">
                <span className="lp-demo-dots"><i /><i /><i /></span>
                <span className="lp-demo-addr">app.friesenlabs.com</span>
                <span className="lp-demo-live"><span className="dot" />Live</span>
              </div>
              <div className="lp-demo-stage" style={{ gridTemplateColumns: "1fr", minHeight: 0 }}>
                <div className="lp-demo-canvas" style={{ borderRight: "none" }}><FoxDemo /></div>
              </div>
            </div>
          </div>
        </div>
        <div className="lp-proof-marquee" aria-hidden="true">
          <div className="lp-marquee-track">
            {[...LP_TESTIMONIALS, ...LP_TESTIMONIALS].map((t, i) => (
              <span className="lp-proof-chip" key={i}><b>{t.metric}</b> · {t.role}</span>
            ))}
          </div>
        </div>
      </section>

      {/* products, grouped by the stack */}
      <section className="lp-section alt" id="products">
        <div className="lp-wrap lp-head-left">
          <div className="lp-eyebrow">One system, not nine tools</div>
          <h2 className="lp-h2">Eleven products. One agentic stack.</h2>
          <p className="lp-sub">Five layers that work as one system. Tap any product to see what it does for you.</p>
          <div className="lp-stack">
            {LP_STACK.map((L, li) => (
              <React.Fragment key={L.h}>
                <div className="lp-layer" style={{ "--fc": L.fc }}>
                  <div>
                    <div className="ll-eyebrow">{L.eyebrow}</div>
                    <h3>{L.h}</h3>
                    <div className="ll-desc">{L.desc}</div>
                  </div>
                  <div className="lp-layer-pills">
                    {L.pills.map(([n, ic, tone]) => {
                      const [bg, fg] = LP_TONE[tone];
                      const prod = LP_PRODUCTS.find((p) => p.name === n);
                      return <span className={"lp-pp" + (prod ? " clickable" : "")} key={n} {...(prod ? { role: "button", tabIndex: 0, onClick: () => setOpenProduct(prod.id), onKeyDown: onActivate(() => setOpenProduct(prod.id)) } : {})}><span className="pp-ico" style={{ background: bg, color: fg }}><LpIcon name={ic} size={14} /></span>{n}{prod && <LpIcon name="arrowRight" size={12} sw={2.2} style={{ opacity: .45, marginLeft: 1 }} />}</span>;
                    })}
                  </div>
                </div>
                {li < LP_STACK.length - 1 && <div className="lp-stack-arrow"><LpIcon name="chevDown" size={18} /></div>}
              </React.Fragment>
            ))}
          </div>
        </div>
      </section>

      {/* nice-to-have roadmap products */}
      <NiceToHave />

      {/* demos */}
      <section className="lp-section" id="demos">
        <div className="lp-wrap">
          <div className="lp-eyebrow">See it in action</div>
          <h2 className="lp-h2">See exactly what your agents do.</h2>
          <p className="lp-sub">This is the real product, running live right here. Click around.</p>
          <div className="lp-demo-tabs">
            {LP_DEMOS.map((d) => <button key={d.id} className={"lp-demo-tab" + (demoTab === d.id ? " active" : "")} onClick={() => setDemoTab(d.id)}>{d.tab}</button>)}
          </div>
          <div className="lp-demo-window">
            <div className="lp-demo-chrome">
              <span className="lp-demo-dots"><i /><i /><i /></span>
              <span className="lp-demo-addr">{activeDemo.cat.toLowerCase()}.friesenlabs.com</span>
              <span className="lp-demo-live"><span className="dot" />Live</span>
            </div>
            <div className="lp-demo-stage">
              <div className="lp-demo-canvas">{activeDemo.Demo()}</div>
              <div className="lp-demo-side">
                <span className="cat">{activeDemo.cat}</span>
                <h3>{activeDemo.title}</h3>
                <p>{activeDemo.desc}</p>
                <ul>{activeDemo.bullets.map((b) => <li key={b}><LpIcon name="check" size={16} sw={2.4} style={{ color: "var(--accent)", flexShrink: 0, marginTop: 1 }} />{b}</li>)}</ul>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* BYO CRM sales point */}
      <section className="lp-section alt">
        <div className="lp-wrap lp-byo" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 40, alignItems: "center" }}>
          <div>
            <span className="lp-pill"><LpIcon name="link" size={14} />Bring your own CRM</span>
            <h2 style={{ fontSize: 32, fontWeight: 760, letterSpacing: "-.03em", margin: "16px 0 0", textAlign: "left" }}>Love your CRM? Keep it.</h2>
            <p style={{ fontSize: 16, color: "var(--ink-2)", lineHeight: 1.6, marginTop: 14 }}>You don't have to rip anything out. Connect HubSpot, Salesforce or Pipedrive in Switchboard and your agents work right inside it, enriching contacts, sending outreach, and pushing approved actions back to your system of record.</p>
            <ul style={{ listStyle: "none", marginTop: 20, display: "flex", flexDirection: "column", gap: 11 }}>
              {["Command Center, Workflows, Agents & Greenlight work on your CRM's data", "Two-way sync, nothing lives in two places", "No migration, no data export, live in a day"].map((b) => (
                <li key={b} style={{ display: "flex", gap: 10, fontSize: 14.5 }}><LpIcon name="checkCircle" size={18} style={{ color: "var(--green)", flexShrink: 0 }} />{b}</li>
              ))}
            </ul>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            {[["HubSpot", "#ff7a59", "H"], ["Salesforce", "#00a1e0", "S"], ["Pipedrive", "#1a1a1a", "P"], ["Your CRM", "var(--accent)", "+"]].map(([n, c, l]) => (
              <div key={n} style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-md)", padding: 18, display: "flex", alignItems: "center", gap: 12, boxShadow: "var(--shadow-sm)" }}>
                <div style={{ width: 40, height: 40, borderRadius: 11, background: c, color: "#fff", display: "grid", placeItems: "center", fontWeight: 800, fontFamily: "var(--mono)", fontSize: 17 }}>{l}</div>
                <div><b style={{ fontSize: 14, fontWeight: 680 }}>{n}</b><div style={{ fontSize: 11.5, color: "var(--green)", fontWeight: 600, display: "flex", alignItems: "center", gap: 4 }}><span className="cdot" style={{ width: 6, height: 6, borderRadius: 99, background: "var(--green)" }} />Connects</div></div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Friesen vs GoHighLevel */}
      <VsSection />

      {/* live ROI calculator */}
      <RoiCalculator />

      {/* how it works */}
      <section className="lp-section">
        <div className="lp-wrap lp-hiw">
          <div className="lp-hiw-rail">
            <div className="lp-eyebrow">How it works</div>
            <h2 className="lp-h2">Live in an afternoon.</h2>
            <p className="lp-sub" style={{ marginTop: 14 }}>No rip-and-replace, no consultants. Three steps and your agents are working.</p>
            <a className="btn btn-primary btn-lg" style={{ marginTop: 22 }} href={SIGNUP_HREF}><LpIcon name="bolt" size={16} />Build your suite</a>
          </div>
          <div className="lp-hiw-steps">
            {[["01", "Connect your stack", "Plug in your CRM, inbox, calendar and payments, or start fresh with Uplift.", "plug"], ["02", "Hire your agents", "Pick your crew, give them names and faces, and set how much they can do on their own.", "spark"], ["03", "Approve & go", "Agents work 24/7. The judgment calls land in Greenlight for your one-tap sign-off.", "checkCircle"]].map(([n, h, p, ic], i, arr) => (
              <div className="lp-hiw-step" key={n}>
                <div className="lp-hiw-marker"><span className="lp-hiw-num">{n}</span>{i < arr.length - 1 && <span className="lp-hiw-line" />}</div>
                <div className="lp-hiw-body">
                  <div style={{ display: "flex", alignItems: "center", gap: 9 }}><LpIcon name={ic} size={17} style={{ color: "var(--accent-ink)" }} /><h3>{h}</h3></div>
                  <p>{p}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* full-bleed rhythm moment */}
      <section className="lp-fullbleed">
        <div className="lp-wrap" style={{ textAlign: "center" }}>
          <div className="lp-eyebrow" style={{ color: "color-mix(in oklch, var(--accent) 60%, #fff)" }}>The shift</div>
          <h2 style={{ fontSize: "clamp(28px, 4.5vw, 46px)", fontWeight: 780, letterSpacing: "-.03em", lineHeight: 1.1, margin: "14px auto 0", maxWidth: 760, color: "#fff", textWrap: "balance" }}>Stop hiring for busywork. Put a crew of agents on it, and get your nights back.</h2>
          <div style={{ display: "flex", gap: "clamp(24px,6vw,72px)", justifyContent: "center", flexWrap: "wrap", marginTop: 38 }}>
            {[["1,284", "tasks handled / mo"], ["47 hrs", "saved every week"], ["3.4×", "more pipeline touched"]].map(([n, l]) => (
              <div key={l}>
                <div style={{ fontSize: "clamp(30px,5vw,48px)", fontWeight: 800, letterSpacing: "-.04em", color: "#fff" }}>{n}</div>
                <div style={{ fontSize: 13, color: "oklch(1 0 0 / .6)", marginTop: 4, fontFamily: "var(--mono)" }}>{l}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* testimonials */}
      <section className="lp-section" id="testimonials">
        <div className="lp-wrap">
          <div className="lp-eyebrow">Loved by small businesses</div>
          <h2 className="lp-h2">Owners are getting their time back.</h2>
          <p className="lp-sub">Real owners who got their nights and weekends back.</p>
          <div className="lp-testi-grid">
            {LP_TESTIMONIALS.slice(0, 5).map((t, i) => (
              <figure className={"lp-testi" + (i === 0 ? " lp-testi-lead" : "")} key={t.name}>
                <LpIcon name="quote" size={i === 0 ? 30 : 22} style={{ color: "var(--accent)", opacity: .5 }} />
                <blockquote>{t.quote}</blockquote>
                <figcaption>
                  <div className="avatar" style={{ background: t.color, width: 38, height: 38, fontSize: 13 }}>{t.init}</div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <b>{t.name}</b>
                    <span>{t.role}</span>
                  </div>
                  <span className="lp-testi-metric">{t.metric}</span>
                </figcaption>
              </figure>
            ))}
          </div>
        </div>
      </section>

      {/* pricing / builder */}
      <section className="lp-section alt" id="pricing">
        <div className="lp-wrap">
          <div className="lp-eyebrow">Pricing</div>
          <h2 className="lp-h2">Pay for work done, not for seats.</h2>
          <p className="lp-sub">One monthly plan with agent credits included. No per seat fees, ever.</p>

          <div className="lp-model3" style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16, margin: "40px 0 8px" }}>
            {[["grid", "A monthly plan", "Pick your products and get a predictable monthly fee with a bucket of agent credits included. No surprises."], ["bolt", "Credits = agent work", "Every meaningful action, a workflow run, a prediction, an outreach, a knowledge answer, spends credits. Always transparent."], ["trend", "Overage only if you exceed", "Go over your bucket and it's a simple per-credit rate. Quiet month? You're never overpaying."]].map(([ic, h, p], i) => (
              <div className="card" key={h} style={{ padding: 22 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 11 }}>
                  <div className="lp-mod-ico" style={{ background: "var(--accent-soft)", color: "var(--accent-ink)" }}><LpIcon name={ic} size={18} /></div>
                  <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--ink-4)", fontWeight: 600 }}>0{i + 1}</span>
                </div>
                <h3 style={{ fontSize: 16, fontWeight: 700, letterSpacing: "-.01em" }}>{h}</h3>
                <p style={{ fontSize: 13.5, color: "var(--ink-2)", lineHeight: 1.55, marginTop: 7 }}>{p}</p>
              </div>
            ))}
          </div>

          <div className="lp-build">
            <div>
              <div className="lp-plan-chips">
                {Object.entries(LP_PLANS).map(([id, p]) => <button key={id} className={"lp-plan-chip" + (plan === id ? " active" : "")} onClick={() => applyPlan(id)}>{p.label}</button>)}
                <button className={"lp-plan-chip" + (plan === "custom" ? " active" : "")} onClick={() => setPlan("custom")}>Custom</button>
              </div>

              <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "14px 16px", borderRadius: "var(--r-md)", border: "1.5px solid " + (byo ? "var(--accent)" : "var(--line)"), background: byo ? "var(--accent-softer)" : "var(--surface)", marginBottom: 16, cursor: "pointer" }} role="switch" tabIndex={0} aria-checked={byo} aria-label="Bring your own CRM" onClick={() => setByoCrm(!byo)} onKeyDown={onActivate(() => setByoCrm(!byo))}>
                <div className="lp-mod-ico" style={{ background: "var(--surface)", color: "var(--accent-ink)" }}><LpIcon name="link" size={18} /></div>
                <div style={{ flex: 1 }}><b style={{ fontSize: 14.5, fontWeight: 680, display: "block" }}>Bring your own CRM</b><span style={{ fontSize: 12.5, color: "var(--ink-3)" }}>Keep HubSpot / Salesforce, skip Uplift, we connect to yours</span></div>
                <div className={"tog" + (byo ? " on" : "")} />
              </div>

              {LP_MODULES.map((m) => {
                const [bg, fg] = LP_TONE[m.tone]; const on = sel[m.id]; const disabled = m.id === "uplift" && byo;
                return (
                  <div key={m.id} className={"lp-mod" + (on ? " on" : "") + (m.req ? " req" : "")} style={{ opacity: disabled ? .5 : 1 }} role="button" tabIndex={disabled ? -1 : 0} aria-pressed={on} aria-disabled={disabled || undefined} onClick={() => !disabled && toggleMod(m)} onKeyDown={onActivate(() => !disabled && toggleMod(m))}>
                    <div className="lp-mod-ico" style={{ background: bg, color: fg }}><LpIcon name={m.icon} size={18} /></div>
                    <div className="m-info"><b>{m.name}{m.req && <span style={{ fontSize: 11, color: "var(--ink-4)", fontWeight: 500 }}> · included</span>}{disabled && <span style={{ fontSize: 11, color: "var(--ink-4)", fontWeight: 500 }}> · using your CRM</span>}</b><span>{m.blurb}</span></div>
                    <span className="m-price">${m.price}/mo</span>
                    <div className={"gl-check" + (on ? " on" : "")} style={{ marginTop: 0 }}><LpIcon name="check" size={12} sw={3} /></div>
                  </div>
                );
              })}
            </div>

            <div className="lp-summary">
              <h3>Your instance</h3>
              <div className="lp-price">${total}<span>/mo</span></div>
              {discount > 0 && <div className="lp-saver"><LpIcon name="bolt" size={13} />Bundle saver, 10% off applied</div>}
              <div style={{ display: "flex", alignItems: "center", gap: 9, marginTop: 14, padding: "11px 13px", background: "var(--accent-softer)", borderRadius: "var(--r-sm)" }}>
                <LpIcon name="bolt" size={16} style={{ color: "var(--accent-ink)" }} />
                <div style={{ fontSize: 12.5, color: "var(--accent-ink)", lineHeight: 1.4 }}><b style={{ fontWeight: 700 }}>≈ {credits.toLocaleString()} agent credits/mo</b> included<br />then $0.05 / extra credit · you set the cap</div>
              </div>
              <div className="lp-summary-list">
                {selectedMods.map((m) => <div className="sl" key={m.id}><LpIcon name="check" size={15} sw={2.4} style={{ color: "var(--green)" }} />{m.name}</div>)}
                {byo && <div className="sl" style={{ color: "var(--accent-ink)" }}><LpIcon name="link" size={15} style={{ color: "var(--accent-ink)" }} />Your CRM (HubSpot / Salesforce…)</div>}
                <div className="sl" style={{ color: "var(--ink-3)" }}><LpIcon name="shield" size={15} style={{ color: "var(--ink-3)" }} />Security &amp; Control <span style={{ marginLeft: "auto", fontSize: 11, fontWeight: 700, color: "var(--green)" }}>FREE</span></div>
              </div>
              <button className="btn btn-primary btn-lg" style={{ width: "100%" }} onClick={() => setModal("provision")}><LpIcon name="bolt" size={16} />Provision my instance</button>
              <button className="btn btn-ghost" style={{ width: "100%", marginTop: 10 }} onClick={() => setModal("book")}><LpIcon name="calendar" size={15} />Talk to us first</button>
              <p style={{ fontSize: 11.5, color: "var(--ink-4)", textAlign: "center", marginTop: 12 }}>Free to start · starter credits · no card required</p>
            </div>
          </div>

          <div className="lp-guarantees" style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 12, marginTop: 40 }}>
            {[["spark", "Free to start", "Starter credits on every account"], ["shield", "No bill shock", "Spend caps you control"], ["bolt", "No token costs", "We eat the AI & compute bills"], ["plug", "Your private instance", "Isolated, secure, never pooled"], ["target", "Pay for outcomes", "Priced by results, increasingly"]].map(([ic, h, p]) => (
              <div key={h} style={{ textAlign: "center" }}>
                <div style={{ width: 42, height: 42, borderRadius: 12, background: "var(--surface)", border: "1px solid var(--line)", color: "var(--accent-ink)", display: "grid", placeItems: "center", margin: "0 auto 10px", boxShadow: "var(--shadow-sm)" }}><LpIcon name={ic} size={19} /></div>
                <b style={{ fontSize: 13, fontWeight: 680, display: "block" }}>{h}</b>
                <span style={{ fontSize: 11.5, color: "var(--ink-3)", lineHeight: 1.45, display: "block", marginTop: 3 }}>{p}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ROI, the math */}
      <section className="lp-section">
        <div className="lp-wrap">
          <div className="lp-eyebrow">The math</div>
          <h2 className="lp-h2">Do more. Spend less. Keep more.</h2>
          <p className="lp-sub">A crew of agents for a fraction of the cost of headcount.</p>
          <div className="lp-roi-grid">
            {LP_ROI.map((r) => (
              <div className="lp-roi" key={r.b}>
                <div className="r-num">{r.num}</div>
                <b>{r.b}</b>
                <p>{r.p}</p>
              </div>
            ))}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 14, marginTop: 26, padding: "18px 22px", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-lg)", boxShadow: "var(--shadow-sm)", flexWrap: "wrap" }}>
            <div style={{ width: 44, height: 44, borderRadius: 12, background: "var(--green-soft)", color: "oklch(0.42 0.12 152)", display: "grid", placeItems: "center", flexShrink: 0 }}><LpIcon name="trend" size={22} /></div>
            <p style={{ flex: 1, minWidth: 240, fontSize: 14.5, color: "var(--ink-2)", lineHeight: 1.55 }}><b style={{ color: "var(--ink)" }}>Money back in your pocket, time back in your day.</b> Cut the cost of busywork, grow revenue without growing payroll, and reinvest both into the business, and yourself.</p>
            <button className="btn btn-primary" onClick={() => go("pricing")}><LpIcon name="bolt" size={16} />See the plans</button>
          </div>
          <p style={{ fontSize: 11.5, color: "var(--ink-4)", textAlign: "center", marginTop: 14 }}>Figures are typical outcomes for small teams and vary by business.</p>
        </div>
      </section>

      {/* enablement, owner + team */}
      <section className="lp-section alt">
        <div className="lp-wrap">
          <div className="lp-eyebrow">Built for you and your team</div>
          <h2 className="lp-h2">Agents that lift everyone up.</h2>
          <p className="lp-sub">Nobody gets replaced. Everybody gets leverage.</p>
          <div className="lp-enable">
            <div className="lp-enable-card owner">
              <div className="ec-ico" style={{ background: "var(--surface)", color: "var(--accent-ink)" }}><LpIcon name="spark" size={24} /></div>
              <h3>For you, the owner</h3>
              <p className="ec-sub">Run a bigger, calmer business, without a bigger team or a longer day.</p>
              <ul>{LP_ENABLE_OWNER.map((b) => <li key={b}><LpIcon name="checkCircle" size={18} style={{ color: "var(--accent)", flexShrink: 0, marginTop: 1 }} />{b}</li>)}</ul>
            </div>
            <div className="lp-enable-card team">
              <div className="ec-ico" style={{ background: "var(--green-soft)", color: "oklch(0.42 0.12 152)" }}><LpIcon name="users" size={24} /></div>
              <h3>For your team</h3>
              <p className="ec-sub">Give every employee an agent teammate that clears the busywork off their plate.</p>
              <ul>{LP_ENABLE_TEAM.map((b) => <li key={b}><LpIcon name="checkCircle" size={18} style={{ color: "var(--green)", flexShrink: 0, marginTop: 1 }} />{b}</li>)}</ul>
            </div>
          </div>
        </div>
      </section>

      {/* nonprofit */}
      <section className="lp-section">
        <div className="lp-wrap">
          <div style={{ display: "flex", alignItems: "center", gap: 28, flexWrap: "wrap", background: "var(--accent-softer)", border: "1px solid var(--accent-soft)", borderRadius: "var(--r-xl)", padding: "32px 36px" }}>
            <div style={{ flex: 1, minWidth: 260 }}>
              <span className="lp-pill"><LpIcon name="spark" size={14} />Public benefit corporation</span>
              <h2 style={{ fontSize: 28, fontWeight: 760, letterSpacing: "-.03em", margin: "14px 0 0", textAlign: "left" }}>A company with a mission, and a foundation to back it.</h2>
              <p style={{ fontSize: 15, color: "var(--ink-2)", lineHeight: 1.6, marginTop: 12, maxWidth: 560 }}>Friesen Labs is a public benefit corporation building agentic AI for small business. Our independent nonprofit wing, the Friesen Labs Foundation, runs open research, free education, and need-based access, so the businesses that anchor communities can use it too. Paid plans keep the company sustainable; a portion funds the Foundation.</p>
              <p style={{ fontSize: 13, color: "var(--ink-3)", lineHeight: 1.55, marginTop: 12, maxWidth: 560 }}><b style={{ color: "var(--ink)", fontWeight: 650 }}>Separate by design.</b> The Foundation has its own 501(c)(3) board and books. Value flows from the company to the Foundation, never the other way. It serves its mission, not our sales.</p>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <a className="btn btn-primary btn-lg" href="Foundation.html"><LpIcon name="spark" size={16} />Visit the Foundation</a>
              <button className="btn btn-ghost" onClick={() => setModal("statement")}><LpIcon name="doc" size={15} />Read our statement</button>
            </div>
          </div>
        </div>
      </section>

      {/* cta band */}
      {/* about / our mission */}
      <section className="lp-section alt" id="about">
        <div className="lp-wrap">
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1.1fr", gap: 44, alignItems: "center" }} className="lp-about-grid">
            <div>
              <div className="lp-eyebrow" style={{ textAlign: "left" }}>About Friesen Labs</div>
              <h2 style={{ fontSize: 32, fontWeight: 760, letterSpacing: "-.03em", margin: "12px 0 0", textAlign: "left" }}>A public benefit corporation, with a foundation behind it.</h2>
              <p style={{ fontSize: 15, color: "var(--ink-2)", lineHeight: 1.65, marginTop: 16 }}>Friesen Labs is a for-profit company with a public mission written into how it operates: put the same agentic AI the largest companies use into the hands of the cafés, plumbers, clinics and shops that anchor their communities. The company builds and sells the software; agents do the busywork, owners stay in command.</p>
              <p style={{ fontSize: 15, color: "var(--ink-2)", lineHeight: 1.65, marginTop: 12 }}>Our independent nonprofit wing, the Friesen Labs Foundation, runs the charitable work, open research, free education, and need-based access for businesses that can't pay. Separate boards, separate books; the separation is the point.</p>
              <div style={{ display: "flex", gap: 10, marginTop: 22 }}>
                <a className="btn btn-primary" href="Foundation.html"><LpIcon name="spark" size={16} />Visit the Foundation</a>
                <button className="btn btn-ghost" onClick={() => setModal("statement")}><LpIcon name="doc" size={16} />Read our full statement</button>
              </div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
              {[["building", "The company", "A public benefit corporation building the software."], ["spark", "The Foundation", "A 501(c)(3) running research, education & access."], ["trend", "Company and Foundation", "A portion of revenue funds the charitable work."], ["shield", "Separate by design", "Independent board and books, never a sales channel."]].map(([ic, t, d]) => (
                <div key={t} className="card card-pad">
                  <div className="feed-ico" style={{ width: 38, height: 38, background: "var(--accent-soft)", color: "var(--accent-ink)", marginBottom: 12 }}><LpIcon name={ic} size={18} /></div>
                  <b style={{ fontSize: 14.5, fontWeight: 700, display: "block" }}>{t}</b>
                  <p style={{ fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.5, marginTop: 5 }}>{d}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* research */}
      <section className="lp-section" id="research">
        <div className="lp-wrap">
          <div className="lp-eyebrow">Open research</div>
          <h2 className="lp-h2">Our Foundation publishes what we learn.</h2>
          <p className="lp-sub">Open research is a program of the <a href="Foundation.html" style={{ color: "var(--accent-ink)", fontWeight: 600 }}>Friesen Labs Foundation</a>, our independent nonprofit wing. It's released publicly so any small business can benefit. Here's what we've been studying.</p>
          <div className="lp-research-grid">
            {LP_RESEARCH.map((r) => (
              <a key={r.title} className="lp-research" {...actionLink(() => setPaper(r))}>
                <div className="lp-research-top"><span className="lp-research-tag">{r.tag}</span><span className="lp-research-date">{r.date} · {r.readTime}</span></div>
                <h3>{r.title}</h3>
                <p>{r.blurb}</p>
                <span className="lp-research-meta">M. Yee · Friesen Labs Foundation</span>
                <span className="lp-research-link">Read the paper<LpIcon name="arrowRight" size={14} sw={2.2} /></span>
              </a>
            ))}
          </div>
          <div style={{ textAlign: "center", marginTop: 30 }}>
            <button className="btn btn-ghost" onClick={() => setModal("email")}><LpIcon name="mail" size={16} />Get research updates</button>
          </div>
        </div>
      </section>

      {/* meet the team */}
      <section className="lp-section alt" id="team">
        <div className="lp-wrap">
          <div className="lp-eyebrow">Meet the team</div>
          <h2 className="lp-h2">Built by people who root for small business.</h2>
          <p className="lp-sub">A two founder nonprofit putting agentic tools in the hands of every small business.</p>
          <div className="lp-cred">
            <div className="lp-cred-ico"><LpIcon name="shield" size={22} /></div>
            <p>We're not n8n hobbyists or tech bros chasing a trend. Our team has shipped <b>agentic AI in production at some of the world's largest companies</b>, work that has delivered <b>hundreds of millions of dollars in measurable revenue and cost savings</b>. We're bringing that same enterprise-grade muscle to small business.</p>
          </div>
          <div className="lp-team-grid">
            {LP_FOUNDERS.map((f) => { const photo = LP_FOUNDER_PHOTOS[f.id]; return (
              <div className="lp-founder" key={f.id}>
                {photo
                  ? <div style={{ width: 104, height: 104, borderRadius: 99, overflow: "hidden", flexShrink: 0, boxShadow: "var(--shadow-sm)" }}><img src={photo.src} alt={f.name} style={{ width: "100%", height: "100%", objectFit: "cover", objectPosition: photo.pos || "50% 30%", display: "block" }} /></div>
                  : <div aria-hidden="true" style={{ width: 104, height: 104, borderRadius: 99, flexShrink: 0, display: "grid", placeItems: "center", fontSize: 30, fontWeight: 720, color: "#fff", background: "linear-gradient(145deg, var(--accent), var(--accent-press))", boxShadow: "var(--shadow-sm)" }}>{f.name.split(" ").map((w) => w[0]).slice(0, 2).join("")}</div>}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <h3>{f.name}</h3>
                  <div className="lp-founder-title">{f.title}</div>
                  <p>{f.bio}</p>
                  <div className="lp-founder-social">
                    <a href={f.linkedin} target="_blank" rel="noopener noreferrer" aria-label={`${f.name} on LinkedIn`} title={`${f.name} on LinkedIn`}><LpIcon name="linkedin" size={17} /></a>
                    <a href={f.instagram} target="_blank" rel="noopener noreferrer" aria-label={`${f.name} on Instagram`} title={`${f.name} on Instagram`}><LpIcon name="instagram" size={17} /></a>
                  </div>
                </div>
              </div>
            ); })}
          </div>
        </div>
      </section>

      <section className="lp-section">
        <div className="lp-wrap">
          <div className="lp-cta-band">
            <h2>Put your busywork on autopilot.</h2>
            <p>Spin up your agentic workspace today, or talk to a human about what you're trying to automate.</p>
            <div className="lp-cta-row">
              <a className="btn btn-lg btn-onink" href={SIGNUP_HREF}><LpIcon name="bolt" size={16} />Get started free</a>
              <button className="btn btn-lg btn-onink-ghost" onClick={() => setModal("book")}><LpIcon name="calendar" size={16} />Book a call</button>
              <button className="btn btn-lg btn-onink-ghost" onClick={() => setModal("email")}><LpIcon name="mail" size={16} />Email us</button>
            </div>
          </div>
        </div>
      </section>

      {/* app store */}
      <section className="lp-section" style={{ paddingTop: 0 }}>
        <div className="lp-wrap" style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 22, flexWrap: "wrap", textAlign: "center" }}>
          <div>
            <div className="lp-eyebrow" style={{ textAlign: "center" }}>Now on iPhone</div>
            <h2 style={{ fontSize: 24, fontWeight: 750, letterSpacing: "-.03em", marginTop: 8 }}>Run your business from your pocket.</h2>
            <p style={{ fontSize: 14, color: "var(--ink-2)", marginTop: 8, maxWidth: 420 }}>Approve agent actions, check your pipeline, and watch the work happen, wherever you are.</p>
          </div>
          <a className="lp-appstore" href="#" role="button" aria-label="iOS app coming soon" onClick={(e) => e.preventDefault()}>
            <svg viewBox="0 0 24 24" width="26" height="26" fill="#fff" aria-hidden="true"><path d="M16.365 1.43c0 1.14-.46 2.23-1.2 3.02-.79.85-2.08 1.51-3.16 1.42-.13-1.1.42-2.27 1.13-3.01.79-.83 2.18-1.45 3.23-1.43zM20.8 17.12c-.5 1.16-.74 1.68-1.39 2.71-.9 1.44-2.18 3.24-3.76 3.25-1.4.01-1.76-.92-3.67-.91-1.9.01-2.3.92-3.7.9-1.58-.01-2.79-1.62-3.7-3.06-2.53-4.01-2.8-8.72-1.24-11.22 1.11-1.78 2.86-2.82 4.5-2.82 1.68 0 2.73.92 4.12.92 1.35 0 2.17-.92 4.11-.92 1.47 0 3.02.8 4.13 2.18-3.63 1.99-3.04 7.17.2 8.97z" /></svg>
            <span><small>Download on the</small><b>App Store</b></span>
          </a>
        </div>
      </section>

      {/* closing CTA band */}
      <FinalCta onBook={() => setModal("book")} />

      <footer className="lp-footer">
        <div className="lp-wrap">
          <div className="lp-foot-grid">
            <div style={{ maxWidth: 320 }}>
              <div className="lp-brand" style={{ marginBottom: 11 }}><div className="brand-mark" style={{ width: 28, height: 28 }}><Logo size={16} /></div><b style={{ fontSize: 15 }}>Friesen Labs</b></div>
              <p style={{ fontSize: 13, color: "var(--ink-3)", lineHeight: 1.55 }}>A public benefit corporation building agentic software for small business. Our independent nonprofit wing, the Friesen Labs Foundation, makes it reachable for the businesses that anchor communities.</p>
              <div style={{ display: "flex", gap: 9, marginTop: 14 }}>
                <a className="btn btn-soft btn-sm" href="Foundation.html"><LpIcon name="spark" size={13} />The Foundation</a>
                <button className="btn btn-ghost btn-sm" onClick={() => setModal("email")}><LpIcon name="mail" size={13} />Contact</button>
              </div>
            </div>
            <div className="lp-foot-cols">
              <div className="lp-foot-col">
                <h4>Product</h4>
                <a {...sectionLink("products")}>Products</a>
                <a {...sectionLink("pricing")}>Pricing</a>
                <a {...sectionLink("demos")}>See it work</a>
                <a href={SIGNIN_HREF} onClick={signInClick}>Sign in</a>
              </div>
              <div className="lp-foot-col">
                <h4>Organization</h4>
                <a href="Foundation.html">Foundation</a>
                <a {...sectionLink("research")}>Research</a>
                <a {...sectionLink("team")}>Team</a>
                <a {...actionLink(() => setDoc("Form 990"))}>Form 990</a>
              </div>
              <div className="lp-foot-col">
                <h4>Legal</h4>
                <a {...actionLink(() => setDoc("Privacy Policy"))}>Privacy Policy</a>
                <a {...actionLink(() => setDoc("Terms of Service"))}>Terms of Service</a>
                <a {...actionLink(() => setDoc("Donor Privacy Policy"))}>Donor privacy</a>
                <a {...actionLink(() => setDoc("Accessibility Statement"))}>Accessibility</a>
              </div>
            </div>
          </div>
          <div className="lp-foot-legal">
            <span>© 2026 Friesen Labs PBC, a public benefit corporation. The Friesen Labs Foundation is a separate 501(c)(3) tax-exempt organization (EIN 00-0000000); donations to the Foundation are tax-deductible to the extent allowed by law.</span>
            <span>1 Main Street, Suite 100, Austin, TX 78701</span>
          </div>
        </div>
      </footer>

      {modal === "book" && <BookModal onClose={() => setModal(null)} />}
      {modal === "email" && <EmailModal onClose={() => setModal(null)} />}
      {modal === "donate" && <DonateModal onClose={() => setModal(null)} />}
      {modal === "provision" && <ProvisionModal selected={selectedMods} byo={byo} onClose={() => setModal(null)} />}
      {modal === "statement" && (
        <div className="lp-modal-scrim" onClick={() => setModal(null)} style={{ alignItems: "flex-start", overflowY: "auto", padding: "5vh 16px" }}>
          <div className="lp-paper" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <button className="icon-btn" aria-label="Close" style={{ position: "absolute", top: 16, right: 16 }} onClick={() => setModal(null)}><LpIcon name="x" size={18} /></button>
            <div className="lp-paper-tag">Company Statement</div>
            <h1 className="lp-paper-title">Friesen Labs</h1>
            <div className="lp-paper-meta" style={{ marginTop: 14 }}>A public benefit corporation for small business · Austin, TX</div>
            {[
              ["Who we are", "Friesen Labs is a company building agentic AI for small business, and the steward of a foundation that makes sure the businesses holding communities together can get it too. We're organized as a public benefit corporation: a for-profit company with a public mission written into how it operates. The company builds and sells the software. The Friesen Labs Foundation, our independent nonprofit wing, runs the charitable work, free education, open research, and need-based access for businesses that can't pay. The two are separate by design, and the separation is the point: it keeps the charitable programs genuinely charitable and the company honest about being a company."],
              ["What we build", "An agentic operating system for small business, where AI agents do the work and the owner stays in command. Command Center gives a calm morning overview. Uplift is an agentic CRM where every deal has an agent working it. Frontline is an autonomous support desk that deflects the routine and routes the rest to a human. Workflows lets anyone build automations by dragging blocks or describing them. Agents, the Agent Studio, and the Skill Marketplace let owners hire, build, and equip a crew of agents. Cortex is the private intelligence layer: knowledge grounding, fine-tuned private models, and a flywheel that sharpens with every decision. Greenlight keeps a human in the loop on anything sensitive. Sidecar and Switchboard let agents work on top of the tools a business already uses. Security and Control, kill switch, guardrails, autonomy levels, full audit trail, is included on every tier."],
              ["How we operate", "Agents handle the busywork, so one owner gets the output of a team and people focus on relationships and judgment. Nobody gets replaced; everybody gets leverage. We price for the work the agents do, reachable on a small-business budget, not an enterprise one, and we keep the runtime abstracted so we're never locked to one provider. A permanent free tier is our own commitment, on the house."],
              ["Our nonprofit wing", "The Friesen Labs Foundation is a 501(c)(3) with a charitable, educational, and scientific purpose: to keep capable AI from becoming something only large companies can afford. It runs three programs. Open research: peer-reviewed and preprint work on the real questions of agentic adoption, released publicly with open-source tools and benchmarks anyone can use. Free education: plain-language curriculum, workshops, and templates that help any owner adopt AI safely, whether or not they ever touch our software. Charitable access: need-based support that puts safe agentic AI in the hands of businesses whose survival matters to their communities, the only clinic, pharmacy, grocery, or repair shop for miles; owner-operators in rural and under-resourced areas. The Foundation has its own independent board and its own books. It serves its mission, not the company's sales."],
              ["How it fits together", "The company sustains itself on earned revenue from paid plans and contributes a portion of it to the Foundation; philanthropic grants fund the rest of the charitable work. Value flows from the company to the Foundation, never the other way, the Foundation isn't a sales channel, and donated or granted funds go only to its charitable programs. Any services the two share are documented and priced at arm's length. The company publishes its mission metrics; the Foundation publishes its Form 990 and an annual report on who it reached and what it cost."],
              ["Why this structure", "Because the goal is to make sure the businesses that anchor a community don't fail for want of tools the largest companies take for granted, and the most durable way to pursue that is to build a real company that can fund the work, alongside a real foundation that can do the parts the market won't."],
            ].map(([h, p], i) => (
              <div key={i} className="lp-paper-sec"><div className="lp-paper-h">{h}</div><p>{p}</p></div>
            ))}
            <div className="lp-paper-cite">
              <div className="lp-paper-h">Press boilerplate</div>
              <code>Friesen Labs is a public benefit corporation building agentic AI for small business. Its independent nonprofit wing, the Friesen Labs Foundation (a 501(c)(3)), runs open research, free education, and need-based access so the businesses that anchor communities can use it too. friesenlabs.org</code>
            </div>
            <div style={{ display: "flex", gap: 9, marginTop: 18, flexWrap: "wrap" }}>
              <button className="btn btn-primary" onClick={() => setModal("donate")}><LpIcon name="spark" size={15} />Support the mission</button>
              <button className="btn btn-ghost" onClick={() => setModal(null)}>Close</button>
            </div>
          </div>
        </div>
      )}
      {paper && (
        <div className="lp-modal-scrim" onClick={() => setPaper(null)} style={{ alignItems: "flex-start", overflowY: "auto", padding: "5vh 16px" }}>
          <div className="lp-paper" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <button className="icon-btn" aria-label="Close" style={{ position: "absolute", top: 16, right: 16 }} onClick={() => setPaper(null)}><LpIcon name="x" size={18} /></button>
            <div className="lp-paper-tag">{paper.tag} · Friesen Labs Foundation</div>
            <h1 className="lp-paper-title">{paper.title}</h1>
            <div className="lp-paper-authors">
              <span><b>Matthew Yee</b></span>
              <span className="lp-paper-aff">Friesen Labs Foundation, Austin, TX · <a href="mailto:research@friesenlabs.org">research@friesenlabs.org</a></span>
            </div>
            <div className="lp-paper-meta">Published {paper.date} · Friesen Labs Foundation Technical Report · {paper.readTime} read</div>
            <div className="lp-paper-abstract">
              <div className="lp-paper-h">Abstract</div>
              <p>{paper.abstract}</p>
            </div>
            {paper.body.map((s, i) => (
              <div key={i} className="lp-paper-sec">
                <div className="lp-paper-h">{i + 1}. {s.h}</div>
                <p>{s.p}</p>
              </div>
            ))}
            <div className="lp-paper-cite">
              <div className="lp-paper-h">Cite this work</div>
              <code>Yee, M. ({paper.date.split(" ")[1]}). {paper.title}. Friesen Labs Foundation Technical Report.</code>
            </div>
            <div style={{ display: "flex", gap: 9, marginTop: 18, flexWrap: "wrap" }}>
              <button className="btn btn-primary" onClick={() => { setPaper(null); setModal("email"); }}><LpIcon name="mail" size={15} />Get research updates</button>
              <button className="btn btn-ghost" onClick={() => setPaper(null)}>Close</button>
            </div>
          </div>
        </div>
      )}
      {doc && (
        <div className="lp-modal-scrim" onClick={() => setDoc(null)}>
          <div className="lp-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <div className="lp-modal-head">
              <div className="lp-prod-ico" style={{ background: "var(--accent-soft)", color: "var(--accent-ink)", marginBottom: 0 }}><LpIcon name="doc" size={20} /></div>
              <div style={{ flex: 1 }}><h3 style={{ fontSize: 19, fontWeight: 730, letterSpacing: "-.02em" }}>{doc}</h3><p style={{ fontSize: 13, color: "var(--ink-3)", marginTop: 2 }}>Friesen Labs nonprofit disclosures</p></div>
              <button className="icon-btn" aria-label="Close" onClick={() => setDoc(null)}><LpIcon name="x" size={18} /></button>
            </div>
            <div className="lp-modal-body">
              <p style={{ fontSize: 14, color: "var(--ink-2)", lineHeight: 1.6 }}>Our {doc} is being finalized with our counsel ahead of public launch. We're committed to full transparency, request the current copy and we'll send it over.</p>
              <button className="btn btn-primary btn-lg" style={{ width: "100%", marginTop: 16 }} onClick={() => { setDoc(null); setModal("email"); }}><LpIcon name="mail" size={16} />Request {doc}</button>
              <p style={{ fontSize: 11.5, color: "var(--ink-4)", textAlign: "center", marginTop: 12 }}>Once published, this will link directly to the document.</p>
            </div>
          </div>
        </div>
      )}
      {openProduct && <ProductPage id={openProduct} onClose={() => setOpenProduct(null)} onAdd={addProduct} onBook={() => { setOpenProduct(null); setModal("book"); }} />}

      <BackToTop />

      {/* sticky mobile CTA — always-reachable primary action */}
      <div className="lp-mobar">
        <a className="btn btn-primary" href={SIGNUP_HREF}><LpIcon name="bolt" size={16} />Build your suite</a>
        <a className="lp-mobar-signin" href={SIGNIN_HREF} onClick={signInClick}>Sign in</a>
      </div>
    </div>
  );
}

export default Landing;

