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

// Demo seam for the Phase 7 view-spec renderer. Reachable at ?view=dashboard-demo;
// the normal SPA shell renders otherwise. Keeps the renderer mountable in the
// running app without touching the converted (@ts-nocheck) shell.
const isDashboardDemo = /[?&]view=dashboard-demo\b/.test(window.location.search);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {isDashboardDemo ? <DashboardDemo /> : <App />}
  </React.StrictMode>
);
