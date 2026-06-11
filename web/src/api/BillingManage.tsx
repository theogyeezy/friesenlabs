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
import { ApiError, defaultClient, friendlyErrorMessage, type BillingState } from "./client";
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

  useEffect(() => {
    void load();
  }, [load]);

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
    </div>
  );
}

export default BillingManage;
