// Tenant security context for Cube (Build Guide Phase 3, Step 21).
// This is the defense-in-depth layer OVER Postgres RLS: every query Cube runs is force-filtered
// to the tenant derived from the verified JWT. Isolation is enforced once, for every consumer,
// instead of every generated query having to get it right.
//
// THE TRUST RULE (CLAUDE.md hard constraint #6): the tenant arrives ONLY inside a signed HS256
// JWT minted by the API/tool plane from the verified Cognito claim (the Python mirror is
// agents/tools/cube_client.py). checkAuth below verifies that token itself — unsigned, expired,
// or wrongly-signed tokens are rejected — and queryRewrite refuses any context whose tenant is
// missing or shaped like a forgery.
//
// Pulled into its own module so it is unit-testable without a running Cube.

const crypto = require('node:crypto');

// Tenants are UUIDs in this system (db/schema.sql: tenant_id uuid), but validate by safe charset
// (parity with the Python guard) so a forged context — object, array, empty/whitespace string,
// quote/wildcard junk — can never reach a filter value.
const TENANT_ID_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]*$/;

/** True only for a sane, non-empty tenant-id string. Anything else is missing or forged. */
function isValidTenantId(tenantId) {
  return typeof tenantId === 'string' && TENANT_ID_RE.test(tenantId);
}

/** Throw unless the security context carries a valid tenant; return the tenant id. */
function requireTenant(securityContext) {
  if (!securityContext || securityContext.tenant_id === undefined || securityContext.tenant_id === null) {
    throw new Error('no tenant');
  }
  if (!isValidTenantId(securityContext.tenant_id)) {
    throw new Error('forged tenant context');
  }
  return securityContext.tenant_id;
}

/** Return the distinct cube names referenced by a Cube query (e.g. "Deals" from "Deals.count"). */
function cubesInQuery(query) {
  const members = [];
  for (const m of query.measures || []) members.push(m);
  for (const d of query.dimensions || []) members.push(d);
  for (const f of query.filters || []) if (f && f.member) members.push(f.member);
  for (const td of query.timeDimensions || []) if (td && td.dimension) members.push(td.dimension);
  for (const o of Object.keys(query.order || {})) members.push(o);

  const cubes = new Set();
  for (const m of members) {
    if (typeof m === 'string' && m.includes('.')) cubes.add(m.split('.')[0]);
  }
  return [...cubes];
}

/**
 * queryRewrite: force a tenant_id filter onto every cube the query touches.
 * Throws if the tenant is missing OR forged (non-string / empty / unsafe charset) so a bad
 * context can never return cross-tenant data.
 */
function queryRewrite(query, { securityContext } = {}) {
  const tenantId = requireTenant(securityContext);
  query.filters = query.filters || [];

  for (const cube of cubesInQuery(query)) {
    query.filters.push({
      member: `${cube}.tenant_id`,
      operator: 'equals',
      values: [tenantId],
    });
  }
  return query;
}

/** Keep compile/cache resources separate per tenant. Same missing/forged rejection. */
function contextToAppId({ securityContext } = {}) {
  return `CUBE_${requireTenant(securityContext)}`;
}

/**
 * Verify an HS256 JWT and return its payload. Throws on ANY defect:
 * - unsigned / malformed shape (not three non-empty dot-separated segments)
 * - alg other than HS256 (including `none` — alg-stripping is the classic forgery)
 * - signature that doesn't verify against the secret (constant-time compare)
 * - missing or past `exp` (every Cube token is short-lived by construction)
 * Python mirror: agents/tools/cube_client.decode_verified — both sides enforce the same contract.
 */
function decodeVerifiedJwt(token, secret, { now = Math.floor(Date.now() / 1000) } = {}) {
  if (typeof token !== 'string' || !token) throw new Error('no token');
  if (!secret) throw new Error('no api secret');
  const raw = token.startsWith('Bearer ') ? token.slice('Bearer '.length) : token;
  const parts = raw.split('.');
  if (parts.length !== 3 || parts.some((p) => !p)) throw new Error('unsigned or malformed token');
  const [headerB64, payloadB64, sigB64] = parts;

  let header;
  let payload;
  let givenSig;
  try {
    header = JSON.parse(Buffer.from(headerB64, 'base64url').toString('utf8'));
    payload = JSON.parse(Buffer.from(payloadB64, 'base64url').toString('utf8'));
    givenSig = Buffer.from(sigB64, 'base64url');
  } catch (e) {
    throw new Error('unsigned or malformed token');
  }
  if (!header || header.alg !== 'HS256') throw new Error('bad alg');

  const expected = crypto
    .createHmac('sha256', secret)
    .update(`${headerB64}.${payloadB64}`)
    .digest();
  if (givenSig.length !== expected.length || !crypto.timingSafeEqual(givenSig, expected)) {
    throw new Error('bad signature');
  }
  if (!payload || typeof payload.exp !== 'number') throw new Error('no expiry');
  if (payload.exp <= now) throw new Error('expired token');
  return payload;
}

