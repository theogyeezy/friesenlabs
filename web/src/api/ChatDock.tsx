// Chat dock wired to POST /chat via ApiClient. Renders the answer plus inline
// citations (claim + source ref + snippet). Mock mode returns a canned, grounded
// answer so Playwright runs offline. No token or payload is ever rendered.

import React from "react";
import { ApiClient, ApiError, defaultClient, friendlyErrorMessage, type Citation } from "./client";
import { Spinner } from "./Spinner";

const { useState, useCallback } = React;

interface Message {
  who: "me" | "agent";
  text: string;
  citations?: Citation[];
  /** True for friendly error copy rendered as an agent bubble. */
  error?: boolean;
}

export interface ChatDockProps {
  client?: ApiClient;
  /**
   * When mounted inside the shell's slide-over panel (which carries its own
   * "Ask your agents" header), suppress the standalone heading.
   */
  embedded?: boolean;
}

export function ChatDock({ client, embedded = false }: ChatDockProps) {
  const api = client ?? defaultClient();
  const [msgs, setMsgs] = useState<Message[]>([
    {
      who: "agent",
      text: "Ask anything about your pipeline, your agents, or what happened this week.",
    },
  ]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);

  const send = useCallback(
    async (text?: string) => {
      const body = (text ?? draft).trim();
      if (!body || sending) return;
      setDraft("");
      setSending(true);
      setMsgs((m) => [...m, { who: "me", text: body }]);
      try {
        const res = await api.chat(body);
        setMsgs((m) => [...m, { who: "agent", text: res.answer, citations: res.citations }]);
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
    [api, draft, sending],
  );

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
    </div>
  );
}

export default ChatDock;
