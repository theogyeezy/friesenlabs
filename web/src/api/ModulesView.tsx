// "Your suite" — the per-tenant module manager (Settings).
//
// Wired to the real API via ApiClient (GET/PUT /account/modules). The tenant's
// instance shows ONLY the modules enabled here: toggling a module on/off persists
// the entitlement set (RLS-scoped, server derives the tenant from the verified JWT —
// the client never sends a tenant_id) and re-gates the app's nav + routes.
//
// Billing model = "selection sets the price": the monthly total is the sum of the
// enabled modules' prices (Phase 2 wires each enabled module to a Stripe subscription
// item; here we surface the à-la-carte total the selection implies).
//
// HONEST states:
//   * GET 503 (the module store isn't wired on this deployment) → a calm notice,
//     never a fake toggle grid that silently discards.
//   * Required modules (the Command Center spine) render as "Included" and cannot
//     be switched off — the server forces them on regardless.
//   * Save PUTs the chosen set; on success the server-normalized catalog is reflected
//     back and onChange fires so the app refreshes its route gate.

import React from "react";
import {
  ApiClient,
  ApiError,
  defaultClient,
  friendlyErrorMessage,
  type ModuleCatalog,
  type ModuleEntry,
} from "./client";
import { Spinner } from "./Spinner";

const { useState, useCallback, useEffect, useMemo } = React;

const card: React.CSSProperties = {
  border: "1px solid var(--line, #e3ddd3)",
  background: "var(--surface, #fff)",
  borderRadius: 14,
  padding: "18px 20px",
};
const muted: React.CSSProperties = { color: "var(--ink-3, #8a8278)" };
const primaryBtn: React.CSSProperties = {
  padding: "9px 16px",
  borderRadius: 10,
  border: "none",
  background: "var(--ink, #2a2622)",
  color: "var(--bg, #fff)",
  fontSize: 13.5,
  fontWeight: 680,
  cursor: "pointer",
};

