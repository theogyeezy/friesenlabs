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
  type ConversationRow,
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
  /** Count of actions this turn STAGED into the Greenlight approval queue (a drafted email, a
   * proposed CRM write). > 0 renders a "Review in Greenlight" affordance so the user can act on
   * it without hunting for the queue. */
  approvals?: number;
}

/** A turn's `pending_approvals` carries TWO kinds of entry: routed items the worker actually staged
 * (they carry a `tool_name`) and async-settle markers (`{reason: ...}`, no tool_name). Count only
 * the real queued items — the markers are plumbing, never user-facing. */
function routedApprovalCount(pending: unknown[] | undefined, seenIds: Set<string>): number {
  if (!Array.isArray(pending)) return 0;
  let n = 0;
  for (const raw of pending) {
    if (!raw || typeof raw !== "object") continue;
    const p = raw as Record<string, unknown>;
    if (typeof p.tool_name !== "string") continue;
    // Dedupe across continue legs by the tool-call id (a staged call surfaces in exactly one
    // leg's digest, but guard anyway so a replay never double-counts).
    const id = typeof p.custom_tool_use_id === "string" ? p.custom_tool_use_id : null;
    if (id) {
      if (seenIds.has(id)) continue;
      seenIds.add(id);
    }
    n += 1;
  }
  return n;
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
  /** In-shell citation → knowledge-page navigation (the shell switches route without a
   * reload). Absent (standalone /?view=chat mount): the link falls back to the
   * /?view=knowledge&doc=<ref> deep link. */
  onOpenKnowledgePage?: (refPrefix: string) => void;
  /** In-shell navigation to the Greenlight approval queue (the shell passes navTo("approvals")).
   * Absent (standalone /?view=chat mount): falls back to the /?view=greenlight deep link. */
  onOpenGreenlight?: () => void;
}

/** A citation source_ref that IS a knowledge-page chunk ('upload:pricing-policy-ab12#1',
 * 'demo:kb:discounts#0') -> its page ref. CRM refs ('deal-42') and single-row corpus
 * shadows ('demo:doc:act:1' — no chunk suffix) get no link: they aren't pages. */
export function citationPageRef(sourceRef: string | null | undefined): string | null {
  if (!sourceRef || !/^[a-z0-9][a-z0-9:.-]*#\d+$/.test(sourceRef)) return null;
  const prefix = sourceRef.split("#")[0];
  return prefix.includes(":") ? prefix : null;
}

const iconBtn: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  border: "1px solid var(--line, #e3ddd3)",
  background: "var(--surface, #fff)",
  borderRadius: 8,
  padding: "5px 9px",
  fontSize: 12.5,
  fontWeight: 600,
  color: "var(--ink-2, #5d564d)",
  cursor: "pointer",
  flexShrink: 0,
};

