// Tests for the tenant-scoped Postgres driver (#177 fix): Cube connects as the non-owner
// crm_app role under FORCE'd RLS, so every connection MUST set app.current_tenant or every
// governed query returns zero rows. The driver class is built over an injectable base so these
// tests run without @cubejs-backend/postgres-driver installed (it lives in the cube image only).
// Run with: node --test semantic/test/  (CI/smoke: scripts/smoke/03_semantic.sh)
const { test } = require('node:test');
const assert = require('node:assert');
const { buildTenantScopedDriverClass, driverFactory } = require('../security');

const TENANT = '11111111-1111-1111-1111-111111111111';

class FakeConn {
  constructor() {
    this.queries = [];
  }

  async query(q) {
    this.queries.push(q);
  }
}

class FakeBaseDriver {
  constructor(config = {}) {
    this.config = config;
    this.preparedWith = [];
  }

  async prepareConnection(conn, options) {
    this.preparedWith.push(options);
    conn.queries.push({ text: 'BASE_PREPARE' });
  }
}

test('tenant-scoped driver sets app.current_tenant AFTER the base connection prep', async () => {
  const Driver = buildTenantScopedDriverClass(FakeBaseDriver);
  const driver = new Driver(TENANT);
  const conn = new FakeConn();

  await driver.prepareConnection(conn, { executionTimeout: 600 });

  // Base prep ran first (timezone/statement_timeout etc.), then the GUC bind.
  assert.strictEqual(conn.queries[0].text, 'BASE_PREPARE');
  assert.strictEqual(conn.queries.length, 2);
  const guc = conn.queries[1];
  assert.match(guc.text, /set_config\('app\.current_tenant', \$1, false\)/);
  assert.deepStrictEqual(guc.values, [TENANT]);
  // The tenant rides as a BOUND PARAMETER — never interpolated into the SQL text.
  assert.ok(!guc.text.includes(TENANT));
  // The base driver's own prep args pass through untouched.
  assert.deepStrictEqual(driver.preparedWith, [{ executionTimeout: 600 }]);
});

test('tenant-scoped driver refuses missing/forged tenants at construction', () => {
  const Driver = buildTenantScopedDriverClass(FakeBaseDriver);
  for (const bad of [undefined, null, '', '   ', "x'; DROP TABLE deals;--", { tenant_id: TENANT }, ['a']]) {
    assert.throws(() => new Driver(bad), /forged tenant context/);
  }
});

test('driverFactory binds the security-context tenant onto the connection', async () => {
  const driver = driverFactory(
    { securityContext: { tenant_id: TENANT } },
    { driverClass: FakeBaseDriver },
  );
  const conn = new FakeConn();
  await driver.prepareConnection(conn, undefined);
  assert.deepStrictEqual(conn.queries[1].values, [TENANT]);
});

test('driverFactory throws on a forged tenant shape (never hands out a driver)', () => {
  for (const bad of ['', '  ', 42, ['x'], { nested: true }, "t'; --"]) {
    assert.throws(
      () => driverFactory({ securityContext: { tenant_id: bad } }, { driverClass: FakeBaseDriver }),
      /forged tenant context/,
    );
  }
});

test('driverFactory with NO tenant returns the unscoped base driver (RLS fail-closed)', async () => {
  for (const ctx of [{}, { securityContext: undefined }, { securityContext: null }, { securityContext: {} }]) {
    const driver = driverFactory(ctx, { driverClass: FakeBaseDriver });
    assert.ok(driver instanceof FakeBaseDriver);
    const conn = new FakeConn();
    await driver.prepareConnection(conn, undefined);
    // Base prep only — no GUC bind: with app.current_tenant unset, FORCE'd RLS hides every row.
    assert.deepStrictEqual(conn.queries.map((q) => q.text), ['BASE_PREPARE']);
  }
});

test('per-tenant drivers are independent: two tenants never share a GUC value', async () => {
  const a = driverFactory({ securityContext: { tenant_id: 'tenant-A' } }, { driverClass: FakeBaseDriver });
  const b = driverFactory({ securityContext: { tenant_id: 'tenant-B' } }, { driverClass: FakeBaseDriver });
  const connA = new FakeConn();
  const connB = new FakeConn();
  await a.prepareConnection(connA, undefined);
  await b.prepareConnection(connB, undefined);
  assert.deepStrictEqual(connA.queries[1].values, ['tenant-A']);
  assert.deepStrictEqual(connB.queries[1].values, ['tenant-B']);
});
