// Tasks / reminders surface (CRM-depth #14), wired to the control-plane API via
// ApiClient. Everything here is honest:
//
//   * The list comes straight from GET /tasks (RLS-scoped, claims-bound server-side),
//     filtered by the selected scope (open / overdue / done / all / archived). The
//     open + overdue counts ride along for the scope-tab badges.
//   * Creating a task POSTs /tasks (a direct user write — never Greenlight, nothing
//     leaves the system). It may link an optional contact so the task surfaces on that
//     contact's drawer.
//   * Completing / reopening flips done via POST /tasks/{id}/complete|reopen; editing
//     PATCHes title/due; archive/unarchive soft-deletes (reversible). After any
//     mutation the list reloads so the displayed state always matches the server.
//   * A 404 from GET /tasks means the live API image predates this route (the web can
//     deploy ahead of the API): a calm "rolling out" state with a refresh, not an error.
//   * Raw transport strings never reach the DOM — every catch routes through
//     friendlyErrorMessage / honest per-status copy.

import React from "react";
import {
  ApiClient,
  ApiError,
  defaultClient,
  friendlyErrorMessage,
  type ContactRow,
  type ListTasksResponse,
  type TaskRow,
  type TaskScope,
} from "./client";
import { Spinner } from "./Spinner";

const { useState, useEffect, useCallback } = React;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDue(iso: string | null): string {
  if (!iso) return "No due date";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "No due date";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

// An ISO timestamp -> the yyyy-mm-dd value an <input type="date"> expects (or "").
function isoToDateInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toISOString().slice(0, 10);
}

