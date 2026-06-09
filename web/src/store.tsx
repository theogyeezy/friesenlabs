// @ts-nocheck
import React from "react";
// store.jsx, shared store: agents, deals, feed, greenlight, workflows, points
const FLStore = (() => {
  const D = window.FL_DATA;
  let state = {
    agents: JSON.parse(JSON.stringify(D.AGENTS)),
    deals: D.DEALS.map((d) => ({ ...d })),
    feed: D.FEED_SEED.map((f, i) => ({ ...f, _k: "seed" + i })),
    greenlight: D.GREENLIGHT_SEED.map((x) => ({ ...x, draft: x.body })),
    workflows: [{ id: "wf-1", name: "New website lead", steps: D.WORKFLOW_SEED, active: true, runs: [{ when: "Today, 9:14am", result: "1 lead processed, sent to Greenlight", ok: true, dur: "4.2s" }, { when: "Today, 7:30am", result: "Enrichment API timed out at step 2", ok: false, dur: "1.1s" }, { when: "Yesterday, 4:02pm", result: "1 lead processed end-to-end", ok: true, dur: "3.9s" }, { when: "Yesterday, 11:20am", result: "1 lead processed end-to-end", ok: true, dur: "4.0s" }], branches: { Yes: 18, No: 6 } }],
    activeWorkflowId: "wf-1",
    skills: (D.SKILL_MARKET || []).map((s) => ({ ...s, installed: !!s.builtin })),
    knowledgeBases: (D.KB_SEED || []).map((k) => ({ ...k })),
    brainAnswers: (() => { try { return JSON.parse(localStorage.getItem("fl_brainAnswers")) || {}; } catch (e) { return {}; } })(),
    recall: (() => { try { return JSON.parse(localStorage.getItem("fl_recall")) || null; } catch (e) { return null; } })() || { added: false, indexed: false, sources: [] },
    memories: (() => { try { return JSON.parse(localStorage.getItem("fl_memories")) || null; } catch (e) { return null; } })() || (D.MEMORY_SEED ? D.MEMORY_SEED.map((m) => ({ ...m })) : []),
    quotes: (D.BILLING_SEED ? D.BILLING_SEED.quotes : []).map((q) => ({ ...q })),
    invoices: (D.BILLING_SEED ? D.BILLING_SEED.invoices : []).map((i) => ({ ...i })),
    campaigns: (D.EMAIL_SEED ? D.EMAIL_SEED.campaigns : []).map((c) => ({ ...c })),
    meetings: (D.BILLING_SEED ? D.BILLING_SEED.meetings : []).map((m) => ({ ...m })),
    reviews: (D.BILLING_SEED ? D.BILLING_SEED.reviews : []).map((r) => ({ ...r })),
    referrals: (D.BILLING_SEED ? D.BILLING_SEED.referrals : []).map((r) => ({ ...r })),
    emailTemplates: (D.BILLING_SEED ? D.BILLING_SEED.templates : []).map((t) => ({ ...t })),
    sequences: (D.BILLING_SEED ? D.BILLING_SEED.sequences : []).map((s) => ({ ...s })),
    points: 1240,
    lastAward: null,
    gamifyOn: true,
    dashRange: (() => { try { return localStorage.getItem("fl_dashRange") || "7d"; } catch (e) { return "7d"; } })(),
    productFlags: (() => { try { return JSON.parse(localStorage.getItem("fl_productFlags")) || {}; } catch (e) { return {}; } })(),
    salesGoals: (() => { try { return JSON.parse(localStorage.getItem("fl_salesGoals")) || null; } catch (e) { return null; } })() || { revenue: 50000, calls: 20, closeRate: 35, meetings: 12 },
    dashViews: (() => { try { return JSON.parse(localStorage.getItem("fl_dashViews")) || null; } catch (e) { return null; } })() || [
      { id: "overview", name: "Full overview", builtin: true, range: "7d", panels: { posture: true, reps: true, support: true }, repFilter: "all" },
      { id: "sales", name: "Sales focus", builtin: true, range: "7d", panels: { posture: false, reps: true, support: false }, repFilter: "people" },
      { id: "ops", name: "Operations", builtin: true, range: "7d", panels: { posture: true, reps: false, support: true }, repFilter: "all" },
      { id: "exec", name: "Executive", builtin: true, range: "yoy", panels: { posture: false, reps: true, support: true }, repFilter: "all" },
    ],
    activeDashView: (() => { try { return localStorage.getItem("fl_activeDashView") || "overview"; } catch (e) { return "overview"; } })(),
    dashRepFilter: "all",
    stats: { tasksHandled: 342, hoursSaved: 47 },
    gamify: {
      streak: 4,
      goalTarget: 12,
      goalDone: 7,
      multiplier: 2,
      powerHourEndsAt: 0,
      quests: [
        { id: "q1", label: "Send 5 follow-ups", kind: "followup", goal: 5, progress: 2, reward: 50, done: false },
        { id: "q2", label: "Close a deal today", kind: "win", goal: 1, progress: 0, reward: 100, done: false },
        { id: "q3", label: "Clear 3 approvals", kind: "approve", goal: 3, progress: 1, reward: 40, done: false },
        { id: "q4", label: "Add 2 fresh deals", kind: "deal", goal: 2, progress: 0, reward: 30, done: false },
      ],
    },
    market: D.AGENT_MARKET.map((m) => ({ ...m })),
    team: D.TEAM.map((m) => ({ ...m })),
    tickets: D.TICKETS.map((t) => ({ ...t })),
    security: {
      mode: "auto", // auto | semi | paused
      agentPaused: {}, // agentId -> true when individually paused
      guardrails: {
        approveOverCap: true, businessHours: false, noBulk: true, piiRedact: true, twoPerson: false, blockExternalShare: true,
      },
      spendCap: 1000,
      access: { twoFA: true, sso: false, sessionTimeout: true, ipAllowlist: false },
      roles: JSON.parse(JSON.stringify(D.ROLE_DEFAULTS)),
      rateLimits: { emailsPerHour: 50, actionsPerHour: 120, enabled: true },
      sessions: D.SESSIONS.map((s) => ({ ...s })),
      anomalies: D.ANOMALIES.map((a) => ({ ...a })),
      anomalyWatch: true,
      autoPause: true,
      retentionDays: 365,
    },
  };
  const listeners = new Set();
  const emit = () => listeners.forEach((l) => l());
  const cap = (arr, n) => arr.slice(0, n);
  const money = (n) => "$" + n.toLocaleString();

  const SALES_KINDS = ["followup", "win", "approve", "deal", "ticket"];
  const award = (n, meta) => {
    const g0 = state.gamify;
    const mult = (g0.powerHourEndsAt && Date.now() < g0.powerHourEndsAt) ? g0.multiplier : 1;
    let gained = n * mult;
    let g = g0;
    let celebrate = null;
    if (meta && meta.kind && SALES_KINDS.includes(meta.kind)) {
      let quests = g0.quests.map((q) => (q.kind === meta.kind && !q.done) ? { ...q, progress: Math.min(q.goal, q.progress + 1) } : q);
      quests = quests.map((q) => {
        if (!q.done && q.progress >= q.goal) { gained += q.reward; celebrate = q.label; return { ...q, done: true }; }
        return q;
      });
      g = { ...g0, goalDone: g0.goalDone + 1, quests };
    }
    state = { ...state, points: state.points + gained, lastAward: { n: gained, meta: meta || null, mult, celebrate, k: Date.now() + Math.random() }, gamify: g };
  };
  const feedPush = (ev) => { state = { ...state, feed: cap([{ ...ev, _k: Date.now() + Math.random() }, ...state.feed], 8) }; };

  return {
    getState: () => state,
    subscribe: (l) => { listeners.add(l); return () => listeners.delete(l); },
    pushFeed: (ev) => { feedPush(ev); emit(); },
    addPoints: (n, meta) => { award(n, meta); emit(); },
    setGamifyOn: (val) => { state = { ...state, gamifyOn: val }; emit(); },
    setDashRange: (id) => { try { localStorage.setItem("fl_dashRange", id); } catch (e) {} state = { ...state, dashRange: id }; emit(); },
    setSalesGoal: (key, val) => { const salesGoals = { ...state.salesGoals, [key]: val }; try { localStorage.setItem("fl_salesGoals", JSON.stringify(salesGoals)); } catch (e) {} state = { ...state, salesGoals }; emit(); },
    setDashRepFilter: (f) => { state = { ...state, dashRepFilter: f }; emit(); },
    setProductFlag: (key, val) => { const productFlags = { ...state.productFlags, [key]: val }; try { localStorage.setItem("fl_productFlags", JSON.stringify(productFlags)); } catch (e) {} state = { ...state, productFlags }; emit(); },
    previewDashView: (spec) => {
      const productFlags = { ...state.productFlags, "dashboard.posture": spec.panels.posture, "dashboard.reps": spec.panels.reps, "dashboard.support": spec.panels.support };
      try { localStorage.setItem("fl_productFlags", JSON.stringify(productFlags)); localStorage.setItem("fl_dashRange", spec.range); } catch (e) {}
      state = { ...state, productFlags, dashRange: spec.range, dashRepFilter: spec.repFilter, activeDashView: "custom" }; emit();
    },
    applyDashView: (id) => {
      const v = state.dashViews.find((x) => x.id === id); if (!v) return;
      const productFlags = { ...state.productFlags, "dashboard.posture": v.panels.posture, "dashboard.reps": v.panels.reps, "dashboard.support": v.panels.support };
      try { localStorage.setItem("fl_productFlags", JSON.stringify(productFlags)); localStorage.setItem("fl_dashRange", v.range); localStorage.setItem("fl_activeDashView", id); } catch (e) {}
      state = { ...state, productFlags, dashRange: v.range, dashRepFilter: v.repFilter, activeDashView: id }; emit();
    },
    saveDashView: (name, cfg) => {
      const id = "dv-" + Date.now();
      const v = { id, name, range: cfg.range, panels: cfg.panels, repFilter: cfg.repFilter };
      const dashViews = [...state.dashViews, v];
      try { localStorage.setItem("fl_dashViews", JSON.stringify(dashViews)); localStorage.setItem("fl_activeDashView", id); } catch (e) {}
      state = { ...state, dashViews, activeDashView: id }; emit(); return id;
    },
    deleteDashView: (id) => {
      const dashViews = state.dashViews.filter((x) => x.id !== id);
      try { localStorage.setItem("fl_dashViews", JSON.stringify(dashViews)); } catch (e) {}
      state = { ...state, dashViews, activeDashView: state.activeDashView === id ? "overview" : state.activeDashView }; emit();
    },

    editDraft: (id, val) => {
      state = { ...state, greenlight: state.greenlight.map((i) => i.id === id ? { ...i, draft: val, edited: val !== i.body } : i) };
      emit();
    },
    resolveGreenlight: (ids, decision) => {
      const resolved = state.greenlight.filter((i) => ids.includes(i.id) && i.status === "pending");
      if (resolved.length === 0) { emit(); return; }
      let feed = state.feed;
      if (decision === "approved") {
        resolved.forEach((r) => { feed = [{ agent: r.agent, ico: "checkCircle", tone: "green", html: `Executed: <b>${r.title}</b>`, meta: "just now · approved by you", _k: Date.now() + Math.random() }, ...feed]; });
        feed = cap(feed, 8);
      }
      state = { ...state, feed, greenlight: state.greenlight.map((i) => ids.includes(i.id) ? { ...i, status: decision, resolvedAgo: "just now" } : i),
        stats: { ...state.stats, tasksHandled: state.stats.tasksHandled + (decision === "approved" ? resolved.length : 0) } };
      if (decision === "approved") award(15 * resolved.length, { kind: "approve" });
      emit();
    },
    addGreenlight: (item) => {
      if (state.greenlight.some((i) => i.id === item.id)) return false;
      state = { ...state, greenlight: [{ ...item, draft: item.body, status: "pending", edited: false }, ...state.greenlight] };
      emit();
      return true;
    },

    // ---- deals ----
    moveDeal: (id, stage) => {
      const deal = state.deals.find((d) => d.id === id);
      const wasWon = deal && deal.stage === "won";
      state = { ...state, deals: state.deals.map((d) => d.id === id ? { ...d, stage } : d) };
      const newWin = deal && stage === "won" && !wasWon;
      if (newWin) {
        feedPush({ agent: deal.agent, ico: "checkCircle", tone: "green", html: `🎉 Won <b>${deal.co}</b> · ${money(deal.value)}`, meta: "just now" });
        award(120, { kind: "win", label: deal.co });
        state = { ...state, stats: { ...state.stats, tasksHandled: state.stats.tasksHandled + 1 } };
      }
      emit();
      return newWin;
    },
    addDeal: (deal) => {
      const id = Date.now();
      const colors = ["oklch(0.56 0.17 277)", "oklch(0.62 0.15 18)", "oklch(0.62 0.13 152)", "oklch(0.66 0.12 235)", "oklch(0.66 0.14 50)"];
      const d = { id, stage: "lead", heat: "warm", days: 0, agent: deal.agent || "scout", human: null, agentNote: "Just added · Scout is enriching…", coColor: colors[id % colors.length], init: (deal.co || "ND").slice(0, 2).toUpperCase(), person: deal.person || "New contact", email: "", phone: "", value: deal.value || 5000, co: deal.co || "New deal" };
      state = { ...state, deals: [d, ...state.deals] };
      feedPush({ agent: "scout", ico: "spark", tone: "indigo", html: `New deal added: <b>${d.co}</b>, enriching now`, meta: "just now" });
      award(10, { kind: "deal" });
      emit();
      return id;
    },
    assignDeal: (id, patch) => {
      state = { ...state, deals: state.deals.map((d) => d.id === id ? { ...d, ...patch } : d) };
      emit();
    },
    addDealTask: (id, title) => {
      state = { ...state, deals: state.deals.map((d) => d.id === id ? { ...d, tasks: [...(d.tasks || []), { id: "t" + Date.now(), title, due: "today", done: false }] } : d) };
      emit();
    },
    toggleDealTask: (id, taskId) => {
      let completed = false;
      state = { ...state, deals: state.deals.map((d) => d.id === id ? { ...d, tasks: (d.tasks || []).map((t) => { if (t.id === taskId) { completed = !t.done; return { ...t, done: !t.done }; } return t; }) } : d) };
      if (completed) award(6, { kind: "task" });
      emit();
    },
    logDealActivity: (id, txt, ico, tone) => {
      state = { ...state, deals: state.deals.map((d) => d.id === id ? { ...d, timeline: [{ ico: ico || "note", tone: tone || "indigo", t: "just now", txt }, ...(d.timeline || [])] } : d) };
      emit();
    },
    markDealLost: (id, reason) => {
      const deal = state.deals.find((d) => d.id === id);
      state = { ...state, deals: state.deals.map((d) => d.id === id ? { ...d, stage: "lost", lostReason: reason, closeDate: "lost", timeline: [{ ico: "x", tone: "rose", t: "just now", txt: "Marked lost · " + reason }, ...(d.timeline || [])] } : d) };
      if (deal) feedPush({ agent: deal.agent || "scout", ico: "x", tone: "rose", html: `Marked <b>${deal.co}</b> as lost · ${reason}`, meta: "just now" });
      emit();
    },

    updateAgent: (id, patch) => { state = { ...state, agents: { ...state.agents, [id]: { ...state.agents[id], ...patch } } }; emit(); },
    removeAgent: (id) => { const next = { ...state.agents }; delete next[id]; state = { ...state, agents: next }; emit(); },
    addAgent: (agent) => { const id = agent.id || ("ag-" + Date.now()); state = { ...state, agents: { ...state.agents, [id]: { ...agent, id } } }; emit(); return id; },
    addSkill: (skill) => { const id = "sk-u" + Date.now(); state = { ...state, skills: [{ ...skill, id, author: "You", installs: "new", rating: 5, installed: true }, ...state.skills] }; emit(); return id; },
    addKB: ({ name, icon, tone }) => { const id = "kb-" + Date.now(); state = { ...state, knowledgeBases: [...state.knowledgeBases, { id, name, icon: icon || "doc", tone: tone || "indigo", status: "ready", embModel: "fl-embed-v2", topK: 6, visibility: "private", updated: "just now", agents: [], sources: [] }] }; emit(); return id; },
    sendQuote: (id) => { state = { ...state, quotes: state.quotes.map((q) => q.id === id ? { ...q, status: "sent" } : q) }; const q = state.quotes.find((x) => x.id === id); if (q) feedPush({ agent: "margo", ico: "doc", tone: "amber", html: `Sent quote <b>${id}</b> to ${q.co} for e-signature`, meta: "just now · Billing" }); emit(); },
    convertQuote: (id) => {
      const q = state.quotes.find((x) => x.id === id); if (!q) return;
      const invId = "INV-" + (2050 + state.invoices.length);
      state = { ...state, quotes: state.quotes.map((x) => x.id === id ? { ...x, status: "signed" } : x), invoices: [{ id: invId, co: q.co, init: q.init, color: q.color, amount: q.amount, status: "due", due: "due in 14 days" }, ...state.invoices] };
      feedPush({ agent: "ledger", ico: "trend", tone: "green", html: `Converted ${id} → invoice <b>${invId}</b> for ${q.co}`, meta: "just now · Billing" }); award(15, { kind: "invoice" }); emit(); return invId;
    },
    markInvoicePaid: (id) => { const inv = state.invoices.find((x) => x.id === id); state = { ...state, invoices: state.invoices.map((x) => x.id === id ? { ...x, status: "paid", due: "paid just now" } : x) }; if (inv) { feedPush({ agent: "ledger", ico: "checkCircle", tone: "green", html: `Payment received on <b>${id}</b> · ${"$" + inv.amount.toLocaleString()} from ${inv.co}`, meta: "just now · Billing" }); award(25, { kind: "payment", celebrate: true }); } emit(); },
    sendCampaign: (camp) => {
      const id = "ec-" + Date.now();
      const n = camp.count || 0;
      const delivered = Math.round(n * 0.975);
      const opens = Math.round(delivered * (0.42 + Math.random() * 0.16));
      const clicks = Math.round(opens * (0.22 + Math.random() * 0.12));
      const replies = Math.round(clicks * (0.3 + Math.random() * 0.2));
      const c = { id, name: camp.name, status: camp.schedule ? "scheduled" : "sent", sent: camp.schedule ? 0 : n, delivered: camp.schedule ? 0 : delivered, opens: camp.schedule ? 0 : opens, clicks: camp.schedule ? 0 : clicks, replies: camp.schedule ? 0 : replies, when: camp.schedule ? "scheduled" : "just now", segment: camp.segment };
      state = { ...state, campaigns: [c, ...state.campaigns] };
      feedPush({ agent: "echo", ico: "mail", tone: "indigo", html: camp.schedule ? `Scheduled <b>${camp.name}</b> to ${n} contacts` : `Sent <b>${camp.name}</b> to ${n} contacts · ${opens} opens so far`, meta: "just now · Email" });
      if (!camp.schedule) award(10, { kind: "campaign" });
      emit(); return id;
    },
    addMeeting: (m) => { const id = "m" + Date.now(); state = { ...state, meetings: [...state.meetings, { ...m, id }] }; feedPush({ agent: m.agent || "echo", ico: "calendar", tone: "indigo", html: `Booked <b>${m.type}</b> with ${m.co} · ${m.when} ${m.time}`, meta: "just now · Calendar" }); award(8, { kind: "meeting" }); emit(); return id; },
    cancelMeeting: (id) => { state = { ...state, meetings: state.meetings.filter((m) => m.id !== id) }; emit(); },
    requestReview: (id) => { const r = state.reviews.find((x) => x.id === id); state = { ...state, reviews: state.reviews.map((x) => x.id === id ? { ...x, status: "requested" } : x) }; if (r) feedPush({ agent: "echo", ico: "spark", tone: "amber", html: `Requested a review from <b>${r.co}</b>`, meta: "just now · Reputation" }); award(5, { kind: "review" }); emit(); },
    addReviewRequest: (co) => { const id = "rv" + Date.now(); state = { ...state, reviews: [{ id, co, who: co, init: co.slice(0, 2).toUpperCase(), color: "oklch(0.56 0.17 277)", status: "requested", rating: 0, source: "Google", text: "" }, ...state.reviews] }; emit(); },
    toggleSequence: (id) => { state = { ...state, sequences: state.sequences.map((s) => s.id === id ? { ...s, active: !s.active } : s) }; emit(); },
    addTemplate: (tpl) => { const id = "tp" + Date.now(); state = { ...state, emailTemplates: [{ ...tpl, id, uses: 0 }, ...state.emailTemplates] }; emit(); },
    updateTemplate: (id, patch) => { state = { ...state, emailTemplates: state.emailTemplates.map((t) => t.id === id ? { ...t, ...patch } : t) }; emit(); },
    addKBSource: (kbId, source) => { state = { ...state, knowledgeBases: state.knowledgeBases.map((k) => k.id === kbId ? { ...k, sources: [...k.sources, source], updated: "just now" } : k) }; emit(); },
    removeKB: (kbId) => { state = { ...state, knowledgeBases: state.knowledgeBases.filter((k) => k.id !== kbId) }; emit(); },
    setKBField: (kbId, patch) => { state = { ...state, knowledgeBases: state.knowledgeBases.map((k) => k.id === kbId ? { ...k, ...patch } : k) }; emit(); },
    saveBrainAnswer: (id, q, text) => {
      const brainAnswers = { ...state.brainAnswers, [id]: { q, text, at: Date.now() } };
      try { localStorage.setItem("fl_brainAnswers", JSON.stringify(brainAnswers)); } catch (e) {}
      // ensure a hosted "Business Brain" KB exists and reflects the answered count
      let kbs = state.knowledgeBases; let brain = kbs.find((k) => k.id === "kb-brain");
      const answered = Object.values(brainAnswers).filter((a) => a.text && a.text.trim()).length;
      if (!brain) { brain = { id: "kb-brain", name: "Business Brain", icon: "spark", tone: "indigo", embModel: "fl-embed-v2", topK: 8, visibility: "private", updated: "just now", agents: Object.keys(state.agents), sources: [] }; kbs = [brain, ...kbs]; }
      kbs = kbs.map((k) => k.id === "kb-brain" ? { ...k, updated: "just now", sources: [{ name: "Founder interview.brain", type: "doc", chunks: answered * 6 }] } : k);
      state = { ...state, brainAnswers, knowledgeBases: kbs }; emit();
    },
    saveMemory: (mem) => {
      const id = "mem-" + Date.now();
      const memories = [{ id, at: Date.now(), pinned: false, ...mem }, ...state.memories];
      try { localStorage.setItem("fl_memories", JSON.stringify(memories)); } catch (e) {}
      state = { ...state, memories };
      feedPush({ agent: mem.agent || "scout", ico: "spark", tone: "indigo", html: `Saved to memory: <b>${(mem.text || "").slice(0, 60)}${(mem.text || "").length > 60 ? "…" : ""}</b>`, meta: "just now · Knowledge" });
      emit(); return id;
    },
    toggleMemoryPin: (id) => { const memories = state.memories.map((m) => m.id === id ? { ...m, pinned: !m.pinned } : m); try { localStorage.setItem("fl_memories", JSON.stringify(memories)); } catch (e) {} state = { ...state, memories }; emit(); },
    addPersonalRecall: () => { const recall = { ...state.recall, added: true }; try { localStorage.setItem("fl_recall", JSON.stringify(recall)); } catch (e) {} state = { ...state, recall }; emit(); },
    indexPersonalRecall: (stats) => {
      const recall = { added: true, indexed: true, sources: [{ name: "iMessage · chat.db", type: "messages", ...stats }] };
      try { localStorage.setItem("fl_recall", JSON.stringify(recall)); } catch (e) {}
      state = { ...state, recall };
      feedPush({ agent: "scout", ico: "spark", tone: "indigo", html: `Personal Recall is live · <b>${stats.memories.toLocaleString()} searchable memories</b> indexed`, meta: "just now · Knowledge" });
      emit();
    },
    resetPersonalRecall: () => { const recall = { added: true, indexed: false, sources: [] }; try { localStorage.setItem("fl_recall", JSON.stringify(recall)); } catch (e) {} state = { ...state, recall }; emit(); },
    deleteMemory: (id) => { const memories = state.memories.filter((m) => m.id !== id); try { localStorage.setItem("fl_memories", JSON.stringify(memories)); } catch (e) {} state = { ...state, memories }; emit(); },
    toggleKBAgent: (kbId, agentId) => { state = { ...state, knowledgeBases: state.knowledgeBases.map((k) => k.id === kbId ? { ...k, agents: k.agents.includes(agentId) ? k.agents.filter((a) => a !== agentId) : [...k.agents, agentId] } : k) }; emit(); },
    installSkill: (id) => { state = { ...state, skills: state.skills.map((s) => s.id === id ? { ...s, installed: true } : s) }; const sk = state.skills.find((s) => s.id === id); if (sk) feedPush({ agent: "scout", ico: "puzzle", tone: "indigo", html: `Installed the <b>${sk.name}</b> skill`, meta: "just now" }); emit(); },
    addWorkflow: ({ name, steps }) => { const id = "wf-" + Date.now(); state = { ...state, workflows: [...state.workflows, { id, name, steps, active: true, runs: [] }], activeWorkflowId: id }; emit(); return id; },
    setActiveWorkflow: (id) => { state = { ...state, activeWorkflowId: id }; emit(); },
    renameWorkflow: (id, name) => { state = { ...state, workflows: state.workflows.map((w) => w.id === id ? { ...w, name } : w) }; emit(); },
    toggleWorkflowActive: (id) => { state = { ...state, workflows: state.workflows.map((w) => w.id === id ? { ...w, active: !w.active } : w) }; emit(); },
    saveWorkflowSteps: (id, steps) => { state = { ...state, workflows: state.workflows.map((w) => w.id === id ? { ...w, steps } : w) }; emit(); },
    duplicateWorkflow: (id) => { const src = state.workflows.find((w) => w.id === id); if (!src) return; const nid = "wf-" + Date.now(); state = { ...state, workflows: [...state.workflows, { id: nid, name: src.name + " (copy)", steps: src.steps.map((s) => ({ ...s })), active: false, runs: [] }], activeWorkflowId: nid }; emit(); return nid; },
    deleteWorkflow: (id) => { const rest = state.workflows.filter((w) => w.id !== id); const wfs = rest.length ? rest : [{ id: "wf-1", name: "Untitled workflow", steps: [], active: false, runs: [] }]; state = { ...state, workflows: wfs, activeWorkflowId: state.activeWorkflowId === id ? wfs[0].id : state.activeWorkflowId }; emit(); },
    logWorkflowRun: (id, result, ok) => { state = { ...state, workflows: state.workflows.map((w) => w.id === id ? { ...w, runs: [{ when: "Just now", result, ok }, ...(w.runs || [])].slice(0, 12) } : w) }; emit(); },
    startPowerHour: () => {
      state = { ...state, gamify: { ...state.gamify, powerHourEndsAt: Date.now() + 60 * 60 * 1000 } };
      feedPush({ agent: "scout", ico: "bolt", tone: "amber", html: `⚡ <b>Power hour!</b> Every action earns ${state.gamify.multiplier}× points for the next 60 minutes`, meta: "just now" });
      emit();
    },
    addMarketListing: (listing) => { state = { ...state, market: [{ ...listing }, ...state.market] }; emit(); },

    // ---- team ----
    addMember: (m) => {
      const id = "u-" + Date.now();
      const colors = ["oklch(0.62 0.15 18)", "oklch(0.62 0.13 152)", "oklch(0.66 0.12 235)", "oklch(0.66 0.14 50)", "oklch(0.55 0.15 330)"];
      const parts = (m.name || "New User").trim().split(/\s+/);
      const init = ((parts[0][0] || "") + (parts[1] ? parts[1][0] : "")).toUpperCase() || "U";
      const member = { id, name: m.name || "New User", email: m.email || "", role: m.role || "Member", color: colors[state.team.length % colors.length], init, kind: "human" };
      state = { ...state, team: [...state.team, member] };
      feedPush({ agent: "ledger", ico: "users", tone: "indigo", html: `Invited <b>${member.name}</b> to the workspace as ${member.role}`, meta: "just now" });
      emit();
      return id;
    },
    setMemberRole: (id, role) => { state = { ...state, team: state.team.map((m) => m.id === id ? { ...m, role } : m) }; emit(); },
    removeMember: (id) => { state = { ...state, team: state.team.filter((m) => m.id !== id) }; emit(); },

    // ---- security ----
    setSecurityMode: (mode) => {
      state = { ...state, security: { ...state.security, mode } };
      const label = mode === "paused" ? "🛑 Kill switch engaged, all agents stopped" : mode === "semi" ? "Agents set to analyze-only, nothing runs without approval" : "Agents are Live, full autonomy on";
      feedPush({ agent: "ledger", ico: "shield", tone: mode === "paused" ? "rose" : mode === "semi" ? "amber" : "green", html: `<b>Security:</b> ${label}`, meta: "just now · by you" });
      emit();
    },
    toggleAgentPause: (id) => {
      const cur = !!state.security.agentPaused[id];
      state = { ...state, security: { ...state.security, agentPaused: { ...state.security.agentPaused, [id]: !cur } } };
      const a = state.agents[id];
      feedPush({ agent: id, ico: cur ? "play" : "pause", tone: cur ? "green" : "amber", html: `<b>Security:</b> ${a ? a.name : "Agent"} ${cur ? "resumed" : "paused"}`, meta: "just now · by you" });
      emit();
    },
    setGuardrail: (key, val) => { state = { ...state, security: { ...state.security, guardrails: { ...state.security.guardrails, [key]: val } } }; emit(); },
    setSpendCap: (n) => { state = { ...state, security: { ...state.security, spendCap: n } }; emit(); },
    setAccess: (key, val) => { state = { ...state, security: { ...state.security, access: { ...state.security.access, [key]: val } } }; emit(); },
    setRolePerm: (role, key, val) => { state = { ...state, security: { ...state.security, roles: { ...state.security.roles, [role]: { ...state.security.roles[role], [key]: val } } } }; emit(); },
    setRateLimit: (key, val) => { state = { ...state, security: { ...state.security, rateLimits: { ...state.security.rateLimits, [key]: val } } }; emit(); },
    revokeSession: (id) => {
      const s = state.security.sessions.find((x) => x.id === id);
      state = { ...state, security: { ...state.security, sessions: state.security.sessions.filter((x) => x.id !== id) } };
      if (s) feedPush({ agent: "ledger", ico: "shield", tone: "amber", html: `<b>Security:</b> signed out session on ${s.device}`, meta: "just now · by you" });
      emit();
    },
    revokeAllSessions: () => { state = { ...state, security: { ...state.security, sessions: state.security.sessions.filter((x) => x.current) } }; feedPush({ agent: "ledger", ico: "shield", tone: "amber", html: `<b>Security:</b> signed out all other sessions`, meta: "just now · by you" }); emit(); },
    setAnomalyWatch: (val) => { state = { ...state, security: { ...state.security, anomalyWatch: val } }; emit(); },
    setAutoPause: (val) => { state = { ...state, security: { ...state.security, autoPause: val } }; emit(); },
    resolveAnomaly: (id) => { state = { ...state, security: { ...state.security, anomalies: state.security.anomalies.filter((a) => a.id !== id) } }; emit(); },
    setRetention: (days) => { state = { ...state, security: { ...state.security, retentionDays: days } }; emit(); },

    // ---- Frontline support ----
    resolveTicket: (id, how) => {
      const t = state.tickets.find((x) => x.id === id);
      state = { ...state, tickets: state.tickets.map((x) => x.id === id ? { ...x, status: how === "deflect" ? "deflected" : "resolved" } : x) };
      if (t) {
        feedPush({ agent: "pip", ico: "checkCircle", tone: "green", html: `${how === "deflect" ? "Auto-resolved" : "Resolved"} <b>${t.cust}</b>: ${t.subject}`, meta: "just now · Frontline" });
        award(8, { kind: "ticket" });
        state = { ...state, stats: { ...state.stats, tasksHandled: state.stats.tasksHandled + 1 } };
      }
      emit();
    },
    escalateTicket: (id, human) => {
      state = { ...state, tickets: state.tickets.map((x) => x.id === id ? { ...x, status: "needs_human", human: human || x.human } : x) };
      emit();
    },
    sendTicketReply: (id) => {
      const t = state.tickets.find((x) => x.id === id);
      state = { ...state, tickets: state.tickets.map((x) => x.id === id ? { ...x, status: "resolved" } : x) };
      if (t) { feedPush({ agent: "pip", ico: "mail", tone: "indigo", html: `Replied to <b>${t.cust}</b> and closed: ${t.subject}`, meta: "just now · Frontline" }); award(8, { kind: "ticket" }); }
      emit();
    },
  };
})();

function useStore(selector) {
  const [, force] = React.useState(0);
  React.useEffect(() => FLStore.subscribe(() => force((x) => x + 1)), []);
  return selector ? selector(FLStore.getState()) : FLStore.getState();
}

window.FLStore = FLStore;
window.useStore = useStore;
