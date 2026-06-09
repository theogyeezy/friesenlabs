// @ts-nocheck
import React from "react";
// data.jsx, mock data for Friesen Labs
const AGENTS = {
  scout:   { id: "scout",   name: "Scout",   role: "Lead research",   color: "oklch(0.56 0.17 277)", init: "🦉" },
  nadia:   { id: "nadia",   name: "Nadia",   role: "Outreach",        color: "oklch(0.62 0.15 18)",  init: "🦊" },
  ledger:  { id: "ledger",  name: "Ledger",  role: "Approvals & ops", color: "oklch(0.62 0.13 152)", init: "🦫" },
  echo:    { id: "echo",    name: "Echo",    role: "Follow-ups",      color: "oklch(0.66 0.12 235)", init: "🦜" },
  margo:   { id: "margo",   name: "Margo",   role: "Quoting",         color: "oklch(0.66 0.14 50)",  init: "🦝" },
  pip:     { id: "pip",     name: "Pip",     role: "Support",         color: "oklch(0.6 0.13 200)",  init: "🐧" },
};
const AGENT_FACES = ["🦊","🦉","🦝","🦫","🦜","🐻","🐺","🐧","🐝","🐱","🐶","🦁","🐯","🦅","🐢","🤖","🐙","🦄"];

const TEAM = [
  { id: "u-jordan", name: "Jordan Reyes", email: "jordan@reyesco.com", role: "Owner", color: "oklch(0.56 0.17 277)", init: "JR", kind: "human" },
  { id: "u-sam", name: "Sam Lee", email: "sam@reyesco.com", role: "Admin", color: "oklch(0.62 0.15 18)", init: "SL", kind: "human" },
  { id: "u-pat", name: "Pat Kim", email: "pat@reyesco.com", role: "Member", color: "oklch(0.62 0.13 152)", init: "PK", kind: "human" },
];

const STAGES = [
  { id: "lead",      name: "New Lead",   color: "oklch(0.66 0.12 235)" },
  { id: "qualified", name: "Qualified",  color: "oklch(0.56 0.17 277)" },
  { id: "proposal",  name: "Proposal",   color: "oklch(0.66 0.14 50)" },
  { id: "won",       name: "Won",        color: "oklch(0.62 0.13 152)" },
];

const COLORS_CO = ["oklch(0.56 0.17 277)","oklch(0.62 0.15 18)","oklch(0.62 0.13 152)","oklch(0.66 0.12 235)","oklch(0.66 0.14 50)","oklch(0.55 0.15 330)"];

let _id = 0;
const _CLOSE = { lead: "in ~3 wks", qualified: "in ~2 wks", proposal: "in ~1 wk", won: "closed", lost: "lost" };
const _TASK = { lead: "Send intro & qualify", qualified: "Confirm the discovery call", proposal: "Follow up on the quote", won: "", lost: "" };
const _SOURCES = ["Organic search", "Referral", "Paid ads", "Direct traffic", "Social", "Inbound form"];
const _TITLES = ["Owner", "Operations Manager", "Office Manager", "Founder", "Practice Manager", "GM", "Procurement Lead"];
const _INDUSTRY = ["Food & beverage", "Healthcare", "Hospitality", "Retail", "Fitness & wellness", "Home services", "Professional services"];
const _EMP = ["1–10", "11–50", "11–50", "1–10", "51–200"];
const _lineItems = (value, co) => value > 12000
  ? [{ name: "Implementation & onboarding", qty: 1, price: Math.round(value * 0.25) }, { name: "Annual service plan", qty: 1, price: value - Math.round(value * 0.25) }]
  : [{ name: "Service package", qty: 1, price: value }];
const D = (co, person, value, stage, agent, agentNote, heat, init, days, email, phone, extra) => {
  const id = ++_id;
  const domain = (email.split("@")[1]) || (co.toLowerCase().replace(/[^a-z]/g, "") + ".com");
  const base = { id, co, person, value, stage, agent, agentNote, heat, init, days, email, phone,
    coColor: COLORS_CO[id % COLORS_CO.length],
    closeDate: _CLOSE[stage] || "in ~2 wks",
    stageDays: days,                 // days untouched in current stage
    createdDays: days + 6 + (id % 9), // total age of the deal
    // ---- HubSpot-standard properties ----
    source: _SOURCES[id % _SOURCES.length],
    dealType: id % 3 === 0 ? "Existing business" : "New business",
    priority: heat === "hot" ? "High" : heat === "warm" ? "Medium" : "Low",
    persuadable: stage === "won" || stage === "lost" ? 0 : Math.max(18, Math.min(95, (heat === "hot" ? 74 : heat === "warm" ? 55 : 34) + ((id * 7) % 21) - 10)),
    title: _TITLES[id % _TITLES.length],
    domain, industry: _INDUSTRY[id % _INDUSTRY.length], employees: _EMP[id % _EMP.length],
    lineItems: _lineItems(value, co),
    lastActivity: days === 0 ? "today" : days + "d ago",
    timesContacted: 2 + (id % 6),
    timeline: [
      { ico: "spark", tone: "indigo", t: (days || 0) + "d ago", txt: agentNote },
      { ico: "mail", tone: "indigo", t: (days + 2) + "d ago", txt: "Email opened by " + person.split(" ")[0] },
      { ico: "doc", tone: "amber", t: (days + 6) + "d ago", txt: "Deal created and assigned" },
    ],
    tasks: _TASK[stage] ? [{ id: "t" + id, title: _TASK[stage], due: days > 4 ? "overdue" : "today", done: false }] : [],
    lostReason: null };
  return Object.assign(base, extra || {});
};

const DEALS = [
  D("Birch & Co. Roasters", "Dana Okafor",   18500, "lead",      "scout",  "Enriched 9 data points · scored fit 88/100", "hot",  "BC", 1, "dana@birchco.com",      "(415) 555-0182"),
  D("Tidewater Dental",     "Marcus Liu",     7200, "lead",      "scout",  "Found 3 buying signals on their site",       "warm", "TD", 2, "m.liu@tidewater.health","(206) 555-0147"),
  D("Hollow Pine Cabins",   "Renee Vasquez", 12400, "lead",      "nadia",  "Drafted intro email, awaiting your OK",      "warm", "HP", 1, "renee@hollowpine.co",   "(541) 555-0193"),
  D("North Loop Cycles",    "Sam Petrov",     4900, "qualified", "nadia",  "2 emails sent · opened 4×, no reply yet",    "warm", "NL", 4, "sam@northloop.bike",    "(612) 555-0166"),
  D("Cedar Street Yoga",    "Priya Nair",     6800, "qualified", "echo",   "Booked discovery call for Thu 2pm",          "hot",  "CS", 3, "priya@cedaryoga.studio","(503) 555-0128"),
  D("Maple Grove Vet",      "Tom Becker",     9300, "qualified", "scout",  "Re-scored to 91 after site visit signal",    "hot",  "MG", 2, "tom@maplegrovevet.com", "(414) 555-0175"),
  D("Lantern Bakehouse",    "Aisha Rahman",  15700, "proposal",  "margo",  "Generated 3-tier quote · sent for review",   "hot",  "LB", 5, "aisha@lanternbake.com", "(773) 555-0119"),
  D("Riverside Plumbing",   "Gus Hartley",   22100, "proposal",  "margo",  "Quote opened 6× · prepping follow-up",       "hot",  "RP", 6, "gus@riversideplumb.com","(615) 555-0154"),
  D("Quill & Press",        "Bea Coleman",    5400, "proposal",  "echo",   "Sent reminder · proposal expires in 3d",     "warm", "QP", 8, "bea@quillpress.studio", "(919) 555-0188"),
  D("Sundial Landscaping",  "Owen Reyes",    13900, "won",       "ledger", "Contract signed · onboarding queued",        "hot",  "SL", 0, "owen@sundial.land",     "(480) 555-0102"),
  D("Park Ave Optometry",   "Lena Fischer",   8600, "won",       "ledger", "Invoiced · payment scheduled",               "warm", "PA", 0, "lena@parkaveeyes.com",  "(312) 555-0137"),
  D("Granite Peak Gym",     "Dale Monroe",   11200, "lost",      "nadia",  "Went with a cheaper competitor",             "warm", "GP", 14, "dale@granitepeak.fit",  "(720) 555-0143", { lostReason: "Price" }),
  D("Bluebird Cafe",        "Nora Adler",     6300, "lost",      "echo",   "Stopped responding after the proposal",      "warm", "BB", 21, "nora@bluebirdcafe.co",  "(503) 555-0177", { lostReason: "Went silent" }),
];

