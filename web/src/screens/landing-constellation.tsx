// @ts-nocheck
import React from "react";
const { useEffect, useRef } = React;
// landing-constellation.tsx — the live "neural constellation" hero plate.
//
// A dependency-free canvas render of the real product suite: Command Center at
// the heart, the ten products as hubs with neuron clusters, signals travelling
// between ANY two products (the route lights up only while in use), activity
// cards narrating product-true work, and a recurring Security guardrail
// interception that animates the draft-only guarantee. Spec:
// docs/superpowers/specs/2026-06-11-constellation-hero-design.md
//
// Perf/a11y contract: rAF runs only while on-screen AND the tab is visible;
// prefers-reduced-motion gets one static frame; DPR capped at 2; the whole
// canvas/cards layer is aria-hidden (the semantic hero copy is the overlay).

const CENTER = { name: "Command Center", label: "COMMAND CENTER" };
const PRODUCTS = [
  { name: "Uplift CRM", label: "UPLIFT CRM" },
  { name: "Frontline", label: "FRONTLINE" },
  { name: "Workflows", label: "WORKFLOWS" },
  { name: "Greenlight", label: "GREENLIGHT" },
  { name: "Agents", label: "AGENTS" },
  { name: "Switchboard", label: "SWITCHBOARD" },
  { name: "Sidecar", label: "SIDECAR" },
  { name: "Knowledge", label: "KNOWLEDGE" },
  { name: "Cortex", label: "CORTEX" },
  { name: "Security", label: "SECURITY" },
];

// Product-true activity (numbers highlighted in clay). Personas match the page.
const AGENT_MSGS = {
  "Command Center": ['Morning view ready: <span class="num">6 items</span> need you', 'All agents green, <span class="num">31 tasks</span> queued', 'Pipeline up <span class="num">$8.2k</span> this week'],
  "Uplift CRM": ['Moved <span class="num">2 deals</span> to Proposal', "Logged 14 touchpoints overnight", "Next step set on every open deal"],
  "Frontline": ['Pip deflected <span class="num">9 tickets</span>', "Routed a refund request to you", 'Answered "are you open?" in 4s'],
  "Workflows": ['Ran the new-lead workflow <span class="num">12×</span>', "Built a workflow from your prompt", "Paused at the approval step"],
  "Greenlight": ['Quote <span class="num">#1042</span> approved by you', "Holding 3 drafts for review", "Spend limit honored on every send"],
  "Agents": ['Margo drafted <span class="num">3 quotes</span>', "Nadia booked a discovery call", "Ledger nudged 2 overdue invoices"],
  "Switchboard": ["Synced HubSpot, Stripe & Gmail", 'Wrote <span class="num">28 updates</span> back to your CRM', "Connected a new tool in 2 min"],
  "Sidecar": ['Enriched <span class="num">12 deals</span> in your CRM', "Advanced a deal inside HubSpot", "Scout briefed your next call"],
  "Knowledge": ['Indexed <span class="num">12 new pages</span>', "Cited the 2026 price sheet", "Answered from your own docs"],
  "Cortex": ['Scored <span class="num">18 new leads</span>', "Accuracy up 4% this cycle", "Flagged a churn-risk account"],
  "Security": ['Audit trail: <span class="num">142 actions</span> logged', "Blocked an off-policy send", "Kill switch armed and ready"],
};
const SEC_MSGS = [
  'Held an off-policy send, parked the draft in <span class="num">Greenlight</span>',
  'Caught a send outside policy, routed to <span class="num">Greenlight</span> for you',
];

// Canvas literals visually matched to the .lp editorial tokens (canvas can't
// read CSS custom properties per-frame cheaply).
const CLAY = { node: [163, 78, 40], pulse: [217, 111, 58], line: "179,85,46", trail: "217,111,58", edge: "124,86,52", ring: "#b3552e", ink: "rgba(42,33,24,.85)" };
const GREEN = { rgb: [63, 125, 78], line: "63,125,78", hex: "#3f7d4e" };
const BG0 = "#fdfaf3", BG1 = "#f3ecdd";

