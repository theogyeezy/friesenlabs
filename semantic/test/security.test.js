// Tests for the Cube tenant security context (the defense-in-depth layer over Postgres RLS).
// Run with: node --test semantic/test/  (CI/smoke: scripts/smoke/03_semantic.sh)
const { test } = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');
const {
  queryRewrite,
  contextToAppId,
  cubesInQuery,
  checkAuth,
  decodeVerifiedJwt,
  isValidTenantId,
} = require('../security');

const TENANT = '11111111-1111-1111-1111-111111111111';
const SECRET = 'test-cube-secret';

/** Mint an HS256 JWT the way agents/tools/cube_client.py does (the Python mirror). */
function mintJwt(payload, secret, { alg = 'HS256' } = {}) {
  const b64 = (obj) => Buffer.from(JSON.stringify(obj)).toString('base64url');
  const head = b64({ alg, typ: 'JWT' });
  const body = b64(payload);
  const sig = crypto.createHmac('sha256', secret).update(`${head}.${body}`).digest('base64url');
  return `${head}.${body}.${sig}`;
}

function freshPayload(overrides = {}) {
  const now = Math.floor(Date.now() / 1000);
  return { tenant_id: TENANT, iat: now, exp: now + 60, ...overrides };
}

test('queryRewrite throws when no tenant is present', () => {
  assert.throws(() => queryRewrite({ measures: ['Deals.count'] }, { securityContext: {} }), /no tenant/);
  assert.throws(() => queryRewrite({ measures: ['Deals.count'] }, {}), /no tenant/);
  assert.throws(() => queryRewrite({ measures: ['Deals.count'] }), /no tenant/);
});

test('queryRewrite forces a tenant_id filter on every referenced cube', () => {
  const q = {
    measures: ['Deals.pipeline_value'],
    dimensions: ['Contacts.name'],
    timeDimensions: [{ dimension: 'Activities.occurred_at', granularity: 'day' }],
  };
  const out = queryRewrite(q, { securityContext: { tenant_id: TENANT } });
  const tenantFilters = out.filters.filter((f) => f.member.endsWith('.tenant_id'));
  const members = tenantFilters.map((f) => f.member).sort();
  assert.deepStrictEqual(members, ['Activities.tenant_id', 'Contacts.tenant_id', 'Deals.tenant_id']);
  for (const f of tenantFilters) {
    assert.strictEqual(f.operator, 'equals');
    assert.deepStrictEqual(f.values, [TENANT]);
  }
});

test('queryRewrite preserves existing (non-tenant) filters', () => {
  const q = { measures: ['Deals.count'], filters: [{ member: 'Deals.stage', operator: 'equals', values: ['won'] }] };
  const out = queryRewrite(q, { securityContext: { tenant_id: TENANT } });
  assert.ok(out.filters.some((f) => f.member === 'Deals.stage'));
  assert.ok(out.filters.some((f) => f.member === 'Deals.tenant_id' && f.values[0] === TENANT));
});

test('a forged query with no measures/dimensions still cannot leak (no cubes => no rows queried)', () => {
  const out = queryRewrite({ measures: [], dimensions: [] }, { securityContext: { tenant_id: TENANT } });
  assert.deepStrictEqual(out.filters, []); // nothing referenced => nothing to scope; Cube returns nothing
});

test('cubesInQuery extracts distinct cube names', () => {
  const cubes = cubesInQuery({ measures: ['Deals.count', 'Deals.pipeline_value'], dimensions: ['Contacts.name'] });
  assert.deepStrictEqual(cubes.sort(), ['Contacts', 'Deals']);
});

test('contextToAppId is per-tenant and throws without a tenant', () => {
  assert.strictEqual(contextToAppId({ securityContext: { tenant_id: TENANT } }), `CUBE_${TENANT}`);
  assert.throws(() => contextToAppId({ securityContext: {} }), /no tenant/);
});

// ---------------------------------------------------------------- forged tenant contexts

test('queryRewrite rejects forged tenant shapes (non-string / empty / unsafe charset)', () => {
  const q = () => ({ measures: ['Deals.count'] });
  for (const forged of [
    '', '   ', 42, true, {}, [TENANT], { toString: () => TENANT },
    "x' OR '1'='1", 'tenant id with spaces', '%', '"quoted"',
  ]) {
    assert.throws(
      () => queryRewrite(q(), { securityContext: { tenant_id: forged } }),
      /forged tenant/,
      `expected forged-tenant rejection for ${JSON.stringify(forged)}`
    );
  }
});

test('contextToAppId rejects forged tenant shapes too (no per-tenant cache pollution)', () => {
  assert.throws(() => contextToAppId({ securityContext: { tenant_id: { evil: 1 } } }), /forged tenant/);
  assert.throws(() => contextToAppId({ securityContext: { tenant_id: '' } }), /forged tenant/);
});

test('isValidTenantId accepts sane ids and rejects junk', () => {
  assert.ok(isValidTenantId(TENANT));
  assert.ok(isValidTenantId('t1'));
  assert.ok(!isValidTenantId(''));
  assert.ok(!isValidTenantId(null));
  assert.ok(!isValidTenantId(undefined));
  assert.ok(!isValidTenantId(123));
  assert.ok(!isValidTenantId('-leading-dash'));
  assert.ok(!isValidTenantId('has space'));
});