// dashboard activity feed (newest first); "streamed" ones appended live
const FEED_SEED = [
  { agent: "ledger", ico: "checkCircle", tone: "green",  html: "Approved & sent invoice <b>#INV-2049</b> to Park Ave Optometry", meta: "auto-approved · 2 min ago" },
  { agent: "margo",  ico: "doc",         tone: "amber",  html: "Generated a 3-tier quote for <b>Lantern Bakehouse</b>, needs your sign-off", meta: "needs approval · 6 min ago" },
  { agent: "scout",  ico: "target",      tone: "indigo", html: "Re-scored <b>Maple Grove Vet</b> to <b>91/100</b> after a pricing-page visit", meta: "11 min ago" },
  { agent: "echo",   ico: "calendar",    tone: "indigo", html: "Booked a discovery call with <b>Cedar Street Yoga</b> for Thu 2:00pm", meta: "24 min ago" },
  { agent: "nadia",  ico: "mail",        tone: "indigo", html: "Sent follow-up #2 to <b>North Loop Cycles</b>, opened 4 times", meta: "38 min ago" },
  { agent: "scout",  ico: "spark",       tone: "indigo", html: "Enriched <b>Birch & Co. Roasters</b> with 9 firmographic data points", meta: "52 min ago" },
];

// candidate live events that stream in over time
const FEED_LIVE = [
  { agent: "echo",   ico: "mail",      tone: "indigo", html: "Replied to <b>Quill & Press</b> about proposal timing", meta: "just now" },
  { agent: "scout",  ico: "spark",     tone: "indigo", html: "Found a hiring signal at <b>Tidewater Dental</b> (+3 fit)", meta: "just now" },
  { agent: "ledger", ico: "checkCircle", tone: "green", html: "Reconciled 2 payments and closed <b>3 tasks</b>", meta: "just now" },
  { agent: "nadia",  ico: "send",      tone: "indigo", html: "Queued a warm intro to <b>Hollow Pine Cabins</b>", meta: "just now" },
  { agent: "margo",  ico: "doc",       tone: "amber",  html: "Drafted a renewal quote for <b>Riverside Plumbing</b>", meta: "just now" },
];

const APPROVALS_SEED = [
  { id: "a1", agent: "margo", ico: "doc", tone: "amber", title: "Send 3-tier quote to Lantern Bakehouse",
    sub: "Quoting agent · $15,700 estimated", pf: "Proposal draft",
    body: "Hi Aisha, based on your volume I've put together three options. The Growth tier ($15,700/yr) fits your 4 locations best and includes priority support…" },
  { id: "a2", agent: "nadia", ico: "mail", tone: "indigo", title: "Send intro email to Hollow Pine Cabins",
    sub: "Outreach agent · first touch", pf: "Email draft",
    body: "Hi Renee, noticed Hollow Pine is opening two new cabins this spring. We help small hospitality teams automate booking follow-ups so nothing slips…" },
  { id: "a3", agent: "ledger", ico: "trend", tone: "green", title: "Apply 8% loyalty discount for Sundial",
    sub: "Ops agent · within your policy", pf: "Price adjustment",
    body: "Sundial Landscaping has been a customer for 3 years with zero late payments. Policy allows up to 10% loyalty discount, recommending 8% on renewal." },
];

const NAV = [
  { id: "dashboard", label: "Command Center", icon: "grid" },
  { id: "sell",      label: "Sell",           icon: "trophy" },
  { id: "frontline", label: "Frontline",      icon: "inbox" },
  { id: "workflows", label: "Workflows",      icon: "workflow" },
  { id: "approvals", label: "Greenlight",     icon: "checkCircle", badge: "3", badgeAmber: true },
];
const NAV_CRM = [
  { id: "crm",       label: "Pipeline",       icon: "users", badge: "11" },
  { id: "contacts",  label: "Contacts",       icon: "users" },
  { id: "billing",   label: "Billing",        icon: "trend" },
  { id: "calendar",  label: "Calendar",       icon: "calendar" },
  { id: "reviews",   label: "Reputation",     icon: "spark" },
  { id: "templates", label: "Templates",      icon: "note" },
  { id: "email",     label: "Email",          icon: "mail" },
];
const NAV_AGENTS = [
  { id: "agents", label: "Agents", icon: "spark" },
  { id: "marketplace", label: "Marketplace", icon: "puzzle" },
  { id: "cortex", label: "Cortex", icon: "network" },
  { id: "knowledge", label: "Knowledge", icon: "doc" },
];
const NAV_CONNECT = [
  { id: "sidecar", label: "Sidecar", icon: "layers" },
  { id: "integrations", label: "Switchboard", icon: "plug" },
];
const NAV2 = [
  { id: "reports",  label: "Reports",  icon: "trend" },
  { id: "security", label: "Security", icon: "shield" },
  { id: "settings", label: "Settings", icon: "settings" },
];

// chart series, workflow throughput last 14 days (tasks auto-handled vs human)
const THROUGHPUT = [
  { d: "M", auto: 42, human: 14 }, { d: "T", auto: 51, human: 12 }, { d: "W", auto: 48, human: 16 },
  { d: "T", auto: 63, human: 11 }, { d: "F", auto: 72, human: 13 }, { d: "S", auto: 38, human: 6 },
  { d: "S", auto: 31, human: 4 },  { d: "M", auto: 69, human: 12 }, { d: "T", auto: 78, human: 10 },
  { d: "W", auto: 84, human: 14 }, { d: "T", auto: 91, human: 9 },  { d: "F", auto: 103, human: 11 },
  { d: "S", auto: 64, human: 5 },  { d: "S", auto: 58, human: 7 },
];

