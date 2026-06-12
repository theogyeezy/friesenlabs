// Agent Studio, wired to the control-plane API — the real-mode composer +
// playbook library over /studio/* (api/routes_studio.py). Follows the
// AgentsRoster/WorkflowsView conventions. Everything rendered here is honest:
//
//   * Playbooks come straight from GET /studio/playbooks (RLS-scoped rows) and
//     the starter library from GET /studio/templates (the 5 committed JSON
//     templates). Nothing is invented client-side.
//   * Playbooks are SPEC, NOT CODE: the editor edits a JSON definition that the
//     SERVER validates against shared/schemas/playbook.schema.json + the
//     owned-roster cross-checks before anything persists — a 422's
//     human-authored detail is surfaced verbatim as the validation feedback.
//     The client adds instant pre-flight checks (JSON parse, required keys,
//     the draft-only greenlight constant) but the server is the authority.
//   * Activate registers the playbook with the existing roster mechanism
//     behind the existing Greenlight/autonomy gates — side-effecting tools
//     stay draft-only no matter what a definition says, and the UI says so.
//   * No graph canvas yet (a future React Flow surface) — the JSON editor is
//     the honest MVP composer.
//   * A 404 from /studio means the live API image predates these routes (the
//     web can deploy ahead of the API): a calm "rolling out" state — NOT an
//     error wall. Raw transport strings never reach the DOM — every catch
//     routes through friendlyErrorMessage.
//
// This file is deliberately self-contained (its own thin /studio fetcher over
// the same auth primitives ApiClient uses) so the Studio lane only ADDS files;
// folding these calls into ApiClient proper is noted follow-up work.

import React from "react";
import { ApiError, configFromEnv, friendlyErrorMessage, isApiMock } from "./client";
import { fetchWithAuthRetry } from "../auth/core.js";
import { Spinner } from "./Spinner";

const { useState, useEffect, useCallback } = React;

// ---------------------------------------------------------------------------
// Wire types (mirror api/routes_studio.py response shapes)
// ---------------------------------------------------------------------------

export interface PlaybookDefinition {
  name: string;
  description?: string;
  trigger: { kind: "manual" | "schedule" | "event"; schedule?: string; event?: string };
  roster: Array<{ agent: string; tools?: string[] }>;
  autonomy: "L0" | "L1" | "L2" | "L3";
  greenlight: { side_effects: "always_ask"; note?: string };
}

export interface PlaybookRow {
  id: string;
  name: string;
  version: number;
  status: "draft" | "active";
  definition: PlaybookDefinition;
  template_id: string | null;
  created_by: string | null;
  created_at: string | null;
  updated_at: string | null;
  registered?: boolean;
  registration?: { agents: string[]; agent_id_tails: string[]; coordinator_id_tail: string | null };
  registration_reason?: string;
}

export interface StudioTemplate {
  template_id: string;
  summary: string;
  definition: PlaybookDefinition;
}

export interface PlaybookRunResult {
  ran: boolean;
  run?: { status: string; actions: unknown[] };
  run_reason?: string;
}

// ---------------------------------------------------------------------------
// Thin authed fetcher over the SAME auth primitives ApiClient uses. Never
// sends tenant_id (the trust rule); only the bearer token rides along.
// ---------------------------------------------------------------------------

const cfg = configFromEnv();
const baseURL = (cfg.baseURL ?? "").replace(/\/$/, "");

