// React context over the Cognito session (auth/cognito.ts). Exposes the
// decoded identity ({isAuthenticated, idToken, claims, email, tenantId}) plus
// signIn/signOut, and keeps itself current:
//   - listens for AUTH_CHANGED_EVENT (login, refresh, logout) and cross-tab
//     "storage" events;
//   - ticks getValidIdToken() once on mount and every minute, which refreshes
//     the ID token when it is within 5 minutes of expiry (rotation-tolerant:
//     a rotated refresh_token from the token endpoint replaces the stored
//     one) and, when the refresh fails, ends the session via sessionExpired()
//     — clear + return to the sign-in route (no Hosted-UI redirect, no loop).
//
// When auth is disabled (mock mode / Cognito unconfigured) the provider is
// fully inert: no listeners, no timers, no network, isAuthenticated=false —
// so dev, unit tests, and Playwright behave exactly as before.

import React from "react";
import {
  AUTH_CHANGED_EVENT,
  getIdToken,
  getValidIdToken,
  isAuthEnabled,
  signIn as cognitoSignIn,
  signOut as cognitoSignOut,
} from "./cognito";
import { decodeJwtPayload, idTokenRemainingMs } from "./core.js";

const { createContext, useContext, useEffect, useMemo, useState } = React;

export interface AuthValue {
  isAuthenticated: boolean;
  /** The raw ID token (the bearer the API requires), null when signed out. */
  idToken: string | null;
  /**
   * Decoded ID-token payload. Base64 decode ONLY — the browser cannot verify
   * the signature and does not try; api/auth.py verifies against the JWKS.
   * Used purely for display (email, name) and routing context.
   */
  claims: Record<string, unknown> | null;
  email: string | null;
  /** The custom:tenant_id claim (the trust-rule tenant), null when absent. */
  tenantId: string | null;
  signIn: () => void;
  signOut: () => void;
}

const signedOut: AuthValue = {
  isAuthenticated: false,
  idToken: null,
  claims: null,
  email: null,
  tenantId: null,
  signIn: () => {
    void cognitoSignIn();
  },
  signOut: () => {
    cognitoSignOut();
  },
};

// Default value doubles as the no-provider fallback so useAuth() never throws.
const AuthContext = createContext<AuthValue>(signedOut);

export function useAuth(): AuthValue {
  return useContext(AuthContext);
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [idToken, setIdToken] = useState<string | null>(() =>
    isAuthEnabled() ? getIdToken() : null,
  );

  useEffect(() => {
    if (!isAuthEnabled()) return;
    const sync = () => setIdToken(getIdToken());
    window.addEventListener(AUTH_CHANGED_EVENT, sync);
    window.addEventListener("storage", sync);
    // Auto-refresh tick: refreshes inside the 5-minute window, clears the
    // session on refresh failure (see getValidIdToken), then syncs state.
    const tick = () => {
      void getValidIdToken().then(sync, sync);
    };
    tick();
    const iv = window.setInterval(tick, 60_000);
    return () => {
      window.removeEventListener(AUTH_CHANGED_EVENT, sync);
      window.removeEventListener("storage", sync);
      window.clearInterval(iv);
    };
  }, []);

  const value = useMemo<AuthValue>(() => {
    const claims = idToken ? decodeJwtPayload(idToken) : null;
    const live = claims !== null && idTokenRemainingMs(idToken) > 0;
    if (!live) return signedOut;
    return {
      isAuthenticated: true,
      idToken,
      claims,
      email: typeof claims.email === "string" ? claims.email : null,
      tenantId:
        typeof claims["custom:tenant_id"] === "string"
          ? (claims["custom:tenant_id"] as string)
          : null,
      signIn: signedOut.signIn,
      signOut: signedOut.signOut,
    };
  }, [idToken]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export default AuthProvider;
