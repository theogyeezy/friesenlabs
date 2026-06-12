// Self-service billing surface, wired to the control-plane API via ApiClient —
// the real-mode "Plan & billing" panel that lets a tenant manage their
// subscription through the Stripe-hosted Customer Portal (change card, cancel,
// view invoices — Stripe-hosted, so it's free).
//
// Everything here is HONEST:
//   * The plan + billing status come from GET /billing (real server state). A
//     cancelled/past-due subscription shows a degraded badge, not a green lie.
//   * "Manage billing" mints a Stripe Customer Portal session (POST
//     /billing/portal-session) and redirects to billing.stripe.com. The TRUST
//     RULE holds: the server resolves the customer from the verified JWT tenant;
//     this component sends no tenant_id and no customer id.
//   * FEATURE DETECTION: if /billing answers 404 (the web can deploy ahead of
//     the billing routes), the panel renders an honest "not yet available"
//     state instead of a fake button. A 403 (no customer mapping yet) disables
//     the button with honest copy; a 503 (Stripe unconfigured) surfaces friendly
//     copy and lets the user retry.
//   * Raw transport strings never reach the DOM — catches route through
//     friendlyErrorMessage.

import React from "react";
import { ApiError, defaultClient, friendlyErrorMessage, type BillingState, type Invoice } from "./client";
import { Spinner } from "./Spinner";

const { useState, useEffect, useCallback } = React;

const card: React.CSSProperties = {
  border: "1px solid var(--line, #e3ddd3)",
  background: "var(--surface, #fff)",
  borderRadius: 14,
  padding: "18px 20px",
  maxWidth: 620,
};

const muted: React.CSSProperties = { color: "var(--ink-3, #8a8278)" };

const primaryBtn: React.CSSProperties = {
  padding: "9px 16px",
  borderRadius: 10,
  border: "1px solid var(--line, #e3ddd3)",
  background: "var(--ink, #2a2622)",
  color: "var(--bg, #fff)",
  fontSize: 13.5,
  fontWeight: 680,
  cursor: "pointer",
};

const PLAN_LABELS: Record<string, string> = {
  starter: "Starter",
  team: "Team",
  scale: "Scale",
};

// Format an integer-cents amount to a readable currency string (e.g. $1,234.00).
function formatMoney(cents: number, currency: string | null | undefined): string {
  // Guard a null/empty currency BEFORE any .toUpperCase() (a null would throw — and the throw
  // would re-throw inside the catch too). Stripe contracts default to "usd" but the row could be
  // malformed; never let the invoice list crash the panel.
  const cur = (currency || "USD").toUpperCase();
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: cur,
    }).format(cents / 100);
  } catch {
    // Unknown currency code — fall back to a plain decimal with the code.
    return `${(cents / 100).toFixed(2)} ${cur}`;
  }
}

// Format a Unix timestamp in seconds to a readable date string.
function formatUnixDate(seconds: number): string {
  const d = new Date(seconds * 1000);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

// Friendly, honest copy for each billing status (Stripe Subscription.status).
const STATUS_COPY: Record<string, { label: string; tone: "ok" | "warn" }> = {
  active: { label: "Active", tone: "ok" },
  trialing: { label: "Trialing", tone: "ok" },
  past_due: { label: "Past due", tone: "warn" },
  unpaid: { label: "Unpaid", tone: "warn" },
  canceled: { label: "Canceled", tone: "warn" },
};

function StatusBadge({ status }: { status: string }) {
  const meta = STATUS_COPY[status] || { label: status, tone: "warn" as const };
  const warn = meta.tone === "warn";
  return (
    <span
      data-testid="billing-status"
      style={{
        display: "inline-flex",
        alignItems: "center",
        height: 22,
        padding: "0 9px",
        borderRadius: 999,
        fontSize: 11.5,
        fontWeight: 650,
        background: warn ? "var(--amber-soft, #fdf0d8)" : "var(--green-soft, #def2e3)",
        color: warn ? "oklch(0.5 0.12 60)" : "oklch(0.42 0.12 152)",
      }}
    >
      {meta.label}
    </span>
  );
}

// Invoice status display — honest labels for Stripe invoice statuses.
const INVOICE_STATUS_COPY: Record<string, { label: string; tone: "ok" | "warn" | "neutral" }> = {
  paid: { label: "Paid", tone: "ok" },
  open: { label: "Open", tone: "warn" },
  draft: { label: "Draft", tone: "neutral" },
  uncollectible: { label: "Uncollectible", tone: "warn" },
  void: { label: "Void", tone: "neutral" },
};

function InvoiceStatusBadge({ status }: { status: string }) {
  const meta = INVOICE_STATUS_COPY[status] || { label: status, tone: "neutral" as const };
  const style: React.CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    height: 20,
    padding: "0 8px",
    borderRadius: 999,
    fontSize: 11,
    fontWeight: 650,
    ...(meta.tone === "ok"
      ? { background: "var(--green-soft, #def2e3)", color: "oklch(0.42 0.12 152)" }
      : meta.tone === "warn"
        ? { background: "var(--amber-soft, #fdf0d8)", color: "oklch(0.5 0.12 60)" }
        : { background: "var(--accent-soft, #f4f1ea)", color: "var(--ink-2, #5d564d)" }),
  };
  return <span style={style}>{meta.label}</span>;
}

