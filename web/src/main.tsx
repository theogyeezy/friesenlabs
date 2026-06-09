import React from "react";
import ReactDOM from "react-dom/client";

import "./styles.css";
import "./landing.css";

// Side-effect import: registers all shared globals (FL_DATA, store, Icon, the
// AI helper, charts, panels, tweaks, and every screen component) onto window in
// the prototype's original load order. Must run before <App/> renders.
import "./globals";

import App from "./app";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
