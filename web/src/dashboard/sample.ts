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

// A valid spec_version 2 sample exercising EVERY new catalog component (funnel,
// leaderboard, stat-with-sparkline, cohort-grid, markdown-note) plus the
// grid/span layout primitive. Plain data, validates against validateViewSpec.
// Members are real governed-catalog members (semantic/model/catalog.json).
export const sampleSpecV2: ViewSpec = {
  view_id: "demo_revenue_room_v2",
  title: "Revenue room",
  version: 1,
  spec_version: 2,
  source_prompt: "Build me a revenue room: funnel, top companies, trend, cohorts, and a reading guide",
  semantic_refs: ["Deals.count", "Deals.pipeline_value", "Deals.stage", "Deals.created_at", "Companies.name"],
  grid: { columns: 12 },
  layout: [
    {
      type: "stat-with-sparkline",
      title: "Pipeline value",
      metric: "Deals.pipeline_value",
      span: 3,
      trend: {
        measures: ["Deals.pipeline_value"],
        timeDimensions: [{ dimension: "Deals.created_at", granularity: "week" }],
      },
    },
    {
      type: "funnel",
      title: "Stage funnel",
      span: 5,
      query: { measures: ["Deals.count"], dimensions: ["Deals.stage"] },
    },
    {
      type: "leaderboard",
      title: "Top companies by pipeline",
      limit: 5,
      span: 4,
      query: { measures: ["Deals.pipeline_value"], dimensions: ["Companies.name"] },
    },
    {
      type: "cohort-grid",
      title: "Deals by stage and month",
      span: 8,
      query: { measures: ["Deals.count"], dimensions: ["Deals.stage", "Deals.created_at"] },
    },
    {
      type: "markdown-note",
      title: "Reading guide",
      span: 4,
      body: "# How to read this\nThe funnel counts **open** deals per stage.\n- Leaderboard ranks companies by `pipeline_value`\n- Cohort cells shade by deal count\n1. Check the trend first\n2. Then the funnel conversion",
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
  "Deals.pipeline_value": [{ "Deals.pipeline_value": 380000 }],
};

// v2 fixtures, member-keyed like real Cube result rows. The stage rows also carry
// fixture-shaped keys (stage/value) so the Balto demo chart spec, whose vega-lite
// encoding binds field "stage"/"value", draws from the same stub.
const dealCountByStage: DataRow[] = [
  { "Deals.stage": "Qualify", "Deals.count": 120, stage: "Qualify", value: 120 },
  { "Deals.stage": "Discovery", "Deals.count": 84, stage: "Discovery", value: 84 },
  { "Deals.stage": "Proposal", "Deals.count": 41, stage: "Proposal", value: 41 },
  { "Deals.stage": "Negotiation", "Deals.count": 22, stage: "Negotiation", value: 22 },
  { "Deals.stage": "Closing", "Deals.count": 11, stage: "Closing", value: 11 },
];

const pipelineByCompany: DataRow[] = [
  { "Companies.name": "Brightline Realty", "Deals.pipeline_value": 96000 },
  { "Companies.name": "Cedar & Stone", "Deals.pipeline_value": 81000 },
  { "Companies.name": "Harbor Lights", "Deals.pipeline_value": 64000 },
  { "Companies.name": "Juniper Health", "Deals.pipeline_value": 52000 },
  { "Companies.name": "Mosaic Travel", "Deals.pipeline_value": 47000 },
  { "Companies.name": "Northwind Supply", "Deals.pipeline_value": 40000 },
];

const pipelineTrend: DataRow[] = [
  { "Deals.created_at": "2026-04-27", "Deals.pipeline_value": 210000 },
  { "Deals.created_at": "2026-05-04", "Deals.pipeline_value": 245000 },
  { "Deals.created_at": "2026-05-11", "Deals.pipeline_value": 232000 },
  { "Deals.created_at": "2026-05-18", "Deals.pipeline_value": 290000 },
  { "Deals.created_at": "2026-05-25", "Deals.pipeline_value": 314000 },
  { "Deals.created_at": "2026-06-01", "Deals.pipeline_value": 380000 },
];

const dealsByStageAndMonth: DataRow[] = [
  { "Deals.stage": "Qualify", "Deals.created_at": "Apr", "Deals.count": 34 },
  { "Deals.stage": "Qualify", "Deals.created_at": "May", "Deals.count": 48 },
  { "Deals.stage": "Qualify", "Deals.created_at": "Jun", "Deals.count": 38 },
  { "Deals.stage": "Proposal", "Deals.created_at": "Apr", "Deals.count": 12 },
  { "Deals.stage": "Proposal", "Deals.created_at": "May", "Deals.count": 18 },
  { "Deals.stage": "Proposal", "Deals.created_at": "Jun", "Deals.count": 11 },
  { "Deals.stage": "Closing", "Deals.created_at": "Apr", "Deals.count": 3 },
  { "Deals.stage": "Closing", "Deals.created_at": "May", "Deals.count": 5 },
  { "Deals.stage": "Closing", "Deals.created_at": "Jun", "Deals.count": 3 },
];

/**
 * Offline data stub for the demo and e2e. Resolves a CubeQuery to fixture rows.
 * Injected into SpecRenderer as the loadData prop; the renderer itself never
 * fetches.
 */
export async function sampleLoadData(query: CubeQuery): Promise<DataRow[]> {
  const measures = query.measures ?? [];
  const dimensions = query.dimensions ?? [];

  // v2: weekly pipeline trend (sparkline) — time-grained, so it must be
  // resolved BEFORE the single-measure KPI branch below.
  if (
    (query.timeDimensions ?? []).some((td) => td.dimension === "Deals.created_at") &&
    measures.includes("Deals.pipeline_value")
  ) {
    return pipelineTrend;
  }

  // KPI-style single-measure, no-dimension query.
  if (measures.length === 1 && dimensions.length === 0 && (query.timeDimensions ?? []).length === 0) {
    const rows = kpiTotals[measures[0]];
    if (rows) return rows;
  }

  // Pipeline value by stage chart.
  if (dimensions.includes("Deals.stage") && measures.includes("Deals.totalValue")) {
    return pipelineByStage;
  }

  // v2: cohort grid — stage x month deal counts.
  if (
    dimensions.includes("Deals.stage") &&
    dimensions.includes("Deals.created_at") &&
    measures.includes("Deals.count")
  ) {
    return dealsByStageAndMonth;
  }

  // v2: stage funnel — deal counts per stage (also the Balto-synthesized demo view).
  if (dimensions.includes("Deals.stage") && measures.includes("Deals.count")) {
    return dealCountByStage;
  }

  // v2: company leaderboard.
  if (dimensions.includes("Companies.name") && measures.includes("Deals.pipeline_value")) {
    return pipelineByCompany;
  }

  return [];
}
