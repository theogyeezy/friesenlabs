// Minimal demo mount for the dashboard renderer, reachable at ?view=dashboard-demo.
//
// It shows the renderer against the valid sampleSpec and, via a toggle, against
// a deliberately malicious/invalid spec (an unknown component type that tries to
// smuggle a <script> and raw HTML). The renderer must refuse the latter and show
// the safe fallback, proving spec-not-code: no injected markup ever reaches the
// DOM. Data is the offline sampleLoadData stub (no real network).

import React from "react";
import { SpecRenderer } from "./SpecRenderer";
import { sampleSpec, sampleLoadData } from "./sample";

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

export default function DashboardDemo() {
  const [mode, setMode] = useState<"valid" | "invalid">("valid");

  return (
    <div style={{ maxWidth: 920, margin: "0 auto", padding: "40px 24px", fontFamily: "system-ui, sans-serif" }}>
      <div style={{ display: "flex", gap: 10, marginBottom: 24, alignItems: "center" }}>
        <strong style={{ fontSize: 14 }}>Dashboard renderer demo</strong>
        <button
          data-testid="show-valid"
          onClick={() => setMode("valid")}
          style={{ padding: "6px 12px", borderRadius: 8, border: "1px solid #ccc", background: mode === "valid" ? "#222" : "#fff", color: mode === "valid" ? "#fff" : "#222", cursor: "pointer" }}
        >
          Valid spec
        </button>
        <button
          data-testid="show-invalid"
          onClick={() => setMode("invalid")}
          style={{ padding: "6px 12px", borderRadius: 8, border: "1px solid #ccc", background: mode === "invalid" ? "#222" : "#fff", color: mode === "invalid" ? "#fff" : "#222", cursor: "pointer" }}
        >
          Invalid spec
        </button>
      </div>

      <SpecRenderer spec={mode === "valid" ? sampleSpec : maliciousSpec} loadData={sampleLoadData} />
    </div>
  );
}
