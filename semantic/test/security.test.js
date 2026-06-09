// Tests for the Cube tenant security context (the defense-in-depth layer over Postgres RLS).
// Run with: node --test semantic/test/
const { test } = require('node:test');
const assert = require('node:assert');
const { queryRewrite, contextToAppId, cubesInQuery } = require('../security');

const TENANT = '11111111-1111-1111-1111-111111111111';

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