// ---------------------------------------------------------------- JWT verification (checkAuth)

test('decodeVerifiedJwt accepts a well-signed unexpired token and returns the payload', () => {
  const payload = freshPayload();
  const out = decodeVerifiedJwt(mintJwt(payload, SECRET), SECRET);
  assert.strictEqual(out.tenant_id, TENANT);
  assert.strictEqual(out.exp, payload.exp);
});

test('decodeVerifiedJwt accepts a Bearer-prefixed token', () => {
  const out = decodeVerifiedJwt(`Bearer ${mintJwt(freshPayload(), SECRET)}`, SECRET);
  assert.strictEqual(out.tenant_id, TENANT);
});

test('decodeVerifiedJwt rejects unsigned / malformed tokens', () => {
  const token = mintJwt(freshPayload(), SECRET);
  const [head, body] = token.split('.');
  assert.throws(() => decodeVerifiedJwt(`${head}.${body}`, SECRET), /unsigned or malformed/);
  assert.throws(() => decodeVerifiedJwt(`${head}.${body}.`, SECRET), /unsigned or malformed/);
  assert.throws(() => decodeVerifiedJwt('not-a-jwt', SECRET), /unsigned or malformed/);
  assert.throws(() => decodeVerifiedJwt('', SECRET), /no token/);
  assert.throws(() => decodeVerifiedJwt(undefined, SECRET), /no token/);
});

test('decodeVerifiedJwt rejects alg=none and any non-HS256 alg (alg-stripping forgery)', () => {
  assert.throws(() => decodeVerifiedJwt(mintJwt(freshPayload(), SECRET, { alg: 'none' }), SECRET), /bad alg/);
  assert.throws(() => decodeVerifiedJwt(mintJwt(freshPayload(), SECRET, { alg: 'RS256' }), SECRET), /bad alg/);
});

test('decodeVerifiedJwt rejects a token signed with the wrong secret', () => {
  assert.throws(() => decodeVerifiedJwt(mintJwt(freshPayload(), 'forged-secret'), SECRET), /bad signature/);
});

test('decodeVerifiedJwt rejects a tampered payload (signature no longer matches)', () => {
  const token = mintJwt(freshPayload(), SECRET);
  const [head, , sig] = token.split('.');
  const tampered = Buffer.from(
    JSON.stringify(freshPayload({ tenant_id: '22222222-2222-2222-2222-222222222222' }))
  ).toString('base64url');
  assert.throws(() => decodeVerifiedJwt(`${head}.${tampered}.${sig}`, SECRET), /bad signature/);
});

test('decodeVerifiedJwt rejects expired and expiry-less tokens', () => {
  const now = Math.floor(Date.now() / 1000);
  assert.throws(
    () => decodeVerifiedJwt(mintJwt(freshPayload({ exp: now - 5 }), SECRET), SECRET),
    /expired token/
  );
  const noExp = freshPayload();
  delete noExp.exp;
  assert.throws(() => decodeVerifiedJwt(mintJwt(noExp, SECRET), SECRET), /no expiry/);
  // injectable clock: a token valid now is rejected at a later "now"
  const tok = mintJwt(freshPayload(), SECRET);
  assert.throws(() => decodeVerifiedJwt(tok, SECRET, { now: now + 3600 }), /expired token/);
});

test('checkAuth stamps securityContext from the VERIFIED payload only', () => {
  process.env.CUBEJS_API_SECRET = SECRET;
  try {
    const req = {};
    checkAuth(req, mintJwt(freshPayload({ extra_claim: 'never-copied' }), SECRET));
    assert.deepStrictEqual(req.securityContext, { tenant_id: TENANT }); // nothing else crosses
  } finally {
    delete process.env.CUBEJS_API_SECRET;
  }
});

test('checkAuth rejects bad tokens and never stamps a context', () => {
  process.env.CUBEJS_API_SECRET = SECRET;
  try {
    const cases = [
      ['forged secret', mintJwt(freshPayload(), 'forged-secret'), /bad signature/],
      ['expired', mintJwt(freshPayload({ exp: 1 }), SECRET), /expired token/],
      ['alg none', mintJwt(freshPayload(), SECRET, { alg: 'none' }), /bad alg/],
      ['tenantless payload', mintJwt({ iat: 1, exp: Math.floor(Date.now() / 1000) + 60 }, SECRET), /no tenant/],
      ['missing token', undefined, /no token/],
    ];
    for (const [label, token, re] of cases) {
      const req = {};
      assert.throws(() => checkAuth(req, token), re, label);
      assert.strictEqual(req.securityContext, undefined, `${label}: context must not be stamped`);
    }
  } finally {
    delete process.env.CUBEJS_API_SECRET;
  }
});

test('checkAuth fails CLOSED when the Cube API secret is not configured', () => {
  delete process.env.CUBEJS_API_SECRET;
  const req = {};
  assert.throws(() => checkAuth(req, mintJwt(freshPayload(), SECRET)), /no api secret/);
  assert.strictEqual(req.securityContext, undefined);
});

test('cube.js exports checkAuth + queryRewrite + contextToAppId wired from security.js', () => {
  const cubeConf = require('../cube');
  assert.strictEqual(cubeConf.checkAuth, checkAuth);
  assert.strictEqual(cubeConf.queryRewrite, queryRewrite);
  assert.strictEqual(cubeConf.contextToAppId, contextToAppId);
});
