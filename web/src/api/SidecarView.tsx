// Sidecar — the agentic layer, real mode.
//
// Reads grounded next-action suggestions over the tenant's CRM (GET /sidecar/suggestions): aging
// open deals, unreachable contacts, deals with no contact attached. Each suggestion is backed by a
// REAL row — nothing is fabricated. Accepting one (POST /sidecar/act) enqueues a DRAFT in Greenlight;
// Sidecar never writes to the CRM itself, so the row only changes after the user signs off there.
//
// HONEST states:
//   * 503 (no data plane on this deployment) → a calm notice, never a fake suggestion list.
//   * Empty → "You're all caught up" (a real, earned empty state).
//   * truncated → an explicit "showing top N of M" (no silent truncation).
//   * An accepted suggestion shows "Queued in Greenlight" with a link to the approvals queue; a 409
//     (the row changed and it no longer applies) reloads the list honestly.

import React from "react";
import {
  ApiClient,
  ApiError,
  defaultClient,
  friendlyErrorMessage,
  type SidecarSuggestion,
} from "./client";
import { Spinner } from "./Spinner";

const { useState, useCallback, useEffect } = React;

const card: React.CSSProperties = {
  border: "1px solid var(--line, #e3ddd3)",
  background: "var(--surface, #fff)",
  borderRadius: 14,
  padding: "16px 18px",
};
const muted: React.CSSProperties = { color: "var(--ink-3, #8a8278)" };
const primaryBtn: React.CSSProperties = {
  padding: "8px 14px",
  borderRadius: 10,
  border: "none",
  background: "var(--ink, #2a2622)",
  color: "var(--bg, #fff)",
  fontSize: 13,
  fontWeight: 660,
  cursor: "pointer",
};

