// globals.ts — runtime registry shim.
//
// The original prototype shared everything through `window` globals and relied
// on a fixed <script> load order in index.html. We preserve that contract as a
// real ES module graph: this barrel imports the infrastructure + screen modules
// in the SAME order the prototype loaded them, so each module's window
// registrations (window.FL_DATA, window.useStore, window.Icon, window.claude,
// the chart/panel/tweak helpers, every screen component, etc.) are populated in
// a deterministic order before any screen renders.
//
// Screens are converted to read their shared dependencies from `window` at the
// top of the module (e.g. `const { Icon, useStore } = window as any`). Because
// this barrel is imported first and every shared symbol is registered at module
// eval time, those reads resolve correctly. All shared reads happen inside
// component render bodies anyway, which run after the whole graph has loaded.
//
// `window.claude` (the AI helper) stays a simulated, typed stub — no real API
// calls. See ai.tsx.

// Make `window` usable as a typed registry without per-symbol declarations.
declare global {
  interface Window {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    [key: string]: any;
    claude?: { complete: (prompt: string) => Promise<string> };
  }
}

// ---- load order mirrors the prototype's index.html ----
import "./screens/tweaks-panel";
import "./icons";
import "./data";
import "./store";
import "./ai";
import "./screens/gamify";
import "./screens/charts";
import "./screens/dashboard";
import "./screens/crm";
import "./screens/contacts";
import "./screens/billing";
import "./screens/calendar";
import "./screens/reviews";
import "./screens/templates";
import "./screens/sell";
import "./screens/salesdesk";
import "./screens/frontline";
import "./screens/panels";
import "./screens/onboarding";
import "./screens/tour";
import "./screens/chat";
import "./screens/workflow";
import "./screens/integrations";
import "./screens/sidecar";
import "./screens/cortex";
import "./screens/knowledge";
import "./screens/brain";
import "./screens/personal-recall";
import "./screens/agents";
import "./screens/agent-market";
import "./screens/import-data";
import "./screens/greenlight";
import "./screens/reports";
import "./screens/security";
import "./screens/settings";
import "./screens/commandbot";
import "./screens/intake";
import "./screens/email";

export {};