const titleBtn: React.CSSProperties = {
  width: "100%",
  textAlign: "left",
  border: "none",
  background: "transparent",
  padding: "5px 2px",
  fontSize: 13.5,
  fontWeight: 700,
  color: "var(--ink, #2a2622)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

export function ChatDock({ client, analytics, embedded = false, onOpenKnowledgePage, onOpenGreenlight }: ChatDockProps) {
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

  // Multi-thread chat history. `activeId` null = an unsaved new chat (created on first send, in
  // real mode). `conversations` backs the history list; `titleDraft` non-null = renaming the
  // active thread inline. Mock builds keep the single ephemeral thread (the methods are inert).
  const [conversations, setConversations] = useState<ConversationRow[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [titleDraft, setTitleDraft] = useState<string | null>(null);

  const greeting = useCallback((): Message => ({
    who: "agent",
    text: "Ask anything about your pipeline, your agents, or what happened this week.",
  }), []);

  const refreshConversations = useCallback(async () => {
    if (api.isMock()) return;
    try {
      const r = await api.listConversations("active");
      setConversations(r.conversations);
    } catch {
      /* honest: an empty/failed list just shows "No past chats yet" — never invented threads. */
    }
  }, [api]);

  useEffect(() => { void refreshConversations(); }, [refreshConversations]);

  const newChat = useCallback(() => {
    setActiveId(null);
    setMsgs([greeting()]);
    setShowHistory(false);
    setTitleDraft(null);
  }, [greeting]);

  const openConversation = useCallback(async (c: ConversationRow) => {
    setActiveId(c.id);
    setShowHistory(false);
    setTitleDraft(null);
    try {
      const r = await api.getConversationMessages(c.id);
      const mapped: Message[] = r.messages.map((m) => ({
        who: m.role === "user" ? "me" : "agent",
        text: m.content,
        citations: m.citations,
        grounding: m.grounding_status ?? null,
      }));
      setMsgs(mapped.length ? mapped : [greeting()]);
    } catch {
      setMsgs([greeting()]);
    }
  }, [api, greeting]);

  const currentTitle = activeId
    ? (conversations.find((c) => c.id === activeId)?.title || "New chat")
    : "New chat";

  const commitRename = useCallback(async () => {
    const t = (titleDraft ?? "").trim();
    if (!activeId || !t) { setTitleDraft(null); return; }
    try {
      const row = await api.renameConversation(activeId, t);
      setConversations((cs) => cs.map((c) => (c.id === row.id ? row : c)));
    } catch {
      /* keep the old title — a failed rename is silent, never a broken UI */
    }
    setTitleDraft(null);
  }, [api, activeId, titleDraft]);

  const archiveConversation = useCallback(async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try { await api.archiveConversation(id); } catch { /* best-effort */ }
    setConversations((cs) => cs.filter((c) => c.id !== id));
    if (id === activeId) newChat();
  }, [api, activeId, newChat]);

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
      // Ensure a real conversation thread exists (real mode) so this turn + its transcript land in
      // one; auto-name a fresh thread from the first message. A creation failure falls back to the
      // legacy tenant-level session (convId stays null) — chat keeps working either way.
      let convId = activeId;
      if (!api.isMock() && convId === null) {
        try {
          const created = await api.createConversation(
            body.slice(0, 48).replace(/\s+/g, " ").trim());
          convId = created.id;
          setActiveId(created.id);
          setConversations((cs) => [created, ...cs]);
        } catch {
          /* fall back to the tenant-level session */
        }
      }
      // ASYNC TURN CONTRACT: settled === false means the delegation/tool round-trips are
      // still in flight server-side — continue the SAME turn (no new message, no human
      // nudge) until it settles. Each continue is a short request under the edge's 60s
      // ceiling; narration accumulates progressively so the customer watches it work.
      const settleTurn = async (first: ChatResponse) => {
        let res = first;
        let narration = res.answer ?? "";
        let continues = 0;
        const MAX_CONTINUES = 10;
        // Tally everything STAGED for approval across the whole turn (a staged call surfaces in
        // exactly one settle leg's digest, so we accumulate rather than read only the final leg).
        const seenApprovalIds = new Set<string>();
        let queued = routedApprovalCount(res.pending_approvals, seenApprovalIds);
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
          res = await api.continueChat(convId);
          if (res.answer) {
            narration = narration ? `${narration}\n\n${res.answer}` : res.answer;
          }
          queued += routedApprovalCount(res.pending_approvals, seenApprovalIds);
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
              grounding: res.grounding_status ?? null,
              approvals: queued > 0 ? queued : undefined },
          ];
        });
        return res;
      };
      try {
        let res: ChatResponse;
        try {
          res = await api.chat(body, convId);
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
        // Re-pull the list so the active thread floats to the top (updated_at) and a freshly
        // auto-named thread shows its title. Best-effort; never blocks the turn.
        void refreshConversations();
      }
    },
    [api, ph, draft, sending, embedded, runBalto, activeId, refreshConversations],
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

      {/* History strip: toggle the past-chats list, rename the active thread inline, start a new
          one. Hidden in mock builds (the prototype keeps a single ephemeral thread). */}
      {!api.isMock() && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, paddingBottom: 10,
                      borderBottom: "1px solid var(--line, #e3ddd3)" }}>
          <button
            data-testid="chat-history-toggle"
            title="Chat history"
            onClick={() => { const next = !showHistory; setShowHistory(next); if (next) void refreshConversations(); }}
            style={iconBtn}
          >
            <span aria-hidden style={{ fontSize: 15, lineHeight: 1 }}>☰</span>
          </button>
          <div style={{ flex: 1, minWidth: 0 }}>
            {titleDraft !== null ? (
              <input
                data-testid="chat-title-input"
                autoFocus
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
                onBlur={() => void commitRename()}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void commitRename();
                  if (e.key === "Escape") setTitleDraft(null);
                }}
                style={{ width: "100%", boxSizing: "border-box", border: "1px solid var(--line, #e3ddd3)",
                         borderRadius: 8, padding: "5px 9px", fontSize: 13.5, fontFamily: "inherit" }}
              />
            ) : (
              <button
                data-testid="chat-title"
                title={activeId ? "Rename this chat" : "Start typing to begin a new chat"}
                onClick={() => { if (activeId) setTitleDraft(currentTitle); }}
                style={{ ...titleBtn, cursor: activeId ? "text" : "default" }}
              >
                {currentTitle}
              </button>
            )}
          </div>
          <button data-testid="chat-new" title="New chat" onClick={newChat} style={iconBtn}>
            <span aria-hidden style={{ fontSize: 15, lineHeight: 1, marginRight: 4 }}>+</span>New
          </button>
        </div>
      )}

      {showHistory && !api.isMock() && (
        <div data-testid="chat-history"
             style={{ display: "flex", flexDirection: "column", gap: 2, maxHeight: 220,
                      overflowY: "auto", paddingBottom: 6 }}>
          {conversations.length === 0 ? (
            <div data-testid="chat-history-empty"
                 style={{ fontSize: 13, color: "var(--ink-3, #8a8278)", padding: "6px 4px" }}>
              No past chats yet — your conversations will appear here.
            </div>
          ) : (
            conversations.map((c) => (
              <div
                key={c.id}
                data-testid="chat-history-item"
                onClick={() => void openConversation(c)}
                style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 9px",
                         borderRadius: 8, cursor: "pointer",
                         background: c.id === activeId ? "var(--accent-soft, #f4f1ea)" : "transparent" }}
              >
                <span style={{ flex: 1, minWidth: 0, fontSize: 13.5, overflow: "hidden",
                               textOverflow: "ellipsis", whiteSpace: "nowrap",
                               color: "var(--ink, #2a2622)" }}>
                  {c.title || "New chat"}
                </span>
                <button
                  data-testid="chat-history-archive"
                  title="Archive this chat"
                  onClick={(e) => void archiveConversation(c.id, e)}
                  style={{ ...iconBtn, padding: "2px 7px", fontSize: 12, color: "var(--ink-3, #8a8278)" }}
                >
                  ✕
                </button>
              </div>
            ))
          )}
        </div>
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

            {m.who === "agent" && m.approvals && m.approvals > 0 && (
              <div
                data-testid="chat-approval-prompt"
                style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}
              >
                <span style={{ fontSize: 13, color: "var(--ink-2, #5d564d)" }}>
                  {m.approvals === 1
                    ? "1 action is waiting for your approval."
                    : `${m.approvals} actions are waiting for your approval.`}
                </span>
                <button
                  data-testid="chat-review-greenlight"
                  onClick={() => {
                    if (onOpenGreenlight) {
                      onOpenGreenlight();
                    } else {
                      window.location.assign("/?view=greenlight");
                    }
                  }}
                  style={{
                    padding: "6px 13px",
                    borderRadius: 10,
                    border: "1px solid var(--accent, #2a2622)",
                    background: "var(--accent, #2a2622)",
                    color: "#fff",
                    fontSize: 13,
                    fontWeight: 650,
                    cursor: "pointer",
                  }}
                >
                  Review in Greenlight
                </button>
              </div>
            )}

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
                    <div data-testid="citation-source" style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                      {c.source_ref || "ungrounded"}
                      {citationPageRef(c.source_ref) && (
                        <button
                          data-testid="citation-open-page"
                          onClick={() => {
                            const ref = citationPageRef(c.source_ref)!;
                            if (onOpenKnowledgePage) {
                              onOpenKnowledgePage(ref);
                            } else {
                              window.location.assign(`/?view=knowledge&doc=${encodeURIComponent(ref)}`);
                            }
                          }}
                          style={{
                            border: "1px solid var(--line, #e3ddd3)",
                            background: "transparent",
                            color: "var(--ink-2, #5d564d)",
                            borderRadius: 7,
                            padding: "1px 8px",
                            fontSize: 11,
                            fontWeight: 650,
                            cursor: "pointer",
                          }}
                        >
                          Open page
                        </button>
                      )}
                    </div>
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