// time-range presets for the Command Center, each reorganizes the dashboard
const RANGES = [
  { id: "24h", label: "24 hours", sub: "Last 24 hours", greet: "in the last 24 hours", cmp: "vs prior day",
    throughput: [
      { d: "12a", auto: 6, human: 1 }, { d: "3a", auto: 4, human: 0 }, { d: "6a", auto: 9, human: 1 },
      { d: "9a", auto: 22, human: 5 }, { d: "12p", auto: 28, human: 6 }, { d: "3p", auto: 31, human: 4 },
      { d: "6p", auto: 19, human: 3 }, { d: "9p", auto: 11, human: 2 },
    ],
    stats: { tasks: 142, hours: 6, approval: 88, tasksD: "6%", pipelineD: "2%", hoursD: "4%", apprD: "1%",
      taskSpark: [8,12,9,18,22,28,24,31], pipeSpark: [80,82,79,84,86,88,90,92], hourSpark: [3,4,4,5,5,6,6,6], apprSpark: [82,84,83,86,87,86,88,88] } },
  { id: "7d", label: "7 days", sub: "Last 7 days", greet: "this week", cmp: "vs last week",
    throughput: [
      { d: "M", auto: 69, human: 12 }, { d: "T", auto: 78, human: 10 }, { d: "W", auto: 84, human: 14 },
      { d: "T", auto: 91, human: 9 }, { d: "F", auto: 103, human: 11 }, { d: "S", auto: 64, human: 5 }, { d: "S", auto: 58, human: 7 },
    ],
    stats: { tasks: 1284, hours: 47, approval: 86, tasksD: "18%", pipelineD: "12%", hoursD: "9%", apprD: "4%",
      taskSpark: [20,28,24,40,38,52,61], pipeSpark: [60,58,66,72,70,84,96], hourSpark: [30,34,31,38,42,40,47], apprSpark: [70,74,72,78,80,83,86] } },
  { id: "30d", label: "30 days", sub: "Last 30 days", greet: "this month", cmp: "vs prior month",
    throughput: [
      { d: "W1", auto: 412, human: 78 }, { d: "W2", auto: 486, human: 71 }, { d: "W3", auto: 538, human: 64 }, { d: "W4", auto: 602, human: 59 },
    ],
    stats: { tasks: 5420, hours: 198, approval: 87, tasksD: "23%", pipelineD: "16%", hoursD: "14%", apprD: "5%",
      taskSpark: [320,360,410,460,500,560,602], pipeSpark: [220,260,300,340,380,420,460], hourSpark: [120,140,150,165,175,188,198], apprSpark: [78,80,82,84,85,86,87] } },
  { id: "yoy", label: "Year over year", sub: "Last 12 months", greet: "over the past year", cmp: "vs prior year",
    throughput: [
      { d: "J", auto: 1820, human: 410 }, { d: "F", auto: 2010, human: 388 }, { d: "M", auto: 2240, human: 372 },
      { d: "A", auto: 2480, human: 351 }, { d: "M", auto: 2710, human: 340 }, { d: "J", auto: 3020, human: 322 },
      { d: "J", auto: 3310, human: 305 }, { d: "A", auto: 3580, human: 298 }, { d: "S", auto: 3860, human: 281 },
      { d: "O", auto: 4190, human: 270 }, { d: "N", auto: 4520, human: 258 }, { d: "D", auto: 4880, human: 244 },
    ],
    stats: { tasks: 61240, hours: 2280, approval: 89, tasksD: "212%", pipelineD: "148%", hoursD: "186%", apprD: "11%",
      taskSpark: [1820,2240,2710,3310,3860,4520,4880], pipeSpark: [400,900,1500,2200,3000,3900,4800], hourSpark: [800,1100,1400,1650,1900,2100,2280], apprSpark: [72,76,80,83,86,88,89] } },
];

// CRM rep performance, base = 7-day; scales with the dashboard range
const REP_STATS = [
  { name: "Jordan Reyes", you: true, kind: "human", init: "JR", color: "oklch(0.56 0.17 277)", closed: 4, pipeline: 58200, winRate: 38, activities: 142, agent: "scout", quota: 60000, closedVal: 52400 },
  { name: "Sam Lee", kind: "human", init: "SL", color: "oklch(0.62 0.15 18)", closed: 3, pipeline: 41800, winRate: 33, activities: 118, agent: "nadia", quota: 50000, closedVal: 38600 },
  { name: "Pat Kim", kind: "human", init: "PK", color: "oklch(0.62 0.13 152)", closed: 2, pipeline: 29400, winRate: 29, activities: 96, agent: "echo", quota: 45000, closedVal: 24800 },
  { name: "Scout", kind: "agent", init: "🦊", color: "oklch(0.56 0.17 277)", closed: 5, pipeline: 67500, winRate: 41, activities: 1240, quota: 70000, closedVal: 71200 },
  { name: "Nadia", kind: "agent", init: "🦉", color: "oklch(0.62 0.13 235)", closed: 3, pipeline: 38900, winRate: 31, activities: 980, quota: 40000, closedVal: 36400 },
];
// per-range scale for rep stats (relative to 7-day baseline)
const REP_SCALE = { "24h": 0.16, "7d": 1, "30d": 4.1, "yoy": 48 };

// Business Brain founder interview, grouped & skippable
const BRAIN_QUESTIONS = [
  { group: "The business", items: [
    { id: "what", q: "In one or two sentences, what does your business actually do?", hint: "Imagine explaining it to a new neighbor." },
    { id: "who", q: "Who is your ideal customer, and who is not a fit?", hint: "The more specific, the better your agents qualify." },
    { id: "diff", q: "Why do customers pick you over the alternatives?", hint: "Your real edge, not marketing speak." },
    { id: "offer", q: "Walk through what you sell and roughly what it costs.", hint: "Packages, price ranges, what's included." },
  ] },
  { group: "How you operate", items: [
    { id: "process", q: "What happens from first contact to a closed sale?", hint: "Your sales motion in plain words." },
    { id: "voice", q: "How should we sound when we talk to your customers?", hint: "Warm, formal, funny? Any words you love or ban?" },
    { id: "never", q: "What should an agent never do without asking you first?", hint: "Your hard lines, refunds, discounts, promises." },
    { id: "busy", q: "What eats most of your time that you wish ran itself?", hint: "Where automation would help you most." },
  ] },
  { group: "You & the vision", items: [
    { id: "why", q: "Why did you start this, and what do you care about most?", hint: "What gets you up in the morning." },
    { id: "win", q: "What does a great year look like for you?", hint: "Growth, freedom, a number, a feeling." },
    { id: "proud", q: "Tell a story of a customer you were proud to serve.", hint: "Specifics help the models capture your standard." },
    { id: "worry", q: "What keeps you up at night about the business?", hint: "Risks and worries we should be mindful of." },
  ] },
];

const RECALL_HITS = [
  { who: "Matt Yee", date: "Mar 14, 2026", text: "Honestly the Chicago trip was a blur, but that deep dish place near the river, Lou's, was unreal. We have to go back.", tag: "Travel" },
  { who: "Allie", date: "May 28, 2026", text: "No worries! Just promise me you'll actually take the weekend off this time 😅 you said you'd unplug.", tag: "Promise" },
  { who: "Mom", date: "Apr 2, 2026", text: "Don't forget Dad's birthday dinner is the 19th. Table booked for 7. Bring the good wine.", tag: "Family" },
  { who: "Landlord (Rick)", date: "Feb 9, 2026", text: "Lease renewal is ready whenever. Same terms, I can hold the rate if you sign before March.", tag: "Logistics" },
  { who: "Matt Yee", date: "Jan 22, 2026", text: "That ramen spot I mentioned is Kaze on 4th, get the spicy miso. Thank me later.", tag: "Rec" },
];

const MEMORY_SEED = [
  { id: "mem-s1", text: "Owner prefers a warm, first-name tone in all customer email, never corporate or stiff.", source: "Frontline chat", agent: "echo", tag: "Voice", at: Date.now() - 86400000 * 2, pinned: true },
  { id: "mem-s2", text: "Never offer more than 15% discount without owner approval, it erodes our margins.", source: "Greenlight", agent: "margo", tag: "Policy", at: Date.now() - 86400000 * 5, pinned: false },
  { id: "mem-s3", text: "Riverside Plumbing's decision-maker is Gus, not the front desk. Always ask for him.", source: "Uplift deal", agent: "scout", tag: "Account", at: Date.now() - 86400000 * 1, pinned: false },
];

