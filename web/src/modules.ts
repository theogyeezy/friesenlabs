// Module catalog — the TypeScript mirror of shared/modules.py (MODULES).
//
// The running app does NOT gate off this file: it reads the authoritative
// catalog at runtime from GET /account/modules (so the live nav/route gate can
// never drift from the server). This mirror exists so the suite is legible on
// the web side without a network round-trip, and — most importantly — so a unit
// test (tests/unit/test_modules_parity.py) can assert the two stay in lockstep:
// every id, name, monthly price, required flag, and the route-ids each module
// gates must match shared/modules.py exactly. Edit BOTH (and the test will fail
// loudly if you forget one).
//
// Keep this a plain, easily-parseable array literal (the parity test reads it
// with a regex, not a TS compiler) — one object per module, fields in the order
// below, no computed values.

export interface ModuleDef {
  /** stable id (matches shared/modules.py + the app route-id mapping). */
  id: string;
  /** display name (the marketing suite-builder + Settings "Your suite"). */
  name: string;
  /** ratified monthly price in cents ("selection sets the price"). */
  monthlyCents: number;
  /** required modules cannot be disabled (Command Center is the spine). */
  required: boolean;
  /** the app route-ids this module gates (empty = no dedicated route yet). */
  routes: string[];
}

// The catalog, in the SAME order as shared/modules.py MODULES.
export const MODULES: ModuleDef[] = [
  { id: "command", name: "Command Center", monthlyCents: 4900, required: true, routes: ["dashboard", "reports", "dashboards"] },
  { id: "uplift", name: "Uplift CRM", monthlyCents: 4900, required: false, routes: ["crm", "contacts"] },
  { id: "agents", name: "Agents", monthlyCents: 3900, required: false, routes: ["agents", "studio", "marketplace"] },
  { id: "workflows", name: "Workflows", monthlyCents: 3900, required: false, routes: ["workflows"] },
  { id: "greenlight", name: "Greenlight", monthlyCents: 2500, required: false, routes: ["approvals"] },
  { id: "frontline", name: "Frontline", monthlyCents: 3900, required: false, routes: ["frontline"] },
  { id: "knowledge", name: "Knowledge", monthlyCents: 2500, required: false, routes: ["knowledge"] },
  { id: "cortex", name: "Cortex", monthlyCents: 4500, required: false, routes: ["cortex"] },
  { id: "integration", name: "Switchboard", monthlyCents: 2900, required: false, routes: ["integrations"] },
  { id: "sidecar", name: "Sidecar", monthlyCents: 3500, required: false, routes: ["sidecar"] },
  { id: "sell", name: "Sell", monthlyCents: 2500, required: false, routes: ["sell"] },
];

/** Account + governance surfaces every tenant can always reach (never gated). */
export const ALWAYS_ON_ROUTES: readonly string[] = ["settings", "security"];

export const MODULE_IDS: readonly string[] = MODULES.map((m) => m.id);

export default MODULES;
