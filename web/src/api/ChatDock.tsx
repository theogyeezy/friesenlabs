// Chat dock wired to POST /chat via ApiClient. Renders the answer plus inline
// citations (claim + source ref + snippet). Mock mode returns a canned, grounded
// answer so Playwright runs offline. No token or payload is ever rendered.
//
// Balto (NL view creation): a view-shaped ask comes back from /chat flagged
// `view_intent` with the EXACT Balto status line as the answer — it renders as
// a normal agent message while the client drives POST /views/synthesize. The
// result lands back in the thread:
//   - ok            -> a button that opens the new visualization in an overlay
//                      (rendered by the existing trusted SpecRenderer; spec, not
//                      code), with an X to back out and Save / Discard. Save
//                      persists through the existing saved-view store; discard
//                      is simply never saving (the draft stays ephemeral).
//   - exists        -> the saved view that already covers the ask, same button.
//   - data_not_found-> the honest "data does not exist on the platform" copy.

import React from "react";
import { ApiClient, ApiError, defaultClient, friendlyErrorMessage, type Citation } from "./client";
import { SpecRenderer, type LoadData } from "../dashboard/SpecRenderer";
import { Spinner } from "./Spinner";
import { Analytics, defaultAnalytics } from "../analytics/posthog";

const { useState, useCallback, useEffect } = React;

// Data loaders for the view overlay — identical policy to DashboardView: real
// builds resolve every query to zero rows (honest "No data yet"; canned numbers
// on a real tenant are a lie), mock builds lazily load the offline fixture
// behind the BUILD-TIME gate so the demo chunk never ships in production.
const noLiveData: LoadData = async () => [];
let mockLoadData: LoadData = noLiveData;
if (import.meta.env.VITE_API_MOCK !== "0" && import.meta.env.VITE_API_MOCK !== "false") {
  mockLoadData = async (query) => (await import("../dashboard/sample")).sampleLoadData(query);
}

/** A synthesized (or already-saved) view attached to an agent message. */
interface ViewAttachment {
  spec: Record<string, unknown>;
  /** The ephemeral draft handle; null when the view is already saved (exists path). */
  draftId: string | null;
  /** False on the exists path — there is nothing new to save. */
  saveable: boolean;
}

interface Message {
  who: "me" | "agent";
  text: string;
  citations?: Citation[];
  /** True for friendly error copy rendered as an agent bubble. */
  error?: boolean;
  /** Balto result: the view this message opens. */
  view?: ViewAttachment;
}

interface OverlayState extends ViewAttachment {
  saving: boolean;
  savedNote: string | null;
  saveError: string | null;
}

export interface ChatDockProps {
  client?: ApiClient;
  analytics?: Analytics;
  /**
   * When mounted inside the shell's slide-over panel (which carries its own
   * "Ask your agents" header), suppress the standalone heading.
   */
  embedded?: boolean;
}

