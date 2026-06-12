// Knowledge view, wired to the control-plane API via ApiClient — a Notion-style
// knowledge workspace over the real /knowledge surface. Follows the
// AgentsRoster/WorkflowsView conventions exactly. Everything rendered here is
// honest:
//
//   * PAGES (the editable corpus) ride GET/POST/PUT/DELETE /knowledge/documents:
//     a left rail lists every uploaded document (title + preview straight from
//     the stored original); the reader renders the EXACT stored text through the
//     safe markdown subset renderer (spec-not-code: react nodes only, no HTML,
//     no links); the editor round-trips it. Changed content lands under a NEW
//     ref server-side — the view follows the returned ref. A legacy upload
//     (predates raw-original storage) lists read-only with its indexed chunk
//     texts and an honest re-add-to-edit note — never reconstructed content.
//   * The pages endpoints can be AHEAD of the live API image (the web deploys
//     first): a 404/503 from GET /knowledge/documents degrades to a calm
//     "pages are rolling out" note in the rail while inventory + search stay
//     fully useful. Same honesty the inventory itself has had since day one.
//   * The inventory comes straight from GET /knowledge: per-source document
//     counts + newest-ingested timestamp, RLS-scoped server-side. An un-ingested
//     tenant gets a calm empty state — never an invented corpus.
//   * Search rides GET /knowledge/search (cosine over the tenant's corpus). When
//     the embedder isn't reachable the API answers search_available: false and
//     this view shows a calm "search is warming up" note, NOT an error. A hit in
//     the editable corpus opens its page in place.
//   * Saving a page when uploads aren't switched on (the ingest plane's
//     INGEST_REAL_STORES gate answers 503) degrades to honest copy — the
//     document did NOT land, never a fake success.
//   * Raw transport strings ("API <code>", server detail dumps) never reach the
//     DOM — every catch routes through friendlyErrorMessage.

import React from "react";
import {
  ApiClient,
  ApiError,
  defaultClient,
  friendlyErrorMessage,
  type KnowledgeDocumentDetail,
  type KnowledgeDocumentSummary,
  type KnowledgeInventoryResponse,
  type KnowledgeSearchResponse,
  type KnowledgeSearchResult,
  type KnowledgeSource,
} from "./client";
import { Spinner } from "./Spinner";
import { SafeMarkdown } from "../dashboard/markdown";

const { useState, useEffect, useCallback, useLayoutEffect, useRef } = React;

// Mirrors api/knowledge_routes.py MAX_Q_LEN — the input enforces it so typing
// can never produce a 422.
const MAX_Q_LEN = 500;

// Mirror api/knowledge_routes.py upload bounds (the inputs enforce them client-side).
const MAX_TITLE_LEN = 200;
const MAX_DOC_CHARS = 100_000;

