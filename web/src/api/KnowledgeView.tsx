// Knowledge view, wired to the control-plane API via ApiClient — the real-mode
// counterpart of the FLStore Knowledge prototype (src/screens/knowledge.tsx,
// mock mode only). Follows the AgentsRoster/WorkflowsView conventions exactly.
// Everything rendered here is honest:
//
//   * The inventory comes straight from GET /knowledge: per-source document
//     counts + the newest-ingested timestamp + totals, RLS-scoped server-side.
//     A plain aggregate (no embedder), so it's honest the moment the data
//     plane is wired. An un-ingested tenant gets a calm empty state — never an
//     invented corpus.
//   * Search rides GET /knowledge/search (cosine similarity over the tenant's
//     corpus): ref_id + source + a bounded snippet + score. The API embeds the
//     query with Titan (Bedrock) at call time — env-key-gated on the live task
//     today — so when the model isn't reachable the API answers
//     search_available: false and this view shows a calm "search is warming up"
//     note, NOT an error. The inventory stays useful regardless.
//   * READ-ONLY by design: no upload/delete controls this cycle — ingestion is
//     the worker's job; the UI promises nothing it can't keep.
//   * A 404 from /knowledge means the live API image predates these routes (the
//     web can deploy ahead of the API): a calm "rolling out" state with a
//     refresh affordance — NOT an error wall.
//   * Raw transport strings ("API <code>", server detail dumps) never reach the
//     DOM — every catch routes through friendlyErrorMessage.

import React from "react";
import {
  ApiClient,
  ApiError,
  defaultClient,
  friendlyErrorMessage,
  type KnowledgeInventoryResponse,
  type KnowledgeSearchResponse,
  type KnowledgeSearchResult,
  type KnowledgeSource,
} from "./client";
import { Spinner } from "./Spinner";

const { useState, useEffect, useCallback } = React;

// Mirrors api/knowledge_routes.py MAX_Q_LEN — the input enforces it so typing
// can never produce a 422.
const MAX_Q_LEN = 500;

// Friendly labels for the known ingest sources (db/schema.sql: hubspot|stripe|
// call|email|upload). An unknown source renders its raw value, never dropped.
const SOURCE_LABELS: Record<string, string> = {
  hubspot: "HubSpot",
  stripe: "Stripe",
  call: "Calls",
  email: "Email",
  upload: "Uploads",
};

function sourceLabel(source: string | null): string {
  if (!source) return "Other";
  return SOURCE_LABELS[source] ?? source;
}

function fmtCount(n: number): string {
  return n.toLocaleString();
}