// A yyyy-mm-dd date-input value -> an ISO timestamp at UTC midnight (or null when blank).
// Tasks are day-granular reminders, so midnight-UTC is a fine, stable anchor.
function dateInputToIso(value: string): string | null {
  const v = value.trim();
  if (!v) return null;
  const d = new Date(`${v}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString();
}

// ---------------------------------------------------------------------------
// Styles (house style: hairline cards on the soft surface palette)
// ---------------------------------------------------------------------------

const card: React.CSSProperties = {
  border: "1px solid var(--line, #e3ddd3)",
  background: "var(--surface, #fff)",
  borderRadius: 14,
  padding: "18px 20px",
  marginBottom: 16,
};

const primaryBtn: React.CSSProperties = {
  padding: "8px 16px",
  borderRadius: 10,
  border: "none",
  background: "var(--accent, #2a2622)",
  color: "#fff",
  fontSize: 13.5,
  fontWeight: 650,
  cursor: "pointer",
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

const input: React.CSSProperties = {
  padding: "8px 12px",
  borderRadius: 10,
  border: "1px solid var(--line, #e3ddd3)",
  background: "var(--surface, #fff)",
  color: "var(--ink, #2a2622)",
  fontSize: 13.5,
  fontFamily: "inherit",
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const SCOPES: { id: TaskScope; label: string }[] = [
  { id: "open", label: "Open" },
  { id: "overdue", label: "Overdue" },
  { id: "done", label: "Done" },
  { id: "all", label: "All" },
  { id: "archived", label: "Archived" },
];

const CONTACT_PICKER_LIMIT = 100;

export interface TasksViewProps {
  client?: ApiClient;
  /** First-run: a one-click "Load sample data" on the empty list. */
  onLoadSample?: () => void | Promise<void>;
}

export function TasksView({ client, onLoadSample }: TasksViewProps) {
  const api = client ?? defaultClient();
  const [data, setData] = useState<ListTasksResponse | null>(null);
  const [scope, setScope] = useState<TaskScope>("open");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rollout, setRollout] = useState(false);
  const [loadingSample, setLoadingSample] = useState(false);

  // Create form.
  const [formOpen, setFormOpen] = useState(false);
  const [formTitle, setFormTitle] = useState("");
  const [formDue, setFormDue] = useState("");
  const [formContact, setFormContact] = useState(""); // "" = no link
  const [formBusy, setFormBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // Inline edit (one task at a time).
  const [editId, setEditId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editDue, setEditDue] = useState("");
  const [editBusy, setEditBusy] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  // Per-row busy flag (complete/archive) so a row's controls disable while it mutates.
  const [busyId, setBusyId] = useState<string | null>(null);

  // Contacts for the optional link picker, loaded lazily when the form opens.
  const [contacts, setContacts] = useState<ContactRow[]>([]);
  const [contactsLoaded, setContactsLoaded] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setRollout(false);
    try {
      setData(await api.listTasks({ scope }));
    } catch (e) {
      setData(null);
      if (e instanceof ApiError && e.status === 404) {
        setRollout(true);
      } else {
        setError(friendlyErrorMessage(e, "Couldn't load your tasks. Please try again."));
      }
    } finally {
      setLoading(false);
    }
  }, [api, scope]);

  useEffect(() => {
    void load();
  }, [load]);

  const ensureContacts = useCallback(async () => {
    if (contactsLoaded) return;
    try {
      const res = await api.listContacts({ limit: CONTACT_PICKER_LIMIT });
      setContacts(res.contacts);
    } catch {
      // Optional link — a failed contact load never blocks task creation.
      setContacts([]);
    } finally {
      setContactsLoaded(true);
    }
  }, [api, contactsLoaded]);

  const openForm = useCallback(() => {
    setFormOpen(true);
    setFormTitle("");
    setFormDue("");
    setFormContact("");
    setFormError(null);
    void ensureContacts();
  }, [ensureContacts]);

  const submitForm = useCallback(async () => {
    const title = formTitle.trim();
    if (!title) {
      setFormError("Give the task a title.");
      return;
    }
    setFormBusy(true);
    setFormError(null);
    try {
      await api.createTask({
        title,
        due_at: dateInputToIso(formDue),
        contact_id: formContact || null,
      });
      setFormOpen(false);
      await load();
    } catch (e) {
      setFormError(friendlyErrorMessage(e, "Couldn't create that task. Please try again."));
    } finally {
      setFormBusy(false);
    }
  }, [api, formTitle, formDue, formContact, load]);

  const toggleDone = useCallback(
    async (task: TaskRow) => {
      setBusyId(task.id);
      try {
        await api.setTaskDone(task.id, !task.done);
        await load();
      } catch (e) {
        setError(friendlyErrorMessage(e, "Couldn't update that task. Please try again."));
      } finally {
        setBusyId(null);
      }
    },
    [api, load],
  );

  const setArchived = useCallback(
    async (task: TaskRow, archived: boolean) => {
      setBusyId(task.id);
      try {
        await api.setTaskArchived(task.id, archived);
        await load();
      } catch (e) {
        setError(friendlyErrorMessage(e, "Couldn't update that task. Please try again."));
      } finally {
        setBusyId(null);
      }
    },
    [api, load],
  );

  const beginEdit = useCallback((task: TaskRow) => {
    setEditId(task.id);
    setEditTitle(task.title);
    setEditDue(isoToDateInput(task.due_at));
    setEditError(null);
  }, []);

  const saveEdit = useCallback(async () => {
    if (!editId) return;
    const title = editTitle.trim();
    if (!title) {
      setEditError("Title can't be empty.");
      return;
    }
    setEditBusy(true);
    setEditError(null);
    try {
      await api.updateTask(editId, { title, due_at: dateInputToIso(editDue) ?? "" });
      setEditId(null);
      await load();
    } catch (e) {
      setEditError(friendlyErrorMessage(e, "Couldn't save those changes. Please try again."));
    } finally {
      setEditBusy(false);
    }
  }, [api, editId, editTitle, editDue, load]);

  const runLoadSample = useCallback(async () => {
    if (loadingSample || !onLoadSample) return;
    setLoadingSample(true);
    try {
      await onLoadSample();
      await load();
    } finally {
      setLoadingSample(false);
    }
  }, [loadingSample, onLoadSample, load]);

  const tasks = data?.tasks ?? [];

  return (
    <div data-testid="tasks-view" style={{ maxWidth: 760 }}>
      {/* Header: title + new-task button */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 20, fontWeight: 720, color: "var(--ink, #2a2622)" }}>Tasks</h2>
          <div style={{ marginTop: 2, fontSize: 13, color: "var(--ink-3, #8a8278)" }}>
            Follow-ups and reminders across your CRM.
          </div>
        </div>
        <button data-testid="new-task-btn" onClick={openForm} style={primaryBtn}>
          + New task
        </button>
      </div>

      {/* Scope tabs with open/overdue counts */}
      <div style={{ display: "flex", gap: 6, marginBottom: 16, flexWrap: "wrap" }}>
        {SCOPES.map((s) => {
          const active = scope === s.id;
          const badge =
            s.id === "open" && data ? data.open_count
              : s.id === "overdue" && data ? data.overdue_count
              : null;
          return (
            <button
              key={s.id}
              data-testid={`tasks-scope-${s.id}`}
              onClick={() => setScope(s.id)}
              aria-pressed={active}
              style={{
                ...ghostBtn,
                padding: "6px 12px",
                fontSize: 13,
                background: active ? "var(--accent, #2a2622)" : "transparent",
                color: active ? "#fff" : "var(--ink, #2a2622)",
                borderColor: active ? "var(--accent, #2a2622)" : "var(--line, #e3ddd3)",
              }}
            >
              {s.label}
              {badge !== null && badge > 0 ? (
                <span
                  data-testid={`tasks-${s.id}-count`}
                  style={{ marginLeft: 6, fontFamily: "var(--mono)", fontSize: 11, opacity: 0.85 }}
                >
                  {badge}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>

      {/* Create form */}
      {formOpen ? (
        <div data-testid="task-form" style={card}>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <input
              data-testid="task-form-title"
              placeholder="What needs doing? (e.g. Call back Tuesday)"
              value={formTitle}
              onChange={(e) => setFormTitle(e.target.value)}
              style={input}
              autoFocus
            />
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12, color: "var(--ink-3, #8a8278)" }}>
                Due date
                <input
                  data-testid="task-form-due"
                  type="date"
                  value={formDue}
                  onChange={(e) => setFormDue(e.target.value)}
                  style={input}
                />
              </label>
              <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12, color: "var(--ink-3, #8a8278)" }}>
                Link a contact (optional)
                <select
                  data-testid="task-form-contact"
                  value={formContact}
                  onChange={(e) => setFormContact(e.target.value)}
                  style={input}
                >
                  <option value="">— none —</option>
                  {contacts.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name ?? c.email ?? c.id}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {formError ? (
              <div data-testid="task-form-error" style={{ color: "var(--danger, #b4453a)", fontSize: 13 }}>
                {formError}
              </div>
            ) : null}
            <div style={{ display: "flex", gap: 8 }}>
              <button data-testid="task-form-submit" onClick={() => void submitForm()} disabled={formBusy} style={primaryBtn}>
                {formBusy ? "Saving…" : "Create task"}
              </button>
              <button data-testid="task-form-cancel" onClick={() => setFormOpen(false)} disabled={formBusy} style={ghostBtn}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {/* Body: loading / rollout / error / empty / list */}
      {loading ? (
        <div data-testid="tasks-loading" style={{ ...card, textAlign: "center" }}>
          <Spinner /> <span style={{ marginLeft: 8, color: "var(--ink-3, #8a8278)" }}>Loading tasks…</span>
        </div>
      ) : rollout ? (
        <div data-testid="tasks-rollout" style={{ ...card, color: "var(--ink, #2a2622)", fontSize: 13.5 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Tasks API is rolling out</div>
          <div style={{ color: "var(--ink-3, #8a8278)" }}>
            This deployment's API doesn't serve tasks yet. It'll appear here once the rollout completes.
          </div>
          <button data-testid="tasks-rollout-refresh" onClick={() => void load()} style={{ ...ghostBtn, marginTop: 10 }}>
            Refresh
          </button>
        </div>
      ) : error ? (
        <div data-testid="tasks-error" style={{ ...card, color: "var(--danger, #b4453a)", fontSize: 13.5 }}>
          {error}
          <button data-testid="tasks-retry" onClick={() => void load()} style={{ ...ghostBtn, marginTop: 10 }}>
            Try again
          </button>
        </div>
      ) : tasks.length === 0 ? (
        <div data-testid="tasks-empty" style={{ ...card, textAlign: "center", color: "var(--ink-3, #8a8278)" }}>
          <div style={{ fontWeight: 650, color: "var(--ink, #2a2622)", marginBottom: 4 }}>
            {scope === "archived" ? "No archived tasks" : scope === "done" ? "No completed tasks yet" : "No tasks here"}
          </div>
          <div style={{ fontSize: 13 }}>
            {scope === "open" || scope === "all"
              ? "Create a follow-up to keep deals moving."
              : "Nothing to show for this view."}
          </div>
          {(scope === "open" || scope === "all") && onLoadSample ? (
            <button
              data-testid="tasks-load-sample"
              onClick={() => void runLoadSample()}
              disabled={loadingSample}
              style={{ ...ghostBtn, marginTop: 10 }}
            >
              {loadingSample ? "Loading…" : "Load sample data"}
            </button>
          ) : null}
        </div>
      ) : (
        <div data-testid="tasks-list">
          {tasks.map((t) => {
            const editing = editId === t.id;
            const rowBusy = busyId === t.id;
            const isArchived = t.archived_at !== null;
            return (
              <div
                key={t.id}
                data-testid="task-row"
                data-task-id={t.id}
                style={{
                  ...card,
                  marginBottom: 10,
                  padding: "14px 16px",
                  display: "flex",
                  flexDirection: "column",
                  gap: editing ? 10 : 4,
                }}
              >
                <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
                  {/* Complete toggle */}
                  <button
                    data-testid="task-complete-toggle"
                    onClick={() => void toggleDone(t)}
                    disabled={rowBusy || isArchived}
                    aria-pressed={t.done}
                    aria-label={t.done ? "Mark not done" : "Mark done"}
                    title={t.done ? "Mark not done" : "Mark done"}
                    style={{
                      flexShrink: 0,
                      width: 20,
                      height: 20,
                      marginTop: 1,
                      borderRadius: 6,
                      border: "1.5px solid var(--line, #c9c1b4)",
                      background: t.done ? "var(--accent, #2a2622)" : "transparent",
                      color: "#fff",
                      cursor: rowBusy || isArchived ? "default" : "pointer",
                      fontSize: 12,
                      lineHeight: 1,
                    }}
                  >
                    {t.done ? "✓" : ""}
                  </button>

                  <div style={{ flex: 1, minWidth: 0 }}>
                    {editing ? (
                      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                        <input
                          data-testid="task-edit-title"
                          value={editTitle}
                          onChange={(e) => setEditTitle(e.target.value)}
                          style={input}
                          autoFocus
                        />
                        <input
                          data-testid="task-edit-due"
                          type="date"
                          value={editDue}
                          onChange={(e) => setEditDue(e.target.value)}
                          style={{ ...input, maxWidth: 200 }}
                        />
                        {editError ? (
                          <div data-testid="task-edit-error" style={{ color: "var(--danger, #b4453a)", fontSize: 13 }}>
                            {editError}
                          </div>
                        ) : null}
                        <div style={{ display: "flex", gap: 8 }}>
                          <button data-testid="task-edit-save" onClick={() => void saveEdit()} disabled={editBusy} style={primaryBtn}>
                            {editBusy ? "Saving…" : "Save"}
                          </button>
                          <button data-testid="task-edit-cancel" onClick={() => setEditId(null)} disabled={editBusy} style={ghostBtn}>
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <div
                          data-testid="task-title"
                          style={{
                            fontSize: 14.5,
                            fontWeight: 600,
                            color: "var(--ink, #2a2622)",
                            textDecoration: t.done ? "line-through" : "none",
                            opacity: t.done ? 0.6 : 1,
                          }}
                        >
                          {t.title}
                        </div>
                        <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 4, flexWrap: "wrap" }}>
                          <span
                            data-testid="task-due"
                            style={{
                              fontSize: 12.5,
                              color: t.overdue ? "var(--danger, #b4453a)" : "var(--ink-3, #8a8278)",
                              fontWeight: t.overdue ? 650 : 400,
                            }}
                          >
                            {formatDue(t.due_at)}
                          </span>
                          {t.overdue ? (
                            <span
                              data-testid="task-overdue-flag"
                              style={{
                                fontSize: 11,
                                fontWeight: 700,
                                color: "var(--danger, #b4453a)",
                                border: "1px solid var(--danger, #b4453a)",
                                borderRadius: 6,
                                padding: "1px 6px",
                              }}
                            >
                              OVERDUE
                            </span>
                          ) : null}
                          {t.contact_name ? (
                            <span data-testid="task-link-contact" style={{ fontSize: 12.5, color: "var(--ink-3, #8a8278)" }}>
                              · {t.contact_name}
                            </span>
                          ) : null}
                          {t.deal_title ? (
                            <span data-testid="task-link-deal" style={{ fontSize: 12.5, color: "var(--ink-3, #8a8278)" }}>
                              · {t.deal_title}
                            </span>
                          ) : null}
                        </div>
                      </>
                    )}
                  </div>

                  {/* Row actions */}
                  {!editing ? (
                    <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                      {!isArchived ? (
                        <button
                          data-testid="task-edit-btn"
                          onClick={() => beginEdit(t)}
                          disabled={rowBusy}
                          style={{ ...ghostBtn, padding: "5px 10px", fontSize: 12.5 }}
                        >
                          Edit
                        </button>
                      ) : null}
                      {isArchived ? (
                        <button
                          data-testid="task-restore-btn"
                          onClick={() => void setArchived(t, false)}
                          disabled={rowBusy}
                          style={{ ...ghostBtn, padding: "5px 10px", fontSize: 12.5 }}
                        >
                          Restore
                        </button>
                      ) : (
                        <button
                          data-testid="task-archive-btn"
                          onClick={() => void setArchived(t, true)}
                          disabled={rowBusy}
                          style={{ ...ghostBtn, padding: "5px 10px", fontSize: 12.5 }}
                        >
                          Archive
                        </button>
                      )}
                    </div>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default TasksView;
