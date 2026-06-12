// Trusted client-side view-spec contract for the Uplift dashboard renderer.
//
// SPEC, NOT CODE. A view-spec is a declarative description of catalog
// components bound to Cube metrics. It carries no React, no JS, no HTML. This
// module is the front-end mirror of shared/schemas/view_spec.schema.json and is
// the only thing the renderer trusts: SpecRenderer re-validates with
// validateViewSpec before drawing a single pixel.
//
// The validator is hand-written (no runtime schema engine) so the catalog is
// closed by construction: it rejects unknown component types, any encoding that
// is not "vega-lite", and any unknown ("additional") property at every level.
//
// Spec versioning (additive evolution, mirrors shared/view_spec.py):
//   * spec_version 1 (default when absent) — kpi / chart / table.
//   * spec_version 2 — adds funnel, leaderboard, stat-with-sparkline,
//     cohort-grid, markdown-note and the grid/span layout primitive.
//     A spec that uses v2 features MUST declare spec_version: 2.
//
// Forward compatibility: a layout block whose `type` is a string outside the
// catalog is NOT a hard error — it is reported in `unknownBlocks` and the
// renderer draws a safe inert placeholder for it (graceful rejection), so a
// newer server-side catalog never blanks a whole dashboard on an older client.
// Nothing inside an unknown block is ever interpreted.

// ---------------------------------------------------------------------------
// Types (mirror of the JSON schema)
// ---------------------------------------------------------------------------

export type Member = string; // "Cube.field" form, validated by MEMBER_RE.

export type FilterOperator =
  | "equals"
  | "notEquals"
  | "gt"
  | "gte"
  | "lt"
  | "lte"
  | "set"
  | "notSet"
  | "contains";

export type Granularity = "day" | "week" | "month" | "quarter" | "year";

export interface TimeDimension {
  dimension: Member;
  granularity?: Granularity;
  dateRange?: [string, string];
}

export interface QueryFilter {
  member: Member;
  operator: FilterOperator;
  values?: string[];
}

export interface CubeQuery {
  measures?: Member[];
  dimensions?: Member[];
  timeDimensions?: TimeDimension[];
  filters?: QueryFilter[];
}

/** Grid layout primitive (spec_version 2): how many columns the view's grid has. */
export interface GridSpec {
  columns?: number; // 1..12, default 12
}

export interface KpiBlock {
  type: "kpi";
  title?: string;
  metric: Member;
  filter?: CubeQuery;
  span?: number;
}

/** Whitelisted Vega-Lite fragment: mark + encoding + transform ONLY (mirror of the
 * server's chartSpecFragment). Anything else — params, signals, data, datasets,
 * usermeta, projection, config, width, ... — is a hard validation error, and no key
 * named href/url may appear anywhere inside encoding/transform (no links, no URL
 * loads). The renderer additionally strips unknown keys before vega-embed. */
export interface ChartSpecFragment {
  mark?: string;
  encoding?: Record<string, unknown>;
  transform?: Array<Record<string, unknown>>;
}

export interface ChartBlock {
  type: "chart";
  title?: string;
  encoding: "vega-lite";
  /** Optional Vega-Lite spec fragment. Treated as untrusted data, never code. */
  spec?: ChartSpecFragment;
  query: CubeQuery;
  span?: number;
}

export interface TableBlock {
  type: "table";
  title?: string;
  query: CubeQuery;
  span?: number;
}

/** spec_version 2: ordered stage funnel — first dimension = stage, first measure = value. */
export interface FunnelBlock {
  type: "funnel";
  title?: string;
  query: CubeQuery;
  span?: number;
}

/** spec_version 2: ranked list — first dimension = label, first measure = score. */
export interface LeaderboardBlock {
  type: "leaderboard";
  title?: string;
  query: CubeQuery;
  limit?: number; // 1..100, default 10
  span?: number;
}

/** spec_version 2: headline number (like kpi) plus a small trend sparkline. */
export interface StatSparklineBlock {
  type: "stat-with-sparkline";
  title?: string;
  metric: Member;
  filter?: CubeQuery;
  trend: CubeQuery;
  span?: number;
}

