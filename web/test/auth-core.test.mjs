// Unit tests for the auth core (web/src/auth/core.js): PKCE challenge shape,
// state validation, token storage round-trip, JWT payload decode, and the
// 401-refresh-retry policy. Runs under the repo's zero-dependency test runner
// (`node --test`, same as semantic/test/) on Node >= 20 — no vitest/jest, no
// build step. `npm test` in web/ executes this.

import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";

import {
  AUTH_TOKEN_STORAGE_KEY,
  PKCE_STORAGE_KEY,
  base64UrlEncode,
  buildAuthorizeUrl,
  buildHostedUiPasswordUrl,
  buildLogoutUrl,
  clearTokens,
  createPkcePair,
  decodeJwtPayload,
  fetchWithAuthRetry,
  idTokenRemainingMs,
  loadTokens,
  mergeRefreshedTokens,
  newState,
  parseCallbackParams,
  randomUrlSafeString,
  savePkce,
  saveTokens,
  singleFlight,
  takePkce,
  validateState,
} from "../src/auth/core.js";

/** Minimal Storage stand-in (localStorage/sessionStorage shape). */
function memoryStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  };
}

/** Build an unsigned JWT-shaped token around the given payload object. */
function fakeJwt(payload) {
  const b64url = (s) => Buffer.from(s).toString("base64url");
  return [b64url('{"alg":"none"}'), b64url(JSON.stringify(payload)), "sig"].join(".");
}

// --- PKCE -------------------------------------------------------------------

test("createPkcePair: verifier shape + S256 challenge match RFC 7636", async () => {
  const { verifier, challenge } = await createPkcePair();
  // 32 random bytes -> 43 base64url chars, the RFC minimum; charset is url-safe.
  assert.match(verifier, /^[A-Za-z0-9_-]{43,128}$/);
  // Challenge = BASE64URL(SHA256(verifier)), no padding.
  assert.match(challenge, /^[A-Za-z0-9_-]{43}$/);
  assert.equal(challenge, createHash("sha256").update(verifier).digest("base64url"));
  // Verifiers are random per call.
  const again = await createPkcePair();
  assert.notEqual(again.verifier, verifier);
});

test("base64UrlEncode emits no +, /, or padding", () => {
  const bytes = Uint8Array.from({ length: 64 }, (_, i) => (i * 37 + 250) % 256);
  const out = base64UrlEncode(bytes);
  assert.match(out, /^[A-Za-z0-9_-]+$/);
  assert.equal(Buffer.from(bytes).toString("base64url"), out);
});

test("randomUrlSafeString length tracks byte length", () => {
  assert.equal(randomUrlSafeString(32).length, 43);
  assert.equal(randomUrlSafeString(16).length, 22);
});

// --- authorize/logout URLs ----------------------------------------------------

test("buildAuthorizeUrl carries code + S256 + scope + state", () => {
  const url = new URL(
    buildAuthorizeUrl({
      domain: "uplift-x.auth.us-east-1.amazoncognito.com",
      clientId: "client123",
      redirectUri: "http://localhost:5173/auth/callback",
      scope: "openid email profile",
      state: "st_1",
      codeChallenge: "ch_1",
    }),
  );
  assert.equal(url.origin, "https://uplift-x.auth.us-east-1.amazoncognito.com");
  assert.equal(url.pathname, "/oauth2/authorize");
  assert.equal(url.searchParams.get("response_type"), "code");
  assert.equal(url.searchParams.get("client_id"), "client123");
  assert.equal(url.searchParams.get("redirect_uri"), "http://localhost:5173/auth/callback");
  assert.equal(url.searchParams.get("scope"), "openid email profile");
  assert.equal(url.searchParams.get("state"), "st_1");
  assert.equal(url.searchParams.get("code_challenge"), "ch_1");
  assert.equal(url.searchParams.get("code_challenge_method"), "S256");
});

