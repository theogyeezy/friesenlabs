// Tenant security context for Cube (Build Guide Phase 3, Step 21).
// This is the defense-in-depth layer OVER Postgres RLS: every query Cube runs is force-filtered
// to the tenant derived from the verified JWT. Isolation is enforced once, for every consumer,
// instead of every generated query having to get it right.
//
// Pulled into its own module so it is unit-testable without a running Cube.

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
 * Throws if no tenant is present so a missing/forged context can never return cross-tenant data.
 */
function queryRewrite(query, { securityContext } = {}) {
  if (!securityContext || !securityContext.tenant_id) {
    throw new Error('no tenant');
  }
  const tenantId = securityContext.tenant_id;
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

/** Keep compile/cache resources separate per tenant. */
function contextToAppId({ securityContext } = {}) {
  if (!securityContext || !securityContext.tenant_id) {
    throw new Error('no tenant');
  }
  return `CUBE_${securityContext.tenant_id}`;
}

module.exports = { queryRewrite, contextToAppId, cubesInQuery };
