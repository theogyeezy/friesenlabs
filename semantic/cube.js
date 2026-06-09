// Cube configuration (Build Guide Phase 3, Step 21).
// Wires the tenant security context so every query is force-scoped to the JWT's tenant_id.
// securityContext arrives from the JWT Cube receives (the Cognito token, or a short-lived token
// the API mints carrying tenant_id). This is defense-in-depth OVER Postgres RLS.
const { queryRewrite, contextToAppId } = require('./security');

module.exports = {
  // Keep compile/cache resources separate per tenant.
  contextToAppId,
  // Force a tenant_id filter onto every query; throw if no tenant.
  queryRewrite,
};