test("buildHostedUiPasswordUrl(forgotPassword) carries the same code + S256 + PKCE grant", () => {
  const url = new URL(
    buildHostedUiPasswordUrl({
      action: "forgotPassword",
      domain: "uplift-x.auth.us-east-1.amazoncognito.com",
      clientId: "client123",
      redirectUri: "http://localhost:5173/auth/callback",
      scope: "openid email profile",
      state: "st_fp",
      codeChallenge: "ch_fp",
    }),
  );
  assert.equal(url.origin, "https://uplift-x.auth.us-east-1.amazoncognito.com");
  // The managed page — NOT /oauth2/authorize — but the same code+PKCE grant so
  // the existing /auth/callback exchange finishes it.
  assert.equal(url.pathname, "/forgotPassword");
  assert.equal(url.searchParams.get("response_type"), "code");
  assert.equal(url.searchParams.get("client_id"), "client123");
  assert.equal(url.searchParams.get("redirect_uri"), "http://localhost:5173/auth/callback");
  assert.equal(url.searchParams.get("scope"), "openid email profile");
  assert.equal(url.searchParams.get("state"), "st_fp");
  assert.equal(url.searchParams.get("code_challenge"), "ch_fp");
  assert.equal(url.searchParams.get("code_challenge_method"), "S256");
});

test("buildHostedUiPasswordUrl(changePassword) targets the /changePassword managed page", () => {
  const url = new URL(
    buildHostedUiPasswordUrl({
      action: "changePassword",
      domain: "d.example.com",
      clientId: "c1",
      redirectUri: "http://localhost:5173/auth/callback",
      scope: "openid email profile",
      state: "st_cp",
      codeChallenge: "ch_cp",
    }),
  );
  assert.equal(url.pathname, "/changePassword");
  assert.equal(url.searchParams.get("client_id"), "c1");
  assert.equal(url.searchParams.get("code_challenge_method"), "S256");
  assert.equal(url.searchParams.get("state"), "st_cp");
});

test("buildLogoutUrl points at /logout with client_id + logout_uri", () => {
  const url = new URL(
    buildLogoutUrl({ domain: "d.example.com", clientId: "c1", logoutUri: "http://localhost:5173/" }),
  );
  assert.equal(url.pathname, "/logout");
  assert.equal(url.searchParams.get("client_id"), "c1");
  assert.equal(url.searchParams.get("logout_uri"), "http://localhost:5173/");
});

// --- state validation ---------------------------------------------------------

test("validateState: exact match only, fails closed on empty/missing", () => {
  const st = newState();
  assert.equal(validateState(st, st), true);
  assert.equal(validateState(st, st + "x"), false);
  assert.equal(validateState("", ""), false);
  assert.equal(validateState(undefined, undefined), false);
  assert.equal(validateState(null, null), false);
});

test("parseCallbackParams reads code/state/error", () => {
  assert.deepEqual(parseCallbackParams("?code=c1&state=s1"), {
    code: "c1",
    state: "s1",
    error: "",
    errorDescription: "",
  });
  const denied = parseCallbackParams("?error=access_denied&error_description=nope");
  assert.equal(denied.error, "access_denied");
  assert.equal(denied.errorDescription, "nope");
  assert.equal(denied.code, "");
});

// --- token storage round-trip ---------------------------------------------------

test("token storage round-trip: save -> load -> clear, one key", () => {
  const s = memoryStorage();
  assert.equal(loadTokens(s), null);
  const tokens = { id_token: "a.b.c", access_token: "at", refresh_token: "rt" };
  saveTokens(s, tokens);
  assert.deepEqual(loadTokens(s), tokens);
  // Everything lives under the single documented key.
  assert.equal(typeof s.getItem(AUTH_TOKEN_STORAGE_KEY), "string");
  clearTokens(s);
  assert.equal(loadTokens(s), null);
});

test("loadTokens fails closed on garbage or missing id_token", () => {
  const s = memoryStorage();
  s.setItem(AUTH_TOKEN_STORAGE_KEY, "{not json");
  assert.equal(loadTokens(s), null);
  s.setItem(AUTH_TOKEN_STORAGE_KEY, JSON.stringify({ access_token: "at" }));
  assert.equal(loadTokens(s), null);
  s.setItem(AUTH_TOKEN_STORAGE_KEY, JSON.stringify({ id_token: "" }));
  assert.equal(loadTokens(s), null);
});

