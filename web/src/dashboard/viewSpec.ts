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

export interface KpiBlock {
  type: "kpi";
  title?: string;
  metric: Member;
  filter?: CubeQuery;
}

export interface ChartBlock {
  type: "chart";
  title?: string;
  encoding: "vega-lite";
  /** Optional Vega-Lite spec fragment. Treated as untrusted data, never code. */
  spec?: Record<string, unknown>;
  query: CubeQuery;
}

export interface TableBlock {
  type: "table";
  title?: string;
  query: CubeQuery;
}

export type LayoutBlock = KpiBlock | ChartBlock | TableBlock;

export interface ViewSpec {
  view_id: string;
  title: string;
  version?: number;
  source_prompt?: string;
  semantic_refs: Member[];
  layout: LayoutBlock[];
}

export interface ValidationResult {
  ok: boolean;
  errors: string[];
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

// Closed catalog of component types. Anything else is refused.
const COMPONENT_TYPES = ["kpi", "chart", "table"] as const;

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function isString(v: unknown): v is string {
  return typeof v === "string";
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

function checkBlock(v: unknown, path: string, errors: string[]): void {
  if (!isPlainObject(v)) {
    errors.push(`${path}: must be an object`);
    return;
  }
  const type = v.type;
  if (!isString(type) || !(COMPONENT_TYPES as readonly string[]).includes(type)) {
    errors.push(
      `${path}.type: unknown component type ${JSON.stringify(type)} is not in the catalog (${COMPONENT_TYPES.join(", ")})`
    );
    return; // cannot validate further without a known catalog type
  }

  if (type === "kpi") {
    rejectUnknownKeys(v, ["type", "title", "metric", "filter"], path, errors);
    if (v.title !== undefined && !isString(v.title)) errors.push(`${path}.title: must be a string`);
    if (v.metric === undefined) errors.push(`${path}: "metric" is required`);
    else checkMember(v.metric, `${path}.metric`, errors);
    if (v.filter !== undefined) checkCubeQuery(v.filter, `${path}.filter`, errors);
    return;
  }

  if (type === "chart") {
    rejectUnknownKeys(v, ["type", "title", "encoding", "spec", "query"], path, errors);
    if (v.title !== undefined && !isString(v.title)) errors.push(`${path}.title: must be a string`);
    // Only the "vega-lite" encoding is in the catalog. Anything else is refused.
    if (v.encoding === undefined) errors.push(`${path}: "encoding" is required`);
    else if (v.encoding !== "vega-lite") {
      errors.push(`${path}.encoding: only "vega-lite" is allowed, got ${JSON.stringify(v.encoding)}`);
    }
    if (v.spec !== undefined && !isPlainObject(v.spec)) {
      errors.push(`${path}.spec: must be an object`);
    }
    if (v.query === undefined) errors.push(`${path}: "query" is required`);
    else checkCubeQuery(v.query, `${path}.query`, errors);
    return;
  }

  // type === "table"
  rejectUnknownKeys(v, ["type", "title", "query"], path, errors);
  if (v.title !== undefined && !isString(v.title)) errors.push(`${path}.title: must be a string`);
  if (v.query === undefined) errors.push(`${path}: "query" is required`);
  else checkCubeQuery(v.query, `${path}.query`, errors);
}

/**
 * Validate an arbitrary value against the view-spec contract.
 *
 * Returns { ok, errors }. `ok` is true only when the value is a fully-formed
 * view-spec drawn entirely from the closed catalog. Unknown component types,
 * non-"vega-lite" chart encodings, and any extra ("additional") property cause
 * a hard rejection. The renderer calls this first and refuses to draw anything
 * on a non-empty error list.
 */
export function validateViewSpec(spec: unknown): ValidationResult {
  const errors: string[] = [];

  if (!isPlainObject(spec)) {
    return { ok: false, errors: ["root: view-spec must be an object"] };
  }

  rejectUnknownKeys(
    spec,
    ["view_id", "title", "version", "source_prompt", "semantic_refs", "layout"],
    "root",
    errors
  );

  if (!isString(spec.view_id) || spec.view_id.length < 1) {
    errors.push('root.view_id: must be a non-empty string');
  }
  if (!isString(spec.title) || spec.title.length < 1 || spec.title.length > 200) {
    errors.push("root.title: must be a string of length 1 to 200");
  }
  if (spec.version !== undefined) {
    if (typeof spec.version !== "number" || !Number.isInteger(spec.version) || spec.version < 1) {
      errors.push("root.version: must be an integer >= 1");
    }
  }
  if (spec.source_prompt !== undefined && !isString(spec.source_prompt)) {
    errors.push("root.source_prompt: must be a string");
  }

  if (!Array.isArray(spec.semantic_refs) || spec.semantic_refs.length < 1) {
    errors.push("root.semantic_refs: must be a non-empty array");
  } else {
    checkMemberArray(spec.semantic_refs, "root.semantic_refs", errors);
  }

  if (!Array.isArray(spec.layout) || spec.layout.length < 1) {
    errors.push("root.layout: must be a non-empty array");
  } else {
    spec.layout.forEach((block, i) => checkBlock(block, `root.layout[${i}]`, errors));
  }

  return { ok: errors.length === 0, errors };
}

/** Type guard built on the validator. */
export function isViewSpec(spec: unknown): spec is ViewSpec {
  return validateViewSpec(spec).ok;
}
