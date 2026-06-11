// Dashboards screen — named compositions of saved views, wired to the
// control-plane API via ApiClient. Follows the ReportsView/DashboardView
// conventions exactly and REUSES the trusted spec engine end to end:
//
//   * The gallery lists the tenant's dashboards from GET /dashboards
//     (kind=dashboard saved views, RLS-scoped). A fresh tenant sees the honest
//     empty state, never demo dashboards.
//   * Opening one loads GET /dashboards/{id} — the dashboard row plus every
//     referenced view resolved server-side — validates the dashboard spec with
//     validateDashboardSpec (references only, no components), then renders each
//     referenced view through the SAME trusted SpecRenderer on the grid/span
//     layout. A reference that no longer resolves renders an honest per-panel
//     notice instead of failing the whole dashboard.
//   * Creating one is pure composition: pick from the tenant's saved views
//     (GET /views), name it, and POST /dashboards with a kind=dashboard spec.
//     The server validates the spec AND that every referenced view exists.
//   * Data loading policy is identical to DashboardView: real builds resolve
//     every query to zero rows (the Cube data plane isn't deployed yet) so each
//     block honestly renders "No data yet"; mock builds use the offline fixture.
//   * Every transport failure routes through friendlyErrorMessage — raw
//     "API <code>" strings and server detail dumps never reach the DOM.

import React from "react";
import {
  ApiClient,
  buildViewDataLoader,
  defaultClient,
  friendlyErrorMessage,
  type SavedViewRow,
} from "./client";
import { SpecRenderer, type LoadData } from "../dashboard/SpecRenderer";
import { validateDashboardSpec, type DashboardSpec } from "../dashboard/dashboardSpec";
import { Spinner } from "./Spinner";

const { useState, useEffect, useCallback, useMemo } = React;

// ---------------------------------------------------------------------------
// Data loaders — identical policy to DashboardView/ReportsView. A dashboard
// composes MANY referenced views, each with its own spec and its own data, so
// the loader is resolved PER referenced view (see loadDashboardData below); the
// empty noLiveData is the calm fallback when a view's data plane is unavailable.
// ---------------------------------------------------------------------------

const noLiveData: LoadData = async () => [];

let mockLoadData: LoadData = noLiveData;
if (import.meta.env.VITE_API_MOCK !== "0" && import.meta.env.VITE_API_MOCK !== "false") {
  mockLoadData = async (query) => (await import("../dashboard/sample")).sampleLoadData(query);
}

// ---------------------------------------------------------------------------
// Styles (house style: hairline cards on the soft surface palette)
// ---------------------------------------------------------------------------

const card: React.CSSProperties = {
  border: "1px solid var(--line, #e3ddd3)",
  background: "var(--surface, #fff)",
  borderRadius: 14,
  padding: "18px 20px",
};

const ghostBtn: React.CSSProperties = {
  padding: "8px 16px",
  borderRadius: 10,
  border: "1px solid var(--line, #e3ddd3)",
  background: "transparent",
  color: "var(--ink, #2a2622)",
  fontSize: 13.5,
  fontWeight: 650,
  cursor: "pointer",
};

const primaryBtn: React.CSSProperties = {
  ...ghostBtn,
  background: "var(--ink, #2a2622)",
  color: "#fff",
  border: "1px solid var(--ink, #2a2622)",
};

const muted: React.CSSProperties = { color: "var(--ink-3, #8a8278)" };

function specTitle(row: SavedViewRow): string {
  const t = (row.spec_json as Record<string, unknown>)?.title;
  return typeof t === "string" && t.trim() ? t : row.view_id;
}

function slugify(name: string): string {
  const slug = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 64);
  return slug || "dashboard";
}