function buildGraph(lite) {
  const seed = { s: 42 };
  const R = () => { seed.s = (seed.s * 1664525 + 1013904223) % 4294967296; return seed.s / 4294967296; };
  const gauss = () => (R() + R() + R() - 1.5) * 0.82;
  const SATS = lite ? 12 : 21, LINKS = lite ? 4 : 8, DUST = lite ? 90 : 170;

  const nodes = [], edges = [];
  nodes.push({ x: 0, y: 0, z: 0, r: 5.4, hub: true, name: CENTER.name, label: CENTER.label, ph: R() * 6.28 });

  const hubIdx = [];
  for (let i = 0; i < PRODUCTS.length; i++) {
    const a = (i / PRODUCTS.length) * Math.PI * 2 + 0.3;
    const rad = 0.55 + (R() - 0.5) * 0.14;
    nodes.push({
      x: Math.cos(a) * rad + (R() - 0.5) * 0.08,
      y: Math.sin(i * 2.4) * 0.26 + (R() - 0.5) * 0.08,
      z: Math.sin(a) * rad + (R() - 0.5) * 0.08,
      r: 3.4, hub: true, name: PRODUCTS[i].name, label: PRODUCTS[i].label, ph: R() * 6.28,
    });
    hubIdx.push(nodes.length - 1);
  }

  hubIdx.forEach((hi) => {
    const h = nodes[hi], firstSat = nodes.length;
    for (let k = 0; k < SATS; k++) {
      nodes.push({ x: h.x + gauss() * 0.19, y: h.y + gauss() * 0.19, z: h.z + gauss() * 0.19, r: 1.1 + R() * 1.1, hub: false, ph: R() * 6.28 });
      edges.push([hi, nodes.length - 1]);
    }
    for (let m = 0; m < LINKS; m++) {
      const p = firstSat + Math.floor(R() * SATS), q = firstSat + Math.floor(R() * SATS);
      if (p !== q) edges.push([p, q]);
    }
  });

  // faint structural spine for shape; traffic is NOT limited to it
  hubIdx.forEach((hi) => edges.push([0, hi]));
  for (let j = 0; j < hubIdx.length; j++) edges.push([hubIdx[j], hubIdx[(j + 1) % hubIdx.length]]);

  const dust = [];
  for (let d = 0; d < DUST; d++) {
    const u = R() * 2 - 1, th = R() * Math.PI * 2, rr = 0.78 + R() * 0.32;
    const s2 = Math.sqrt(1 - u * u);
    dust.push({ x: s2 * Math.cos(th) * rr, y: u * rr, z: s2 * Math.sin(th) * rr, r: 0.7 + R() * 0.6, ph: R() * 6.28 });
  }
  return { nodes, edges, dust, hubIdx: [0].concat(hubIdx) };
}

function makeSprite(rgb, coreA, haloA) {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const x = c.getContext("2d");
  const grad = x.createRadialGradient(32, 32, 0, 32, 32, 32);
  grad.addColorStop(0, `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${coreA})`);
  grad.addColorStop(0.25, `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${haloA})`);
  grad.addColorStop(1, `rgba(${rgb[0]},${rgb[1]},${rgb[2]},0)`);
  x.fillStyle = grad;
  x.fillRect(0, 0, 64, 64);
  return c;
}

