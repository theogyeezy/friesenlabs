// Pure, dependency-free helpers for the Cognito Hosted UI login flow:
// PKCE (S256), state, JWT payload decode, token/PKCE storage, and the
// 401-refresh-retry policy used by the API client.
//
// DELIBERATELY plain ESM JavaScript, not TypeScript: the unit tests in
// web/test/ run this file directly under `node --test` (the repo's zero-dep
// test runner, see semantic/test/) on Node 20 in CI, where node:test cannot
// execute .ts files. Everything here is platform-neutral — Web Crypto,
// atob/btoa, TextEncoder/TextDecoder, and URLSearchParams exist in both
// evergreen browsers and Node >= 20. No DOM, no import.meta.env, no network.
// The browser-only wiring (env, redirects, fetch) lives in ./cognito.ts.

/**
 * localStorage key holding the token set as one JSON blob
 * ({ id_token, access_token, refresh_token }).
 *
 * TRADEOFF (XSS): localStorage is readable by any script running on the
 * origin, so a successful XSS could exfiltrate tokens. We accept that here
 * because (a) the SPA only renders untrusted HTML through SafeHtml/DOMPurify,
 * (b) the ID token is short-lived (Cognito default 60 min) and the refresh
 * token is revocable server-side, and (c) the safer alternative (httpOnly
 * session cookie) needs a backend session layer this app doesn't have.
 * Revisit if the threat model changes.
 */
export const AUTH_TOKEN_STORAGE_KEY = "uplift_auth_tokens";

/** sessionStorage key holding the in-flight PKCE { verifier, state } pair. */
export const PKCE_STORAGE_KEY = "uplift_auth_pkce";

/**
 * Encode bytes as base64url (RFC 4648 §5: +/ -> -_, no padding).
 * @param {Uint8Array} bytes
 * @returns {string}
 */
export function base64UrlEncode(bytes) {
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * Cryptographically random base64url string (for PKCE verifiers and state).
 * @param {number} [byteLength]
 * @returns {string}
 */
export function randomUrlSafeString(byteLength = 32) {
  const bytes = new Uint8Array(byteLength);
  globalThis.crypto.getRandomValues(bytes);
  return base64UrlEncode(bytes);
}

/**
 * Create a PKCE verifier + S256 code challenge (RFC 7636). 32 random bytes
 * encode to a 43-char verifier, the RFC minimum.
 * @returns {Promise<{ verifier: string, challenge: string }>}
 */
export async function createPkcePair() {
  const verifier = randomUrlSafeString(32);
  const digest = await globalThis.crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(verifier),
  );
  return { verifier, challenge: base64UrlEncode(new Uint8Array(digest)) };
}

/** Random opaque state for the authorize round-trip (CSRF binding). */
export function newState() {
  return randomUrlSafeString(16);
}

/**
 * The state returned on the callback must exactly equal the one we stashed
 * before redirecting. Empty/missing on either side fails closed.
 * @param {unknown} returnedState
 * @param {unknown} expectedState
 * @returns {boolean}
 */
export function validateState(returnedState, expectedState) {
  return (
    typeof returnedState === "string" &&
    returnedState.length > 0 &&
    returnedState === expectedState
  );
}

/**
 * Hosted UI GET /oauth2/authorize URL (authorization code + PKCE S256).
 * @param {{ domain: string, clientId: string, redirectUri: string, scope: string, state: string, codeChallenge: string }} p
 * @returns {string}
 */
export function buildAuthorizeUrl(p) {
  const q = new URLSearchParams({
    client_id: p.clientId,
    response_type: "code",
    scope: p.scope,
    redirect_uri: p.redirectUri,
    state: p.state,
    code_challenge: p.codeChallenge,
    code_challenge_method: "S256",
  });
  return `https://${p.domain}/oauth2/authorize?${q.toString()}`;
}

/**
 * Hosted UI GET /logout URL. logout_uri must exactly match a registered
 * logout URL (the site roots, with trailing slash — see infra/variables.tf).
 * @param {{ domain: string, clientId: string, logoutUri: string }} p
 * @returns {string}
 */
export function buildLogoutUrl(p) {
  const q = new URLSearchParams({ client_id: p.clientId, logout_uri: p.logoutUri });
  return `https://${p.domain}/logout?${q.toString()}`;
}

/**
 * Parse the ?code=&state= (or ?error=) query the Hosted UI redirects back with.
 * @param {string} search
 * @returns {{ code: string, state: string, error: string, errorDescription: string }}
 */
export function parseCallbackParams(search) {
  const q = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  return {
    code: q.get("code") ?? "",
    state: q.get("state") ?? "",
    error: q.get("error") ?? "",
    errorDescription: q.get("error_description") ?? "",
  };
}

/**
 * Decode a JWT payload (middle segment, base64url). NO signature check —
 * the browser only reads display claims (email, custom:tenant_id, exp);
 * verification happens server-side in api/auth.py against the Cognito JWKS.
 * Returns null for anything malformed.
 * @param {unknown} jwt
 * @returns {Record<string, unknown> | null}
 */
export function decodeJwtPayload(jwt) {
  if (typeof jwt !== "string") return null;
  const parts = jwt.split(".");
  if (parts.length !== 3) return null;
  try {
    const b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
    const bin = atob(padded);
    const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
    const payload = JSON.parse(new TextDecoder().decode(bytes));
    return payload && typeof payload === "object" ? payload : null;
  } catch {
    return null;
  }
}

