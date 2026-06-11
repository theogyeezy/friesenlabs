// The trusted dashboard renderer.
//
// Given an untrusted view-spec, this component:
//   1. RE-VALIDATES the spec with validateViewSpec before drawing anything. An
//      invalid spec renders a safe fallback message and nothing else. A block
//      whose type is outside this client's catalog (a NEWER server catalog)
//      renders a safe inert placeholder — graceful rejection, never a blank
//      dashboard and never interpretation of unknown content.
//   2. Renders ONLY the closed catalog: kpi, vega-lite chart, table, and the
//      spec_version 2 additions — funnel, leaderboard, stat-with-sparkline,
//      cohort-grid, markdown-note. There is no path that turns spec content
//      into markup or code.
//   3. NEVER uses dangerouslySetInnerHTML, eval, new Function, or a raw-HTML
//      sink. Every string from the spec lands in a React text node, which React
//      escapes. Chart data is bound as Vega-Lite *data values*, never as a URL
//      or executable. markdown-note bodies go through the SafeMarkdown subset
//      parser (React nodes only, no links/images/HTML).
//   4. Pulls data only through the injected loadData(query) prop. The renderer
//      does not fetch on its own.
//   5. Every data-bound component has explicit loading / empty / error states —
//      no blank panels, no fabricated numbers.
//
// Layout: v1 specs keep the original auto-fit grid pixel-for-pixel. A spec that
// declares spec_version 2 (or a grid) renders on an explicit N-column grid
// (grid.columns, default 12) where each block spans block.span columns
// (clamped; per-type defaults below).
//
// This is the only attack surface for the dashboard, so the catalog is closed
// by construction (see viewSpec.ts) and re-checked here at render time.

import React from "react";
import embed from "vega-embed";
import {
  validateViewSpec,
  type ChartBlock,
  type CohortGridBlock,
  type CubeQuery,
  type FunnelBlock,
  type KpiBlock,
  type LayoutBlock,
  type LeaderboardBlock,
  type MarkdownNoteBlock,
  type StatSparklineBlock,
  type TableBlock,
  type ViewSpec,
} from "./viewSpec";
import { SafeMarkdown } from "./markdown";

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
// Shared bits
// ---------------------------------------------------------------------------

const cardStyle: React.CSSProperties = {
  border: "1px solid var(--line, #e3ddd3)",
  background: "var(--surface, #fff)",
  borderRadius: 14,
  padding: "16px 18px",
};

const mutedText: React.CSSProperties = { fontSize: 13, color: "var(--ink-3, #8a8278)" };

function CardTitle({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ fontSize: 13, fontWeight: 700, color: "var(--ink, #2a2622)", marginBottom: 10 }}>
      {children}
    </div>
  );
}

type QueryState = "loading" | "ready" | "empty" | "failed";

/** One-shot query hook: explicit loading / ready / empty / failed, no blanks. */
function useQueryRows(query: CubeQuery, loadData: LoadData): { rows: DataRow[]; state: QueryState } {
  const [rows, setRows] = useState<DataRow[]>([]);
  const [state, setState] = useState<QueryState>("loading");

  useEffect(() => {
    let live = true;
    setState("loading");
    loadData(query)
      .then((r) => {
        if (!live) return;
        setRows(r);
        setState(r.length === 0 ? "empty" : "ready");
      })
      .catch(() => {
        if (live) setState("failed");
      });
    return () => {
      live = false;
    };
  }, [query, loadData]);

  return { rows, state };
}

function StatusLine({ state, testidBase }: { state: QueryState; testidBase: string }) {
  if (state === "loading") {
    return (
      <div data-testid={`${testidBase}-loading`} style={mutedText}>
        Loading...
      </div>
    );
  }
  if (state === "empty") {
    return (
      <div data-testid={`${testidBase}-empty`} style={mutedText}>
        No data yet
      </div>
    );
  }
  // failed
  return (
    <div data-testid={`${testidBase}-error`} style={mutedText}>
      This panel could not be loaded.
    </div>
  );
}

function formatValue(v: unknown): string {
  if (typeof v === "number") {
    return v.toLocaleString();
  }
  return v === undefined || v === null ? "" : String(v);
}