/**
 * checkAuth for cube.js: verify the request's JWT OURSELVES (never trust an upstream to have
 * done it) and stamp the security context from the verified payload only. The signing secret is
 * CUBEJS_API_SECRET — injected into the Cube task from Secrets Manager (infra/modules/cube);
 * the minting side holds the same value as CUBEJS_API_SECRET_VALUE (shared/config.py).
 */
function checkAuth(req, authorization) {
  const secret = process.env.CUBEJS_API_SECRET;
  if (!secret) throw new Error('no api secret'); // fail CLOSED: no secret => nobody authenticates
  const payload = decodeVerifiedJwt(authorization, secret);
  if (!isValidTenantId(payload.tenant_id)) throw new Error('no tenant');
  // ONLY the verified tenant crosses into the security context — nothing else from the token.
  req.securityContext = { tenant_id: payload.tenant_id };
}

/**
 * Build the tenant-scoped Postgres driver class over `BaseDriver` (the real
 * `@cubejs-backend/postgres-driver` in the image; a fake in tests — injectable so the unit
 * tests need no Cube install).
 *
 * Issue #177 root cause: Cube connects as the NON-OWNER `crm_app` role and every tenant table
 * carries a FORCE'd RLS policy on `current_setting('app.current_tenant', true)::uuid` — with
 * the GUC unset, `current_setting(..., true)` is NULL, the policy is false, and EVERY query
 * returns zero rows. The `queryRewrite` tenant filter is defense-in-depth OVER RLS; it cannot
 * reveal what RLS hides. The fix: every pooled connection this driver hands out runs a
 * PARAMETERIZED `set_config('app.current_tenant', $1, false)` right after the base driver's
 * own connection prep, binding the connection to the verified tenant. Session-level (not
 * SET LOCAL) is correct here: the pool itself is per-tenant — `driverFactory` keys the driver
 * on the security context and `contextToAppId` keys Cube's orchestrator/compile cache the same
 * way — so a connection only ever serves the one tenant it was prepared for.
 */
function buildTenantScopedDriverClass(BaseDriver) {
  return class TenantScopedPostgresDriver extends BaseDriver {
    constructor(tenantId, config = {}) {
      super(config);
      if (!isValidTenantId(tenantId)) throw new Error('forged tenant context');
      this.tenantId = tenantId;
    }

    async prepareConnection(conn, options) {
      await super.prepareConnection(conn, options);
      // Parameterized on purpose: the tenant is never interpolated into SQL, even though
      // requireTenant/isValidTenantId already constrained the charset (defense in depth).
      await conn.query({
        text: "SELECT set_config('app.current_tenant', $1, false)",
        values: [this.tenantId],
      });
    }
  };
}

/**
 * driverFactory for cube.js: per-security-context tenant-scoped Postgres driver (#177).
 *
 * THE TRUST RULE: the tenant comes ONLY from `securityContext.tenant_id`, which `checkAuth`
 * above stamped from the VERIFIED HS256 JWT — never from env/headers/query. A context that
 * carries a tenant_id of a forged shape THROWS (same `requireTenant` contract as queryRewrite/
 * contextToAppId).
 *
 * A context with NO tenant at all (internal bootstrap/health-check connections — never a
 * governed query: checkAuth guarantees authed requests carry a tenant and queryRewrite throws
 * without one) gets the UNSCOPED base driver. That is fail-CLOSED, not open: with the GUC
 * unset, FORCE'd RLS hides every row.
 *
 * `opts.driverClass` is a test seam only — production always lazy-requires the real
 * `@cubejs-backend/postgres-driver` (present in the cubejs/cube image, not in this repo).
 */
function driverFactory(context = {}, opts = {}) {
  const securityContext = (context && context.securityContext) || undefined;
  const BaseDriver = opts.driverClass || requirePostgresDriver();
  const hasTenant =
    securityContext !== undefined &&
    securityContext !== null &&
    securityContext.tenant_id !== undefined &&
    securityContext.tenant_id !== null;
  if (!hasTenant) {
    return new BaseDriver({}); // unscoped: GUC unset => FORCE'd RLS yields zero rows (fail closed)
  }
  const tenantId = requireTenant(securityContext); // throws on forged shapes
  const TenantScoped = buildTenantScopedDriverClass(BaseDriver);
  return new TenantScoped(tenantId);
}

/** Lazy + shape-tolerant require: the package historically default-exported the class and now
 * also names it — never required at module load so semantic/test/ runs without a Cube install. */
function requirePostgresDriver() {
  // eslint-disable-next-line global-require
  const mod = require('@cubejs-backend/postgres-driver');
  return (mod && (mod.PostgresDriver || mod.default)) || mod;
}

module.exports = {
  queryRewrite,
  contextToAppId,
  cubesInQuery,
  checkAuth,
  decodeVerifiedJwt,
  isValidTenantId,
  buildTenantScopedDriverClass,
  driverFactory,
};