export function ConstellationHero({ children }) {
  const stageRef = useRef(null);
  const canvasRef = useRef(null);
  const cardRefs = [useRef(null), useRef(null), useRef(null)];

  useEffect(() => {
    const stage = stageRef.current, canvas = canvasRef.current;
    if (!stage || !canvas) return;
    const ctx = canvas.getContext("2d");
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const lite = window.innerWidth < 640;

    const G = buildGraph(lite);
    const COMMAND_I = G.hubIdx[0], GREENLIGHT_I = G.hubIdx[4], SECURITY_I = G.hubIdx[10];
    const sprite = makeSprite(CLAY.node, 0.95, 0.22);
    const spritePulse = makeSprite(CLAY.pulse, 0.95, 0.5);
    const spriteSec = makeSprite(GREEN.rgb, 0.95, 0.5);

    let W = 0, H = 0, cx = 0, cy = 0, R0 = 0;
    const resize = () => {
      const DPR = Math.min(window.devicePixelRatio || 1, 2);
      W = canvas.clientWidth; H = canvas.clientHeight;
      canvas.width = W * DPR; canvas.height = H * DPR;
      ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
      cx = W / 2; cy = H * 0.44; R0 = Math.min(W, H) * 0.47;
    };
    resize();

    const TILT = 0.3, PERSP = 3.2;
    const projCache = [];
    const project = (p, rot, t, out) => {
      const wob = 0.012;
      const px = p.x + Math.sin(t * 0.0006 + p.ph) * wob;
      const py = p.y + Math.cos(t * 0.0005 + p.ph * 1.7) * wob;
      const pz = p.z + Math.sin(t * 0.0007 + p.ph * 0.6) * wob;
      const cR = Math.cos(rot), sR = Math.sin(rot);
      const x = px * cR + pz * sR, z = -px * sR + pz * cR;
      const cT = Math.cos(TILT), sT = Math.sin(TILT);
      const y2 = py * cT - z * sT, z2 = py * sT + z * cT;
      const k = PERSP / (PERSP - z2);
      out.x = cx + x * R0 * k; out.y = cy - y2 * R0 * k; out.z = z2; out.k = k;
      return out;
    };
    const dist3 = (a, b) => {
      const na = G.nodes[a], nb = G.nodes[b];
      return Math.hypot(na.x - nb.x, na.y - nb.y, na.z - nb.z);
    };

    // ---- any-to-any product routes (transient synapses) ----
    const routes = [], flashes = {};
    let secFlash = -1e9, pendingGl = null;
    const pickDest = (from, cameFrom) => {
      if (Math.random() < 0.3) {
        const sink = Math.random() < 0.5 ? GREENLIGHT_I : COMMAND_I;
        if (sink !== from && sink !== cameFrom) return sink;
      }
      for (let tries = 8; tries--; ) {
        const d = G.hubIdx[Math.floor(Math.random() * G.hubIdx.length)];
        if (d !== from && d !== cameFrom) return d;
      }
      return null;
    };
    const spawnRoute = (from, cameFrom, opts) => {
      if (routes.length > 30) return;
      const dest = opts && opts.dest !== undefined ? opts.dest : pickDest(from, cameFrom);
      if (dest === null || dest === from) return;
      routes.push({ a: from, b: dest, t: 0, sp: (0.010 + Math.random() * 0.006) / Math.max(0.35, dist3(from, dest)), sec: !!(opts && opts.sec), gl: !!(opts && opts.gl) });
    };
    const clusterPulses = [];
    const spawnClusterPulse = () => {
      if (clusterPulses.length > 24) return;
      clusterPulses.push({ e: Math.floor(Math.random() * G.edges.length), t: 0, sp: 0.012 + Math.random() * 0.008 });
    };
    for (let s = 0; s < 10; s++) spawnRoute(G.hubIdx[Math.floor(Math.random() * G.hubIdx.length)], null);
    for (let s = 0; s < 10; s++) spawnClusterPulse();
    const hubBurst = () => {
      for (let hb = 0; hb < G.hubIdx.length; hb++) {
        if (Math.random() > 0.35) continue;
        spawnRoute(G.hubIdx[hb], null);
      }
    };

    // ---- activity cards: slots 0-1 rotate products, slot 2 is Security ----
    const cards = [null, null, null];
    const msgCursor = {};
    const CARD_LIFE = 3200, SEC_CARD_LIFE = 4200, CARD_EVERY = 1700;
    let lastCard = -CARD_EVERY, nextSlot = 0, secMsgI = 0;

    // Cards must never enter the hero text block. The block's height depends on
    // viewport (multi-line serif headline), so measure its real top each pass
    // instead of trusting a fixed fraction.
    const overlayFirst = stage.querySelector(".lp-constellation-overlay > *");
    const fenceY = () => {
      if (!overlayFirst) return H * 0.56;
      const top = overlayFirst.getBoundingClientRect().top - stage.getBoundingClientRect().top;
      return Math.min(Math.max(120, top - 14), H);
    };

    const spawnCard = (t) => {
      const fy = fenceY();
      for (let tries = 6; tries--; ) {
        const hi = G.hubIdx[Math.floor(Math.random() * G.hubIdx.length)];
        const hp = projCache[hi];
        if (!hp || hp.z < -0.05 || hp.y > fy - 60) continue;
        const other = cards[1 - nextSlot];
        if (other && other.hub === hi) continue;
        const name = G.nodes[hi].name;
        const list = AGENT_MSGS[name];
        msgCursor[name] = ((msgCursor[name] || 0) + 1) % list.length;
        const el = cardRefs[nextSlot].current;
        if (!el) return;
        el.querySelector(".aname").textContent = G.nodes[hi].label;
        el.querySelector(".abody").innerHTML = list[msgCursor[name]];
        cards[nextSlot] = { hub: hi, born: t, life: CARD_LIFE };
        nextSlot = 1 - nextSlot;
        return;
      }
    };
    const spawnSecCard = (t) => {
      const el = cardRefs[2].current;
      if (!el) return;
      secMsgI = (secMsgI + 1) % SEC_MSGS.length;
      el.querySelector(".abody").innerHTML = SEC_MSGS[secMsgI];
      cards[2] = { hub: SECURITY_I, born: t, life: SEC_CARD_LIFE };
    };
    const layoutCards = (t) => {
      const fy = fenceY();
      for (let ci = 0; ci < 3; ci++) {
        const c = cards[ci], el = cardRefs[ci].current;
        if (!el) continue;
        if (!c) { el.style.opacity = "0"; continue; }
        const age = t - c.born;
        if (age > c.life) { cards[ci] = null; el.style.opacity = "0"; continue; }
        const hp = projCache[c.hub];
        const w = el.offsetWidth || 230, h = el.offsetHeight || 58;
        const fade = Math.min(1, age / 260) * Math.min(1, (c.life - age) / 420);
        const lift = (1 - Math.min(1, age / 260)) * 7;
        const side = hp.x < W / 2 ? 1 : -1;
        let ex = hp.x + (side === 1 ? 18 : -18 - w);
        let ey = hp.y + 16 - lift;
        ex = Math.max(10, Math.min(W - w - 10, ex));
        ey = Math.max(10, Math.min(fy - h, ey));
        for (let oj = 0; oj < 3; oj++) {
          if (oj === ci) continue;
          const other = cards[oj];
          if (other && other.ex !== undefined) {
            const oX = ex < other.ex + other.w + 8 && other.ex < ex + w + 8;
            const oY = ey < other.ey + other.h + 8 && other.ey < ey + h + 8;
            if (oX && oY) {
              // dodge below unless that crosses the text fence, then dodge above
              const below = other.ey + other.h + 12;
              ey = below + h <= fy ? below : Math.max(10, other.ey - h - 12);
            }
          }
        }
        el.style.opacity = fade.toFixed(2);
        el.style.transform = `translate(${ex.toFixed(1)}px,${ey.toFixed(1)}px)`;
        c.ex = ex; c.ey = ey; c.w = w; c.h = h; c.fade = fade; c.side = side;
      }
    };

    const drawShield = (x, y, s, alpha) => {
      ctx.globalAlpha = alpha;
      ctx.strokeStyle = GREEN.hex; ctx.lineWidth = 1.8;
      ctx.beginPath();
      ctx.moveTo(x, y - s * 0.55);
      ctx.bezierCurveTo(x + s * 0.38, y - s * 0.44, x + s * 0.5, y - s * 0.38, x + s * 0.5, y - s * 0.12);
      ctx.bezierCurveTo(x + s * 0.5, y + s * 0.26, x + s * 0.26, y + s * 0.5, x, y + s * 0.6);
      ctx.bezierCurveTo(x - s * 0.26, y + s * 0.5, x - s * 0.5, y + s * 0.26, x - s * 0.5, y - s * 0.12);
      ctx.bezierCurveTo(x - s * 0.5, y - s * 0.38, x - s * 0.38, y - s * 0.44, x, y - s * 0.55);
      ctx.closePath(); ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(x - s * 0.2, y + s * 0.02);
      ctx.lineTo(x - s * 0.04, y + s * 0.2);
      ctx.lineTo(x + s * 0.26, y - s * 0.18);
      ctx.stroke();
      ctx.lineWidth = 0.7; ctx.globalAlpha = 1;
    };

    let lastBurst = 0, lastCluster = 0, lastSec = -6000;

    const frame = (t) => {
      const rot = reduced ? 0.6 : t * 0.00009;
      ctx.clearRect(0, 0, W, H);

      const bg = ctx.createRadialGradient(cx, cy, R0 * 0.1, cx, cy, Math.max(W, H) * 0.75);
      bg.addColorStop(0, BG0); bg.addColorStop(1, BG1);
      ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);

      for (let d = 0; d < G.dust.length; d++) {
        const dp = project(G.dust[d], rot * 0.55, t, {});
        ctx.globalAlpha = 0.05 + 0.1 * (dp.z + 1) / 2;
        const ds = G.dust[d].r * 4 * dp.k;
        ctx.drawImage(sprite, dp.x - ds / 2, dp.y - ds / 2, ds, ds);
      }
      ctx.globalAlpha = 1;

      for (let i = 0; i < G.nodes.length; i++) projCache[i] = project(G.nodes[i], rot, t, projCache[i] || {});

      ctx.lineWidth = 0.7;
      for (let e = 0; e < G.edges.length; e++) {
        const a = projCache[G.edges[e][0]], b = projCache[G.edges[e][1]];
        const depth = ((a.z + b.z) / 2 + 1) / 2;
        ctx.strokeStyle = `rgba(${CLAY.edge},${(0.05 + depth * 0.16).toFixed(3)})`;
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
      }

      for (let r = routes.length - 1; r >= 0; r--) {
        const ru = routes[r];
        if (!reduced) ru.t += ru.sp; else ru.t = 0.5;
        const pa = projCache[ru.a], pb = projCache[ru.b];
        if (ru.t >= 1) {
          flashes[ru.b] = t;
          const wasSec = ru.sec, wasGl = ru.gl, contA = ru.b, cameA = ru.a;
          routes.splice(r, 1);
          if (wasSec) {
            secFlash = t;
            spawnSecCard(t);
            pendingGl = t + 650;
          } else if (!wasGl) {
            if (Math.random() < 0.55) spawnRoute(contA, cameA);
            if (Math.random() < 0.15) spawnRoute(contA, cameA);
          }
          continue;
        }
        const depthR = ((pa.z + pb.z) / 2 + 1) / 2;
        const lineC = ru.sec || ru.gl ? GREEN.line : CLAY.line;
        const trailC = ru.sec || ru.gl ? GREEN.line : CLAY.trail;
        ctx.strokeStyle = `rgba(${lineC},${(0.04 + depthR * 0.07).toFixed(3)})`;
        ctx.lineWidth = 0.8;
        ctx.beginPath(); ctx.moveTo(pa.x, pa.y); ctx.lineTo(pb.x, pb.y); ctx.stroke();
        const t0 = Math.max(0, ru.t - 0.16);
        const x0 = pa.x + (pb.x - pa.x) * t0, y0 = pa.y + (pb.y - pa.y) * t0;
        const x1 = pa.x + (pb.x - pa.x) * ru.t, y1 = pa.y + (pb.y - pa.y) * ru.t;
        ctx.strokeStyle = `rgba(${trailC},${(0.18 + depthR * 0.25).toFixed(3)})`;
        ctx.lineWidth = 1.3;
        ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
        ctx.lineWidth = 0.7;
        const gz = (pa.z + (pb.z - pa.z) * ru.t + 1) / 2;
        const gs = 8 * (0.6 + gz * 0.7);
        ctx.globalAlpha = 0.4 + gz * 0.6;
        ctx.drawImage(ru.sec || ru.gl ? spriteSec : spritePulse, x1 - gs / 2, y1 - gs / 2, gs, gs);
        ctx.globalAlpha = 1;
      }

      for (let q = clusterPulses.length - 1; q >= 0; q--) {
        const pu = clusterPulses[q];
        if (!reduced) pu.t += pu.sp; else pu.t = 0.5;
        if (pu.t >= 1) { clusterPulses.splice(q, 1); continue; }
        const eg = G.edges[pu.e], qa = projCache[eg[0]], qb = projCache[eg[1]];
        const qx = qa.x + (qb.x - qa.x) * pu.t, qy = qa.y + (qb.y - qa.y) * pu.t;
        const qz = (qa.z + (qb.z - qa.z) * pu.t + 1) / 2;
        const qs = 5.5 * (0.6 + qz * 0.7);
        ctx.globalAlpha = 0.25 + qz * 0.45;
        ctx.drawImage(spritePulse, qx - qs / 2, qy - qs / 2, qs, qs);
      }
      ctx.globalAlpha = 1;

      const order = [];
      for (let n = 0; n < G.nodes.length; n++) order.push(n);
      order.sort((u, v) => projCache[u].z - projCache[v].z);
      for (let o = 0; o < order.length; o++) {
        const idx = order[o], nd = G.nodes[idx], pc = projCache[idx];
        const depthA = 0.22 + 0.78 * (pc.z + 1) / 2;
        const size = nd.r * 5.2 * pc.k;
        ctx.globalAlpha = depthA;
        ctx.drawImage(sprite, pc.x - size / 2, pc.y - size / 2, size, size);
        if (nd.hub) {
          const fAge = flashes[idx] !== undefined ? t - flashes[idx] : 1e9;
          if (fAge < 600) {
            const fr = size / 2 + (fAge / 600) * 24;
            ctx.globalAlpha = (1 - fAge / 600) * 0.55;
            ctx.strokeStyle = idx === SECURITY_I && t - secFlash < 1200 ? GREEN.hex : CLAY.ring;
            ctx.lineWidth = 1.4;
            ctx.beginPath(); ctx.arc(pc.x, pc.y, fr, 0, Math.PI * 2); ctx.stroke();
            ctx.lineWidth = 0.7;
          }
          ctx.globalAlpha = Math.min(1, depthA + 0.15);
          ctx.fillStyle = CLAY.ink;
          ctx.font = `600 ${Math.round((lite ? 8.5 : 9.5) * Math.min(pc.k, 1.25))}px "JetBrains Mono", ui-monospace, monospace`;
          ctx.textAlign = "center";
          if (pc.z > -0.25) ctx.fillText(nd.label, pc.x, pc.y - size / 2 - 5);
        }
      }
      ctx.globalAlpha = 1;

      const sAge = t - secFlash;
      if (sAge < 1200) {
        const sp2 = projCache[SECURITY_I];
        const k1 = sAge / 1200;
        for (let ring = 0; ring < 2; ring++) {
          ctx.globalAlpha = (1 - k1) * (0.5 - ring * 0.18);
          ctx.strokeStyle = GREEN.hex; ctx.lineWidth = 1.6;
          ctx.beginPath(); ctx.arc(sp2.x, sp2.y, 12 + k1 * 34 + ring * 9, 0, Math.PI * 2); ctx.stroke();
        }
        ctx.lineWidth = 0.7; ctx.globalAlpha = 1;
        drawShield(sp2.x, sp2.y - 34, 15, Math.min(1, (1 - k1) * 2.2));
      }

      for (let ci = 0; ci < 3; ci++) {
        const c = cards[ci];
        if (!c || c.fade === undefined || c.ex === undefined) continue;
        const hp = projCache[c.hub];
        const sx = c.side === 1 ? c.ex + 10 : c.ex + c.w - 10;
        ctx.globalAlpha = 0.5 * c.fade;
        ctx.strokeStyle = ci === 2 ? GREEN.hex : CLAY.ring; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(hp.x, hp.y); ctx.lineTo(sx, c.ey + 1); ctx.stroke();
        ctx.beginPath(); ctx.arc(hp.x, hp.y, 2.4, 0, Math.PI * 2);
        ctx.fillStyle = ci === 2 ? GREEN.hex : CLAY.ring; ctx.fill();
        ctx.lineWidth = 0.7;
      }
      ctx.globalAlpha = 1;

      if (!reduced) {
        if (t - lastBurst > 760) { lastBurst = t; hubBurst(); }
        if (t - lastCluster > 700) { lastCluster = t; spawnClusterPulse(); }
        if (t - lastCard > CARD_EVERY) { lastCard = t; spawnCard(t); }
        if (t - lastSec > 9000) {
          lastSec = t;
          let src;
          for (let tr = 8; tr--; ) {
            src = G.hubIdx[Math.floor(Math.random() * G.hubIdx.length)];
            if (src !== SECURITY_I && src !== GREENLIGHT_I) break;
          }
          spawnRoute(src, null, { dest: SECURITY_I, sec: true });
        }
        if (pendingGl !== null && t > pendingGl) {
          pendingGl = null;
          spawnRoute(SECURITY_I, null, { dest: GREENLIGHT_I, gl: true });
        }
      } else if (!cards[0]) {
        spawnCard(t); lastCard = t;
      }
      layoutCards(t);
    };

    // ---- run loop only while visible: on-screen AND tab focused ----
    let raf = 0, running = false, onScreen = true, destroyed = false;
    const loop = (t) => {
      if (!running || destroyed) return;
      frame(t);
      raf = requestAnimationFrame(loop);
    };
    const setRunning = () => {
      const want = onScreen && document.visibilityState === "visible" && !reduced;
      if (want && !running) { running = true; raf = requestAnimationFrame(loop); }
      else if (!want && running) { running = false; cancelAnimationFrame(raf); }
    };
    const io = new IntersectionObserver((entries) => { onScreen = entries[0].isIntersecting; setRunning(); });
    io.observe(stage);
    const onVis = () => setRunning();
    document.addEventListener("visibilitychange", onVis);
    const onResize = () => resize();
    window.addEventListener("resize", onResize);

    if (reduced) {
      // single static, fully-formed frame; no loop
      frame(2400);
      frame(2400);
    } else {
      setRunning();
    }

    return () => {
      destroyed = true; running = false;
      cancelAnimationFrame(raf);
      io.disconnect();
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  return (
    <div className="lp-constellation" ref={stageRef}>
      <div className="lp-constellation-fx" aria-hidden="true">
        <canvas ref={canvasRef} />
        <div className="lp-cstl-live"><i />LIVE · YOUR AGENT NETWORK</div>
        <div className="lp-acard" ref={cardRefs[0]}><div className="ahead"><span className="aname" /><span className="atime">just now</span></div><div className="abody" /></div>
        <div className="lp-acard" ref={cardRefs[1]}><div className="ahead"><span className="aname" /><span className="atime">just now</span></div><div className="abody" /></div>
        <div className="lp-acard sec" ref={cardRefs[2]}><div className="ahead"><span className="aname">SECURITY · GUARDRAIL</span><span className="atime">intercepted</span></div><div className="abody" /></div>
      </div>
      <div className="lp-constellation-overlay">{children}</div>
    </div>
  );
}

export default ConstellationHero;
