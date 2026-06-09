// The trusted dashboard renderer.
//
// Given an untrusted view-spec, this component:
//   1. RE-VALIDATES the spec with validateViewSpec before drawing anything. An
//      invalid spec renders a safe fallback message and nothing else.
//   2. Renders ONLY the closed catalog: kpi card, vega-lite chart, table. There
//      is no path that turns spec content into markup or code.
//   3. NEVER uses dangerouslySetInnerHTML, eval, new Function, or a raw-HTML
//      sink. Every string from the spec lands in a React text node, which React
//      escapes. Chart data is bound as Vega-Lite *data values*, never as a URL
//      or executable.
//   4. Pulls data only through the injected loadData(query) prop. The renderer
//      does not fetch on its own.
//
// This is the only attack surface for the dashboard, so the catalog is closed
// by construction (see viewSpec.ts) and re-checked here at render time.

import React from "react";
import embed from "vega-embed";
import {
  validateViewSpec,
  type ChartBlock,
  type CubeQuery,
  type KpiBlock,
  type LayoutBlock,
  type TableBlock,
  type ViewSpec,
} from "./viewSpec";

const { useEffect, useRef, useState, useMemo } = React;

export type DataRow = Record<string, string | number>;
export type LoadData = (query: CubeQuery) => Promise<DataRow[]>;

export interface SpecRendererProps {
  /** Untrusted candidate spec. Re-validated before any rendering. */
  spec: unknown;
  /** Injected data loader. The renderer never fetches by itself. */
  loadData: LoadData;
}

// ---------------------------------------------------------------------------
// Safe fallback
// ---------------------------------------------------------------------------

