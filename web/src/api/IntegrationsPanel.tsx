// Integrations (Switchboard), wired to the control-plane API via ApiClient.
//
// The real-mode counterpart of the FLStore prototype screen
// (src/screens/integrations.tsx, mock mode only). Everything rendered here is
// honest: the list, the connected/not-connected/unknown badges and the
// secrets/sync configuration notes come straight from GET /integrations —
// nothing is invented client-side and there are no fake successes anywhere.
//
// A 404 from GET /integrations means the live API image predates this route
// (the web can deploy ahead of the API): a calm "rolling out" state with a
// refresh affordance — NOT a red error wall. Non-404 errors keep the existing
// red error + retry.
//
// Connect flow (sync-kind connectors) — two honest paths:
//   1. "Connect with {Provider}" (OAuth): a full-page browser redirect to
//      GET /integrations/{name}/oauth/start. The provider handles login +
//      consent and redirects back to /oauth/callback, which lands the credential
//      in the vault; the existing connection-status display then reflects
//      "Connected" with no special client handling. This path shows only when the
//      connector advertises OAuth — feature-detected from the integrations list
//      (an optional `oauth_available` flag); until the API ships that flag, the
//      connectors known to support it ("hubspot", "gohighlevel") are treated as
//      OAuth-capable (graceful degrade). The pattern is generic, so stripe/
//      pipedrive reuse it the moment the flag turns on for them. A connector
//      without OAuth simply shows path 2 — never a broken button.
//   2. "Advanced: paste an API key instead" (fallback): a masked token input
//      POSTs to /integrations/{name}/credentials. The token is write-only — held
//      transiently
// in component state, sent in the request body (token ONLY, never a tenant_id:
// THE TRUST RULE), never logged, never echoed back into the DOM, and cleared as
// soon as the request settles successfully. Per-status copy mirrors the API
// contract: 503 storage not configured on this deployment, 422 empty token, 502
// vault write failed.
//
// Sync-now: POSTs /integrations/{name}/sync. A run-store deployment answers
// 202 {run} — the panel then POLLS GET /integrations/{name}/syncs until that
// run leaves "running" and reports the counts the server recorded (capped;
// past the cap it says, honestly, that the sync is still running). A storeless
// deployment answers 200 {result} inline, reported as before. 503 (ingestion
// plane not wired), 409 (connect first / a sync is already running) and 502
// are surfaced with honest copy; raw "API <code>" strings never reach the user.
//
// Disconnect: DELETE /integrations/{name}/credentials behind an INLINE confirm
// step (never a silent destructive click). The server is idempotent; the panel
// reports the vault deletion exactly as answered. Each sync card also shows the
// last recorded sync run (last_sync from GET /integrations) — absent history
// renders nothing, never an invented "last synced".
//
// CSV import (file-kind connectors): an entity picker (contacts|companies|deals)
// + a file input post multipart to /integrations/csv/import via api.csvImport().
// The result is rendered honestly: imported + skipped counts plus per-row errors.
// 503 (ingest plane not wired) surfaces as "not enabled on this deployment" — no
// fake row-landed state. 422 (encoding/mapping/entity problem) shows the server's
// detail. The csv card is filtered OUT of the credentialed-connect flow.

import React from "react";
import {
  ApiClient,
  ApiError,
  defaultClient,
  friendlyErrorMessage,
  type CsvImportReport,
  type Integration,
  type IntegrationStatus,
  type ListIntegrationsResponse,
  type SyncRun,
} from "./client";
import { Spinner } from "./Spinner";
import { getValidIdToken } from "../auth/cognito";

const { useState, useEffect, useCallback, useRef } = React;

// Background-sync polling: every POLL_MS until the run leaves "running", at
// most MAX_POLLS times (then an honest "still running" note — never a spinner
// that outlives the user's patience).
const POLL_MS = 5000;
const MAX_POLLS = 60;

// ---------------------------------------------------------------------------
// Honest per-status copy. The API authors machine-facing detail strings (env
// var names, REQ ids) — these map each contract status to user-facing copy
// without ever exposing the raw "API <code>" message or the token.
// ---------------------------------------------------------------------------

function connectErrorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 503) {
      return "Credential storage isn't configured on this deployment yet, so connecting is unavailable for now.";
    }
    if (e.status === 422) {
      return "That token can't be empty. Paste the full token and try again.";
    }
    if (e.status === 502) {
      return "The credential vault didn't accept the write. Nothing was stored — please try again.";
    }
  }
  return friendlyErrorMessage(e, "Couldn't store the credential. Please try again.");
}

function syncErrorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 503) {
      return "Sync isn't configured on this deployment yet, so nothing was synced.";
    }
    if (e.status === 409) {
      // Two honest 409s share the code: "connect first" and "already running".
      // The server's detail says which — sniff it rather than guess wrong.
      if ((e.detail ?? "").includes("already running")) {
        return "A sync is already running for this integration — it will show up in the history when it finishes.";
      }
      return "Connect this integration first — no credential is stored for your workspace, so the sync didn't run.";
    }
    if (e.status === 502) {
      return "The sync didn't complete. Nothing to show — please try again shortly.";
    }
  }
  return friendlyErrorMessage(e, "Couldn't start the sync. Please try again.");
}

function disconnectErrorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 503) {
      return "Credential storage isn't configured on this deployment, so there is no vault to disconnect from.";
    }
    if (e.status === 502) {
      return "The credential vault didn't accept the delete. The token may still be stored — please try again.";
    }
  }
  return friendlyErrorMessage(e, "Couldn't disconnect. Please try again.");
}

function csvImportErrorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 503) {
      return "CSV import isn't enabled on this deployment yet — no rows were imported.";
    }
    if (e.status === 413) {
      return "That file exceeds the 5 MB import limit. Split it into smaller batches and try again.";
    }
    if (e.status === 422) {
      // Whole-file problem: the server authors a human-readable detail string
      // (encoding error, unusable mapping, bad entity) — surface it directly.
      if (e.detail && e.detail.trim().length > 0) return e.detail;
      return "The file couldn't be parsed. Check the encoding and column headers, then try again.";
    }
    if (e.status === 502) {
      return "The import pipeline didn't complete. Nothing was stored — please try again.";
    }
  }
  return friendlyErrorMessage(e, "Couldn't import the file. Please try again.");
}

// Summarize ONLY what the server reported — counts that exist in the result.
// No invented numbers: when the result carries none of the known fields, the
// summary claims nothing beyond completion.
function syncSummary(result: Record<string, unknown>): string {
  const parts: string[] = [];
  const labels: Array<[string, string]> = [
    ["pulled", "pulled"],
    ["landed_rows", "landed"],
    ["embedded", "embedded"],
    ["skipped", "skipped"],
  ];
  for (const [field, label] of labels) {
    const v = result[field];
    if (typeof v === "number") parts.push(`${v} ${label}`);
  }
  return parts.length > 0 ? `Sync finished: ${parts.join(", ")}.` : "Sync finished.";
}

// Compact local timestamp for run rows; an absent timestamp renders nothing.
function fmtWhen(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

// One honest line for the latest recorded run (the card's "last synced" note).
function lastSyncLine(run: SyncRun): string {
  switch (run.status) {
    case "running": {
      const when = fmtWhen(run.started_at);
      return when ? `Sync in progress (started ${when}).` : "Sync in progress.";
    }
    case "succeeded": {
      const when = fmtWhen(run.finished_at);
      const landed = typeof run.landed_rows === "number" ? `, ${run.landed_rows} landed` : "";
      return `Last synced${when ? ` ${when}` : ""}${landed}.`;
    }
    case "failed": {
      const when = fmtWhen(run.finished_at);
      return `Last sync${when ? ` (${when})` : ""} failed — nothing new was stored.`;
    }
    default: {
      const when = fmtWhen(run.finished_at);
      return `Last sync${when ? ` (${when})` : ""} was interrupted before finishing.`;
    }
  }
}

// ---------------------------------------------------------------------------
// OAuth ("Connect with login") path — generic across providers, gated by a
// per-connector feature flag so a connector without OAuth never shows a broken
// button.
// ---------------------------------------------------------------------------

// Nice display names for the "Connect with {Provider}" button; falls back to the
// connector's own label for any provider not listed here.
const OAUTH_PROVIDER_LABEL: Record<string, string> = {
  hubspot: "HubSpot",
  stripe: "Stripe",
  gohighlevel: "GoHighLevel",
  pipedrive: "Pipedrive",
  google: "Google",
  microsoft: "Microsoft",
  salesforce: "Salesforce",
};

// Connectors known to support the browser-OAuth path before the API advertises
// it explicitly. The API now ships an `oauth_available` boolean on each
// integration (computed from the registered OAuth providers), and the server's
// word is authoritative when present. This set is only the graceful-degrade
// fallback for older API images that predate the flag: every provider with an
// `/integrations/{name}/oauth/start` redirect registered in ingest oauth.py.
const OAUTH_DEFAULT_CONNECTORS = new Set([
  "hubspot", "gohighlevel", "google", "microsoft", "salesforce", "pipedrive",
]);

// Feature-detect whether a connector offers the browser-OAuth path. This reads
// the optional `oauth_available` field — the server's word is authoritative when
// present (so a connector can explicitly opt out) — and otherwise falls back to
// the known-capable default set above. Reading the optional field this way keeps
// this file disjoint from client.ts (no shared-type widening).
function oauthAvailable(item: Integration): boolean {
  const flag = (item as { oauth_available?: unknown }).oauth_available;
  if (typeof flag === "boolean") return flag;
  return OAUTH_DEFAULT_CONNECTORS.has(item.name);
}

function oauthProviderLabel(item: Integration): string {
  return OAUTH_PROVIDER_LABEL[item.name] ?? item.label;
}

// Resolve the API base URL the same way the shared client does (build-time env),
// so a full-page OAuth redirect targets the live API origin. Kept local to this
// panel to avoid reaching into client.ts.
function apiBaseURL(): string {
  const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env ?? {};
  return (env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
}

// Begin the OAuth dance. `GET /integrations/{name}/oauth/start` is AUTH-GATED —
// it signs THIS tenant (from the verified JWT) into the OAuth `state`, then
// returns { authorize_url }. So we must FETCH it WITH the bearer and only then
// send the browser to the returned authorize_url. A bare full-page navigation to
// /oauth/start carries no Authorization header and is rejected 401 "missing
// bearer token" — that was the bug. The error path maps the documented statuses
// (503 app-not-registered, 401 session expired) to honest copy; anything else
// surfaces the server detail. On success we leave the SPA, so we never clear busy.
async function startOAuth(item: Integration): Promise<void> {
  const token = await getValidIdToken();
  const res = await fetch(
    `${apiBaseURL()}/integrations/${encodeURIComponent(item.name)}/oauth/start`,
    { headers: token ? { Authorization: `Bearer ${token}` } : {} },
  );
  if (!res.ok) {
    let detail = "";
    try {
      detail = ((await res.json()) as { detail?: string }).detail ?? "";
    } catch {
      /* non-JSON error body — fall through to the generic message */
    }
    throw new ApiError(res.status, detail || res.statusText);
  }
  const body = (await res.json()) as { authorize_url?: unknown };
  if (typeof body.authorize_url !== "string" || body.authorize_url === "") {
    throw new ApiError(502, "the server did not return an authorize_url");
  }
  window.location.assign(body.authorize_url);
}

// Map an OAuth-start failure to honest, human copy (mirrors connectErrorMessage).
function oauthStartErrorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 503) {
      return "This connector isn't ready to connect yet — its app credentials aren't registered on this deployment.";
    }
    if (e.status === 401) {
      return "Your session expired. Please sign in again, then retry connecting.";
    }
  }
  return friendlyErrorMessage(e, "Couldn't start the connection. Please try again.");
}