test("PKCE stash is take-once", () => {
  const s = memoryStorage();
  assert.equal(takePkce(s), null);
  savePkce(s, { verifier: "v1", state: "s1" });
  assert.equal(typeof s.getItem(PKCE_STORAGE_KEY), "string");
  assert.deepEqual(takePkce(s), { verifier: "v1", state: "s1" });
  // A second take returns nothing: the verifier is single-use.
  assert.equal(takePkce(s), null);
});

// --- JWT decode -----------------------------------------------------------------

test("decodeJwtPayload: base64url payload decode, no signature check", () => {
  const payload = {
    email: "owner@riverside.example",
    "custom:tenant_id": "tenant-1",
    token_use: "id",
    exp: 1900000000,
    name: "Zoë Q",
  };
  assert.deepEqual(decodeJwtPayload(fakeJwt(payload)), payload);
});

test("decodeJwtPayload fails closed on malformed input", () => {
  assert.equal(decodeJwtPayload("garbage"), null);
  assert.equal(decodeJwtPayload("a.b"), null);
  assert.equal(decodeJwtPayload("a.!!!.c"), null);
  assert.equal(decodeJwtPayload(null), null);
  assert.equal(decodeJwtPayload(12), null);
});

test("idTokenRemainingMs measures time to exp", () => {
  const exp = 2_000_000_000;
  const tok = fakeJwt({ exp });
  assert.equal(idTokenRemainingMs(tok, (exp - 60) * 1000), 60_000);
  assert.equal(idTokenRemainingMs(tok, (exp + 1) * 1000), -1000);
  assert.equal(idTokenRemainingMs("not-a-jwt", 0), 0);
  assert.equal(idTokenRemainingMs(fakeJwt({ email: "x" }), 0), 0);
});

// --- 401-refresh-retry policy ------------------------------------------------------

test("401 triggers exactly one refresh and one retry", async () => {
  let fetches = 0;
  let refreshes = 0;
  const res = await fetchWithAuthRetry(
    async () => {
      fetches += 1;
      return { status: fetches === 1 ? 401 : 200 };
    },
    async () => {
      refreshes += 1;
      return true;
    },
  );
  assert.equal(res.status, 200);
  assert.equal(fetches, 2);
  assert.equal(refreshes, 1);
});

test("a second 401 surfaces — never loops", async () => {
  let fetches = 0;
  let refreshes = 0;
  const res = await fetchWithAuthRetry(
    async () => {
      fetches += 1;
      return { status: 401 };
    },
    async () => {
      refreshes += 1;
      return true;
    },
  );
  assert.equal(res.status, 401);
  assert.equal(fetches, 2);
  assert.equal(refreshes, 1);
});

test("failed refresh skips the retry and surfaces the 401", async () => {
  let fetches = 0;
  const res = await fetchWithAuthRetry(
    async () => {
      fetches += 1;
      return { status: 401 };
    },
    async () => false,
  );
  assert.equal(res.status, 401);
  assert.equal(fetches, 1);
});

test("a throwing refresh is treated as a failed refresh", async () => {
  let fetches = 0;
  const res = await fetchWithAuthRetry(
    async () => {
      fetches += 1;
      return { status: 401 };
    },
    async () => {
      throw new Error("network down");
    },
  );
  assert.equal(res.status, 401);
  assert.equal(fetches, 1);
});

test("non-401 responses never refresh", async () => {
  let refreshes = 0;
  for (const status of [200, 403, 503]) {
    const res = await fetchWithAuthRetry(
      async () => ({ status }),
      async () => {
        refreshes += 1;
        return true;
      },
    );
    assert.equal(res.status, status);
  }
  assert.equal(refreshes, 0);
});

test("no refreshAuth wired (mock/unconfigured) means no retry", async () => {
  let fetches = 0;
  const res = await fetchWithAuthRetry(async () => {
    fetches += 1;
    return { status: 401 };
  }, undefined);
  assert.equal(res.status, 401);
  assert.equal(fetches, 1);
});

// --- rotation-tolerant refresh merge ---------------------------------------------

const CURRENT = { id_token: "old.id.tok", access_token: "old-at", refresh_token: "old-rt" };

