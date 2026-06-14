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
//   * A 404 means the live API image predates this route (web can deploy ahead of
//     the API): a calm "rolling out" state with a refresh affordance — not an error wall.
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
const ghostBtn: React.CSSProperties = {
  padding: "8px 16px",
  borderRadius: 10,
  border: "1px solid var(--line, #e3ddd3)",
  background: "transparent",
  color: "var(--ink, #2a2622)",
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
  const [rollout, setRollout] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [templates, setTemplates] = useState<StudioTemplateSummary[]>([]);

  // Per-card hire state.
  const [hiring, setHiring] = useState<Record<string, boolean>>({});
  const [hired, setHired] = useState<Record<string, boolean>>({});
  const [hireError, setHireError] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setRollout(false);
    setUnavailable(false);
    try {
      setTemplates(await api.getStudioTemplates());
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        // The live API image predates /studio/templates (the web can deploy ahead
        // of the API): a calm rollout note, not an error wall.
        setRollout(true);
      } else if (e instanceof ApiError && e.status === 503) {
        setUnavailable(true);
      } else {
        setError(friendlyErrorMessage(e));
      }
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
  if (rollout) {
    return (
      <div data-testid="marketplace-rollout" style={{ ...card, color: "var(--ink, #2a2622)", fontSize: 13.5 }}>
        <div style={{ fontWeight: 700, marginBottom: 4 }}>Marketplace API is rolling out</div>
        <p style={{ ...muted, lineHeight: 1.5 }}>
          Your deployment doesn&rsquo;t serve the marketplace endpoint yet &mdash; refresh after
          the next API deploy. Nothing is wrong with your workspace.
        </p>
        <button data-testid="marketplace-rollout-refresh" onClick={() => void load()} style={{ ...ghostBtn, marginTop: 10 }}>
          Refresh
        </button>
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
      <div data-testid="marketplace-error" style={{ ...card, borderColor: "var(--rose, #b4413b)" }}>
        <div style={{ fontWeight: 700, marginBottom: 4 }}>Something needs another try</div>
        <p style={{ ...muted, lineHeight: 1.5 }}>{error}</p>
        <button data-testid="marketplace-retry" style={primaryBtn} onClick={() => void load()}>Try again</button>
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
      {/* Intro banner — a deliberate section header instead of an orphaned line of
          gray text under the topbar title. Mirrors Switchboard's banner styling so
          the top of the page reads as intentional. */}
      <div
        data-testid="marketplace-intro"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 14,
          padding: "15px 18px",
          borderRadius: 14,
          border: "1px solid var(--accent-soft, #e9e3d7)",
          background: "linear-gradient(120deg, var(--accent-softer, #f6f3ec), var(--surface, #fff))",
          marginBottom: 20,
        }}
      >
        <div
          aria-hidden
          style={{
            width: 38,
            height: 38,
            borderRadius: 11,
            background: "var(--surface, #fff)",
            border: "1px solid var(--line, #e3ddd3)",
            display: "grid",
            placeItems: "center",
            flexShrink: 0,
            fontSize: 19,
          }}
        >
          🛒
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 720, fontSize: 14.5, color: "var(--ink, #2a2622)" }}>
            Hire a ready-made agent
          </div>
          <div style={{ ...muted, fontSize: 12.5, lineHeight: 1.5, marginTop: 2 }}>
            Each one lands in your library as a <b style={{ color: "var(--ink-2, #5d564d)" }}>draft</b> playbook
            you can review, tune, and activate in Studio. Nothing runs until you activate it.
          </div>
        </div>
        <span
          style={{
            fontSize: 12,
            fontWeight: 650,
            color: "var(--ink-3, #8a8278)",
            background: "var(--surface, #fff)",
            border: "1px solid var(--line, #e3ddd3)",
            borderRadius: 999,
            padding: "4px 11px",
            whiteSpace: "nowrap",
            flexShrink: 0,
          }}
        >
          {templates.length} available
        </span>
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
