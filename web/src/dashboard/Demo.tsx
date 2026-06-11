// Minimal demo mount for the dashboard renderer, reachable at ?view=dashboard-demo.
//
// Four modes:
//   * "valid"   — the original v1 sampleSpec (kpi + chart). Proves v1 specs
//                 keep rendering unchanged under the v2 renderer.
//   * "v2"      — sampleSpecV2: every spec_version 2 catalog component
//                 (funnel, leaderboard, stat-with-sparkline, cohort-grid,
//                 markdown-note) on the grid/span layout.
//   * "future"  — a valid spec containing ONE component type from a newer
//                 catalog than this client: the renderer must draw the known
//                 blocks and an inert "Panel not supported" placeholder for the
//                 unknown one (graceful rejection), interpreting nothing in it.
//   * "invalid" — a malicious spec (unknown type smuggling raw HTML AND a
//                 non-vega-lite chart encoding). The renderer must refuse it
//                 with the safe fallback, proving spec-not-code: no injected
//                 markup ever reaches the DOM.
// Data is the offline sampleLoadData stub (no real network).

import React from "react";
import { SpecRenderer } from "./SpecRenderer";
import { sampleSpec, sampleSpecV2, sampleLoadData } from "./sample";

const { useState } = React;

// An invalid spec. Every field below is an attack the catalog refuses:
//  - an "html" component type that is not in the catalog,
//  - a chart encoding that is not "vega-lite",
//  - raw HTML / <script> strings and an onerror handler as data.
// validateViewSpec rejects it outright; even if it did not, the renderer has no
// HTML sink, so these strings could only ever appear as escaped text.
const maliciousSpec = {
  view_id: "evil",
  title: "Injection attempt",
  semantic_refs: ["Deals.count"],
  layout: [
    {
      type: "html",
      html: "<img src=x onerror=\"window.__pwned=true\">",
      content: "<script>window.__pwned=true</script>",
    },
    {
      type: "chart",
      encoding: "raw-html",
      query: { measures: ["Deals.count"] },
    },
  ],
};

// A FORWARD-COMPATIBLE spec: valid v2 spec whose middle block is a component
// type this client does not know ("holo-globe", from some future catalog).
// Even its payload strings are attack-shaped to prove the placeholder never
// interprets unknown block content.
const futureCatalogSpec = {
  view_id: "future",
  title: "From a newer catalog",
  spec_version: 2,
  semantic_refs: ["Deals.count"],
  layout: [
    { type: "kpi", title: "Open deals", metric: "Deals.count", span: 4 },
    {
      type: "holo-globe",
      payload: "<script>window.__pwned_future=true</script>",
      span: 4,
    },
    {
      type: "markdown-note",
      title: "Note",
      body: "Known panels keep rendering around the unknown one.",
      span: 4,
    },
  ],
};

const MODES = [
  { id: "valid", label: "Valid v1 spec" },
  { id: "v2", label: "v2 spec" },
  { id: "future", label: "Future catalog" },
  { id: "invalid", label: "Invalid spec" },
] as const;

type Mode = (typeof MODES)[number]["id"];

const SPEC_BY_MODE: Record<Mode, unknown> = {
  valid: sampleSpec,
  v2: sampleSpecV2,
  future: futureCatalogSpec,
  invalid: maliciousSpec,
};

export default function DashboardDemo() {
  const [mode, setMode] = useState<Mode>("valid");

  return (
    <div style={{ maxWidth: 920, margin: "0 auto", padding: "40px 24px", fontFamily: "system-ui, sans-serif" }}>
      <div style={{ display: "flex", gap: 10, marginBottom: 24, alignItems: "center", flexWrap: "wrap" }}>
        <strong style={{ fontSize: 14 }}>Dashboard renderer demo</strong>
        {MODES.map((m) => (
          <button
            key={m.id}
            data-testid={`show-${m.id}`}
            onClick={() => setMode(m.id)}
            style={{
              padding: "6px 12px",
              borderRadius: 8,
              border: "1px solid #ccc",
              background: mode === m.id ? "#222" : "#fff",
              color: mode === m.id ? "#fff" : "#222",
              cursor: "pointer",
            }}
          >
            {m.label}
          </button>
        ))}
      </div>

      <SpecRenderer spec={SPEC_BY_MODE[mode]} loadData={sampleLoadData} />
    </div>
  );
}
