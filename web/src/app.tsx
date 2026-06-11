// @ts-nocheck
import React from "react";
import "./globals";
import { SafeHtml } from "./lib/SafeHtml";
import { useAuth } from "./auth/AuthContext";
// API-wired surfaces (real mode). When the build runs against the real control
// plane (VITE_API_MOCK=0), the API-backed surfaces — Command Center
// (DashboardView), Pipeline (PipelineBoard), Contacts (ContactsDirectory),
// Agents (AgentsRoster), Greenlight (GreenlightQueue), Ask agents (ChatDock),
// Switchboard (IntegrationsPanel) — mount with honest loading/empty/error states. EVERY
// other route renders an explicit "isn't live yet" panel in real mode: no
// FLStore prototype screen, demo number, fake badge/feed/agent-rail, or
// prototype overlay (palette, intake, marketplace, onboarding/tour) is
// reachable when the build is real. The full FLStore prototype experience
// exists only in mock builds.
import { isApiMock } from "./api/client";
import GreenlightQueue from "./api/GreenlightQueue";
import DashboardView from "./api/DashboardView";
import ChatDock from "./api/ChatDock";
import IntegrationsPanel from "./api/IntegrationsPanel";
import PipelineBoard from "./api/PipelineBoard";
import ContactsDirectory from "./api/ContactsDirectory";
import AgentsRoster from "./api/AgentsRoster";
import StudioView from "./api/StudioView";
import WorkflowsView from "./api/WorkflowsView";
import ReportsView from "./api/ReportsView";
import DashboardsView from "./api/DashboardsView";
import KnowledgeView from "./api/KnowledgeView";
import SecurityControls from "./api/SecurityControls";
import BillingManage from "./api/BillingManage";
import CortexView from "./api/CortexView";
import AccountDataControls from "./api/AccountDataControls";
import WorkspaceSettings from "./api/WorkspaceSettings";
import MarketplaceView from "./api/MarketplaceView";
import ModulesView from "./api/ModulesView";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// app.jsx, shell: sidebar, topbar, routing, tweaks, palette
import { FirstRunChecklist } from "./onboarding/FirstRunChecklist";
import { defaultClient } from "./api/client";

const ACCENTS = [
  { id: "indigo", name: "Indigo", h: 277 },
  { id: "blue",   name: "Blue",   h: 248 },
  { id: "teal",   name: "Teal",   h: 192 },
  { id: "green",  name: "Green",  h: 152 },
  { id: "terra",  name: "Terra",  h: 38  },
];

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "accent": "indigo",
  "dark": false,
  "density": "regular"
}/*EDITMODE-END*/;

// Honest real-mode placeholder. In a real build, any route without an
// API-backed component renders this — never an FLStore prototype screen, which
// would present demo data as the tenant's own. It promises nothing it can't
// keep: this surface simply isn't live yet.
const ComingSoon = ({ title, icon }) => (
  <div className="screen screen-anim" data-testid="coming-soon" style={{ display: "grid", placeItems: "center", minHeight: "60vh" }}>
    <div style={{ textAlign: "center", maxWidth: 400 }}>
      <div style={{ width: 60, height: 60, borderRadius: 16, background: "var(--accent-soft)", color: "var(--accent-ink)", display: "grid", placeItems: "center", margin: "0 auto 18px" }}>
        <Icon name={icon} size={28} />
      </div>
      <h2 style={{ fontSize: 21, fontWeight: 720, letterSpacing: "-.02em" }}>{title} isn&rsquo;t live yet</h2>
      <p style={{ color: "var(--ink-3)", fontSize: 14, marginTop: 8, lineHeight: 1.55 }}>
        Friesen Labs is rolling out surface by surface. <b style={{ color: "var(--ink)" }}>Command Center</b>, <b style={{ color: "var(--ink)" }}>Pipeline</b>, <b style={{ color: "var(--ink)" }}>Contacts</b>, <b style={{ color: "var(--ink)" }}>Agents</b>, <b style={{ color: "var(--ink)" }}>Greenlight</b>, <b style={{ color: "var(--ink)" }}>Ask agents</b> and <b style={{ color: "var(--ink)" }}>Switchboard</b> are live today; this area is still being built.
      </p>
    </div>
  </div>
);

// Shown when a tenant navigates to a surface whose module they've turned OFF in
// Settings → "Your suite". Honest: the surface exists, they just haven't enabled
// it — with a one-tap path to go enable it.
const ModuleNotEnabled = ({ title, icon, onManage }) => (
  <div className="screen screen-anim" data-testid="module-not-enabled" style={{ display: "grid", placeItems: "center", minHeight: "60vh" }}>
    <div style={{ textAlign: "center", maxWidth: 400 }}>
      <div style={{ width: 60, height: 60, borderRadius: 16, background: "var(--accent-soft)", color: "var(--accent-ink)", display: "grid", placeItems: "center", margin: "0 auto 18px" }}>
        <Icon name={icon} size={28} />
      </div>
      <h2 style={{ fontSize: 21, fontWeight: 720, letterSpacing: "-.02em" }}>{title} isn&rsquo;t in your suite</h2>
      <p style={{ color: "var(--ink-3)", fontSize: 14, marginTop: 8, lineHeight: 1.55 }}>
        This module isn&rsquo;t turned on for your workspace. Add it in Settings to start using it.
      </p>
      <button className="btn btn-primary" style={{ marginTop: 18 }} onClick={onManage}>Manage your suite</button>
    </div>
  </div>
);

