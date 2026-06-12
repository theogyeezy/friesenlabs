// Contacts & Companies directory, wired to the control-plane API via ApiClient
// — the real-mode counterpart of the FLStore Contacts prototype
// (src/screens/contacts.tsx, mock mode only). Follows the PipelineBoard
// conventions exactly. Everything rendered here is honest:
//
//   * The contact rows come straight from GET /contacts (RLS-scoped,
//     claims-bound server-side): name, email, phone, the joined company name
//     and the last-activity timestamp. Nothing is invented client-side.
//   * Search rides the server's ?q= param (name/email; companies: name/domain)
//     — ILIKE with bind params and metacharacter escaping server-side; the
//     input mirrors the server's 200-char cap so a long paste can't 422.
//   * Clicking a row opens a detail drawer fed by GET /contacts/{id} (contact
//     + recent activities + the company's OPEN deals — each deal links toward
//     the Pipeline board, where stage moves go through Greenlight).
//   * A Companies toggle switches to GET /companies (contact + open-deal
//     counts) with its own drawer (GET /companies/{id}: contacts + deals).
//   * READ-ONLY by design: no create/edit controls exist this cycle — CRM
//     writes arrive with a later update_contact tool through the gate, so the
//     UI promises nothing it can't keep.
//   * A 404 from the list means the live API image predates these routes (the
//     web can deploy ahead of the API): that renders a calm "rolling out"
//     state with a refresh affordance — NOT an error wall.
//   * Raw transport strings ("API <code>", server detail dumps) never reach
//     the DOM — every catch routes through friendlyErrorMessage / honest
//     per-status copy.

import React from "react";
import {
  ApiClient,
  ApiError,
  defaultClient,
  friendlyErrorMessage,
  type CompanyDeal,
  type CompanyDetailResponse,
  type CompanyRow,
  type ContactDetailResponse,
  type ContactRow,
  type CreateContactBody,
  type EditContactBody,
} from "./client";
import { Spinner } from "./Spinner";

const { useState, useEffect, useCallback, useRef, useReducer, useMemo } = React;

// Mirrors api/contacts_routes.py MAX_Q_LEN — the input enforces it so typing
// can never produce a 422.
const MAX_Q_LEN = 200;
const PAGE_SIZE = 50;
// How many companies to pull into the form's company picker. The picker filters
// this page client-side; company is optional, so a partial page is fine.
const COMPANY_PICKER_LIMIT = 100;

