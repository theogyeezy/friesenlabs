// Pipeline board, wired to the control-plane API via ApiClient — the real-mode
// counterpart of the FLStore CRM prototype (src/screens/crm.tsx, mock mode
// only). Everything rendered here is honest:
//
//   * The stage columns and deal cards come straight from GET /deals (RLS-
//     scoped, claims-bound server-side). Nothing is invented client-side.
//   * Clicking a card opens a detail drawer fed by GET /deals/{id} (deal +
//     recent activities).
//   * The move-stage control calls POST /deals/{id}/move-stage, which does NOT
//     move the deal: the server lands a Greenlight proposal and answers
//     {queued: true}. The UI says exactly that — a "queued for approval in
//     Greenlight" toast with a link to the queue — and keeps showing the
//     CURRENT stage everywhere until a human approves. No optimistic move,
//     ever.
//   * A 404 from GET /deals means the live API image predates this route (the
//     web can deploy ahead of the API): that renders a calm "rolling out"
//     state with a refresh affordance — NOT an error wall.
//   * Raw transport strings ("API <code>", server detail dumps) never reach
//     the DOM — every catch routes through friendlyErrorMessage / honest
//     per-status copy.

import React from "react";
import {
  ApiClient,
  ApiError,
  defaultClient,
  friendlyErrorMessage,
  type DealCard,
  type DealDetailResponse,
  type DealStageGroup,
  type ListDealsResponse,
  type MoveStageResponse,
} from "./client";
import { Spinner } from "./Spinner";

const { useState, useEffect, useCallback, useMemo } = React;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatMoney(v: number | null | undefined, currency?: string | null): string {
  if (v === null || v === undefined) return "—";
  const cur = currency === null || currency === undefined || currency === "USD" ? "$" : `${currency} `;
  if (Math.abs(v) >= 1000) return `${cur}${(v / 1000).toFixed(1)}k`;
  return `${cur}${v.toFixed(0)}`;
}

function formatWhen(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

// Honest per-status copy for the move-stage contract. The API authors
// machine-facing detail strings — map statuses to user copy without ever
// exposing the raw "API <code>" message.
function moveErrorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 503) {
      return "The deals data plane isn't configured on this deployment yet, so stage moves are unavailable for now.";
    }
    if (e.status === 404) {
      return "That deal can't be found anymore. Refresh the board and try again.";
    }
    if (e.status === 409) {
      // Same-stage or gate-blocked — the server's detail is human-authored.
      return friendlyErrorMessage(e, "That move can't be queued right now.");
    }
    if (e.status === 422) {
      return "Pick a stage to move this deal to first.";
    }
  }
  return friendlyErrorMessage(e, "Couldn't queue that move. Please try again.");
}

// ---------------------------------------------------------------------------
// Styles (house style: hairline cards on the soft surface palette)
// ---------------------------------------------------------------------------

const card: React.CSSProperties = {
  border: "1px solid var(--line, #e3ddd3)",
  background: "var(--surface, #fff)",
  borderRadius: 14,
  padding: "18px 20px",
  marginBottom: 16,
};

const dealCardStyle: React.CSSProperties = {
  border: "1px solid var(--line, #e3ddd3)",
  background: "var(--surface, #fff)",
  borderRadius: 12,
  padding: "12px 14px",
  marginBottom: 10,
  cursor: "pointer",
  textAlign: "left",
  width: "100%",
  display: "block",
  fontFamily: "inherit",
};