// Row-shape helpers: Cube resolves rows keyed by member name. Prefer the keys
// the query names; fall back to the nth string key (labels) / first number key
// (values) so fixture-shaped rows still draw.
function labelKey(rows: DataRow[], query: CubeQuery, index = 0): string | null {
  const dims = query.dimensions ?? [];
  if (dims.length > index && rows[0] && dims[index] in rows[0]) return dims[index];
  const keys = rows[0] ? Object.keys(rows[0]) : [];
  const strings = keys.filter((k) => typeof rows[0][k] === "string");
  return strings[index] ?? null;
}

function valueKey(rows: DataRow[], query: CubeQuery): string | null {
  const measures = query.measures ?? [];
  if (measures.length > 0 && rows[0] && measures[0] in rows[0]) return measures[0];
  const keys = rows[0] ? Object.keys(rows[0]) : [];
  return keys.find((k) => typeof rows[0][k] === "number") ?? null;
}

function asNumber(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}

// ---------------------------------------------------------------------------
// Safe fallback (whole-spec rejection)
// ---------------------------------------------------------------------------

function SafeFallback({ errors }: { errors: string[] }) {
  return (
    <div data-testid="spec-fallback" style={{ ...cardStyle, padding: "20px 22px", maxWidth: 560 }}>
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

// Per-block graceful rejection: a component type from a NEWER catalog than this
// client. Inert by construction — only the (escaped) type name is shown;
// nothing else from the block is read.
function UnknownBlockCard({ typeName }: { typeName: string }) {
  return (
    <div data-testid="unknown-block" style={cardStyle}>
      <div style={{ fontSize: 13, fontWeight: 700, color: "var(--ink, #2a2622)" }}>
        Panel not supported
      </div>
      <p style={{ ...mutedText, marginTop: 6, lineHeight: 1.5 }}>
        This view uses a component (<span data-testid="unknown-block-type">{typeName}</span>) that
        this version of the app cannot draw yet. The rest of the view renders normally.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Catalog component: KPI card
// ---------------------------------------------------------------------------

function KpiCard({ block, loadData }: { block: KpiBlock; loadData: LoadData }) {
  const [value, setValue] = useState<string | null>(null);
  // "empty" = the query resolved but carried no value for this metric. That is
  // an honest "No data yet", never a fabricated number and never a silent blank.
  const [state, setState] = useState<QueryState>("loading");

  // Build the implied single-measure query for this KPI.
  const query: CubeQuery = useMemo(
    () => ({ measures: [block.metric], ...(block.filter ?? {}) }),
    [block.metric, block.filter]
  );

  useEffect(() => {
    let live = true;
    setState("loading");
    loadData(query)
      .then((rows) => {
        if (!live) return;
        const row = rows[0];
        const raw = row ? row[block.metric] : undefined;
        if (raw === undefined || raw === null || raw === "") {
          setState("empty");
        } else {
          setValue(formatValue(raw));
          setState("ready");
        }
      })
      .catch(() => {
        if (live) setState("failed");
      });
    return () => {
      live = false;
    };
  }, [query, block.metric, loadData]);

  return (
    <div data-testid="kpi-card" style={{ ...cardStyle, minWidth: 180 }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-3, #8a8278)", letterSpacing: ".01em" }}>
        {block.title ?? block.metric}
      </div>
      {state === "empty" ? (
        <div
          data-testid="kpi-empty"
          style={{ fontSize: 14, fontWeight: 600, color: "var(--ink-3, #8a8278)", marginTop: 10 }}
        >
          No data yet
        </div>
      ) : (
        <div
          data-testid="kpi-value"
          style={{ fontSize: 28, fontWeight: 760, color: "var(--ink, #2a2622)", marginTop: 6, letterSpacing: "-.02em" }}
        >
          {state === "failed" ? "--" : state === "loading" ? "..." : value}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Catalog component: Vega-Lite chart
// ---------------------------------------------------------------------------

// Defense in depth for the (already validated) chart fragment: rebuild it onto a
// clean object keeping ONLY the whitelisted keys (mark / encoding / transform),
// dropping any key named href/url anywhere inside. Validation already hard-rejects
// these; the renderer additionally strips them so an unexpected shape is render-inert
// rather than reaching vega-embed. Mirrors shared/view_spec.py CHART_FRAGMENT_ALLOWED_KEYS.
function stripLinkKeysDeep(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(stripLinkKeysDeep);
  if (v !== null && typeof v === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, val] of Object.entries(v as Record<string, unknown>)) {
      if (key === "href" || key === "url") continue;
      out[key] = stripLinkKeysDeep(val);
    }
    return out;
  }
  return v;
}

function sanitizeChartFragment(fragment: unknown): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (fragment === null || typeof fragment !== "object" || Array.isArray(fragment)) return out;
  const f = fragment as Record<string, unknown>;
  if (typeof f.mark === "string") out.mark = f.mark;
  if (f.encoding !== null && typeof f.encoding === "object" && !Array.isArray(f.encoding)) {
    out.encoding = stripLinkKeysDeep(f.encoding);
  }
  if (Array.isArray(f.transform)) {
    out.transform = f.transform
      .filter((t) => t !== null && typeof t === "object" && !Array.isArray(t))
      .map(stripLinkKeysDeep);
  }
  return out;
}

function ChartCard({ block, loadData }: { block: ChartBlock; loadData: LoadData }) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [failed, setFailed] = useState(false);
  // The query resolved with zero rows: say "No data yet" instead of drawing an
  // empty (and misleading) chart frame.
  const [empty, setEmpty] = useState(false);

  useEffect(() => {
    let live = true;
    let view: { finalize: () => void } | null = null;

    loadData(block.query)
      .then((rows) => {
        if (!live || !hostRef.current) return;
        if (rows.length === 0) {
          setEmpty(true);
          return;
        }
        setEmpty(false);

        // Compose the final Vega-Lite spec from the (untrusted) spec fragment
        // plus the loaded data. We override `data` with inline values so a spec
        // can never point the chart at a URL or loader. The fragment is rebuilt
        // through the whitelist (mark / encoding / transform, no href/url keys)
        // even though validation already enforced it — defense in depth;
        // vega-embed parses it as data, not code.
        const fragment = sanitizeChartFragment(block.spec);
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
    <div data-testid="chart-card" style={{ ...cardStyle, gridColumn: "1 / -1" }}>
      {block.title && <CardTitle>{block.title}</CardTitle>}
      {failed ? (
        <div style={mutedText}>Chart could not be drawn.</div>
      ) : (
        <>
          {empty && (
            <div data-testid="chart-empty" style={mutedText}>
              No data yet
            </div>
          )}
          <div data-testid="chart-host" ref={hostRef} style={{ width: "100%", display: empty ? "none" : undefined }} />
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Catalog component: Table
// ---------------------------------------------------------------------------

function TableCard({ block, loadData }: { block: TableBlock; loadData: LoadData }) {
  const [rows, setRows] = useState<DataRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    loadData(block.query)
      .then((r) => {
        if (live) {
          setRows(r);
          setLoaded(true);
        }
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
    <div data-testid="table-card" style={{ ...cardStyle, gridColumn: "1 / -1", overflowX: "auto" }}>
      {block.title && <CardTitle>{block.title}</CardTitle>}
      {failed ? (
        <div style={mutedText}>Table could not be loaded.</div>
      ) : loaded && rows.length === 0 ? (
        // Resolved but empty: an honest "No data yet", not a bare header row.
        <div data-testid="table-empty" style={mutedText}>
          No data yet
        </div>
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
// Catalog component (v2): Funnel — ordered stages, width proportional to the
// first stage, with per-stage conversion shown as data, not decoration.
// ---------------------------------------------------------------------------

function FunnelCard({ block, loadData }: { block: FunnelBlock; loadData: LoadData }) {
  const { rows, state } = useQueryRows(block.query, loadData);

  let body: React.ReactNode = <StatusLine state={state} testidBase="funnel" />;
  if (state === "ready") {
    const lk = labelKey(rows, block.query);
    const vk = valueKey(rows, block.query);
    if (lk === null || vk === null) {
      body = <StatusLine state="failed" testidBase="funnel" />;
    } else {
      const first = Math.max(asNumber(rows[0][vk]), 1);
      body = (
        <div style={{ display: "grid", gap: 6 }}>
          {rows.map((row, i) => {
            const v = asNumber(row[vk]);
            const pct = Math.max(Math.min(v / first, 1), 0);
            return (
              <div key={i} data-testid="funnel-step" style={{ display: "grid", gap: 2 }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5 }}>
                  <span style={{ color: "var(--ink, #2a2622)", fontWeight: 600 }}>
                    {formatValue(row[lk])}
                  </span>
                  <span style={{ color: "var(--ink-3, #8a8278)" }}>
                    {formatValue(v)} ({Math.round(pct * 100)}%)
                  </span>
                </div>
                <div style={{ background: "var(--line-2, #efe9df)", borderRadius: 6, height: 14 }}>
                  <div
                    style={{
                      width: `${Math.max(pct * 100, 2)}%`,
                      height: "100%",
                      borderRadius: 6,
                      background: "var(--accent, #b4664a)",
                      opacity: 0.55 + 0.45 * pct,
                    }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      );
    }
  }

  return (
    <div data-testid="funnel-card" style={cardStyle}>
      {block.title && <CardTitle>{block.title}</CardTitle>}
      {body}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Catalog component (v2): Leaderboard — ranked rows with proportional bars.
// ---------------------------------------------------------------------------

function LeaderboardCard({ block, loadData }: { block: LeaderboardBlock; loadData: LoadData }) {
  const { rows, state } = useQueryRows(block.query, loadData);

  let body: React.ReactNode = <StatusLine state={state} testidBase="leaderboard" />;
  if (state === "ready") {
    const lk = labelKey(rows, block.query);
    const vk = valueKey(rows, block.query);
    if (lk === null || vk === null) {
      body = <StatusLine state="failed" testidBase="leaderboard" />;
    } else {
      const limit = block.limit ?? 10;
      const ranked = [...rows].sort((a, b) => asNumber(b[vk]) - asNumber(a[vk])).slice(0, limit);
      const top = Math.max(asNumber(ranked[0]?.[vk]), 1);
      body = (
        <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 6 }}>
          {ranked.map((row, i) => {
            const v = asNumber(row[vk]);
            return (
              <li key={i} data-testid="leaderboard-row" style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span style={{ width: 18, fontSize: 12, color: "var(--ink-4, #a39a8d)", fontWeight: 700 }}>
                  {i + 1}
                </span>
                <span style={{ flex: "0 0 38%", fontSize: 12.5, fontWeight: 600, color: "var(--ink, #2a2622)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {formatValue(row[lk])}
                </span>
                <span style={{ flex: 1, background: "var(--line-2, #efe9df)", borderRadius: 5, height: 10 }}>
                  <span
                    style={{
                      display: "block",
                      width: `${Math.max((v / top) * 100, 2)}%`,
                      height: "100%",
                      borderRadius: 5,
                      background: "var(--accent, #b4664a)",
                    }}
                  />
                </span>
                <span style={{ width: 70, textAlign: "right", fontSize: 12.5, color: "var(--ink-3, #8a8278)" }}>
                  {formatValue(v)}
                </span>
              </li>
            );
          })}
        </ol>
      );
    }
  }

  return (
    <div data-testid="leaderboard-card" style={cardStyle}>
      {block.title && <CardTitle>{block.title}</CardTitle>}
      {body}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Catalog component (v2): stat-with-sparkline — KPI headline + inline SVG trend.
// The sparkline is built from loaded data values only (numbers run through a
// scale; no string from the spec touches the SVG).
// ---------------------------------------------------------------------------

function Sparkline({ values }: { values: number[] }) {
  const w = 160;
  const h = 36;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const step = values.length > 1 ? w / (values.length - 1) : w;
  const points = values
    .map((v, i) => `${(i * step).toFixed(1)},${(h - 3 - ((v - min) / range) * (h - 6)).toFixed(1)}`)
    .join(" ");
  return (
    <svg
      data-testid="sparkline"
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      role="img"
      aria-label="trend"
      style={{ display: "block", marginTop: 8 }}
    >
      <polyline
        points={points}
        fill="none"
        stroke="var(--accent, #b4664a)"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function StatSparklineCard({ block, loadData }: { block: StatSparklineBlock; loadData: LoadData }) {
  const headlineQuery: CubeQuery = useMemo(
    () => ({ measures: [block.metric], ...(block.filter ?? {}) }),
    [block.metric, block.filter]
  );
  const headline = useQueryRows(headlineQuery, loadData);
  const trend = useQueryRows(block.trend, loadData);

  const headlineValue =
    headline.state === "ready" ? headline.rows[0]?.[block.metric] : undefined;

  const trendValues: number[] = useMemo(() => {
    if (trend.state !== "ready") return [];
    const vk = valueKey(trend.rows, block.trend) ?? block.metric;
    return trend.rows.map((r) => asNumber(r[vk]));
  }, [trend.state, trend.rows, block.trend, block.metric]);

  const anyFailed = headline.state === "failed" || trend.state === "failed";
  const anyLoading = headline.state === "loading" || trend.state === "loading";
  const bothEmpty = headline.state === "empty" && trend.state === "empty";

  return (
    <div data-testid="stat-card" style={{ ...cardStyle, minWidth: 180 }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-3, #8a8278)", letterSpacing: ".01em" }}>
        {block.title ?? block.metric}
      </div>
      {anyFailed ? (
        <StatusLine state="failed" testidBase="stat" />
      ) : anyLoading ? (
        <StatusLine state="loading" testidBase="stat" />
      ) : bothEmpty ? (
        <StatusLine state="empty" testidBase="stat" />
      ) : (
        <>
          <div
            data-testid="stat-value"
            style={{ fontSize: 28, fontWeight: 760, color: "var(--ink, #2a2622)", marginTop: 6, letterSpacing: "-.02em" }}
          >
            {headlineValue === undefined || headlineValue === null || headlineValue === ""
              ? "--"
              : formatValue(headlineValue)}
          </div>
          {trendValues.length > 1 ? (
            <Sparkline values={trendValues} />
          ) : (
            <div data-testid="stat-trend-empty" style={{ ...mutedText, marginTop: 8, fontSize: 12 }}>
              Not enough history for a trend yet
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Catalog component (v2): cohort-grid — first dimension = row, second = column,
// first measure = cell, shaded by normalized intensity.
// ---------------------------------------------------------------------------

function CohortGridCard({ block, loadData }: { block: CohortGridBlock; loadData: LoadData }) {
  const { rows, state } = useQueryRows(block.query, loadData);

  let body: React.ReactNode = <StatusLine state={state} testidBase="cohort" />;
  if (state === "ready") {
    const rk = labelKey(rows, block.query, 0);
    const ck = labelKey(rows, block.query, 1);
    const vk = valueKey(rows, block.query);
    if (rk === null || ck === null || vk === null || rk === ck) {
      body = <StatusLine state="failed" testidBase="cohort" />;
    } else {
      const rowLabels = [...new Set(rows.map((r) => String(r[rk])))];
      const colLabels = [...new Set(rows.map((r) => String(r[ck])))];
      const cell = new Map<string, number>();
      for (const r of rows) cell.set(`${String(r[rk])} ${String(r[ck])}`, asNumber(r[vk]));
      const max = Math.max(...[...cell.values()], 1);
      body = (
        <div style={{ overflowX: "auto" }}>
          <table style={{ borderCollapse: "collapse", fontSize: 12.5, width: "100%" }}>
            <thead>
              <tr>
                <th style={{ padding: "4px 8px" }} />
                {colLabels.map((c) => (
                  <th key={c} style={{ padding: "4px 8px", color: "var(--ink-3, #8a8278)", fontWeight: 600, textAlign: "center" }}>
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rowLabels.map((r) => (
                <tr key={r}>
                  <th style={{ padding: "4px 8px", textAlign: "left", color: "var(--ink, #2a2622)", fontWeight: 600 }}>
                    {r}
                  </th>
                  {colLabels.map((c) => {
                    const v = cell.get(`${r} ${c}`);
                    const intensity = v === undefined ? 0 : v / max;
                    return (
                      <td
                        key={c}
                        data-testid="cohort-cell"
                        style={{
                          padding: "6px 8px",
                          textAlign: "center",
                          borderRadius: 6,
                          color: intensity > 0.6 ? "#fff" : "var(--ink, #2a2622)",
                          background:
                            v === undefined
                              ? "transparent"
                              : `color-mix(in oklab, var(--accent, #b4664a) ${Math.round(
                                  15 + intensity * 85
                                )}%, var(--surface, #fff))`,
                        }}
                      >
                        {v === undefined ? "·" : formatValue(v)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }
  }

  return (
    <div data-testid="cohort-card" style={cardStyle}>
      {block.title && <CardTitle>{block.title}</CardTitle>}
      {body}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Catalog component (v2): markdown-note — narrative via the SafeMarkdown subset.
// No query, so no loading/empty states; an empty body cannot validate.
// ---------------------------------------------------------------------------

function MarkdownNoteCard({ block }: { block: MarkdownNoteBlock }) {
  return (
    <div data-testid="markdown-note-card" style={cardStyle}>
      {block.title && <CardTitle>{block.title}</CardTitle>}
      <SafeMarkdown body={block.body} />
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
    case "funnel":
      return <FunnelCard key={key} block={block} loadData={loadData} />;
    case "leaderboard":
      return <LeaderboardCard key={key} block={block} loadData={loadData} />;
    case "stat-with-sparkline":
      return <StatSparklineCard key={key} block={block} loadData={loadData} />;
    case "cohort-grid":
      return <CohortGridCard key={key} block={block} loadData={loadData} />;
    case "markdown-note":
      return <MarkdownNoteCard key={key} block={block} />;
    default:
      // Unreachable after validation; kept as a defensive no-op so an unknown
      // type can never fall through to raw rendering.
      return null;
  }
}

// Default column spans by component type (12-column grid).
const DEFAULT_SPAN: Record<LayoutBlock["type"], number> = {
  kpi: 3,
  "stat-with-sparkline": 3,
  funnel: 6,
  leaderboard: 6,
  chart: 12,
  table: 12,
  "cohort-grid": 12,
  "markdown-note": 12,
};

// ---------------------------------------------------------------------------
// Top-level renderer
// ---------------------------------------------------------------------------

export function SpecRenderer({ spec, loadData }: SpecRendererProps) {
  // RE-VALIDATE FIRST. Never trust the caller's claim that the spec is valid.
  const result = useMemo(() => validateViewSpec(spec), [spec]);

  if (!result.ok) {
    return <SafeFallback errors={result.errors} />;
  }

  // Safe to narrow now: validateViewSpec guarantees the shape (except the
  // indices in result.unknownBlocks, which render only the inert placeholder).
  const view = spec as ViewSpec;
  const unknown = new Set(result.unknownBlocks);

  // Layout mode: v1 specs keep the original auto-fit grid; spec_version 2 (or
  // an explicit grid) renders the N-column grid with per-block spans.
  const v2Layout = (view.spec_version ?? 1) >= 2 || view.grid !== undefined;
  const columns = Math.min(Math.max(view.grid?.columns ?? 12, 1), 12);

  const children = view.layout.map((block, i) => {
    if (unknown.has(i)) {
      const typeName = String((block as { type?: unknown }).type ?? "unknown");
      const node = <UnknownBlockCard key={i} typeName={typeName} />;
      return v2Layout ? (
        <div key={i} style={{ gridColumn: `span ${columns}`, minWidth: 0 }}>
          {node}
        </div>
      ) : (
        node
      );
    }
    if (!v2Layout) return renderBlock(block, i, loadData);
    const span = Math.min(block.span ?? DEFAULT_SPAN[block.type] ?? columns, columns);
    return (
      // The wrapper owns grid placement; the inner card's own gridColumn (the
      // v1 "1 / -1" full-width style) is inert here because the wrapper is not
      // a grid container.
      <div key={i} style={{ gridColumn: `span ${span}`, minWidth: 0 }}>
        {renderBlock(block, i, loadData)}
      </div>
    );
  });

  return (
    <div data-testid="spec-renderer" data-spec-version={view.spec_version ?? 1}>
      <h2 style={{ fontSize: 20, fontWeight: 740, letterSpacing: "-.02em", color: "var(--ink, #2a2622)" }}>
        {view.title}
      </h2>
      <div
        style={
          v2Layout
            ? {
                display: "grid",
                gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
                gap: 16,
                marginTop: 18,
              }
            : {
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
                gap: 16,
                marginTop: 18,
              }
        }
      >
        {children}
      </div>
    </div>
  );
}

export default SpecRenderer;