/**
 * The real-mode "Plan & billing" panel. `client` is injectable for tests;
 * defaults to the shared app client.
 */
export function BillingManage({ client = defaultClient() }: { client?: ReturnType<typeof defaultClient> }) {
  const [state, setState] = useState<BillingState | null>(null);
  const [loading, setLoading] = useState(true);
  // null = available; string = an honest "not available" reason (404 feature-off).
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const [redirecting, setRedirecting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Invoices — separate fetch so a billing-routes 404 degrades both sections.
  // "unavailable" mirrors the plan panel's 404 feature-detect pattern.
  const [invoices, setInvoices] = useState<Invoice[] | null>(null);
  const [invoicesLoading, setInvoicesLoading] = useState(true);
  const [invoicesUnavailable, setInvoicesUnavailable] = useState<string | null>(null);
  const [invoicesError, setInvoicesError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const s = await client.getBillingState();
      setState(s);
      setUnavailable(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        // The billing routes aren't deployed on this backend yet — honest, not an error.
        setUnavailable("Self-service billing isn't available on this workspace yet.");
      } else {
        setError(friendlyErrorMessage(e, "Couldn't load your billing details."));
      }
    } finally {
      setLoading(false);
    }
  }, [client]);

  const loadInvoices = useCallback(async () => {
    setInvoicesLoading(true);
    setInvoicesError(null);
    try {
      const list = await client.listInvoices();
      setInvoices(list);
      setInvoicesUnavailable(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        // Billing routes not yet deployed — degrade honestly, same as plan panel.
        setInvoicesUnavailable("Invoice history isn't available on this workspace yet.");
      } else {
        setInvoicesError(friendlyErrorMessage(e, "Couldn't load your invoice history."));
      }
    } finally {
      setInvoicesLoading(false);
    }
  }, [client]);

  useEffect(() => {
    void load();
    void loadInvoices();
  }, [load, loadInvoices]);

  const manage = useCallback(async () => {
    setRedirecting(true);
    setError(null);
    try {
      const { url } = await client.createBillingPortalSession();
      if (url) {
        // Hand off to the Stripe-hosted portal. Provisioning/cancellation flow
        // back through the signed webhook; nothing is assumed client-side.
        window.location.assign(url);
        return; // navigating away — keep the button in its redirecting state
      }
      // Mock/offline build returns an empty url — surface an honest notice.
      setError("Billing portal isn't available in this preview.");
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) {
        setError(
          e.detail ||
            "There's no active billing account to manage yet.",
        );
      } else {
        setError(friendlyErrorMessage(e, "Couldn't open the billing portal. Please try again."));
      }
    } finally {
      setRedirecting(false);
    }
  }, [client]);

  if (loading) {
    return (
      <div style={card} data-testid="billing-panel">
        <Spinner label="Loading your plan…" />
      </div>
    );
  }

  if (unavailable) {
    return (
      <div style={card} data-testid="billing-panel">
        <div style={{ fontWeight: 680, marginBottom: 6 }}>Plan &amp; billing</div>
        <p style={{ ...muted, fontSize: 13.5, margin: 0 }}>{unavailable}</p>
      </div>
    );
  }

  const planLabel = state?.plan ? PLAN_LABELS[state.plan] || state.plan : "No active plan";
  const hasCustomer = !!state?.customer;

  return (
    <div style={card} data-testid="billing-panel">
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 3 }}>
            <span style={{ fontSize: 16, fontWeight: 720 }} data-testid="billing-plan">
              {planLabel}
            </span>
            {state && <StatusBadge status={state.status} />}
          </div>
          <div style={{ ...muted, fontSize: 13 }}>
            Manage your subscription, payment method, and invoices.
          </div>
        </div>
        <button
          type="button"
          style={{ ...primaryBtn, opacity: hasCustomer ? 1 : 0.55, cursor: hasCustomer ? "pointer" : "not-allowed" }}
          onClick={manage}
          disabled={!hasCustomer || redirecting}
          data-testid="manage-billing"
          title={hasCustomer ? "Open the Stripe billing portal" : "No active billing account to manage yet"}
        >
          {redirecting ? "Opening…" : "Manage billing"}
        </button>
      </div>
      {!hasCustomer && (
        <p style={{ ...muted, fontSize: 12.5, margin: "12px 0 0" }}>
          There's no active paid subscription on this workspace yet, so there's nothing to manage.
        </p>
      )}
      {error && (
        <div
          role="alert"
          style={{ marginTop: 12, fontSize: 13, color: "oklch(0.5 0.16 25)" }}
          data-testid="billing-error"
        >
          {error}
          <button
            type="button"
            style={{ marginLeft: 10, ...primaryBtn, padding: "5px 11px", fontSize: 12.5 }}
            onClick={() => void manage()}
          >
            Try again
          </button>
        </div>
      )}

      {/* --- Invoices section ----------------------------------------------- */}
      <div
        data-testid="billing-invoices"
        style={{ marginTop: 20, paddingTop: 18, borderTop: "1px solid var(--line-2, #efe9df)" }}
      >
        <div style={{ fontWeight: 680, fontSize: 13.5, marginBottom: 10 }}>Invoice history</div>

        {invoicesLoading && <Spinner label="Loading invoices…" />}

        {!invoicesLoading && invoicesUnavailable && (
          <p style={{ ...muted, fontSize: 13, margin: 0 }} data-testid="invoices-unavailable">
            {invoicesUnavailable}
          </p>
        )}

        {!invoicesLoading && invoicesError && (
          <div role="alert" data-testid="invoices-error">
            <p style={{ fontSize: 13, color: "oklch(0.5 0.16 25)", margin: "0 0 8px" }}>{invoicesError}</p>
            <button
              type="button"
              style={{ ...primaryBtn, padding: "5px 11px", fontSize: 12.5 }}
              onClick={() => void loadInvoices()}
            >
              Try again
            </button>
          </div>
        )}

        {!invoicesLoading && !invoicesUnavailable && !invoicesError && invoices !== null && (
          invoices.length === 0 ? (
            <p style={{ ...muted, fontSize: 13, margin: 0 }} data-testid="invoices-empty">
              No invoices yet.
            </p>
          ) : (
            <div data-testid="invoices-list">
              {invoices.map((inv, i) => (
                <div
                  key={inv.id}
                  data-testid="invoice-row"
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 12,
                    flexWrap: "wrap",
                    padding: "10px 0",
                    borderTop: i === 0 ? "none" : "1px solid var(--line-2, #efe9df)",
                    fontSize: 13,
                  }}
                >
                  {/* Invoice number */}
                  <span
                    style={{
                      flex: 1,
                      minWidth: 120,
                      fontFamily: "var(--mono, ui-monospace, monospace)",
                      fontWeight: 650,
                      color: "var(--ink, #2a2622)",
                      fontSize: 12.5,
                      overflowWrap: "anywhere",
                    }}
                  >
                    {inv.number ?? inv.id}
                  </span>

                  {/* Amounts */}
                  <span style={{ ...muted, fontSize: 12.5, whiteSpace: "nowrap" }}>
                    Due{" "}
                    <span style={{ color: "var(--ink, #2a2622)", fontWeight: 650 }}>
                      {formatMoney(inv.amount_due, inv.currency)}
                    </span>
                    {inv.amount_paid > 0 && inv.amount_paid !== inv.amount_due && (
                      <span> · Paid {formatMoney(inv.amount_paid, inv.currency)}</span>
                    )}
                  </span>

                  {/* Status badge */}
                  <InvoiceStatusBadge status={inv.status} />

                  {/* Created date */}
                  <span
                    style={{
                      fontSize: 12,
                      fontFamily: "var(--mono, ui-monospace, monospace)",
                      ...muted,
                      whiteSpace: "nowrap",
                    }}
                  >
                    {formatUnixDate(inv.created)}
                  </span>

                  {/* Links */}
                  <span style={{ display: "flex", gap: 8, flexShrink: 0 }}>
                    {inv.hosted_invoice_url && (
                      <a
                        href={inv.hosted_invoice_url}
                        target="_blank"
                        rel="noreferrer"
                        style={{ fontSize: 12.5, color: "var(--ink, #2a2622)", fontWeight: 650 }}
                        data-testid="invoice-view-link"
                      >
                        View
                      </a>
                    )}
                    {inv.invoice_pdf && (
                      <a
                        href={inv.invoice_pdf}
                        target="_blank"
                        rel="noreferrer"
                        style={{ fontSize: 12.5, color: "var(--ink, #2a2622)", fontWeight: 650 }}
                        data-testid="invoice-pdf-link"
                      >
                        PDF
                      </a>
                    )}
                  </span>
                </div>
              ))}
            </div>
          )
        )}
      </div>
    </div>
  );
}

export default BillingManage;