/** spec_version 2: matrix — first dimension = row, second dimension = column, first measure = cell. */
export interface CohortGridBlock {
  type: "cohort-grid";
  title?: string;
  query: CubeQuery;
  span?: number;
}

/** spec_version 2: narrative panel. Body is a markdown SUBSET rendered to React
 * nodes only (see markdown.tsx) — never HTML, never code. */
export interface MarkdownNoteBlock {
  type: "markdown-note";
  title?: string;
  body: string;
  span?: number;
}

export type LayoutBlock =
  | KpiBlock
  | ChartBlock
  | TableBlock
  | FunnelBlock
  | LeaderboardBlock
  | StatSparklineBlock
  | CohortGridBlock
  | MarkdownNoteBlock;

export interface ViewSpec {
  kind?: "view";
  view_id: string;
  title: string;
  version?: number;
  spec_version?: number;
  source_prompt?: string;
  grid?: GridSpec;
  semantic_refs: Member[];
  layout: LayoutBlock[];
}

export interface ValidationResult {
  ok: boolean;
  errors: string[];
  /** Indices into layout[] whose `type` is outside this client's catalog.
   * Soft: the spec can still be ok; the renderer shows a placeholder there. */
  unknownBlocks: number[];
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

const MEMBER_RE = /^[A-Za-z][A-Za-z0-9_]*\.[A-Za-z][A-Za-z0-9_]*$/;
const GRANULARITIES: Granularity[] = ["day", "week", "month", "quarter", "year"];
const OPERATORS: FilterOperator[] = [
  "equals",
  "notEquals",
  "gt",
  "gte",
  "lt",
  "lte",
  "set",
  "notSet",
  "contains",
];

// Closed catalog of component types. Anything else renders only a placeholder.
const V1_COMPONENT_TYPES = ["kpi", "chart", "table"] as const;
const V2_COMPONENT_TYPES = [
  "funnel",
  "leaderboard",
  "stat-with-sparkline",
  "cohort-grid",
  "markdown-note",
] as const;
const COMPONENT_TYPES: readonly string[] = [...V1_COMPONENT_TYPES, ...V2_COMPONENT_TYPES];

export const SPEC_VERSION_LATEST = 2;

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function isString(v: unknown): v is string {
  return typeof v === "string";
}

function isInt(v: unknown): v is number {
  return typeof v === "number" && Number.isInteger(v);
}

// Reject any key not in `allowed` (the additionalProperties: false guarantee).
function rejectUnknownKeys(
  obj: Record<string, unknown>,
  allowed: string[],
  path: string,
  errors: string[]
): void {
  for (const key of Object.keys(obj)) {
    if (!allowed.includes(key)) {
      errors.push(`${path}: unknown property "${key}" is not allowed`);
    }
  }
}

function checkMember(v: unknown, path: string, errors: string[]): void {
  if (!isString(v) || !MEMBER_RE.test(v)) {
    errors.push(`${path}: must be a "Cube.field" member string`);
  }
}

function checkStringArray(v: unknown, path: string, errors: string[]): void {
  if (!Array.isArray(v)) {
    errors.push(`${path}: must be an array`);
    return;
  }
  v.forEach((item, i) => {
    if (!isString(item)) errors.push(`${path}[${i}]: must be a string`);
  });
}

function checkMemberArray(v: unknown, path: string, errors: string[]): void {
  if (!Array.isArray(v)) {
    errors.push(`${path}: must be an array`);
    return;
  }
  v.forEach((item, i) => checkMember(item, `${path}[${i}]`, errors));
}

function checkSpan(v: unknown, path: string, errors: string[]): void {
  if (!isInt(v) || v < 1 || v > 12) {
    errors.push(`${path}: must be an integer from 1 to 12`);
  }
}

function checkTitle(v: unknown, path: string, errors: string[]): void {
  if (v !== undefined && !isString(v)) errors.push(`${path}: must be a string`);
}

// Whitelist for a chart block's Vega-Lite `spec` fragment (mirror of the server's
// chartSpecFragment in shared/schemas/view_spec.schema.json + shared/view_spec.py —
// keep all three in lockstep). The renderer owns data/sizing; a fragment must stay
// declarative data, never code, signals, or a loader.
const CHART_FRAGMENT_KEYS = ["mark", "encoding", "transform"];

// No key named href/url may appear anywhere inside encoding/transform: kills the
// `href` encoding channel (clickable marks), the `url` channel (external images),
// and any lookup transform that references a URL (from.data.url).
const LINK_KEYS = ["href", "url"];

function checkNoLinkKeys(v: unknown, path: string, errors: string[]): void {
  if (Array.isArray(v)) {
    v.forEach((item, i) => checkNoLinkKeys(item, `${path}[${i}]`, errors));
    return;
  }
  if (!isPlainObject(v)) return;
  for (const key of Object.keys(v)) {
    if (LINK_KEYS.includes(key)) {
      errors.push(`${path}.${key}: link/URL keys are not allowed in a chart spec fragment`);
    } else {
      checkNoLinkKeys(v[key], `${path}.${key}`, errors);
    }
  }
}

function checkChartFragment(v: unknown, path: string, errors: string[]): void {
  if (!isPlainObject(v)) {
    errors.push(`${path}: must be an object`);
    return;
  }
  rejectUnknownKeys(v, CHART_FRAGMENT_KEYS, path, errors);
  if (v.mark !== undefined && !isString(v.mark)) {
    errors.push(`${path}.mark: must be a string mark name`);
  }
  if (v.encoding !== undefined) {
    if (!isPlainObject(v.encoding)) {
      errors.push(`${path}.encoding: must be an object`);
    } else {
      checkNoLinkKeys(v.encoding, `${path}.encoding`, errors);
    }
  }
  if (v.transform !== undefined) {
    if (!Array.isArray(v.transform)) {
      errors.push(`${path}.transform: must be an array`);
    } else {
      v.transform.forEach((entry, i) => {
        if (!isPlainObject(entry)) {
          errors.push(`${path}.transform[${i}]: must be an object`);
        } else {
          checkNoLinkKeys(entry, `${path}.transform[${i}]`, errors);
        }
      });
    }
  }
}

function checkCubeQuery(v: unknown, path: string, errors: string[]): void {
  if (!isPlainObject(v)) {
    errors.push(`${path}: must be an object`);
    return;
  }
  rejectUnknownKeys(
    v,
    ["measures", "dimensions", "timeDimensions", "filters"],
    path,
    errors
  );

  if (v.measures !== undefined) checkMemberArray(v.measures, `${path}.measures`, errors);
  if (v.dimensions !== undefined) checkMemberArray(v.dimensions, `${path}.dimensions`, errors);

  if (v.timeDimensions !== undefined) {
    if (!Array.isArray(v.timeDimensions)) {
      errors.push(`${path}.timeDimensions: must be an array`);
    } else {
      v.timeDimensions.forEach((td, i) => {
        const tp = `${path}.timeDimensions[${i}]`;
        if (!isPlainObject(td)) {
          errors.push(`${tp}: must be an object`);
          return;
        }
        rejectUnknownKeys(td, ["dimension", "granularity", "dateRange"], tp, errors);
        if (td.dimension === undefined) errors.push(`${tp}: "dimension" is required`);
        else checkMember(td.dimension, `${tp}.dimension`, errors);
        if (td.granularity !== undefined && !GRANULARITIES.includes(td.granularity as Granularity)) {
          errors.push(`${tp}.granularity: must be one of ${GRANULARITIES.join(", ")}`);
        }
        if (td.dateRange !== undefined) {
          if (!Array.isArray(td.dateRange) || td.dateRange.length !== 2) {
            errors.push(`${tp}.dateRange: must be an array of exactly 2 strings`);
          } else {
            checkStringArray(td.dateRange, `${tp}.dateRange`, errors);
          }
        }
      });
    }
  }

  if (v.filters !== undefined) {
    if (!Array.isArray(v.filters)) {
      errors.push(`${path}.filters: must be an array`);
    } else {
      v.filters.forEach((f, i) => {
        const fp = `${path}.filters[${i}]`;
        if (!isPlainObject(f)) {
          errors.push(`${fp}: must be an object`);
          return;
        }
        rejectUnknownKeys(f, ["member", "operator", "values"], fp, errors);
        if (f.member === undefined) errors.push(`${fp}: "member" is required`);
        else checkMember(f.member, `${fp}.member`, errors);
        if (f.operator === undefined) errors.push(`${fp}: "operator" is required`);
        else if (!OPERATORS.includes(f.operator as FilterOperator)) {
          errors.push(`${fp}.operator: must be one of ${OPERATORS.join(", ")}`);
        }
        if (f.values !== undefined) checkStringArray(f.values, `${fp}.values`, errors);
      });
    }
  }
}

// Returns true when the block's type is outside the catalog (soft-unknown).
function checkBlock(v: unknown, path: string, errors: string[]): boolean {
  if (!isPlainObject(v)) {
    errors.push(`${path}: must be an object`);
    return false;
  }
  const type = v.type;
  if (!isString(type)) {
    errors.push(`${path}.type: must be a string component type`);
    return false;
  }
  if (!COMPONENT_TYPES.includes(type)) {
    // Soft-unknown: a future catalog component. The renderer shows a safe
    // placeholder; nothing inside the block is validated or interpreted.
    return true;
  }

  if (v.span !== undefined) checkSpan(v.span, `${path}.span`, errors);

  if (type === "kpi") {
    rejectUnknownKeys(v, ["type", "title", "metric", "filter", "span"], path, errors);
    checkTitle(v.title, `${path}.title`, errors);
    if (v.metric === undefined) errors.push(`${path}: "metric" is required`);
    else checkMember(v.metric, `${path}.metric`, errors);
    if (v.filter !== undefined) checkCubeQuery(v.filter, `${path}.filter`, errors);
    return false;
  }

  if (type === "chart") {
    rejectUnknownKeys(v, ["type", "title", "encoding", "spec", "query", "span"], path, errors);
    checkTitle(v.title, `${path}.title`, errors);
    // Only the "vega-lite" encoding is in the catalog. Anything else is refused.
    if (v.encoding === undefined) errors.push(`${path}: "encoding" is required`);
    else if (v.encoding !== "vega-lite") {
      errors.push(`${path}.encoding: only "vega-lite" is allowed, got ${JSON.stringify(v.encoding)}`);
    }
    if (v.spec !== undefined) checkChartFragment(v.spec, `${path}.spec`, errors);
    if (v.query === undefined) errors.push(`${path}: "query" is required`);
    else checkCubeQuery(v.query, `${path}.query`, errors);
    return false;
  }

  if (type === "table" || type === "funnel" || type === "cohort-grid") {
    rejectUnknownKeys(v, ["type", "title", "query", "span"], path, errors);
    checkTitle(v.title, `${path}.title`, errors);
    if (v.query === undefined) errors.push(`${path}: "query" is required`);
    else checkCubeQuery(v.query, `${path}.query`, errors);
    return false;
  }

  if (type === "leaderboard") {
    rejectUnknownKeys(v, ["type", "title", "query", "limit", "span"], path, errors);
    checkTitle(v.title, `${path}.title`, errors);
    if (v.limit !== undefined && (!isInt(v.limit) || v.limit < 1 || v.limit > 100)) {
      errors.push(`${path}.limit: must be an integer from 1 to 100`);
    }
    if (v.query === undefined) errors.push(`${path}: "query" is required`);
    else checkCubeQuery(v.query, `${path}.query`, errors);
    return false;
  }

  if (type === "stat-with-sparkline") {
    rejectUnknownKeys(v, ["type", "title", "metric", "filter", "trend", "span"], path, errors);
    checkTitle(v.title, `${path}.title`, errors);
    if (v.metric === undefined) errors.push(`${path}: "metric" is required`);
    else checkMember(v.metric, `${path}.metric`, errors);
    if (v.filter !== undefined) checkCubeQuery(v.filter, `${path}.filter`, errors);
    if (v.trend === undefined) errors.push(`${path}: "trend" is required`);
    else checkCubeQuery(v.trend, `${path}.trend`, errors);
    return false;
  }

  // type === "markdown-note"
  rejectUnknownKeys(v, ["type", "title", "body", "span"], path, errors);
  checkTitle(v.title, `${path}.title`, errors);
  if (!isString(v.body) || v.body.length < 1 || v.body.length > 4000) {
    errors.push(`${path}.body: must be a string of length 1 to 4000`);
  }
  return false;
}

/** The minimum spec_version the features in this (already shape-checked) spec require. */
function requiredSpecVersion(spec: Record<string, unknown>): number {
  if (spec.grid !== undefined || spec.kind !== undefined) return 2;
  const layout = Array.isArray(spec.layout) ? spec.layout : [];
  for (const block of layout) {
    if (!isPlainObject(block)) continue;
    if (
      (isString(block.type) && (V2_COMPONENT_TYPES as readonly string[]).includes(block.type)) ||
      block.span !== undefined
    ) {
      return 2;
    }
  }
  return 1;
}

/**
 * Validate an arbitrary value against the view-spec contract.
 *
 * Returns { ok, errors, unknownBlocks }. `ok` is true only when the value is a
 * fully-formed view-spec drawn from the closed catalog (plus, possibly, blocks
 * whose type is outside this client's catalog — those are listed in
 * `unknownBlocks` and rendered as safe placeholders, never interpreted).
 * Non-"vega-lite" chart encodings and any extra ("additional") property cause
 * a hard rejection. The renderer calls this first and refuses to draw anything
 * on a non-empty error list.
 */
export function validateViewSpec(spec: unknown): ValidationResult {
  const errors: string[] = [];
  const unknownBlocks: number[] = [];

  if (!isPlainObject(spec)) {
    return { ok: false, errors: ["root: view-spec must be an object"], unknownBlocks };
  }

  rejectUnknownKeys(
    spec,
    ["kind", "view_id", "title", "version", "spec_version", "source_prompt", "grid", "semantic_refs", "layout"],
    "root",
    errors
  );

  if (spec.kind !== undefined && spec.kind !== "view") {
    errors.push('root.kind: must be "view" when present (dashboards have their own validator)');
  }
  if (!isString(spec.view_id) || spec.view_id.length < 1) {
    errors.push('root.view_id: must be a non-empty string');
  }
  if (!isString(spec.title) || spec.title.length < 1 || spec.title.length > 200) {
    errors.push("root.title: must be a string of length 1 to 200");
  }
  if (spec.version !== undefined) {
    if (!isInt(spec.version) || spec.version < 1) {
      errors.push("root.version: must be an integer >= 1");
    }
  }
  if (spec.spec_version !== undefined && spec.spec_version !== 1 && spec.spec_version !== 2) {
    errors.push("root.spec_version: must be 1 or 2");
  }
  if (spec.source_prompt !== undefined && !isString(spec.source_prompt)) {
    errors.push("root.source_prompt: must be a string");
  }
  if (spec.grid !== undefined) {
    if (!isPlainObject(spec.grid)) {
      errors.push("root.grid: must be an object");
    } else {
      rejectUnknownKeys(spec.grid, ["columns"], "root.grid", errors);
      if (spec.grid.columns !== undefined) {
        const c = spec.grid.columns;
        if (!isInt(c) || c < 1 || c > 12) {
          errors.push("root.grid.columns: must be an integer from 1 to 12");
        }
      }
    }
  }

  if (!Array.isArray(spec.semantic_refs) || spec.semantic_refs.length < 1) {
    errors.push("root.semantic_refs: must be a non-empty array");
  } else {
    checkMemberArray(spec.semantic_refs, "root.semantic_refs", errors);
  }

  if (!Array.isArray(spec.layout) || spec.layout.length < 1) {
    errors.push("root.layout: must be a non-empty array");
  } else {
    spec.layout.forEach((block, i) => {
      if (checkBlock(block, `root.layout[${i}]`, errors)) unknownBlocks.push(i);
    });
  }

  // spec_version gate: declaring less than the features used is a hard error,
  // exactly like the server-side validator (shared/view_spec.py).
  if (errors.length === 0) {
    const declared = isInt(spec.spec_version) ? (spec.spec_version as number) : 1;
    const needed = requiredSpecVersion(spec);
    if (declared < needed) {
      errors.push(
        `root.spec_version: declares ${declared} but uses spec_version ${needed} features`
      );
    }
  }

  return { ok: errors.length === 0, errors, unknownBlocks };
}

/** Type guard built on the validator. */
export function isViewSpec(spec: unknown): spec is ViewSpec {
  return validateViewSpec(spec).ok;
}