async function studioRequest<T>(method: string, path: string, body?: unknown): Promise<T> {
  const doFetch = async () => {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const token = cfg.getToken ? await cfg.getToken() : "";
    if (token) headers["Authorization"] = `Bearer ${token}`;
    return fetch(`${baseURL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  };
  const res = await fetchWithAuthRetry(doFetch, cfg.refreshAuth);
  if (res.status === 401 && cfg.onAuthRejected) cfg.onAuthRejected();
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = (await res.json()) as { detail?: string };
      if (j && typeof j.detail === "string") detail = j.detail;
    } catch {
      // non-JSON error body; keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// Instant pre-flight validation (the server is the authority; this only makes
// feedback immediate while typing). Mirrors the schema's coarse shape.
// ---------------------------------------------------------------------------

export function preflightValidate(text: string): { ok: boolean; message: string } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (e) {
    return { ok: false, message: `Not valid JSON: ${(e as Error).message}` };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return { ok: false, message: "The definition must be a JSON object." };
  }
  const d = parsed as Record<string, unknown>;
  for (const key of ["name", "trigger", "roster", "autonomy", "greenlight"]) {
    if (!(key in d)) return { ok: false, message: `Missing required field "${key}".` };
  }
  if (!["L0", "L1", "L2", "L3"].includes(d.autonomy as string)) {
    return { ok: false, message: 'autonomy must be one of "L0", "L1", "L2", "L3".' };
  }
  const gl = d.greenlight as Record<string, unknown> | null;
  if (!gl || gl.side_effects !== "always_ask") {
    return {
      ok: false,
      message: 'greenlight.side_effects must be "always_ask" — playbooks are draft-only by design.',
    };
  }
  if (!Array.isArray(d.roster) || d.roster.length === 0) {
    return { ok: false, message: "roster must list at least one agent." };
  }
  return { ok: true, message: "Looks valid. The server runs the full schema + roster checks on save." };
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
  padding: "8px 14px",
  borderRadius: 10,
  border: "1px solid var(--line, #e3ddd3)",
  background: "transparent",
  color: "var(--ink, #2a2622)",
  fontSize: 13,
  fontWeight: 650,
  cursor: "pointer",
};

const primaryBtn: React.CSSProperties = {
  ...ghostBtn,
  background: "var(--accent, #4f46e5)",
  borderColor: "var(--accent, #4f46e5)",
  color: "#fff",
};

const muted: React.CSSProperties = { color: "var(--ink-3, #8a8278)" };

const chip = (bg: string, fg: string): React.CSSProperties => ({
  display: "inline-flex",
  alignItems: "center",
  gap: 5,
  padding: "3px 10px",
  borderRadius: 999,
  fontSize: 11.5,
  fontWeight: 650,
  fontFamily: "var(--mono, ui-monospace, monospace)",
  background: bg,
  color: fg,
});

const chipActive = chip("rgba(63, 143, 92, .12)", "var(--green, #2e7d4f)");
const chipDraft = chip("rgba(138, 130, 120, .14)", "var(--ink-3, #6e675e)");

function triggerLabel(t: PlaybookDefinition["trigger"] | undefined): string {
  if (!t) return "?";
  if (t.kind === "schedule") return `schedule · ${t.schedule ?? "?"}`;
  if (t.kind === "event") return `event · ${t.event ?? "?"}`;
  return "manual";
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function StudioView() {
  const [playbooks, setPlaybooks] = useState<PlaybookRow[] | null>(null);
  const [templates, setTemplates] = useState<StudioTemplate[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rollout, setRollout] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [noticeOk, setNoticeOk] = useState(true); // false = error-styled notice

  // Editor state: editing an existing playbook (id) or composing a new one (null).
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editorText, setEditorText] = useState("");
  const [editorFeedback, setEditorFeedback] = useState<{ ok: boolean; message: string } | null>(null);
  const [serverFeedback, setServerFeedback] = useState<string | null>(null);

  const mock = isApiMock();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setRollout(false);
    setUnavailable(false);
    setNotice(null);
    try {
      const [pb, tp] = await Promise.all([
        studioRequest<{ playbooks: PlaybookRow[] }>("GET", "/studio/playbooks"),
        studioRequest<{ templates: StudioTemplate[] }>("GET", "/studio/templates"),
      ]);
      setPlaybooks(pb.playbooks);
      setTemplates(tp.templates);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setRollout(true); // the live API image predates /studio — calm, not an error wall
      } else if (e instanceof ApiError && e.status === 503) {
        setUnavailable(true); // data plane not wired on this deployment — calm, not an error wall
      } else {
        setError(friendlyErrorMessage(e, "Couldn't load the Studio. Please try again."));
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!mock) void load();
  }, [load, mock]);

  const openComposer = (definition?: PlaybookDefinition, id?: string) => {
    const starter: PlaybookDefinition = definition ?? {
      name: "My playbook",
      description: "",
      trigger: { kind: "manual" },
      roster: [{ agent: "scout", tools: ["search_rag", "read_crm"] }],
      autonomy: "L1",
      greenlight: { side_effects: "always_ask" },
    };
    setEditingId(id ?? null);
    setEditorText(JSON.stringify(starter, null, 2));
    setEditorFeedback(null);
    setServerFeedback(null);
    setEditorOpen(true);
  };

  const onEditorChange = (text: string) => {
    setEditorText(text);
    setEditorFeedback(preflightValidate(text));
    setServerFeedback(null);
  };

  const saveEditor = async () => {
    const pre = preflightValidate(editorText);
    setEditorFeedback(pre);
    if (!pre.ok) return;
    setBusy(true);
    setServerFeedback(null);
    try {
      const definition = JSON.parse(editorText) as PlaybookDefinition;
      if (editingId === null) {
        await studioRequest<PlaybookRow>("POST", "/studio/playbooks", { definition });
      } else {
        await studioRequest<PlaybookRow>("PUT", `/studio/playbooks/${editingId}`, { definition });
      }
      setEditorOpen(false);
      setNoticeOk(true);
      setNotice(editingId === null ? "Playbook created." : "Playbook updated.");
      await load();
    } catch (e) {
      // 422 carries the validator's human-authored reason — that IS the feedback.
      setServerFeedback(friendlyErrorMessage(e, "Couldn't save the playbook. Please try again."));
    } finally {
      setBusy(false);
    }
  };

  const act = async (fn: () => Promise<unknown>, doneNotice: string) => {
    setBusy(true);
    setNotice(null);
    try {
      await fn();
      setNoticeOk(true);
      setNotice(doneNotice);
      await load();
    } catch (e) {
      setNoticeOk(false);
      setNotice(friendlyErrorMessage(e, "That didn't work. Please try again."));
    } finally {
      setBusy(false);
    }
  };

  const instantiate = (templateId: string) =>
    act(
      () => studioRequest("POST", `/studio/templates/${encodeURIComponent(templateId)}/instantiate`),
      "Template added to your playbooks as a draft.",
    );

  const activate = async (id: string) => {
    setBusy(true);
    setNotice(null);
    try {
      const row = await studioRequest<PlaybookRow>("POST", `/studio/playbooks/${id}/activate`);
      setNoticeOk(true);
      if (row.registered === false) {
        const reason = row.registration_reason
          ? ` (${row.registration_reason})`
          : "";
        setNotice(
          `Playbook activated (record-only${reason}) — crew registration pending. Side effects still wait in Greenlight.`,
        );
      } else {
        setNotice(
          "Playbook activated — its crew is registered. Side effects still wait in Greenlight.",
        );
      }
      await load();
    } catch (e) {
      setNoticeOk(false);
      setNotice(friendlyErrorMessage(e, "That didn't work. Please try again."));
    } finally {
      setBusy(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Run now — POST /studio/playbooks/{id}/run — only available for active playbooks.
  // Returns {ran:true, run:{status, actions:[]}} when a registrar resolves, or
  // {ran:false, run_reason} for record-only state. The UI is honest: draft-only
  // guarantee means we never claim "sent" — actions are drafted, waiting in Greenlight.
  // ---------------------------------------------------------------------------

  const runNow = async (id: string) => {
    setBusy(true);
    setNotice(null);
    try {
      const result = await studioRequest<PlaybookRunResult>("POST", `/studio/playbooks/${id}/run`);
      setNoticeOk(true);
      if (result.ran) {
        const count = result.run?.actions?.length ?? 0;
        setNotice(
          `Run started — ${count} action${count === 1 ? "" : "s"} drafted, waiting in Greenlight.`,
        );
      } else {
        const reason = result.run_reason ? ` (${result.run_reason})` : "";
        setNotice(
          `Run recorded (record-only${reason}) — agent plane pending.`,
        );
      }
      await load();
    } catch (e) {
      setNoticeOk(false);
      setNotice(friendlyErrorMessage(e, "That didn't work. Please try again."));
    } finally {
      setBusy(false);
    }
  };

  const deactivate = (id: string) =>
    act(() => studioRequest("POST", `/studio/playbooks/${id}/deactivate`), "Playbook deactivated.");

  const remove = (id: string) =>
    act(() => studioRequest("DELETE", `/studio/playbooks/${id}`), "Playbook deleted.");

  // Mock/preview builds run fully offline — the Studio is a live-workspace
  // surface, so the honest move is to say so rather than fake a library.
  if (mock) {
    return (
      <div data-testid="studio-view" style={{ maxWidth: 980, margin: "0 auto", padding: "32px 24px" }}>
        <h1 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.02em" }}>Agent Studio</h1>
        <div style={{ ...card, marginTop: 14, fontSize: 13.5 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Studio connects to your live workspace</div>
          <p style={{ ...muted, lineHeight: 1.55 }}>
            This preview build runs offline, so the playbook library isn&rsquo;t available here.
            Sign in to your workspace to compose playbooks and activate agent crews.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div
      data-testid="studio-view"
      style={{ maxWidth: 980, margin: "0 auto", padding: "32px 24px", fontFamily: "system-ui, sans-serif" }}
    >
      <div style={{ marginBottom: 18, display: "flex", alignItems: "flex-end", gap: 12, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 280 }}>
          <div style={{ fontSize: 12, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase", ...muted }}>
            Agent Studio
          </div>
          <h1 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.02em", margin: "6px 0 4px" }}>Playbooks</h1>
          <p style={{ ...muted, fontSize: 14 }}>
            Named, versioned definitions your agent crew runs: a trigger, a roster, an autonomy
            level. Spec, not code — and every send or CRM write still waits for you in Greenlight.
          </p>
        </div>
        <button data-testid="studio-new" style={primaryBtn} disabled={busy} onClick={() => openComposer()}>
          New playbook
        </button>
      </div>

      {loading && <Spinner testid="studio-loading" label="Loading your playbooks..." />}

      {rollout && (
        <div data-testid="studio-rollout" style={{ ...card, fontSize: 13.5 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Agent Studio is rolling out</div>
          <p style={{ ...muted, lineHeight: 1.5 }}>
            Your deployment doesn&rsquo;t serve the Studio endpoints yet — refresh after the next
            API deploy. Nothing is wrong with your workspace.
          </p>
          <button data-testid="studio-rollout-refresh" onClick={() => void load()} style={{ ...ghostBtn, marginTop: 10 }}>
            Refresh
          </button>
        </div>
      )}

      {unavailable && (
        <div data-testid="studio-unavailable" style={{ ...card, fontSize: 13.5 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Agent Studio isn&rsquo;t available on this deployment yet</div>
          <p style={{ ...muted, lineHeight: 1.5 }}>
            The data plane isn&rsquo;t wired on this deployment. Nothing is wrong with your workspace —
            the Studio will be available once the data plane is configured.
          </p>
          <button data-testid="studio-unavailable-refresh" onClick={() => void load()} style={{ ...ghostBtn, marginTop: 10 }}>
            Refresh
          </button>
        </div>
      )}

      {error && (
        <div data-testid="studio-error" style={{ ...card, borderColor: "var(--rose, #b4413b)", fontSize: 13.5 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Something needs another try</div>
          <p style={{ ...muted, lineHeight: 1.5 }}>{error}</p>
          <button data-testid="studio-retry" onClick={() => void load()} style={{ ...ghostBtn, marginTop: 10 }}>
            Try again
          </button>
        </div>
      )}

      {notice && !loading && (
        <div
          data-testid="studio-notice"
          data-ok={noticeOk ? "1" : "0"}
          style={{
            ...card,
            marginBottom: 14,
            fontSize: 13,
            padding: "10px 16px",
            ...(noticeOk
              ? {}
              : { borderColor: "var(--rose, #b4413b)", color: "var(--rose, #b4413b)" }),
          }}
        >
          {notice}
        </div>
      )}

      {!loading && !error && !rollout && playbooks !== null && (
        <>
          {/* ----- the tenant's library ----- */}
          <section style={{ marginBottom: 26 }}>
            {playbooks.length === 0 && (
              <div data-testid="studio-empty" style={{ ...card, fontSize: 13.5 }}>
                <div style={{ fontWeight: 700, marginBottom: 4 }}>No playbooks yet</div>
                <p style={{ ...muted, lineHeight: 1.5 }}>
                  Start from a template below, or compose one from scratch with New playbook.
                </p>
              </div>
            )}
            <div style={{ display: "grid", gap: 10 }}>
              {playbooks.map((p) => (
                <div key={p.id} data-testid="playbook-row" style={{ ...card, display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                  <div style={{ flex: 1, minWidth: 220 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                      <b style={{ fontSize: 14.5 }}>{p.name}</b>
                      <span data-testid="playbook-status" data-status={p.status} style={p.status === "active" ? chipActive : chipDraft}>
                        {p.status}
                      </span>
                      <span style={{ ...muted, fontSize: 11.5 }}>v{p.version}</span>
                    </div>
                    <div style={{ ...muted, fontSize: 12.5, marginTop: 3 }}>
                      {triggerLabel(p.definition?.trigger)} · crew:{" "}
                      {(p.definition?.roster ?? []).map((r) => r.agent).join(", ") || "none"}
                      {p.template_id ? ` · from ${p.template_id}` : ""}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    {p.status === "draft" ? (
                      <>
                        <button data-testid="playbook-activate" style={primaryBtn} disabled={busy} onClick={() => void activate(p.id)}>
                          Activate
                        </button>
                        <button data-testid="playbook-edit" style={ghostBtn} disabled={busy} onClick={() => openComposer(p.definition, p.id)}>
                          Edit
                        </button>
                        <button data-testid="playbook-delete" style={ghostBtn} disabled={busy} onClick={() => void remove(p.id)}>
                          Delete
                        </button>
                      </>
                    ) : (
                      <>
                        <button data-testid="playbook-run" style={primaryBtn} disabled={busy} onClick={() => void runNow(p.id)}>
                          Run now
                        </button>
                        <button data-testid="playbook-deactivate" style={ghostBtn} disabled={busy} onClick={() => void deactivate(p.id)}>
                          Deactivate
                        </button>
                        <button data-testid="playbook-view" style={ghostBtn} disabled={busy} onClick={() => openComposer(p.definition, p.id)}>
                          View
                        </button>
                      </>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </section>

          {/* ----- the starter library ----- */}
          <section>
            <h2 style={{ fontSize: 17, fontWeight: 720, letterSpacing: "-.01em", marginBottom: 10 }}>
              Starter templates
            </h2>
            <div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))" }}>
              {(templates ?? []).map((t) => (
                <div key={t.template_id} data-testid="template-card" style={{ ...card, display: "flex", flexDirection: "column", gap: 8 }}>
                  <b style={{ fontSize: 13.5 }}>{t.definition.name}</b>
                  <p style={{ ...muted, fontSize: 12.5, lineHeight: 1.5, flex: 1 }}>{t.summary}</p>
                  <div style={{ ...muted, fontSize: 11.5 }}>
                    {triggerLabel(t.definition.trigger)} · crew: {t.definition.roster.map((r) => r.agent).join(", ")}
                  </div>
                  <button
                    data-testid="template-instantiate"
                    style={{ ...ghostBtn, alignSelf: "flex-start" }}
                    disabled={busy}
                    onClick={() => void instantiate(t.template_id)}
                  >
                    Use template
                  </button>
                </div>
              ))}
            </div>
          </section>
        </>
      )}

      {/* ----- the composer (JSON editor with validation feedback) ----- */}
      {editorOpen && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={editingId === null ? "New playbook" : "Edit playbook"}
          data-testid="studio-editor"
          style={{
            position: "fixed", inset: 0, background: "rgba(20, 16, 12, .4)", zIndex: 60,
            display: "grid", placeItems: "center", padding: 16,
          }}
          onKeyDown={(e) => { if (e.key === "Escape") setEditorOpen(false); }}
        >
          <div style={{ ...card, width: "min(720px, 100%)", maxHeight: "86vh", overflow: "auto", background: "var(--surface, #fff)" }}>
            <div style={{ display: "flex", alignItems: "center", marginBottom: 10 }}>
              <b style={{ flex: 1, fontSize: 15 }}>{editingId === null ? "New playbook" : "Edit playbook"}</b>
              <button data-testid="studio-editor-close" style={ghostBtn} onClick={() => setEditorOpen(false)}>
                Close
              </button>
            </div>
            <p style={{ ...muted, fontSize: 12.5, lineHeight: 1.5, marginBottom: 8 }}>
              The definition is validated server-side against the playbook schema and your crew&rsquo;s
              owned tool grants. greenlight.side_effects only accepts &ldquo;always_ask&rdquo; — playbooks
              can&rsquo;t skip Greenlight.
            </p>
            <textarea
              data-testid="studio-editor-text"
              value={editorText}
              onChange={(e) => onEditorChange(e.target.value)}
              spellCheck={false}
              style={{
                width: "100%", minHeight: 320, resize: "vertical", padding: 12,
                fontFamily: "var(--mono, ui-monospace, monospace)", fontSize: 12.5, lineHeight: 1.5,
                border: "1px solid var(--line, #e3ddd3)", borderRadius: 10, boxSizing: "border-box",
              }}
            />
            {editorFeedback && (
              <div
                data-testid="studio-editor-feedback"
                data-ok={editorFeedback.ok ? "1" : "0"}
                style={{
                  marginTop: 8, fontSize: 12.5, lineHeight: 1.5,
                  color: editorFeedback.ok ? "var(--green, #2e7d4f)" : "var(--rose, #b4413b)",
                }}
              >
                {editorFeedback.message}
              </div>
            )}
            {serverFeedback && (
              <div data-testid="studio-editor-server-feedback" style={{ marginTop: 8, fontSize: 12.5, lineHeight: 1.5, color: "var(--rose, #b4413b)" }}>
                {serverFeedback}
              </div>
            )}
            <div style={{ display: "flex", gap: 8, marginTop: 12, justifyContent: "flex-end" }}>
              <button data-testid="studio-editor-cancel" style={ghostBtn} disabled={busy} onClick={() => setEditorOpen(false)}>
                Cancel
              </button>
              <button data-testid="studio-editor-save" style={primaryBtn} disabled={busy} onClick={() => void saveEditor()}>
                {editingId === null ? "Create" : "Save"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default StudioView;
