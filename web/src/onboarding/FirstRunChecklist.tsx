// A lightweight, dismissible first-run checklist for a brand-new tenant.
//
// Shown ONLY while the tenant's onboarding_state is incomplete (not dismissed,
// and at least one step still open). NEVER blocks the app: it is a calm card at
// the top of the workspace, always skippable ("Skip for now" dismisses it, and
// it auto-hides once every step is done). Completion + dismissal persist per
// tenant via PUT /onboarding (claims-bound, RLS-scoped on the server).
//
// Three steps, each a real action that moves the tenant forward:
//   1. load_data    — one-click "Load sample data" (POST /onboarding/load-sample,
//                     idempotent). On success the populated views surface.
//   2. try_chat     — jump to the chat / Balto view (the agent front door).
//   3. invite_team  — open settings to invite teammates.
//
// Accessibility: a labelled region; the dismiss control is a real <button> with
// an aria-label; each step's action is keyboard-operable; the load-sample button
// reports aria-busy while the (idempotent) load runs.

import React from "react";
import type { OnboardingStepId } from "../api/client";
import { friendlyErrorMessage } from "../api/client";
import { useOnboarding } from "./useOnboarding";

const { useState } = React;

export interface FirstRunChecklistProps {
  /** Navigate the shell to a route id (e.g. "crm", "settings"); the "Try chat"
   * step opens the chat dock via onOpenChat instead. */
  onNavigate?: (route: string) => void;
  onOpenChat?: () => void;
}

interface StepDef {
  id: OnboardingStepId;
  label: string;
  hint: string;
  cta: string;
}

const STEPS: StepDef[] = [
  {
    id: "load_data",
    label: "Add your data",
    hint: "Load a realistic sample CRM so every view has something to show — or import your own later.",
    cta: "Load sample data",
  },
  {
    id: "try_chat",
    label: "Ask Balto",
    hint: "Ask a question in plain language. Balto builds a view or answers from your data.",
    cta: "Open chat",
  },
  {
    id: "invite_team",
    label: "Invite your team",
    hint: "Bring teammates into the workspace from settings.",
    cta: "Open settings",
  },
];

const wrap: React.CSSProperties = {
  background: "var(--surface, #fff)",
  border: "1px solid var(--line, #e3ddd3)",
  borderRadius: 14,
  padding: "18px 20px",
  margin: "0 0 18px",
  maxWidth: 760,
};

const stepRow: React.CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: 12,
  padding: "12px 0",
  borderTop: "1px solid var(--line-2, #efe9df)",
};

const primaryBtn: React.CSSProperties = {
  appearance: "none",
  border: "1px solid var(--line, #e3ddd3)",
  borderRadius: 9,
  padding: "7px 14px",
  fontSize: 12.5,
  fontWeight: 700,
  fontFamily: "inherit",
  cursor: "pointer",
  background: "var(--accent, #b4593b)",
  color: "var(--accent-ink-on, #fff)",
  whiteSpace: "nowrap",
};

const ghostBtn: React.CSSProperties = {
  appearance: "none",
  border: "1px solid var(--line, #e3ddd3)",
  borderRadius: 9,
  padding: "7px 14px",
  fontSize: 12.5,
  fontWeight: 650,
  fontFamily: "inherit",
  cursor: "pointer",
  background: "var(--surface, #fff)",
  color: "var(--ink, #2a2622)",
  whiteSpace: "nowrap",
};

