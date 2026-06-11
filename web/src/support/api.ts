// Support-surface API helpers — self-contained so the contact/help form and the
// status page never reach into the shared ApiClient core (keeps this feature's
// territory isolated). Both call the SAME pre-auth, no-tenant-id, no-bearer
// contract the marketing forms use (POST /public/support, GET /healthz).
//
// Mirrors the leads helper's honesty rules:
//   - submitSupport: returns ok=false (never throws) so the form can surface an
//     honest mailto: fallback when the route can't take the request (404 before
//     deploy, network, 5xx, 429), instead of a fake "we got it".
//   - fetchStatus: degrades gracefully — a failed probe is "unknown", never a
//     thrown error or a fabricated "all good".
//
// In mock/test builds (VITE_API_MOCK !== "0") there is no backend, so both
// resolve from canned results without a network call — the form is exercisable
// and the status page renders deterministically under Playwright.

import { apiMockEnabled } from "../api/client";

/** Where a fallback mailto: lands when the API can't take the request. */
export const SUPPORT_FALLBACK_EMAIL = "support@friesenlabs.com";

export interface SupportBody {
  name: string;
  email: string;
  subject: string;
  message: string;
  /** Optional free-text workspace hint — a triage aid, never trusted for auth. */
  tenant?: string;
}

export interface SupportSubmitResult {
  /** True only when the server accepted the request (2xx). */
  ok: boolean;
  /** A ready-to-use mailto: link, present only on the fallback path. */
  mailtoHref?: string;
}

/** Resolve the API base URL the same way the shared client does (build-time env). */
function apiBaseURL(): string {
  const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env ?? {};
  return (env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
}

/** Build a prefilled mailto: link for a request the API couldn't accept. */
export function supportMailtoHref(body: SupportBody): string {
  const lines = [
    body.name ? `Name: ${body.name}` : "",
    body.email ? `Email: ${body.email}` : "",
    body.tenant ? `Workspace: ${body.tenant}` : "",
    body.message ? `\n${body.message}` : "",
  ].filter(Boolean);
  const params = new URLSearchParams({
    subject: body.subject || "Friesen Labs support request",
    body: lines.join("\n"),
  });
  return `mailto:${SUPPORT_FALLBACK_EMAIL}?${params.toString()}`;
}

/**
 * Submit a support request, falling back to a mailto: link if the server can't
 * accept it. Never throws — always resolves to a result the UI can render
 * honestly (ok === true only when the server returned 2xx).
 */
export async function submitSupport(
  body: SupportBody,
  fetchImpl: typeof fetch = globalThis.fetch?.bind(globalThis),
): Promise<SupportSubmitResult> {
  if (apiMockEnabled()) {
    // Offline/test builds: acknowledge without a network call so the form is
    // exercisable without a backend.
    return { ok: true };
  }
  const payload: SupportBody = {
    name: body.name,
    email: body.email,
    subject: body.subject,
    message: body.message,
  };
  if (body.tenant) payload.tenant = body.tenant;
  try {
    const res = await fetchImpl(`${apiBaseURL()}/public/support`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (res.ok) {
      // The route answers {ok:true,id}; treat a 2xx as accepted even if the body
      // is unexpected (the server's status is the source of truth).
      try {
        const j = (await res.json()) as { ok?: boolean };
        if (j && j.ok === false) return { ok: false, mailtoHref: supportMailtoHref(body) };
      } catch {
        // non-JSON 2xx — still accepted.
      }
      return { ok: true };
    }
    return { ok: false, mailtoHref: supportMailtoHref(body) };
  } catch {
    // Network failure / CORS / DNS — fall back so the request never vanishes.
    return { ok: false, mailtoHref: supportMailtoHref(body) };
  }
}

// ---------------------------------------------------------------------------
// Status page — read the public health signal (GET /healthz). The API exposes a
// single overall liveness probe today; the status page shows that overall
// signal plus an honest note that per-component readiness will appear as the
// platform exposes it (see STATUS_COMPONENTS in the PR notes — infra can inject
// richer probes later WITHOUT a web change to this contract).
// ---------------------------------------------------------------------------

export type ProbeState = "operational" | "degraded" | "down" | "unknown";

export interface ComponentStatus {
  /** Stable id (used as a key / data-testid suffix). */
  id: string;
  /** Human label. */
  label: string;
  state: ProbeState;
  /** Short honest note shown under the label. */
  note: string;
}

export interface StatusReport {
  /** Roll-up of the components below. */
  overall: ProbeState;
  components: ComponentStatus[];
  /** When the probe ran (ISO) — display only. */
  checkedAt: string;
}

/** Roll a set of component states up to a single overall state (worst wins,
 *  but an all-unknown set stays "unknown" rather than masquerading as down).
 *  Unknown entries from probe-less informational rows are excluded — only
 *  real probed components influence the rollup (healthz ok => operational). */
export function rollupState(states: ProbeState[]): ProbeState {
  if (states.some((s) => s === "down")) return "down";
  if (states.some((s) => s === "degraded")) return "degraded";
  if (states.some((s) => s === "operational")) return "operational";
  return "unknown";
}

/**
 * Fetch the public health signal and shape it into a StatusReport. Degrades
 * gracefully: a failed/unreachable probe yields "unknown" for the API
 * component (never a thrown error, never a fabricated "operational"). In
 * mock/test builds it resolves a deterministic operational report.
 */
export async function fetchStatus(
  fetchImpl: typeof fetch = globalThis.fetch?.bind(globalThis),
): Promise<StatusReport> {
  const checkedAt = new Date().toISOString();
  const components: ComponentStatus[] = [];

  if (apiMockEnabled()) {
    components.push({
      id: "api",
      label: "Application & API",
      state: "operational",
      note: "Sign-in, chat, and the dashboard are responding.",
    });
  } else {
    let apiState: ProbeState = "unknown";
    try {
      const res = await fetchImpl(`${apiBaseURL()}/healthz`, { method: "GET" });
      apiState = res.ok ? "operational" : "down";
    } catch {
      // Unreachable probe: honest "unknown", never a fabricated state.
      apiState = "unknown";
    }
    components.push({
      id: "api",
      label: "Application & API",
      state: apiState,
      note:
        apiState === "operational"
          ? "Sign-in, chat, and the dashboard are responding."
          : apiState === "down"
            ? "The API health check is failing. We're on it."
            : "We couldn't reach the health check just now.",
    });
  }

  // The platform exposes a single overall liveness probe today (GET /healthz).
  // As component-level readiness lands (the agent plane, data plane, ingest),
  // infra can surface them through this same shape — see the PR notes
  // (STATUS_COMPONENTS). Until then we show the honest note below as a
  // non-probed informational row. The rollupState function correctly ignores
  // unknown rows when at least one real probe is operational, so this row
  // does NOT force the overall status to "degraded".
  components.push({
    id: "subsystems",
    label: "Agent, data & ingest planes",
    state: "unknown",
    note: "Per-component health is not individually probed yet; it follows the API status above.",
  });

  return {
    overall: rollupState(components.map((c) => c.state)),
    components,
    checkedAt,
  };
}