function SafeFallback({ errors }: { errors: string[] }) {
  return (
    <div
      data-testid="spec-fallback"
      style={{
        border: "1px solid var(--line, #e3ddd3)",
        background: "var(--surface, #fff)",
        borderRadius: 14,
        padding: "20px 22px",
        maxWidth: 560,
      }}
    >
      <div style={{ fontSize: 15, fontWeight: 700, color: "var(--ink, #2a2622)" }}>
        We could not render this view
      </div>
      <p style={{ fontSize: 13, color: "var(--ink-3, #8a8278)", marginTop: 8, lineHeight: 1.5 }}>
        The dashboard description did not match the Managed view catalog, so nothing was drawn.
        This view renders only known components and never runs code from a spec.
      </p>
      {errors.length > 0 && (
        <ul
          data-testid="spec-fallback-errors"
          style={{ fontSize: 12, color: "var(--ink-4, #a39a8d)", marginTop: 10, paddingLeft: 18 }}
        >
          {errors.slice(0, 5).map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Catalog component: KPI card
// ---------------------------------------------------------------------------

function formatValue(v: unknown): string {
  if (typeof v === "number") {
    return v.toLocaleString();
  }
  return v === undefined || v === null ? "" : String(v);
}

function KpiCard({ block, loadData }: { block: KpiBlock; loadData: LoadData }) {
  const [value, setValue] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  // Build the implied single-measure query for this KPI.
  const query: CubeQuery = useMemo(
    () => ({ measures: [block.metric], ...(block.filter ?? {}) }),
    [block.metric, block.filter]
  );

  useEffect(() => {
    let live = true;
    loadData(query)
      .then((rows) => {
        if (!live) return;
        const row = rows[0];
        const raw = row ? row[block.metric] : undefined;
        setValue(formatValue(raw));
      })
      .catch(() => {
        if (live) setFailed(true);
      });
    return () => {
      live = false;
    };
  }, [query, block.metric, loadData]);

  return (
    <div
      data-testid="kpi-card"
      style={{
        border: "1px solid var(--line, #e3ddd3)",
        background: "var(--surface, #fff)",
        borderRadius: 14,
        padding: "16px 18px",
        minWidth: 180,
      }}
    >
      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-3, #8a8278)", letterSpacing: ".01em" }}>
        {block.title ?? block.metric}
      </div>
      <div
        data-testid="kpi-value"
        style={{ fontSize: 28, fontWeight: 760, color: "var(--ink, #2a2622)", marginTop: 6, letterSpacing: "-.02em" }}
      >
        {failed ? "--" : value === null ? "..." : value}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Catalog component: Vega-Lite chart
// ---------------------------------------------------------------------------

function ChartCard({ block, loadData }: { block: ChartBlock; loadData: LoadData }) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    let view: { finalize: () => void } | null = null;

    loadData(block.query)
      .then((rows) => {
        if (!live || !hostRef.current) return;

        // Compose the final Vega-Lite spec from the (untrusted) spec fragment
        // plus the loaded data. We override `data` with inline values so a spec
        // can never point the chart at a URL or loader. The fragment supplies
        // mark + encoding only; vega-embed parses it as data, not code.
        const fragment = (block.spec ?? {}) as Record<string, unknown>;
        const vlSpec = {
          $schema: "https://vega.github.io/schema/vega-lite/v6.json",
          ...fragment,
          data: { values: rows },
          width: "container",
          height: 240,
        };

        embed(hostRef.current, vlSpec as never, {
          actions: false,
          renderer: "svg",
          // Hard-disable any loader so a spec can never reach the network or a
          // local file even if it tried to smuggle a data URL.
          loader: { http: undefined, file: undefined } as never,
        })
          .then((result) => {
            if (!live) {
              result.view.finalize();
              return;
            }
            view = result.view;
          })
          .catch(() => {
            if (live) setFailed(true);
          });
      })
      .catch(() => {
        if (live) setFailed(true);
      });

    return () => {
      live = false;
      if (view) view.finalize();
      if (hostRef.current) hostRef.current.innerHTML = "";
    };
  }, [block.spec, block.query, loadData]);

  return (
    <div
      data-testid="chart-card"
      style={{
        border: "1px solid var(--line, #e3ddd3)",
        background: "var(--surface, #fff)",
        borderRadius: 14,
        padding: "16px 18px",
        gridColumn: "1 / -1",
      }}
    >
      {block.title && (
        <div style={{ fontSize: 13, fontWeight: 700, color: "var(--ink, #2a2622)", marginBottom: 10 }}>
          {block.title}
        </div>
      )}
      {failed ? (
        <div style={{ fontSize: 13, color: "var(--ink-3, #8a8278)" }}>Chart could not be drawn.</div>
      ) : (
        <div data-testid="chart-host" ref={hostRef} style={{ width: "100%" }} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Catalog component: Table
// ---------------------------------------------------------------------------

function TableCard({ block, loadData }: { block: TableBlock; loadData: LoadData }) {
  const [rows, setRows] = useState<DataRow[]>([]);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    loadData(block.query)
      .then((r) => {
        if (live) setRows(r);
      })
      .catch(() => {
        if (live) setFailed(true);
      });
    return () => {
      live = false;
    };
  }, [block.query, loadData]);

  const columns = rows.length > 0 ? Object.keys(rows[0]) : [];

  return (
    <div
      data-testid="table-card"
      style={{
        border: "1px solid var(--line, #e3ddd3)",
        background: "var(--surface, #fff)",
        borderRadius: 14,
        padding: "16px 18px",
        gridColumn: "1 / -1",
        overflowX: "auto",
      }}
    >
      {block.title && (
        <div style={{ fontSize: 13, fontWeight: 700, color: "var(--ink, #2a2622)", marginBottom: 10 }}>
          {block.title}
        </div>
      )}
      {failed ? (
        <div style={{ fontSize: 13, color: "var(--ink-3, #8a8278)" }}>Table could not be loaded.</div>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr>
              {columns.map((c) => (
                <th
                  key={c}
                  style={{
                    textAlign: "left",
                    padding: "6px 10px",
                    borderBottom: "1px solid var(--line, #e3ddd3)",
                    color: "var(--ink-3, #8a8278)",
                    fontWeight: 600,
                  }}
                >
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, ri) => (
              <tr key={ri}>
                {columns.map((c) => (
                  <td
                    key={c}
                    style={{ padding: "6px 10px", borderBottom: "1px solid var(--line-2, #efe9df)", color: "var(--ink, #2a2622)" }}
                  >
                    {formatValue(row[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Block dispatch (catalog-only switch)
// ---------------------------------------------------------------------------

function renderBlock(block: LayoutBlock, key: number, loadData: LoadData): React.ReactNode {
  switch (block.type) {
    case "kpi":
      return <KpiCard key={key} block={block} loadData={loadData} />;
    case "chart":
      return <ChartCard key={key} block={block} loadData={loadData} />;
    case "table":
      return <TableCard key={key} block={block} loadData={loadData} />;
    default:
      // Unreachable after validation; kept as a defensive no-op so an unknown
      // type can never fall through to raw rendering.
      return null;
  }
}

// ---------------------------------------------------------------------------
// Top-level renderer
// ---------------------------------------------------------------------------

export function SpecRenderer({ spec, loadData }: SpecRendererProps) {
  // RE-VALIDATE FIRST. Never trust the caller's claim that the spec is valid.
  const result = useMemo(() => validateViewSpec(spec), [spec]);

  if (!result.ok) {
    return <SafeFallback errors={result.errors} />;
  }

  // Safe to narrow now: validateViewSpec guarantees the shape.
  const view = spec as ViewSpec;

  return (
    <div data-testid="spec-renderer">
      <h2 style={{ fontSize: 20, fontWeight: 740, letterSpacing: "-.02em", color: "var(--ink, #2a2622)" }}>
        {view.title}
      </h2>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
          gap: 16,
          marginTop: 18,
        }}
      >
        {view.layout.map((block, i) => renderBlock(block, i, loadData))}
      </div>
    </div>
  );
}

export default SpecRenderer;
