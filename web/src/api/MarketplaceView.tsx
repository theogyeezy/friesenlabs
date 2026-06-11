// Agent marketplace (real-mode) — browse the committed starter "ready-made
// agents" (GET /studio/templates) and add one to your library
// (POST /studio/templates/{id}/instantiate). The real, honest counterpart of the
// FLStore agent-market demo screen.
//
// Everything here is HONEST:
//   * The catalog is exactly what the server returns — no invented agents.
//   * "Add to my agents" instantiates the template as a DRAFT playbook (the same
//     gated path Studio uses); we say "added as a draft", never "live".
//   * An empty catalog, a load error, or a 503 each render an honest state.
//   * Raw transport strings never reach the DOM (friendlyErrorMessage).

import React from "react";
import {
  ApiClient,
  ApiError,
  defaultClient,
  friendlyErrorMessage,
  type StudioTemplateSummary,
} from "./client";
import { Spinner } from "./Spinner";

const { useState, useCallback, useEffect } = React;

const card: React.CSSProperties = {
  border: "1px solid var(--line, #e3ddd3)",
  background: "var(--surface, #fff)",
  borderRadius: 14,
  padding: "18px 20px",
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

/** Title-case a template id like "lead_qualifier" → "Lead Qualifier". */
function titleFor(id: string): string {
  return id.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export interface MarketplaceViewProps {
  client?: ApiClient;
  /** Optional: navigate to Studio after hiring (where the draft lands). */
  onOpenStudio?: () => void;
}

export function MarketplaceView({ client, onOpenStudio }: MarketplaceViewProps) {
  const api = client ?? defaultClient();

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [templates, setTemplates] = useState<StudioTemplateSummary[]>([]);

  // Per-card hire state.
  const [hiring, setHiring] = useState<Record<string, boolean>>({});
  const [hired, setHired] = useState<Record<string, boolean>>({});
  const [hireError, setHireError] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setUnavailable(false);
    try {
      setTemplates(await api.getStudioTemplates());
    } catch (e) {
      if (e instanceof ApiError && e.status === 503) setUnavailable(true);
      else setError(friendlyErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  const hire = useCallback(
    async (id: string) => {
      setHiring((h) => ({ ...h, [id]: true }));
      setHireError((e) => ({ ...e, [id]: "" }));
      try {
        await api.instantiateTemplate(id);
        setHired((h) => ({ ...h, [id]: true }));
      } catch (e) {
        setHireError((m) => ({ ...m, [id]: friendlyErrorMessage(e) }));
      } finally {
        setHiring((h) => ({ ...h, [id]: false }));
      }
    },
    [api],
  );

  if (loading) {
    return (
      <div data-testid="marketplace-loading" style={{ ...card, ...muted, display: "flex", gap: 10, alignItems: "center" }}>
        <Spinner /> Loading the marketplace…
      </div>
    );
  }
  if (unavailable) {
    return (
      <div data-testid="marketplace-unavailable" style={{ ...card, ...muted }}>
        The agent marketplace isn’t available on this deployment yet.
      </div>
    );
  }
  if (error) {
    return (
      <div data-testid="marketplace-error" style={{ ...card }}>
        <div style={{ marginBottom: 10 }}>{error}</div>
        <button style={primaryBtn} onClick={() => void load()}>Try again</button>
      </div>
    );
  }
  if (templates.length === 0) {
    return (
      <div data-testid="marketplace-empty" style={{ ...card, ...muted }}>
        No ready-made agents are published yet.
      </div>
    );
  }

  return (
    <div data-testid="marketplace-view" className="screen-anim">
      <div style={{ ...muted, fontSize: 13, marginBottom: 16 }}>
        Hire a ready-made agent. It’s added to your library as a draft playbook you can review,
        tune, and activate in Studio — nothing runs until you activate it.
      </div>
      <div style={{ display: "grid", gap: 14, gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))" }}>
        {templates.map((t) => (
          <div key={t.template_id} data-testid="marketplace-card" style={{ ...card, display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ fontWeight: 720, fontSize: 15.5 }}>{titleFor(t.template_id)}</div>
            <div style={{ ...muted, fontSize: 13, flex: 1 }}>{t.summary}</div>
            {hired[t.template_id] ? (
              <div data-testid="marketplace-hired" style={{ fontSize: 13, color: "var(--ink-2, #5d564d)" }}>
                Added as a draft.{" "}
                {onOpenStudio && (
                  <button
                    style={{ background: "none", border: "none", padding: 0, color: "var(--clay, #b5613f)", cursor: "pointer", fontWeight: 640 }}
                    onClick={onOpenStudio}
                  >
                    Open in Studio →
                  </button>
                )}
              </div>
            ) : (
              <button
                data-testid={`marketplace-hire-${t.template_id}`}
                style={{ ...primaryBtn, alignSelf: "flex-start", opacity: hiring[t.template_id] ? 0.7 : 1 }}
                disabled={hiring[t.template_id]}
                onClick={() => void hire(t.template_id)}
              >
                {hiring[t.template_id] ? "Adding…" : "Add to my agents"}
              </button>
            )}
            {hireError[t.template_id] && (
              <span style={{ color: "var(--rose, #b4413b)", fontSize: 12.5 }}>{hireError[t.template_id]}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export default MarketplaceView;