const primaryBtn: React.CSSProperties = {
  padding: "8px 16px",
  borderRadius: 10,
  border: "none",
  background: "var(--accent, #2a2622)",
  color: "#fff",
  fontSize: 13.5,
  fontWeight: 650,
  cursor: "pointer",
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

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface QueuedMove {
  to_stage: string;
  approval_id: number | string | null;
}

export interface PipelineBoardProps {
  client?: ApiClient;
  /** Navigate to the Greenlight queue (the shell passes navTo("approvals")).
   * Without it the toast links to the ?view=greenlight seam. */
  onOpenGreenlight?: () => void;
}

export function PipelineBoard({ client, onOpenGreenlight }: PipelineBoardProps) {
  const api = client ?? defaultClient();
  const [data, setData] = useState<ListDealsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // The live API image may predate /deals (the web deploys ahead): 404 = the
  // route isn't rolled out yet — an expected state, not a failure.
  const [rollout, setRollout] = useState(false);

  // Detail drawer state.
  const [openDealId, setOpenDealId] = useState<string | null>(null);
  const [detail, setDetail] = useState<DealDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  // Move-stage control state.
  const [moveTo, setMoveTo] = useState("");
  const [moveBusy, setMoveBusy] = useState(false);
  const [moveError, setMoveError] = useState<string | null>(null);
  // Moves we queued this session, keyed by deal id — shown as "awaiting
  // approval" so the user knows it's pending WITHOUT pretending it happened.
  const [queued, setQueued] = useState<Record<string, QueuedMove>>({});
  const [toast, setToast] = useState<MoveStageResponse | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setRollout(false);
    try {
      setData(await api.listDeals());
    } catch (e) {
      setData(null);
      if (e instanceof ApiError && e.status === 404) {
        setRollout(true); // route not deployed yet — honest, calm, retryable
      } else {
        setError(friendlyErrorMessage(e, "Couldn't load your pipeline. Please try again."));
      }
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  const openDeal = useCallback(
    async (deal: DealCard) => {
      setOpenDealId(deal.id);
      setDetail(null);
      setDetailError(null);
      setMoveTo("");
      setMoveError(null);
      setDetailLoading(true);
      try {
        setDetail(await api.getDeal(deal.id));
      } catch (e) {
        setDetailError(friendlyErrorMessage(e, "Couldn't load this deal. Please try again."));
      } finally {
        setDetailLoading(false);
      }
    },
    [api],
  );

  const closeDrawer = useCallback(() => {
    setOpenDealId(null);
    setDetail(null);
    setDetailError(null);
    setMoveError(null);
    setMoveTo("");
  }, []);

  // Esc closes the drawer (house pattern for slide-overs).
  useEffect(() => {
    if (openDealId === null) return;
    const k = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeDrawer();
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [openDealId, closeDrawer]);

  const queueMove = useCallback(async () => {
    if (!detail || !moveTo) return;
    setMoveBusy(true);
    setMoveError(null);
    try {
      const res = await api.moveDealStage(detail.deal.id, { to_stage: moveTo });
      // HONEST: {queued} means a human still has to approve. The board keeps
      // the deal in its CURRENT stage; we only record the pending intent.
      setQueued((q) => ({
        ...q,
        [detail.deal.id]: { to_stage: res.to_stage, approval_id: res.approval_id },
      }));
      setToast(res);
      setMoveTo("");
      window.setTimeout(() => setToast(null), 6000);
    } catch (e) {
      setMoveError(moveErrorMessage(e));
    } finally {
      setMoveBusy(false);
    }
  }, [api, detail, moveTo]);

  // Stage options for the move control: every stage the board knows about,
  // minus the deal's current one. Labels come from the server's groups.
  const stageOptions = useMemo(() => {
    if (!data || !detail) return [] as Array<{ stage: string; label: string }>;
    return data.stages
      .map((s) => ({ stage: s.stage, label: s.label }))
      .filter((s) => s.stage !== detail.deal.stage);
  }, [data, detail]);

  const stages: DealStageGroup[] = data?.stages ?? [];
  const total = data?.total ?? 0;
  const pendingMove = detail ? queued[detail.deal.id] : undefined;

  return (
    <div
      data-testid="pipeline-board"
      style={{ maxWidth: 1280, margin: "0 auto", padding: "32px 24px", fontFamily: "system-ui, sans-serif" }}
    >
      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 12, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--ink-3, #8a8278)" }}>
          Uplift CRM
        </div>
        <h1 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.02em", margin: "6px 0 4px" }}>Pipeline</h1>
        <p style={{ color: "var(--ink-3, #8a8278)", fontSize: 14 }}>
          Your deals by stage. Stage moves go through Greenlight — nothing changes until you approve it there.
        </p>
        {/* Only claim a count once we actually know it (post-load, no error). */}
        {!loading && !error && !rollout && data !== null && (
          <div data-testid="pipeline-count" style={{ marginTop: 10, fontSize: 13, color: "var(--ink-3, #8a8278)" }}>
            {total} open {total === 1 ? "deal" : "deals"}
          </div>
        )}
      </div>

      {loading && <Spinner testid="pipeline-loading" label="Loading your pipeline..." />}

      {/* The live API image may predate /deals: a calm rollout note, not an error wall. */}
      {rollout && (
        <div data-testid="pipeline-rollout" style={{ ...card, color: "var(--ink, #2a2622)", fontSize: 13.5 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Pipeline API is rolling out</div>
          <p style={{ color: "var(--ink-3, #8a8278)", lineHeight: 1.5 }}>
            Your deployment doesn&rsquo;t serve the deals endpoint yet — refresh after the next API deploy.
            Nothing is wrong with your data.
          </p>
          <button data-testid="pipeline-rollout-refresh" onClick={() => void load()} style={{ ...ghostBtn, marginTop: 10 }}>
            Refresh
          </button>
        </div>
      )}

      {error && (
        <div
          data-testid="pipeline-error"
          style={{ ...card, borderColor: "var(--rose, #b4413b)", color: "var(--ink, #2a2622)", fontSize: 13.5 }}
        >
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Something needs another try</div>
          <p style={{ color: "var(--ink-3, #8a8278)", lineHeight: 1.5 }}>{error}</p>
          <button data-testid="pipeline-retry" onClick={() => void load()} style={{ ...ghostBtn, marginTop: 10 }}>
            Try again
          </button>
        </div>
      )}

      {!loading && !error && !rollout && data !== null && total === 0 && (
        <div data-testid="pipeline-empty" style={{ ...card, textAlign: "center", color: "var(--ink-3, #8a8278)" }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: "var(--ink, #2a2622)" }}>No deals yet</div>
          <p style={{ fontSize: 13, marginTop: 4 }}>
            When deals land in your workspace — synced from your CRM or created by your agents —
            they&rsquo;ll appear here by stage.
          </p>
        </div>
      )}

      {!loading && !error && !rollout && data !== null && total > 0 && (
        <div
          data-testid="pipeline-columns"
          style={{ display: "flex", gap: 14, alignItems: "flex-start", overflowX: "auto", paddingBottom: 12 }}
        >
          {stages.map((col) => (
            <div
              key={col.stage}
              data-testid="stage-col"
              data-stage={col.stage}
              style={{ minWidth: 230, width: 230, flexShrink: 0 }}
            >
              <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", padding: "0 2px 8px" }}>
                <div style={{ fontSize: 12.5, fontWeight: 700, letterSpacing: ".04em", textTransform: "uppercase", color: "var(--ink-3, #8a8278)" }}>
                  {col.label}
                </div>
                <div data-testid="stage-count" style={{ fontSize: 12, color: "var(--ink-3, #8a8278)" }}>
                  {col.count}{col.total_amount > 0 ? ` · ${formatMoney(col.total_amount)}` : ""}
                </div>
              </div>
              {col.deals.length === 0 ? (
                <div
                  data-testid="stage-empty"
                  style={{ border: "1px dashed var(--line, #e3ddd3)", borderRadius: 12, padding: "14px 12px", fontSize: 12.5, color: "var(--ink-3, #8a8278)", textAlign: "center" }}
                >
                  No deals
                </div>
              ) : (
                col.deals.map((d) => (
                  <button
                    key={d.id}
                    data-testid="deal-card"
                    data-deal-id={d.id}
                    onClick={() => void openDeal(d)}
                    style={dealCardStyle}
                  >
                    <div style={{ fontSize: 13.5, fontWeight: 700, color: "var(--ink, #2a2622)", lineHeight: 1.35 }}>
                      {d.title ?? "Untitled deal"}
                    </div>
                    {d.company_name && (
                      <div style={{ fontSize: 12.5, color: "var(--ink-3, #8a8278)", marginTop: 3 }}>{d.company_name}</div>
                    )}
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginTop: 8 }}>
                      <span style={{ fontSize: 13, fontWeight: 700, color: "var(--ink, #2a2622)" }}>
                        {formatMoney(d.amount, d.currency)}
                      </span>
                      {queued[d.id] && (
                        <span
                          data-testid="deal-pending-move"
                          style={{ fontSize: 11, fontWeight: 650, color: "oklch(0.5 0.12 60)", background: "oklch(0.96 0.05 85)", borderRadius: 999, padding: "2px 8px" }}
                        >
                          move awaiting approval
                        </span>
                      )}
                    </div>
                  </button>
                ))
              )}
            </div>
          ))}
        </div>
      )}

      {/* ----------------------------------------------------------------- drawer */}
      {openDealId !== null && (
        <>
          <div
            data-testid="drawer-scrim"
            onClick={closeDrawer}
            style={{ position: "fixed", inset: 0, background: "rgba(20, 16, 12, .28)", zIndex: 50 }}
          />
          <div
            data-testid="deal-drawer"
            role="dialog"
            aria-label="Deal detail"
            style={{
              position: "fixed",
              top: 0,
              right: 0,
              bottom: 0,
              width: "min(440px, 92vw)",
              background: "var(--surface, #fff)",
              borderLeft: "1px solid var(--line, #e3ddd3)",
              boxShadow: "-12px 0 40px rgba(20,16,12,.12)",
              zIndex: 51,
              padding: "24px 24px 32px",
              overflowY: "auto",
              fontFamily: "system-ui, sans-serif",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
              <div style={{ fontSize: 12, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--ink-3, #8a8278)" }}>
                Deal
              </div>
              <button data-testid="drawer-close" onClick={closeDrawer} style={{ ...ghostBtn, padding: "5px 12px" }}>
                Close
              </button>
            </div>

            {detailLoading && <Spinner testid="drawer-loading" label="Loading the deal..." />}

            {detailError && (
              <div
                data-testid="drawer-error"
                style={{ ...card, borderColor: "var(--rose, #b4413b)", fontSize: 13.5 }}
              >
                <div style={{ fontWeight: 700, marginBottom: 4 }}>Something needs another try</div>
                <p style={{ color: "var(--ink-3, #8a8278)", lineHeight: 1.5 }}>{detailError}</p>
              </div>
            )}

            {detail !== null && (
              <>
                <h2 data-testid="drawer-title" style={{ fontSize: 20, fontWeight: 760, letterSpacing: "-.02em", margin: "0 0 6px" }}>
                  {detail.deal.title ?? "Untitled deal"}
                </h2>
                <div style={{ fontSize: 13, color: "var(--ink-3, #8a8278)" }}>
                  {detail.deal.company_name ?? "No company"}
                  {detail.deal.contact_name ? ` · ${detail.deal.contact_name}` : ""}
                </div>

                <div style={{ display: "flex", gap: 10, margin: "14px 0 4px", flexWrap: "wrap" }}>
                  {/* The CURRENT stage — stays put until a Greenlight approval lands. */}
                  <span
                    data-testid="drawer-stage"
                    data-stage={detail.deal.stage}
                    style={{ fontSize: 12.5, fontWeight: 650, padding: "4px 12px", borderRadius: 999, background: "var(--accent-soft, #f4f1ea)", color: "var(--ink, #2a2622)" }}
                  >
                    {stages.find((s) => s.stage === detail.deal.stage)?.label ?? detail.deal.stage}
                  </span>
                  <span style={{ fontSize: 13.5, fontWeight: 700, alignSelf: "center" }}>
                    {formatMoney(detail.deal.amount, detail.deal.currency)}
                  </span>
                  {detail.deal.created_at && (
                    <span style={{ fontSize: 12.5, color: "var(--ink-3, #8a8278)", alignSelf: "center" }}>
                      opened {formatWhen(detail.deal.created_at)}
                    </span>
                  )}
                </div>

                {pendingMove && (
                  <div
                    data-testid="drawer-pending-move"
                    style={{ fontSize: 13, lineHeight: 1.5, margin: "10px 0 0", padding: "10px 12px", borderRadius: 10, background: "oklch(0.96 0.05 85)", color: "oklch(0.42 0.1 60)" }}
                  >
                    A move to <b>{stages.find((s) => s.stage === pendingMove.to_stage)?.label ?? pendingMove.to_stage}</b> is
                    waiting for approval in Greenlight. The deal stays here until it&rsquo;s approved.
                  </div>
                )}

                {/* ------------------------------------------------ move-stage */}
                <div style={{ margin: "18px 0 6px", fontSize: 12, fontWeight: 600, color: "var(--ink-3, #8a8278)" }}>
                  Move stage (goes to Greenlight for approval)
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <select
                    data-testid="move-select"
                    value={moveTo}
                    disabled={moveBusy}
                    onChange={(e) => setMoveTo(e.target.value)}
                    style={{
                      flex: 1,
                      borderRadius: 10,
                      border: "1px solid var(--line, #e3ddd3)",
                      padding: "8px 10px",
                      fontSize: 13.5,
                      fontFamily: "inherit",
                      background: "var(--surface, #fff)",
                      color: "var(--ink, #2a2622)",
                    }}
                  >
                    <option value="">Choose a stage…</option>
                    {stageOptions.map((s) => (
                      <option key={s.stage} value={s.stage}>
                        {s.label}
                      </option>
                    ))}
                  </select>
                  <button
                    data-testid="move-queue-btn"
                    disabled={moveBusy || moveTo === ""}
                    onClick={() => void queueMove()}
                    style={{ ...primaryBtn, opacity: moveBusy || moveTo === "" ? 0.6 : 1 }}
                  >
                    {moveBusy ? "Queueing..." : "Queue move"}
                  </button>
                </div>
                {moveError && (
                  <div
                    data-testid="move-error"
                    style={{ fontSize: 13, lineHeight: 1.5, marginTop: 10, padding: "10px 12px", borderRadius: 10, color: "var(--rose, #b4413b)", background: "oklch(0.97 0.02 18)" }}
                  >
                    {moveError}
                  </div>
                )}

                {/* ------------------------------------------------ activities */}
                <div style={{ margin: "22px 0 8px", fontSize: 12, fontWeight: 600, color: "var(--ink-3, #8a8278)" }}>
                  Recent activity
                </div>
                {detail.activities.length === 0 ? (
                  <div data-testid="activities-empty" style={{ fontSize: 13, color: "var(--ink-3, #8a8278)" }}>
                    No activity logged on this deal yet.
                  </div>
                ) : (
                  detail.activities.map((a, i) => (
                    <div
                      key={a.id ?? i}
                      data-testid="activity-item"
                      style={{ borderTop: i === 0 ? "none" : "1px solid var(--line-2, #efe9df)", padding: "10px 2px" }}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
                        <span style={{ fontSize: 11.5, fontWeight: 700, letterSpacing: ".05em", textTransform: "uppercase", color: "var(--ink-3, #8a8278)" }}>
                          {a.kind ?? "note"}
                        </span>
                        <span style={{ fontSize: 11.5, color: "var(--ink-3, #8a8278)" }}>{formatWhen(a.occurred_at)}</span>
                      </div>
                      {a.body && (
                        <p style={{ fontSize: 13, color: "var(--ink, #2a2622)", lineHeight: 1.5, margin: "4px 0 0" }}>{a.body}</p>
                      )}
                    </div>
                  ))
                )}
              </>
            )}
          </div>
        </>
      )}

      {/* ----------------------------------------------------------------- toast */}
      {toast && (
        <div
          data-testid="pipeline-toast"
          style={{
            position: "fixed",
            bottom: 24,
            left: "50%",
            transform: "translateX(-50%)",
            background: "var(--ink, #2a2622)",
            color: "#fff",
            borderRadius: 12,
            padding: "12px 18px",
            fontSize: 13.5,
            fontWeight: 600,
            zIndex: 60,
            display: "flex",
            gap: 14,
            alignItems: "center",
            maxWidth: "min(560px, 92vw)",
          }}
        >
          <span>
            Queued for approval in Greenlight — the deal stays in its current stage until you approve it.
          </span>
          {onOpenGreenlight ? (
            <button
              data-testid="toast-greenlight-link"
              onClick={() => {
                setToast(null);
                onOpenGreenlight();
              }}
              style={{ background: "transparent", border: "none", color: "#f3c87a", fontSize: 13.5, fontWeight: 700, cursor: "pointer", whiteSpace: "nowrap", padding: 0, fontFamily: "inherit" }}
            >
              Review in Greenlight
            </button>
          ) : (
            <a
              data-testid="toast-greenlight-link"
              href="/?view=greenlight"
              style={{ color: "#f3c87a", fontSize: 13.5, fontWeight: 700, whiteSpace: "nowrap", textDecoration: "none" }}
            >
              Review in Greenlight
            </a>
          )}
        </div>
      )}
    </div>
  );
}

export default PipelineBoard;