// Sales desk: scheduled calls (today + tomorrow), each tied to a real deal so it opens in the CRM
const SALES_CALLS = [
  { id: "c1", co: "Riverside Plumbing", rep: "Sam Lee", repInit: "SL", repColor: "oklch(0.62 0.15 18)", when: "today", time: "9:30 AM", score: 91, note: "Quote opened 6× · close call" },
  { id: "c2", co: "Birch & Co. Roasters", rep: "You", repInit: "JR", repColor: "oklch(0.56 0.17 277)", when: "today", time: "11:00 AM", score: 88, note: "Warm intro · discovery" },
  { id: "c3", co: "Cedar Street Yoga", rep: "Pat Kim", repInit: "PK", repColor: "oklch(0.62 0.13 152)", when: "today", time: "1:00 PM", score: 84, note: "Confirm discovery outcome" },
  { id: "c4", co: "Maple Grove Vet", rep: "Sam Lee", repInit: "SL", repColor: "oklch(0.62 0.15 18)", when: "today", time: "3:30 PM", score: 91, note: "Re-scored to 91 · push demo" },
  { id: "c5", co: "North Loop Cycles", rep: "You", repInit: "JR", repColor: "oklch(0.56 0.17 277)", when: "today", time: "4:30 PM", score: 72, note: "4 opens, no reply · nudge" },
  { id: "c6", co: "Lantern Bakehouse", rep: "Pat Kim", repInit: "PK", repColor: "oklch(0.62 0.13 152)", when: "tomorrow", time: "9:00 AM", score: 90, note: "Proposal review" },
  { id: "c7", co: "Hollow Pine Cabins", rep: "Sam Lee", repInit: "SL", repColor: "oklch(0.62 0.15 18)", when: "tomorrow", time: "10:30 AM", score: 76, note: "Intro call" },
  { id: "c8", co: "Quill & Press", rep: "You", repInit: "JR", repColor: "oklch(0.56 0.17 277)", when: "tomorrow", time: "1:00 PM", score: 68, note: "Proposal expires in 3d" },
  { id: "c9", co: "Tidewater Dental", rep: "Pat Kim", repInit: "PK", repColor: "oklch(0.62 0.13 152)", when: "tomorrow", time: "2:30 PM", score: 70, note: "Qualify budget" },
];

// standalone Knowledge product: hosted, RAG-indexed knowledge bases
const KB_SEED = [
  { id: "kb-ops", name: "Operations & SOPs", icon: "layers", tone: "indigo", status: "ready", embModel: "fl-embed-v2", topK: 6, visibility: "private", updated: "2h ago", agents: ["scout", "nadia"],
    sources: [{ name: "Employee handbook.pdf", type: "pdf", chunks: 142 }, { name: "Opening & closing SOP.docx", type: "doc", chunks: 64 }, { name: "Approved vendor list.csv", type: "csv", chunks: 38 }, { name: "Safety procedures.md", type: "md", chunks: 51 }] },
  { id: "kb-sales", name: "Pricing & packages", icon: "trend", tone: "amber", status: "ready", embModel: "fl-embed-v2", topK: 5, visibility: "private", updated: "yesterday", agents: ["margo", "echo"],
    sources: [{ name: "2026 price book.csv", type: "csv", chunks: 120 }, { name: "Service packages.pdf", type: "pdf", chunks: 88 }, { name: "Discount policy.md", type: "md", chunks: 24 }] },
  { id: "kb-support", name: "Support help center", icon: "inbox", tone: "green", status: "ready", embModel: "fl-embed-v2", topK: 8, visibility: "shared", updated: "4h ago", agents: ["echo"],
    sources: [{ name: "Help center export.zip", type: "zip", chunks: 1840 }, { name: "Returns & refunds FAQ.md", type: "md", chunks: 96 }, { name: "Troubleshooting guide.pdf", type: "pdf", chunks: 204 }] },
];
const KB_STARTERS = [
  ["Employee handbook", "doc"], ["SOPs & playbooks", "layers"], ["Pricing & packages", "trend"],
  ["Contracts & templates", "doc"], ["FAQs & scripts", "inbox"], ["Product docs", "spark"],
  ["Brand & voice guide", "spark"], ["Past winning deals", "trend"],
];

const AGENT_LOAD = [
  { agent: "scout",  pct: 92, tasks: 148 },
  { agent: "nadia",  pct: 74, tasks: 96 },
  { agent: "margo",  pct: 61, tasks: 64 },
  { agent: "echo",   pct: 48, tasks: 52 },
  { agent: "ledger", pct: 37, tasks: 41 },
];

const PIPELINE_BY_STAGE = [
  { stage: "lead", label: "New Lead", val: 38100 },
  { stage: "qualified", label: "Qualified", val: 21000 },
  { stage: "proposal", label: "Proposal", val: 43200 },
  { stage: "won", label: "Won", val: 22500 },
];

// ---- Integration Hub catalog ----
const INTG_CATS = ["All", "CRM & Marketing", "Communication", "Payments & Finance", "Scheduling", "Commerce & Support"];
const I = (id, name, cat, desc, color, letter, dark, connected, agentIds) =>
  ({ id, name, cat, desc, color, letter, dark: !!dark, connected: !!connected, agents: agentIds || [] });
const INTEGRATIONS = [
  I("hubspot",  "HubSpot",          "CRM & Marketing",     "Sync contacts, deals & marketing activity into Uplift.", "#ff7a59", "H", false, true,  ["scout", "nadia"]),
  I("salesforce","Salesforce",      "CRM & Marketing",     "Two-way sync of accounts, leads and opportunities.",     "#00a1e0", "S", false, false, []),
  I("mailchimp","Mailchimp",        "CRM & Marketing",     "Let agents trigger and personalize email campaigns.",    "#ffe01b", "M", true,  false, []),
  I("gmail",    "Gmail",            "Communication",       "Agents read, draft and send from your inbox.",           "#ea4335", "G", false, true,  ["nadia", "echo"]),
  I("outlook",  "Outlook",          "Communication",       "Connect Microsoft 365 mail and contacts.",               "#0a6ed1", "O", false, false, []),
  I("slack",    "Slack",            "Communication",       "Get agent updates and approvals in your channels.",      "#4a154b", "S", false, true,  ["ledger"]),
  I("twilio",   "Twilio SMS",       "Communication",       "Send texts and reminders from your business number.",    "#f22f46", "T", false, false, []),
  I("whatsapp", "WhatsApp Business", "Communication",      "Reach customers on WhatsApp with agent replies.",        "#25d366", "W", false, false, []),
  I("gcal",     "Google Calendar",  "Scheduling",          "Agents book, move and confirm appointments.",            "#4285f4", "C", false, true,  ["echo"]),
  I("calendly", "Calendly",         "Scheduling",          "Auto-share booking links and sync new meetings.",        "#006bff", "C", false, false, []),
  I("stripe",   "Stripe",           "Payments & Finance",  "Create invoices, take payments, track payouts.",         "#635bff", "S", false, true,  ["ledger", "margo"]),
  I("quickbooks","QuickBooks",      "Payments & Finance",  "Keep the books in sync, invoices and reconciliation.",  "#2ca01c", "Q", false, false, []),
  I("square",   "Square",           "Payments & Finance",  "Sync POS sales and customer records.",                   "#1a1a1a", "S", false, false, []),
  I("xero",     "Xero",             "Payments & Finance",  "Accounting sync for invoices and bills.",                "#13b5ea", "X", false, false, []),
  I("shopify",  "Shopify",          "Commerce & Support",  "Pull orders and customers from your store.",             "#5e8e3e", "S", false, false, []),
  I("zendesk",  "Zendesk",          "Commerce & Support",  "Let agents triage and resolve support tickets.",         "#03363d", "Z", false, false, []),
  I("intercom", "Intercom",         "Commerce & Support",  "Agents handle live chat and follow-ups.",                "#1f8ded", "I", false, false, []),
  I("sheets",   "Google Sheets",    "Commerce & Support",  "Read and write data to your spreadsheets.",              "#0f9d58", "S", false, true,  ["scout"]),
];

