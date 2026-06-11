// Trusted client-side contract for kind=dashboard specs (spec_version 2).
//
// A dashboard spec is a NAMED COMPOSITION of saved views: it carries view
// REFERENCES (ids + spans), never components, queries, or markup — so it is
// even more inert than a view-spec. The dashboard screen validates with
// validateDashboardSpec before rendering, then renders each referenced view's
// own spec through the same trusted SpecRenderer (which re-validates again).
//
// Mirror of the dashboardSpec branch of shared/schemas/view_spec.schema.json.
// Hand-written, closed-world: unknown ("additional") properties are rejected
// at every level, exactly like viewSpec.ts.

export interface DashboardItem {
  view_id: string;
  span?: number; // 1..12
}

export interface DashboardSpec {
  kind: "dashboard";
  view_id: string;
  title: string;
  version?: number;
  spec_version: 2;
  source_prompt?: string;
  grid?: { columns?: number };
  items: DashboardItem[];
}

export interface DashboardValidationResult {
  ok: boolean;
  errors: string[];
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function isString(v: unknown): v is string {
  return typeof v === "string";
}

function isInt(v: unknown): v is number {
  return typeof v === "number" && Number.isInteger(v);
}

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

export function validateDashboardSpec(spec: unknown): DashboardValidationResult {
  const errors: string[] = [];

  if (!isPlainObject(spec)) {
    return { ok: false, errors: ["root: dashboard spec must be an object"] };
  }

  rejectUnknownKeys(
    spec,
    ["kind", "view_id", "title", "version", "spec_version", "source_prompt", "grid", "items"],
    "root",
    errors
  );

  if (spec.kind !== "dashboard") {
    errors.push('root.kind: must be "dashboard"');
  }
  if (!isString(spec.view_id) || spec.view_id.length < 1) {
    errors.push("root.view_id: must be a non-empty string");
  }
  if (!isString(spec.title) || spec.title.length < 1 || spec.title.length > 200) {
    errors.push("root.title: must be a string of length 1 to 200");
  }
  if (spec.version !== undefined && (!isInt(spec.version) || spec.version < 1)) {
    errors.push("root.version: must be an integer >= 1");
  }
  if (spec.spec_version !== 2) {
    errors.push("root.spec_version: must be 2");
  }
  if (spec.source_prompt !== undefined && !isString(spec.source_prompt)) {
    errors.push("root.source_prompt: must be a string");
  }
  if (spec.grid !== undefined) {
    if (!isPlainObject(spec.grid)) {
      errors.push("root.grid: must be an object");
    } else {
      rejectUnknownKeys(spec.grid, ["columns"], "root.grid", errors);
      const c = spec.grid.columns;
      if (c !== undefined && (!isInt(c) || c < 1 || c > 12)) {
        errors.push("root.grid.columns: must be an integer from 1 to 12");
      }
    }
  }

  if (!Array.isArray(spec.items) || spec.items.length < 1 || spec.items.length > 24) {
    errors.push("root.items: must be an array of 1 to 24 view references");
  } else {
    spec.items.forEach((item, i) => {
      const path = `root.items[${i}]`;
      if (!isPlainObject(item)) {
        errors.push(`${path}: must be an object`);
        return;
      }
      rejectUnknownKeys(item, ["view_id", "span"], path, errors);
      if (!isString(item.view_id) || item.view_id.length < 1 || item.view_id.length > 128) {
        errors.push(`${path}.view_id: must be a string of length 1 to 128`);
      }
      if (item.span !== undefined && (!isInt(item.span) || item.span < 1 || item.span > 12)) {
        errors.push(`${path}.span: must be an integer from 1 to 12`);
      }
    });
  }

  return { ok: errors.length === 0, errors };
}

/** Type guard built on the validator. */
export function isDashboardSpec(spec: unknown): spec is DashboardSpec {
  return validateDashboardSpec(spec).ok;
}