function dollars(cents: number): string {
  return `$${(cents / 100).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
}

export interface ModulesViewProps {
  client?: ApiClient;
  /** Fires after a successful save with the server-normalized catalog, so the app
   * can re-gate its nav/routes against the new enabled_routes. */
  onChange?: (catalog: ModuleCatalog) => void;
}

export function ModulesView({ client, onChange }: ModulesViewProps) {
  const api = client ?? defaultClient();

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  const [modules, setModules] = useState<ModuleEntry[]>([]);
  // The pending selection (local, edited by the toggles). Required ids stay in here.
  const [enabled, setEnabled] = useState<Set<string>>(new Set());
  // The last-saved selection — drives the "unsaved changes" affordance.
  const [savedSet, setSavedSet] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  // A non-fatal note when the entitlement saved but the Stripe billing sync didn't (only
  // possible once per-module billing is wired). The suite IS updated regardless.
  const [billingNote, setBillingNote] = useState<string | null>(null);

  const applyCatalog = useCallback((cat: ModuleCatalog) => {
    setModules(cat.modules);
    const on = new Set(cat.modules.filter((m) => m.enabled).map((m) => m.id));
    setEnabled(on);
    setSavedSet(new Set(on));
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    setUnavailable(false);
    try {
      applyCatalog(await api.getModules());
    } catch (e) {
      if (e instanceof ApiError && e.status === 503) {
        setUnavailable(true);
      } else {
        setLoadError(friendlyErrorMessage(e));
      }
    } finally {
      setLoading(false);
    }
  }, [api, applyCatalog]);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = useCallback((m: ModuleEntry) => {
    if (m.required) return; // required spine can't be switched off
    setSaved(false);
    setEnabled((prev) => {
      const next = new Set(prev);
      if (next.has(m.id)) next.delete(m.id);
      else next.add(m.id);
      return next;
    });
  }, []);

  // Live monthly total from the PENDING selection (required modules always count).
  const pendingTotal = useMemo(
    () => modules.filter((m) => m.required || enabled.has(m.id)).reduce((sum, m) => sum + m.monthly_cents, 0),
    [modules, enabled],
  );

  const dirty = useMemo(() => {
    if (enabled.size !== savedSet.size) return true;
    for (const id of enabled) if (!savedSet.has(id)) return true;
    return false;
  }, [enabled, savedSet]);

  const save = useCallback(async () => {
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    setBillingNote(null);
    try {
      const cat = await api.putModules([...enabled]);
      applyCatalog(cat);
      setSaved(true);
      // Honest billing feedback: only when billing is wired AND the sync didn't fully apply.
      if (cat.billing && cat.billing.status === "error") {
        setBillingNote("Your suite is saved, but updating billing didn’t go through. We’ll retry — your access is unchanged.");
      }
      onChange?.(cat);
    } catch (e) {
      setSaveError(friendlyErrorMessage(e));
    } finally {
      setSaving(false);
    }
  }, [api, enabled, applyCatalog, onChange]);

  if (loading) {
    return (
      <div data-testid="modules-loading" style={{ ...card, ...muted, display: "flex", gap: 10, alignItems: "center" }}>
        <Spinner /> Loading your suite…
      </div>
    );
  }

  if (unavailable) {
    return (
      <div data-testid="modules-unavailable" style={{ ...card, ...muted }}>
        Tailoring your suite isn’t available on this deployment yet.
      </div>
    );
  }

  if (loadError) {
    return (
      <div data-testid="modules-error" style={{ ...card }}>
        <div style={{ marginBottom: 10 }}>{loadError}</div>
        <button style={primaryBtn} onClick={() => void load()}>Try again</button>
      </div>
    );
  }

  return (
    <div data-testid="modules-view" style={{ display: "grid", gap: 16 }}>
      <div style={{ ...muted, fontSize: 13 }}>
        Turn modules on or off to tailor what your workspace shows. Your monthly total reflects
        the modules you keep on.
      </div>

      <div style={card}>
        <div style={{ display: "grid", gap: 4 }}>
          {modules.map((m) => {
            const on = m.required || enabled.has(m.id);
            return (
              <label
                key={m.id}
                style={{
                  display: "flex",
                  gap: 12,
                  alignItems: "center",
                  padding: "11px 4px",
                  borderBottom: "1px solid var(--line-2, #efebe3)",
                  cursor: m.required ? "default" : "pointer",
                }}
              >
                <input
                  type="checkbox"
                  data-testid={`module-toggle-${m.id}`}
                  checked={on}
                  disabled={m.required}
                  onChange={() => toggle(m)}
                />
                <span style={{ flex: 1 }}>
                  <span style={{ fontWeight: 640, fontSize: 14 }}>{m.name}</span>
                  {m.required && (
                    <span style={{ ...muted, fontSize: 11.5, marginLeft: 8, fontWeight: 600 }}>Included</span>
                  )}
                </span>
                <span style={{ ...muted, fontSize: 13, fontVariantNumeric: "tabular-nums" }}>
                  {dollars(m.monthly_cents)}/mo
                </span>
              </label>
            );
          })}
        </div>

        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "baseline",
            paddingTop: 14,
            marginTop: 2,
          }}
        >
          <span style={{ fontWeight: 680, fontSize: 14 }}>Monthly total</span>
          <span data-testid="modules-total" style={{ fontWeight: 760, fontSize: 18, fontVariantNumeric: "tabular-nums" }}>
            {dollars(pendingTotal)}/mo
          </span>
        </div>
      </div>

      <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
        <button
          style={{ ...primaryBtn, opacity: saving || !dirty ? 0.6 : 1, cursor: saving || !dirty ? "default" : "pointer" }}
          data-testid="modules-save"
          disabled={saving || !dirty}
          onClick={() => void save()}
        >
          {saving ? "Saving…" : "Save suite"}
        </button>
        {saved && !dirty && <span data-testid="modules-saved" style={{ ...muted, fontSize: 13 }}>Saved.</span>}
        {dirty && !saving && <span style={{ ...muted, fontSize: 12.5 }}>Unsaved changes</span>}
        {saveError && <span data-testid="modules-save-error" style={{ color: "var(--rose, #b4413b)", fontSize: 13 }}>{saveError}</span>}
      </div>

      {billingNote && (
        <div data-testid="modules-billing-note" style={{ ...card, ...muted, fontSize: 12.5, borderColor: "var(--amber, #c79a3a)" }}>
          {billingNote}
        </div>
      )}
    </div>
  );
}

export default ModulesView;
