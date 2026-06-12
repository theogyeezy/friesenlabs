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
import {
  ApiClient,
  ApiError,
  buildViewDataLoader,
  defaultClient,
  friendlyErrorMessage,
  type ChatResponse,
  type Citation,
} from "./client";
import { SpecRenderer, type LoadData } from "../dashboard/SpecRenderer";
import { Spinner } from "./Spinner";
import { Analytics, defaultAnalytics } from "../analytics/posthog";

const { useState, useCallback, useEffect } = React;

// Data loaders for the view overlay — identical policy to DashboardView. Mock
// builds lazily load the offline fixture behind the BUILD-TIME gate (the demo
// chunk never ships in production). Real builds resolve an ALREADY-SAVED view's
// data via POST /views/{id}/data (the `exists` path); an unsaved Balto draft has
// no view id yet, so it honestly renders "No data yet" until the user saves it
// (canned numbers on a real tenant are a lie). The empty loader is the fallback.
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
  /** The saved view's id when it already exists (exists path) — lets the overlay
   * resolve real rows via POST /views/{id}/data. null for an unsaved draft. */
  viewId: string | null;
}

interface Message {
  who: "me" | "agent";
  text: string;
  citations?: Citation[];
  /** True for friendly error copy rendered as an agent bubble. */
  error?: boolean;
  /** Balto result: the view this message opens. */
  view?: ViewAttachment;
  /** Grounding observability: non-grounded statuses render an honest note under the answer. */
  grounding?: string | null;
  /** Interim narration of a still-settling turn (async contract) — replaced when it settles. */
  working?: boolean;
}

/** Honest copy for each non-grounded retrieval outcome — "grounded" renders citations instead. */
const GROUNDING_NOTES: Record<string, string> = {
  no_sources_found:
    "No matching documents in your knowledge base — this answer isn't grounded in your docs. " +
    "Add documents under Knowledge to ground answers.",
  ungrounded:
    "Your documents couldn't verify this — unverifiable claims were filtered out of the answer.",
  unavailable:
    "Knowledge grounding isn't connected yet, so this answer isn't grounded in your documents.",
};

interface OverlayState extends ViewAttachment {
  saving: boolean;
  savedNote: string | null;
  saveError: string | null;
  /** The resolved data loader for this overlay's view (mock fixture, real data,
   * or the empty fallback for an unsaved draft / data-plane loss). */
  loadData: LoadData;
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
              view: { spec: res.spec, draftId: res.draft_id ?? null, saveable: true, viewId: null },
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
                viewId: res.view.view_id,
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
      // ASYNC TURN CONTRACT: settled === false means the delegation/tool round-trips are
      // still in flight server-side — continue the SAME turn (no new message, no human
      // nudge) until it settles. Each continue is a short request under the edge's 60s
      // ceiling; narration accumulates progressively so the customer watches it work.
      const settleTurn = async (first: ChatResponse) => {
        let res = first;
        let narration = res.answer ?? "";
        let continues = 0;
        const MAX_CONTINUES = 10;
        while (res.settled === false && continues < MAX_CONTINUES) {
          continues += 1;
          if (narration) {
            // Show the interim narration while the agents keep working.
            setMsgs((m) => {
              const last = m[m.length - 1];
              const note = { who: "agent" as const, text: narration, working: true };
              return last?.working ? [...m.slice(0, -1), note] : [...m, note];
            });
          }
          res = await api.continueChat();
          if (res.answer) {
            narration = narration ? `${narration}\n\n${res.answer}` : res.answer;
          }
        }
        const finalText =
          narration ||
          res.answer ||
          "The agents are still working on this — check back in a moment.";
        setMsgs((m) => {
          const kept = m[m.length - 1]?.working ? m.slice(0, -1) : m;
          return [
            ...kept,
            { who: "agent", text: finalText, citations: res.citations,
              grounding: res.grounding_status ?? null },
          ];
        });
        return res;
      };
      try {
        let res: ChatResponse;
        try {
          res = await api.chat(body);
        } catch (e) {
          // The edge gave up on the request (its ~60s ceiling), but the turn keeps settling
          // SERVER-SIDE — recover it through the continue leg instead of erroring out.
          if (!(e instanceof ApiError && (e.status === 504 || e.status === 502))) throw e;
          res = { answer: "", citations: [], settled: false };
        }
        res = await settleTurn(res);
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
        setMsgs((m) => {
          const kept = m[m.length - 1]?.working ? m.slice(0, -1) : m;
          return [...kept, { who: "agent", text, error: true }];
        });
      } finally {
        setSending(false);
      }
    },
    [api, ph, draft, sending, embedded, runBalto],
  );

  // Open a Balto view in the overlay, resolving its data. A view that already
  // exists (the `exists` path, carrying a saved viewId) loads real rows via
  // POST /views/{id}/data; an unsaved draft (viewId null) and mock builds use
  // the fixture/empty fallback. A data-plane failure degrades to the empty
  // loader — the overlay still opens, panels just say "No data yet".
  const openViewOverlay = useCallback(
    async (attachment: ViewAttachment) => {
      const fallback: LoadData = api.isMock() ? mockLoadData : noLiveData;
      setOverlay({
        ...attachment,
        saving: false,
        savedNote: null,
        saveError: null,
        loadData: fallback,
      });
      if (api.isMock() || !attachment.viewId) return;
      const viewId = attachment.viewId;
      try {
        const data = await api.loadViewData(viewId);
        const loader = buildViewDataLoader(attachment.spec, data);
        // Only apply if the overlay still shows THIS view (the user may have
        // closed it or opened another while the request was in flight).
        setOverlay((o) => (o && o.viewId === viewId ? { ...o, loadData: loader } : o));
      } catch {
        // Keep the empty fallback already in place — honest "No data yet".
      }
    },
    [api],
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
                onClick={() => void openViewOverlay(m.view!)}
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

            {m.who === "agent" && !m.error && m.grounding && GROUNDING_NOTES[m.grounding] && (
              <div
                data-testid="grounding-note"
                style={{
                  marginTop: 8,
                  fontSize: 12,
                  color: "var(--ink-3, #8a8278)",
                  borderLeft: "2px solid var(--line, #e3ddd3)",
                  paddingLeft: 10,
                }}
              >
                {GROUNDING_NOTES[m.grounding]}
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
            <SpecRenderer spec={overlay.spec} loadData={overlay.loadData} />

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