// ---------------------------------------------------------------------------
// Status badge — the three statuses the API can answer, including a VISIBLE
// "Unknown" (connected: null = the server honestly couldn't determine it).
// ---------------------------------------------------------------------------

const BADGE: Record<IntegrationStatus, { label: string; fg: string; bg: string }> = {
  connected: { label: "Connected", fg: "oklch(0.42 0.1 152)", bg: "oklch(0.95 0.04 152)" },
  not_connected: { label: "Not connected", fg: "var(--ink-3, #8a8278)", bg: "var(--accent-soft, #f4f1ea)" },
  unknown: { label: "Unknown", fg: "oklch(0.5 0.12 60)", bg: "oklch(0.96 0.05 85)" },
  // "available" = file-kind (CSV): no vault slot, always upload-ready when configured.
  available: { label: "Available", fg: "oklch(0.42 0.1 152)", bg: "oklch(0.95 0.04 152)" },
};

function StatusBadge({ status }: { status: IntegrationStatus }) {
  // An unrecognized status from a newer API still renders honestly as Unknown.
  const b = BADGE[status] ?? BADGE.unknown;
  return (
    <span
      data-testid="int-status"
      data-status={status}
      style={{
        fontSize: 12,
        fontWeight: 650,
        padding: "3px 10px",
        borderRadius: 999,
        color: b.fg,
        background: b.bg,
        whiteSpace: "nowrap",
      }}
    >
      {b.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------

const card: React.CSSProperties = {
  border: "1px solid var(--line, #e3ddd3)",
  background: "var(--surface, #fff)",
  borderRadius: 14,
  padding: "18px 20px",
  marginBottom: 16,
};

interface CardMsg {
  kind: "ok" | "error";
  text: string;
}

export interface IntegrationsPanelProps {
  client?: ApiClient;
}

// Entity options for CSV import.
const CSV_ENTITIES = [
  { value: "contacts", label: "Contacts" },
  { value: "companies", label: "Companies" },
  { value: "deals", label: "Deals" },
] as const;

type CsvEntity = (typeof CSV_ENTITIES)[number]["value"];

export function IntegrationsPanel({ client }: IntegrationsPanelProps) {
  const api = client ?? defaultClient();
  const [data, setData] = useState<ListIntegrationsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rollout, setRollout] = useState(false);
  // Per-integration UI state, keyed by integration name.
  const [connectOpen, setConnectOpen] = useState<Record<string, boolean>>({});
  // Tokens live ONLY here (in memory, masked in the DOM) until the POST
  // settles; success clears them. They are never logged or echoed back.
  const [tokens, setTokens] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [msgs, setMsgs] = useState<Record<string, CardMsg>>({});
  // Per-card inline disconnect confirmation (a destructive click is never silent).
  const [confirmDisc, setConfirmDisc] = useState<Record<string, boolean>>({});
  // Per-card background-run watch (202 path): true while this panel is polling
  // the run to completion — renders the Sync button as "Syncing...".
  const [watching, setWatching] = useState<Record<string, boolean>>({});
  const watchTimers = useRef<Record<string, number>>({});
  useEffect(() => {
    const timers = watchTimers.current;
    return () => {
      // Unmount: stop every poll loop (the background sync itself continues
      // server-side; the history shows its outcome on the next visit).
      Object.values(timers).forEach((t) => window.clearTimeout(t));
    };
  }, []);
  // CSV import state (one per file-kind card; keyed for symmetry with other per-card state).
  const [csvEntity, setCsvEntity] = useState<CsvEntity>("contacts");
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [csvReport, setCsvReport] = useState<CsvImportReport | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setRollout(false);
    try {
      setData(await api.listIntegrations());
    } catch (e) {
      setData(null);
      if (e instanceof ApiError && e.status === 404) {
        // The live API image predates /integrations (the web can deploy ahead of
        // the API): a calm rollout note, not an error wall.
        setRollout(true);
      } else {
        setError(friendlyErrorMessage(e, "Couldn't load your integrations. Please try again."));
      }
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  // Silent re-fetch (keeps the current view on a transient failure) — used after
  // a background run settles so last_sync/status update without a loading flash.
  const refresh = useCallback(async () => {
    try {
      setData(await api.listIntegrations());
    } catch {
      // Transient: the visible state stays; the next explicit load surfaces errors.
    }
  }, [api]);

  const setMsg = (name: string, msg: CardMsg | null) =>
    setMsgs((m) => {
      const next = { ...m };
      if (msg === null) delete next[name];
      else next[name] = msg;
      return next;
    });

  const connect = useCallback(
    async (item: Integration) => {
      const token = tokens[item.name] ?? "";
      setBusy((b) => ({ ...b, [item.name]: true }));
      setMsg(item.name, null);
      try {
        const res = await api.storeIntegrationCredentials(item.name, { token });
        // Status straight from the API response — never assumed.
        setData((cur) =>
          cur === null
            ? cur
            : {
                ...cur,
                integrations: cur.integrations.map((i) =>
                  i.name === item.name
                    ? { ...i, status: res.status, connected: res.status === "connected" }
                    : i,
                ),
              },
        );
        // Drop the token from memory and close the form: it was vaulted
        // server-side and must not linger client-side.
        setTokens((t) => {
          const next = { ...t };
          delete next[item.name];
          return next;
        });
        setConnectOpen((o) => ({ ...o, [item.name]: false }));
        // Verification is reported exactly as the server answered it: true =
        // the provider accepted the token; null = stored unverified (a
        // definitive rejection never reaches here — the POST 422s instead).
        setMsg(item.name, {
          kind: "ok",
          text:
            res.verified === true
              ? `${item.label} is connected and the token was verified with the provider. It was stored in your workspace vault and is never shown again.`
              : `${item.label} is connected. The token was stored in your workspace vault and is never shown again.`,
        });
      } catch (e) {
        // The token is deliberately NOT part of any message; the input keeps
        // its (masked) value so the user can correct and retry.
        setMsg(item.name, { kind: "error", text: connectErrorMessage(e) });
      } finally {
        setBusy((b) => ({ ...b, [item.name]: false }));
      }
    },
    [api, tokens],
  );

  // OAuth connect: fetch the signed authorize_url (authed) then redirect. Busy
  // stays on through the redirect (we're leaving the SPA); only an error clears
  // it and surfaces honest copy on the card.
  const beginOAuth = useCallback(async (item: Integration) => {
    setBusy((b) => ({ ...b, [item.name]: true }));
    setMsg(item.name, null);
    try {
      await startOAuth(item); // navigates away on success
    } catch (e) {
      setMsg(item.name, { kind: "error", text: oauthStartErrorMessage(e) });
      setBusy((b) => ({ ...b, [item.name]: false }));
    }
  }, []);

  // Poll the run (202 path) until it leaves "running" — then report the
  // recorded counts and silently refresh the list (last_sync/status). Past
  // MAX_POLLS the panel says, honestly, that the sync is still running.
  const watchRun = useCallback(
    (item: Integration, runId: string) => {
      setWatching((w) => ({ ...w, [item.name]: true }));
      const settle = (msg: CardMsg) => {
        setWatching((w) => ({ ...w, [item.name]: false }));
        delete watchTimers.current[item.name];
        setMsg(item.name, msg);
        void refresh();
      };
      const tick = async (attempt: number) => {
        if (attempt >= MAX_POLLS) {
          settle({
            kind: "ok",
            text: "The sync is still running in the background. Its result will appear in the last-synced line here when it finishes.",
          });
          return;
        }
        try {
          const hist = await api.listIntegrationSyncs(item.name);
          const run = hist.runs.find((r) => r.id === runId);
          if (run && run.status !== "running") {
            if (run.status === "succeeded") {
              settle({ kind: "ok", text: syncSummary(run as unknown as Record<string, unknown>) });
            } else {
              settle({
                kind: "error",
                text: "The sync didn't complete — nothing new was stored. Please try again shortly.",
              });
            }
            return;
          }
        } catch {
          // Transient read failure — keep polling until the cap.
        }
        watchTimers.current[item.name] = window.setTimeout(() => void tick(attempt + 1), POLL_MS);
      };
      void tick(0);
    },
    [api, refresh],
  );

  const syncNow = useCallback(
    async (item: Integration) => {
      setBusy((b) => ({ ...b, [item.name]: true }));
      setMsg(item.name, null);
      try {
        const res = await api.kickIntegrationSync(item.name);
        if (res.run) {
          // 202: the sync runs server-side; watch it to completion.
          setMsg(item.name, {
            kind: "ok",
            text: "Sync started — it's running in the background. Results will appear here.",
          });
          watchRun(item, res.run.id);
        } else {
          // 200 (storeless deployment): the inline result, reported as before.
          setMsg(item.name, { kind: "ok", text: syncSummary(res.result ?? {}) });
        }
      } catch (e) {
        setMsg(item.name, { kind: "error", text: syncErrorMessage(e) });
      } finally {
        setBusy((b) => ({ ...b, [item.name]: false }));
      }
    },
    [api, watchRun],
  );

  const disconnect = useCallback(
    async (item: Integration) => {
      setBusy((b) => ({ ...b, [item.name]: true }));
      setMsg(item.name, null);
      try {
        const res = await api.deleteIntegrationCredentials(item.name);
        setConfirmDisc((c) => ({ ...c, [item.name]: false }));
        // Status straight from the API response — never assumed.
        setData((cur) =>
          cur === null
            ? cur
            : {
                ...cur,
                integrations: cur.integrations.map((i) =>
                  i.name === item.name
                    ? { ...i, status: res.status, connected: false }
                    : i,
                ),
              },
        );
        setMsg(item.name, {
          kind: "ok",
          text: res.deleted
            ? `${item.label} is disconnected. The stored token was deleted from your workspace vault.`
            : `${item.label} had no stored token — nothing needed deleting.`,
        });
      } catch (e) {
        setMsg(item.name, { kind: "error", text: disconnectErrorMessage(e) });
      } finally {
        setBusy((b) => ({ ...b, [item.name]: false }));
      }
    },
    [api],
  );

  const runCsvImport = useCallback(
    async (item: Integration) => {
      if (!csvFile) return;
      setBusy((b) => ({ ...b, [item.name]: true }));
      setMsg(item.name, null);
      setCsvReport(null);
      try {
        // csvImport unwraps the {name, report} envelope → the report itself.
        const report = await api.csvImport(csvEntity, csvFile);
        // Report comes straight from the server — never invented.
        setCsvReport(report);
        // Clear the file input after a successful upload so a second upload
        // isn't accidentally re-sent.
        setCsvFile(null);
      } catch (e) {
        setMsg(item.name, { kind: "error", text: csvImportErrorMessage(e) });
      } finally {
        setBusy((b) => ({ ...b, [item.name]: false }));
      }
    },
    [api, csvEntity, csvFile],
  );

  const items = data?.integrations ?? [];
  // Only sync-kind connectors carry "connected" status — file-kind (CSV) never counts.
  const syncItems = items.filter((i) => i.kind !== "file");
  const connectedCount = syncItems.filter((i) => i.status === "connected").length;

  return (
    <div
      data-testid="integrations-panel"
      style={{ maxWidth: 760, margin: "0 auto", padding: "32px 24px", fontFamily: "system-ui, sans-serif" }}
    >
      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 12, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--ink-3, #8a8278)" }}>
          Connect your stack
        </div>
        <h1 style={{ fontSize: 26, fontWeight: 760, letterSpacing: "-.02em", margin: "6px 0 4px" }}>Switchboard</h1>
        <p style={{ color: "var(--ink-3, #8a8278)", fontSize: 14 }}>
          Connect the tools your business runs on. Tokens go straight to your workspace vault and are never shown again.
        </p>
        {/* Only claim a count once we actually know it (post-load, no error, not rolling out). */}
        {!loading && !error && !rollout && data !== null && syncItems.length > 0 && (
          <div data-testid="int-connected-count" style={{ marginTop: 10, fontSize: 13, color: "var(--ink-3, #8a8278)" }}>
            {connectedCount} of {syncItems.length} connected
          </div>
        )}
      </div>

      {/* Deployment configuration notes, straight from the API — shown so an
          unconfigured deployment is honest up front, not only after a 503. */}
      {!loading && !error && !rollout && data !== null && !data.secrets_configured && (
        <div data-testid="int-secrets-note" style={{ ...card, fontSize: 13, color: "var(--ink-3, #8a8278)" }}>
          Credential storage isn&rsquo;t configured on this deployment, so connecting an
          integration won&rsquo;t work yet. Connection statuses may show as Unknown.
        </div>
      )}
      {!loading && !error && !rollout && data !== null && data.secrets_configured && !data.sync_configured && (
        <div data-testid="int-sync-note" style={{ ...card, fontSize: 13, color: "var(--ink-3, #8a8278)" }}>
          Sync isn&rsquo;t configured on this deployment yet — connected integrations
          can&rsquo;t be synced from here for now.
        </div>
      )}

      {loading && <Spinner testid="int-loading" label="Loading integrations..." />}

      {error && (
        <div
          data-testid="int-error"
          style={{ ...card, borderColor: "var(--rose, #b4413b)", color: "var(--ink, #2a2622)", fontSize: 13.5 }}
        >
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Something needs another try</div>
          <p style={{ color: "var(--ink-3, #8a8278)", lineHeight: 1.5 }}>{error}</p>
          <button data-testid="int-retry" onClick={() => void load()} style={{ ...ghostBtn, marginTop: 10, color: "var(--ink, #2a2622)" }}>
            Try again
          </button>
        </div>
      )}

      {/* The live API image may predate /integrations: a calm rollout note, not an error wall. */}
      {rollout && (
        <div data-testid="int-rollout" style={{ ...card, color: "var(--ink, #2a2622)", fontSize: 13.5 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Integrations API is rolling out</div>
          <p style={{ color: "var(--ink-3, #8a8278)", lineHeight: 1.5 }}>
            Your deployment doesn&rsquo;t serve the integrations endpoint yet &mdash; refresh after
            the next API deploy. Nothing is wrong with your workspace.
          </p>
          <button data-testid="int-rollout-refresh" onClick={() => void load()} style={{ ...ghostBtn, marginTop: 10, color: "var(--ink, #2a2622)" }}>
            Refresh
          </button>
        </div>
      )}

      {!loading && !error && !rollout && data !== null && items.length === 0 && (
        <div data-testid="int-empty" style={{ ...card, textAlign: "center", color: "var(--ink-3, #8a8278)" }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: "var(--ink, #2a2622)" }}>No integrations available yet</div>
          <p style={{ fontSize: 13, marginTop: 4 }}>
            When connectors are available for your workspace, they&rsquo;ll appear here.
          </p>
        </div>
      )}

      {!loading &&
        !error &&
        !rollout &&
        items.map((item) => {
          const isBusy = !!busy[item.name];
          const msg = msgs[item.name];

          // ---- File-kind card (CSV import) — no credential form, no sync button. ----
          if (item.kind === "file") {
            return (
              <div key={item.name} data-testid="integration-item" data-integration={item.name} style={card}>
                <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 12 }}>
                  <div style={{ fontSize: 16, fontWeight: 720 }}>{item.label}</div>
                  <StatusBadge status={item.status} />
                </div>
                <div style={{ fontSize: 12.5, color: "var(--ink-3, #8a8278)", marginTop: 2 }}>{item.category}</div>
                <p style={{ fontSize: 13.5, color: "var(--ink, #2a2622)", marginTop: 10, lineHeight: 1.5 }}>
                  {item.description}
                </p>

                {/* Per-card error message (honest, never invented). */}
                {msg && (
                  <div
                    data-testid="int-card-msg"
                    data-kind={msg.kind}
                    style={{
                      fontSize: 13,
                      lineHeight: 1.5,
                      marginTop: 12,
                      padding: "10px 12px",
                      borderRadius: 10,
                      color: msg.kind === "error" ? "var(--rose, #b4413b)" : "var(--ink, #2a2622)",
                      background: msg.kind === "error" ? "oklch(0.97 0.02 18)" : "var(--accent-soft, #f4f1ea)",
                    }}
                  >
                    {msg.text}
                  </div>
                )}

                {/* CSV import result — rendered ONLY from the server's report. */}
                {csvReport && (
                  <div
                    data-testid="csv-import-result"
                    style={{
                      marginTop: 12,
                      padding: "10px 12px",
                      borderRadius: 10,
                      background: "var(--accent-soft, #f4f1ea)",
                      fontSize: 13,
                      lineHeight: 1.6,
                    }}
                  >
                    <div style={{ fontWeight: 700, color: "var(--ink, #2a2622)", marginBottom: 4 }}>
                      Import complete
                    </div>
                    <div style={{ color: "var(--ink, #2a2622)" }}>
                      {csvReport.imported} imported, {csvReport.skipped_unchanged} skipped (total {csvReport.total_rows} rows).
                    </div>
                    {csvReport.errors.length > 0 && (
                      <div
                        data-testid="csv-import-errors"
                        style={{ marginTop: 8 }}
                      >
                        <div style={{ fontWeight: 650, color: "var(--rose, #b4413b)", marginBottom: 4 }}>
                          {csvReport.errors.length} row{csvReport.errors.length !== 1 ? "s" : ""} had problems:
                        </div>
                        <ul style={{ margin: 0, paddingLeft: 18 }}>
                          {csvReport.errors.map((err, i) => (
                            <li key={i} style={{ color: "var(--rose, #b4413b)", fontSize: 12.5 }}>
                              Row {err.row}: {err.error}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )}

                {/* CSV upload UI — entity picker + file input. */}
                <div data-testid="csv-import-form" style={{ marginTop: 16 }}>
                  <div style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
                    <div style={{ flex: "0 0 auto" }}>
                      <label
                        htmlFor={`csv-entity-${item.name}`}
                        style={{ display: "block", fontSize: 12, fontWeight: 600, color: "var(--ink-3, #8a8278)", marginBottom: 6 }}
                      >
                        Entity
                      </label>
                      <select
                        id={`csv-entity-${item.name}`}
                        data-testid="csv-entity-picker"
                        value={csvEntity}
                        disabled={isBusy}
                        onChange={(e) => {
                          setCsvEntity(e.target.value as CsvEntity);
                          // Clear the previous result when the entity changes.
                          setCsvReport(null);
                          setMsg(item.name, null);
                        }}
                        style={{
                          borderRadius: 10,
                          border: "1px solid var(--line, #e3ddd3)",
                          padding: "10px 12px",
                          fontSize: 13.5,
                          fontFamily: "inherit",
                          background: "var(--surface, #fff)",
                          cursor: isBusy ? "not-allowed" : "pointer",
                        }}
                      >
                        {CSV_ENTITIES.map((opt) => (
                          <option key={opt.value} value={opt.value}>{opt.label}</option>
                        ))}
                      </select>
                    </div>

                    <div style={{ flex: "1 1 180px" }}>
                      <label
                        htmlFor={`csv-file-${item.name}`}
                        style={{ display: "block", fontSize: 12, fontWeight: 600, color: "var(--ink-3, #8a8278)", marginBottom: 6 }}
                      >
                        CSV file (5 MB max)
                      </label>
                      <input
                        id={`csv-file-${item.name}`}
                        data-testid="csv-file-input"
                        type="file"
                        accept=".csv"
                        disabled={isBusy}
                        // Controlled via key — cleared after a successful upload by
                        // resetting csvFile to null (the input key re-mounts it).
                        onChange={(e) => {
                          const f = e.target.files?.[0] ?? null;
                          setCsvFile(f);
                          // Clear any previous result or error when a new file is chosen.
                          setCsvReport(null);
                          setMsg(item.name, null);
                        }}
                        style={{
                          display: "block",
                          width: "100%",
                          boxSizing: "border-box",
                          borderRadius: 10,
                          border: "1px solid var(--line, #e3ddd3)",
                          padding: "8px 12px",
                          fontSize: 13,
                          fontFamily: "inherit",
                          background: "var(--surface, #fff)",
                          cursor: isBusy ? "not-allowed" : "pointer",
                        }}
                      />
                    </div>
                  </div>

                  <div style={{ marginTop: 12 }}>
                    <button
                      data-testid="csv-import-submit"
                      disabled={isBusy || !csvFile}
                      onClick={() => void runCsvImport(item)}
                      style={{ ...primaryBtn, opacity: isBusy || !csvFile ? 0.6 : 1 }}
                    >
                      {isBusy ? "Importing..." : "Import CSV"}
                    </button>
                  </div>
                </div>
              </div>
            );
          }

          // ---- Sync-kind card (credentialed pull connector). ----
          const open = !!connectOpen[item.name];
          const token = tokens[item.name] ?? "";
          return (
            <div key={item.name} data-testid="integration-item" data-integration={item.name} style={card}>
              <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 12 }}>
                <div style={{ fontSize: 16, fontWeight: 720 }}>{item.label}</div>
                <StatusBadge status={item.status} />
              </div>
              <div style={{ fontSize: 12.5, color: "var(--ink-3, #8a8278)", marginTop: 2 }}>{item.category}</div>
              <p style={{ fontSize: 13.5, color: "var(--ink, #2a2622)", marginTop: 10, lineHeight: 1.5 }}>
                {item.description}
              </p>

              {/* Last recorded sync run, straight from the API. No history = no line. */}
              {item.last_sync && (
                <div
                  data-testid="int-last-sync"
                  data-run-status={item.last_sync.status}
                  style={{ fontSize: 12.5, color: "var(--ink-3, #8a8278)", marginTop: 8 }}
                >
                  {lastSyncLine(item.last_sync)}
                </div>
              )}

              {msg && (
                <div
                  data-testid="int-card-msg"
                  data-kind={msg.kind}
                  style={{
                    fontSize: 13,
                    lineHeight: 1.5,
                    marginTop: 12,
                    padding: "10px 12px",
                    borderRadius: 10,
                    color: msg.kind === "error" ? "var(--rose, #b4413b)" : "var(--ink, #2a2622)",
                    background: msg.kind === "error" ? "oklch(0.97 0.02 18)" : "var(--accent-soft, #f4f1ea)",
                  }}
                >
                  {msg.text}
                </div>
              )}

              {open ? (
                <div style={{ marginTop: 14 }}>
                  <label
                    htmlFor={`int-token-${item.name}`}
                    style={{ display: "block", fontSize: 12, fontWeight: 600, color: "var(--ink-3, #8a8278)", marginBottom: 6 }}
                  >
                    Access token (stored in your vault, never displayed)
                  </label>
                  <input
                    id={`int-token-${item.name}`}
                    data-testid="int-token-input"
                    // Masked: the token is never rendered in clear text.
                    type="password"
                    autoComplete="off"
                    spellCheck={false}
                    placeholder="Paste the access token"
                    value={token}
                    disabled={isBusy}
                    onChange={(e) => setTokens((t) => ({ ...t, [item.name]: e.target.value }))}
                    style={{
                      width: "100%",
                      boxSizing: "border-box",
                      borderRadius: 10,
                      border: "1px solid var(--line, #e3ddd3)",
                      padding: "10px 12px",
                      fontSize: 13.5,
                      fontFamily: "inherit",
                    }}
                  />
                  <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
                    <button
                      data-testid="int-token-save"
                      disabled={isBusy || token.trim() === ""}
                      onClick={() => void connect(item)}
                      style={{ ...primaryBtn, opacity: isBusy || token.trim() === "" ? 0.6 : 1 }}
                    >
                      {isBusy ? "Storing..." : "Save token"}
                    </button>
                    <button
                      data-testid="int-token-cancel"
                      disabled={isBusy}
                      onClick={() => {
                        // Cancel drops the token from memory immediately.
                        setTokens((t) => {
                          const next = { ...t };
                          delete next[item.name];
                          return next;
                        });
                        setConnectOpen((o) => ({ ...o, [item.name]: false }));
                      }}
                      style={ghostBtn}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : confirmDisc[item.name] ? (
                // Inline disconnect confirmation — a destructive click is never silent.
                <div style={{ marginTop: 14 }}>
                  <div style={{ fontSize: 13, color: "var(--ink, #2a2622)", marginBottom: 10, lineHeight: 1.5 }}>
                    Disconnect {item.label}? The stored token is deleted from your vault and
                    scheduled syncs for it stop. Your already-synced data stays.
                  </div>
                  <div style={{ display: "flex", gap: 8 }}>
                    <button
                      data-testid="int-disconnect-confirm"
                      disabled={isBusy}
                      onClick={() => void disconnect(item)}
                      style={ghostBtn}
                    >
                      {isBusy ? "Disconnecting..." : "Disconnect"}
                    </button>
                    <button
                      data-testid="int-disconnect-cancel"
                      disabled={isBusy}
                      onClick={() => setConfirmDisc((c) => ({ ...c, [item.name]: false }))}
                      style={ghostNeutralBtn}
                    >
                      Keep it connected
                    </button>
                  </div>
                </div>
              ) : (
                <div style={{ marginTop: 14 }}>
                  {/* Not connected + OAuth-capable: lead with the one-click login
                      path; the paste-key form becomes a quiet "Advanced" fallback. */}
                  {item.status !== "connected" && oauthAvailable(item) && (
                    <button
                      data-testid="int-oauth-btn"
                      data-provider={item.name}
                      disabled={isBusy}
                      onClick={() => void beginOAuth(item)}
                      style={primaryBtn}
                    >
                      Connect with {oauthProviderLabel(item)}
                    </button>
                  )}
                  <div
                    style={{
                      display: "flex",
                      gap: 8,
                      marginTop: item.status !== "connected" && oauthAvailable(item) ? 10 : 0,
                      flexWrap: "wrap",
                      alignItems: "center",
                    }}
                  >
                    <button
                      data-testid="int-connect-btn"
                      disabled={isBusy}
                      onClick={() => {
                        setMsg(item.name, null);
                        setConnectOpen((o) => ({ ...o, [item.name]: true }));
                      }}
                      style={
                        item.status === "connected"
                          ? ghostNeutralBtn
                          : oauthAvailable(item)
                            ? linkBtn
                            : primaryBtn
                      }
                    >
                      {item.status === "connected"
                        ? "Replace token"
                        : oauthAvailable(item)
                          ? "Advanced: paste an API key instead"
                          : "Connect"}
                    </button>
                    {item.status === "connected" && (
                    <button
                      data-testid="int-sync-btn"
                      disabled={isBusy || !!watching[item.name]}
                      onClick={() => void syncNow(item)}
                      style={primaryBtn}
                    >
                      {isBusy || watching[item.name] ? "Syncing..." : "Sync now"}
                    </button>
                  )}
                  {item.status === "connected" && (
                    <button
                      data-testid="int-disconnect-btn"
                      disabled={isBusy || !!watching[item.name]}
                      onClick={() => {
                        setMsg(item.name, null);
                        setConfirmDisc((c) => ({ ...c, [item.name]: true }));
                      }}
                      style={ghostBtn}
                    >
                      Disconnect
                    </button>
                  )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
    </div>
  );
}

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
  color: "var(--rose, #b4413b)",
  fontSize: 13.5,
  fontWeight: 650,
  cursor: "pointer",
};

const ghostNeutralBtn: React.CSSProperties = {
  ...ghostBtn,
  color: "var(--ink, #2a2622)",
};

// Quiet, link-style affordance for the "Advanced: paste an API key instead"
// fallback shown beneath the primary "Connect with {Provider}" OAuth button.
const linkBtn: React.CSSProperties = {
  padding: "6px 0",
  border: "none",
  background: "transparent",
  color: "var(--ink-3, #8a8278)",
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
  textDecoration: "underline",
  textUnderlineOffset: 3,
};

export default IntegrationsPanel;
