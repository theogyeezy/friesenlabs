// A valid sample view-spec for the demo and e2e tests: one KPI over Deals plus
// a bar chart of pipeline value by stage. This is plain data (SPEC, NOT CODE);
// it passes validateViewSpec and exercises the kpi + chart catalog components.

import type { CubeQuery, ViewSpec } from "./viewSpec";

export const sampleSpec: ViewSpec = {
  view_id: "demo_pipeline",
  title: "Pipeline overview",
  version: 1,
  source_prompt: "Show me total pipeline and value by stage",
  semantic_refs: ["Deals.totalValue", "Deals.count", "Deals.stage"],
  layout: [
    {
      type: "kpi",
      title: "Open pipeline",
      metric: "Deals.totalValue",
    },
    {
      type: "kpi",
      title: "Open deals",
      metric: "Deals.count",
    },
    {
      type: "chart",
      title: "Pipeline value by stage",
      encoding: "vega-lite",
      // A bar mark with x = stage (nominal), y = value (quantitative). The
      // renderer injects the loaded rows as the data values; it never trusts a
      // data URL or inline executable content from a spec.
      spec: {
        mark: "bar",
        encoding: {
          x: { field: "stage", type: "nominal", title: "Stage" },
          y: { field: "value", type: "quantitative", title: "Value" },
        },
      },
      query: {
        measures: ["Deals.totalValue"],
        dimensions: ["Deals.stage"],
      },
    },
  ],
};

// Fixture rows the demo/test loadData stub returns, keyed by a stable query
// signature. In production loadData would call the Cube semantic layer; here it
// is fully offline (no real network), satisfying the renderer's injected
// loadData(query) contract.
export type DataRow = Record<string, string | number>;

const pipelineByStage: DataRow[] = [
  { stage: "Qualify", value: 48000 },
  { stage: "Discovery", value: 91000 },
  { stage: "Proposal", value: 67000 },
  { stage: "Negotiation", value: 120000 },
  { stage: "Closing", value: 54000 },
];

const kpiTotals: Record<string, DataRow[]> = {
  "Deals.totalValue": [{ "Deals.totalValue": 380000 }],
  "Deals.count": [{ "Deals.count": 42 }],
};

/**
 * Offline data stub for the demo and e2e. Resolves a CubeQuery to fixture rows.
 * Injected into SpecRenderer as the loadData prop; the renderer itself never
 * fetches.
 */
export async function sampleLoadData(query: CubeQuery): Promise<DataRow[]> {
  const measures = query.measures ?? [];
  const dimensions = query.dimensions ?? [];

  // KPI-style single-measure, no-dimension query.
  if (measures.length === 1 && dimensions.length === 0) {
    const rows = kpiTotals[measures[0]];
    if (rows) return rows;
  }

  // Pipeline value by stage chart.
  if (dimensions.includes("Deals.stage") && measures.includes("Deals.totalValue")) {
    return pipelineByStage;
  }

  return [];
}
