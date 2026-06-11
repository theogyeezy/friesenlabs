// Lead capture helper for the marketing forms ("Book a call" / "Email us").
//
// Wraps ApiClient.submitLead (POST /public/leads) with:
//   - a single transient retry (network / 5xx / 429), and
//   - an honest mailto: fallback when the server can't take the lead (non-2xx,
//     including a 404 before the route is deployed).
//
// The caller uses `ok` to decide what to tell the visitor: only show a
// "we'll get back to you" confirmation when ok === true; otherwise surface the
// returned `mailtoHref` so the lead reaches us by email instead of vanishing.

import { ApiClient, ApiError, defaultClient, type LeadBody } from "./client";

/** Where a fallback mailto: lands. Real, monitored inbox on the live domain. */
export const LEADS_FALLBACK_EMAIL = "hello@friesenlabs.com";

export interface LeadSubmitResult {
  /** True only when the server accepted the lead (2xx). */
  ok: boolean;
  /** A ready-to-use mailto: link, present only on the fallback path. */
  mailtoHref?: string;
}

/** Build a prefilled mailto: link for a lead the API couldn't accept. */
export function leadMailtoHref(body: LeadBody): string {
  const subject =
    body.kind === "book_call" ? "Book a call with Friesen Labs" : "Friesen Labs enquiry";
  const lines = [
    body.name ? `Name: ${body.name}` : "",
    body.email ? `Email: ${body.email}` : "",
    body.company ? `Company: ${body.company}` : "",
    body.message ? `\n${body.message}` : "",
  ].filter(Boolean);
  const params = new URLSearchParams({ subject, body: lines.join("\n") });
  return `mailto:${LEADS_FALLBACK_EMAIL}?${params.toString()}`;
}

/** A non-2xx that is NOT worth retrying (a definitive client error). */
function isPermanent(e: unknown): boolean {
  return (
    e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 408 && e.status !== 429
  );
}

/**
 * Submit a lead with one transient retry, falling back to a mailto: link if the
 * server can't accept it. Never throws — always resolves to a result the UI can
 * render honestly.
 */
export async function submitLeadWithFallback(
  body: LeadBody,
  client: ApiClient = defaultClient(),
): Promise<LeadSubmitResult> {
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const res = await client.submitLead(body);
      if (res && res.ok !== false) return { ok: true };
      // The server answered 2xx but declined the lead — fall back honestly.
      return { ok: false, mailtoHref: leadMailtoHref(body) };
    } catch (e) {
      // Permanent client errors (e.g. 404 route-not-deployed, 422) won't get
      // better on retry — fall back immediately. Transient failures get one
      // more try before the mailto fallback.
      if (isPermanent(e) || attempt === 1) {
        return { ok: false, mailtoHref: leadMailtoHref(body) };
      }
    }
  }
  return { ok: false, mailtoHref: leadMailtoHref(body) };
}
