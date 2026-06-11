// A calm, helpful empty state for a main app surface that has no data yet.
//
// Reuses the same inline-CSS-var conventions the API-wired surfaces use
// (var(--ink), var(--accent), card/ghost button shapes), so it drops into any
// surface (Contacts, Pipeline, Reports, Chat) without depending on a stylesheet
// being loaded. A short explainer + an optional primary CTA (e.g. "Load sample
// data" / "Import your contacts" / "Ask Balto") instead of a blank panel.
//
// Accessibility: the panel is a labelled region; the primary CTA is a real
// <button> (keyboard-operable, focus-visible) with an aria-busy state while its
// async action runs.

import React from "react";

const { useState } = React;

export interface EmptyStateProps {
  /** Short, bold headline ("No contacts yet"). */
  title: string;
  /** One calm sentence of explainer copy. */
  body: string;
  /** Optional primary CTA label ("Load sample data"). Omit for a CTA-less panel. */
  ctaLabel?: string;
  /** The primary CTA action. May be async; the button shows a busy state while it runs. */
  onCta?: () => void | Promise<void>;
  /** Optional secondary CTA (a plain text-button, e.g. "Ask Balto"). */
  secondaryLabel?: string;
  onSecondary?: () => void;
  /** Stable test hook (e.g. "contacts-empty-onboarding"). */
  testid?: string;
}

const card: React.CSSProperties = {
  background: "var(--surface, #fff)",
  border: "1px solid var(--line, #e3ddd3)",
  borderRadius: 14,
  padding: "30px 26px",
  textAlign: "center",
  maxWidth: 460,
  margin: "8px auto",
};

const primaryBtn: React.CSSProperties = {
  appearance: "none",
  border: "1px solid transparent",
  borderRadius: 10,
  padding: "10px 18px",
  fontSize: 13.5,
  fontWeight: 700,
  fontFamily: "inherit",
  cursor: "pointer",
  background: "var(--accent, #b4593b)",
  color: "var(--accent-ink-on, #fff)",
};

const ghostBtn: React.CSSProperties = {
  appearance: "none",
  border: "none",
  background: "none",
  color: "var(--accent-ink, #9a4a30)",
  fontSize: 13,
  fontWeight: 650,
  fontFamily: "inherit",
  cursor: "pointer",
  padding: "8px 6px",
  textDecoration: "underline",
};

export function EmptyState({
  title,
  body,
  ctaLabel,
  onCta,
  secondaryLabel,
  onSecondary,
  testid,
}: EmptyStateProps) {
  const [busy, setBusy] = useState(false);

  const runCta = async () => {
    if (busy || !onCta) return;
    setBusy(true);
    try {
      await onCta();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-testid={testid} role="region" aria-label={title} style={card}>
      <h2
        style={{
          fontSize: 17,
          fontWeight: 760,
          letterSpacing: "-.01em",
          margin: 0,
          color: "var(--ink, #2a2622)",
        }}
      >
        {title}
      </h2>
      <p
        style={{
          fontSize: 13.5,
          lineHeight: 1.55,
          color: "var(--ink-3, #8a8278)",
          margin: "8px auto 0",
          maxWidth: 380,
        }}
      >
        {body}
      </p>
      {(ctaLabel || secondaryLabel) && (
        <div
          style={{
            marginTop: 18,
            display: "flex",
            gap: 6,
            justifyContent: "center",
            alignItems: "center",
            flexWrap: "wrap",
          }}
        >
          {ctaLabel && onCta && (
            <button
              type="button"
              data-testid={testid ? `${testid}-cta` : undefined}
              onClick={() => void runCta()}
              disabled={busy}
              aria-busy={busy}
              style={{ ...primaryBtn, opacity: busy ? 0.7 : 1, cursor: busy ? "default" : "pointer" }}
            >
              {busy ? "Loading…" : ctaLabel}
            </button>
          )}
          {secondaryLabel && onSecondary && (
            <button
              type="button"
              data-testid={testid ? `${testid}-secondary` : undefined}
              onClick={onSecondary}
              style={ghostBtn}
            >
              {secondaryLabel}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default EmptyState;