// ---- Agents console config ----
const AUTONOMY_LEVELS = [
  { id: 0, label: "Suggest only",     desc: "Drafts everything and waits, nothing happens without you." },
  { id: 1, label: "Ask first",        desc: "Acts only after you approve each action in Greenlight." },
  { id: 2, label: "Act within limits", desc: "Works autonomously inside the guardrails you set below." },
  { id: 3, label: "Fully autonomous",  desc: "Runs end-to-end and reports back. Best for trusted, low-risk work." },
];
const AC = (status, autonomy, tasks, success, hours, trend, tools, skills, guardrails, activity) =>
  ({ status, autonomy, tasks, success, hours, trend, tools, skills, guardrails, activity });
const AGENT_CFG = {
  scout: AC("active", 3, 148, 96, 12, [30,42,38,55,60,72,68],
    ["hubspot", "sheets", "gmail"],
    ["Enrich new leads with firmographics", "Score fit 0–100", "Detect buying signals", "Route to the right agent"],
    [{ id: "g1", label: "Auto-enrich every new lead", on: true }, { id: "g2", label: "Flag leads scoring above 90", on: true }, { id: "g3", label: "Skip leads outside my service area", on: false }],
    [{ tone: "indigo", ico: "target", who: "Scout", t: "11 min ago", txt: "Re-scored Maple Grove Vet to 91/100." }, { tone: "indigo", ico: "spark", who: "Scout", t: "52 min ago", txt: "Enriched Birch & Co. with 9 data points." }, { tone: "indigo", ico: "sheets", who: "Scout", t: "2h ago", txt: "Logged 14 new leads to your tracking sheet." }]),
  nadia: AC("active", 1, 96, 71, 9, [20,28,24,32,30,38,41],
    ["gmail", "hubspot"],
    ["Draft personalized outreach", "Sequence multi-touch follow-ups", "A/B test subject lines", "Hand warm replies to you"],
    [{ id: "g1", label: "Always ask before first contact", on: true }, { id: "g2", label: "Pause if a lead replies negatively", on: true }, { id: "g3", label: "Cap at 50 sends per day", on: true }],
    [{ tone: "indigo", ico: "mail", who: "Nadia", t: "38 min ago", txt: "Sent follow-up #2 to North Loop Cycles." }, { tone: "amber", ico: "doc", who: "Nadia", t: "1h ago", txt: "Drafted an intro to Hollow Pine, waiting in Greenlight." }]),
  margo: AC("active", 2, 64, 88, 7, [12,18,15,22,26,24,30],
    ["stripe"],
    ["Generate tiered quotes", "Apply your pricing rules", "Track quote opens", "Trigger renewal quotes"],
    [{ id: "g1", label: "Quotes above $10k need approval", on: true }, { id: "g2", label: "Discount cap of 10%", on: true }, { id: "g3", label: "Auto-send quotes under $2k", on: false }],
    [{ tone: "amber", ico: "doc", who: "Margo", t: "6 min ago", txt: "Generated a 3-tier quote for Lantern Bakehouse." }, { tone: "indigo", ico: "trend", who: "Margo", t: "3h ago", txt: "Riverside quote opened 6×, prepping a nudge." }]),
  echo: AC("paused", 2, 52, 64, 5, [10,14,12,16,15,18,14],
    ["gmail", "gcal"],
    ["Chase no-replies", "Book discovery calls", "Send appointment reminders", "Reschedule no-shows"],
    [{ id: "g1", label: "Follow up after 3 days of silence", on: true }, { id: "g2", label: "Max 3 follow-ups per lead", on: true }, { id: "g3", label: "Only book within business hours", on: true }],
    [{ tone: "indigo", ico: "calendar", who: "Echo", t: "24 min ago", txt: "Booked a discovery call with Cedar Street Yoga." }]),
  ledger: AC("active", 2, 41, 99, 14, [22,20,26,24,30,28,34],
    ["stripe", "slack"],
    ["Auto-approve routine actions", "Reconcile payments", "Send & track invoices", "Close completed tasks"],
    [{ id: "g1", label: "Auto-approve invoices under $1,000", on: true }, { id: "g2", label: "Reconcile payments daily", on: true }, { id: "g3", label: "Notify me of anything over $5,000", on: true }],
    [{ tone: "green", ico: "checkCircle", who: "Ledger", t: "2 min ago", txt: "Approved & sent invoice #INV-2049 to Park Ave." }, { tone: "green", ico: "refresh", who: "Ledger", t: "1h ago", txt: "Reconciled 2 payments, closed 3 tasks." }]),
};

const EMAIL_CITIES = ["Austin, TX", "Denver, CO", "Portland, OR", "Nashville, TN", "Chicago, IL", "Austin, TX", "Denver, CO", "Remote"];
const EMAIL_SEED = {
  campaigns: [
    { id: "ec1", name: "March warm-lead re-engage", status: "sent", sent: 312, delivered: 305, opens: 168, clicks: 47, replies: 19, when: "Mar 14", segment: "Warm leads" },
    { id: "ec2", name: "New pricing announcement", status: "sent", sent: 540, delivered: 528, opens: 241, clicks: 63, replies: 28, when: "Mar 2", segment: "All contacts" },
    { id: "ec3", name: "Texas spring promo", status: "scheduled", sent: 0, delivered: 0, opens: 0, clicks: 0, replies: 0, when: "in 2 days", segment: "Austin, TX" },
    { id: "ec4", name: "Quote follow-up nudge", status: "draft", sent: 0, delivered: 0, opens: 0, clicks: 0, replies: 0, when: "—", segment: "Proposal stage" },
  ],
  // AI-suggested "smart" segments that aren't simple field filters
  smart: [
    { id: "sm1", name: "Opened but never replied", desc: "Engaged with the last 2 emails, no reply yet", count: 38, icon: "mail" },
    { id: "sm2", name: "High-intent this week", desc: "Quote opens + site visits spiking", count: 14, icon: "trend" },
    { id: "sm3", name: "Looks like your best customers", desc: "Resemble your top 5 closed-won accounts", count: 22, icon: "spark" },
    { id: "sm4", name: "Gone quiet 30+ days", desc: "Were active, now cold, worth a win-back", count: 41, icon: "clock" },
  ],
};