test("mergeRefreshedTokens stores a ROTATED refresh token when the endpoint returns one", () => {
  const merged = mergeRefreshedTokens(CURRENT, {
    id_token: "new.id.tok",
    access_token: "new-at",
    refresh_token: "rotated-rt",
  });
  assert.deepEqual(merged, {
    id_token: "new.id.tok",
    access_token: "new-at",
    refresh_token: "rotated-rt",
  });
});

test("mergeRefreshedTokens keeps the current refresh/access tokens when the response omits them", () => {
  // Cognito's default (rotation off): the refresh grant returns only id+access.
  const merged = mergeRefreshedTokens(CURRENT, { id_token: "new.id.tok", access_token: "new-at" });
  assert.deepEqual(merged, {
    id_token: "new.id.tok",
    access_token: "new-at",
    refresh_token: "old-rt",
  });
  // Absent access_token keeps the old one too.
  const idOnly = mergeRefreshedTokens(CURRENT, { id_token: "new.id.tok" });
  assert.deepEqual(idOnly, {
    id_token: "new.id.tok",
    access_token: "old-at",
    refresh_token: "old-rt",
  });
});

test("mergeRefreshedTokens ignores empty-string rotations (never store an unusable token)", () => {
  const merged = mergeRefreshedTokens(CURRENT, {
    id_token: "new.id.tok",
    access_token: "",
    refresh_token: "",
  });
  assert.deepEqual(merged, {
    id_token: "new.id.tok",
    access_token: "old-at",
    refresh_token: "old-rt",
  });
});

test("mergeRefreshedTokens fails closed without a usable id_token", () => {
  assert.equal(mergeRefreshedTokens(CURRENT, { refresh_token: "rotated-rt" }), null);
  assert.equal(mergeRefreshedTokens(CURRENT, { id_token: "" }), null);
  assert.equal(mergeRefreshedTokens(CURRENT, { id_token: 42 }), null);
  assert.equal(mergeRefreshedTokens(CURRENT, null), null);
  assert.equal(mergeRefreshedTokens(CURRENT, "nope"), null);
});

test("a rotated token round-trips through storage for the NEXT refresh", () => {
  const s = memoryStorage();
  saveTokens(s, CURRENT);
  const merged = mergeRefreshedTokens(loadTokens(s), {
    id_token: "new.id.tok",
    refresh_token: "rotated-rt",
  });
  saveTokens(s, merged);
  // The next refresh reads the rotated token, never the dead predecessor.
  assert.equal(loadTokens(s).refresh_token, "rotated-rt");
});

// --- single-flight refresh ----------------------------------------------------------

test("singleFlight: concurrent callers share ONE in-flight invocation", async () => {
  let calls = 0;
  let release;
  const gate = new Promise((r) => {
    release = r;
  });
  const fn = singleFlight(async () => {
    calls += 1;
    await gate;
    return "refreshed";
  });
  // Three "concurrent 401s" all grab the same promise.
  const a = fn();
  const b = fn();
  const c = fn();
  assert.equal(a, b);
  assert.equal(b, c);
  release();
  assert.deepEqual(await Promise.all([a, b, c]), ["refreshed", "refreshed", "refreshed"]);
  assert.equal(calls, 1);
});

test("singleFlight: the slot clears after settle, so later calls re-invoke", async () => {
  let calls = 0;
  const fn = singleFlight(async () => {
    calls += 1;
    return calls;
  });
  assert.equal(await fn(), 1);
  assert.equal(await fn(), 2);
  assert.equal(calls, 2);
});

test("singleFlight: a rejection is shared by concurrent callers, then clears the slot", async () => {
  let calls = 0;
  const fn = singleFlight(async () => {
    calls += 1;
    if (calls === 1) throw new Error("token endpoint down");
    return "recovered";
  });
  const p1 = fn();
  const p2 = fn();
  await assert.rejects(p1, /token endpoint down/);
  await assert.rejects(p2, /token endpoint down/);
  assert.equal(calls, 1);
  // The failed flight is not cached: the next call tries again.
  assert.equal(await fn(), "recovered");
  assert.equal(calls, 2);
});

test("singleFlight wraps a synchronous throw into the shared rejected promise", async () => {
  const fn = singleFlight(() => {
    throw new Error("sync boom");
  });
  await assert.rejects(fn(), /sync boom/);
});