export function ChatDock({ client, analytics, embedded = false }: ChatDockProps) {
  const api = client ?? defaultClient();
  const ph = analytics ?? defaultAnalytics();
  const [msgs, setMsgs] = useState<Message[]>([
    {
      who: "agent",
      text: "Ask anything about your pipeline, your agents, or what happened this week.",
    },
  ]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [overlay, setOverlay] = useState<OverlayState | null>(null);

  // The overlay can back out via Escape too (same affordance as the X button).
  useEffect(() => {
    if (!overlay) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOverlay(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [overlay]);

  const runBalto = useCallback(
    async (request: string) => {
      // The Balto status line is already in the thread (the turn's answer). Now do the
      // actual synthesis; only a coarse outcome signal is captured, never the ask text.
      try {
        const res = await api.synthesizeView({ request });
        ph.capture("view_synthesis_finished", { status: res.status });
        if (res.status === "ok" && res.spec) {
          setMsgs((m) => [
            ...m,
            {
              who: "agent",
              text: "Balto is back with your view. Open it below — you can save it or let it go.",
              view: { spec: res.spec, draftId: res.draft_id ?? null, saveable: true },
            },
          ]);
        } else if (res.status === "exists" && res.view) {
          setMsgs((m) => [
            ...m,
            {
              who: "agent",
              text: "You already have a saved view that covers this — here it is.",
              view: {
                spec: res.view.spec_json as Record<string, unknown>,
                draftId: null,
                saveable: false,
              },
            },
          ]);
        } else if (res.status === "data_not_found") {
          setMsgs((m) => [
            ...m,
            {
              who: "agent",
              text:
                res.message ??
                "Your request cannot be fulfilled because the data does not exist on the platform.",
              error: true,
            },
          ]);
        } else {
          setMsgs((m) => [
            ...m,
            {
              who: "agent",
              text: "Balto couldn't put a valid view together for that. Try rephrasing what you want to see.",
              error: true,
            },
          ]);
        }
      } catch (e) {
        const text =
          e instanceof ApiError && e.status === 503
            ? "View synthesis isn't available on this deployment yet. Everything else keeps working."
            : friendlyErrorMessage(e, "Balto couldn't fetch that view. Please try again.");
        setMsgs((m) => [...m, { who: "agent", text, error: true }]);
      }
    },
    [api, ph],
  );

  const send = useCallback(
    async (text?: string) => {
      const body = (text ?? draft).trim();
      if (!body || sending) return;
      setDraft("");
      setSending(true);
      setMsgs((m) => [...m, { who: "me", text: body }]);
      // Coarse usage signal only — the message TEXT is never captured (it can
      // carry tenant data); we mark that a chat happened + its length bucket.
      ph.capture("chat_message_sent", { embedded, length: body.length });
      try {
        const res = await api.chat(body);
        setMsgs((m) => [...m, { who: "agent", text: res.answer, citations: res.citations }]);
        if (res.view_intent && res.view_request) {
          // The Balto status message is on screen while this runs (still `sending`).
          await runBalto(res.view_request);
        }
      } catch (e) {
        // The agent plane is parked until the Managed runtime is connected;
        // /chat returns 503 in that state (api/app.py). Every error renders
        // friendly copy — the raw "API <code>: <detail>" string never does.
        const text =
          e instanceof ApiError && e.status === 503
            ? "Agents unavailable — the agent runtime isn't connected yet, so chat " +
              "is offline for now. Everything else keeps working; check back soon."
            : friendlyErrorMessage(e, "The agents couldn't answer that one. Please try again.");
        setMsgs((m) => [...m, { who: "agent", text, error: true }]);
      } finally {
        setSending(false);
      }
    },
    [api, ph, draft, sending, embedded, runBalto],
  );

  const saveOverlayView = useCallback(async () => {
    if (!overlay || overlay.saving || overlay.savedNote) return;
    setOverlay((o) => (o ? { ...o, saving: true, saveError: null } : o));
    try {
      // Drafts save through the dedicated route (the server re-validates and persists via
      // the existing saved-view store); an already-materialized spec falls back to POST /views.
      const row = overlay.draftId
        ? await api.saveViewDraft(overlay.draftId)
        : await api.saveView({
            spec: overlay.spec,
            source_prompt: String((overlay.spec as Record<string, unknown>).source_prompt ?? ""),
          });
      ph.capture("view_synthesis_saved", {});
      setOverlay((o) => (o ? { ...o, saving: false, savedNote: `Saved as version ${row.version}` } : o));
    } catch (e) {
      setOverlay((o) =>
        o
          ? {
              ...o,
              saving: false,
              saveError: friendlyErrorMessage(e, "Couldn't save this view. Please try again."),
            }
          : o,
      );
    }
  }, [api, ph, overlay]);

  return (
    <div
      data-testid="chat-dock"
      style={{
        maxWidth: 560,
        margin: "0 auto",
        padding: "24px",
        fontFamily: "system-ui, sans-serif",
        display: "flex",
        flexDirection: "column",
        gap: 14,
      }}
    >
      {!embedded && (
        <h1 style={{ fontSize: 20, fontWeight: 740, letterSpacing: "-.02em" }}>Ask your agents</h1>
      )}

      <div data-testid="chat-body" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {msgs.map((m, i) => (
          <div
            key={i}
            data-testid={m.who === "me" ? "chat-msg-me" : "chat-msg-agent"}
            data-error={m.error ? "1" : undefined}
            style={{ alignSelf: m.who === "me" ? "flex-end" : "flex-start", maxWidth: "92%" }}
          >
            <div
              style={{
                background: m.who === "me" ? "var(--accent, #2a2622)" : "var(--surface, #fff)",
                color: m.who === "me" ? "#fff" : "var(--ink, #2a2622)",
                border: m.who === "me" ? "none" : "1px solid var(--line, #e3ddd3)",
                borderRadius: 14,
                padding: "10px 14px",
                fontSize: 14,
                lineHeight: 1.5,
              }}
            >
              {m.text}
            </div>

            {m.view && (
              <button
                data-testid="balto-open-view"
                onClick={() =>
                  setOverlay({ ...m.view!, saving: false, savedNote: null, saveError: null })
                }
                style={{
                  marginTop: 8,
                  padding: "8px 14px",
                  borderRadius: 10,
                  border: "1px solid var(--line, #e3ddd3)",
                  background: "var(--surface, #fff)",
                  color: "var(--ink, #2a2622)",
                  fontSize: 13,
                  fontWeight: 650,
                  cursor: "pointer",
                }}
              >
                Open view: {String((m.view.spec as Record<string, unknown>).title ?? "Untitled")}
              </button>
            )}

            {m.citations && m.citations.length > 0 && (
              <div data-testid="citations" style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
                {m.citations.map((c, ci) => (
                  <div
                    key={ci}
                    data-testid="citation"
                    style={{
                      fontSize: 12,
                      color: "var(--ink-3, #8a8278)",
                      borderLeft: "2px solid var(--line, #e3ddd3)",
                      paddingLeft: 10,
                    }}
                  >
                    <div data-testid="citation-claim" style={{ fontWeight: 600, color: "var(--ink, #2a2622)" }}>
                      {c.claim}
                    </div>
                    <div data-testid="citation-source">{c.source_ref || "ungrounded"}</div>
                    <div style={{ fontStyle: "italic" }}>{c.snippet}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}

        {sending && (
          <div style={{ alignSelf: "flex-start" }}>
            <Spinner testid="chat-busy" size={16} label="Asking your agents..." />
          </div>
        )}
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <textarea
          data-testid="chat-input"
          rows={1}
          value={draft}
          placeholder="Tell your agents what to do..."
          disabled={sending}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          style={{
            flex: 1,
            resize: "none",
            borderRadius: 10,
            border: "1px solid var(--line, #e3ddd3)",
            padding: "10px 12px",
            fontSize: 14,
            fontFamily: "inherit",
          }}
        />
        <button
          data-testid="chat-send"
          disabled={!draft.trim() || sending}
          onClick={() => void send()}
          style={{
            padding: "8px 16px",
            borderRadius: 10,
            border: "none",
            background: "var(--accent, #2a2622)",
            color: "#fff",
            fontSize: 13.5,
            fontWeight: 650,
            cursor: "pointer",
          }}
        >
          Send
        </button>
      </div>

      {overlay && (
        <div
          data-testid="view-overlay"
          role="dialog"
          aria-modal="true"
          aria-label={String((overlay.spec as Record<string, unknown>).title ?? "View")}
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 1000,
            background: "rgba(42, 38, 34, 0.45)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 24,
          }}
        >
          <div
            style={{
              background: "var(--bg, #faf7f2)",
              borderRadius: 16,
              border: "1px solid var(--line, #e3ddd3)",
              maxWidth: 760,
              width: "100%",
              maxHeight: "88vh",
              overflow: "auto",
              padding: "18px 20px",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
              <strong style={{ fontSize: 15 }}>
                {String((overlay.spec as Record<string, unknown>).title ?? "Your view")}
              </strong>
              <button
                data-testid="view-overlay-close"
                aria-label="Close view"
                onClick={() => setOverlay(null)}
                style={{
                  marginLeft: "auto",
                  width: 32,
                  height: 32,
                  borderRadius: 8,
                  border: "1px solid var(--line, #e3ddd3)",
                  background: "var(--surface, #fff)",
                  fontSize: 16,
                  lineHeight: 1,
                  cursor: "pointer",
                }}
              >
                ×
              </button>
            </div>

            {/* The existing trusted renderer: re-validates the spec, draws ONLY catalog
                components — spec, not code. */}
            <SpecRenderer spec={overlay.spec} loadData={api.isMock() ? mockLoadData : noLiveData} />

            <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 16 }}>
              {overlay.saveable && (
                <>
                  <button
                    data-testid="view-overlay-save"
                    disabled={overlay.saving || !!overlay.savedNote}
                    onClick={() => void saveOverlayView()}
                    style={{
                      padding: "8px 16px",
                      borderRadius: 10,
                      border: "none",
                      background: "var(--accent, #2a2622)",
                      color: "#fff",
                      fontSize: 13.5,
                      fontWeight: 650,
                      cursor: "pointer",
                    }}
                  >
                    {overlay.saving ? "Saving..." : overlay.savedNote ? "Saved" : "Save view"}
                  </button>
                  <button
                    data-testid="view-overlay-discard"
                    onClick={() => setOverlay(null)}
                    style={{
                      padding: "8px 16px",
                      borderRadius: 10,
                      border: "1px solid var(--line, #e3ddd3)",
                      background: "var(--surface, #fff)",
                      color: "var(--ink, #2a2622)",
                      fontSize: 13.5,
                      cursor: "pointer",
                    }}
                  >
                    Discard
                  </button>
                </>
              )}
              {overlay.savedNote && (
                <span
                  data-testid="view-overlay-saved"
                  style={{ fontSize: 12.5, color: "var(--green, #2f8a4f)", fontWeight: 600 }}
                >
                  {overlay.savedNote}
                </span>
              )}
              {overlay.saveError && (
                <span
                  data-testid="view-overlay-save-error"
                  style={{ fontSize: 12.5, color: "var(--rose, #b4413b)" }}
                >
                  {overlay.saveError}
                </span>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default ChatDock;
