import React from "react";
import ReactDOM from "react-dom/client";

import "./styles.css";
import "./landing.css";

// Side-effect import: registers all shared globals (FL_DATA, store, Icon, the
// AI helper, charts, panels, tweaks, and every screen component) onto window in
// the prototype's original load order. Must run before <App/> renders.
import "./globals";

import App from "./app";

// Offline demo surfaces (?view=dashboard-demo / ?view=safehtml-demo) — MOCK
// BUILDS ONLY. The gate below is BUILD-TIME: Vite statically replaces
// import.meta.env.VITE_API_MOCK, so in real builds (VITE_API_MOCK=0) the
// branch folds away and rollup never emits these chunks. That keeps the
// __pwned XSS-probe payloads (SafeHtmlDemo + Demo.tsx's malicious spec) and
// the demo renderer fixtures out of production bundles entirely; the demo
// ?view= ids simply fall through to the gated app shell in real mode.
let DashboardDemoLazy: React.LazyExoticComponent<React.ComponentType> | null = null;
let SafeHtmlDemoLazy: React.LazyExoticComponent<React.ComponentType> | null = null;
if (import.meta.env.VITE_API_MOCK !== "0" && import.meta.env.VITE_API_MOCK !== "false") {
  DashboardDemoLazy = React.lazy(() => import("./dashboard/Demo"));
  SafeHtmlDemoLazy = React.lazy(() => import("./dev/SafeHtmlDemo"));
}

// API-wired surfaces (Phase 9b). Each reads through the typed ApiClient, which
// defaults to mock mode so these mount fully offline. They share the same
// ?view= seam pattern as the dashboard demo and never touch the converted
// (@ts-nocheck) shell.
import GreenlightQueue from "./api/GreenlightQueue";
import ChatDock from "./api/ChatDock";
import DashboardView from "./api/DashboardView";
import IntegrationsPanel from "./api/IntegrationsPanel";
import PipelineBoard from "./api/PipelineBoard";
import ContactsDirectory from "./api/ContactsDirectory";
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

// The focused sign-in gate for deep links into gated surfaces (?view=…).
// A signed-out visitor who deep-links into the app gets this minimal gate —
// not the full marketing page — with the Sign in control wired to the SPA's
// PKCE signIn() (auth/cognito.ts). Never a bare Hosted-UI URL: without the
// stashed PKCE state the callback would fail the state (CSRF) check.
function SignInGate() {
  const auth = useAuth();
  const center: React.CSSProperties = {
    display: "grid",
    placeItems: "center",
    minHeight: "100vh",
    fontFamily: "system-ui, sans-serif",
  };
  return (
    <div style={center} data-testid="signin-gate">
      <div style={{ textAlign: "center", maxWidth: 380, padding: 24 }}>
        <h1 style={{ fontSize: 18, fontWeight: 700 }}>Sign in to continue</h1>
        <p style={{ fontSize: 14, color: "#8a8278", margin: "10px 0 18px", lineHeight: 1.5 }}>
          This part of Uplift needs your workspace session.
        </p>
        {/* a.lp-signin is the sign-in-gate contract the auth e2e asserts. */}
        <a
          className="lp-signin"
          href="/"
          onClick={(e) => {
            e.preventDefault();
            auth.signIn();
          }}
          style={{
            display: "inline-block",
            padding: "9px 18px",
            borderRadius: 10,
            background: "#2a2622",
            color: "#fff",
            fontSize: 13.5,
            fontWeight: 650,
            textDecoration: "none",
            cursor: "pointer",
          }}
        >
          Sign in
        </a>
        <p style={{ marginTop: 14 }}>
          <a href="/" style={{ fontSize: 13, color: "#8a8278" }}>
            Back to home
          </a>
        </p>
      </div>
    </div>
  );
}

// Sign-in gate. Only active when Cognito is configured AND the API is real
// (isAuthEnabled): signed-in users get the wrapped surface; signed-out
// visitors get the marketing landing (its Sign in / Get started controls are
// the conversion paths) — except on gated ?view= seams, where the SPA takes
// precedence over the marketing page and the focused SignInGate renders
// (#120). Inert in mock/unconfigured builds (dev, tests, Playwright).
function Gated({ children, seam = false }: { children: React.ReactElement; seam?: boolean }) {
  const auth = useAuth();
  if (!isAuthEnabled() || auth.isAuthenticated) return children;
  return seam ? <SignInGate /> : <Landing onSignIn={auth.signIn} />;
}

function Root() {
  switch (view) {
    // Offline demo surfaces — no API client, safe ungated. Mock builds only:
    // in real builds the lazy components are null and these ids fall through
    // to the default gated shell.
    case "dashboard-demo":
      if (DashboardDemoLazy) {
        return (
          <React.Suspense fallback={null}>
            <DashboardDemoLazy />
          </React.Suspense>
        );
      }
      break;
    case "safehtml-demo":
      if (SafeHtmlDemoLazy) {
        return (
          <React.Suspense fallback={null}>
            <SafeHtmlDemoLazy />
          </React.Suspense>
        );
      }
      break;
    // Pre-auth by design: the signup funnel runs before any tenant or token exists.
    case "signup":
      return <SignupFlow />;
    // API-wired surfaces — gated like the default shell, or a real build would
    // mount them signed-out and 401 on every call. seam: a deep link gets the
    // focused SignInGate when signed out, never the marketing page.
    case "greenlight":
      return (
        <Gated seam>
          <GreenlightQueue />
        </Gated>
      );
    case "chat":
      return (
        <Gated seam>
          <ChatDock />
        </Gated>
      );
    case "dashboard":
      return (
        <Gated seam>
          <DashboardView />
        </Gated>
      );
    case "integrations":
      return (
        <Gated seam>
          <IntegrationsPanel />
        </Gated>
      );
    case "pipeline":
      return (
        <Gated seam>
          <PipelineBoard />
        </Gated>
      );
    case "contacts":
      return (
        <Gated seam>
          <ContactsDirectory />
        </Gated>
      );
  }
  // Default shell — also the fall-through for demo ?view= ids in real builds.
  return (
    <Gated>
      <App />
    </Gated>
  );
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

// Tolerate a trailing slash: Amplify Hosting 301s extensionless paths to the
// slashed form (/auth/callback?code= -> /auth/callback/?code=), preserving the
// query. A strict equality check here would skip the exchange entirely.
const isAuthCallback = /^\/auth\/callback\/?$/.test(window.location.pathname);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AuthProvider>{isAuthCallback ? <AuthCallback /> : <Root />}</AuthProvider>
  </React.StrictMode>
);