// Mirrors api/deals_routes.py STAGE_LABELS for the open-deal chips (display
// only — unknown stages fall back to the raw value, never dropped).
const STAGE_LABELS: Record<string, string> = {
  new: "New",
  qualified: "Qualified",
  proposal: "Proposal",
  negotiation: "Negotiation",
  closed_won: "Closed won",
  closed_lost: "Closed lost",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatMoney(v: number | null | undefined, currency?: string | null): string {
  if (v === null || v === undefined) return "—";
  const cur = currency === null || currency === undefined || currency === "USD" ? "$" : `${currency} `;
  if (Math.abs(v) >= 1000) return `${cur}${(v / 1000).toFixed(1)}k`;
  return `${cur}${v.toFixed(0)}`;
}

function formatWhen(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function stageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? stage;
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

const rowStyle: React.CSSProperties = {
  border: "1px solid var(--line, #e3ddd3)",
  background: "var(--surface, #fff)",
  borderRadius: 12,
  padding: "12px 16px",
  marginBottom: 10,
  cursor: "pointer",
  textAlign: "left",
  width: "100%",
  display: "block",
  fontFamily: "inherit",
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

const sectionLabel: React.CSSProperties = {
  margin: "22px 0 8px",
  fontSize: 12,
  fontWeight: 600,
  color: "var(--ink-3, #8a8278)",
};

const muted: React.CSSProperties = { color: "var(--ink-3, #8a8278)" };

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

type Tab = "people" | "companies";

interface ListState<T> {
  rows: T[];
  hasMore: boolean;
  loading: boolean;
  error: string | null;
  rollout: boolean;
  loaded: boolean; // a successful load happened (gates the empty state)
}

function emptyList<T>(): ListState<T> {
  return { rows: [], hasMore: false, loading: false, error: null, rollout: false, loaded: false };
}

// Contact form field shape (create + edit share the same form).
interface ContactFormFields {
  name: string;
  email: string;
  phone: string;
  company_id: string;
}

function emptyForm(): ContactFormFields {
  return { name: "", email: "", phone: "", company_id: "" };
}

export interface ContactsDirectoryProps {
  client?: ApiClient;
  /** Navigate to the Pipeline board (the shell passes navTo("crm")). Without
   * it the deal links point at the ?view=pipeline seam. */
  onOpenPipeline?: () => void;
  /** First-run: a one-click "Load sample data" on the empty state. The shell
   * passes a handler that loads the demo fixture into this tenant; without it
   * the empty state stays explanatory-only (no CTA). */
  onLoadSample?: () => void | Promise<void>;
}

export function ContactsDirectory({ client, onOpenPipeline, onLoadSample }: ContactsDirectoryProps) {
  const api = client ?? defaultClient();
  const [tab, setTab] = useState<Tab>("people");
  const [query, setQuery] = useState("");
  const [loadingSample, setLoadingSample] = useState(false);

  const runLoadSample = useCallback(async () => {
    if (loadingSample || !onLoadSample) return;
    setLoadingSample(true);
    try {
      await onLoadSample();
    } finally {
      setLoadingSample(false);
    }
  }, [loadingSample, onLoadSample]);

  const [people, setPeople] = useState<ListState<ContactRow>>(emptyList);
  const [companies, setCompanies] = useState<ListState<CompanyRow>>(emptyList);

  // Detail drawer state — one drawer; which entity it shows follows the click.
  const [drawer, setDrawer] = useState<
    | { kind: "contact"; id: string }
    | { kind: "company"; id: string }
    | null
  >(null);
  const [contactDetail, setContactDetail] = useState<ContactDetailResponse | null>(null);
  const [companyDetail, setCompanyDetail] = useState<CompanyDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  // A monotonically growing id per load so a stale response can never clobber
  // a newer one (search keystrokes race their fetches).
  const loadSeq = useRef(0);

  // Create/edit form state. `formMode` is null when the form is closed.
  const [formMode, setFormMode] = useState<"create" | { kind: "edit"; id: string } | null>(null);
  const [formFields, setFormFields] = useState<ContactFormFields>(emptyForm);
  const [formBusy, setFormBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [, forceListRefresh] = useReducer((n: number) => n + 1, 0);

  // Company picker (typeahead) for the contact form. Companies load lazily when
  // the form opens; the query is the visible text, the chosen id lives in the
  // form fields. Company is optional — a failed load never blocks a save.
  const [formCompanies, setFormCompanies] = useState<CompanyRow[]>([]);
  const [formCompaniesLoaded, setFormCompaniesLoaded] = useState(false);
  const [companyQuery, setCompanyQuery] = useState("");
  const [companyMenuOpen, setCompanyMenuOpen] = useState(false);

  // "Show archived" view: lists the archived rows; each opens with a Restore action.
  const [showArchived, setShowArchived] = useState(false);

  const loadPeople = useCallback(
    async (q: string, offset: number, append: boolean) => {
      const seq = ++loadSeq.current;
      setPeople((s) => ({ ...s, loading: true, error: null, rollout: false }));
      try {
        const res = await api.listContacts({ q: q || undefined, limit: PAGE_SIZE, offset, archived: showArchived });
        if (seq !== loadSeq.current) return;
        setPeople((s) => ({
          rows: append ? [...s.rows, ...res.contacts] : res.contacts,
          hasMore: res.has_more,
          loading: false,
          error: null,
          rollout: false,
          loaded: true,
        }));
      } catch (e) {
        if (seq !== loadSeq.current) return;
        if (e instanceof ApiError && e.status === 404) {
          setPeople({ ...emptyList<ContactRow>(), rollout: true });
        } else {
          setPeople((s) => ({
            ...s,
            loading: false,
            error: friendlyErrorMessage(e, "Couldn't load your contacts. Please try again."),
          }));
        }
      }
    },
    [api, showArchived],
  );

  const loadCompanies = useCallback(
    async (q: string, offset: number, append: boolean) => {
      const seq = ++loadSeq.current;
      setCompanies((s) => ({ ...s, loading: true, error: null, rollout: false }));
      try {
        const res = await api.listCompanies({ q: q || undefined, limit: PAGE_SIZE, offset, archived: showArchived });
        if (seq !== loadSeq.current) return;
        setCompanies((s) => ({
          rows: append ? [...s.rows, ...res.companies] : res.companies,
          hasMore: res.has_more,
          loading: false,
          error: null,
          rollout: false,
          loaded: true,
        }));
      } catch (e) {
        if (seq !== loadSeq.current) return;
        if (e instanceof ApiError && e.status === 404) {
          setCompanies({ ...emptyList<CompanyRow>(), rollout: true });
        } else {
          setCompanies((s) => ({
            ...s,
            loading: false,
            error: friendlyErrorMessage(e, "Couldn't load your companies. Please try again."),
          }));
        }
      }
    },
    [api, showArchived],
  );

  const reload = useCallback(() => {
    if (tab === "people") void loadPeople(query.trim(), 0, false);
    else void loadCompanies(query.trim(), 0, false);
  }, [tab, query, loadPeople, loadCompanies, showArchived]);

  // Initial load + tab switches + debounced search. One effect owns all three
  // so there is exactly one trigger path (the debounce only matters while
  // typing; tab flips re-run it immediately via the deps).
  useEffect(() => {
    const t = window.setTimeout(reload, query === "" ? 0 : 250);
    return () => window.clearTimeout(t);
  }, [reload, query]);

  const openContact = useCallback(
    async (id: string) => {
      setDrawer({ kind: "contact", id });
      setContactDetail(null);
      setCompanyDetail(null);
      setDetailError(null);
      setDetailLoading(true);
      try {
        setContactDetail(await api.getContact(id));
      } catch (e) {
        setDetailError(
          e instanceof ApiError && e.status === 404
            ? "That contact can't be found anymore. Refresh the directory and try again."
            : friendlyErrorMessage(e, "Couldn't load this contact. Please try again."),
        );
      } finally {
        setDetailLoading(false);
      }
    },
    [api],
  );

  const openCompany = useCallback(
    async (id: string) => {
      setDrawer({ kind: "company", id });
      setContactDetail(null);
      setCompanyDetail(null);
      setDetailError(null);
      setDetailLoading(true);
      try {
        setCompanyDetail(await api.getCompany(id));
      } catch (e) {
        setDetailError(
          e instanceof ApiError && e.status === 404
            ? "That company can't be found anymore. Refresh the directory and try again."
            : friendlyErrorMessage(e, "Couldn't load this company. Please try again."),
        );
      } finally {
        setDetailLoading(false);
      }
    },
    [api],
  );

  const closeDrawer = useCallback(() => {
    setDrawer(null);
    setContactDetail(null);
    setCompanyDetail(null);
    setDetailError(null);
  }, []);

  // Pull a page of companies the first time the form opens; company is optional,
  // so a failed load just leaves the picker empty (never an error).
  const loadCompaniesForPicker = useCallback(async () => {
    if (formCompaniesLoaded) return;
    try {
      const res = await api.listCompanies({ limit: COMPANY_PICKER_LIMIT });
      setFormCompanies(res.companies);
      setFormCompaniesLoaded(true);
    } catch {
      // Company is optional — swallow and leave the picker empty.
    }
  }, [api, formCompaniesLoaded]);

  const openCreateForm = useCallback(() => {
    setFormMode("create");
    setFormFields(emptyForm());
    setFormError(null);
    setCompanyQuery("");
    setCompanyMenuOpen(false);
  }, []);

  const openEditForm = useCallback((c: ContactRow) => {
    setFormMode({ kind: "edit", id: c.id });
    setFormFields({
      name: c.name ?? "",
      email: c.email ?? "",
      phone: c.phone ?? "",
      company_id: c.company_id ?? "",
    });
    setFormError(null);
    // Prefer the row's own company name; otherwise the picker effect resolves it.
    setCompanyQuery(c.company_name ?? "");
    setCompanyMenuOpen(false);
  }, []);

  const closeForm = useCallback(() => {
    setFormMode(null);
    setFormError(null);
    setFormBusy(false);
    setCompanyQuery("");
    setCompanyMenuOpen(false);
  }, []);

  // Lazy-load the companies page whenever the form is open.
  useEffect(() => {
    if (formMode !== null) void loadCompaniesForPicker();
  }, [formMode, loadCompaniesForPicker]);

  // When editing a contact that already has a company but no resolved name,
  // fill the input once the page is available.
  useEffect(() => {
    if (!formFields.company_id || companyQuery) return;
    const co = formCompanies.find((x) => x.id === formFields.company_id);
    if (co) setCompanyQuery(co.name ?? co.domain ?? "Selected company");
  }, [formCompanies, formFields.company_id, companyQuery]);

  // ---- Company create/edit (a parallel form; the contact form is untouched) ----
  const [companyMode, setCompanyMode] = useState<"create" | { kind: "edit"; id: string } | null>(null);
  const [companyFields, setCompanyFields] = useState<{ name: string; domain: string }>({ name: "", domain: "" });
  const [companyBusy, setCompanyBusy] = useState(false);
  const [companyFormError, setCompanyFormError] = useState<string | null>(null);

  const openCompanyCreate = useCallback(() => {
    setCompanyMode("create");
    setCompanyFields({ name: "", domain: "" });
    setCompanyFormError(null);
  }, []);
  const openCompanyEdit = useCallback((co: { id: string; name: string | null; domain: string | null }) => {
    setCompanyMode({ kind: "edit", id: co.id });
    setCompanyFields({ name: co.name ?? "", domain: co.domain ?? "" });
    setCompanyFormError(null);
  }, []);
  const closeCompanyForm = useCallback(() => {
    setCompanyMode(null); setCompanyBusy(false); setCompanyFormError(null);
  }, []);

  const submitCompanyForm = useCallback(async () => {
    if (!companyMode) return;
    const name = companyFields.name.trim();
    if (!name) { setCompanyFormError("Name is required."); return; }
    setCompanyBusy(true); setCompanyFormError(null);
    try {
      if (companyMode === "create") {
        await api.createCompany({ name, domain: companyFields.domain.trim() || undefined });
      } else {
        await api.editCompany(companyMode.id, { name, domain: companyFields.domain.trim() });
        if (drawer?.kind === "company" && drawer.id === companyMode.id) void openCompany(companyMode.id);
      }
      closeCompanyForm();
      forceListRefresh();
      if (tab === "companies") void loadCompanies(query.trim(), 0, false);
    } catch (e) {
      setCompanyFormError(friendlyErrorMessage(e, "Couldn't save the company. Please try again."));
    } finally { setCompanyBusy(false); }
  }, [api, companyMode, companyFields, drawer, openCompany, closeCompanyForm, forceListRefresh, tab, loadCompanies, query]);

  // ---- Log a note on the open contact (direct write; refreshes the activity list) ----
  const [noteText, setNoteText] = useState("");
  const [noteKind, setNoteKind] = useState("note"); // note / call / email / task
  const [noteBusy, setNoteBusy] = useState(false);
  const logNote = useCallback(async (contactId: string) => {
    const body = noteText.trim();
    if (!body) return;
    setNoteBusy(true);
    try {
      await api.logActivity("contacts", contactId, { kind: noteKind, body });
      setNoteText("");
      void openContact(contactId);
    } catch { /* keep the text so the user can retry */ } finally { setNoteBusy(false); }
  }, [api, noteText, noteKind, openContact]);

  // ---- Archive / restore (soft) an entity, then close the drawer + refresh ----
  const [archiveBusy, setArchiveBusy] = useState(false);
  const setEntityArchived = useCallback(async (entity: "contacts" | "companies", id: string, archived: boolean) => {
    setArchiveBusy(true);
    try {
      await api.setArchived(entity, id, archived);
      closeDrawer();
      forceListRefresh();
      if (entity === "contacts") void loadPeople(query.trim(), 0, false);
      else void loadCompanies(query.trim(), 0, false);
    } catch { /* the list reload surfaces the true state */ } finally { setArchiveBusy(false); }
  }, [api, closeDrawer, forceListRefresh, loadPeople, loadCompanies, query]);
  const archive = useCallback((entity: "contacts" | "companies", id: string) => setEntityArchived(entity, id, true), [setEntityArchived]);
  const restore = useCallback((entity: "contacts" | "companies", id: string) => setEntityArchived(entity, id, false), [setEntityArchived]);

  // Matches for the picker menu: filter the loaded page by name/domain, capped
  // so the dropdown stays short.
  const companyMatches = useMemo(() => {
    const q = companyQuery.trim().toLowerCase();
    const base = q
      ? formCompanies.filter(
          (co) =>
            (co.name ?? "").toLowerCase().includes(q) ||
            (co.domain ?? "").toLowerCase().includes(q),
        )
      : formCompanies;
    return base.slice(0, 8);
  }, [formCompanies, companyQuery]);

  const submitForm = useCallback(async () => {
    if (!formMode) return;
    const name = formFields.name.trim();
    if (!name) {
      setFormError("Name is required.");
      return;
    }
    setFormBusy(true);
    setFormError(null);
    try {
      if (formMode === "create") {
        const body: CreateContactBody = {
          name,
          email: formFields.email.trim() || undefined,
          phone: formFields.phone.trim() || undefined,
          company_id: formFields.company_id.trim() || undefined,
        };
        await api.createContact(body);
      } else {
        const body: EditContactBody = {
          name,
          email: formFields.email.trim() || undefined,
          phone: formFields.phone.trim() || undefined,
          company_id: formFields.company_id.trim() || undefined,
        };
        await api.updateContact(formMode.id, body);
      }
      closeForm();
      forceListRefresh();
      // Also refresh the current tab list.
      if (tab === "people") void loadPeople(query.trim(), 0, false);
    } catch (e) {
      setFormError(friendlyErrorMessage(e, "Couldn't save. Please try again."));
    } finally {
      setFormBusy(false);
    }
  }, [api, formMode, formFields, closeForm, forceListRefresh, tab, loadPeople, query]);

  // Esc closes the drawer (house pattern for slide-overs).
  useEffect(() => {
    if (drawer === null) return;
    const k = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeDrawer();
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [drawer, closeDrawer]);

  const active = tab === "people" ? people : companies;

  const tabBtn = (id: Tab, label: string): React.ReactElement => (
    <button
      data-testid={`dir-tab-${id}`}
      aria-pressed={tab === id}
      onClick={() => setTab(id)}
      style={{
        ...ghostBtn,
        padding: "7px 16px",
        borderRadius: 999,
        background: tab === id ? "var(--accent, #2a2622)" : "transparent",
        color: tab === id ? "#fff" : "var(--ink, #2a2622)",
        borderColor: tab === id ? "var(--accent, #2a2622)" : "var(--line, #e3ddd3)",
      }}
    >
      {label}
    </button>
  );

  // The deal link toward the Pipeline board — a shell callback when the
  // directory is mounted inside the app, the ?view=pipeline seam otherwise.
  const dealLink = (d: CompanyDeal, i: number): React.ReactElement => {
    const inner = (
      <>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
          <span style={{ fontSize: 13, fontWeight: 700, color: "var(--ink, #2a2622)" }}>
            {d.title ?? "Untitled deal"}
          </span>
          <span style={{ fontSize: 13, fontWeight: 700 }}>{formatMoney(d.amount, d.currency)}</span>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 10, marginTop: 4 }}>
          <span
            style={{ fontSize: 11.5, fontWeight: 650, padding: "2px 10px", borderRadius: 999, background: "var(--accent-soft, #f4f1ea)", color: "var(--ink, #2a2622)" }}
          >
            {stageLabel(d.stage)}
          </span>
          <span style={{ fontSize: 11.5, ...muted }}>View in Pipeline →</span>
        </div>
      </>
    );
    const style: React.CSSProperties = {
      ...rowStyle,
      padding: "10px 12px",
      marginBottom: 8,
      textDecoration: "none",
    };
    return onOpenPipeline ? (
      <button
        key={d.id ?? i}
        data-testid="company-deal"
        data-deal-id={d.id}
        onClick={onOpenPipeline}
        style={style}
      >
        {inner}
      </button>
    ) : (
      <a
        key={d.id ?? i}
        data-testid="company-deal"
        data-deal-id={d.id}
        href="/?view=pipeline"
        style={{ ...style, display: "block", color: "inherit" }}
      >
        {inner}
      </a>
    );
  };

  const contactRows = (rows: ContactRow[], testid: string): React.ReactElement[] =>
    rows.map((c) => (
      <button
        key={c.id}
        data-testid={testid}
        data-contact-id={c.id}
        onClick={() => void openContact(c.id)}
        style={rowStyle}
      >
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
          <span style={{ fontSize: 13.5, fontWeight: 700, color: "var(--ink, #2a2622)" }}>
            {c.name ?? "Unnamed contact"}
          </span>
          {c.company_name && <span style={{ fontSize: 12.5, ...muted }}>{c.company_name}</span>}
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginTop: 4, flexWrap: "wrap" }}>
          <span style={{ fontSize: 12.5, ...muted }}>
            {c.email ?? "no email"}
            {c.phone ? ` · ${c.phone}` : ""}
          </span>
          <span style={{ fontSize: 12, ...muted }}>
            {c.last_activity_at ? `last activity ${formatWhen(c.last_activity_at)}` : "no activity yet"}
          </span>
        </div>
      </button>
    ));

  return (
    <div
      data-testid="contacts-directory"
      style={{ maxWidth: 860, margin: "0 auto", padding: "32px 24px", fontFamily: "system-ui, sans-serif" }}
    >
      <div style={{ marginBottom: 18 }}>
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
          <div>
            <div style={{ fontSize: 12, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase", ...muted }}>
              Uplift CRM
            </div>
            <h1 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.02em", margin: "6px 0 4px" }}>Contacts</h1>
          </div>
          {tab === "people" && (
            <button
              data-testid="add-contact-btn"
              onClick={openCreateForm}
              style={{
                padding: "9px 16px",
                borderRadius: 10,
                border: "none",
                background: "var(--accent, #2a2622)",
                color: "#fff",
                fontSize: 13.5,
                fontWeight: 700,
                cursor: "pointer",
                fontFamily: "inherit",
                marginTop: 4,
              }}
            >
              + Add contact
            </button>
          )}
          {tab === "companies" && (
            <button
              data-testid="add-company-btn"
              onClick={openCompanyCreate}
              style={{
                padding: "9px 16px",
                borderRadius: 10,
                border: "none",
                background: "var(--accent, #2a2622)",
                color: "#fff",
                fontSize: 13.5,
                fontWeight: 700,
                cursor: "pointer",
                fontFamily: "inherit",
                marginTop: 4,
              }}
            >
              + Add company
            </button>
          )}
        </div>
        <p style={{ ...muted, fontSize: 14 }}>
          Everyone your business talks to — synced from your CRM or added directly. Their open
          deals live on the Pipeline board.
        </p>
      </div>

      {/* toggle + search */}
      <div style={{ display: "flex", gap: 10, marginBottom: 16, flexWrap: "wrap", alignItems: "center" }}>
        {tabBtn("people", "People")}
        {tabBtn("companies", "Companies")}
        <input
          data-testid="dir-search"
          type="search"
          placeholder={tab === "people" ? "Search by name or email…" : "Search by name or domain…"}
          value={query}
          maxLength={MAX_Q_LEN}
          onChange={(e) => setQuery(e.target.value)}
          style={{
            flex: 1,
            minWidth: 220,
            borderRadius: 10,
            border: "1px solid var(--line, #e3ddd3)",
            padding: "9px 12px",
            fontSize: 13.5,
            fontFamily: "inherit",
            background: "var(--surface, #fff)",
            color: "var(--ink, #2a2622)",
          }}
        />
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, ...muted, cursor: "pointer" }}>
          <input
            type="checkbox"
            data-testid="dir-show-archived"
            checked={showArchived}
            onChange={(e) => setShowArchived(e.target.checked)}
          />
          Show archived
        </label>
      </div>

      {active.loading && active.rows.length === 0 && (
        <Spinner testid="dir-loading" label={tab === "people" ? "Loading your contacts..." : "Loading your companies..."} />
      )}

      {/* The live API image may predate /contacts: a calm rollout note, not an error wall. */}
      {active.rollout && (
        <div data-testid="dir-rollout" style={{ ...card, color: "var(--ink, #2a2622)", fontSize: 13.5 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Contacts API is rolling out</div>
          <p style={{ ...muted, lineHeight: 1.5 }}>
            Your deployment doesn&rsquo;t serve the contacts endpoints yet — refresh after the next
            API deploy. Nothing is wrong with your data.
          </p>
          <button data-testid="dir-rollout-refresh" onClick={reload} style={{ ...ghostBtn, marginTop: 10 }}>
            Refresh
          </button>
        </div>
      )}

      {active.error && (
        <div
          data-testid="dir-error"
          style={{ ...card, borderColor: "var(--rose, #b4413b)", color: "var(--ink, #2a2622)", fontSize: 13.5 }}
        >
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Something needs another try</div>
          <p style={{ ...muted, lineHeight: 1.5 }}>{active.error}</p>
          <button data-testid="dir-retry" onClick={reload} style={{ ...ghostBtn, marginTop: 10 }}>
            Try again
          </button>
        </div>
      )}

      {!active.loading && !active.error && !active.rollout && active.loaded && active.rows.length === 0 && (
        <div data-testid="dir-empty" style={{ ...card, textAlign: "center", ...muted }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: "var(--ink, #2a2622)" }}>
            {query.trim()
              ? "No matches"
              : tab === "people"
                ? "No contacts yet"
                : "No companies yet"}
          </div>
          <p style={{ fontSize: 13, marginTop: 4 }}>
            {query.trim()
              ? "Nothing in your workspace matches that search."
              : "When your CRM syncs into your workspace, everyone your business talks to appears here. New here? Load a realistic sample to explore."}
          </p>
          {/* First-run CTA: only on a genuinely empty workspace (not a no-match). */}
          {!query.trim() && onLoadSample && (
            <button
              type="button"
              data-testid="dir-empty-load-sample"
              onClick={() => void runLoadSample()}
              disabled={loadingSample}
              aria-busy={loadingSample}
              style={{
                marginTop: 16,
                appearance: "none",
                border: "1px solid transparent",
                borderRadius: 10,
                padding: "9px 16px",
                fontSize: 13,
                fontWeight: 700,
                fontFamily: "inherit",
                cursor: loadingSample ? "default" : "pointer",
                background: "var(--accent, #b4593b)",
                color: "var(--accent-ink-on, #fff)",
                opacity: loadingSample ? 0.7 : 1,
              }}
            >
              {loadingSample ? "Loading…" : "Load sample data"}
            </button>
          )}
        </div>
      )}

      {!active.error && !active.rollout && active.rows.length > 0 && (
        <div data-testid="dir-list">
          {tab === "people" ? (
            contactRows(people.rows, "contact-row")
          ) : (
            companies.rows.map((co) => (
              <button
                key={co.id}
                data-testid="company-row"
                data-company-id={co.id}
                onClick={() => void openCompany(co.id)}
                style={rowStyle}
              >
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                  <span style={{ fontSize: 13.5, fontWeight: 700, color: "var(--ink, #2a2622)" }}>
                    {co.name ?? "Unnamed company"}
                  </span>
                  {co.domain && <span style={{ fontSize: 12.5, ...muted }}>{co.domain}</span>}
                </div>
                <div style={{ display: "flex", gap: 14, marginTop: 4, fontSize: 12.5, ...muted }}>
                  <span data-testid="company-contact-count">
                    {co.contact_count} {co.contact_count === 1 ? "contact" : "contacts"}
                  </span>
                  <span data-testid="company-deal-count">
                    {co.open_deal_count} open {co.open_deal_count === 1 ? "deal" : "deals"}
                  </span>
                </div>
              </button>
            ))
          )}
          {active.hasMore && (
            <button
              data-testid="dir-load-more"
              disabled={active.loading}
              onClick={() =>
                tab === "people"
                  ? void loadPeople(query.trim(), people.rows.length, true)
                  : void loadCompanies(query.trim(), companies.rows.length, true)
              }
              style={{ ...ghostBtn, width: "100%", marginTop: 4, opacity: active.loading ? 0.6 : 1 }}
            >
              {active.loading ? "Loading..." : "Load more"}
            </button>
          )}
        </div>
      )}

      {/* ----------------------------------------------------------------- drawer */}
      {drawer !== null && (
        <>
          <div
            data-testid="drawer-scrim"
            onClick={closeDrawer}
            style={{ position: "fixed", inset: 0, background: "rgba(20, 16, 12, .28)", zIndex: 50 }}
          />
          <div
            data-testid="dir-drawer"
            role="dialog"
            aria-label={drawer.kind === "contact" ? "Contact detail" : "Company detail"}
            style={{
              position: "fixed",
              top: 0,
              right: 0,
              bottom: 0,
              width: "min(440px, 92vw)",
              background: "var(--surface, #fff)",
              borderLeft: "1px solid var(--line, #e3ddd3)",
              boxShadow: "-12px 0 40px rgba(20,16,12,.12)",
              zIndex: 51,
              padding: "24px 24px 32px",
              overflowY: "auto",
              fontFamily: "system-ui, sans-serif",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
              <div style={{ fontSize: 12, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase", ...muted }}>
                {drawer.kind === "contact" ? "Contact" : "Company"}
              </div>
              <button data-testid="drawer-close" onClick={closeDrawer} style={{ ...ghostBtn, padding: "5px 12px" }}>
                Close
              </button>
            </div>

            {detailLoading && <Spinner testid="drawer-loading" label="Loading..." />}

            {detailError && (
              <div data-testid="drawer-error" style={{ ...card, borderColor: "var(--rose, #b4413b)", fontSize: 13.5 }}>
                <div style={{ fontWeight: 700, marginBottom: 4 }}>Something needs another try</div>
                <p style={{ ...muted, lineHeight: 1.5 }}>{detailError}</p>
              </div>
            )}

            {/* ------------------------------------------------ contact detail */}
            {drawer.kind === "contact" && contactDetail !== null && (
              <>
                <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 10, marginBottom: 4 }}>
                  <h2 data-testid="drawer-title" style={{ fontSize: 20, fontWeight: 760, letterSpacing: "-.02em", margin: 0 }}>
                    {contactDetail.contact.name ?? "Unnamed contact"}
                  </h2>
                  <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                    <button
                      data-testid="edit-contact-btn"
                      onClick={() => openEditForm(contactDetail.contact)}
                      style={{ ...ghostBtn, padding: "5px 12px", fontSize: 12.5 }}
                    >
                      Edit
                    </button>
                    {showArchived ? (
                      <button
                        data-testid="restore-contact-btn"
                        disabled={archiveBusy}
                        onClick={() => void restore("contacts", contactDetail.contact.id)}
                        style={{ ...ghostBtn, padding: "5px 12px", fontSize: 12.5, opacity: archiveBusy ? 0.6 : 1 }}
                      >
                        Restore
                      </button>
                    ) : (
                      <button
                        data-testid="archive-contact-btn"
                        disabled={archiveBusy}
                        onClick={() => void archive("contacts", contactDetail.contact.id)}
                        style={{ ...ghostBtn, padding: "5px 12px", fontSize: 12.5, opacity: archiveBusy ? 0.6 : 1 }}
                      >
                        Archive
                      </button>
                    )}
                  </div>
                </div>
                <div style={{ fontSize: 13, ...muted }}>
                  {contactDetail.contact.company_name ?? "No company"}
                </div>

                {(contactDetail.contact_deals?.length ?? 0) > 0 && (
                  <>
                    <div style={sectionLabel}>This contact&rsquo;s deals</div>
                    {contactDetail.contact_deals!.map(dealLink)}
                  </>
                )}
                <div style={{ fontSize: 13, marginTop: 8, lineHeight: 1.6 }}>
                  <div data-testid="drawer-email">{contactDetail.contact.email ?? "no email"}</div>
                  {contactDetail.contact.phone && (
                    <div data-testid="drawer-phone">{contactDetail.contact.phone}</div>
                  )}
                  {contactDetail.contact.last_activity_at && (
                    <div style={muted}>
                      last activity {formatWhen(contactDetail.contact.last_activity_at)}
                    </div>
                  )}
                </div>

                <div style={sectionLabel}>
                  Open deals at {contactDetail.contact.company_name ?? "their company"}
                </div>
                {contactDetail.company_deals.length === 0 ? (
                  <div data-testid="company-deals-empty" style={{ fontSize: 13, ...muted }}>
                    No open deals with this company.
                  </div>
                ) : (
                  contactDetail.company_deals.map(dealLink)
                )}

                <div style={sectionLabel}>Recent activity</div>
                {contactDetail.activities.length === 0 ? (
                  <div data-testid="activities-empty" style={{ fontSize: 13, ...muted }}>
                    No activity logged with this contact yet.
                  </div>
                ) : (
                  contactDetail.activities.map((a, i) => (
                    <div
                      key={a.id ?? i}
                      data-testid="activity-item"
                      style={{ borderTop: i === 0 ? "none" : "1px solid var(--line-2, #efe9df)", padding: "10px 2px" }}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
                        <span style={{ fontSize: 11.5, fontWeight: 700, letterSpacing: ".05em", textTransform: "uppercase", ...muted }}>
                          {a.kind ?? "note"}
                        </span>
                        <span style={{ fontSize: 11.5, ...muted }}>{formatWhen(a.occurred_at)}</span>
                      </div>
                      {a.body && (
                        <p style={{ fontSize: 13, color: "var(--ink, #2a2622)", lineHeight: 1.5, margin: "4px 0 0" }}>{a.body}</p>
                      )}
                    </div>
                  ))
                )}

                {/* Log a note/call/email/task directly on this contact (direct write, not an agent send). */}
                <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
                  <select
                    data-testid="note-kind"
                    value={noteKind}
                    onChange={(e) => setNoteKind(e.target.value)}
                    style={{ padding: "8px 8px", borderRadius: 8, border: "1px solid var(--line, #e3ddd3)", fontSize: 13, fontFamily: "inherit", background: "var(--surface, #fff)" }}
                  >
                    <option value="note">Note</option>
                    <option value="call">Call</option>
                    <option value="email">Email</option>
                    <option value="task">Task</option>
                  </select>
                  <input
                    data-testid="note-input"
                    value={noteText}
                    onChange={(e) => setNoteText(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") void logNote(contactDetail.contact.id); }}
                    placeholder="Log a note, call, email, or task…"
                    style={{ flex: 1, padding: "8px 10px", borderRadius: 8, border: "1px solid var(--line, #e3ddd3)", fontSize: 13, fontFamily: "inherit" }}
                  />
                  <button
                    data-testid="note-submit"
                    disabled={noteBusy || !noteText.trim()}
                    onClick={() => void logNote(contactDetail.contact.id)}
                    style={{ ...ghostBtn, padding: "8px 14px", fontSize: 13, opacity: noteBusy || !noteText.trim() ? 0.6 : 1 }}
                  >
                    {noteBusy ? "Logging…" : "Log"}
                  </button>
                </div>
              </>
            )}

            {/* ------------------------------------------------ company detail */}
            {drawer.kind === "company" && companyDetail !== null && (
              <>
                <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 10, marginBottom: 4 }}>
                  <h2 data-testid="drawer-title" style={{ fontSize: 20, fontWeight: 760, letterSpacing: "-.02em", margin: 0 }}>
                    {companyDetail.company.name ?? "Unnamed company"}
                  </h2>
                  <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                    <button
                      data-testid="edit-company-btn"
                      onClick={() => openCompanyEdit(companyDetail.company)}
                      style={{ ...ghostBtn, padding: "5px 12px", fontSize: 12.5 }}
                    >
                      Edit
                    </button>
                    {showArchived ? (
                      <button
                        data-testid="restore-company-btn"
                        disabled={archiveBusy}
                        onClick={() => void restore("companies", companyDetail.company.id)}
                        style={{ ...ghostBtn, padding: "5px 12px", fontSize: 12.5, opacity: archiveBusy ? 0.6 : 1 }}
                      >
                        Restore
                      </button>
                    ) : (
                    <button
                      data-testid="archive-company-btn"
                      disabled={archiveBusy}
                      onClick={() => void archive("companies", companyDetail.company.id)}
                      style={{ ...ghostBtn, padding: "5px 12px", fontSize: 12.5, opacity: archiveBusy ? 0.6 : 1 }}
                    >
                      Archive
                    </button>
                    )}
                  </div>
                </div>
                {companyDetail.company.domain && (
                  <div style={{ fontSize: 13, ...muted }}>{companyDetail.company.domain}</div>
                )}

                <div style={sectionLabel}>Open deals</div>
                {companyDetail.deals.length === 0 ? (
                  <div data-testid="company-deals-empty" style={{ fontSize: 13, ...muted }}>
                    No open deals with this company.
                  </div>
                ) : (
                  companyDetail.deals.map(dealLink)
                )}

                <div style={sectionLabel}>People</div>
                {companyDetail.contacts.length === 0 ? (
                  <div data-testid="company-people-empty" style={{ fontSize: 13, ...muted }}>
                    No contacts at this company yet.
                  </div>
                ) : (
                  contactRows(companyDetail.contacts, "company-contact-row")
                )}
              </>
            )}
          </div>
        </>
      )}

      {/* ----------------------------------------------------------------- create/edit form modal */}
      {formMode !== null && (
        <>
          <div
            data-testid="form-scrim"
            onClick={closeForm}
            style={{ position: "fixed", inset: 0, background: "rgba(20, 16, 12, .28)", zIndex: 60 }}
          />
          <div
            data-testid="contact-form"
            role="dialog"
            aria-label={formMode === "create" ? "Add contact" : "Edit contact"}
            style={{
              position: "fixed",
              top: "50%",
              left: "50%",
              transform: "translate(-50%, -50%)",
              width: "min(440px, 92vw)",
              background: "var(--surface, #fff)",
              border: "1px solid var(--line, #e3ddd3)",
              borderRadius: 16,
              boxShadow: "0 12px 48px rgba(20,16,12,.18)",
              zIndex: 61,
              padding: "28px 28px 32px",
              fontFamily: "system-ui, sans-serif",
            }}
          >
            <h2 style={{ fontSize: 18, fontWeight: 760, letterSpacing: "-.02em", margin: "0 0 20px" }}>
              {formMode === "create" ? "Add contact" : "Edit contact"}
            </h2>

            {(["name", "email", "phone"] as const).map((field) => (
              <div key={field} style={{ marginBottom: 14 }}>
                <label
                  htmlFor={`contact-form-${field}`}
                  style={{ display: "block", fontSize: 12, fontWeight: 600, ...muted, marginBottom: 4 }}
                >
                  {field === "name" ? "Name *" : field.charAt(0).toUpperCase() + field.slice(1)}
                </label>
                <input
                  id={`contact-form-${field}`}
                  data-testid={`contact-form-${field}`}
                  type={field === "email" ? "email" : "text"}
                  value={formFields[field]}
                  onChange={(e) => setFormFields((f) => ({ ...f, [field]: e.target.value }))}
                  disabled={formBusy}
                  style={{
                    width: "100%",
                    boxSizing: "border-box",
                    borderRadius: 10,
                    border: "1px solid var(--line, #e3ddd3)",
                    padding: "9px 12px",
                    fontSize: 13.5,
                    fontFamily: "inherit",
                    background: "var(--surface, #fff)",
                    color: "var(--ink, #2a2622)",
                  }}
                />
              </div>
            ))}

            <div style={{ marginBottom: 14, position: "relative" }}>
              <label
                htmlFor="contact-form-company"
                style={{ display: "block", fontSize: 12, fontWeight: 600, ...muted, marginBottom: 4 }}
              >
                Company
              </label>
              <input
                id="contact-form-company"
                data-testid="contact-form-company"
                type="text"
                role="combobox"
                aria-expanded={companyMenuOpen}
                aria-autocomplete="list"
                autoComplete="off"
                value={companyQuery}
                onChange={(e) => {
                  setCompanyQuery(e.target.value);
                  setCompanyMenuOpen(true);
                  // Editing the text clears any prior selection until one is picked.
                  setFormFields((f) => ({ ...f, company_id: "" }));
                }}
                onFocus={() => setCompanyMenuOpen(true)}
                disabled={formBusy}
                placeholder={formCompaniesLoaded ? "Search a company by name or domain…" : "Loading companies…"}
                style={{
                  width: "100%",
                  boxSizing: "border-box",
                  borderRadius: 10,
                  border: "1px solid var(--line, #e3ddd3)",
                  padding: "9px 12px",
                  fontSize: 13.5,
                  fontFamily: "inherit",
                  background: "var(--surface, #fff)",
                  color: "var(--ink, #2a2622)",
                }}
              />
              {formFields.company_id !== "" && (
                <button
                  type="button"
                  data-testid="contact-form-company-clear"
                  onClick={() => {
                    setFormFields((f) => ({ ...f, company_id: "" }));
                    setCompanyQuery("");
                    setCompanyMenuOpen(false);
                  }}
                  aria-label="Clear company"
                  style={{
                    position: "absolute",
                    right: 8,
                    top: 30,
                    border: "none",
                    background: "transparent",
                    color: "var(--ink-3, #8a8278)",
                    fontSize: 16,
                    lineHeight: 1,
                    cursor: "pointer",
                    fontFamily: "inherit",
                  }}
                >
                  ×
                </button>
              )}
              {companyMenuOpen && companyMatches.length > 0 && (
                <div
                  role="listbox"
                  data-testid="contact-form-company-menu"
                  style={{
                    position: "absolute",
                    left: 0,
                    right: 0,
                    top: "100%",
                    marginTop: 4,
                    zIndex: 2,
                    maxHeight: 220,
                    overflowY: "auto",
                    background: "var(--surface, #fff)",
                    border: "1px solid var(--line, #e3ddd3)",
                    borderRadius: 10,
                    boxShadow: "0 8px 28px rgba(20,16,12,.14)",
                  }}
                >
                  {companyMatches.map((co) => (
                    <button
                      key={co.id}
                      type="button"
                      role="option"
                      aria-selected={formFields.company_id === co.id}
                      data-testid="contact-form-company-option"
                      data-company-id={co.id}
                      onClick={() => {
                        setFormFields((f) => ({ ...f, company_id: co.id }));
                        setCompanyQuery(co.name ?? co.domain ?? "Selected company");
                        setCompanyMenuOpen(false);
                      }}
                      style={{
                        display: "block",
                        width: "100%",
                        textAlign: "left",
                        padding: "8px 12px",
                        border: "none",
                        borderBottom: "1px solid var(--line-2, #efe9df)",
                        background: "transparent",
                        cursor: "pointer",
                        fontFamily: "inherit",
                      }}
                    >
                      <div style={{ fontSize: 13, fontWeight: 650, color: "var(--ink, #2a2622)" }}>
                        {co.name ?? "Unnamed company"}
                      </div>
                      {co.domain && (
                        <div style={{ fontSize: 12, ...muted }}>{co.domain}</div>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {formError && (
              <div
                data-testid="contact-form-error"
                style={{ fontSize: 13, lineHeight: 1.5, marginBottom: 14, padding: "10px 12px", borderRadius: 10, color: "var(--rose, #b4413b)", background: "oklch(0.97 0.02 18)" }}
              >
                {formError}
              </div>
            )}

            <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
              <button
                data-testid="contact-form-cancel"
                onClick={closeForm}
                disabled={formBusy}
                style={{ ...ghostBtn }}
              >
                Cancel
              </button>
              <button
                data-testid="contact-form-submit"
                onClick={() => void submitForm()}
                disabled={formBusy || !formFields.name.trim()}
                style={{
                  padding: "8px 16px",
                  borderRadius: 10,
                  border: "none",
                  background: "var(--accent, #2a2622)",
                  color: "#fff",
                  fontSize: 13.5,
                  fontWeight: 650,
                  cursor: formBusy ? "default" : "pointer",
                  opacity: formBusy || !formFields.name.trim() ? 0.6 : 1,
                }}
              >
                {formBusy ? "Saving…" : formMode === "create" ? "Add contact" : "Save changes"}
              </button>
            </div>
          </div>
        </>
      )}

      {/* ----------------------------------------------------------------- company create/edit modal */}
      {companyMode !== null && (
        <>
          <div
            data-testid="company-form-scrim"
            onClick={closeCompanyForm}
            style={{ position: "fixed", inset: 0, background: "rgba(20, 16, 12, .28)", zIndex: 60 }}
          />
          <div
            data-testid="company-form"
            role="dialog"
            aria-label={companyMode === "create" ? "Add company" : "Edit company"}
            style={{
              position: "fixed", top: "50%", left: "50%", transform: "translate(-50%, -50%)",
              width: "min(420px, calc(100vw - 32px))", background: "var(--surface, #fff)",
              border: "1px solid var(--line, #e3ddd3)", borderRadius: 16, padding: 22, zIndex: 61,
              boxShadow: "0 24px 60px rgba(20,16,12,.22)",
            }}
          >
            <h2 style={{ fontSize: 18, fontWeight: 760, margin: "0 0 14px" }}>
              {companyMode === "create" ? "Add company" : "Edit company"}
            </h2>
            <label style={{ display: "block", fontSize: 12.5, fontWeight: 600, ...muted, marginBottom: 4 }}>Name</label>
            <input
              data-testid="company-form-name"
              value={companyFields.name}
              onChange={(e) => setCompanyFields((f) => ({ ...f, name: e.target.value }))}
              placeholder="Company name"
              style={{ width: "100%", padding: "9px 11px", borderRadius: 9, border: "1px solid var(--line, #e3ddd3)", fontSize: 14, fontFamily: "inherit", marginBottom: 12 }}
            />
            <label style={{ display: "block", fontSize: 12.5, fontWeight: 600, ...muted, marginBottom: 4 }}>Domain</label>
            <input
              data-testid="company-form-domain"
              value={companyFields.domain}
              onChange={(e) => setCompanyFields((f) => ({ ...f, domain: e.target.value }))}
              placeholder="acme.com"
              style={{ width: "100%", padding: "9px 11px", borderRadius: 9, border: "1px solid var(--line, #e3ddd3)", fontSize: 14, fontFamily: "inherit" }}
            />
            {companyFormError && (
              <div data-testid="company-form-error" style={{ color: "var(--rose, #b4413b)", fontSize: 13, marginTop: 10 }}>{companyFormError}</div>
            )}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 18 }}>
              <button data-testid="company-form-cancel" onClick={closeCompanyForm} style={{ ...ghostBtn, padding: "9px 16px", fontSize: 13.5 }}>Cancel</button>
              <button
                data-testid="company-form-submit"
                disabled={companyBusy || !companyFields.name.trim()}
                onClick={() => void submitCompanyForm()}
                style={{
                  padding: "9px 16px", borderRadius: 10, border: "none", background: "var(--accent, #2a2622)",
                  color: "#fff", fontSize: 13.5, fontWeight: 700, cursor: "pointer", fontFamily: "inherit",
                  opacity: companyBusy || !companyFields.name.trim() ? 0.6 : 1,
                }}
              >
                {companyBusy ? "Saving…" : companyMode === "create" ? "Add company" : "Save changes"}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export default ContactsDirectory;