function money(cents: number | null): string | null {
  if (cents == null) return null;
  return `$${Number(cents).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

export interface SidecarViewProps {
  client?: ApiClient;
  onOpenGreenlight?: () => void;
  /** Optional callbacks to open a deal or contact panel in the shell. When
   * absent the entity chip falls back to a plain display (no link). */
  onOpenDeal?: (id: string) => void;
  onOpenContact?: (id: string) => void;
}

export function SidecarView({ client, onOpenGreenlight, onOpenDeal, onOpenContact }: SidecarViewProps) {
  const api = client ?? defaultClient();

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  const [suggestions, setSuggestions] = useState<SidecarSuggestion[]>([]);
  const [total, setTotal] = useState(0);
  const [truncated, setTruncated] = useState(false);
  // Per-suggestion UI state keyed by id: "acting" while the POST is in flight, "queued" after.
  const [busy, setBusy] = useState<Record<string, "acting" | "queued">>({});
  const [actError, setActError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    setUnavailable(false);
    // NB: do NOT clear actError here — the 409 path sets it and then reloads the list, and we want
    // the "that suggestion changed" notice to survive that refresh. actError is cleared at the
    // start of the next accept() instead.
    try {
      const res = await api.getSidecarSuggestions();
      setSuggestions(res.suggestions);
      setTotal(res.total);
      setTruncated(res.truncated);
      setBusy({});
    } catch (e) {
      if (e instanceof ApiError && (e.status === 503 || e.status === 404)) {
        setUnavailable(true);
      } else {
        setLoadError(friendlyErrorMessage(e));
      }
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  const accept = useCallback(async (s: SidecarSuggestion) => {
    setActError(null);
    setBusy((b) => ({ ...b, [s.id]: "acting" }));
    try {
      await api.actOnSidecarSuggestion(s.id);
      setBusy((b) => ({ ...b, [s.id]: "queued" }));
    } catch (e) {
      // 409: the underlying row changed and the suggestion no longer applies — reload honestly.
      if (e instanceof ApiError && e.status === 409) {
        setActError("That suggestion changed and no longer applies. Refreshed the list.");
        void load();
        return;
      }
      setActError(friendlyErrorMessage(e));
      setBusy((b) => { const n = { ...b }; delete n[s.id]; return n; });
    }
  }, [api, load]);

  if (loading) {
    return (
      <div data-testid="sidecar-loading" style={{ ...card, ...muted, display: "flex", gap: 10, alignItems: "center" }}>
        <Spinner testid="sidecar-spinner" /> Looking across your tools…
      </div>
    );
  }

  if (unavailable) {
    return (
      <div data-testid="sidecar-unavailable" style={{ ...card, ...muted }}>
        Sidecar isn’t available on this deployment yet.
      </div>
    );
  }

  if (loadError) {
    return (
      <div data-testid="sidecar-error" role="alert" style={{ ...card }}>
        <div style={{ marginBottom: 10 }}>{loadError}</div>
        <button style={primaryBtn} onClick={() => void load()}>Try again</button>
      </div>
    );
  }

  if (suggestions.length === 0) {
    return (
      <div data-testid="sidecar-empty" style={{ ...card, ...muted }}>
        You’re all caught up — Sidecar has no suggestions right now. As your deals and contacts move,
        new next-actions will surface here.
      </div>
    );
  }

  return (
    <div data-testid="sidecar-view" style={{ display: "grid", gap: 12 }}>
      <div style={{ ...muted, fontSize: 13 }}>
        Sidecar works on top of your CRM and surfaces the next move. Accepting one sends a draft to
        Greenlight for your sign-off — nothing changes until you approve it.
      </div>

      {suggestions.map((s) => {
        const state = busy[s.id];
        const val = money(s.value_at_stake);
        // Determine if there's a callback for this entity type.
        const openEntityCb =
          s.entity_type === "deal" ? onOpenDeal :
          s.entity_type === "contact" ? onOpenContact :
          undefined;
        const entityLinkLabel =
          s.entity_type === "deal" ? "View deal →" :
          s.entity_type === "contact" ? "View contact →" :
          null;
        return (
          <div key={s.id} data-testid={`sidecar-suggestion-${s.id}`} style={{ ...card, display: "flex", gap: 14, alignItems: "flex-start" }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 660, fontSize: 14.5 }}>{s.title}</div>
              <div style={{ ...muted, fontSize: 13, marginTop: 3 }}>{s.detail}</div>
              <div style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "center" }}>
                {openEntityCb && entityLinkLabel ? (
                  <button
                    data-testid={`sidecar-entity-link-${s.id}`}
                    style={{
                      ...muted,
                      fontSize: 11,
                      textTransform: "uppercase",
                      letterSpacing: ".04em",
                      background: "none",
                      border: "none",
                      cursor: "pointer",
                      padding: 0,
                      textDecoration: "underline",
                    }}
                    onClick={() => openEntityCb(s.entity_id)}
                  >
                    {entityLinkLabel}
                  </button>
                ) : (
                  <span style={{ ...muted, fontSize: 11, textTransform: "uppercase", letterSpacing: ".04em" }}>
                    {s.entity_type}
                  </span>
                )}
                {val && <span style={{ fontSize: 11.5, fontWeight: 600 }}>· {val}</span>}
              </div>
            </div>
            <div style={{ flexShrink: 0 }}>
              {state === "queued" ? (
                onOpenGreenlight ? (
                  <button
                    data-testid={`sidecar-queued-${s.id}`}
                    style={{ ...primaryBtn, background: "transparent", color: "var(--ink, #2a2622)", border: "1px solid var(--line, #e3ddd3)" }}
                    onClick={onOpenGreenlight}
                  >
                    Queued ✓ — view in Greenlight
                  </button>
                ) : (
                  <a
                    data-testid={`sidecar-queued-${s.id}`}
                    href="/?view=greenlight"
                    style={{ ...primaryBtn, background: "transparent", color: "var(--ink, #2a2622)", border: "1px solid var(--line, #e3ddd3)", display: "inline-block", textDecoration: "none" }}
                  >
                    Queued ✓ — view in Greenlight
                  </a>
                )
              ) : (
                <button
                  data-testid={`sidecar-accept-${s.id}`}
                  style={{ ...primaryBtn, opacity: state === "acting" ? 0.6 : 1 }}
                  disabled={state === "acting"}
                  onClick={() => void accept(s)}
                >
                  {state === "acting" ? "Sending…" : "Send to Greenlight"}
                </button>
              )}
            </div>
          </div>
        );
      })}

      {truncated && (
        <div data-testid="sidecar-truncated" style={{ ...muted, fontSize: 12 }}>
          Showing the top {suggestions.length} of {total} suggestions.
        </div>
      )}
      {actError && (
        <div data-testid="sidecar-act-error" role="alert" style={{ ...card, color: "var(--rose, #b4413b)", fontSize: 13 }}>
          {actError}
        </div>
      )}
    </div>
  );
}

export default SidecarView;