function ErrorCard({
  message,
  onRetry,
  testid,
  retryTestid,
}: {
  message: string;
  onRetry?: () => void;
  testid: string;
  retryTestid: string;
}) {
  return (
    <div
      data-testid={testid}
      style={{ ...card, border: "1px solid var(--rose, #b4413b)", fontSize: 13.5 }}
    >
      <div style={{ fontWeight: 700, marginBottom: 4 }}>Something needs another try</div>
      <p style={{ ...muted, lineHeight: 1.5 }}>{message}</p>
      {onRetry && (
        <button data-testid={retryTestid} onClick={onRetry} style={{ ...ghostBtn, marginTop: 10 }}>
          Try again
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Open dashboard — the composition rendered on the grid/span layout
// ---------------------------------------------------------------------------

function OpenDashboard({
  dashboard,
  views,
  loaders,
  fallbackLoadData,
  onBack,
}: {
  dashboard: SavedViewRow;
  views: Record<string, SavedViewRow>;
  /** Per-referenced-view data loaders (built from POST /views/{id}/data). */
  loaders: Record<string, LoadData>;
  /** The loader for a view with no resolved data yet (mock fixture or empty). */
  fallbackLoadData: LoadData;
  onBack: () => void;
}) {
  const result = useMemo(() => validateDashboardSpec(dashboard.spec_json), [dashboard.spec_json]);

  if (!result.ok) {
    // Same honesty contract as SpecRenderer's fallback: refuse, explain, draw nothing.
    return (
      <div data-testid="dashboard-spec-fallback" style={{ ...card, maxWidth: 560 }}>
        <div style={{ fontSize: 15, fontWeight: 700 }}>We could not render this dashboard</div>
        <p style={{ ...muted, fontSize: 13, marginTop: 8, lineHeight: 1.5 }}>
          The saved dashboard did not match the dashboard catalog, so nothing was drawn.
        </p>
        <button onClick={onBack} style={{ ...ghostBtn, marginTop: 12 }}>
          Back to dashboards
        </button>
      </div>
    );
  }

  const spec = dashboard.spec_json as unknown as DashboardSpec;
  const columns = Math.min(Math.max(spec.grid?.columns ?? 12, 1), 12);

  return (
    <div data-testid="dashboard-open">
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
        <button data-testid="dashboard-back" onClick={onBack} style={ghostBtn}>
          Back
        </button>
        <h2 style={{ fontSize: 20, fontWeight: 740, letterSpacing: "-.02em" }}>{spec.title}</h2>
        <span data-testid="dashboard-version" style={{ ...muted, fontSize: 12 }}>
          version {dashboard.version}
        </span>
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
          gap: 18,
        }}
      >
        {spec.items.map((item, i) => {
          const span = Math.min(item.span ?? columns, columns);
          const ref = views[item.view_id];
          return (
            <div
              key={`${item.view_id}-${i}`}
              data-testid="dashboard-panel"
              style={{ gridColumn: `span ${span}`, minWidth: 0 }}
            >
              {ref ? (
                // Each referenced view re-validates inside SpecRenderer — the
                // composition layer never relaxes the spec-not-code contract.
                // Its data comes from its OWN per-view loader (the spec's
                // CubeQueries resolved server-side); the fallback applies until
                // resolved or when that view's data plane is unavailable.
                <SpecRenderer
                  spec={ref.spec_json}
                  loadData={loaders[item.view_id] ?? fallbackLoadData}
                />
              ) : (
                <div data-testid="dashboard-panel-missing" style={card}>
                  <div style={{ fontSize: 13, fontWeight: 700 }}>View not available</div>
                  <p style={{ ...muted, fontSize: 13, marginTop: 6, lineHeight: 1.5 }}>
                    The saved view this panel references could not be found. The rest of the
                    dashboard renders normally.
                  </p>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Composer — name it, pick views, save
// ---------------------------------------------------------------------------

function Composer({
  availableViews,
  saving,
  error,
  onCancel,
  onCreate,
}: {
  availableViews: SavedViewRow[];
  saving: boolean;
  error: string | null;
  onCancel: () => void;
  onCreate: (name: string, viewIds: string[]) => void;
}) {
  const [name, setName] = useState("");
  const [picked, setPicked] = useState<string[]>([]);

  const toggle = (viewId: string) => {
    setPicked((p) => (p.includes(viewId) ? p.filter((v) => v !== viewId) : [...p, viewId]));
  };

  const canCreate = name.trim().length > 0 && picked.length > 0 && !saving;

  return (
    <div data-testid="dashboard-composer" style={{ ...card, maxWidth: 560 }}>
      <div style={{ fontSize: 15, fontWeight: 720, marginBottom: 12 }}>New dashboard</div>
      <label style={{ display: "block", fontSize: 12.5, fontWeight: 600, marginBottom: 6 }}>
        Name
        <input
          data-testid="dashboard-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Executive overview"
          style={{
            display: "block",
            width: "100%",
            marginTop: 6,
            padding: "9px 12px",
            borderRadius: 10,
            border: "1px solid var(--line, #e3ddd3)",
            fontSize: 13.5,
            boxSizing: "border-box",
          }}
        />
      </label>
      <div style={{ fontSize: 12.5, fontWeight: 600, margin: "14px 0 6px" }}>
        Views to include ({picked.length} picked)
      </div>
      {availableViews.length === 0 ? (
        <p data-testid="composer-no-views" style={{ ...muted, fontSize: 13, lineHeight: 1.5 }}>
          No saved views yet. Ask your agents for a metric or chart first — dashboards are built
          from saved views.
        </p>
      ) : (
        <div style={{ display: "grid", gap: 6 }}>
          {availableViews.map((v) => (
            <label
              key={v.view_id}
              data-testid="composer-view-option"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                fontSize: 13.5,
                padding: "7px 10px",
                border: "1px solid var(--line-2, #efe9df)",
                borderRadius: 10,
                cursor: "pointer",
              }}
            >
              <input
                type="checkbox"
                data-testid={`pick-${v.view_id}`}
                checked={picked.includes(v.view_id)}
                onChange={() => toggle(v.view_id)}
              />
              <span style={{ fontWeight: 600 }}>{specTitle(v)}</span>
              <span style={{ ...muted, fontSize: 12 }}>v{v.version}</span>
            </label>
          ))}
        </div>
      )}
      {error && (
        <p data-testid="composer-error" style={{ color: "var(--rose, #b4413b)", fontSize: 13, marginTop: 10 }}>
          {error}
        </p>
      )}
      <div style={{ display: "flex", gap: 10, marginTop: 16 }}>
        <button
          data-testid="create-dashboard"
          disabled={!canCreate}
          onClick={() => onCreate(name.trim(), picked)}
          style={{ ...primaryBtn, opacity: canCreate ? 1 : 0.5 }}
        >
          {saving ? "Creating..." : "Create dashboard"}
        </button>
        <button data-testid="composer-cancel" onClick={onCancel} style={ghostBtn}>
          Cancel
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The screen
// ---------------------------------------------------------------------------

export interface DashboardsViewProps {
  client?: ApiClient;
}

export function DashboardsView({ client }: DashboardsViewProps) {
  const api = client ?? defaultClient();
  // The loader for a referenced view before its data resolves (and for mock
  // builds, the whole time): the offline fixture in mock, empty in real.
  const fallbackLoadData = api.isMock() ? mockLoadData : noLiveData;

  const [dashboards, setDashboards] = useState<SavedViewRow[]>([]);
  const [availableViews, setAvailableViews] = useState<SavedViewRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);

  const [composing, setComposing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [composeError, setComposeError] = useState<string | null>(null);

  const [open, setOpen] = useState<{ dashboard: SavedViewRow; views: Record<string, SavedViewRow> } | null>(null);
  // Per-referenced-view data loaders, resolved when a dashboard opens (real mode).
  const [loaders, setLoaders] = useState<Record<string, LoadData>>({});
  const [openLoading, setOpenLoading] = useState(false);
  const [openError, setOpenError] = useState<string | null>(null);

  const loadList = useCallback(async () => {
    setLoading(true);
    setListError(null);
    try {
      const [dashes, views] = await Promise.all([api.listDashboards(), api.listViews()]);
      setDashboards(dashes);
      setAvailableViews(views);
    } catch (e) {
      setListError(friendlyErrorMessage(e, "Couldn't load your dashboards. Please try again."));
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  const openDashboard = useCallback(
    async (viewId: string) => {
      setOpenLoading(true);
      setOpenError(null);
      setLoaders({});
      try {
        const resolved = await api.getDashboard(viewId);
        setOpen(resolved);
        // Real mode: resolve EACH referenced view's data through its own
        // POST /views/{id}/data, building a per-view loader. A single view's
        // data-plane failure degrades only that panel (it keeps the empty
        // fallback); the rest of the dashboard renders real rows. Mock builds
        // keep the offline fixture loader for every panel.
        if (!api.isMock()) {
          const entries = await Promise.all(
            Object.entries(resolved.views).map(async ([id, row]) => {
              try {
                const data = await api.loadViewData(id);
                return [id, buildViewDataLoader(row.spec_json, data)] as const;
              } catch {
                return [id, noLiveData] as const;
              }
            }),
          );
          setLoaders(Object.fromEntries(entries));
        }
      } catch (e) {
        setOpenError(friendlyErrorMessage(e, "Couldn't open that dashboard. Please try again."));
      } finally {
        setOpenLoading(false);
      }
    },
    [api]
  );

  const createDashboard = useCallback(
    async (name: string, viewIds: string[]) => {
      setSaving(true);
      setComposeError(null);
      const spec: DashboardSpec = {
        kind: "dashboard",
        view_id: slugify(name),
        title: name,
        spec_version: 2,
        grid: { columns: 12 },
        items: viewIds.map((view_id) => ({ view_id, span: 6 })),
      };
      try {
        const row = await api.saveDashboard({ spec: spec as unknown as Record<string, unknown> });
        setComposing(false);
        await loadList();
        await openDashboard(row.view_id);
      } catch (e) {
        setComposeError(
          friendlyErrorMessage(e, "Couldn't create that dashboard. Please try again.")
        );
      } finally {
        setSaving(false);
      }
    },
    [api, loadList, openDashboard]
  );

  return (
    <div
      data-testid="dashboards-view"
      style={{ maxWidth: 1100, margin: "0 auto", padding: "40px 24px", fontFamily: "system-ui, sans-serif" }}
    >
      {open ? (
        <OpenDashboard
          dashboard={open.dashboard}
          views={open.views}
          loaders={loaders}
          fallbackLoadData={fallbackLoadData}
          onBack={() => setOpen(null)}
        />
      ) : (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
            <h2 style={{ fontSize: 20, fontWeight: 740, letterSpacing: "-.02em" }}>Dashboards</h2>
            {!composing && (
              <button
                data-testid="new-dashboard"
                onClick={() => {
                  setComposeError(null);
                  setComposing(true);
                }}
                style={{ ...primaryBtn, marginLeft: "auto" }}
              >
                New dashboard
              </button>
            )}
          </div>

          {openError && (
            <ErrorCard
              message={openError}
              testid="dashboard-open-error"
              retryTestid="dashboard-open-retry"
            />
          )}

          {composing && (
            <div style={{ marginBottom: 18 }}>
              <Composer
                availableViews={availableViews}
                saving={saving}
                error={composeError}
                onCancel={() => setComposing(false)}
                onCreate={(name, viewIds) => void createDashboard(name, viewIds)}
              />
            </div>
          )}

          {loading && <Spinner testid="dashboards-loading" label="Loading dashboards..." />}
          {openLoading && <Spinner testid="dashboard-opening" label="Opening dashboard..." />}

          {!loading && listError && (
            <ErrorCard
              message={listError}
              onRetry={() => void loadList()}
              testid="dashboards-error"
              retryTestid="dashboards-retry"
            />
          )}

          {!loading && !listError && dashboards.length === 0 && !composing && (
            <div data-testid="dashboards-empty" style={{ ...card, textAlign: "center", padding: "26px 24px" }}>
              <div style={{ fontSize: 15, fontWeight: 700 }}>No dashboards yet</div>
              <p style={{ ...muted, fontSize: 13, marginTop: 6, lineHeight: 1.5 }}>
                A dashboard is a named set of your saved views, arranged on a grid. Create one to
                pin the numbers your team checks every morning.
              </p>
            </div>
          )}

          {!loading && !listError && dashboards.length > 0 && (
            <div
              data-testid="dashboards-gallery"
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
                gap: 14,
              }}
            >
              {dashboards.map((d) => {
                const items = ((d.spec_json as Record<string, unknown>).items ?? []) as unknown[];
                return (
                  <button
                    key={d.view_id}
                    data-testid="dashboard-card"
                    data-view-id={d.view_id}
                    onClick={() => void openDashboard(d.view_id)}
                    style={{ ...card, textAlign: "left", cursor: "pointer", display: "block" }}
                  >
                    <div style={{ fontSize: 15, fontWeight: 720, color: "var(--ink, #2a2622)" }}>
                      {specTitle(d)}
                    </div>
                    <div style={{ ...muted, fontSize: 12.5, marginTop: 6 }}>
                      {items.length} view{items.length === 1 ? "" : "s"} · version {d.version}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default DashboardsView;
