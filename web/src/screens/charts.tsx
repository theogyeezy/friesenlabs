// @ts-nocheck
import React from "react";
// charts.jsx, animated data-viz primitives
const { useState, useEffect, useRef } = React;

// count-up number
function useCountUp(target, dur = 1100, deps = []) {
  const [v, setV] = useState(0);
  useEffect(() => {
    let raf, start;
    const tick = (t) => {
      if (!start) start = t;
      const p = Math.min(1, (t - start) / dur);
      const e = 1 - Math.pow(1 - p, 3);
      setV(target * e);
      if (p < 1) raf = requestAnimationFrame(tick);
      else setV(target);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, ...deps]);
  return v;
}

function CountUp({ value, format = (n) => Math.round(n).toLocaleString(), dur }) {
  const v = useCountUp(value, dur);
  return <>{format(v)}</>;
}

// smooth area chart with draw-in + dual series
function AreaChart({ data, w = 640, h = 200, pad = 8 }) {
  const ref = useRef(null);
  const [drawn, setDrawn] = useState(false);
  useEffect(() => { const t = setTimeout(() => setDrawn(true), 80); return () => clearTimeout(t); }, []);

  const max = Math.max(...data.map(d => d.auto)) * 1.12;
  const iw = w - pad * 2, ih = h - pad * 2 - 18;
  const X = (i) => pad + (i / (data.length - 1)) * iw;
  const Y = (v) => pad + ih - (v / max) * ih;

  const smooth = (pts) => {
    let d = `M ${pts[0][0]} ${pts[0][1]}`;
    for (let i = 0; i < pts.length - 1; i++) {
      const [x0, y0] = pts[i], [x1, y1] = pts[i + 1];
      const cx = (x0 + x1) / 2;
      d += ` C ${cx} ${y0}, ${cx} ${y1}, ${x1} ${y1}`;
    }
    return d;
  };
  const autoPts = data.map((d, i) => [X(i), Y(d.auto)]);
  const humanPts = data.map((d, i) => [X(i), Y(d.human)]);
  const autoLine = smooth(autoPts);
  const humanLine = smooth(humanPts);
  const autoArea = `${autoLine} L ${X(data.length - 1)} ${pad + ih} L ${pad} ${pad + ih} Z`;

  return (
    <svg ref={ref} viewBox={`0 0 ${w} ${h}`} width="100%" style={{ display: "block", overflow: "visible" }}>
      <defs>
        <linearGradient id="areaFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.22" />
          <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
        </linearGradient>
      </defs>
      {[0.25, 0.5, 0.75, 1].map((g, i) => (
        <line key={i} x1={pad} x2={w - pad} y1={pad + ih * g} y2={pad + ih * g}
          stroke="var(--line-2)" strokeWidth="1" />
      ))}
      <path d={autoArea} fill="url(#areaFill)" style={{ opacity: drawn ? 1 : 0, transition: "opacity .9s .3s" }} />
      <path d={humanLine} fill="none" stroke="var(--ink-4)" strokeWidth="2" strokeDasharray="3 4"
        style={{ strokeDashoffset: drawn ? 0 : 1400, strokeDasharray: drawn ? "3 4" : "1400", transition: "stroke-dashoffset 1.3s ease" }} />
      <path d={autoLine} fill="none" stroke="var(--accent)" strokeWidth="2.6" strokeLinecap="round"
        style={{ strokeDasharray: 1600, strokeDashoffset: drawn ? 0 : 1600, transition: "stroke-dashoffset 1.4s cubic-bezier(.3,.7,.2,1)" }} />
      {autoPts.map((p, i) => (
        <circle key={i} cx={p[0]} cy={p[1]} r={i === data.length - 1 ? 4.5 : 0} fill="var(--accent)"
          stroke="var(--surface)" strokeWidth="2.5"
          style={{ opacity: drawn ? 1 : 0, transition: "opacity .4s 1.3s" }} />
      ))}
      {data.map((d, i) => (
        <text key={i} x={X(i)} y={h - 2} textAnchor="middle" fontSize="10"
          fill="var(--ink-4)" fontFamily="var(--mono)">{d.d}</text>
      ))}
    </svg>
  );
}

// tiny sparkline for stat cards
function Sparkline({ data, w = 84, h = 32, color = "var(--accent)" }) {
  const max = Math.max(...data), min = Math.min(...data);
  const rng = max - min || 1;
  const pts = data.map((v, i) => [(i / (data.length - 1)) * w, h - ((v - min) / rng) * (h - 4) - 2]);
  const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  const [drawn, setDrawn] = useState(false);
  useEffect(() => { const t = setTimeout(() => setDrawn(true), 200); return () => clearTimeout(t); }, []);
  return (
    <svg width={w} height={h} style={{ display: "block" }}>
      <path d={d} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
        style={{ strokeDasharray: 300, strokeDashoffset: drawn ? 0 : 300, transition: "stroke-dashoffset 1.1s ease" }} />
    </svg>
  );
}

// horizontal load bars
function LoadBars({ rows, agents }) {
  const [on, setOn] = useState(false);
  useEffect(() => { const t = setTimeout(() => setOn(true), 120); return () => clearTimeout(t); }, []);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 15 }}>
      {rows.map((r, i) => {
        const a = agents[r.agent];
        return (
          <div key={r.agent} style={{ display: "flex", alignItems: "center", gap: 11 }}>
            <div className="avatar" style={{ background: a.color, width: 28, height: 28, fontSize: 10 }}>{a.init}</div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 5 }}>
                <span style={{ fontSize: 12.5, fontWeight: 600 }}>{a.name}</span>
                <span style={{ fontSize: 11.5, color: "var(--ink-3)", fontFamily: "var(--mono)" }}>{r.tasks} tasks</span>
              </div>
              <div className="meter">
                <span style={{ width: on ? r.pct + "%" : "0%", background: a.color, transitionDelay: (i * 90) + "ms" }} />
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// donut for pipeline split
function Donut({ slices, size = 132, thick = 18 }) {
  const total = slices.reduce((s, x) => s + x.val, 0);
  const r = (size - thick) / 2;
  const C = 2 * Math.PI * r;
  const [on, setOn] = useState(false);
  useEffect(() => { const t = setTimeout(() => setOn(true), 120); return () => clearTimeout(t); }, []);
  let acc = 0;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ transform: "rotate(-90deg)" }}>
      {slices.map((s, i) => {
        const frac = s.val / total;
        const len = frac * C;
        const off = acc * C;
        acc += frac;
        return (
          <circle key={i} cx={size / 2} cy={size / 2} r={r} fill="none"
            stroke={s.color} strokeWidth={thick} strokeLinecap="round"
            strokeDasharray={`${on ? len - 3 : 0} ${C}`} strokeDashoffset={-off}
            style={{ transition: `stroke-dasharray .9s cubic-bezier(.3,.7,.2,1) ${i * 90}ms` }} />
        );
      })}
    </svg>
  );
}

Object.assign(window, { useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut });
