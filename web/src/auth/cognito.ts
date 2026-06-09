// Cognito Hosted UI sign-in for the SPA: authorization code + PKCE (S256),
// hand-rolled against the OAuth2 endpoints — no aws-amplify, no oidc-client-ts,
// no new runtime dependencies.
//
// Flow:
//   signIn()          stash {verifier, state} in sessionStorage, redirect to
//                     GET https://{domain}/oauth2/authorize
//   handleCallback()  on /auth/callback: validate state, exchange the code at
//                     POST https://{domain}/oauth2/token (x-www-form-urlencoded),
//                     store the tokens
//   refreshTokens()   grant_type=refresh_token at the same token endpoint
//   signOut()         clear storage, redirect to GET https://{domain}/logout
//
// The API requires the **ID token** as the bearer (api/auth.py checks
// aud == client_id and token_use == "id"; an access token is rejected), so
// getValidIdToken() is what the API client attaches.
//
// Tokens live in localStorage under ONE key (AUTH_TOKEN_STORAGE_KEY in
// core.js, where the XSS tradeoff is documented). The pure logic (PKCE, JWT
// decode, storage shapes, retry policy) lives in ./core.js so `node --test`
// can unit-test it without a build step; this module adds only the browser
// wiring: env, redirects, fetch, and change notification.
//
// INERT GUARANTEE: when Cognito is unconfigured or the app runs in mock mode
// (VITE_API_MOCK semantics shared with api/client.ts), every entry point here
// is a no-op with zero network calls, so local dev and Playwright behave
// exactly as before.

import {
  buildAuthorizeUrl,
  buildLogoutUrl,
  clearTokens,
  createPkcePair,
  idTokenRemainingMs,
  loadTokens,
  newState,
  parseCallbackParams,
  savePkce,
  saveTokens,
  takePkce,
  validateState,
} from "./core.js";

/** The token set persisted in localStorage (one JSON blob, one key). */
export interface StoredTokens {
  id_token: string;
  access_token?: string;
  refresh_token?: string;
}

/** Scopes granted to the app client (infra/modules/auth/main.tf). */
const SCOPES = "openid email profile";

/** Refresh proactively when the ID token is within 5 minutes of expiry. */
const REFRESH_WINDOW_MS = 5 * 60 * 1000;

/** Fired on window whenever the stored token set changes (login/refresh/logout). */
export const AUTH_CHANGED_EVENT = "uplift:auth-changed";

interface CognitoEnv {
  domain: string;
  clientId: string;
  region: string;
  mock: boolean;
}

/**
 * Resolve Cognito config from the Vite environment. The Hosted UI domain is a
 * bare host (no scheme). Mock detection mirrors api/client.ts: mock unless
 * VITE_API_MOCK is explicitly "0"/"false" (same rule as analytics/posthog.ts).
 */
function cognitoEnv(): CognitoEnv {
  const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env ?? {};
  const mockFlag = env.VITE_API_MOCK;
  const mock = mockFlag === undefined ? true : !(mockFlag === "0" || mockFlag === "false");
  return {
    domain: env.VITE_COGNITO_DOMAIN ?? "",
    clientId: env.VITE_COGNITO_CLIENT_ID ?? "",
    region: env.VITE_COGNITO_REGION ?? "",
    mock,
  };
}

/** True when the build carries a Hosted UI domain + client id. */
export function isAuthConfigured(): boolean {
  const e = cognitoEnv();
  return e.domain !== "" && e.clientId !== "";
}

/**
 * The sign-in gate condition: Cognito configured AND the API is real (not
 * mock). When false, the whole auth layer is inert and the app behaves
 * exactly as the historical mock build.
 */
export function isAuthEnabled(): boolean {
  return isAuthConfigured() && !cognitoEnv().mock;
}

/** Registered callback URL: {origin}/auth/callback (see infra/variables.tf). */
function redirectUri(): string {
  return `${window.location.origin}/auth/callback`;
}

/** Registered logout URL: the site root, with trailing slash. */
function logoutUri(): string {
  return `${window.location.origin}/`;
}

function notifyAuthChanged(): void {
  window.dispatchEvent(new Event(AUTH_CHANGED_EVENT));
}

/** The stored token set, or null when signed out. No freshness check. */
export function getStoredTokens(): StoredTokens | null {
  return loadTokens(window.localStorage) as StoredTokens | null;
}

/** The stored ID token (possibly stale), or null when signed out. */
export function getIdToken(): string | null {
  const tokens = getStoredTokens();
  return tokens ? tokens.id_token : null;
}

/**
 * Begin sign-in: mint a PKCE pair + state, stash them in sessionStorage, and
 * redirect to the Hosted UI authorize endpoint. No-op when auth is disabled.
 */
export async function signIn(): Promise<void> {
  if (!isAuthEnabled()) return;
  const { domain, clientId } = cognitoEnv();
  const { verifier, challenge } = await createPkcePair();
  const state = newState();
  savePkce(window.sessionStorage, { verifier, state });
  window.location.assign(
    buildAuthorizeUrl({
      domain,
      clientId,
      redirectUri: redirectUri(),
      scope: SCOPES,
      state,
      codeChallenge: challenge,
    }),
  );
}

// One-shot guard: authorization codes are single-use and React.StrictMode
// double-invokes effects in dev, so both invocations share one exchange.
let _callbackOnce: Promise<void> | null = null;

/**
 * Process the /auth/callback redirect: validate state against the stashed
 * PKCE pair, exchange the code for tokens, persist them. Rejects with a
 * user-presentable Error on any failure. Idempotent per page load.
 */
export function handleCallback(): Promise<void> {
  if (_callbackOnce === null) _callbackOnce = exchangeCallbackCode();
  return _callbackOnce;
}

// Canned copy per OAuth error code. error_description from the query string is
// NEVER rendered: it's attacker-influenceable text that would display under our
// origin (content spoofing) — anyone can craft /auth/callback?error=...&error_description=...
const OAUTH_ERROR_COPY: Record<string, string> = {
  access_denied: "Sign-in was cancelled.",
  invalid_request: "The sign-in request was invalid. Please try again.",
  unauthorized_client: "This app isn't authorized for sign-in right now.",
  unsupported_response_type: "The sign-in request was invalid. Please try again.",
  invalid_scope: "The sign-in request was invalid. Please try again.",
  server_error: "The sign-in service had a problem. Please try again.",
  temporarily_unavailable: "The sign-in service is temporarily unavailable. Please try again.",
};

async function exchangeCallbackCode(): Promise<void> {
  const params = parseCallbackParams(window.location.search);
  // Take-once FIRST so the {verifier, state} stash is consumed on every
  // callback outcome (error / no-code / mismatch), not just the happy path.
  const pkce = takePkce(window.sessionStorage);
  if (params.error) {
    throw new Error(OAUTH_ERROR_COPY[params.error] ?? "Sign-in didn't complete. Please try again.");
  }
  if (!params.code) throw new Error("The sign-in response carried no authorization code.");
  if (!pkce || !validateState(params.state, pkce.state)) {
    throw new Error("Sign-in state didn't match. Please try signing in again.");
  }
  const { domain, clientId } = cognitoEnv();
  const res = await fetch(`https://${domain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: clientId,
      code: params.code,
      redirect_uri: redirectUri(),
      code_verifier: pkce.verifier,
    }).toString(),
  });
  if (!res.ok) throw new Error(`Token exchange failed (${res.status}).`);
  const data = (await res.json()) as Partial<StoredTokens>;
  if (typeof data.id_token !== "string" || data.id_token === "") {
    throw new Error("Token exchange returned no ID token.");
  }
  saveTokens(window.localStorage, {
    id_token: data.id_token,
    access_token: data.access_token,
    refresh_token: data.refresh_token,
  });
  notifyAuthChanged();
}

// Single-flight: concurrent callers (interval tick + an API 401) share one
// refresh request instead of racing the token endpoint.
let _refreshing: Promise<boolean> | null = null;

/**
 * grant_type=refresh_token at the token endpoint. Resolves true when a new
 * ID token was stored. Never throws; failures resolve false.
 */
export function refreshTokens(): Promise<boolean> {
  if (_refreshing === null) {
    _refreshing = doRefresh().finally(() => {
      _refreshing = null;
    });
  }
  return _refreshing;
}

async function doRefresh(): Promise<boolean> {
  if (!isAuthEnabled()) return false;
  const tokens = getStoredTokens();
  if (!tokens || !tokens.refresh_token) return false;
  const { domain, clientId } = cognitoEnv();
  try {
    const res = await fetch(`https://${domain}/oauth2/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "refresh_token",
        client_id: clientId,
        refresh_token: tokens.refresh_token,
      }).toString(),
    });
    if (!res.ok) return false;
    const data = (await res.json()) as Partial<StoredTokens>;
    if (typeof data.id_token !== "string" || data.id_token === "") return false;
    // Cognito does not rotate the refresh token on this grant; keep the old one.
    saveTokens(window.localStorage, {
      id_token: data.id_token,
      access_token: data.access_token ?? tokens.access_token,
      refresh_token: tokens.refresh_token,
    });
    notifyAuthChanged();
    return true;
  } catch {
    return false;
  }
}

/**
 * The token the API client attaches per request: the stored ID token if it
 * has more than 5 minutes left, otherwise the result of one refresh attempt.
 * A failed refresh signs out LOCALLY (clear + notify — no redirect, so there
 * is no sign-in loop) and resolves null.
 */
export async function getValidIdToken(): Promise<string | null> {
  const tokens = getStoredTokens();
  if (!tokens) return null;
  if (idTokenRemainingMs(tokens.id_token) > REFRESH_WINDOW_MS) return tokens.id_token;
  if (await refreshTokens()) {
    const fresh = getStoredTokens();
    return fresh ? fresh.id_token : null;
  }
  localSignOut();
  return null;
}

/**
 * 401-retry hook for the API client: one refresh attempt; failure drops the
 * local session so the UI flips to signed-out when the 401 surfaces.
 */
export async function refreshAuthForRetry(): Promise<boolean> {
  const ok = await refreshTokens();
  if (!ok) localSignOut();
  return ok;
}

/** Clear the stored session and notify, without leaving the page. */
export function localSignOut(): void {
  clearTokens(window.localStorage);
  notifyAuthChanged();
}

/**
 * Full sign-out: best-effort revoke of the refresh token (it would otherwise
 * stay valid for its full 30-day window — /logout only kills the Cognito
 * session cookie), then clear local storage and redirect to the Hosted UI
 * /logout. Local-only when auth is disabled.
 */
export function signOut(): void {
  const enabled = isAuthEnabled();
  const { domain, clientId } = cognitoEnv();
  const refreshToken = getStoredTokens()?.refresh_token;
  if (enabled && refreshToken) {
    // keepalive lets the request survive the imminent navigation; failures are ignored.
    void fetch(`https://${domain}/oauth2/revoke`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ token: refreshToken, client_id: clientId }).toString(),
      keepalive: true,
    }).catch(() => undefined);
  }
  localSignOut();
  if (!enabled) return;
  window.location.assign(buildLogoutUrl({ domain, clientId, logoutUri: logoutUri() }));
}