// Slide-over panel hosting the API-wired ChatDock in real mode. Reuses the
// prototype's .chat/.scrim chrome so "Ask agents" feels identical, but the
// content is the real /chat surface (grounded answers, citations, honest
// "Agents unavailable" copy on 503). Stays mounted so the thread survives
// close/reopen, exactly like the prototype AgentChat.
function RealChatPanel({ open, onClose }) {
  useEffect(() => {
    if (!open) return;
    const k = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [open, onClose]);
  return (
    <>
      <div className={"scrim" + (open ? " show" : "")} style={{ pointerEvents: open ? "auto" : "none" }} onClick={onClose} />
      <div className={"chat" + (open ? " show" : "")} data-testid="real-chat-panel" aria-hidden={!open}>
        <div className="chat-head">
          <div style={{ flex: 1 }}>
            <b style={{ fontSize: 14.5, fontWeight: 700, display: "flex", alignItems: "center", gap: 7 }}>Ask your agents</b>
            <span style={{ fontSize: 11.5, color: "var(--ink-3)" }}>grounded answers with citations</span>
          </div>
          <button className="icon-btn" onClick={onClose}><Icon name="x" size={18} /></button>
        </div>
        <div className="chat-body" style={{ padding: 0 }}>
          <ChatDock embedded />
        </div>
      </div>
    </>
  );
}

function App() {
  const { STAGES, NAV, NAV_CRM, NAV_AGENTS, NAV_CONNECT, NAV2, FEED_LIVE } = window.FL_DATA;
  // Real mode (VITE_API_MOCK=0): primary surfaces read through the ApiClient.
  // Mock mode keeps the full FLStore prototype experience.
  const realMode = !isApiMock();
  const agents = useStore((s) => s.agents);
  const pendingCount = useStore((s) => s.greenlight.filter((i) => i.status === "pending").length);
  const gamifyOn = useStore((s) => s.gamifyOn);
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [route, setRoute] = useState("dashboard");
  // Per-tenant module entitlements (real mode): the set of route-ids this tenant
  // has enabled in Settings → "Your suite". null = not loaded / errored / 503 →
  // we SHOW ALL routes (fail-open: never hide a surface because the gate is down).
  const [enabledRoutes, setEnabledRoutes] = useState(null);
  // First-run: bumped after a "Load sample data" so the API-wired surfaces remount
  // and re-fetch (the populated views surface immediately).
  const [sampleReloadKey, setSampleReloadKey] = useState(0);
  const loadSampleData = useCallback(async () => {
    await defaultClient().loadSampleData();
    setSampleReloadKey((k) => k + 1);
  }, []);
  // Load this tenant's enabled modules (real mode only) so the app shows ONLY the
  // surfaces they've chosen. Any failure (503/404/network) leaves enabledRoutes
  // null → show-all, so the gate can never strand a tenant out of their workspace.
  useEffect(() => {
    if (!realMode) return;
    let live = true;
    defaultClient().getModules()
      .then((cat) => { if (live) setEnabledRoutes(new Set(cat.enabled_routes)); })
      .catch(() => { if (live) setEnabledRoutes(null); });
    return () => { live = false; };
  }, [realMode]);
  // A route is visible when: not real mode, the gate isn't loaded (show-all), or
  // the tenant has the route enabled. Settings/Security are always-on server-side
  // (they come back in enabled_routes), so this needs no special-casing here.
  const routeEnabled = useCallback(
    (r) => !realMode || enabledRoutes === null || enabledRoutes.has(r),
    [realMode, enabledRoutes],
  );
  // The visible nav per section after entitlement gating (so an all-disabled
  // section hides its label too, not just its buttons).
  const navMain = useMemo(() => NAV.filter((n) => n.id !== "sell" || gamifyOn).filter((n) => routeEnabled(n.id)), [NAV, gamifyOn, routeEnabled]);
  const navCrm = useMemo(() => NAV_CRM.filter((n) => routeEnabled(n.id)), [NAV_CRM, routeEnabled]);
  const navAgents = useMemo(() => NAV_AGENTS.filter((n) => routeEnabled(n.id)), [NAV_AGENTS, routeEnabled]);
  const navConnect = useMemo(() => NAV_CONNECT.filter((n) => routeEnabled(n.id)), [NAV_CONNECT, routeEnabled]);
  const navInsights = useMemo(() => NAV2.filter((n) => routeEnabled(n.id)), [NAV2, routeEnabled]);
  const [collapsed, setCollapsed] = useState(false);
  const [cmdk, setCmdk] = useState(false);
  const [deal, setDeal] = useState(null);
  const [chat, setChat] = useState(false);
  const [mnav, setMnav] = useState(false);
  const [market, setMarket] = useState(false);
  const [intake, setIntake] = useState(false);
  const [notif, setNotif] = useState(false);
  const [profile, setProfile] = useState(false);
  const [editProfile, setEditProfile] = useState(false);
  const auth = useAuth();
  const [me, setMe] = useState(() => { try { return JSON.parse(localStorage.getItem("fl_me")) || null; } catch (e) { return null; } });
  // Real identity from the Cognito ID token claims when signed in; the editable
  // mock persona ("Jordan Reyes" / localStorage fl_me) otherwise (mock mode).
  const authMe = auth.isAuthenticated ? {
    name: (auth.claims && (auth.claims.name || auth.claims.given_name)) || (auth.email ? auth.email.split("@")[0] : "Account"),
    title: "Signed in",
    email: auth.email || "",
    status: (me && me.status) || "available",
  } : null;
  const meData = authMe || me || { name: "Jordan Reyes", title: "Owner", email: "jordan@reyesco.com", status: "available" };
  // Never seed fl_me from authMe: claim-derived PII (real name/email) must not
  // persist in localStorage past sign-out. Edits while authenticated only carry
  // prior fl_me content + the patch (e.g. status), not the token claims.
  const saveMe = (patch) => { const next = { ...(me || {}), ...patch }; setMe(next); try { localStorage.setItem("fl_me", JSON.stringify(next)); } catch (e) {} };
  const STATUS = { available: ["Available", "var(--green)"], busy: ["Busy", "var(--rose)"], away: ["Away", "var(--amber)"] };
  const feed = useStore((s) => s.feed);
  const [onb, setOnb] = useState(() => { try { if (/[?&]onboard=1/.test(location.search)) { localStorage.removeItem("fl_onboarded"); localStorage.removeItem("fl_toured"); return true; } return !localStorage.getItem("fl_onboarded"); } catch (e) { return true; } });
  const [tour, setTour] = useState(false);
  const finishOnb = () => { try { localStorage.setItem("fl_onboarded", "1"); } catch (e) {} setOnb(false); try { if (!localStorage.getItem("fl_toured")) setTour(true); } catch (e) { setTour(true); } };
  const finishTour = () => { try { localStorage.setItem("fl_toured", "1"); } catch (e) {} setTour(false); };

  // apply tweaks to :root
  useEffect(() => {
    const root = document.documentElement;
    const acc = ACCENTS.find((a) => a.id === t.accent) || ACCENTS[0];
    root.style.setProperty("--accent-h", acc.h);
    root.setAttribute("data-theme", t.dark ? "dark" : "light");
    root.setAttribute("data-density", t.density);
  }, [t.accent, t.dark, t.density]);

  // ⌘K (mock only: the palette is prototype chrome with fake counts/agents)
  useEffect(() => {
    if (realMode) return;
    const k = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setCmdk((v) => !v); }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, []);

  // agents keep working: stream live activity into the shared feed (MOCK ONLY:
  // FEED_LIVE is scripted prototype activity — fabricating "live" agent events
  // for a real tenant would be dishonest)
  const secMode = useStore((s) => s.security.mode);
  const agentPaused = useStore((s) => s.security.agentPaused);
  useEffect(() => {
    if (realMode) return;
    const iv = setInterval(() => {
      const st = FLStore.getState();
      if (st.security.mode === "paused") return; // master kill switch halts autonomous activity
      const pool = FEED_LIVE.filter((e) => !st.security.agentPaused[e.agent]);
      if (pool.length === 0) return;
      const ev = pool[Math.floor(Math.random() * pool.length)];
      FLStore.pushFeed({ ...ev });
    }, 6000);
    return () => clearInterval(iv);
  }, []);

  const routeMeta = {
    dashboard: { h1: "Command Center", p: "Your agents, your pipeline, your morning at a glance" },
    crm:       { h1: "Uplift", p: "Deals worked autonomously, with you in the loop" },
    contacts:  { h1: "Contacts", p: "Everyone your business talks to" },
    billing:   { h1: "Billing", p: "Invoices, payments and revenue" },
    calendar:  { h1: "Calendar", p: "Bookings and schedules" },
    reviews:   { h1: "Reputation", p: "Reviews and public presence" },
    templates: { h1: "Templates", p: "Reusable messages and documents" },
    email:     { h1: "Email", p: "Campaigns and outreach" },
    sell:      { h1: "Sell", p: "Your streaks, goals, quests and leaderboard" },
    frontline: { h1: "Frontline", p: "Your autonomous support desk" },
    workflows: { h1: "Workflows", p: "Automations your agents run" },
    approvals: { h1: "Greenlight", p: "Every agent action waiting on your sign-off" },
    agents:    { h1: "Agents", p: "Your always-on team" },
    studio:    { h1: "Agent Studio", p: "Compose and run playbooks for your agent crew" },
    marketplace: { h1: "Marketplace", p: "Add agents to your team" },
    knowledge: { h1: "Knowledge", p: "What your agents know" },
    reports:   { h1: "Reports", p: "Performance & outcomes" },
    dashboards: { h1: "Dashboards", p: "Your saved views, composed and pinned" },
    sidecar:   { h1: "Sidecar", p: "Your agents, on top of the tools you already use" },
    cortex:    { h1: "Cortex", p: "Your private, compounding intelligence" },
    integrations: { h1: "Switchboard", p: "Connect the products your business runs on" },
    security:  { h1: "Security", p: "Your guardrails, kill switches & audit trail" },
    settings:  { h1: "Settings", p: "Workspace & team" },
  };
  const meta = routeMeta[route] || { h1: route, p: "" };

  // Sidebar icon for a route id (used by the real-mode ComingSoon panel).
  const navIconFor = (r) => {
    const hit = [...NAV, ...NAV_CRM, ...NAV_AGENTS, ...NAV_CONNECT, ...NAV2].find((n) => n.id === r);
    return hit ? hit.icon : "grid";
  };

  // Marketplace is a prototype overlay (fake agent catalog): in real mode it
  // routes to the honest ComingSoon panel instead.
  const navTo = (r) => { if (r === "marketplace" && !realMode) { setMarket(true); setMnav(false); return; } setRoute(r); setMnav(false); document.querySelector(".viewport") && (document.querySelector(".viewport").scrollTop = 0); };

  return (
    <div className={"app" + (collapsed ? " nav-collapsed" : "") + (mnav ? " nav-open" : "")}>
      <div className="nav-scrim" onClick={() => setMnav(false)} />
      {/* sidebar */}
      <aside className="sidebar">
        <button className="collapse-btn" onClick={() => setCollapsed((c) => !c)}><Icon name="chevL" size={13} sw={2.4} /></button>
        <a className="brand" href="Home.html" style={{ textDecoration: "none", color: "inherit" }} title="Back to home">
          <div className="brand-mark"><Logo size={20} /></div>
          <div className="brand-name"><b>Friesen Labs</b><span>agentic ops</span></div>
        </a>

        <div className="nav-scroll">
        {/* Intake logs into the FLStore prototype — mock mode only. */}
        {!realMode && (
        <button className="intake-nav-btn" onClick={() => setIntake(true)}>
          <span className="intake-nav-plus"><Icon name="plus" size={16} sw={2.6} /></span>
          <span style={{ flex: 1, textAlign: "left" }}>Intake</span>
          <span className="intake-nav-hint">log anything</span>
        </button>
        )}
        {navMain.length > 0 && <div className="nav-section-label">Workspace</div>}
        <nav className="nav">
          {navMain.map((n) => {
            // In real mode the FLStore pending count is prototype data, not the
            // tenant's queue — show no badge rather than a fake number.
            const badge = n.id === "approvals" ? (realMode ? null : pendingCount || null) : realMode ? null : n.badge;
            return (
              <button key={n.id} className={"nav-item" + (route === n.id ? " active" : "")} onClick={() => navTo(n.id)}>
                <span className="nav-ico"><Icon name={n.icon} size={18} /></span>
                <span className="nav-label">{n.label}</span>
                {badge && <span className={"nav-badge" + (n.badgeAmber ? " amber" : "")}>{badge}</span>}
              </button>
            );
          })}
        </nav>

        {navCrm.length > 0 && <div className="nav-section-label">Uplift CRM</div>}
        <nav className="nav">
          {navCrm.map((n) => {
            // Prototype badge counts ("11" deals) are fake — mock mode only.
            const badge = realMode ? null : n.badge;
            return (
              <button key={n.id} className={"nav-item" + (route === n.id ? " active" : "")} onClick={() => navTo(n.id)}>
                <span className="nav-ico"><Icon name={n.icon} size={18} /></span>
                <span className="nav-label">{n.label}</span>
                {badge && <span className={"nav-badge" + (n.badgeAmber ? " amber" : "")}>{badge}</span>}
              </button>
            );
          })}
        </nav>

        {navAgents.length > 0 && <div className="nav-section-label">Agents &amp; intelligence</div>}
        <nav className="nav">
          {navAgents.map((n) => (
            <button key={n.id} className={"nav-item" + (route === n.id ? " active" : "")} onClick={() => navTo(n.id)}>
              <span className="nav-ico"><Icon name={n.icon} size={18} /></span>
              <span className="nav-label">{n.label}</span>
            </button>
          ))}
        </nav>

        {navConnect.length > 0 && <div className="nav-section-label">Connect your stack</div>}
        <nav className="nav">
          {navConnect.map((n) => (
            <button key={n.id} className={"nav-item" + (route === n.id ? " active" : "")} onClick={() => navTo(n.id)}>
              <span className="nav-ico"><Icon name={n.icon} size={18} /></span>
              <span className="nav-label">{n.label}</span>
            </button>
          ))}
        </nav>

        {navInsights.length > 0 && <div className="nav-section-label">Insights &amp; admin</div>}
        <nav className="nav">
          {navInsights.map((n) => (
            <button key={n.id} className={"nav-item" + (route === n.id ? " active" : "")} onClick={() => navTo(n.id)}>
              <span className="nav-ico"><Icon name={n.icon} size={18} /></span>
              <span className="nav-label">{n.label}</span>
            </button>
          ))}
        </nav>
        </div>

        {/* "5 agents online" + the roster are FLStore prototype agents; claiming
            they're online for a real tenant would be false. Mock mode only. */}
        {!realMode && (
        <div className="agent-rail">
          <div className="agent-rail-head"><span className="live-dot" />5 agents online</div>
          <div className="agent-rail-body">
            {Object.values(agents).slice(0, 3).map((a) => (
              <div className="mini-agent" key={a.id}>
                <div className="avatar" style={{ background: a.color }}>{a.init}</div>
                <div className="mini-agent-info"><b>{a.name}</b><span>{a.role}</span></div>
              </div>
            ))}
          </div>
        </div>
        )}
      </aside>

      {/* main */}
      <div className="main">
        <header className="topbar">
          <button className="icon-btn mobile-only" onClick={() => setMnav((v) => !v)}><Icon name="menu" size={20} /></button>
          <div className="topbar-title"><h1>{meta.h1}</h1><p>{meta.p}</p></div>
          <div className="topbar-spacer" />
          {/* XP/gamify reads FLStore prototype stats — mock mode only. */}
          {(!realMode && gamifyOn && (route === "crm" || route === "agents" || route === "sell")) && <XPBadge />}
          {/* The command palette surfaces prototype content (fake pending
              counts, scripted agents) — mock mode only. */}
          {!realMode && (
          <button className="search-trigger" onClick={() => setCmdk(true)}>
            <Icon name="search" size={16} />
            <span>Search or ask…</span>
            <span className="kbd">⌘K</span>
          </button>
          )}
          <button className="btn btn-soft" onClick={() => setChat(true)}><Icon name="spark" size={16} /><span>Ask agents</span></button>
          <div style={{ position: "relative" }}>
            {/* The feed is FLStore prototype activity. In real mode there is no
                notification source yet: no fake unread dot, and the panel says
                so honestly instead of listing scripted events. */}
            <button className="icon-btn" onClick={() => setNotif((v) => !v)}><Icon name="bell" size={19} />{!realMode && feed.length > 0 && <span className="dot" />}</button>
            {notif && (
              <>
                <div style={{ position: "fixed", inset: 0, zIndex: 40 }} onClick={() => setNotif(false)} />
                <div style={{ position: "absolute", top: 46, right: 0, width: 320, maxHeight: 380, overflowY: "auto", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-md)", boxShadow: "var(--shadow-xl)", zIndex: 41, animation: "feed-in .2s both" }}>
                  <div style={{ padding: "12px 15px", borderBottom: "1px solid var(--line)", fontSize: 13, fontWeight: 700 }}>Notifications</div>
                  {realMode || feed.length === 0 ? (
                    <div data-testid="notif-empty" style={{ padding: "18px 15px", fontSize: 12.5, color: "var(--ink-3)" }}>No notifications yet.</div>
                  ) : (
                    feed.slice(0, 8).map((f, i) => (
                      <div key={f._k || i} style={{ display: "flex", gap: 10, padding: "11px 15px", borderBottom: "1px solid var(--line-2)" }}>
                        <span style={{ width: 7, height: 7, borderRadius: 99, background: agents[f.agent] ? agents[f.agent].color : "var(--accent)", marginTop: 5, flexShrink: 0 }} />
                        <div><SafeHtml as="p" style={{ fontSize: 12.5, lineHeight: 1.45 }} html={f.html} /><span style={{ fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>{f.meta}</span></div>
                      </div>
                    ))
                  )}
                </div>
              </>
            )}
          </div>
          <button className="icon-btn" onClick={() => setTweak("dark", !t.dark)}><Icon name={t.dark ? "sun" : "layers"} size={19} /></button>
          <div style={{ position: "relative" }}>
            <button className="user-chip" onClick={() => setProfile((v) => !v)}>
              <div style={{ position: "relative" }}>
                <div className="avatar" style={{ background: "linear-gradient(145deg, var(--accent), var(--accent-press))" }}>{meData.name.split(" ").map((w) => w[0]).slice(0, 2).join("")}</div>
                <span style={{ position: "absolute", right: -1, bottom: -1, width: 11, height: 11, borderRadius: 99, background: STATUS[meData.status][1], border: "2px solid var(--bg)" }} />
              </div>
              <div className="ucol"><b>{meData.name}</b><span>{meData.title}</span></div>
              <Icon name="chevDown" size={15} style={{ color: "var(--ink-3)" }} />
            </button>
            {profile && (
              <>
                <div style={{ position: "fixed", inset: 0, zIndex: 40 }} onClick={() => setProfile(false)} />
                <div style={{ position: "absolute", top: 50, right: 0, width: 270, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r-md)", boxShadow: "var(--shadow-xl)", zIndex: 41, overflow: "hidden", animation: "feed-in .18s both" }}>
                  <div style={{ padding: "14px 16px", borderBottom: "1px solid var(--line)", display: "flex", alignItems: "center", gap: 11 }}>
                    <div className="avatar" style={{ background: "linear-gradient(145deg, var(--accent), var(--accent-press))", width: 40, height: 40, fontSize: 14 }}>{meData.name.split(" ").map((w) => w[0]).slice(0, 2).join("")}</div>
                    <div style={{ minWidth: 0, flex: 1 }}><b style={{ fontSize: 14, fontWeight: 700, display: "block" }}>{meData.name}</b><span style={{ fontSize: 12, color: "var(--ink-3)" }}>{meData.email}</span></div>
                    <button className="icon-btn" style={{ width: 28, height: 28 }} title="Edit profile" onClick={() => { setEditProfile(true); setProfile(false); }}><Icon name="note" size={15} /></button>
                  </div>
                  <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--line)" }}>
                    <div style={{ fontSize: 10.5, fontWeight: 650, textTransform: "uppercase", letterSpacing: ".05em", color: "var(--ink-4)", marginBottom: 7 }}>Status</div>
                    <div className="seg" style={{ width: "100%" }}>
                      {Object.entries(STATUS).map(([k, [label, col]]) => (
                        <button key={k} className={meData.status === k ? "active" : ""} style={{ flex: 1, justifyContent: "center", gap: 5 }} onClick={() => saveMe({ status: k })}><span style={{ width: 7, height: 7, borderRadius: 99, background: col }} />{label}</button>
                      ))}
                    </div>
                  </div>
                  <div style={{ padding: 6 }}>
                    {[["building", "Workspace settings", "settings"], ["users", "Manage team", "settings"], ["shield", "Security", "security"], ["trend", "Plan & billing", "settings"]].map(([ic, label, r]) => (
                      <button key={label} className="pm-item" onClick={() => { navTo(r); setProfile(false); }}>
                        <Icon name={ic} size={16} style={{ color: "var(--ink-3)" }} /><span>{label}</span>
                      </button>
                    ))}
                    <button className="pm-item" onClick={() => { setProfile(false); setCmdk(true); }}>
                      <Icon name="search" size={16} style={{ color: "var(--ink-3)" }} /><span style={{ flex: 1 }}>Command palette</span><span className="kbd">⌘K</span>
                    </button>
                    <div className="pm-row">
                      <Icon name="sun" size={16} style={{ color: "var(--ink-3)" }} /><span style={{ flex: 1 }}>Dark mode</span>
                      <div className={"tog" + (t.dark ? " on" : "")} onClick={() => setTweak("dark", !t.dark)} />
                    </div>
                  </div>
                  <div style={{ borderTop: "1px solid var(--line)", padding: 6 }}>
                    {auth.isAuthenticated && (
                      // Routes through Cognito's managed /changePassword (old +
                      // new password, validated against the live Hosted-UI
                      // session). The raw passwords never reach our app or DB.
                      <a className="pm-item pm-change-pw" href="#" onClick={(e) => { e.preventDefault(); auth.changePassword(); }} style={{ textDecoration: "none" }}><Icon name="lock" size={16} /><span>Change password</span></a>
                    )}
                    <a className="pm-item" href="Home.html" onClick={(e) => { if (auth.isAuthenticated) { e.preventDefault(); try { localStorage.removeItem("fl_me"); } catch (err) {} auth.signOut(); } }} style={{ color: "var(--rose)", textDecoration: "none" }}><Icon name="arrowRight" size={16} /><span>Sign out</span></a>
                  </div>
                </div>
              </>
            )}
          </div>
        </header>

        {/* Security banner reflects FLStore prototype kill-switch state — mock only. */}
        {!realMode && (secMode === "paused" || secMode === "semi" || Object.values(agentPaused).some(Boolean)) && (
          <div onClick={() => navTo("security")} style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 16px", cursor: "pointer",
            background: secMode === "paused" ? "var(--rose-soft)" : "var(--amber-soft)",
            color: secMode === "paused" ? "oklch(0.48 0.14 18)" : "oklch(0.5 0.12 60)",
            borderBottom: "1px solid var(--line)", fontSize: 12.5, fontWeight: 600 }}>
            <Icon name={secMode === "paused" ? "pause" : "shield"} size={15} />
            {secMode === "paused" ? "Kill switch engaged, all agents are stopped." : secMode === "semi" ? "Agents are in analyze-only mode, nothing runs without your approval." : "Some agents are paused."}
            <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 4, opacity: .8 }}>Manage in Security<Icon name="arrowRight" size={13} sw={2.2} /></span>
          </div>
        )}

        <div className="viewport">
          {realMode && !routeEnabled(route) ? (
            // Entitlement gate: the tenant navigated (e.g. via a deep button) to a
            // surface whose module they've disabled in Settings → "Your suite".
            // Show the honest "not in your suite" panel with a path to enable it.
            <ModuleNotEnabled title={meta.h1} icon={navIconFor(route)} onManage={() => navTo("settings")} />
          ) : realMode ? (
            // REAL MODE: only ApiClient-backed surfaces render data. Every
            // other route gets the honest ComingSoon panel — never an FLStore
            // prototype screen, which would pass demo numbers off as real.
            <>
              {/* First-run checklist (dismissible, never blocks the app): shows
                  only while this tenant's onboarding_state is incomplete. Its
                  "Load sample data" step loads the demo fixture into the tenant
                  and remounts the surfaces (sampleReloadKey) so populated views
                  surface immediately. */}
              <FirstRunChecklist onNavigate={navTo} onOpenChat={() => setChat(true)} />
              {route === "dashboard" && <DashboardView />}
              {/* Pipeline is LIVE in real mode: RLS-scoped deals from GET /deals;
                  stage moves queue through Greenlight (never a direct write). */}
              {route === "crm" && <PipelineBoard key={sampleReloadKey} onOpenGreenlight={() => navTo("approvals")} onLoadSample={loadSampleData} />}
              {/* Contacts is LIVE in real mode: RLS-scoped directory from
                  GET /contacts + /companies, read-only; open deals link to
                  the Pipeline board. */}
              {route === "contacts" && <ContactsDirectory key={sampleReloadKey} onOpenPipeline={() => navTo("crm")} onLoadSample={loadSampleData} />}
              {/* Agents is LIVE in real mode: the tenant's crew from GET /agents
                  (owned roster + trusted tool policies + truncated provisioned
                  ids) — never the FLStore prototype console. */}
              {route === "agents" && <AgentsRoster onOpenGreenlight={() => navTo("approvals")} />}
              {/* Studio is LIVE in real mode: the playbook composer + starter
                  library over /studio/* (RLS-scoped CRUD, server-side schema
                  validation, activation behind the existing Greenlight gates)
                  — never the FLStore AgentStudio modal prototype. */}
              {route === "studio" && <StudioView />}
              {/* Workflows is LIVE in real mode: the provisioning machine made
                  visible from GET /workflows (the OWNED 5-step diagram + recent
                  executions, read-only) — never the FLStore drag-and-drop
                  builder prototype. */}
              {route === "workflows" && <WorkflowsView onOpenGreenlight={() => navTo("approvals")} />}
              {/* Reports is LIVE in real mode: the saved-views gallery from
                  GET /views, each rendered through the trusted dashboard
                  SpecRenderer; "ask for a chart" rides the existing /views/{id}
                  /refine NL route (honest "not live yet" until the agent runtime
                  is wired) — never the FLStore Reports prototype + DataAssistant
                  overlay. */}
              {route === "reports" && <ReportsView />}
              {/* Dashboards is LIVE in real mode: named compositions of saved
                  views over GET/POST /dashboards (kind=dashboard rows), each
                  referenced view rendered through the SAME trusted SpecRenderer
                  — never executable code, never FLStore prototype numbers. */}
              {route === "dashboards" && <DashboardsView />}
              {/* Knowledge is LIVE in real mode: the tenant's ingested corpus from
                  GET /knowledge (per-source inventory) + /knowledge/search (RLS
                  cosine search, honest degrade while the embedder warms up) —
                  never the FLStore Knowledge prototype. */}
              {route === "knowledge" && <KnowledgeView />}
              {route === "approvals" && <GreenlightQueue />}
              {route === "integrations" && <IntegrationsPanel />}
              {/* Security is LIVE in real mode: the kill switch + autonomy dial
                  PUT real state through GET/PUT /control/*, and a read-only
                  decision-trace feed renders GET /control/traces — each control
                  feature-detects a 404 and degrades to a disabled "not yet
                  enabled" state rather than a fake working toggle. */}
              {route === "security" && <SecurityControls />}
              {/* Settings is LIVE for self-service billing in real mode: the
                  Plan & billing panel reads GET /billing and "Manage billing"
                  redirects to the Stripe-hosted Customer Portal (change card,
                  cancel, view invoices). It feature-detects a 404 and degrades
                  to an honest "not yet available" state. */}
              {/* Cortex is LIVE in real mode: the tenant's model health from
                  GET /cortex/health — champion (version/estimator/metrics) +
                  the live-AUC drift verdict, with honest "no_registry"/
                  "no_champion" degraded states (NEVER fabricated accuracy/
                  drift numbers) — never the FLStore Cortex prototype. */}
              {route === "cortex" && <CortexView />}
              {/* Marketplace is LIVE in real mode: browse the committed starter
                  "ready-made agents" (GET /studio/templates) and add one to your
                  library as a draft playbook (POST /studio/templates/{id}/
                  instantiate) — the honest counterpart of the FLStore agent-market
                  demo. Nothing runs until you activate it in Studio. */}
              {route === "marketplace" && <MarketplaceView onOpenStudio={() => navTo("studio")} />}
              {/* Settings is LIVE for workspace, billing + account data in real
                  mode: Workspace name + notification prefs PERSIST via GET/PUT
                  /account/settings; the Plan & billing panel reads GET /billing
                  (+ invoices) and "Manage billing" redirects to the Stripe Customer
                  Portal; the Account & data section exports (GET /account/export)
                  and can request teardown (POST /account/delete, confirm-gated +
                  honest 503 when the destructive path isn't live-wired). Each panel
                  feature-detects a 404/503 and degrades honestly. */}
              {route === "settings" && (
                <div className="screen-anim" style={{ maxWidth: 720 }}>
                  <div className="ad-sec-label" style={{ marginBottom: 14 }}>Workspace</div>
                  <WorkspaceSettings />
                  {/* Your suite — toggle modules on/off; the workspace shows only
                      what's enabled, and the monthly total reflects the selection.
                      onChange re-gates the app's nav/routes immediately. */}
                  <div className="ad-sec-label" style={{ margin: "28px 0 14px" }}>Your suite</div>
                  <ModulesView onChange={(cat) => setEnabledRoutes(new Set(cat.enabled_routes))} />
                  <div className="ad-sec-label" style={{ margin: "28px 0 14px" }}>Plan &amp; billing</div>
                  <BillingManage />
                  <div className="ad-sec-label" style={{ margin: "28px 0 14px" }}>Account &amp; data</div>
                  <AccountDataControls />
                </div>
              )}
              {route !== "dashboard" && route !== "crm" && route !== "contacts" && route !== "agents" && route !== "studio" && route !== "workflows" && route !== "reports" && route !== "dashboards" && route !== "knowledge" && route !== "approvals" && route !== "integrations" && route !== "security" && route !== "settings" && route !== "cortex" && route !== "marketplace" && (
                <ComingSoon title={meta.h1} icon={navIconFor(route)} />
              )}
            </>
          ) : (
            // MOCK MODE: the full FLStore prototype experience.
            <>
              {route === "dashboard" && <Dashboard agents={agents} onNavigate={navTo} />}
              {route === "crm" && <CRM agents={agents} onOpen={setDeal} onNavigate={navTo} />}
              {route === "contacts" && <Contacts agents={agents} onNavigate={navTo} onOpenDeal={setDeal} />}
              {route === "billing" && <Billing agents={agents} onNavigate={navTo} />}
              {route === "calendar" && <Calendar agents={agents} onNavigate={navTo} />}
              {route === "reviews" && <Reviews agents={agents} onNavigate={navTo} />}
              {route === "templates" && <Templates agents={agents} onNavigate={navTo} />}
              {route === "email" && <Email agents={agents} onNavigate={navTo} />}
              {route === "sell" && <Sell agents={agents} gamifyOn={gamifyOn} onNavigate={navTo} onOpenDeal={(d) => { navTo("crm"); setDeal(d); }} />}
              {route === "frontline" && <Frontline agents={agents} />}
              {route === "workflows" && <WorkflowBuilder agents={agents} />}
              {route === "approvals" && <Greenlight agents={agents} />}
              {route === "agents" && <AgentsConsole agents={agents} />}
              {/* The mock build runs offline: StudioView renders its honest
                  "connects to your live workspace" card (never a fake library). */}
              {route === "studio" && <StudioView />}
              {route === "sidecar" && <Sidecar agents={agents} onNavigate={navTo} />}
              {route === "cortex" && <Cortex agents={agents} onNavigate={navTo} />}
              {route === "knowledge" && <Knowledge agents={agents} onNavigate={navTo} />}
              {route === "integrations" && <IntegrationHub agents={agents} onNavigate={navTo} />}
              {route === "reports" && <Reports agents={agents} />}
              {/* Dashboards has no FLStore prototype: the API-wired screen runs
                  against the offline mock client here (like StudioView), so the
                  nav entry never dead-ends in either mode. */}
              {route === "dashboards" && <DashboardsView />}
              {route === "security" && <Security agents={agents} />}
              {route === "settings" && <Settings agents={agents} onNavigate={navTo} />}
            </>
          )}
        </div>
      </div>

      <SlideOver deal={deal} agents={agents} stages={STAGES} onClose={() => setDeal(null)} />
      {realMode
        ? <RealChatPanel open={chat} onClose={() => setChat(false)} />
        : <AgentChat open={chat} agents={agents} onClose={() => setChat(false)} />}
      {/* Prototype overlays (palette, onboarding, tour, marketplace, intake)
          present scripted FLStore content — mock mode only. */}
      {!realMode && <CommandPalette open={cmdk} onClose={() => setCmdk(false)} onNavigate={navTo} onChat={() => setChat(true)} onSetup={() => setOnb(true)} onTour={() => setTour(true)} />}
      {!realMode && onb && <Onboarding agents={agents} onDone={finishOnb} />}
      {!realMode && tour && !onb && <ProductTour onNavigate={navTo} onClose={finishTour} />}
      {!realMode && <AgentMarket open={market} onClose={() => setMarket(false)} />}
      {!realMode && <IntakeModal open={intake} onClose={() => setIntake(false)} onNavigate={navTo} onOpenDeal={(d) => setDeal(d)} />}
      {editProfile && (
        <div className="cmdk-scrim show" onClick={() => setEditProfile(false)} style={{ alignItems: "center", paddingTop: 0 }}>
          <div className="cmdk" style={{ maxWidth: 420 }} onClick={(e) => e.stopPropagation()}>
            <div style={{ padding: "18px 20px", borderBottom: "1px solid var(--line)", display: "flex", alignItems: "center", gap: 11 }}>
              <div className="avatar" style={{ background: "linear-gradient(145deg, var(--accent), var(--accent-press))", width: 36, height: 36, fontSize: 13 }}>{meData.name.split(" ").map((w) => w[0]).slice(0, 2).join("")}</div>
              <b style={{ fontSize: 16, fontWeight: 720, flex: 1 }}>Edit your profile</b>
              <button className="icon-btn" onClick={() => setEditProfile(false)}><Icon name="x" size={18} /></button>
            </div>
            <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 13 }}>
              <div className="wf-field"><label>Name</label><input value={meData.name} onChange={(e) => saveMe({ name: e.target.value })} /></div>
              <div className="wf-field"><label>Title</label><input value={meData.title} onChange={(e) => saveMe({ title: e.target.value })} /></div>
              <div className="wf-field"><label>Email</label><input value={meData.email} onChange={(e) => saveMe({ email: e.target.value })} /></div>
              <button className="btn btn-primary" onClick={() => setEditProfile(false)}><Icon name="check" size={16} sw={2.2} />Done</button>
              <p style={{ fontSize: 11.5, color: "var(--ink-4)", textAlign: "center" }}>Saved to this workspace.</p>
            </div>
          </div>
        </div>
      )}
      {/* DataAssistant is a prototype overlay over FLStore data — mock only. */}
      {route === "reports" && !realMode && <DataAssistant surface="reports" onNavigate={navTo} />}
      {route === "dashboard" && !realMode && <DataAssistant surface="dashboard" onNavigate={navTo} />}

      {/* tweaks */}
      <TweaksPanel>
        <TweakSection label="Brand accent" />
        <div style={{ display: "flex", gap: 9, padding: "2px 2px 6px" }}>
          {ACCENTS.map((a) => (
            <button key={a.id} title={a.name} onClick={() => setTweak("accent", a.id)}
              style={{ width: 30, height: 30, borderRadius: 9, background: `oklch(0.56 0.17 ${a.h})`,
                boxShadow: t.accent === a.id ? "0 0 0 2px var(--surface), 0 0 0 4px oklch(0.56 0.17 " + a.h + ")" : "var(--shadow-sm)",
                transition: "box-shadow .15s" }} />
          ))}
        </div>
        <TweakSection label="Appearance" />
        <TweakToggle label="Dark mode" value={t.dark} onChange={(v) => setTweak("dark", v)} />
        <TweakRadio label="Density" value={t.density} options={["compact", "regular", "comfy"]} onChange={(v) => setTweak("density", v)} />
      </TweaksPanel>
    </div>
  );
}



export default App;