const BILLING_SEED = {
  reviews: [
    { id: "rv1", co: "Sundial Landscaping", who: "Owen Reyes", init: "SL", color: "oklch(0.62 0.15 18)", status: "posted", rating: 5, source: "Google", text: "They transformed our backyard, on time and on budget. Couldn't recommend more." },
    { id: "rv2", co: "Park Ave Optometry", who: "Lena Fischer", init: "PA", color: "oklch(0.66 0.12 235)", status: "posted", rating: 5, source: "Yelp", text: "Seamless from start to finish. The team really knows their stuff." },
    { id: "rv3", co: "Cedar Street Yoga", who: "Priya Nair", init: "CS", color: "oklch(0.56 0.17 277)", status: "requested", rating: 0, source: "Google", text: "" },
    { id: "rv4", co: "North Loop Cycles", who: "Sam Petrov", init: "NL", color: "oklch(0.62 0.13 152)", status: "requested", rating: 0, source: "Google", text: "" },
  ],
  referrals: [
    { id: "rf1", from: "Sundial Landscaping", who: "Owen Reyes", referred: "Granite Path Co.", status: "won", reward: "$200 credit" },
    { id: "rf2", from: "Park Ave Optometry", who: "Lena Fischer", referred: "Vista Eyecare", status: "in pipeline", reward: "pending" },
  ],
  templates: [
    { id: "tp1", name: "Cold intro", channel: "Email", uses: 142, body: "Hi {first}, noticed {company} is {signal}. We help teams like yours {value}. Open to a quick 15-min call this week?" },
    { id: "tp2", name: "Quote follow-up", channel: "Email", uses: 98, body: "Hi {first}, just checking in on the proposal I sent, happy to adjust anything. Want to hop on a call to walk through it?" },
    { id: "tp3", name: "Meeting reminder", channel: "SMS", uses: 210, body: "Hi {first}, reminder of our {meeting} tomorrow at {time}. Reply R to reschedule." },
    { id: "tp4", name: "Win-back", channel: "Email", uses: 54, body: "Hi {first}, it's been a while! We've shipped a lot since we last spoke, can I share what's new for {company}?" },
    { id: "tp5", name: "Review request", channel: "SMS", uses: 167, body: "Thanks for choosing us, {first}! If you have 30 seconds, a quick review really helps: {link}" },
  ],
  sequences: [
    { id: "sq1", name: "New lead nurture", steps: 4, active: true, enrolled: 23, desc: "Intro → value → case study → call CTA over 9 days" },
    { id: "sq2", name: "Quote chaser", steps: 3, active: true, enrolled: 11, desc: "Day 2 nudge → day 5 reminder → day 8 last call" },
    { id: "sq3", name: "Post-win onboarding", steps: 5, active: false, enrolled: 0, desc: "Welcome → setup → check-in → review ask → referral ask" },
  ],
  meetings: [
    { id: "m1", co: "Cedar Street Yoga", who: "Priya Nair", init: "CS", color: "oklch(0.56 0.17 277)", type: "Discovery call", when: "Today", time: "2:00 PM", dur: "30 min", agent: "echo", mode: "Video" },
    { id: "m2", co: "Maple Grove Vet", who: "Tom Becker", init: "MG", color: "oklch(0.66 0.12 235)", type: "Demo", when: "Today", time: "4:30 PM", dur: "45 min", agent: "scout", mode: "Video" },
    { id: "m3", co: "Riverside Plumbing", who: "Gus Hartley", init: "RP", color: "oklch(0.62 0.13 152)", type: "Quote review", when: "Tomorrow", time: "10:00 AM", dur: "30 min", agent: "margo", mode: "Phone" },
    { id: "m4", co: "Hollow Pine Cabins", who: "Renee Vasquez", init: "HP", color: "oklch(0.66 0.14 50)", type: "Intro", when: "Thu", time: "1:00 PM", dur: "20 min", agent: "nadia", mode: "Video" },
  ],
  quotes: [
    { id: "Q-1042", co: "Lantern Bakehouse", init: "LB", color: "oklch(0.66 0.14 50)", amount: 15700, status: "sent", created: "2d ago", items: [["Implementation & onboarding", 3925], ["Annual service plan", 11775]] },
    { id: "Q-1041", co: "Riverside Plumbing", init: "RP", color: "oklch(0.62 0.13 152)", amount: 22100, status: "signed", created: "5d ago", items: [["Implementation & onboarding", 5525], ["Annual service plan", 16575]] },
    { id: "Q-1039", co: "Cedar Street Yoga", init: "CS", color: "oklch(0.56 0.17 277)", amount: 6800, status: "draft", created: "1d ago", items: [["Service package", 6800]] },
  ],
  invoices: [
    { id: "INV-2049", co: "Park Ave Optometry", init: "PA", color: "oklch(0.66 0.12 235)", amount: 8600, status: "paid", due: "paid 3d ago" },
    { id: "INV-2048", co: "Sundial Landscaping", init: "SL", color: "oklch(0.62 0.15 18)", amount: 13900, status: "due", due: "due in 9 days" },
    { id: "INV-2047", co: "North Loop Cycles", init: "NL", color: "oklch(0.62 0.13 152)", amount: 4900, status: "overdue", due: "overdue 4 days" },
  ],
};

window.FL_DATA = { AGENTS, AGENT_FACES, TEAM, STAGES, DEALS, FEED_SEED, FEED_LIVE, APPROVALS_SEED, NAV, NAV_CRM, NAV_AGENTS, NAV_CONNECT, NAV2, THROUGHPUT, RANGES, REP_STATS, REP_SCALE, SALES_CALLS, RECALL_HITS, BRAIN_QUESTIONS, MEMORY_SEED, KB_SEED, KB_STARTERS, BILLING_SEED, EMAIL_SEED, EMAIL_CITIES, AGENT_LOAD, PIPELINE_BY_STAGE, INTEGRATIONS, INTG_CATS, AUTONOMY_LEVELS, AGENT_CFG };// ---- next-best-action nudges (agent-surfaced) ----
const NUDGES = [
  { id: "n1", agent: "echo", ico: "trend", tone: "amber", text: "Riverside's quote was opened 6× but no reply, a nudge could close it.", cta: "Let Echo follow up", action: "followup" },
  { id: "n2", agent: "scout", ico: "flame", tone: "rose", text: "Maple Grove Vet just re-scored to 91, strike while it's hot.", cta: "Open the deal", action: "open" },
  { id: "n3", agent: "nadia", ico: "mail", tone: "indigo", text: "3 leads have gone quiet for 4+ days, want a batch follow-up?", cta: "Draft follow-ups", action: "followup" },
  { id: "n4", agent: "margo", ico: "doc", tone: "amber", text: "Lantern Bakehouse opened your proposal twice today.", cta: "Send a check-in", action: "followup" },
];
window.FL_DATA.NUDGES = NUDGES;

// ---- Skills: composable capabilities agents can use ----
const SKILL_CATS = ["All", "Sales", "Support", "Ops", "Marketing", "Finance"];
const SKILL_MARKET = [
  { id: "sk-crm", name: "CRM read & write", cat: "Sales", ico: "users", tone: "rose", author: "Friesen", verified: true, installs: "12k", rating: 4.9, blurb: "Read and update deals, contacts and stages in Uplift or your connected CRM.", builtin: true },
  { id: "sk-email", name: "Send & track email", cat: "Sales", ico: "mail", tone: "indigo", author: "Friesen", verified: true, installs: "11k", rating: 4.9, blurb: "Send personalized email and track opens, clicks and replies.", builtin: true },
  { id: "sk-enrich", name: "Lead enrichment", cat: "Sales", ico: "spark", tone: "indigo", author: "Friesen", verified: true, installs: "9k", rating: 4.8, blurb: "Pull firmographics and buying signals, then score fit 0-100." },
  { id: "sk-quote", name: "Quote generator", cat: "Sales", ico: "doc", tone: "amber", author: "Lantern Labs", verified: true, installs: "4.2k", rating: 4.7, price: 9, blurb: "Build tiered quotes from your pricebook and send for approval." },
  { id: "sk-book", name: "Meeting booker", cat: "Sales", ico: "calendar", tone: "indigo", author: "Friesen", verified: true, installs: "7k", rating: 4.8, blurb: "Offer times from your calendar and book demos automatically." },
  { id: "sk-deflect", name: "Ticket deflection", cat: "Support", ico: "inbox", tone: "green", author: "Friesen", verified: true, installs: "6k", rating: 4.9, blurb: "Answer routine questions from your help docs before they reach a human." },
  { id: "sk-order", name: "Order status lookup", cat: "Support", ico: "search", tone: "green", author: "Shipwell", verified: true, installs: "3.1k", rating: 4.6, blurb: "Look up order and shipment status across your tools." },
  { id: "sk-refund", name: "Refund & returns", cat: "Support", ico: "trend", author: "Friesen", tone: "rose", verified: true, installs: "2.4k", rating: 4.5, blurb: "Process refunds within policy, routing exceptions to Greenlight." },
  { id: "sk-invoice", name: "Invoice chaser", cat: "Finance", ico: "doc", tone: "amber", author: "Ledgerly", verified: true, installs: "5.3k", rating: 4.8, price: 12, blurb: "Nudge overdue invoices on a schedule and reconcile payments." },
  { id: "sk-review", name: "Review requests", cat: "Marketing", ico: "spark", tone: "amber", author: "Bea C.", verified: false, installs: "1.8k", rating: 4.6, blurb: "Ask happy customers for a review at the right moment." },
  { id: "sk-social", name: "Social drafting", cat: "Marketing", ico: "send", tone: "indigo", author: "Quill & Press", verified: false, installs: "2.1k", rating: 4.4, price: 7, blurb: "Draft on-brand social posts from your updates and wins." },
  { id: "sk-slack", name: "Slack notifier", cat: "Ops", ico: "bell", tone: "green", author: "Friesen", verified: true, installs: "8k", rating: 4.7, blurb: "Post alerts and digests to your team's Slack channels." },
];
window.FL_DATA.SKILL_CATS = SKILL_CATS;
window.FL_DATA.SKILL_MARKET = SKILL_MARKET;

