import React from "react";
import ReactDOM from "react-dom/client";

import "./styles.css";
import "./landing.css";

// Side-effect import: registers all shared globals (FL_DATA, store, Icon, the
// AI helper, charts, panels, tweaks, and every screen component) onto window in
// the prototype's original load order. Must run before <App/> renders.
import "./globals";

import App from "./app";
import DashboardDemo from "./dashboard/Demo";

// API-wired surfaces (Phase 9b). Each reads through the typed ApiClient, which
// defaults to mock mode so these mount fully offline. They share the same
// ?view= seam pattern as the dashboard demo and never touch the converted
// (@ts-nocheck) shell.
import GreenlightQueue from "./api/GreenlightQueue";
import ChatDock from "./api/ChatDock";
import DashboardView from "./api/DashboardView";
import SignupFlow from "./signup/SignupFlow";

// Demo/wiring seams reachable via ?view=. The normal SPA shell renders otherwise.
const search = window.location.search;
const viewMatch = /[?&]view=([a-z0-9-]+)/.exec(search);
const view = viewMatch ? viewMatch[1] : null;

function Root() {
  switch (view) {
    case "dashboard-demo":
      return <DashboardDemo />;
    case "greenlight":
      return <GreenlightQueue />;
    case "chat":
      return <ChatDock />;
    case "dashboard":
      return <DashboardView />;
    case "signup":
      return <SignupFlow />;
    default:
      return <App />;
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>
);