function fmtWhen(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

// ---------------------------------------------------------------------------
// Styles (house style: hairline cards on the soft surface palette)
// ---------------------------------------------------------------------------

const card: React.CSSProperties = {
  border: "1px solid var(--line, #e3ddd3)",
  background: "var(--surface, #fff)",
  borderRadius: 14,
  padding: "18px 20px",
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

const muted: React.CSSProperties = { color: "var(--ink-3, #8a8278)" };

const sourceBadge: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  padding: "3px 10px",
  borderRadius: 999,
  fontSize: 11.5,
  fontWeight: 700,
  letterSpacing: ".02em",
  fontFamily: "var(--mono, ui-monospace, monospace)",
  background: "var(--accent-soft, #f4f1ea)",
  color: "var(--ink-2, #5d564d)",
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface KnowledgeViewProps {
  client?: ApiClient;
}

export function KnowledgeView({ client }: KnowledgeViewProps) {
  const api = client ?? defaultClient();
  const [data, setData] = useState<KnowledgeInventoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rollout, setRollout] = useState(false);

  // Search state — independent of the inventory load.
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState<KnowledgeSearchResponse | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setRollout(false);
    try {
      setData(await api.getKnowledge());
    } catch (e) {
      setData(null);
      if (e instanceof ApiError && e.status === 404) {
        setRollout(true);
      } else {
        setError(friendlyErrorMessage(e, "Couldn't load your knowledge base. Please try again."));
      }
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  const runSearch = useCallback(
    async (q: string) => {
      const term = q.trim();
      if (!term) return;
      setSearching(true);
      setSearchError(null);
      try {
        setSearch(await api.searchKnowledge(term));
      } catch (e) {
        setSearch(null);
        setSearchError(
          friendlyErrorMessage(e, "Couldn't run that search. Please try again."),
        );
      } finally {
        setSearching(false);
      }
    },
    [api],
  );

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void runSearch(query);
  };

  // --- source inventory card ---
  const sourceCard = (s: KnowledgeSource, i: number): React.ReactElement => (
    <div
      key={`${s.source ?? "other"}-${i}`}
      data-testid="knowledge-source"
      data-source={s.source ?? ""}
      style={{ ...card, display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}
    >
      <span style={sourceBadge}>{sourceLabel(s.source)}</span>
      <span style={{ fontSize: 18, fontWeight: 760, color: "var(--ink, #2a2622)" }}>
        {fmtCount(s.document_count)}
        <span style={{ fontSize: 12.5, fontWeight: 600, marginLeft: 5, ...muted }}>
          {s.document_count === 1 ? "document" : "documents"}
        </span>
      </span>
      <span style={{ marginLeft: "auto", fontSize: 12, ...muted }}>
        updated {fmtWhen(s.last_updated)}
      </span>
    </div>
  );

  // --- one search hit ---
  const resultRow = (r: KnowledgeSearchResult, i: number): React.ReactElement => (
    <div
      key={`${r.ref_id ?? "hit"}-${i}`}
      data-testid="knowledge-result"
      style={{
        padding: "12px 0",
        borderTop: i === 0 ? "none" : "1px solid var(--line-2, #efe9df)",
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span style={sourceBadge}>{sourceLabel(r.source)}</span>
        {r.score !== null && (
          <span style={{ fontSize: 11.5, fontFamily: "var(--mono, ui-monospace, monospace)", ...muted }}>
            {Math.round(r.score * 100)}% match
          </span>
        )}
      </div>
      <p style={{ fontSize: 13, lineHeight: 1.55, color: "var(--ink, #2a2622)", margin: 0 }}>
        {r.snippet || "(no preview)"}
      </p>
    </div>
  );

  return (
    <div
      data-testid="knowledge-view"
      style={{ maxWidth: 920, margin: "0 auto", padding: "32px 24px", fontFamily: "system-ui, sans-serif" }}
    >
      <div style={{ marginBottom: 18 }}>
        <div style={{ fontSize: 12, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase", ...muted }}>
          Uplift knowledge
        </div>
        <h1 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.02em", margin: "6px 0 4px" }}>Knowledge</h1>
        <p style={{ ...muted, fontSize: 14 }}>
          Everything your agents can draw on — ingested from your connected sources and searchable
          in plain language. Read-only: this is the live corpus, not an editor.
        </p>
      </div>

      {loading && <Spinner testid="knowledge-loading" label="Loading your knowledge base..." />}

      {/* The live API image may predate /knowledge: a calm rollout note, not an error wall. */}
      {rollout && (
        <div data-testid="knowledge-rollout" style={{ ...card, fontSize: 13.5 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Knowledge API is rolling out</div>
          <p style={{ ...muted, lineHeight: 1.5 }}>
            Your deployment doesn&rsquo;t serve the knowledge endpoint yet — refresh after the next
            API deploy. Nothing is wrong with your workspace.
          </p>
          <button data-testid="knowledge-rollout-refresh" onClick={() => void load()} style={{ ...ghostBtn, marginTop: 10 }}>
            Refresh
          </button>
        </div>
      )}

      {error && (
        <div
          data-testid="knowledge-error"
          style={{ ...card, borderColor: "var(--rose, #b4413b)", fontSize: 13.5 }}
        >
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Something needs another try</div>
          <p style={{ ...muted, lineHeight: 1.5 }}>{error}</p>
          <button data-testid="knowledge-retry" onClick={() => void load()} style={{ ...ghostBtn, marginTop: 10 }}>
            Try again
          </button>
        </div>
      )}

      {!loading && !error && !rollout && data !== null && (
        <>
          {/* search — rides the corpus; honest degrade when the embedder is warming up */}
          <form onSubmit={onSubmit} style={{ display: "flex", gap: 8, marginBottom: 16 }}>
            <input
              data-testid="knowledge-search-input"
              type="text"
              value={query}
              maxLength={MAX_Q_LEN}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Ask your knowledge base — e.g. deals in negotiation"
              aria-label="Search your knowledge base"
              style={{
                flex: 1,
                minWidth: 0,
                padding: "10px 14px",
                borderRadius: 10,
                border: "1px solid var(--line, #e3ddd3)",
                background: "var(--surface, #fff)",
                color: "var(--ink, #2a2622)",
                fontSize: 14,
                fontFamily: "inherit",
              }}
            />
            <button
              data-testid="knowledge-search-submit"
              type="submit"
              disabled={searching || !query.trim()}
              style={{
                ...ghostBtn,
                background: "var(--ink, #2a2622)",
                color: "#fff",
                border: "none",
                opacity: searching || !query.trim() ? 0.55 : 1,
                cursor: searching || !query.trim() ? "default" : "pointer",
              }}
            >
              {searching ? "Searching..." : "Search"}
            </button>
          </form>

          {/* search results / honest states (only after a search has run) */}
          {searchError && (
            <div data-testid="knowledge-search-error" style={{ ...card, borderColor: "var(--rose, #b4413b)", fontSize: 13.5, marginBottom: 16 }}>
              <p style={{ ...muted, lineHeight: 1.5, margin: 0 }}>{searchError}</p>
            </div>
          )}
          {!searchError && search !== null && (
            <div style={{ marginBottom: 18 }}>
              {!search.search_available ? (
                <div
                  data-testid="knowledge-search-unavailable"
                  style={{ ...card, background: "var(--accent-soft, #f4f1ea)", fontSize: 13.5 }}
                >
                  <div style={{ fontWeight: 700, marginBottom: 4 }}>Search is warming up</div>
                  <p style={{ ...muted, lineHeight: 1.55, margin: 0 }}>
                    Semantic search needs the embedding model, which is being connected on our side.
                    Your documents below are already here; search lights up the moment it&rsquo;s
                    ready.
                  </p>
                </div>
              ) : search.results.length === 0 ? (
                <div data-testid="knowledge-search-empty" style={{ ...card, fontSize: 13.5, ...muted }}>
                  No matches for &ldquo;{search.query}&rdquo; in your knowledge base.
                </div>
              ) : (
                <div data-testid="knowledge-results" style={{ ...card, paddingTop: 7, paddingBottom: 9 }}>
                  {search.results.map(resultRow)}
                </div>
              )}
            </div>
          )}

          {/* inventory — sources + totals, or the honest empty state */}
          <h2 style={{ fontSize: 16, fontWeight: 750, letterSpacing: "-.01em", margin: "0 0 10px" }}>
            Your sources
          </h2>
          {data.total_documents === 0 ? (
            <div data-testid="knowledge-empty" style={{ ...card, fontSize: 13.5, ...muted }}>
              No documents yet. Your knowledge base fills in automatically as your connected sources
              (CRM, calls, email, uploads) are ingested — there&rsquo;s nothing for you to do here.
            </div>
          ) : (
            <>
              <div data-testid="knowledge-total" style={{ fontSize: 13, marginBottom: 12, ...muted }}>
                {fmtCount(data.total_documents)} documents across {data.source_count}{" "}
                {data.source_count === 1 ? "source" : "sources"}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {data.sources.map(sourceCard)}
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}

export default KnowledgeView;