window.FL_DATA.WEEKLY_RECAP = {
  range: "May 24, 30",
  headline: "Your best week yet",
  stats: [
    { label: "Revenue won", val: "$36.5k", delta: "+22%", up: true },
    { label: "Tasks automated", val: "1,284", delta: "+18%", up: true },
    { label: "Hours saved", val: "47h", delta: "+9%", up: true },
    { label: "Deflection rate", val: "73%", delta: "+5%", up: true },
  ],
  wins: ["Closed Sundial Landscaping ($13.9k)", "Pip deflected 186 support tickets", "Nadia booked 9 demos on autopilot"],
  topAgent: "scout",
  nextWeek: "4 quotes are sitting unopened, a Monday nudge campaign could recover ~$28k.",
};
const GL = (id, agent, type, title, company, value, risk, policy, ago, rows, body, why) =>
  ({ id, agent, type, title, company, value, risk, policy, ago, rows, body, why, status: "pending", edited: false });
const GREENLIGHT_SEED = [
  GL("g1", "nadia", "email", "Send intro email to Hollow Pine Cabins", "Hollow Pine Cabins", 12400, "low", "within", "6 min ago",
    [["To", "Renee Vasquez · renee@hollowpine.co"], ["Subject", "A quick idea for Hollow Pine's spring season"]],
    "Hi Renee, saw that Hollow Pine is opening two new cabins this spring. We help small hospitality teams automate booking confirmations and follow-ups so nothing slips during your busy season. Worth a 15-minute look?\n\nBest,\nJordan",
    "First-touch outreach for a warm inbound lead. Tone and timing match your other intros, low risk, so I'd normally send, but first contact always pauses for you."),
  GL("g2", "margo", "quote", "Send 3-tier quote to Lantern Bakehouse", "Lantern Bakehouse", 15700, "med", "review", "12 min ago",
    [["Customer", "Aisha Rahman · 4 locations"], ["Recommended", "Growth, $15,700/yr"], ["Tiers", "Starter $9,400 · Growth $15,700 · Scale $24,000"]],
    "Hi Aisha, based on your four locations and current volume, the Growth tier fits best. It includes priority support and multi-site reporting. I've included Starter and Scale so you can compare.",
    "Quote value is above your $10k auto-send threshold, so it's routed for review. Pricing follows your standard rate card with no discount applied."),
  GL("g3", "ledger", "discount", "Apply 8% loyalty discount, Sundial Landscaping", "Sundial Landscaping", 13900, "low", "within", "28 min ago",
    [["Customer", "Owen Reyes · 3-year customer"], ["Discount", "8% loyalty on renewal"], ["Your cap", "10%, within policy"]],
    "Sundial has been a customer for 3 years with zero late payments. Recommending an 8% loyalty discount on their renewal to lock in another year.",
    "Within your 10% loyalty cap and the account has a clean payment history, so this is low risk. Flagging only because any discount over 5% asks for a quick nod."),
  GL("g4", "margo", "quote", "Renewal quote for Riverside Plumbing", "Riverside Plumbing", 22100, "high", "exceeds", "41 min ago",
    [["Customer", "Gus Hartley"], ["Amount", "$22,100/yr renewal"], ["Signal", "Quote opened 6× this week"]],
    "Hi Gus, here's your renewal for the year ahead, with the same coverage plus the new dispatch automation you asked about. Happy to walk through it.",
    "Above your $10k threshold and a high-value renewal, so this needs your sign-off. The 6 quote opens suggest strong intent, I'd send today."),
  GL("g5", "echo", "schedule", "Book discovery call, Cedar Street Yoga", "Cedar Street Yoga", 6800, "low", "within", "1h ago",
    [["With", "Priya Nair"], ["When", "Thu, May 31 · 2:00 pm"], ["Type", "30-min discovery call"]],
    "Calendar invite and a reminder text will go out once confirmed. I'll reschedule automatically if they can't make it.",
    "Slot is inside your business hours and your calendar is free. Booking confirmations normally auto-send, pausing because it's a new contact."),
  GL("g6", "nadia", "email", "Follow-up #3 to North Loop Cycles", "North Loop Cycles", 4900, "med", "review", "2h ago",
    [["To", "Sam Petrov · sam@northloop.bike"], ["Subject", "Still worth a quick chat?"], ["History", "2 sent · opened 4×, no reply"]],
    "Hi Sam, I'll keep this short. You've opened my last note a few times, so the timing might just be off. Want me to send a 2-minute overview instead of booking a call?",
    "This is the 3rd follow-up, which hits your 'max 3 per lead' guardrail, so I'm checking before sending. Engagement is high, so it's worth one more touch."),
  GL("g7", "ledger", "invoice", "Send invoice #INV-2050, Park Ave Optometry", "Park Ave Optometry", 8600, "low", "review", "3h ago",
    [["Customer", "Lena Fischer"], ["Amount", "$8,600"], ["Terms", "Net 30"]],
    "Invoice for the annual plan. Payment link included; I'll chase automatically if it's unpaid at day 25.",
    "Over your $1,000 auto-approve limit for invoices, so it's here for review. Amount matches the signed quote exactly."),
];

window.FL_DATA.GREENLIGHT_SEED = GREENLIGHT_SEED;

// ---- default workflow (steps), lives in the shared store ----
const WORKFLOW_SEED = [
  { type: "trigger",   title: "New lead arrives",  body: "From your website or inbox" },
  { type: "agent",     title: "Scout enriches",    body: "Research + score fit 0–100", agent: "scout" },
  { type: "condition", title: "Fit score > 80?",   body: "High-intent leads only" },
  { type: "agent",     title: "Nadia reaches out", body: "Draft a personalized intro", agent: "nadia" },
  { type: "approval",  title: "You approve",       body: "Review before it sends" },
  { type: "action",    title: "Send email",        body: "Delivered + tracked", agent: "nadia" },
];
window.FL_DATA.WORKFLOW_SEED = WORKFLOW_SEED;

