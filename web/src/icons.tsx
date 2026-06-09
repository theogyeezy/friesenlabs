// @ts-nocheck
import React from "react";
// icons.jsx, minimal stroke icon set (Lucide-style functional UI glyphs)
const Icon = ({ name, size = 18, sw = 1.8, style, className }) => {
  const P = ICON_PATHS[name] || ICON_PATHS.dot;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round"
      style={style} className={className} aria-hidden="true">
      {P}
    </svg>
  );
};

const ICON_PATHS = {
  dot: <circle cx="12" cy="12" r="3" />,
  grid: <><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/></>,
  spark: <><path d="M12 3v3M12 18v3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M3 12h3M18 12h3M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/><circle cx="12" cy="12" r="3.2"/></>,
  star: <path d="M12 3.5l2.6 5.3 5.9.9-4.2 4.1 1 5.8-5.3-2.8-5.3 2.8 1-5.8L3.5 9.7l5.9-.9L12 3.5Z"/>,
  starOff: <path d="M12 3.5l2.6 5.3 5.9.9-4.2 4.1 1 5.8-5.3-2.8-5.3 2.8 1-5.8L3.5 9.7l5.9-.9L12 3.5Z" fill="none"/>,
  workflow: <><rect x="3" y="3" width="6" height="6" rx="1.5"/><rect x="15" y="15" width="6" height="6" rx="1.5"/><path d="M9 6h4a2 2 0 0 1 2 2v7"/></>,
  users: <><circle cx="9" cy="8" r="3.2"/><path d="M3.5 20a5.5 5.5 0 0 1 11 0"/><path d="M16 5.2a3.2 3.2 0 0 1 0 6"/><path d="M18.5 20a5.5 5.5 0 0 0-3-4.9"/></>,
  check: <path d="M5 12.5l4.5 4.5L19 7"/>,
  checkCircle: <><circle cx="12" cy="12" r="9"/><path d="M8.5 12.5l2.4 2.4 4.6-5"/></>,
  x: <path d="M6 6l12 12M18 6L6 18"/>,
  xCircle: <><circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/></>,
  search: <><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></>,
  megaphone: <><path d="M3 11v2a1 1 0 0 0 1 1h2l4 4V6L6 10H4a1 1 0 0 0-1 1Z"/><path d="M14 8a4 4 0 0 1 0 8M10 18l1 3"/></>,
  bell: <><path d="M6 9a6 6 0 0 1 12 0c0 5 2 6 2 6H4s2-1 2-6Z"/><path d="M10 19a2 2 0 0 0 4 0"/></>,
  bolt: <path d="M13 2L4.5 13.5H11l-1 8.5L19.5 10.5H13l0-8.5Z"/>,
  arrowUp: <path d="M12 19V5M6 11l6-6 6 6"/>,
  arrowDown: <path d="M12 5v14M6 13l6 6 6-6"/>,
  arrowRight: <path d="M5 12h14M13 6l6 6-6 6"/>,
  chevR: <path d="M9 6l6 6-6 6"/>,
  chevL: <path d="M15 6l-6 6 6 6"/>,
  chevDown: <path d="M6 9l6 6 6-6"/>,
  plus: <path d="M12 5v14M5 12h14"/>,
  filter: <path d="M3 5h18l-7 8v6l-4 2v-8L3 5Z"/>,
  sort: <path d="M7 4v16M7 4L4 7M7 4l3 3M17 20V4M17 20l3-3M17 20l-3-3"/>,
  inbox: <><path d="M3 13h5l1.5 3h5L16 13h5"/><path d="M5 5h14l2 8v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4L5 5Z"/></>,
  mail: <><rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M4 7l8 6 8-6"/></>,
  phone: <path d="M5 4h3l1.5 4-2 1.5a11 11 0 0 0 5 5l1.5-2 4 1.5V18a2 2 0 0 1-2.2 2A15 15 0 0 1 4 6.2 2 2 0 0 1 5 4Z"/>,
  calendar: <><rect x="3.5" y="5" width="17" height="16" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/></>,
  clock: <><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></>,
  building: <><rect x="5" y="3" width="14" height="18" rx="1.5"/><path d="M9 7h2M13 7h2M9 11h2M13 11h2M9 15h2M13 15h2M10 21v-3h4v3"/></>,
  doc: <><path d="M7 3h7l5 5v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z"/><path d="M14 3v5h5M9 13h6M9 16h4"/></>,
  trend: <path d="M3 16l5-5 4 4 9-9M21 6h-5M21 6v5"/>,
  settings: <><circle cx="12" cy="12" r="3"/><path d="M12 2.5l1.6 2.3 2.7-.7.5 2.8 2.6 1.1-1.2 2.5 1.2 2.5-2.6 1.1-.5 2.8-2.7-.7L12 21.5l-1.6-2.3-2.7.7-.5-2.8-2.6-1.1 1.2-2.5L4.6 11l2.6-1.1.5-2.8 2.7.7L12 2.5Z"/></>,
  sidebar: <><rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M9 4v16"/></>,
  send: <path d="M4 12l16-7-7 16-2.5-6.5L4 12Z"/>,
  target: <><circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="1"/></>,
  flame: <path d="M12 3s5 4 5 9a5 5 0 0 1-10 0c0-1.5.7-2.8 1.5-3.5C9 10 9.5 11 10 11c0-2 1-5 2-8Z"/>,
  cmd: <path d="M9 9V7.5A2.5 2.5 0 1 0 6.5 10H9m0-1v6m0-6h6m-6 6v1.5A2.5 2.5 0 1 0 17.5 14H15m0 1V9m0 6h1.5A2.5 2.5 0 1 0 14 6.5V9m1 0H9"/>,
  sun: <><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4 12H2M22 12h-2M5.6 5.6L4.2 4.2M19.8 19.8l-1.4-1.4M5.6 18.4l-1.4 1.4M19.8 4.2l-1.4 1.4"/></>,
  note: <><rect x="4" y="4" width="16" height="16" rx="2.5"/><path d="M8 9h8M8 13h5"/></>,
  layers: <path d="M12 3l9 5-9 5-9-5 9-5ZM3 13l9 5 9-5M3 16.5l9 5 9-5"/>,
  plug: <><path d="M9 3v5M15 3v5M7 8h10v2a5 5 0 0 1-10 0V8ZM12 15v6"/></>,
  link: <path d="M9.5 14.5l5-5M10 6.5l1-1a4 4 0 0 1 5.7 5.7l-1 1M14 17.5l-1 1a4 4 0 0 1-5.7-5.7l1-1"/>,
  shield: <path d="M12 3l7 3v5c0 4.6-3 7.7-7 9-4-1.3-7-4.4-7-9V6l7-3Z"/>,
  gauge: <><path d="M12 13l3.5-3.5"/><path d="M4.5 17a8 8 0 1 1 15 0"/></>,
  pause: <><rect x="7" y="5" width="3.4" height="14" rx="1"/><rect x="13.6" y="5" width="3.4" height="14" rx="1"/></>,
  play: <path d="M7 5l12 7-12 7V5Z"/>,
  sliders: <><path d="M4 8h9M19 8h1M4 16h1M11 16h9"/><circle cx="16" cy="8" r="2.4"/><circle cx="8" cy="16" r="2.4"/></>,
  puzzle: <path d="M10 4a2 2 0 1 1 4 0v2h3a1 1 0 0 1 1 1v3h-2a2 2 0 1 0 0 4h2v3a1 1 0 0 1-1 1h-3v-2a2 2 0 1 0-4 0v2H7a1 1 0 0 1-1-1v-3H4a2 2 0 1 1 0-4h2V7a1 1 0 0 1 1-1h3V4Z"/>,
  refresh: <path d="M20 11a8 8 0 0 0-14-4M4 5v3h3M4 13a8 8 0 0 0 14 4M20 19v-3h-3"/>,
  menu: <path d="M4 6h16M4 12h16M4 18h16"/>,
  linkedin: <><rect x="3" y="3" width="18" height="18" rx="3"/><path d="M7 10v7M7 7v.01M11 17v-4a2 2 0 0 1 4 0v4M11 17v-7"/></>,
  instagram: <><rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="3.5"/><circle cx="17.5" cy="6.5" r="1"/></>,
  quote: <path d="M7 7h4v6c0 2-1 3-3 4M14 7h4v6c0 2-1 3-3 4"/>,
  network: <><circle cx="12" cy="5" r="2.4"/><circle cx="5" cy="18" r="2.4"/><circle cx="19" cy="18" r="2.4"/><path d="M12 7.4v3M11 12l-4.2 4M13 12l4.2 4"/><circle cx="12" cy="12" r="2.2"/></>,
  trophy: <><path d="M7 4h10v4a5 5 0 0 1-10 0V4Z"/><path d="M7 6H4.5a2.5 2.5 0 0 0 4 2M17 6h2.5a2.5 2.5 0 0 1-4 2M9 17h6M10 21h4M12 13v4"/></>,
  flame2: <path d="M12 3s5 4 5 9a5 5 0 0 1-10 0c0-1.5.7-2.8 1.5-3.5C9 10 9.5 11 10 11c0-2 1-5 2-8Z"/>,
  history: <><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 4v4h4M12 8v4l3 2"/></>,
  lock: <><rect x="4.5" y="10.5" width="15" height="10" rx="2.2"/><path d="M8 10.5V7a4 4 0 0 1 8 0v3.5"/></>,
  unlock: <><rect x="4.5" y="10.5" width="15" height="10" rx="2.2"/><path d="M8 10.5V7a4 4 0 0 1 7.5-1.9"/></>,
};

window.Icon = Icon;

// Friesen Labs logomark, a hub with agents orbiting it
function Logo({ size = 20 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <ellipse cx="12" cy="12" rx="10" ry="5.1" transform="rotate(-32 12 12)" stroke="#fff" strokeWidth="1.7" opacity="0.92" />
      <circle cx="12" cy="12" r="3.7" fill="#fff" />
      <circle cx="20.4" cy="6.9" r="1.85" fill="#fff" />
      <circle cx="3.6" cy="17.1" r="1.25" fill="#fff" opacity="0.85" />
    </svg>
  );
}
window.Logo = Logo;