/**
 * Milliseconds until the token's `exp`. <= 0 means expired or undecodable.
 * @param {unknown} idToken
 * @param {number} [nowMs]
 * @returns {number}
 */
export function idTokenRemainingMs(idToken, nowMs = Date.now()) {
  const claims = decodeJwtPayload(idToken);
  if (!claims || typeof claims.exp !== "number") return 0;
  return claims.exp * 1000 - nowMs;
}

// --- storage round-trips (storage is injected: localStorage/sessionStorage in
// --- the app, a Map-backed stub in tests) ----------------------------------

/**
 * @param {{ setItem(k: string, v: string): void }} storage
 * @param {{ id_token: string, access_token?: string, refresh_token?: string }} tokens
 */
export function saveTokens(storage, tokens) {
  storage.setItem(AUTH_TOKEN_STORAGE_KEY, JSON.stringify(tokens));
}

/**
 * Load the stored token set; null when absent, unparseable, or missing the
 * id_token (fail closed, never throw).
 * @param {{ getItem(k: string): string | null }} storage
 * @returns {{ id_token: string, access_token?: string, refresh_token?: string } | null}
 */
export function loadTokens(storage) {
  try {
    const raw = storage.getItem(AUTH_TOKEN_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    if (typeof parsed.id_token !== "string" || parsed.id_token === "") return null;
    return parsed;
  } catch {
    return null;
  }
}

/** @param {{ removeItem(k: string): void }} storage */
export function clearTokens(storage) {
  storage.removeItem(AUTH_TOKEN_STORAGE_KEY);
}

/**
 * Stash the PKCE verifier + state across the Hosted UI redirect.
 * @param {{ setItem(k: string, v: string): void }} storage
 * @param {{ verifier: string, state: string }} pkce
 */
export function savePkce(storage, pkce) {
  storage.setItem(PKCE_STORAGE_KEY, JSON.stringify(pkce));
}

/**
 * Read-and-delete the stashed PKCE pair (take-once: a verifier is never
 * reusable). Null when absent or malformed.
 * @param {{ getItem(k: string): string | null, removeItem(k: string): void }} storage
 * @returns {{ verifier: string, state: string } | null}
 */
export function takePkce(storage) {
  try {
    const raw = storage.getItem(PKCE_STORAGE_KEY);
    storage.removeItem(PKCE_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed.verifier !== "string" || typeof parsed.state !== "string") {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

/**
 * Merge a token-endpoint refresh response into the current stored token set.
 *
 * ROTATION-TOLERANT: when the endpoint returns a new refresh_token (Cognito
 * with refresh-token rotation enabled rotates on every refresh grant), the
 * rotated token REPLACES the stored one — keeping the old token after a
 * rotation would strand the session on the next refresh, because a rotated
 * predecessor is invalidated server-side. When the response carries no
 * refresh_token (rotation off — Cognito's default), the current one is kept.
 * Same keep-on-absent rule for access_token.
 *
 * Returns the merged set to store, or null when the response carries no usable
 * id_token (the refresh failed; nothing should be stored).
 * @param {{ id_token: string, access_token?: string, refresh_token?: string }} current
 * @param {unknown} data  parsed token-endpoint response body
 * @returns {{ id_token: string, access_token?: string, refresh_token?: string } | null}
 */
export function mergeRefreshedTokens(current, data) {
  if (!data || typeof data !== "object") return null;
  const d = /** @type {Record<string, unknown>} */ (data);
  if (typeof d.id_token !== "string" || d.id_token === "") return null;
  return {
    id_token: d.id_token,
    access_token:
      typeof d.access_token === "string" && d.access_token !== ""
        ? d.access_token
        : current.access_token,
    refresh_token:
      typeof d.refresh_token === "string" && d.refresh_token !== ""
        ? d.refresh_token
        : current.refresh_token,
  };
}

/**
 * Single-flight combinator: while a call is in flight, every concurrent call
 * shares the SAME promise instead of issuing its own. The slot clears when the
 * flight settles (resolve or reject), so the next call starts fresh. Used for
 * token refresh: N requests hitting 401 at once must produce exactly one
 * token-endpoint round-trip — critical under rotation, where a second
 * concurrent refresh with the just-rotated (now invalid) token would kill the
 * session.
 * @template T
 * @param {() => Promise<T>} fn
 * @returns {() => Promise<T>}
 */
export function singleFlight(fn) {
  /** @type {Promise<T> | null} */
  let inflight = null;
  return () => {
    if (inflight === null) {
      inflight = Promise.resolve()
        .then(fn)
        .finally(() => {
          inflight = null;
        });
    }
    return inflight;
  };
}

/**
 * The 401 policy for authenticated API calls: run the request; on a 401,
 * make ONE refresh attempt and, if it succeeds, retry ONCE (the doFetch
 * closure rebuilds headers so the retry carries the refreshed token). A
 * second 401 — or a failed/absent refresh — returns the 401 response for the
 * caller to surface. Never loops.
 * @template {{ status: number }} R
 * @param {() => Promise<R>} doFetch
 * @param {(() => Promise<boolean>) | undefined} [refreshAuth]
 * @returns {Promise<R>}
 */
export async function fetchWithAuthRetry(doFetch, refreshAuth) {
  let res = await doFetch();
  if (res && res.status === 401 && refreshAuth) {
    let refreshed = false;
    try {
      refreshed = await refreshAuth();
    } catch {
      refreshed = false;
    }
    if (refreshed) res = await doFetch();
  }
  return res;
}
