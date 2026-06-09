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
import { SafeHtml } from "./lib/SafeHtml";

// Demo proving feed HTML is sanitized: a malicious payload renders inert, safe markup survives.
function SafeHtmlDemo() {
  const payload =
    '<img src=x onerror="window.__pwned=1"><script>window.__pwned=1<\/script><b>safe bold</b>';
  return (
    <div style={{ padding: 24 }}>
      <h1>SafeHtml</h1>
      <div data-testid="feed">
        <SafeHtml as="p" html={payload} />
      </div>
    </div>
  );
}

// API-wired surfaces (Phase 9b). Each reads through the typed ApiClient, which
// defaults to mock mode so these mount fully offline. They share the same
// ?view= seam pattern as the dashboard demo and never touch the converted
// (@ts-nocheck) shell.
import GreenlightQueue from "./api/GreenlightQueue";
import ChatDock from "./api/ChatDock";
import DashboardView from "./api/DashboardView";
import SignupFlow from "./signup/SignupFlow";

// Auth (Cognito Hosted UI, PKCE). The provider and the gate are fully inert
// when auth is disabled (mock mode / Cognito unconfigured), so dev and
// Playwright behave exactly as before.
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { handleCallback, isAuthEnabled, signIn } from "./auth/cognito";

// The landing screen reads its demo components (FoxDemo, KanbanDemo, ...) off
// window at module-eval time, and landing-demos registers them there — so
// landing-demos MUST be imported (evaluated) before landing.
import "./screens/landing-demos";
import Landing from "./screens/landing";

// Demo/wiring seams reachable via ?view=. The normal SPA shell renders otherwise.
const search = window.location.search;
const viewMatch = /[?&]view=([a-z0-9-]+)/.exec(search);
const view = viewMatch ? viewMatch[1] : null;

// Sign-in gate. Only active when Cognito is configured AND the API is real
// (isAuthEnabled): signed-out visitors get the marketing landing with its
// Sign in controls wired to the Hosted UI; signed-in users get the wrapped
// surface. Inert in mock/unconfigured builds (dev, tests, Playwright).
function Gated({ children }: { children: React.ReactElement }) {
  const auth = useAuth();
  if (!isAuthEnabled() || auth.isAuthenticated) return children;
  return <Landing onSignIn={auth.signIn} />;
}

function Root() {
  switch (view) {
    // Offline demo surfaces — no API client, safe ungated.
    case "dashboard-demo":
      return <DashboardDemo />;
    case "safehtml-demo":
      return <SafeHtmlDemo />;
    // Pre-auth by design: the signup funnel runs before any tenant or token exists.
    case "signup":
      return <SignupFlow />;
    // API-wired surfaces — gated like the default shell, or a real build would
    // mount them signed-out and 401 on every call.
    case "greenlight":
      return (
        <Gated>
          <GreenlightQueue />
        </Gated>
      );
    case "chat":
      return (
        <Gated>
          <ChatDock />
        </Gated>
      );
    case "dashboard":
      return (
        <Gated>
          <DashboardView />
        </Gated>
      );
    default:
      return (
        <Gated>
          <App />
        </Gated>
      );
  }
}

// The OAuth callback (/auth/callback — the SPA rewrite serves index.html for
// any path). Exchange the code, strip the URL back to "/", then render the
// app. handleCallback() is one-shot internally, so StrictMode's double effect
// never burns the single-use authorization code twice.
function AuthCallback() {
  const [phase, setPhase] = React.useState<"working" | "done" | "error">("working");
  const [message, setMessage] = React.useState("");

  React.useEffect(() => {
    if (!isAuthEnabled()) {
      // Nothing to exchange in mock/unconfigured builds; just land on the app.
      window.history.replaceState(null, "", "/");
      setPhase("done");
      return;
    }
    handleCallback().then(
      () => {
        window.history.replaceState(null, "", "/");
        setPhase("done");
      },
      (e) => {
        // Strip ?code=&state= on failure too: the params are already captured,
        // and a stale single-use code shouldn't linger in the URL/history.
        window.history.replaceState(null, "", "/auth/callback");
        setMessage(e instanceof Error ? e.message : "Sign-in failed.");
        setPhase("error");
      },
    );
  }, []);

  if (phase === "done") return <Root />;

  const center: React.CSSProperties = {
    display: "grid",
    placeItems: "center",
    minHeight: "100vh",
    fontFamily: "system-ui, sans-serif",
  };
  if (phase === "error") {
    return (
      <div style={center}>
        <div style={{ textAlign: "center", maxWidth: 380, padding: 24 }}>
          <h1 style={{ fontSize: 18, fontWeight: 700 }}>Sign-in didn&apos;t complete</h1>
          <p style={{ fontSize: 14, color: "#8a8278", margin: "10px 0 18px", lineHeight: 1.5 }}>
            {message}
          </p>
          <button
            onClick={() => void signIn()}
            style={{
              padding: "9px 18px",
              borderRadius: 10,
              border: "none",
              background: "#2a2622",
              color: "#fff",
              fontSize: 13.5,
              fontWeight: 650,
              cursor: "pointer",
            }}
          >
            Try again
          </button>
          <p style={{ marginTop: 14 }}>
            <a href="/" style={{ fontSize: 13, color: "#8a8278" }}>
              Back to home
            </a>
          </p>
        </div>
      </div>
    );
  }
  return (
    <div style={{ ...center, color: "#8a8278", fontSize: 14 }}>Signing you in…</div>
  );
}

const isAuthCallback = window.location.pathname === "/auth/callback";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AuthProvider>{isAuthCallback ? <AuthCallback /> : <Root />}</AuthProvider>
  </React.StrictMode>
);
