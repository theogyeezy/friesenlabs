// @ts-nocheck
import React from "react";
// gamify.jsx, confetti + XP HUD
function confettiBurst(x, y) {
  const colors = ["#5b53e8", "#e0653f", "#2ca05a", "#e8a33d", "#d24d6a", "#3d8fe0", "#9b5de0"];
  for (let i = 0; i < 56; i++) {
    const p = document.createElement("div");
    const sz = 6 + Math.random() * 7;
    p.style.cssText = `position:fixed;left:${x}px;top:${y}px;width:${sz}px;height:${sz * 0.55}px;background:${colors[i % colors.length]};z-index:99999;border-radius:2px;pointer-events:none;`;
    document.body.appendChild(p);
    const ang = Math.random() * Math.PI * 2, dist = 70 + Math.random() * 210;
    const dx = Math.cos(ang) * dist, dy = Math.sin(ang) * dist - (40 + Math.random() * 130);
    p.animate([{ transform: "translate(0,0) rotate(0)", opacity: 1 }, { transform: `translate(${dx}px,${dy + 320}px) rotate(${Math.random() * 720 - 360}deg)`, opacity: 0 }], { duration: 1000 + Math.random() * 700, easing: "cubic-bezier(.2,.6,.3,1)" }).onfinish = () => p.remove();
  }
}
window.confettiBurst = confettiBurst;

function XPBadge() {
  const points = useStore((s) => s.points);
  const last = useStore((s) => s.lastAward);
  const [floats, setFloats] = React.useState([]);
  const prevK = React.useRef(null);
  React.useEffect(() => {
    if (last && last.k && last.k !== prevK.current) {
      prevK.current = last.k; const id = last.k;
      setFloats((f) => [...f, { id, n: last.n }]);
      setTimeout(() => setFloats((f) => f.filter((x) => x.id !== id)), 1200);
    }
  }, [last]);
  const level = Math.floor(points / 500) + 1, into = points % 500, pct = into / 500 * 100;
  return (
    <div className="xp-hud" title={`${points.toLocaleString()} points · Level ${level}`} style={{ position: "relative", display: "flex", alignItems: "center", gap: 9, height: 38, padding: "0 13px", borderRadius: "var(--r-md)", background: "var(--surface)", border: "1px solid var(--line)" }}>
      <span style={{ fontSize: 15 }}>⭐</span>
      <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 66 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10.5, fontWeight: 700, fontFamily: "var(--mono)" }}><span>Lv {level}</span><span style={{ color: "var(--ink-3)" }}>{points.toLocaleString()}</span></div>
        <div style={{ height: 4, borderRadius: 99, background: "var(--surface-2)", overflow: "hidden" }}><span style={{ display: "block", height: "100%", width: pct + "%", background: "var(--accent)", transition: "width .5s cubic-bezier(.2,.7,.2,1)" }} /></div>
      </div>
      {floats.map((f) => <span key={f.id} style={{ position: "absolute", right: 10, top: -4, fontSize: 13, fontWeight: 800, color: "var(--green)", animation: "xpfloat 1.2s ease-out forwards", pointerEvents: "none" }}>+{f.n}</span>)}
    </div>
  );
}
window.XPBadge = XPBadge;