// Friendly labels for the known ingest sources (db/schema.sql: hubspot|stripe|
// call|email|upload). An unknown source renders its raw value, never dropped.
const SOURCE_LABELS: Record<string, string> = {
  hubspot: "HubSpot",
  stripe: "Stripe",
  call: "Calls",
  email: "Email",
  upload: "Pages",
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

/** A search hit's chunk-family prefix: 'upload:pricing-policy-ab12cd34#2' -> the page ref. */
function hitRefPrefix(refId: string | null): string | null {
  if (!refId) return null;
  return refId.split("#")[0];
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

const primaryBtn: React.CSSProperties = {
  ...ghostBtn,
  background: "var(--ink, #2a2622)",
  color: "#fff",
  border: "none",
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

const fieldStyle: React.CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  padding: "9px 12px",
  borderRadius: 10,
  border: "1px solid var(--line, #e3ddd3)",
  background: "var(--surface, #fff)",
  color: "var(--ink, #2a2622)",
  fontSize: 14,
  fontFamily: "inherit",
};

// The two-pane workspace collapses on small screens; a tiny scoped stylesheet
// beats prop-drilling a resize observer for one breakpoint.
const LAYOUT_CSS = `
  .kn-grid { display: grid; grid-template-columns: 290px minmax(0, 1fr); gap: 20px; align-items: start; }
  .kn-rail { position: sticky; top: 16px; display: flex; flex-direction: column; gap: 18px; }
  .kn-page-btn { display: block; width: 100%; text-align: left; padding: 8px 10px; border: none;
    border-radius: 9px; background: transparent; cursor: pointer; font-family: inherit; }
  .kn-page-btn:hover { background: var(--accent-soft, #f4f1ea); }
  .kn-page-btn.sel { background: var(--accent-soft, #f4f1ea); }
  @media (max-width: 800px) { .kn-grid { grid-template-columns: 1fr; } .kn-rail { position: static; } }
`;

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

type EditorState =
  | { mode: "closed" }
  | { mode: "new"; title: string; content: string }
  | { mode: "edit"; refId: string; title: string; content: string; baseTitle: string; baseContent: string };

export interface KnowledgeViewProps {
  client?: ApiClient;
  /** Open this page on mount / when it changes (the in-shell citation → page path).
   * The `?doc=` URL param covers the same need for standalone/deep-link mounts. */
  initialPageRef?: string | null;
  /** Called once after `initialPageRef` is applied, so the owner can clear its state
   * (a later remount must not re-open a page the user already navigated away from). */
  onInitialPageConsumed?: () => void;
}

/** The `?doc=` deep-link target, read ONCE at module use (mount): every knowledge page is
 * linkable as /?view=knowledge&doc=<ref> — citations in standalone chat ride this. */
function docParamRef(): string | null {
  try {
    return new URLSearchParams(window.location.search).get("doc");
  } catch {
    return null;
  }
}

export function KnowledgeView({ client, initialPageRef, onInitialPageConsumed }: KnowledgeViewProps) {
  const api = client ?? defaultClient();
  const [data, setData] = useState<KnowledgeInventoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // "rollout" = the live image predates the route (404 — refresh after the next deploy);
  // "unprovisioned" = the API answered but the data plane isn't wired for this deployment
  // (503). Distinct copy (knowledge audit P1: unprovisioned ≠ rolling-out) — both calm.
  const [rollout, setRollout] = useState<false | "rollout" | "unprovisioned">(false);

  // Pages (the editable corpus) — independent of the inventory load so either
  // surface staying useful never depends on the other.
  const [pages, setPages] = useState<KnowledgeDocumentSummary[] | null>(null);
  const [pagesRollout, setPagesRollout] = useState(false);
  const [pagesError, setPagesError] = useState<string | null>(null);

  // The open page.
  const [openRef, setOpenRef] = useState<string | null>(null);
  const [doc, setDoc] = useState<KnowledgeDocumentDetail | null>(null);
  const [docLoading, setDocLoading] = useState(false);
  const [docError, setDocError] = useState<string | null>(null);
  const [cleanupNote, setCleanupNote] = useState(false); // previous_removed: false after an edit

  // Editor (new page / edit page).
  const [editor, setEditor] = useState<EditorState>({ mode: "closed" });
  const [editorPreview, setEditorPreview] = useState(false);
  const [saving, setSaving] = useState(false);
  const [addNote, setAddNote] = useState<string | null>(null);
  const [addError, setAddError] = useState<string | null>(null);
  const [uploadsOff, setUploadsOff] = useState(false);

  // Delete (two-step confirm, inline).
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // Search state — independent of everything else.
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState<KnowledgeSearchResponse | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  // Rail filter — pure client-side narrowing of the loaded pages list (the semantic
  // search box above is the corpus-wide tool; this is for eyeballing a long rail).
  const [pageFilter, setPageFilter] = useState("");

  const titleRef = useRef<HTMLInputElement | null>(null);
  const contentRef = useRef<HTMLTextAreaElement | null>(null);
  // Where the caret belongs after a programmatic list-continuation edit — applied in a
  // layout effect (synchronously after commit, BEFORE the next key event can land), so
  // fast typing can never race the caret restore.
  const pendingCursor = useRef<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setRollout(false);
    try {
      setData(await api.getKnowledge());
    } catch (e) {
      setData(null);
      if (e instanceof ApiError && (e.status === 404 || e.status === 503)) {
        // 404 = live API image predates the route; 503 = data plane unconfigured.
        // Distinct calm copy for each — never a red error wall.
        setRollout(e.status === 404 ? "rollout" : "unprovisioned");
      } else {
        setError(friendlyErrorMessage(e, "Couldn't load your knowledge base. Please try again."));
      }
    } finally {
      setLoading(false);
    }
  }, [api]);

  const loadPages = useCallback(async () => {
    setPagesRollout(false);
    setPagesError(null);
    try {
      const res = await api.listKnowledgeDocuments();
      setPages(res.documents);
    } catch (e) {
      setPages(null);
      if (e instanceof ApiError && (e.status === 404 || e.status === 503)) {
        // The web can deploy ahead of the API image — calm note, not an error.
        setPagesRollout(true);
      } else {
        setPagesError(friendlyErrorMessage(e, "Couldn't load your pages. Please try again."));
      }
    }
  }, [api]);

  useEffect(() => {
    void load();
    void loadPages();
  }, [load, loadPages]);

  // Citation → page. Two paths: the in-shell PROP (consumed-and-cleared by the owner, so
  // re-clicking the same citation is a fresh null→ref transition and opens again), and the
  // ?doc= deep-link param (once, on mount). openPage 404s honestly if the ref isn't a page.
  const docParamApplied = useRef(false);
  useEffect(() => {
    if (initialPageRef) {
      setEditor({ mode: "closed" });
      void openPage(initialPageRef);
      onInitialPageConsumed?.();
      return;
    }
    if (!docParamApplied.current) {
      docParamApplied.current = true;
      const target = docParamRef();
      if (target) {
        setEditor({ mode: "closed" });
        void openPage(target);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps — fires per prop change only;
    // re-running on openPage recreation must not re-open a page the user navigated away from.
  }, [initialPageRef]);

  const openPage = useCallback(
    async (refId: string) => {
      setOpenRef(refId);
      setDoc(null);
      setDocError(null);
      setCleanupNote(false);
      setConfirmingDelete(false);
      setDocLoading(true);
      try {
        setDoc(await api.getKnowledgeDocument(refId));
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) {
          // The page vanished between list and open (deleted elsewhere) — honest copy
          // and a fresh list, never a stale ghost.
          setDocError("That page isn't in your knowledge base anymore.");
          void loadPages();
        } else {
          setDocError(friendlyErrorMessage(e, "Couldn't open that page. Please try again."));
        }
      } finally {
        setDocLoading(false);
      }
    },
    [api, loadPages],
  );

  // --- editor ----------------------------------------------------------------

  const editorDirty =
    editor.mode === "new"
      ? editor.title.trim() !== "" || editor.content.trim() !== ""
      : editor.mode === "edit"
        ? editor.title !== editor.baseTitle || editor.content !== editor.baseContent
        : false;

  const guardDiscard = (): boolean => {
    if (editor.mode === "closed" || !editorDirty) return true;
    return window.confirm("Discard unsaved changes to this page?");
  };

  const startNewPage = () => {
    if (!guardDiscard()) return;
    setEditor({ mode: "new", title: "", content: "" });
    setEditorPreview(false);
    setAddNote(null);
    setAddError(null);
    setConfirmingDelete(false);
    setTimeout(() => titleRef.current?.focus(), 0);
  };

  const startEdit = () => {
    if (!doc || !doc.editable || doc.content === null) return;
    setEditor({
      mode: "edit",
      refId: doc.ref_id,
      title: doc.title,
      content: doc.content,
      baseTitle: doc.title,
      baseContent: doc.content,
    });
    setEditorPreview(false);
    setAddNote(null);
    setAddError(null);
    setConfirmingDelete(false);
  };

  const closeEditor = () => {
    if (!guardDiscard()) return;
    setEditor({ mode: "closed" });
    setAddError(null);
  };

  const setEditorField = (patch: Partial<{ title: string; content: string }>) => {
    setEditor((cur) => (cur.mode === "closed" ? cur : { ...cur, ...patch }));
  };

  const saveEditor = async () => {
    if (editor.mode === "closed" || saving) return;
    const title = editor.title.trim();
    const content = editor.content.trim();
    if (!title || !content) return;
    setSaving(true);
    setAddError(null);
    setAddNote(null);
    try {
      let refId: string | null;
      let chunks: number;
      let cleanupPending = false;
      if (editor.mode === "new") {
        const res = await api.addKnowledgeDocument(title, content);
        refId = res.ref_id;
        chunks = res.chunks;
      } else {
        const res = await api.updateKnowledgeDocument(editor.refId, title, content);
        refId = res.ref_id;
        chunks = res.chunks;
        cleanupPending = res.previous_removed === false;
      }
      setAddNote(`Added "${title}" — ${chunks} ${chunks === 1 ? "section" : "sections"} indexed.`);
      setEditor({ mode: "closed" });
      void load(); // the inventory now includes the new upload
      void loadPages();
      if (refId) {
        // Best-effort open of the saved page — if the pages surface isn't served
        // yet (web ahead of API), the success note above already tells the story.
        await openPage(refId);
      }
      // After openPage's state reset, so the honest cleanup signal survives the open.
      setCleanupNote(cleanupPending);
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) {
        // The ingest plane isn't switched on for this deployment — honest copy, never
        // a fake success (the API refused loudly; nothing landed).
        setUploadsOff(true);
        setEditor({ mode: "closed" });
      } else if (err instanceof ApiError && err.status === 409) {
        setAddError("This page predates editing — add its content as a new page instead.");
      } else {
        setAddError(friendlyErrorMessage(err, "Couldn't save the page. Please try again."));
      }
    } finally {
      setSaving(false);
    }
  };

  const onEditorKeyDown = (e: React.KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && (e.key === "s" || e.key === "Enter")) {
      e.preventDefault();
      void saveEditor();
    }
    if (e.key === "Escape") {
      e.preventDefault();
      closeEditor();
    }
  };

  // Auto-grow the editor with its content (Notion-style: the page scrolls, not the box).
  useEffect(() => {
    const ta = contentRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.max(320, ta.scrollHeight)}px`;
  }, [editor, editorPreview]);

  // Enter inside a markdown list continues it ("- " / "* " / "3. " -> "4. "); Enter on an
  // EMPTY item exits the list (removes the dangling prefix) — the Notion muscle memory.
  const onContentKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== "Enter" || e.shiftKey || e.metaKey || e.ctrlKey || e.altKey) return;
    const ta = e.currentTarget;
    const { selectionStart, selectionEnd, value } = ta;
    if (selectionStart !== selectionEnd) return; // replacing a selection: browser default
    const lineStart = value.lastIndexOf("\n", selectionStart - 1) + 1;
    const line = value.slice(lineStart, selectionStart);
    const m = /^(\s*)(?:([-*])|(\d+)([.)]))\s/.exec(line);
    if (!m) return;
    e.preventDefault();
    const [prefix, indent, bullet, num, numSep] = m;
    let newValue: string;
    let cursor: number;
    if (line.length === prefix.length) {
      // Empty item: drop the prefix, stay on this line.
      newValue = value.slice(0, lineStart) + value.slice(selectionStart);
      cursor = lineStart;
    } else {
      const next = bullet ? `${indent}${bullet} ` : `${indent}${Number(num) + 1}${numSep} `;
      newValue = `${value.slice(0, selectionStart)}\n${next}${value.slice(selectionEnd)}`;
      cursor = selectionStart + 1 + next.length;
    }
    pendingCursor.current = cursor;
    setEditorField({ content: newValue });
  };

  useLayoutEffect(() => {
    if (pendingCursor.current !== null && contentRef.current) {
      contentRef.current.selectionStart = contentRef.current.selectionEnd = pendingCursor.current;
      pendingCursor.current = null;
    }
  });

  // --- delete ------------------------------------------------------------------

  const deletePage = async () => {
    if (!doc || deleting) return;
    setDeleting(true);
    try {
      await api.deleteKnowledgeDocument(doc.ref_id);
      setOpenRef(null);
      setDoc(null);
      setConfirmingDelete(false);
      void load();
      void loadPages();
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        // Already gone — same outcome the user wanted; refresh honestly.
        setOpenRef(null);
        setDoc(null);
        void loadPages();
      } else {
        setDocError(friendlyErrorMessage(e, "Couldn't delete that page. Please try again."));
      }
    } finally {
      setDeleting(false);
    }
  };

  // --- search ------------------------------------------------------------------

  const runSearch = useCallback(
    async (q: string, offset = 0) => {
      const term = q.trim();
      if (!term) return;
      setSearching(true);
      setSearchError(null);
      try {
        const res = await api.searchKnowledge(term, undefined, offset);
        // offset > 0 = "show more": APPEND to the same query's results; anything else
        // (a new query, a degrade, an old-API response) replaces wholesale.
        setSearch((prev) =>
          offset > 0 && prev !== null && prev.query === res.query &&
          prev.search_available && res.search_available
            ? { ...res, results: [...prev.results, ...res.results] }
            : res,
        );
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

  const clearSearch = () => {
    setSearch(null);
    setSearchError(null);
    setQuery("");
  };

  // --- pieces --------------------------------------------------------------------

  const sourceRow = (s: KnowledgeSource, i: number): React.ReactElement => (
    <div
      key={`${s.source ?? "other"}-${i}`}
      data-testid="knowledge-source"
      data-source={s.source ?? ""}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "8px 2px",
        borderTop: i === 0 ? "none" : "1px solid var(--line-2, #efe9df)",
      }}
    >
      <span style={sourceBadge}>{sourceLabel(s.source)}</span>
      <span style={{ fontSize: 13, fontWeight: 720, color: "var(--ink, #2a2622)" }}>
        {fmtCount(s.document_count)}
      </span>
      <span style={{ marginLeft: "auto", fontSize: 11, ...muted }}>{fmtWhen(s.last_updated)}</span>
    </div>
  );

  const pageRow = (p: KnowledgeDocumentSummary): React.ReactElement => {
    const sel = p.ref_id !== null && p.ref_id === openRef;
    return (
      <button
        key={p.ref_id ?? p.title}
        data-testid="knowledge-page-item"
        className={`kn-page-btn${sel ? " sel" : ""}`}
        onClick={() => {
          if (!p.ref_id || !guardDiscard()) return;
          setEditor({ mode: "closed" });
          setAddNote(null);
          void openPage(p.ref_id);
        }}
      >
        <span
          style={{
            display: "block",
            fontSize: 13.5,
            fontWeight: sel ? 750 : 650,
            color: "var(--ink, #2a2622)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {p.title}
          {!p.editable && (
            <span style={{ fontSize: 10.5, fontWeight: 700, marginLeft: 6, ...muted }}>read-only</span>
          )}
        </span>
        {p.preview && (
          <span
            style={{
              display: "block",
              fontSize: 11.5,
              marginTop: 2,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              ...muted,
            }}
          >
            {p.preview}
          </span>
        )}
      </button>
    );
  };

  const resultRow = (r: KnowledgeSearchResult, i: number): React.ReactElement => {
    // Offer "Open page" only when the hit's chunk family IS a listed page (covers both
    // upload: and seeded demo:kb: refs) — never a guess from the ref shape alone.
    const prefix = hitRefPrefix(r.ref_id);
    const pageRef = prefix !== null && (pages ?? []).some((p) => p.ref_id === prefix) ? prefix : null;
    return (
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
          {pageRef && (
            <button
              data-testid="knowledge-result-open"
              onClick={() => {
                if (!guardDiscard()) return;
                setEditor({ mode: "closed" });
                void openPage(pageRef);
              }}
              style={{ ...ghostBtn, marginLeft: "auto", padding: "3px 10px", fontSize: 12 }}
            >
              Open page
            </button>
          )}
        </div>
        <p style={{ fontSize: 13, lineHeight: 1.55, color: "var(--ink, #2a2622)", margin: 0 }}>
          {r.snippet || "(no preview)"}
        </p>
      </div>
    );
  };

  // --- the reader / editor pane -----------------------------------------------------

  const editorPane = editor.mode !== "closed" && (
    <div data-testid="knowledge-add-form" style={{ ...card, padding: "22px 24px" }} onKeyDown={onEditorKeyDown}>
      <input
        ref={titleRef}
        data-testid="knowledge-add-title"
        type="text"
        value={editor.title}
        maxLength={MAX_TITLE_LEN}
        onChange={(e) => setEditorField({ title: e.target.value })}
        placeholder="Untitled"
        aria-label="Page title"
        style={{
          width: "100%",
          boxSizing: "border-box",
          border: "none",
          outline: "none",
          background: "transparent",
          color: "var(--ink, #2a2622)",
          fontSize: 24,
          fontWeight: 760,
          letterSpacing: "-.02em",
          fontFamily: "inherit",
          padding: "0 0 10px",
        }}
      />
      <div style={{ display: "flex", gap: 6, marginBottom: 10 }}>
        <button
          data-testid="knowledge-editor-write"
          onClick={() => setEditorPreview(false)}
          style={{ ...ghostBtn, padding: "4px 12px", fontSize: 12, ...(editorPreview ? {} : { background: "var(--accent-soft, #f4f1ea)" }) }}
        >
          Write
        </button>
        <button
          data-testid="knowledge-editor-preview"
          onClick={() => setEditorPreview(true)}
          style={{ ...ghostBtn, padding: "4px 12px", fontSize: 12, ...(editorPreview ? { background: "var(--accent-soft, #f4f1ea)" } : {}) }}
        >
          Preview
        </button>
        <span style={{ marginLeft: "auto", fontSize: 11.5, alignSelf: "center", ...muted }}>
          # heading · **bold** · *italic* · `code` · - list
        </span>
      </div>
      {editorPreview ? (
        <div
          data-testid="knowledge-editor-rendered"
          style={{ minHeight: 320, padding: "4px 2px", fontSize: 14.5, lineHeight: 1.65, color: "var(--ink, #2a2622)" }}
        >
          {editor.content.trim() ? (
            <SafeMarkdown body={editor.content} />
          ) : (
            <p style={{ ...muted, fontSize: 13.5 }}>Nothing to preview yet.</p>
          )}
        </div>
      ) : (
        <textarea
          ref={contentRef}
          data-testid="knowledge-add-content"
          value={editor.content}
          maxLength={MAX_DOC_CHARS}
          onChange={(e) => setEditorField({ content: e.target.value })}
          onKeyDown={onContentKeyDown}
          placeholder="Write the page — your agents will ground answers on it and cite it by section."
          aria-label="Page content"
          rows={14}
          style={{
            ...fieldStyle,
            fontSize: 13.5,
            lineHeight: 1.6,
            resize: "vertical",
            minHeight: 320,
            overflow: "hidden",
          }}
        />
      )}
      {addError && (
        <p data-testid="knowledge-add-error" style={{ color: "var(--rose, #b4413b)", fontSize: 13, margin: "10px 0 0" }}>
          {addError}
        </p>
      )}
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 12 }}>
        <button
          data-testid="knowledge-add-submit"
          onClick={() => void saveEditor()}
          disabled={saving || !editor.title.trim() || !editor.content.trim()}
          style={{
            ...primaryBtn,
            opacity: saving || !editor.title.trim() || !editor.content.trim() ? 0.55 : 1,
            cursor: saving || !editor.title.trim() || !editor.content.trim() ? "default" : "pointer",
          }}
        >
          {saving ? "Indexing..." : editor.mode === "new" ? "Add to knowledge base" : "Save changes"}
        </button>
        <button data-testid="knowledge-add-cancel" onClick={closeEditor} style={ghostBtn}>
          Cancel
        </button>
        <span style={{ marginLeft: "auto", fontSize: 11.5, ...muted }}>⌘S to save · Esc to cancel</span>
      </div>
    </div>
  );

  const readerPane = editor.mode === "closed" && openRef !== null && (
    <div data-testid="knowledge-doc" style={{ ...card, padding: "22px 24px" }}>
      {docLoading && <Spinner testid="knowledge-doc-loading" label="Opening the page..." />}
      {docError && (
        <div data-testid="knowledge-doc-error" style={{ fontSize: 13.5 }}>
          <p style={{ ...muted, lineHeight: 1.5, margin: 0 }}>{docError}</p>
          <button onClick={() => setOpenRef(null)} style={{ ...ghostBtn, marginTop: 10 }}>
            Back to pages
          </button>
        </div>
      )}
      {!docLoading && !docError && doc !== null && (
        <>
          <div style={{ display: "flex", alignItems: "flex-start", gap: 10, flexWrap: "wrap" }}>
            <div style={{ flex: 1, minWidth: 200 }}>
              <h2
                data-testid="knowledge-doc-title"
                style={{ fontSize: 24, fontWeight: 760, letterSpacing: "-.02em", margin: 0 }}
              >
                {doc.title}
              </h2>
              <div style={{ fontSize: 12, marginTop: 5, ...muted }}>
                updated {fmtWhen(doc.updated_at)} · {fmtCount(doc.chunks)}{" "}
                {doc.chunks === 1 ? "section" : "sections"} indexed
                {!doc.editable && " · read-only"}
              </div>
            </div>
            {doc.editable && !confirmingDelete && (
              <button data-testid="knowledge-doc-edit" onClick={startEdit} style={ghostBtn}>
                Edit
              </button>
            )}
            {!confirmingDelete ? (
              <button
                data-testid="knowledge-doc-delete"
                onClick={() => setConfirmingDelete(true)}
                style={{ ...ghostBtn, color: "var(--rose, #b4413b)" }}
              >
                Delete
              </button>
            ) : (
              <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
                <span style={{ fontSize: 12.5, ...muted }}>Delete this page?</span>
                <button
                  data-testid="knowledge-doc-confirm-delete"
                  onClick={() => void deletePage()}
                  disabled={deleting}
                  style={{ ...ghostBtn, color: "#fff", background: "var(--rose, #b4413b)", border: "none" }}
                >
                  {deleting ? "Deleting..." : "Delete"}
                </button>
                <button onClick={() => setConfirmingDelete(false)} style={ghostBtn}>
                  Keep
                </button>
              </span>
            )}
          </div>

          {cleanupNote && (
            <div
              data-testid="knowledge-doc-cleanup-note"
              style={{ ...card, background: "var(--accent-soft, #f4f1ea)", fontSize: 12.5, margin: "14px 0 0", padding: "10px 14px" }}
            >
              The edit saved, but the previous version couldn&rsquo;t be cleaned up yet — it may
              still appear in your pages until it&rsquo;s removed.
            </div>
          )}

          {!doc.editable && (
            <div
              data-testid="knowledge-legacy-note"
              style={{ ...card, background: "var(--accent-soft, #f4f1ea)", fontSize: 12.5, margin: "14px 0 0", padding: "10px 14px" }}
            >
              This page was added before editing existed, so only its indexed sections are shown.
              To make it editable, add its content as a new page and delete this one.
            </div>
          )}

          <div
            data-testid="knowledge-doc-body"
            style={{ marginTop: 18, fontSize: 14.5, lineHeight: 1.65, color: "var(--ink, #2a2622)", maxWidth: 720 }}
          >
            {doc.content !== null ? (
              <SafeMarkdown body={doc.content} />
            ) : (
              (doc.sections ?? []).map((s, i) => (
                <p key={i} style={{ margin: "0 0 14px" }}>
                  {s}
                </p>
              ))
            )}
          </div>
        </>
      )}
    </div>
  );

  const emptyReader = editor.mode === "closed" && openRef === null && (
    <div style={{ ...card, textAlign: "center", padding: "46px 24px" }}>
      <div style={{ fontSize: 15, fontWeight: 720, marginBottom: 6 }}>
        {pages && pages.length > 0 ? "Pick a page" : "Your team's knowledge lives here"}
      </div>
      <p style={{ ...muted, fontSize: 13.5, lineHeight: 1.55, maxWidth: 460, margin: "0 auto" }}>
        {pages && pages.length > 0
          ? "Open a page from the left, search your whole knowledge base above, or start a new page."
          : "Write pages your agents ground every answer on — pricing, playbooks, FAQs, SOPs. Add your first page to get started."}
      </p>
      {!uploadsOff && (
        <button onClick={startNewPage} style={{ ...primaryBtn, marginTop: 16 }}>
          New page
        </button>
      )}
    </div>
  );

  // ---------------------------------------------------------------------------

  return (
    <div
      data-testid="knowledge-view"
      style={{ maxWidth: 1100, margin: "0 auto", padding: "32px 24px", fontFamily: "system-ui, sans-serif" }}
    >
      <style>{LAYOUT_CSS}</style>
      <div style={{ marginBottom: 18, display: "flex", alignItems: "flex-end", gap: 14, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 260 }}>
          <div style={{ fontSize: 12, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase", ...muted }}>
            Uplift knowledge
          </div>
          <h1 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.02em", margin: "6px 0 4px" }}>Knowledge</h1>
          <p style={{ ...muted, fontSize: 14, margin: 0 }}>
            Everything your agents draw on — written as pages, searchable in plain language, cited
            back to you.
          </p>
        </div>
        {!loading && !rollout && !error && !uploadsOff && (
          <button data-testid="knowledge-add-toggle" onClick={startNewPage} style={primaryBtn}>
            New page
          </button>
        )}
      </div>

      {loading && <Spinner testid="knowledge-loading" label="Loading your knowledge base..." />}

      {/* Two calm not-here-yet states, never an error wall (P1: unprovisioned ≠ rolling-out):
          404 = the live API image predates the route; 503 = the data plane isn't wired. */}
      {rollout && (
        <div
          data-testid={rollout === "rollout" ? "knowledge-rollout" : "knowledge-unprovisioned"}
          style={{ ...card, fontSize: 13.5 }}
        >
          <div style={{ fontWeight: 700, marginBottom: 4 }}>
            {rollout === "rollout"
              ? "Knowledge API is rolling out"
              : "Knowledge isn't switched on for this workspace yet"}
          </div>
          <p style={{ ...muted, lineHeight: 1.5 }}>
            {rollout === "rollout"
              ? "Your deployment doesn't serve the knowledge endpoint yet — refresh after the next API deploy. Nothing is wrong with your workspace."
              : "The knowledge data plane isn't connected on this deployment, so there's nothing to show or search here yet. It lights up the moment it's wired — nothing is wrong with your workspace, and refreshing won't change it until then."}
          </p>
          <button
            data-testid="knowledge-rollout-refresh"
            onClick={() => {
              void load();
              void loadPages();
            }}
            style={{ ...ghostBtn, marginTop: 10 }}
          >
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
          <button
            data-testid="knowledge-retry"
            onClick={() => {
              void load();
              void loadPages();
            }}
            style={{ ...ghostBtn, marginTop: 10 }}
          >
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
              placeholder="Ask your knowledge base — e.g. what's our discount policy"
              aria-label="Search your knowledge base"
              style={{ ...fieldStyle, flex: 1, minWidth: 0, padding: "10px 14px" }}
            />
            <button
              data-testid="knowledge-search-submit"
              type="submit"
              disabled={searching || !query.trim()}
              style={{
                ...primaryBtn,
                opacity: searching || !query.trim() ? 0.55 : 1,
                cursor: searching || !query.trim() ? "default" : "pointer",
              }}
            >
              {searching ? "Searching..." : "Search"}
            </button>
            {(search !== null || searchError) && (
              <button data-testid="knowledge-search-clear" type="button" onClick={clearSearch} style={ghostBtn}>
                Clear
              </button>
            )}
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
                // The P1 split: "search_error" = transient (retry-worthy); anything else —
                // incl. a missing reason_code from an older API image — is the embedder
                // warming-up story (the only degrade that existed before the split).
                search.reason_code === "search_error" ? (
                  <div
                    data-testid="knowledge-search-failed"
                    style={{ ...card, background: "var(--accent-soft, #f4f1ea)", fontSize: 13.5 }}
                  >
                    <div style={{ fontWeight: 700, marginBottom: 4 }}>Search hit a snag</div>
                    <p style={{ ...muted, lineHeight: 1.55, margin: "0 0 10px" }}>
                      Something on our side interrupted that search — your pages and documents are
                      unaffected. It&rsquo;s usually momentary.
                    </p>
                    <button
                      data-testid="knowledge-search-retry"
                      onClick={() => void runSearch(search.query || query)}
                      style={ghostBtn}
                    >
                      Try the search again
                    </button>
                  </div>
                ) : (
                  <div
                    data-testid="knowledge-search-unavailable"
                    style={{ ...card, background: "var(--accent-soft, #f4f1ea)", fontSize: 13.5 }}
                  >
                    <div style={{ fontWeight: 700, marginBottom: 4 }}>Search is warming up</div>
                    <p style={{ ...muted, lineHeight: 1.55, margin: 0 }}>
                      Semantic search needs the embedding model, which is being connected on our side.
                      Your pages below are already here; search lights up the moment it&rsquo;s ready.
                    </p>
                  </div>
                )
              ) : search.results.length === 0 ? (
                <div data-testid="knowledge-search-empty" style={{ ...card, fontSize: 13.5, ...muted }}>
                  No matches for &ldquo;{search.query}&rdquo; in your knowledge base.
                </div>
              ) : (
                <div data-testid="knowledge-results" style={{ ...card, paddingTop: 7, paddingBottom: 9 }}>
                  {search.results.map(resultRow)}
                  {search.next_offset != null && (
                    <div style={{ paddingTop: 10, borderTop: "1px solid var(--line-2, #efe9df)" }}>
                      <button
                        data-testid="knowledge-search-more"
                        onClick={() => void runSearch(search.query, search.next_offset!)}
                        disabled={searching}
                        style={{ ...ghostBtn, padding: "5px 14px", fontSize: 12.5, opacity: searching ? 0.55 : 1 }}
                      >
                        {searching ? "Loading..." : "Show more results"}
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {uploadsOff && (
            <div
              data-testid="knowledge-add-unavailable"
              style={{ ...card, background: "var(--accent-soft, #f4f1ea)", fontSize: 13.5, marginBottom: 16 }}
            >
              <div style={{ fontWeight: 700, marginBottom: 4 }}>Direct uploads aren&rsquo;t switched on yet</div>
              <p style={{ ...muted, lineHeight: 1.55, margin: 0 }}>
                Your page was not saved. Adding pages needs the ingestion plane, which
                isn&rsquo;t enabled on this deployment yet — your corpus still fills from connected
                sources, and search keeps working on what&rsquo;s already here.
              </p>
            </div>
          )}

          {addNote && (
            <div data-testid="knowledge-add-note" style={{ ...card, fontSize: 13.5, marginBottom: 16 }}>
              {addNote}
            </div>
          )}

          {/* the workspace: pages rail + reader/editor */}
          <div className="kn-grid">
            <aside className="kn-rail">
              <div>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                  <h2 style={{ fontSize: 13, fontWeight: 750, letterSpacing: ".04em", textTransform: "uppercase", margin: 0, ...muted }}>
                    Pages
                  </h2>
                  {pages !== null && (
                    <span style={{ fontSize: 11.5, ...muted }}>{pages.length}</span>
                  )}
                  {!uploadsOff && (
                    <button
                      data-testid="knowledge-page-new"
                      onClick={startNewPage}
                      title="New page"
                      style={{ ...ghostBtn, marginLeft: "auto", padding: "2px 10px", fontSize: 13 }}
                    >
                      +
                    </button>
                  )}
                </div>
                {pagesRollout && (
                  <p data-testid="knowledge-pages-rollout" style={{ fontSize: 12.5, lineHeight: 1.5, margin: "6px 0 0", ...muted }}>
                    Pages are rolling out — they&rsquo;ll appear here after the next API deploy.
                    Search and sources work today.
                  </p>
                )}
                {pagesError && (
                  <p data-testid="knowledge-pages-error" style={{ fontSize: 12.5, lineHeight: 1.5, margin: "6px 0 0", ...muted }}>
                    {pagesError}{" "}
                    <button onClick={() => void loadPages()} style={{ ...ghostBtn, padding: "2px 10px", fontSize: 12 }}>
                      Retry
                    </button>
                  </p>
                )}
                {pages !== null && pages.length === 0 && (
                  <p data-testid="knowledge-pages-empty" style={{ fontSize: 12.5, lineHeight: 1.5, margin: "6px 0 0", ...muted }}>
                    No pages yet — write your first one.
                  </p>
                )}
                {pages !== null && pages.length > 0 && (() => {
                  const needle = pageFilter.trim().toLowerCase();
                  const visible = needle
                    ? pages.filter((p) => `${p.title}\n${p.preview}`.toLowerCase().includes(needle))
                    : pages;
                  return (
                    <>
                      {pages.length > 4 && (
                        <input
                          data-testid="knowledge-pages-filter"
                          type="text"
                          value={pageFilter}
                          onChange={(e) => setPageFilter(e.target.value)}
                          placeholder="Filter pages..."
                          aria-label="Filter pages"
                          style={{ ...fieldStyle, padding: "6px 10px", fontSize: 12.5, margin: "2px 0 8px" }}
                        />
                      )}
                      {visible.length === 0 ? (
                        <p data-testid="knowledge-pages-nomatch" style={{ fontSize: 12.5, lineHeight: 1.5, margin: "6px 0 0", ...muted }}>
                          No pages match &ldquo;{pageFilter.trim()}&rdquo;.
                        </p>
                      ) : (
                        <nav data-testid="knowledge-pages" style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                          {visible.map(pageRow)}
                        </nav>
                      )}
                    </>
                  );
                })()}
              </div>

              <div>
                <h2 style={{ fontSize: 13, fontWeight: 750, letterSpacing: ".04em", textTransform: "uppercase", margin: "0 0 4px", ...muted }}>
                  Sources
                </h2>
                {data.total_documents === 0 ? (
                  <div data-testid="knowledge-empty" style={{ fontSize: 12.5, lineHeight: 1.55, padding: "6px 0", ...muted }}>
                    No documents yet. Add your first page (pricing, playbooks, FAQs), or connect
                    sources in Switchboard — your agents ground their answers on what lives here,
                    so an empty knowledge base means ungrounded answers.
                  </div>
                ) : (
                  <>
                    <div data-testid="knowledge-total" style={{ fontSize: 12, marginBottom: 4, ...muted }}>
                      {fmtCount(data.total_documents)} documents across {data.source_count}{" "}
                      {data.source_count === 1 ? "source" : "sources"}
                    </div>
                    <div>{data.sources.map(sourceRow)}</div>
                  </>
                )}
              </div>
            </aside>

            <main style={{ minWidth: 0 }}>
              {editorPane}
              {readerPane}
              {emptyReader}
            </main>
          </div>
        </>
      )}
    </div>
  );
}

export default KnowledgeView;