// ---- Security: roles, sessions, anomalies ----
const ROLE_PERMS = [
  { key: "send_external", label: "Send external email & messages" },
  { key: "view_financials", label: "View revenue & financials" },
  { key: "manage_billing", label: "Manage plan & billing" },
  { key: "edit_workflows", label: "Build & edit workflows" },
  { key: "manage_agents", label: "Hire, pause & configure agents" },
  { key: "approve_highrisk", label: "Approve high-risk actions (discounts, contracts)" },
  { key: "manage_integrations", label: "Connect & revoke integrations" },
  { key: "manage_security", label: "Change security & guardrail settings" },
];
const ROLE_DEFAULTS = {
  Owner:  { send_external: true, view_financials: true, manage_billing: true, edit_workflows: true, manage_agents: true, approve_highrisk: true, manage_integrations: true, manage_security: true },
  Admin:  { send_external: true, view_financials: true, manage_billing: false, edit_workflows: true, manage_agents: true, approve_highrisk: true, manage_integrations: true, manage_security: false },
  Member: { send_external: true, view_financials: false, manage_billing: false, edit_workflows: false, manage_agents: false, approve_highrisk: false, manage_integrations: false, manage_security: false },
};
const SESSIONS = [
  { id: "s1", device: "MacBook Pro · Chrome", where: "Austin, TX", ago: "Active now", current: true },
  { id: "s2", device: "iPhone 15 · Safari", where: "Austin, TX", ago: "2 hours ago", current: false },
  { id: "s3", device: "Windows · Edge", where: "Dallas, TX", ago: "Yesterday", current: false },
];
const ANOMALIES = [
  { id: "an1", agent: "nadia", sev: "high", title: "Unusual send volume", detail: "Nadia queued 64 emails in 10 min, 5× her normal rate.", action: "Auto-paused Nadia", ago: "18 min ago", autopaused: true },
  { id: "an2", agent: "margo", sev: "med", title: "Off-hours activity", detail: "Margo generated a quote at 2:14am, outside business hours.", action: "Flagged for review", ago: "6 hours ago", autopaused: false },
  { id: "an3", agent: "echo", sev: "low", title: "New recipient domain", detail: "Echo emailed a domain never contacted before.", action: "Logged", ago: "Yesterday", autopaused: false },
];
window.FL_DATA.ROLE_PERMS = ROLE_PERMS;
window.FL_DATA.ROLE_DEFAULTS = ROLE_DEFAULTS;
window.FL_DATA.SESSIONS = SESSIONS;
window.FL_DATA.ANOMALIES = ANOMALIES;

// ---- Frontline: autonomous support desk ----
const TICKET_CHANNELS = { email: ["mail", "Email"], chat: ["spark", "Live chat"], form: ["doc", "Web form"], social: ["users", "Social DM"] };
let _tk = 0;
const TK = (cust, init, color, channel, subject, preview, status, intent, conf, ago, sla) =>
  ({ id: ++_tk, cust, init, color, channel, subject, preview, status, intent, conf, ago, sla });
const TICKETS = [
  TK("Dana Okafor", "DO", "oklch(0.56 0.17 277)", "email", "Where is my order #4821?", "Hi, I ordered last Tuesday and the tracking hasn't updated in 3 days…", "deflected", "Order status", 0.96, "2 min ago", "ok"),
  TK("Marcus Liu", "ML", "oklch(0.62 0.15 18)", "chat", "Do you offer same-day appointments?", "Hey! Quick question, can I book something for today?", "deflected", "Booking", 0.93, "8 min ago", "ok"),
  TK("Priya Nair", "PN", "oklch(0.62 0.13 152)", "email", "Request a refund for duplicate charge", "I was charged twice for my May invoice and need one reversed.", "needs_human", "Refund", 0.58, "14 min ago", "warn"),
  TK("Sam Petrov", "SP", "oklch(0.66 0.12 235)", "form", "How do I reset my password?", "Can't log in, the reset email never arrives.", "drafted", "Account", 0.91, "22 min ago", "ok"),
  TK("Aisha Rahman", "AR", "oklch(0.66 0.14 50)", "social", "Are you open on the holiday weekend?", "DM: hey are y'all open Monday?", "deflected", "Hours", 0.98, "31 min ago", "ok"),
  TK("Gus Hartley", "GH", "oklch(0.55 0.15 330)", "email", "Damaged item on delivery", "The unit arrived with a cracked panel, photos attached.", "needs_human", "Returns", 0.49, "44 min ago", "warn"),
  TK("Lena Fischer", "LF", "oklch(0.6 0.14 90)", "chat", "Change my subscription plan", "I want to move from Growth to Everything, how?", "drafted", "Billing", 0.88, "1 hr ago", "ok"),
  TK("Tom Becker", "TB", "oklch(0.6 0.15 260)", "email", "Thank you!! 5 stars", "Just wanted to say your team (well, agent!) was super helpful.", "resolved", "Praise", 0.99, "2 hrs ago", "ok"),
];
const KB_GAPS = [
  { q: "Do you price-match competitors?", asks: 7 },
  { q: "What's your holiday return window?", asks: 4 },
  { q: "Can I split payment across two cards?", asks: 3 },
];
const SUPPORT_STATS = { deflectionRate: 72, avgResponse: 2.4, csat: 4.7, openTickets: 3, resolvedToday: 48 };
window.FL_DATA.TICKETS = TICKETS;
window.FL_DATA.TICKET_CHANNELS = TICKET_CHANNELS;
window.FL_DATA.KB_GAPS = KB_GAPS;
window.FL_DATA.SUPPORT_STATS = SUPPORT_STATS;

// ---- agent marketplace catalog ----
const AGENT_MARKET = [
  { id: "m1", name: "Scout", init: "🦉", role: "Lead research", author: "Friesen Labs", verified: true, price: 0, installs: "12k", rating: 4.9, color: "oklch(0.56 0.17 277)", blurb: "Enriches and scores inbound leads automatically." },
  { id: "m2", name: "Nadia", init: "🦊", role: "Outreach", author: "Friesen Labs", verified: true, price: 0, installs: "9.8k", rating: 4.8, color: "oklch(0.62 0.15 18)", blurb: "Drafts and sends personalized outreach that books demos." },
  { id: "m3", name: "Penny", init: "🐧", role: "Bookkeeping", author: "Ada K.", verified: false, price: 0, installs: "5.4k", rating: 4.8, color: "oklch(0.66 0.12 235)", blurb: "Reconciles payments and chases overdue invoices." },
  { id: "m4", name: "Quill", init: "🦫", role: "Proposals", author: "Maya R.", verified: false, price: 12, installs: "2.1k", rating: 4.7, color: "oklch(0.62 0.13 152)", blurb: "Writes polished, on-brand proposals from any deal." },
  { id: "m5", name: "Reaper", init: "🦅", role: "Churn rescue", author: "Dev Collective", verified: true, price: 19, installs: "880", rating: 4.6, color: "oklch(0.64 0.14 40)", blurb: "Spots at-risk customers and wins them back." },
  { id: "m6", name: "Sol", init: "🦁", role: "Review requests", author: "BrightLocal", verified: true, price: 9, installs: "3.3k", rating: 4.9, color: "oklch(0.66 0.14 50)", blurb: "Asks happy customers for reviews at the perfect moment." },
  { id: "m7", name: "Bloom", init: "🐝", role: "Social posts", author: "Theo N.", verified: false, price: 0, installs: "4.0k", rating: 4.5, color: "oklch(0.6 0.15 330)", blurb: "Turns your wins into on-brand social posts." },
  { id: "m8", name: "Atlas", init: "🐢", role: "Scheduling", author: "Friesen Labs", verified: true, price: 0, installs: "7.2k", rating: 4.7, color: "oklch(0.58 0.14 200)", blurb: "Books, confirms and reschedules appointments." },
];
window.FL_DATA.AGENT_MARKET = AGENT_MARKET;