export function FirstRunChecklist({ onNavigate, onOpenChat }: FirstRunChecklistProps) {
  const { state, loading, rollout, update, loadSample } = useOnboarding();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  // Never render while the first GET is in flight (no flash), when the API
  // doesn't serve the routes yet, or once dismissed / fully complete.
  if (loading || rollout || state === null) return null;
  const allDone = STEPS.every((s) => state.steps[s.id]);
  if (state.dismissed || allDone) return null;

  const doneCount = STEPS.filter((s) => state.steps[s.id]).length;

  const onLoadSample = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await loadSample();
      const n = (res.counts.contacts ?? 0) + (res.counts.companies ?? 0) + (res.counts.deals ?? 0);
      setToast(`Sample data loaded (${n} records). Your views are populated.`);
    } catch (e) {
      setError(friendlyErrorMessage(e, "Couldn't load sample data. Please try again."));
    } finally {
      setBusy(false);
    }
  };

  const markAndGo = async (id: OnboardingStepId, go: () => void) => {
    go();
    // Best-effort persistence — a failed PUT must never block the navigation.
    try {
      await update({ steps: { [id]: true } });
    } catch {
      /* keep the UI moving; the next refresh reconciles */
    }
  };

  const onStepCta = (s: StepDef) => {
    if (s.id === "load_data") return void onLoadSample();
    if (s.id === "try_chat") {
      return void markAndGo("try_chat", () => (onOpenChat ? onOpenChat() : onNavigate?.("chat")));
    }
    return void markAndGo("invite_team", () => onNavigate?.("settings"));
  };

  const dismiss = async () => {
    try {
      await update({ dismissed: true });
    } catch {
      /* the optimistic hide already happened via state; ignore */
    }
  };

  return (
    <section data-testid="first-run-checklist" role="region" aria-label="Get started" style={wrap}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
        <div>
          <h2 style={{ fontSize: 16, fontWeight: 760, letterSpacing: "-.01em", margin: 0, color: "var(--ink, #2a2622)" }}>
            Get started with Friesen Labs
          </h2>
          <p style={{ fontSize: 12.5, color: "var(--ink-3, #8a8278)", margin: "4px 0 0" }}>
            <span data-testid="first-run-progress">{doneCount} of {STEPS.length} done</span> — a couple of quick steps.
          </p>
        </div>
        <button
          type="button"
          data-testid="first-run-dismiss"
          onClick={() => void dismiss()}
          aria-label="Skip getting started for now"
          style={{ ...ghostBtn, border: "none", color: "var(--ink-3, #8a8278)", padding: "4px 8px" }}
        >
          Skip for now
        </button>
      </div>

      {toast && (
        <div data-testid="first-run-toast" role="status" style={{ marginTop: 12, fontSize: 12.5, color: "var(--ink, #2a2622)", background: "var(--accent-soft, #f6ece6)", borderRadius: 9, padding: "8px 12px" }}>
          {toast}
        </div>
      )}
      {error && (
        <div data-testid="first-run-error" role="alert" style={{ marginTop: 12, fontSize: 12.5, color: "var(--ink, #2a2622)", border: "1px solid var(--rose, #b4413b)", borderRadius: 9, padding: "8px 12px" }}>
          {error}
        </div>
      )}

      <div style={{ marginTop: 8 }}>
        {STEPS.map((s) => {
          const done = state.steps[s.id];
          return (
            <div key={s.id} data-testid={`first-run-step-${s.id}`} data-done={done ? "true" : "false"} style={stepRow}>
              <span
                aria-hidden="true"
                style={{
                  width: 20,
                  height: 20,
                  borderRadius: 99,
                  flexShrink: 0,
                  marginTop: 1,
                  display: "grid",
                  placeItems: "center",
                  fontSize: 12,
                  fontWeight: 800,
                  background: done ? "var(--accent, #b4593b)" : "var(--line-2, #efe9df)",
                  color: done ? "var(--accent-ink-on, #fff)" : "var(--ink-3, #8a8278)",
                }}
              >
                {done ? "✓" : ""}
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13.5, fontWeight: 700, color: "var(--ink, #2a2622)" }}>
                  {s.label}
                </div>
                <div style={{ fontSize: 12.5, color: "var(--ink-3, #8a8278)", marginTop: 2, lineHeight: 1.5 }}>
                  {s.hint}
                </div>
              </div>
              <button
                type="button"
                data-testid={`first-run-cta-${s.id}`}
                onClick={() => onStepCta(s)}
                disabled={s.id === "load_data" && busy}
                aria-busy={s.id === "load_data" && busy}
                style={done ? ghostBtn : s.id === "load_data" ? primaryBtn : ghostBtn}
              >
                {done ? "Done" : s.id === "load_data" && busy ? "Loading…" : s.cta}
              </button>
            </div>
          );
        })}
      </div>
    </section>
  );
}

export default FirstRunChecklist;
